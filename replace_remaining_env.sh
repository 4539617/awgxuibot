#!/bin/bash
# Замена ВСЕХ оставшихся update_env_value на update_config_value

echo "🔄 Замена ВСЕХ оставшихся вызовов update_env_value..."

# Заменяем все оставшиеся параметры
sed -i 's/update_env_value "API_TIMEOUT"/update_config_value "API_TIMEOUT"/g' install.sh
sed -i 's/update_env_value "SERVER_PORT"/update_config_value "SERVER_PORT"/g' install.sh
sed -i 's/update_env_value "XHTTP_MODE"/update_config_value "XHTTP_MODE"/g' install.sh
sed -i 's/update_env_value "MAX_TRAFFIC_GB"/update_config_value "MAX_TRAFFIC_GB"/g' install.sh
sed -i 's/update_env_value "MAX_DAYS"/update_config_value "MAX_DAYS"/g' install.sh
sed -i 's/update_env_value "MIN_DAYS"/update_config_value "MIN_DAYS"/g' install.sh
sed -i 's/update_env_value "DEFAULT_TRAFFIC_GB"/update_config_value "DEFAULT_TRAFFIC_GB"/g' install.sh
sed -i 's/update_env_value "DEFAULT_DAYS"/update_config_value "DEFAULT_DAYS"/g' install.sh
sed -i 's/update_env_value "DB_PATH"/update_config_value "DB_PATH"/g' install.sh
sed -i 's/update_env_value "DB_BACKUP_ENABLED"/update_config_value "DB_BACKUP_ENABLED"/g' install.sh
sed -i 's/update_env_value "DB_BACKUP_INTERVAL"/update_config_value "DB_BACKUP_INTERVAL"/g' install.sh
sed -i 's/update_env_value "LOG_LEVEL"/update_config_value "LOG_LEVEL"/g' install.sh
sed -i 's/update_env_value "LOG_FILE_ENABLED"/update_config_value "LOG_FILE_ENABLED"/g' install.sh
sed -i 's/update_env_value "LOG_FILE_PATH"/update_config_value "LOG_FILE_PATH"/g' install.sh
sed -i 's/update_env_value "LOG_MAX_SIZE_MB"/update_config_value "LOG_MAX_SIZE_MB"/g' install.sh
sed -i 's/update_env_value "LOG_BACKUP_COUNT"/update_config_value "LOG_BACKUP_COUNT"/g' install.sh
sed -i 's/update_env_value "XUI_BOT_TOKEN"/update_config_value "XUI_BOT_TOKEN"/g' install.sh
sed -i 's/update_env_value "ADMIN_IDS"/update_config_value "ADMIN_IDS"/g' install.sh
sed -i 's/update_env_value "XUI_URL"/update_config_value "XUI_URL"/g' install.sh
sed -i 's/update_env_value "XUI_VERSION"/update_config_value "XUI_VERSION"/g' install.sh

echo "✅ Замена выполнена!"
echo ""
echo "📊 Проверка: осталось вызовов update_env_value:"
grep -c 'update_env_value' install.sh || echo "0"

# Made with Bob
