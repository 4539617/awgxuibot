#!/bin/bash
# Глобальная замена update_env_value на update_config_value для параметров панели

echo "🔄 Выполнение глобальной замены в install.sh..."

sed -i 's/update_env_value "INBOUND_ID"/update_config_value "INBOUND_ID"/g' install.sh
sed -i 's/update_env_value "TRANSPORT"/update_config_value "TRANSPORT"/g' install.sh
sed -i 's/update_env_value "SECURITY"/update_config_value "SECURITY"/g' install.sh
sed -i 's/update_env_value "REALITY_PUBLIC_KEY"/update_config_value "REALITY_PUBLIC_KEY"/g' install.sh
sed -i 's/update_env_value "REALITY_PRIVATE_KEY"/update_config_value "REALITY_PRIVATE_KEY"/g' install.sh
sed -i 's/update_env_value "REALITY_SHORT_ID"/update_config_value "REALITY_SHORT_ID"/g' install.sh
sed -i 's/update_env_value "REALITY_SNI"/update_config_value "REALITY_SNI"/g' install.sh
sed -i 's/update_env_value "REALITY_FINGERPRINT"/update_config_value "REALITY_FINGERPRINT"/g' install.sh
sed -i 's/update_env_value "TLS_FINGERPRINT"/update_config_value "TLS_FINGERPRINT"/g' install.sh
sed -i 's/update_env_value "TLS_ALPN"/update_config_value "TLS_ALPN"/g' install.sh
sed -i 's/update_env_value "TLS_SNI"/update_config_value "TLS_SNI"/g' install.sh
sed -i 's/update_env_value "XUI_PASSWORD"/update_config_value "XUI_PASSWORD"/g' install.sh
sed -i 's/update_env_value "XUI_API_TOKEN"/update_config_value "XUI_API_TOKEN"/g' install.sh
sed -i 's/update_env_value "XUI_DB_PATH"/update_config_value "XUI_DB_PATH"/g' install.sh
sed -i 's/update_env_value "SERVER_ADDRESS"/update_config_value "SERVER_ADDRESS"/g' install.sh
sed -i 's/update_env_value "XUI_USERNAME"/update_config_value "XUI_USERNAME"/g' install.sh
sed -i 's/update_env_value "SERVER_IP"/update_config_value "SERVER_IP"/g' install.sh

echo "✅ Замена выполнена успешно!"
echo ""
echo "📊 Проверка результатов:"
echo "Осталось вызовов update_env_value для параметров панели:"
grep -c 'update_env_value "INBOUND_ID\|TRANSPORT\|SECURITY\|REALITY_\|TLS_\|XUI_PASSWORD\|XUI_API_TOKEN\|XUI_DB_PATH\|SERVER_ADDRESS\|XUI_USERNAME\|SERVER_IP"' install.sh || echo "0"

# Made with Bob
