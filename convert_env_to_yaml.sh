#!/bin/bash

# Script to convert .env file to config.yaml format
# Looks for .env in the same directory as the script

echo "=== .env to config.yaml Converter ==="
echo ""

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Define paths relative to script location
ENV_FILE="$SCRIPT_DIR/.env"
OUTPUT_FILE="$SCRIPT_DIR/config.yaml"

# Check if input file exists
if [ ! -f "$ENV_FILE" ]; then
    echo "Error: .env file not found at $ENV_FILE"
    exit 1
fi

echo "✓ Found .env file: $ENV_FILE"

# Parse .env file
declare -A env_vars

while IFS='=' read -r key value; do
    # Skip empty lines and comments
    [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
    
    # Remove leading/trailing whitespace
    key=$(echo "$key" | xargs)
    value=$(echo "$value" | xargs)
    
    env_vars["$key"]="$value"
done < "$ENV_FILE"

echo "✓ Parsed ${#env_vars[@]} environment variables"
echo "✓ Converting to YAML format..."

# Create YAML file
cat > "$OUTPUT_FILE" << EOF
common:
  xui_bot_token: "${env_vars[XUI_BOT_TOKEN]}"
  awg_bot_token: "${env_vars[AWG_BOT_TOKEN]}"
  admin_ids:
    - ${env_vars[ADMIN_IDS]}
  server_port: ${env_vars[SERVER_PORT]:-443}
  api_timeout: ${env_vars[API_TIMEOUT]:-30}
  xhttp_mode: "${env_vars[XHTTP_MODE]:-auto}"
  tls_fingerprint: "${env_vars[TLS_FINGERPRINT]:-edge}"
  tls_alpn: "${env_vars[TLS_ALPN]:-http/1.1}"
  max_traffic_gb: ${env_vars[MAX_TRAFFIC_GB]:-1000}
  max_days: ${env_vars[MAX_DAYS]:-3650}
  min_days: ${env_vars[MIN_DAYS]:-1}
  default_traffic_gb: ${env_vars[DEFAULT_TRAFFIC_GB]:-100}
  default_days: ${env_vars[DEFAULT_DAYS]:-30}
  db_path: "${env_vars[DB_PATH]:-/app/data/bot_users.db}"
  db_backup_enabled: ${env_vars[DB_BACKUP_ENABLED]:-true}
  db_backup_interval: ${env_vars[DB_BACKUP_INTERVAL]:-24}
  log_level: "${env_vars[LOG_LEVEL]:-INFO}"
  log_file_enabled: ${env_vars[LOG_FILE_ENABLED]:-true}
  log_file_path: "${env_vars[LOG_FILE_PATH]:-/app/logs/bot.log}"
  log_max_size_mb: ${env_vars[LOG_MAX_SIZE_MB]:-10}
  log_backup_count: ${env_vars[LOG_BACKUP_COUNT]:-5}
  panel_monitoring_enabled: true
  panel_check_interval: 30
  panel_failure_threshold: 3
  panel_check_timeout: 5
  allow_user_dns_queries: ${env_vars[ALLOW_USER_DNS_QUERIES]:-false}
default_panel: local_panel
panels:
  local_panel:
    alias: Converted Panel
    enabled: true
    is_local: true
    xui_version: ${env_vars[XUI_VERSION]:-2.8.10}
    xui_url: ${env_vars[XUI_URL]}
    xui_username: ${env_vars[XUI_USERNAME]}
    xui_password: ${env_vars[XUI_PASSWORD]}
    xui_db_path: ${env_vars[XUI_DB_PATH]:-/etc/x-ui/x-ui.db}
    inbound_id: '${env_vars[INBOUND_ID]:-1}'
    server_address: ${env_vars[SERVER_ADDRESS]}
    server_ip: ${env_vars[SERVER_IP]}
    transport: ${env_vars[TRANSPORT]:-tcp}
    security: ${env_vars[SECURITY]:-reality}
    tls_sni: ''
    tls_fingerprint: ${env_vars[TLS_FINGERPRINT]:-edge}
    reality_sni: ${env_vars[REALITY_SNI]:-google.com}
    reality_fingerprint: ${env_vars[REALITY_FINGERPRINT]:-edge}
    reality_public_key: ${env_vars[REALITY_PUBLIC_KEY]}
    reality_private_key: ${env_vars[REALITY_PRIVATE_KEY]}
    reality_short_id: ${env_vars[REALITY_SHORT_ID]}
EOF

echo ""
echo "✓ Conversion completed successfully!"
echo "✓ Created: $OUTPUT_FILE"
echo ""
echo "Configuration summary:"
echo "  - XUI Bot Token: ${env_vars[XUI_BOT_TOKEN]:0:20}..."
echo "  - AWG Bot Token: ${env_vars[AWG_BOT_TOKEN]:0:20}..."
echo "  - Admin IDs: ${env_vars[ADMIN_IDS]}"
echo "  - Server: ${env_vars[SERVER_ADDRESS]}:${env_vars[SERVER_PORT]}"
echo "  - Panel URL: ${env_vars[XUI_URL]}"
echo ""
echo "You can now use config.yaml for your bot configuration!"

# Made with Bob
