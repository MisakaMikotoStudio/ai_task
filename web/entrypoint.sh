#!/bin/sh
# Read config from mounted TOML file and generate config.json for frontend,
# then start nginx in foreground.

CONFIG_FILE="${WEB_CONFIG:-/config/config.toml}"

# Default values
APISERVER_HOST=""
APISERVER_PATH_PREFIX="/api"

if [ -f "$CONFIG_FILE" ]; then
    # Parse [apiserver] section from TOML config
    APISERVER_HOST=$(awk '/^\[apiserver\]/{f=1;next} /^\[/{f=0} f && /^host *=/{sub(/^[^=]*= *"?/, ""); sub(/"? *(#.*)?$/, ""); print}' "$CONFIG_FILE")
    val=$(awk '/^\[apiserver\]/{f=1;next} /^\[/{f=0} f && /^path_prefix *=/{sub(/^[^=]*= *"?/, ""); sub(/"? *(#.*)?$/, ""); print}' "$CONFIG_FILE")
    [ -n "$val" ] && APISERVER_PATH_PREFIX="$val"
    echo "[entrypoint] Loaded config from $CONFIG_FILE"
else
    echo "[entrypoint] Config file not found: $CONFIG_FILE, using defaults"
fi

cat > /app/config.json <<ENDJSON
{
  "apiserver": {
    "host": "${APISERVER_HOST}",
    "path_prefix": "${APISERVER_PATH_PREFIX}"
  }
}
ENDJSON

echo "[entrypoint] Generated /app/config.json (apiserver.host='${APISERVER_HOST}', apiserver.path_prefix='${APISERVER_PATH_PREFIX}')"

# Start nginx in foreground
exec nginx -g 'daemon off;'
