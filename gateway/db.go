package main

import (
	"database/sql"
	"fmt"
	"time"
)

// VirtualKey represents a row from ai_task_gateway_virtual_keys.
type VirtualKey struct {
	ID         int
	Provider   string
	RealKey    string
	VirtualKey string
	TargetURL  string
	DailyLimit float64 // -1 means unlimited
}

// ModelPrice represents a row from ai_task_gateway_model_prices.
type ModelPrice struct {
	InputPricePerMillion  float64
	OutputPricePerMillion float64
}

// getVirtualKey looks up a virtual key by its value. Returns nil if not found.
func getVirtualKey(db *sql.DB, virtualKey string) (*VirtualKey, error) {
	row := db.QueryRow(`
		SELECT id, provider, real_key, virtual_key, target_url, daily_limit
		FROM ai_task_gateway_virtual_keys
		WHERE virtual_key = ? AND deleted_at IS NULL
		LIMIT 1
	`, virtualKey)

	vk := &VirtualKey{}
	err := row.Scan(&vk.ID, &vk.Provider, &vk.RealKey, &vk.VirtualKey, &vk.TargetURL, &vk.DailyLimit)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("query virtual key: %w", err)
	}
	return vk, nil
}

// getDailySpend returns the total RMB spent today for the given virtual key ID.
func getDailySpend(db *sql.DB, virtualKeyID int) (float64, error) {
	today := time.Now().Format("2006-01-02")
	row := db.QueryRow(`
		SELECT COALESCE(SUM(input_cost + output_cost), 0)
		FROM ai_task_gateway_usage_logs
		WHERE virtual_key_id = ? AND stat_date = ?
	`, virtualKeyID, today)

	var total float64
	if err := row.Scan(&total); err != nil {
		return 0, fmt.Errorf("query daily spend: %w", err)
	}
	return total, nil
}

// getModelPrice retrieves pricing for the given model name.
// It first tries an exact match, then a prefix match.
// Returns nil if no price configuration is found.
func getModelPrice(db *sql.DB, modelName string) (*ModelPrice, error) {
	mp := &ModelPrice{}

	// Exact match
	row := db.QueryRow(`
		SELECT input_price_per_million, output_price_per_million
		FROM ai_task_gateway_model_prices
		WHERE model_name = ?
		LIMIT 1
	`, modelName)
	err := row.Scan(&mp.InputPricePerMillion, &mp.OutputPricePerMillion)
	if err == nil {
		return mp, nil
	}
	if err != sql.ErrNoRows {
		return nil, fmt.Errorf("query model price (exact): %w", err)
	}

	// Prefix match: find the longest model_name that is a prefix of the given name
	row2 := db.QueryRow(`
		SELECT input_price_per_million, output_price_per_million
		FROM ai_task_gateway_model_prices
		WHERE ? LIKE CONCAT(model_name, '%')
		ORDER BY LENGTH(model_name) DESC
		LIMIT 1
	`, modelName)
	err2 := row2.Scan(&mp.InputPricePerMillion, &mp.OutputPricePerMillion)
	if err2 == sql.ErrNoRows {
		return nil, nil
	}
	if err2 != nil {
		return nil, fmt.Errorf("query model price (prefix): %w", err2)
	}
	return mp, nil
}

// logUsage inserts a usage record into ai_task_gateway_usage_logs.
func logUsage(db *sql.DB, virtualKeyID int, model string, inputTokens, outputTokens int, inputCost, outputCost float64) error {
	today := time.Now().Format("2006-01-02")
	_, err := db.Exec(`
		INSERT INTO ai_task_gateway_usage_logs
			(virtual_key_id, model, input_tokens, output_tokens, input_cost, output_cost, stat_date)
		VALUES (?, ?, ?, ?, ?, ?, ?)
	`, virtualKeyID, model, inputTokens, outputTokens, inputCost, outputCost, today)
	if err != nil {
		return fmt.Errorf("insert usage log: %w", err)
	}
	return nil
}
