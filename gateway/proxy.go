package main

import (
	"bufio"
	"bytes"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"strings"
)

type proxyHandler struct {
	db     *sql.DB
	client *http.Client
}

func newProxyHandler(db *sql.DB) *proxyHandler {
	return &proxyHandler{
		db:     db,
		client: &http.Client{}, // no timeout: streaming responses can be long
	}
}

func (h *proxyHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// Health check
	if r.URL.Path == "/health" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"status":"ok"}`))
		return
	}

	// Extract virtual key from x-api-key (Anthropic style) or Authorization: Bearer (OpenAI style)
	virtualKeyValue := r.Header.Get("x-api-key")
	if virtualKeyValue == "" {
		auth := r.Header.Get("Authorization")
		if strings.HasPrefix(auth, "Bearer ") {
			virtualKeyValue = strings.TrimPrefix(auth, "Bearer ")
		}
	}
	if virtualKeyValue == "" {
		writeJSONError(w, http.StatusUnauthorized, "missing api key")
		return
	}

	// Look up virtual key in DB
	vk, err := getVirtualKey(h.db, virtualKeyValue)
	if err != nil {
		log.Printf("[gateway] DB error looking up virtual key: %v", err)
		writeJSONError(w, http.StatusInternalServerError, "internal server error")
		return
	}
	if vk == nil {
		writeJSONError(w, http.StatusUnauthorized, "invalid api key")
		return
	}

	// Check daily spend limit (skip if -1)
	if vk.DailyLimit >= 0 {
		spend, err := getDailySpend(h.db, vk.ID)
		if err != nil {
			log.Printf("[gateway] failed to check daily spend for key %d: %v", vk.ID, err)
			// Allow through on error; do not block the user
		} else if spend >= vk.DailyLimit {
			writeJSONError(w, http.StatusTooManyRequests, fmt.Sprintf("daily spend limit %.2f RMB exceeded (current: %.4f RMB)", vk.DailyLimit, spend))
			return
		}
	}

	// Buffer request body so we can (a) extract model name and (b) forward it
	bodyBytes, err := io.ReadAll(r.Body)
	if err != nil {
		writeJSONError(w, http.StatusBadRequest, "failed to read request body")
		return
	}
	r.Body.Close()

	// Extract model name from request for later usage logging
	reqModel := extractModelFromBody(bodyBytes)

	// Build upstream URL
	targetURL := buildTargetURL(vk.TargetURL, r)

	upstreamReq, err := http.NewRequestWithContext(r.Context(), r.Method, targetURL, bytes.NewReader(bodyBytes))
	if err != nil {
		log.Printf("[gateway] failed to create upstream request: %v", err)
		writeJSONError(w, http.StatusInternalServerError, "internal server error")
		return
	}

	// Copy all headers except auth-related ones
	for k, vs := range r.Header {
		lk := strings.ToLower(k)
		if lk == "x-api-key" || lk == "authorization" {
			continue
		}
		for _, v := range vs {
			upstreamReq.Header.Add(k, v)
		}
	}
	upstreamReq.Header.Set("Content-Length", fmt.Sprintf("%d", len(bodyBytes)))

	// Set correct auth header for upstream based on provider
	if strings.EqualFold(vk.Provider, "openai") {
		upstreamReq.Header.Set("Authorization", "Bearer "+vk.RealKey)
	} else {
		// Anthropic, DeepSeek, and most others use x-api-key
		upstreamReq.Header.Set("x-api-key", vk.RealKey)
	}

	// Ensure Host is set to upstream, not our gateway
	parsedTarget, _ := url.Parse(vk.TargetURL)
	upstreamReq.Host = parsedTarget.Host

	// Forward request to upstream
	resp, err := h.client.Do(upstreamReq)
	if err != nil {
		log.Printf("[gateway] upstream request failed: %v", err)
		writeJSONError(w, http.StatusBadGateway, "upstream request failed")
		return
	}
	defer resp.Body.Close()

	// Copy response headers
	for k, vs := range resp.Header {
		for _, v := range vs {
			w.Header().Add(k, v)
		}
	}
	w.WriteHeader(resp.StatusCode)

	// Only parse usage on successful responses
	if resp.StatusCode == http.StatusOK {
		isStreaming := strings.Contains(resp.Header.Get("Content-Type"), "text/event-stream")
		if isStreaming {
			h.handleStreaming(w, resp, vk.ID, reqModel)
		} else {
			h.handleNonStreaming(w, resp, vk.ID, reqModel)
		}
	} else {
		io.Copy(w, resp.Body)
	}
}

// handleNonStreaming reads the full response, writes it to client, then parses usage.
func (h *proxyHandler) handleNonStreaming(w http.ResponseWriter, resp *http.Response, virtualKeyID int, reqModel string) {
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		log.Printf("[gateway] failed to read upstream response: %v", err)
		return
	}
	w.Write(body)

	// Parse usage from Anthropic / OpenAI response
	var apiResp struct {
		Model string `json:"model"`
		Usage struct {
			InputTokens  int `json:"input_tokens"`
			OutputTokens int `json:"output_tokens"`
			// OpenAI format
			PromptTokens     int `json:"prompt_tokens"`
			CompletionTokens int `json:"completion_tokens"`
		} `json:"usage"`
	}
	if err := json.Unmarshal(body, &apiResp); err != nil {
		return
	}
	model := apiResp.Model
	if model == "" {
		model = reqModel
	}
	inputTokens := apiResp.Usage.InputTokens
	if inputTokens == 0 {
		inputTokens = apiResp.Usage.PromptTokens
	}
	outputTokens := apiResp.Usage.OutputTokens
	if outputTokens == 0 {
		outputTokens = apiResp.Usage.CompletionTokens
	}

	if inputTokens > 0 || outputTokens > 0 {
		go h.recordUsage(virtualKeyID, model, inputTokens, outputTokens)
	}
}

// handleStreaming forwards SSE events line by line and collects usage from special events.
func (h *proxyHandler) handleStreaming(w http.ResponseWriter, resp *http.Response, virtualKeyID int, reqModel string) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeJSONError(w, http.StatusInternalServerError, "streaming not supported by server")
		return
	}

	var (
		inputTokens  int
		outputTokens int
		model        = reqModel
	)

	scanner := bufio.NewScanner(resp.Body)
	// Increase buffer to handle large SSE payloads (e.g. tool use responses)
	scanner.Buffer(make([]byte, 512*1024), 512*1024)

	for scanner.Scan() {
		line := scanner.Text()
		// Forward line to client immediately
		fmt.Fprintf(w, "%s\n", line)
		flusher.Flush()

		// Parse only data lines
		if !strings.HasPrefix(line, "data: ") {
			continue
		}
		data := strings.TrimPrefix(line, "data: ")
		if data == "[DONE]" {
			continue
		}

		var event struct {
			Type    string `json:"type"`
			// Anthropic message_start
			Message *struct {
				Model string `json:"model"`
				Usage struct {
					InputTokens  int `json:"input_tokens"`
					OutputTokens int `json:"output_tokens"`
				} `json:"usage"`
			} `json:"message,omitempty"`
			// Anthropic message_delta
			Usage *struct {
				OutputTokens int `json:"output_tokens"`
			} `json:"usage,omitempty"`
			// OpenAI chunk
			Choices []struct {
				FinishReason string `json:"finish_reason"`
			} `json:"choices,omitempty"`
		}

		if err := json.Unmarshal([]byte(data), &event); err != nil {
			continue
		}

		switch event.Type {
		case "message_start":
			if event.Message != nil {
				if event.Message.Model != "" {
					model = event.Message.Model
				}
				inputTokens = event.Message.Usage.InputTokens
				// message_start may also report initial output token count
				if event.Message.Usage.OutputTokens > outputTokens {
					outputTokens = event.Message.Usage.OutputTokens
				}
			}
		case "message_delta":
			if event.Usage != nil && event.Usage.OutputTokens > 0 {
				outputTokens = event.Usage.OutputTokens
			}
		}
	}

	if err := scanner.Err(); err != nil {
		log.Printf("[gateway] stream scanner error: %v", err)
	}

	if inputTokens > 0 || outputTokens > 0 {
		go h.recordUsage(virtualKeyID, model, inputTokens, outputTokens)
	}
}

// recordUsage looks up the model price and writes a usage log entry.
func (h *proxyHandler) recordUsage(virtualKeyID int, model string, inputTokens, outputTokens int) {
	var inputCost, outputCost float64

	if model != "" {
		price, err := getModelPrice(h.db, model)
		if err != nil {
			log.Printf("[gateway] failed to get model price for %q: %v", model, err)
		} else if price != nil {
			inputCost = float64(inputTokens) / 1_000_000 * price.InputPricePerMillion
			outputCost = float64(outputTokens) / 1_000_000 * price.OutputPricePerMillion
		}
	}

	if err := logUsage(h.db, virtualKeyID, model, inputTokens, outputTokens, inputCost, outputCost); err != nil {
		log.Printf("[gateway] failed to log usage: %v", err)
	} else {
		log.Printf("[gateway] usage logged: key_id=%d model=%s in=%d out=%d cost=%.6f+%.6f RMB",
			virtualKeyID, model, inputTokens, outputTokens, inputCost, outputCost)
	}
}

// buildTargetURL constructs the full upstream URL from the base target and incoming request.
func buildTargetURL(targetBase string, r *http.Request) string {
	base := strings.TrimRight(targetBase, "/")
	path := r.URL.Path
	query := r.URL.RawQuery
	if query != "" {
		return base + path + "?" + query
	}
	return base + path
}

// extractModelFromBody parses the request JSON body and returns the "model" field value.
func extractModelFromBody(body []byte) string {
	var req struct {
		Model string `json:"model"`
	}
	if err := json.Unmarshal(body, &req); err == nil {
		return req.Model
	}
	return ""
}

// writeJSONError writes a simple JSON error response.
func writeJSONError(w http.ResponseWriter, code int, msg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	fmt.Fprintf(w, `{"error":{"type":"gateway_error","message":"%s"}}`, msg)
}
