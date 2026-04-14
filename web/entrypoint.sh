#!/bin/sh
# Generate config.json from environment variables for frontend
cat > /app/config.json <<ENDJSON
{
  "apiserver": {
    "host": "${APISERVER_HOST:-}",
    "path_prefix": "${APISERVER_PATH_PREFIX:-/api}"
  }
}
ENDJSON

echo "[entrypoint] Generated /app/config.json"

# Start nginx in foreground
exec nginx -g 'daemon off;'
