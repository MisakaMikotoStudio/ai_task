# ── Stage 1: Build ──────────────────────────────────────────────────────────
FROM golang:1.24-alpine AS builder

WORKDIR /app

# Copy module files and download dependencies first (layer caching)
COPY go.mod ./
# go.sum may not exist yet; tidy will generate/update it
RUN go mod download 2>/dev/null || true

COPY *.go ./

# tidy ensures go.sum is correct, then build a statically linked binary
RUN go mod tidy && \
    CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o gateway .

# ── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM alpine:3.20

# ca-certificates needed for HTTPS upstream calls; tzdata for correct time zone
RUN apk --no-cache add ca-certificates tzdata

WORKDIR /app

COPY --from=builder /app/gateway .

EXPOSE 8080

ENV GATEWAY_PORT=8080 \
    DB_HOST=localhost \
    DB_PORT=3306 \
    DB_USER=root \
    DB_PASSWORD="" \
    DB_NAME=ai_task

CMD ["./gateway"]
