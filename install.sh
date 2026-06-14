#!/bin/bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Константы
WORK_DIR="/opt/awgxuibot"

# ============================================
# НАСТРОЙКИ REALITY ПО УМОЛЧАНИЮ
# Измените эти значения перед установкой
# ============================================
DEFAULT_REALITY_SNI="www.nvidia.com"
DEFAULT_REALITY_FINGERPRINT="edge"  # Варианты: edge, chrome, firefox, safari

# ============================================
# НАСТРОЙКИ SSL СЕРТИФИКАТА
# ============================================
# Включить проверку и переиспользование существующих SSL сертификатов
# true  - проверять существующие сертификаты и предлагать их использовать (рекомендуется)
# false - всегда запрашивать новый сертификат при установке 3x-ui (может привести к Rate Limit)
ENABLE_CERT_REUSE="true"
# ============================================

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   awgbot + xuibot Installer${NC}"
echo -e "${BLUE}   AWG + XUI Management${NC}"
echo -e "${BLUE}========================================${NC}\n"

# Проверка прав root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}❌ Запустите с правами root (sudo ./install.sh)${NC}"
    exit 1
fi

# Автоматический переход в рабочую директорию
if [ -d "$WORK_DIR" ]; then
    cd "$WORK_DIR" || {
        echo -e "${RED}❌ Не удалось перейти в $WORK_DIR${NC}"
        exit 1
    }
fi

# Проверка наличия файлов проекта
if [ ! -f "docker-compose.awgbot.yml" ] || [ ! -f "Dockerfile" ] || [ ! -f "package.json" ]; then
    echo -e "${RED}❌ Файлы проекта не найдены!${NC}"
    echo -e "${YELLOW}Пожалуйста, сначала склонируйте репозиторий:${NC}"
    echo -e "${BLUE}  git clone https://github.com/4539617/awgxuibot.git ${WORK_DIR}${NC}"
    echo -e "${BLUE}  cd ${WORK_DIR}${NC}"
    echo -e "${BLUE}  bash install.sh${NC}"
    exit 1
fi

# Проверка и создание рабочего каталога
if [ "$(pwd)" != "$WORK_DIR" ]; then
    echo -e "${YELLOW}📁 Текущая директория: $(pwd)${NC}"
    echo -e "${YELLOW}📁 Рабочая директория должна быть: ${WORK_DIR}${NC}"
    
    if [ -d "$WORK_DIR" ]; then
        echo -e "${YELLOW}⚠ Директория ${WORK_DIR} уже существует${NC}"
        read -p "Переместить файлы в ${WORK_DIR}? (нажмите Enter для подтверждения или 0 для отмены): " move_files
        if [[ "$move_files" != "0" ]]; then
            echo -e "${YELLOW}📦 Перемещение файлов...${NC}"
            mkdir -p "$WORK_DIR"
            cp -r * "$WORK_DIR/" 2>/dev/null || true
            cp -r .* "$WORK_DIR/" 2>/dev/null || true
            cd "$WORK_DIR"
            echo -e "${GREEN}✅ Файлы перемещены в ${WORK_DIR}${NC}"
        fi
    else
        echo -e "${YELLOW}📦 Создание рабочей директории...${NC}"
        mkdir -p "$WORK_DIR"
        cp -r * "$WORK_DIR/" 2>/dev/null || true
        cp -r .* "$WORK_DIR/" 2>/dev/null || true
        cd "$WORK_DIR"
        echo -e "${GREEN}✅ Рабочая директория создана: ${WORK_DIR}${NC}"
    fi
fi

# Функция установки Docker
install_docker() {
    if ! command -v docker &> /dev/null; then
        echo -e "${YELLOW}📦 Установка Docker...${NC}"
        curl -fsSL https://get.docker.com -o get-docker.sh
        sh get-docker.sh
        rm get-docker.sh
        systemctl enable docker
        systemctl start docker
        echo -e "${GREEN}✅ Docker установлен${NC}"
    else
        echo -e "${GREEN}✅ Docker уже установлен${NC}"
    fi
    
    # Проверка и обновление Docker Compose
    echo -e "${YELLOW}🔍 Проверка Docker Compose...${NC}"
    
    # Проверяем наличие V2
    if docker compose version &> /dev/null 2>&1; then
        echo -e "${GREEN}✅ Docker Compose V2 установлен${NC}"
        export DOCKER_COMPOSE_CMD="docker compose"
    # Проверяем наличие V1
    elif command -v docker-compose &> /dev/null; then
        COMPOSE_V1_VERSION=$(docker-compose version --short 2>/dev/null || echo "unknown")
        echo -e "${GREEN}✅ Docker Compose V1 установлен (версия: ${COMPOSE_V1_VERSION})${NC}"
        export DOCKER_COMPOSE_CMD="docker-compose"
        
        # Пробуем тихо обновить до V2 в фоне (не блокируем выполнение)
        echo -e "${YELLOW}💡 Попытка обновления до Docker Compose V2 в фоне...${NC}"
        export DEBIAN_FRONTEND=noninteractive
        (apt-get update -qq && apt-get install -y -qq docker-compose-plugin) &> /dev/null &
        unset DEBIAN_FRONTEND
        
        # Проверяем успешность обновления
        sleep 2
        if docker compose version &> /dev/null 2>&1; then
            echo -e "${GREEN}✅ Docker Compose V2 успешно установлен!${NC}"
            export DOCKER_COMPOSE_CMD="docker compose"
        fi
    # Если ничего не установлено
    else
        echo -e "${YELLOW}📦 Установка Docker Compose...${NC}"
        
        export DEBIAN_FRONTEND=noninteractive
        
        # Пробуем установить V2
        echo -e "${YELLOW}   Попытка установки V2...${NC}"
        if apt-get update -qq && apt-get install -y -qq docker-compose-plugin; then
            echo -e "${GREEN}✅ Docker Compose V2 установлен${NC}"
            export DOCKER_COMPOSE_CMD="docker compose"
        else
            # Fallback на V1
            echo -e "${YELLOW}   Установка V1 (fallback)...${NC}"
            if apt-get install -y -qq docker-compose; then
                echo -e "${GREEN}✅ Docker Compose V1 установлен${NC}"
                export DOCKER_COMPOSE_CMD="docker-compose"
            else
                echo -e "${RED}❌ Не удалось установить Docker Compose${NC}"
                exit 1
            fi
        fi
        unset DEBIAN_FRONTEND
    fi
    
    echo -e "${GREEN}✅ Используется: $DOCKER_COMPOSE_CMD${NC}"
}
# Функция проверки и установки Git
check_and_install_git() {
    if ! command -v git &> /dev/null; then
        echo -e "${YELLOW}📦 Git не установлен. Установка...${NC}"
        if command -v apt-get &> /dev/null; then
            apt-get update -qq && apt-get install -y git -qq
        elif command -v yum &> /dev/null; then
            yum install -y git -q
        elif command -v dnf &> /dev/null; then
            dnf install -y git -q
        else
            echo -e "${RED}❌ Не удалось установить Git автоматически${NC}"
            echo -e "${YELLOW}Установите Git вручную и запустите скрипт снова${NC}"
            exit 1
        fi
        
        if command -v git &> /dev/null; then
            echo -e "${GREEN}✅ Git успешно установлен${NC}"
        else
            echo -e "${RED}❌ Не удалось установить Git${NC}"
            exit 1
        fi
    else
        echo -e "${GREEN}✅ Git уже установлен${NC}"
    fi
}

# Функция создания директорий
create_directories() {
    echo -e "${GREEN}📁 Создание директорий...${NC}"
    mkdir -p output logs data
}

# Функция создания .env если не существует
create_env_if_not_exists() {
    if [ ! -f ".env" ]; then
        echo -e "${YELLOW}📝 Создание .env файла с дефолтными значениями...${NC}"
        
        # Получаем IP сервера
        SERVER_IP=$(curl -s ifconfig.me)
        
        cat > .env << EOF
# Server Configuration
SERVER_ADDRESS=${SERVER_IP}
SERVER_IP=${SERVER_IP}
SERVER_PORT=443

# 3x-ui Panel Configuration
API_TIMEOUT=30

# Transport Configuration
XHTTP_MODE=auto

# TLS Configuration
TLS_FINGERPRINT=${DEFAULT_REALITY_FINGERPRINT}
TLS_ALPN=http/1.1

# Traffic Limits
MAX_TRAFFIC_GB=1000
MAX_DAYS=3650
MIN_DAYS=1
DEFAULT_TRAFFIC_GB=100
DEFAULT_DAYS=30

# Database Configuration
DB_PATH=/app/data/bot_users.db
DB_BACKUP_ENABLED=true
DB_BACKUP_INTERVAL=24

# Logging Configuration
LOG_LEVEL=INFO
LOG_FILE_ENABLED=true
LOG_FILE_PATH=/app/logs/bot.log
LOG_MAX_SIZE_MB=10
LOG_BACKUP_COUNT=5

# Dynamic parameters
XUI_BOT_TOKEN=
AWG_BOT_TOKEN=
ADMIN_IDS=
XUI_URL=
XUI_USERNAME=
XUI_PASSWORD=
XUI_DB_PATH=/etc/x-ui/x-ui.db
REALITY_PUBLIC_KEY=
REALITY_PRIVATE_KEY=
REALITY_SHORT_ID=
REALITY_SNI=${DEFAULT_REALITY_SNI}
REALITY_FINGERPRINT=${DEFAULT_REALITY_FINGERPRINT}
TRANSPORT=
SECURITY=reality
TLS_SNI=
INBOUND_ID=1
ALLOW_USER_DNS_QUERIES=true

EOF
        echo -e "${GREEN}✅ .env файл создан с дефолтными значениями${NC}"
    fi
}

# Функция обновления значения в .env
update_env_value() {
    local key=$1
    local value=$2
    
    if grep -q "^${key}=" .env 2>/dev/null; then
        # Обновляем существующее значение
        sed -i "s|^${key}=.*|${key}=${value}|" .env
    else
        # Добавляем новое значение
        echo "${key}=${value}" >> .env
    fi
    
    # Если обновляется XUI_URL, автоматически обновляем SERVER_ADDRESS и TLS_SNI
    if [ "$key" = "XUI_URL" ] && [ -n "$value" ]; then
        # Извлекаем домен/IP из URL (например: https://websrvinfo.run:48531/path -> websrvinfo.run)
        local domain=$(echo "$value" | sed -E 's|^https?://([^:/]+).*|\1|')
        
        if [ -n "$domain" ] && [ "$domain" != "localhost" ] && [ "$domain" != "127.0.0.1" ]; then
            # Проверяем, является ли это доменом (не IP адресом)
            # IP адрес содержит только цифры и точки
            if [[ ! "$domain" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
                # Это домен, обновляем SERVER_ADDRESS и TLS_SNI
                echo -e "${YELLOW}🔄 Обнаружен домен в XUI_URL: ${domain}${NC}"
                
                # Получаем текущий SERVER_ADDRESS
                local current_server_address=$(get_env_value "SERVER_ADDRESS")
                
                # Обновляем SERVER_ADDRESS если он отличается от домена
                if [ "$current_server_address" != "$domain" ]; then
                    echo -e "${YELLOW}🔄 Обновление SERVER_ADDRESS: ${current_server_address} -> ${domain}${NC}"
                    if grep -q "^SERVER_ADDRESS=" .env 2>/dev/null; then
                        sed -i "s|^SERVER_ADDRESS=.*|SERVER_ADDRESS=${domain}|" .env
                    else
                        echo "SERVER_ADDRESS=${domain}" >> .env
                    fi
                fi
                
                # Обновляем TLS_SNI если он пустой или отличается
                local current_tls_sni=$(get_env_value "TLS_SNI")
                if [ -z "$current_tls_sni" ] || [ "$current_tls_sni" != "$domain" ]; then
                    echo -e "${YELLOW}🔄 Обновление TLS_SNI: ${current_tls_sni} -> ${domain}${NC}"
                    if grep -q "^TLS_SNI=" .env 2>/dev/null; then
                        sed -i "s|^TLS_SNI=.*|TLS_SNI=${domain}|" .env
                    else
                        echo "TLS_SNI=${domain}" >> .env
                    fi
                fi
            else
                # Это IP адрес, не обновляем
                echo -e "${BLUE}ℹ️  Обнаружен IP адрес в XUI_URL: ${domain}, SERVER_ADDRESS и TLS_SNI не изменяются${NC}"
            fi
        fi
    fi
}

# Функция получения значения из .env
get_env_value() {
    local key=$1
    grep "^${key}=" .env 2>/dev/null | cut -d'=' -f2 | head -1

# Функция генерации Reality ключей через API панели 3x-ui
generate_reality_keys_via_api() {
    local xui_url=$1
    local api_token=$2
    
    echo -e "${YELLOW}🔑 Генерация Reality ключей через API панели...${NC}"
    
    # Пробуем HTTPS сначала
    local response=$(curl -s -k -w "\n%{http_code}" \
        -H "Authorization: Bearer ${api_token}" \
        -H "Accept: application/json" \
        "${xui_url%/}/panel/api/server/getNewX25519Cert" 2>&1)
    
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | head -n-1)
    
    # Если HTTPS не работает и URL содержит https, пробуем HTTP
    if [ "$http_code" != "200" ] && [[ "$xui_url" =~ ^https:// ]]; then
        echo -e "${YELLOW}⚠️  HTTPS не работает, пробуем HTTP...${NC}"
        local http_url="${xui_url/https:/http:}"
        response=$(curl -s -w "\n%{http_code}" \
            -H "Authorization: Bearer ${api_token}" \
            -H "Accept: application/json" \
            "${http_url%/}/panel/api/server/getNewX25519Cert" 2>&1)
        
        http_code=$(echo "$response" | tail -n1)
        body=$(echo "$response" | head -n-1)
    fi
    
    # Проверяем успешность запроса
    if [ "$http_code" = "200" ] && echo "$body" | grep -q '"success":true'; then
        # Извлекаем ключи из JSON ответа
        local private_key=$(echo "$body" | grep -o '"privateKey":"[^"]*"' | cut -d'"' -f4)
        local public_key=$(echo "$body" | grep -o '"publicKey":"[^"]*"' | cut -d'"' -f4)
        
        if [ -n "$private_key" ] && [ -n "$public_key" ]; then
            echo -e "${GREEN}✅ Reality ключи успешно сгенерированы через API${NC}"
            echo -e "${BLUE}  Private Key: ${private_key:0:20}...${NC}"
            echo -e "${BLUE}  Public Key:  ${public_key:0:20}...${NC}"
            
            # Возвращаем ключи через глобальные переменные
            REALITY_PRIVATE_KEY="$private_key"
            REALITY_PUBLIC_KEY="$public_key"
            return 0
        fi
    fi
    
    echo -e "${YELLOW}⚠️  Не удалось сгенерировать ключи через API (HTTP: $http_code)${NC}"
    return 1
}
}
# Функция извлечения параметров из существующего инбаунда панели
extract_inbound_params() {
    echo -e "${YELLOW}🔍 Извлечение параметров из панели...${NC}"
    
    # Проверка наличия базы данных
    if [ ! -f "/etc/x-ui/x-ui.db" ]; then
        echo -e "${YELLOW}⚠️  База данных 3x-ui не найдена, пропускаем извлечение${NC}"
        return 1
    fi
    
    # Получаем ID первого инбаунда
    local INBOUND_ID=$(sqlite3 /etc/x-ui/x-ui.db "SELECT id FROM inbounds ORDER BY id ASC LIMIT 1;" 2>/dev/null)
    
    if [ -z "$INBOUND_ID" ]; then
        echo -e "${YELLOW}⚠️  Инбаунды не найдены в панели${NC}"
        return 1
    fi
    
    echo -e "${GREEN}✅ Найден инбаунд ID: ${INBOUND_ID}${NC}"
    
    # Извлекаем транспорт и безопасность
    local TRANSPORT=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.network') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
    local SECURITY=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.security') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
    
    if [ -n "$TRANSPORT" ] && [ -n "$SECURITY" ]; then
        echo -e "${BLUE}  Транспорт: ${TRANSPORT}${NC}"
        echo -e "${BLUE}  Безопасность: ${SECURITY}${NC}"
        
        # Обновляем базовые параметры
        update_env_value "INBOUND_ID" "${INBOUND_ID}"
        update_env_value "TRANSPORT" "${TRANSPORT}"
        update_env_value "SECURITY" "${SECURITY}"
    fi
    
    # Если Reality - извлекаем ключи
    if [ "$SECURITY" = "reality" ]; then
        echo -e "${YELLOW}🔑 Извлечение Reality параметров...${NC}"
        
        local REALITY_PUBLIC=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.realitySettings.settings.publicKey') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
        local REALITY_PRIVATE=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.realitySettings.privateKey') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
        local REALITY_SHORT=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.realitySettings.shortIds[0]') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
        local REALITY_SNI=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.realitySettings.serverNames[0]') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
        local REALITY_FINGERPRINT=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.realitySettings.settings.fingerprint') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
        
        if [ -n "$REALITY_PUBLIC" ]; then
            update_env_value "REALITY_PUBLIC_KEY" "${REALITY_PUBLIC}"
            echo -e "${GREEN}  ✓ Public Key обновлен${NC}"
        fi
        
        if [ -n "$REALITY_PRIVATE" ]; then
            update_env_value "REALITY_PRIVATE_KEY" "${REALITY_PRIVATE}"
            echo -e "${GREEN}  ✓ Private Key обновлен${NC}"
        fi
        
        if [ -n "$REALITY_SHORT" ]; then
            update_env_value "REALITY_SHORT_ID" "${REALITY_SHORT}"
            echo -e "${GREEN}  ✓ Short ID обновлен${NC}"
        fi
        
        if [ -n "$REALITY_SNI" ]; then
            update_env_value "REALITY_SNI" "${REALITY_SNI}"
            echo -e "${GREEN}  ✓ SNI обновлен: ${REALITY_SNI}${NC}"
        fi
        
        if [ -n "$REALITY_FINGERPRINT" ]; then
            update_env_value "REALITY_FINGERPRINT" "${REALITY_FINGERPRINT}"
            echo -e "${GREEN}  ✓ Fingerprint обновлен: ${REALITY_FINGERPRINT}${NC}"
        fi
    fi
    
    # Если TLS - извлекаем параметры
    if [ "$SECURITY" = "tls" ]; then
        echo -e "${YELLOW}🔑 Извлечение TLS параметров...${NC}"
        
        local TLS_FINGERPRINT=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.tlsSettings.settings.fingerprint') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
        local TLS_ALPN=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.tlsSettings.alpn[0]') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
        local TLS_SNI=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.tlsSettings.serverName') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
        
        if [ -n "$TLS_FINGERPRINT" ]; then
            update_env_value "TLS_FINGERPRINT" "${TLS_FINGERPRINT}"
            echo -e "${GREEN}  ✓ TLS Fingerprint обновлен: ${TLS_FINGERPRINT}${NC}"
        fi
        
        if [ -n "$TLS_ALPN" ]; then
            update_env_value "TLS_ALPN" "${TLS_ALPN}"
            echo -e "${GREEN}  ✓ TLS ALPN обновлен: ${TLS_ALPN}${NC}"
        fi
        
        if [ -n "$TLS_SNI" ] && [ "$TLS_SNI" != "null" ]; then
            update_env_value "TLS_SNI" "${TLS_SNI}"
            echo -e "${GREEN}  ✓ TLS SNI обновлен: ${TLS_SNI}${NC}"
        fi
    fi
    
    echo -e "${GREEN}✅ Параметры успешно извлечены из панели${NC}"
    return 0
}


# Функция создания статических параметров
create_static_params() {
    echo -e "${YELLOW}📋 Создание статических параметров...${NC}"
    
    # 3x-ui Panel статические параметры
    update_env_value "XUI_DB_PATH" "/etc/x-ui/x-ui.db"
    update_env_value "API_TIMEOUT" "30"
    
    # VPN Server статические параметры
    update_env_value "SERVER_PORT" "443"
    
    # TLS статические параметры
    update_env_value "TLS_FINGERPRINT" "${DEFAULT_REALITY_FINGERPRINT}"
    update_env_value "TLS_ALPN" "http/1.1"
    
    # Reality статические параметры
    update_env_value "REALITY_SNI" "${DEFAULT_REALITY_SNI}"
    update_env_value "REALITY_FINGERPRINT" "${DEFAULT_REALITY_FINGERPRINT}"
    
    # xHTTP статические параметры
    update_env_value "XHTTP_MODE" "auto"
    
    # Лимиты
    update_env_value "MAX_TRAFFIC_GB" "1000"
    update_env_value "MAX_DAYS" "3650"
    update_env_value "MIN_DAYS" "1"
    update_env_value "DEFAULT_TRAFFIC_GB" "100"
    update_env_value "DEFAULT_DAYS" "30"
    
    # База данных
    update_env_value "DB_PATH" "/app/data/bot_users.db"
    update_env_value "DB_BACKUP_ENABLED" "true"
    update_env_value "DB_BACKUP_INTERVAL" "24"
    
    # Логирование
    update_env_value "LOG_LEVEL" "INFO"
    update_env_value "LOG_FILE_ENABLED" "true"
    update_env_value "LOG_FILE_PATH" "/app/logs/bot.log"
    update_env_value "LOG_MAX_SIZE_MB" "10"
    update_env_value "LOG_BACKUP_COUNT" "5"
    
    echo -e "${GREEN}✅ Статические параметры созданы${NC}"
}

# Функция интерактивного ввода секретных параметров
interactive_setup() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Настройка Параметров Бота${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    create_env_if_not_exists
    
    # Создаем статические параметры
    create_static_params
    
    # Получаем IP сервера
    SERVER_IP=$(curl -s ifconfig.me)
    
    # ==================== Telegram Bot ====================
    echo -e "\n${GREEN}📱 Настройка Telegram Bot${NC}\n"
    
    XUI_BOT_TOKEN=$(get_env_value "XUI_BOT_TOKEN")
    if [ -z "$XUI_BOT_TOKEN" ]; then
        read -p "Введите XUI_BOT_TOKEN: " XUI_BOT_TOKEN
        update_env_value "XUI_BOT_TOKEN" "$XUI_BOT_TOKEN"
    else
        echo -e "XUI_BOT_TOKEN: ${XUI_BOT_TOKEN:0:10}... ${GREEN}✓${NC}"
    fi
    
    ADMIN_IDS=$(get_env_value "ADMIN_IDS")
    if [ -z "$ADMIN_IDS" ]; then
        read -p "Введите ADMIN_IDS (ID администраторов через запятую): " ADMIN_IDS
        update_env_value "ADMIN_IDS" "$ADMIN_IDS"
    else
        echo -e "ADMIN_IDS: $ADMIN_IDS ${GREEN}✓${NC}"
    fi
    
    # ==================== Автоматическое заполнение ====================
    echo -e "\n${GREEN}🔧 Автоматическое заполнение параметров...${NC}"
    
    # IP сервера
    update_env_value "SERVER_ADDRESS" "$SERVER_IP"
    update_env_value "SERVER_IP" "$SERVER_IP"
    
    # ==================== Проверка данных 3x-ui ====================
    XUI_URL=$(get_env_value "XUI_URL")
    XUI_USERNAME=$(get_env_value "XUI_USERNAME")
    XUI_PASSWORD=$(get_env_value "XUI_PASSWORD")
    REALITY_PUBLIC_KEY=$(get_env_value "REALITY_PUBLIC_KEY")
    REALITY_SHORT_ID=$(get_env_value "REALITY_SHORT_ID")
    
    if [ -z "$XUI_URL" ] || [ -z "$REALITY_PUBLIC_KEY" ]; then
        echo -e "\n${RED}❌ Данные 3x-ui панели не найдены!${NC}"
        echo -e "${YELLOW}Сначала установите 3x-ui панель (пункт 5 в меню)${NC}"
        return 1
    fi
    
    # Не устанавливаем фиксированные значения здесь
    # Они будут установлены при создании конкретного типа подключения
    
    echo -e "${GREEN}✅ Все параметры настроены!${NC}"
}

# Функция установки бота
install_bot() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Установка XUIBot${NC}"
    echo -e "${BLUE}   XUI Management Bot${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    # Переходим в рабочую директорию
    cd /opt/awgxuibot || {
        echo -e "${RED}❌ Ошибка: не удалось перейти в /opt/awgxuibot${NC}"
        exit 1
    }
    
    # Интерактивная настройка параметров
    interactive_setup
    
    # Обновляем SERVER_ADDRESS и TLS_SNI из XUI_URL если он установлен
    XUI_URL=$(get_env_value "XUI_URL")
    if [ -n "$XUI_URL" ]; then
        DOMAIN=$(echo "$XUI_URL" | sed -E 's|^https?://([^:/]+).*|\1|')
        if [ -n "$DOMAIN" ] && [[ ! "$DOMAIN" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            echo -e "${YELLOW}🔄 Обнаружен домен в XUI_URL: ${DOMAIN}${NC}"
            CURRENT_SERVER=$(get_env_value "SERVER_ADDRESS")
            if [ "$CURRENT_SERVER" != "$DOMAIN" ]; then
                update_env_value "SERVER_ADDRESS" "$DOMAIN"
                echo -e "${GREEN}✅ SERVER_ADDRESS обновлён: ${DOMAIN}${NC}"
            fi
            CURRENT_TLS_SNI=$(get_env_value "TLS_SNI")
            if [ "$CURRENT_TLS_SNI" != "$DOMAIN" ]; then
                update_env_value "TLS_SNI" "$DOMAIN"
                echo -e "${GREEN}✅ TLS_SNI обновлён: ${DOMAIN}${NC}"
            fi
        fi
    fi
    
    # Извлекаем параметры из inbound
    extract_inbound_params
    
    # Остановка старых контейнеров
    echo -e "\n${YELLOW}🛑 Остановка старых контейнеров...${NC}"
    docker stop xuibot 2>/dev/null || true
    docker rm xuibot 2>/dev/null || true
    
    # Проверка docker-compose.xuibot.yml
    echo -e "\n${YELLOW}🔍 Проверка конфигурации...${NC}"
    if ! $DOCKER_COMPOSE_CMD -f docker-compose.xuibot.yml config > /dev/null 2>&1; then
        echo -e "${RED}❌ Ошибка в docker-compose.xuibot.yml${NC}"
        echo -e "${YELLOW}Запуск диагностики:${NC}"
        $DOCKER_COMPOSE_CMD -f docker-compose.xuibot.yml config
        exit 1
    fi
    echo -e "${GREEN}✅ Конфигурация корректна${NC}"
    
    # Запуск XUIBot
    echo -e "\n${YELLOW}🐳 Сборка и запуск XUIBot...${NC}"
    echo -e "${BLUE}Это может занять несколько минут...${NC}\n"
    
    if ! $DOCKER_COMPOSE_CMD -f docker-compose.xuibot.yml up -d --build; then
        echo -e "\n${RED}❌ Ошибка при запуске контейнера${NC}"
        echo -e "${YELLOW}Проверьте логи:${NC}"
        echo -e "  $DOCKER_COMPOSE_CMD -f docker-compose.xuibot.yml logs"
        exit 1
    fi
    
    # Проверка
    echo -e "\n${YELLOW}⏳ Ожидание запуска контейнера...${NC}"
    sleep 5
    
    # Проверка статуса контейнера
    XUI_STATUS=$(docker ps --filter name=xuibot --format "{{.Status}}" 2>/dev/null || echo "not found")
    
    echo -e "\n${GREEN}✅ XUIBot установлен!${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo -e "${GREEN}📊 Статус контейнера:${NC}"
    
    if [[ "$XUI_STATUS" == *"Up"* ]]; then
        echo -e "  XUIBot: ${GREEN}✓ Работает${NC}"
    else
        echo -e "  XUIBot: ${RED}✗ Не запущен ($XUI_STATUS)${NC}"
    fi
    
    echo -e "\n${YELLOW}📋 Логи XUIBot (последние 50 строк):${NC}"
    docker logs --tail=50 xuibot 2>&1 || echo -e "${RED}Не удалось получить логи${NC}"
    
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${GREEN}💡 Полезные команды:${NC}"
    echo -e "  Логи: ${YELLOW}docker logs -f xuibot${NC}"
    echo -e "  Статус: ${YELLOW}docker ps${NC}"
    echo -e "  Перезапуск: ${YELLOW}$DOCKER_COMPOSE_CMD -f docker-compose.xuibot.yml restart${NC}"
    echo -e "  Остановка: ${YELLOW}$DOCKER_COMPOSE_CMD -f docker-compose.xuibot.yml down${NC}"
}

# Функция показа логов
show_logs() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Логи XUIBot${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    echo -e "${YELLOW}📋 Логи XUIBot (последние 50 строк):${NC}"
    docker logs --tail=50 xuibot 2>/dev/null || echo -e "${RED}Контейнер xuibot не запущен${NC}"
}

# Функция удаления бота
remove_bot() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Удаление XUIBot${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    read -p "⚠️  Вы уверены что хотите удалить XUIBot? (нажмите Enter для подтверждения или 0 для отмены): " confirm
    
    if [[ "$confirm" == "0" ]]; then
        echo -e "${YELLOW}Отменено${NC}"
        return
    fi
    
    echo -e "${YELLOW}🗑️  Удаление бота...${NC}"
    
    # Остановка и удаление контейнера
    echo -e "${YELLOW}🛑 Остановка контейнера...${NC}"
    $DOCKER_COMPOSE_CMD -f docker-compose.xuibot.yml down
    
    # Удаление образа
    echo -e "${YELLOW}🗑️  Удаление образа...${NC}"
    docker rmi netcrazexuibot-xuibot 2>/dev/null || true
    
    echo -e "${GREEN}✅ XUIBot удален!${NC}"
}
# ============================================
# XUI Bot Functions (отдельные функции для XUI бота)
# ============================================

# Функция установки XUI бота
install_xuibot() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Установка XUI Бота${NC}"
    echo -e "${BLUE}   3x-ui Panel Management${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    # Проверка установки 3x-ui панели
    if ! systemctl is-active --quiet x-ui; then
        echo -e "${RED}❌ 3x-ui панель не установлена или не запущена!${NC}"
        echo -e "${YELLOW}Сначала установите 3x-ui Panel (пункт 9)${NC}"
        echo -e "\n${CYAN}Нажмите Enter для возврата в главное меню...${NC}"
        read
        return
    fi
    
    # Проверка наличия базы данных
    if [ ! -f "/etc/x-ui/x-ui.db" ]; then
        echo -e "${RED}❌ База данных 3x-ui не найдена!${NC}"
        echo -e "${YELLOW}Сначала установите 3x-ui Panel (пункт 9)${NC}"
        echo -e "\n${CYAN}Нажмите Enter для возврата в главное меню...${NC}"
        read
        return
    fi
    
    # Создание .env файла если не существует
    create_env_if_not_exists
    
    # Проверка XUI_URL, XUI_USERNAME, XUI_PASSWORD
    echo -e "\n${YELLOW}🔍 Проверка параметров 3x-ui панели...${NC}"
    
    if ! grep -q "^XUI_URL=.\+" .env; then
        echo -e "${YELLOW}📝 Настройка параметров 3x-ui панели${NC}\n"
        read -p "Введите XUI_URL: " xui_url
        update_env_value "XUI_URL" "$xui_url"
    fi
    
    if ! grep -q "^XUI_USERNAME=.\+" .env; then
        read -p "Введите XUI_USERNAME: " xui_username
        update_env_value "XUI_USERNAME" "$xui_username"
    fi
    
    if ! grep -q "^XUI_PASSWORD=.\+" .env; then
        read -p "Введите XUI_PASSWORD: " xui_password
        update_env_value "XUI_PASSWORD" "$xui_password"
    fi
    
    echo -e "${GREEN}✅ Параметры 3x-ui панели настроены${NC}\n"
    
    # Обновляем SERVER_ADDRESS и TLS_SNI из XUI_URL если он уже установлен
    XUI_URL=$(get_env_value "XUI_URL")
    if [ -n "$XUI_URL" ]; then
        # Извлекаем домен/IP из URL
        DOMAIN=$(echo "$XUI_URL" | sed -E 's|^https?://([^:/]+).*|\1|')
        
        # Проверяем, является ли это доменом (не IP адресом)
        if [ -n "$DOMAIN" ] && [[ ! "$DOMAIN" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            echo -e "${YELLOW}🔄 Обнаружен домен в XUI_URL: ${DOMAIN}${NC}"
            
            # Обновляем SERVER_ADDRESS
            CURRENT_SERVER=$(get_env_value "SERVER_ADDRESS")
            if [ "$CURRENT_SERVER" != "$DOMAIN" ]; then
                update_env_value "SERVER_ADDRESS" "$DOMAIN"
                echo -e "${GREEN}✅ SERVER_ADDRESS обновлён: ${DOMAIN}${NC}"
            fi
            
            # Обновляем TLS_SNI
            CURRENT_TLS_SNI=$(get_env_value "TLS_SNI")
            if [ "$CURRENT_TLS_SNI" != "$DOMAIN" ]; then
                update_env_value "TLS_SNI" "$DOMAIN"
                echo -e "${GREEN}✅ TLS_SNI обновлён: ${DOMAIN}${NC}"
            fi
        fi
    fi
    
    # Автоматическое определение параметров из первого инбаунда
    echo -e "${YELLOW}🔍 Анализ существующих инбаундов...${NC}"
    
    FIRST_INBOUND_ID=$(sqlite3 /etc/x-ui/x-ui.db "SELECT id FROM inbounds ORDER BY id ASC LIMIT 1;" 2>/dev/null)
    
    if [ -n "$FIRST_INBOUND_ID" ]; then
        echo -e "${GREEN}✅ Найден инбаунд ID: ${FIRST_INBOUND_ID}${NC}"
        
        # Получаем транспорт и безопасность
        TRANSPORT=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.network') FROM inbounds WHERE id=${FIRST_INBOUND_ID};" 2>/dev/null)
        SECURITY=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.security') FROM inbounds WHERE id=${FIRST_INBOUND_ID};" 2>/dev/null)
        
        echo -e "${BLUE}Транспорт: ${TRANSPORT}, Безопасность: ${SECURITY}${NC}"
        
        # Сохраняем INBOUND_ID, TRANSPORT и SECURITY
        update_env_value "INBOUND_ID" "${FIRST_INBOUND_ID}"
        update_env_value "TRANSPORT" "${TRANSPORT}"
        update_env_value "SECURITY" "${SECURITY}"
        
        # Если security = reality - извлекаем все Reality параметры
        if [ "$SECURITY" = "reality" ]; then
            echo -e "${YELLOW}🔑 Обнаружен Reality, извлекаем параметры...${NC}"
            
            REALITY_PUBLIC=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.realitySettings.settings.publicKey') FROM inbounds WHERE id=${FIRST_INBOUND_ID};" 2>/dev/null)
            REALITY_PRIVATE=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.realitySettings.privateKey') FROM inbounds WHERE id=${FIRST_INBOUND_ID};" 2>/dev/null)
            REALITY_SHORT=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.realitySettings.shortIds[0]') FROM inbounds WHERE id=${FIRST_INBOUND_ID};" 2>/dev/null)
            REALITY_SNI=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.realitySettings.serverNames[0]') FROM inbounds WHERE id=${FIRST_INBOUND_ID};" 2>/dev/null)
            REALITY_FINGERPRINT=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.realitySettings.settings.fingerprint') FROM inbounds WHERE id=${FIRST_INBOUND_ID};" 2>/dev/null)
            
            if [ -n "$REALITY_PUBLIC" ] && [ -n "$REALITY_PRIVATE" ] && [ -n "$REALITY_SHORT" ]; then
                echo -e "${GREEN}✅ Reality параметры извлечены из инбаунда${NC}"
                
                # Сохраняем все Reality параметры
                update_env_value "REALITY_PUBLIC_KEY" "${REALITY_PUBLIC}"
                update_env_value "REALITY_PRIVATE_KEY" "${REALITY_PRIVATE}"
                update_env_value "REALITY_SHORT_ID" "${REALITY_SHORT}"
                
                echo -e "${BLUE}  Public Key: ${REALITY_PUBLIC:0:20}...${NC}"
                echo -e "${BLUE}  Short ID: ${REALITY_SHORT}${NC}"
                
                # Сохраняем SNI и Fingerprint (обязательно)
                if [ -n "$REALITY_SNI" ]; then
                    update_env_value "REALITY_SNI" "${REALITY_SNI}"
                    echo -e "${BLUE}  SNI: ${REALITY_SNI}${NC}"
                fi
                
                if [ -n "$REALITY_FINGERPRINT" ]; then
                    update_env_value "REALITY_FINGERPRINT" "${REALITY_FINGERPRINT}"
                    echo -e "${BLUE}  Fingerprint: ${REALITY_FINGERPRINT}${NC}"
                fi
            else
                echo -e "${YELLOW}⚠️  Не удалось извлечь Reality ключи, запрашиваем вручную...${NC}\n"
                read -p "Введите REALITY_PUBLIC_KEY: " reality_pub
                read -p "Введите REALITY_PRIVATE_KEY: " reality_priv
                read -p "Введите REALITY_SHORT_ID: " reality_short
                
                update_env_value "REALITY_PUBLIC_KEY" "${reality_pub}"
                update_env_value "REALITY_PRIVATE_KEY" "${reality_priv}"
                update_env_value "REALITY_SHORT_ID" "${reality_short}"
            fi
        fi
        
        # Если security = tls - извлекаем TLS параметры
        if [ "$SECURITY" = "tls" ]; then
            echo -e "${YELLOW}🔑 Обнаружен TLS, извлекаем параметры...${NC}"
            
            TLS_FINGERPRINT=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.tlsSettings.settings.fingerprint') FROM inbounds WHERE id=${FIRST_INBOUND_ID};" 2>/dev/null)
            TLS_ALPN=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.tlsSettings.alpn[0]') FROM inbounds WHERE id=${FIRST_INBOUND_ID};" 2>/dev/null)
            TLS_SNI=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.tlsSettings.serverName') FROM inbounds WHERE id=${FIRST_INBOUND_ID};" 2>/dev/null)
            
            if [ -n "$TLS_FINGERPRINT" ] && [ "$TLS_FINGERPRINT" != "null" ]; then
                update_env_value "TLS_FINGERPRINT" "${TLS_FINGERPRINT}"
                echo -e "${GREEN}✅ TLS Fingerprint: ${TLS_FINGERPRINT}${NC}"
            fi
            
            if [ -n "$TLS_ALPN" ] && [ "$TLS_ALPN" != "null" ]; then
                update_env_value "TLS_ALPN" "${TLS_ALPN}"
                echo -e "${GREEN}✅ TLS ALPN: ${TLS_ALPN}${NC}"
            fi
            
            if [ -n "$TLS_SNI" ] && [ "$TLS_SNI" != "null" ]; then
                update_env_value "TLS_SNI" "${TLS_SNI}"
                echo -e "${GREEN}✅ TLS SNI: ${TLS_SNI}${NC}"
            fi
        fi
    else
        echo -e "${YELLOW}⚠️  Инбаунды не найдены${NC}"
    fi
    
    echo ""
    
    # Проверка XUI_BOT_TOKEN
    if ! grep -q "^XUI_BOT_TOKEN=.\+" .env; then
        echo -e "${YELLOW}📱 Настройка Telegram Bot для XUI${NC}\n"
        read -p "Введите XUI_BOT_TOKEN для XUI бота: " xui_token
        update_env_value "XUI_BOT_TOKEN" "$xui_token"
    fi
    
    # Проверка ADMIN_IDS
    if ! grep -q "^ADMIN_IDS=.\+" .env; then
        read -p "Введите ADMIN_IDS (через запятую): " admin_ids
        update_env_value "ADMIN_IDS" "$admin_ids"
    fi
    
    # Остановка старых контейнеров
    echo -e "\n${YELLOW}🛑 Остановка старых контейнеров...${NC}"
    docker stop xuibot 2>/dev/null || true
    docker rm xuibot 2>/dev/null || true
    docker stop netcrazybot 2>/dev/null || true
    docker rm netcrazybot 2>/dev/null || true
    
    # Запуск только XUI бота
    echo -e "\n${YELLOW}🐳 Сборка и запуск XUI бота...${NC}"
    echo -e "${BLUE}Это может занять несколько минут...${NC}\n"
    
    if ! $DOCKER_COMPOSE_CMD -f docker-compose.xuibot.yml up -d --build; then
        echo -e "\n${RED}❌ Ошибка при запуске XUI бота${NC}"
        echo -e "${YELLOW}Проверьте логи: docker logs xuibot${NC}"
        return
    fi
    
    # Проверка
    echo -e "\n${YELLOW}⏳ Ожидание запуска контейнера...${NC}"
    sleep 5
    
    XUI_STATUS=$(docker ps --filter name=xuibot --format "{{.Status}}" 2>/dev/null || echo "not found")
    
    echo -e "\n${GREEN}✅ XUI Бот установлен!${NC}"
    echo -e "${BLUE}========================================${NC}"
    
    if [[ "$XUI_STATUS" == *"Up"* ]]; then
        echo -e "${GREEN}📊 Статус: ✓ Работает${NC}"
    else
        echo -e "${RED}📊 Статус: ✗ Не запущен ($XUI_STATUS)${NC}"
    fi
    
    echo -e "\n${YELLOW}📋 Логи XUI бота (последние 15 строк):${NC}"
    docker logs --tail=15 xuibot 2>&1 || echo -e "${RED}Не удалось получить логи${NC}"
    
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${GREEN}💡 Полезные команды:${NC}"
    echo -e "  Логи: ${YELLOW}docker logs -f xuibot${NC}"
    echo -e "  Статус: ${YELLOW}docker ps | grep xuibot${NC}"
    echo -e "  Перезапуск: ${YELLOW}docker restart xuibot${NC}"
}

# Функция показа логов XUI бота
show_xuibot_logs() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Логи XUI Бота${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    if ! docker ps --filter name=xuibot --format "{{.Names}}" | grep -q xuibot; then
        echo -e "${RED}❌ Контейнер xuibot не запущен${NC}"
        return
    fi
    
    echo -e "${YELLOW}📋 Логи XUI бота (последние 50 строк):${NC}"
    docker logs --tail=50 xuibot 2>&1
    
    echo -e "\n${YELLOW}Для просмотра в реальном времени:${NC}"
    echo -e "${BLUE}docker logs -f xuibot${NC}"
}

# Функция обновления XUI бота
update_xuibot() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Обновление XUI Бота${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    echo -e "${YELLOW}🔄 Обновление XUI бота...${NC}"
    
    # Переходим в рабочую директорию
    cd /opt/awgxuibot || {
        echo -e "${RED}❌ Ошибка: не удалось перейти в /opt/awgxuibot${NC}"
        return 1
    }
    
    # Проверка наличия git и обновление кода
    if command -v git &> /dev/null; then
        echo -e "${YELLOW}📥 Получение обновлений из репозитория...${NC}"
        
        # Сохраняем текущую ветку
        CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || echo "main")
        
        # Проверяем есть ли изменения
        if git status --porcelain | grep -q .; then
            echo -e "${YELLOW}⚠️  Обнаружены локальные изменения${NC}"
            echo -e "${YELLOW}Создаем резервную копию...${NC}"
            git stash push -m "Auto-stash before update $(date +%Y%m%d_%H%M%S)" 2>/dev/null || true
        fi
        
        # Выполняем git pull
        if git pull origin "$CURRENT_BRANCH" 2>&1 | tee /tmp/git-pull.log; then
            echo -e "${GREEN}✅ Код успешно обновлен${NC}"
        else
            echo -e "${YELLOW}⚠️  Не удалось обновить код из репозитория${NC}"
            echo -e "${YELLOW}Продолжаем с текущей версией...${NC}"
        fi
    else
        echo -e "${YELLOW}⚠️  Git не установлен, пропускаем обновление кода${NC}"
        echo -e "${YELLOW}Пересобираем с текущей версией...${NC}"
    fi
    
    # Обновляем SERVER_ADDRESS и TLS_SNI из XUI_URL (ПОСЛЕ git pull, чтобы всегда выполнялось)
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}📋 Шаг 1: Чтение XUI_URL из .env${NC}"
    XUI_URL=$(get_env_value "XUI_URL")
    echo -e "${GREEN}✓ XUI_URL прочитан: ${XUI_URL}${NC}"
    
    if [ -n "$XUI_URL" ]; then
        echo -e "\n${BLUE}📋 Шаг 2: Извлечение домена из URL${NC}"
        DOMAIN=$(echo "$XUI_URL" | sed -E 's|^https?://([^:/]+).*|\1|')
        echo -e "${GREEN}✓ Извлечён домен/IP: ${DOMAIN}${NC}"
        
        echo -e "\n${BLUE}📋 Шаг 3: Проверка - домен или IP?${NC}"
        if [[ ! "$DOMAIN" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            echo -e "${GREEN}✓ Это ДОМЕН (не IP адрес)${NC}"
            echo -e "${YELLOW}🔄 Будут обновлены SERVER_ADDRESS и TLS_SNI${NC}"
            
            echo -e "\n${BLUE}📋 Шаг 4: Обновление SERVER_ADDRESS${NC}"
            CURRENT_SERVER=$(get_env_value "SERVER_ADDRESS")
            echo -e "${YELLOW}  Текущее значение: ${CURRENT_SERVER}${NC}"
            echo -e "${YELLOW}  Новое значение: ${DOMAIN}${NC}"
            sed -i "s|^SERVER_ADDRESS=.*|SERVER_ADDRESS=${DOMAIN}|" .env
            NEW_SERVER=$(get_env_value "SERVER_ADDRESS")
            echo -e "${GREEN}✅ SERVER_ADDRESS обновлён: ${NEW_SERVER}${NC}"
            
            echo -e "\n${BLUE}📋 Шаг 5: Обновление TLS_SNI${NC}"
            CURRENT_TLS_SNI=$(get_env_value "TLS_SNI")
            echo -e "${YELLOW}  Текущее значение: ${CURRENT_TLS_SNI:-<пусто>}${NC}"
            echo -e "${YELLOW}  Новое значение: ${DOMAIN}${NC}"
            if grep -q "^TLS_SNI=" .env 2>/dev/null; then
                sed -i "s|^TLS_SNI=.*|TLS_SNI=${DOMAIN}|" .env
                echo -e "${GREEN}✓ Строка TLS_SNI обновлена${NC}"
            else
                echo "TLS_SNI=${DOMAIN}" >> .env
                echo -e "${GREEN}✓ Строка TLS_SNI добавлена${NC}"
            fi
            NEW_TLS_SNI=$(get_env_value "TLS_SNI")
            echo -e "${GREEN}✅ TLS_SNI обновлён: ${NEW_TLS_SNI}${NC}"
        else
            echo -e "${YELLOW}⚠️  Это IP АДРЕС (не домен)${NC}"
            echo -e "${BLUE}ℹ️  SERVER_ADDRESS и TLS_SNI НЕ изменяются${NC}"
        fi
    else
        echo -e "${RED}❌ XUI_URL не найден в .env${NC}"
    fi
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
    
    # Извлекаем параметры из панели (TLS_FINGERPRINT, TLS_ALPN и т.д.)
    echo ""
    extract_inbound_params
    echo ""
    
    # Остановка контейнера
    echo -e "${YELLOW}🛑 Остановка контейнера...${NC}"
    docker stop xuibot 2>/dev/null || true
    docker rm xuibot 2>/dev/null || true
    
    # Пересборка образа
    echo -e "${YELLOW}🐳 Пересборка образа...${NC}"
    $DOCKER_COMPOSE_CMD -f docker-compose.xuibot.yml build --no-cache
    
    # Запуск
    echo -e "${YELLOW}🚀 Запуск обновленного контейнера...${NC}"
    $DOCKER_COMPOSE_CMD -f docker-compose.xuibot.yml up -d
    
    sleep 5
    echo -e "\n${GREEN}✅ XUI Бот обновлен!${NC}"
    echo -e "${GREEN}📊 Статус:${NC}"
    docker ps --filter name=xuibot
}

# Функция удаления XUI бота
remove_xuibot() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Удаление XUI Бота${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    read -p "⚠️  Вы уверены что хотите удалить XUI бот? (нажмите Enter для подтверждения или 0 для отмены): " confirm
    
    if [[ "$confirm" == "0" ]]; then
        echo -e "${YELLOW}Отменено${NC}"
        return
    fi
    
    echo -e "${YELLOW}🗑️  Удаление XUI бота...${NC}"
    
    # Остановка и удаление контейнера
    echo -e "${YELLOW}🛑 Остановка контейнера...${NC}"
    docker stop xuibot 2>/dev/null || true
    docker rm xuibot 2>/dev/null || true
    
    # Удаление образа
    echo -e "${YELLOW}🗑️  Удаление образа...${NC}"
    docker rmi awgxuibot-xuibot 2>/dev/null || true
    
    # Очистка XUI_BOT_TOKEN и XUI credentials из .env
    if [ -f ".env" ]; then
        echo -e "${YELLOW}🧹 Очистка XUI настроек из .env...${NC}"
        
        # Удаляем XUI_BOT_TOKEN
        if grep -q "^XUI_BOT_TOKEN=" .env; then
            sed -i '/^XUI_BOT_TOKEN=/d' .env
            echo -e "${GREEN}✅ XUI_BOT_TOKEN удален из .env${NC}"
        fi
        
        # Также удаляем старый TELEGRAM_BOT_TOKEN если он есть (для обратной совместимости)
        if grep -q "^TELEGRAM_BOT_TOKEN=" .env; then
            sed -i '/^TELEGRAM_BOT_TOKEN=/d' .env
            echo -e "${GREEN}✅ TELEGRAM_BOT_TOKEN удален из .env${NC}"
        fi
        
        # Очищаем XUI credentials
        if grep -q "^XUI_URL=" .env; then
            sed -i 's/^XUI_URL=.*/XUI_URL=/' .env
            echo -e "${GREEN}✅ XUI_URL очищен${NC}"
        fi
        
        if grep -q "^XUI_USERNAME=" .env; then
            sed -i 's/^XUI_USERNAME=.*/XUI_USERNAME=/' .env
            echo -e "${GREEN}✅ XUI_USERNAME очищен${NC}"
        fi
        
        if grep -q "^XUI_PASSWORD=" .env; then
            sed -i 's/^XUI_PASSWORD=.*/XUI_PASSWORD=/' .env
            echo -e "${GREEN}✅ XUI_PASSWORD очищен${NC}"
        fi
    fi
    
    echo -e "${GREEN}✅ XUI Бот удален!${NC}"
}

# ============================================
# AWG Bot Functions (отдельные функции для AWG бота)
# ============================================

# Функция установки AWG бота
install_awgbot() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Установка AWGBOT${NC}"
    echo -e "${BLUE}   AWG Management${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    # Проверка наличия AWG сервера
    echo -e "${YELLOW}🔍 Проверка наличия AWG сервера...${NC}"
    local awg_v1_exists=false
    local awg_v2_exists=false
    
    if docker ps -a --format '{{.Names}}' | grep -q "^amnezia-awg$"; then
        awg_v1_exists=true
        echo -e "${GREEN}✅ AWG v1 обнаружен${NC}"
    fi
    
    if docker ps -a --format '{{.Names}}' | grep -q "^amnezia-awg2$"; then
        awg_v2_exists=true
        echo -e "${GREEN}✅ AWG v2 обнаружен${NC}"
    fi
    
    if [ "$awg_v1_exists" = false ] && [ "$awg_v2_exists" = false ]; then
        echo -e "\n${RED}❌ AWG сервер не установлен!${NC}"
        echo -e "${YELLOW}⚠️  AWGBOT требует установленный AWG сервер для работы.${NC}"
        echo -e "${YELLOW}Сначала установите AWG сервер (пункт 3 в меню).${NC}\n"
        read -p "Хотите установить AWG сервер сейчас? (y/n): " install_now
        
        if [ "$install_now" = "y" ]; then
            install_awg
            # Проверяем снова после установки
            if ! docker ps -a --format '{{.Names}}' | grep -qE "^amnezia-awg2?$"; then
                echo -e "\n${RED}❌ AWG сервер не был установлен. Отмена установки AWGBOT.${NC}"
                return 1
            fi
        else
            echo -e "${YELLOW}Установка AWGBOT отменена.${NC}"
            return 1
        fi
    fi
    
    echo -e "\n${GREEN}✅ AWG сервер найден, продолжаем установку AWGBOT...${NC}\n"
    
    # Проверка наличия .env
    if [ ! -f ".env" ]; then
        create_env_if_not_exists
    fi
    
    # Проверка AWG_BOT_TOKEN
    if ! grep -q "^AWG_BOT_TOKEN=.\+" .env; then
        echo -e "${YELLOW}📱 Настройка Telegram Bot для AWG${NC}\n"
        read -p "Введите AWG_BOT_TOKEN для AWG бота: " awg_token
        
        # Добавляем AWG_BOT_TOKEN если его нет
        if ! grep -q "^AWG_BOT_TOKEN=" .env; then
            echo "AWG_BOT_TOKEN=$awg_token" >> .env
        else
            update_env_value "AWG_BOT_TOKEN" "$awg_token"
        fi
    fi
    
    # Проверка ADMIN_IDS
    if ! grep -q "^ADMIN_IDS=.\+" .env; then
        read -p "Введите ADMIN_IDS (через запятую): " admin_ids
        update_env_value "ADMIN_IDS" "$admin_ids"
    fi
    
    # Остановка старых контейнеров
    echo -e "\n${YELLOW}🛑 Остановка старых контейнеров...${NC}"
    docker stop awgbot 2>/dev/null || true
    docker rm awgbot 2>/dev/null || true
    docker stop netcrazybot 2>/dev/null || true
    docker rm netcrazybot 2>/dev/null || true
    
    # Запуск только AWG бота
    echo -e "\n${YELLOW}🐳 Сборка и запуск AWG бота...${NC}"
    echo -e "${BLUE}Это может занять несколько минут...${NC}\n"
    
    if ! $DOCKER_COMPOSE_CMD -f docker-compose.awgbot.yml up -d --build; then
        echo -e "\n${RED}❌ Ошибка при запуске AWG бота${NC}"
        echo -e "${YELLOW}Проверьте логи: docker logs awgbot${NC}"
        return
    fi
    
    # Проверка
    echo -e "\n${YELLOW}⏳ Ожидание запуска контейнера...${NC}"
    sleep 5
    
    AWG_STATUS=$(docker ps --filter name=awgbot --format "{{.Status}}" 2>/dev/null || echo "not found")
    
    echo -e "\n${GREEN}✅ AWG Бот установлен!${NC}"
    echo -e "${BLUE}========================================${NC}"
    
    if [[ "$AWG_STATUS" == *"Up"* ]]; then
        echo -e "${GREEN}📊 Статус: ✓ Работает${NC}"
    else
        echo -e "${RED}📊 Статус: ✗ Не запущен ($AWG_STATUS)${NC}"
    fi
    
    echo -e "\n${YELLOW}📋 Логи AWG бота (последние 15 строк):${NC}"
    docker logs --tail=15 awgbot 2>&1 || echo -e "${RED}Не удалось получить логи${NC}"
    
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${GREEN}💡 Полезные команды:${NC}"
    echo -e "  Логи: ${YELLOW}docker logs -f awgbot${NC}"
    echo -e "  Статус: ${YELLOW}docker ps | grep awgbot${NC}"
    echo -e "  Перезапуск: ${YELLOW}docker restart awgbot${NC}"
}

# Функция показа логов AWG бота
show_awgbot_logs() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Логи AWG Бота${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    if ! docker ps --filter name=awgbot --format "{{.Names}}" | grep -q awgbot; then
        echo -e "${RED}❌ Контейнер awgbot не запущен${NC}"
        return
    fi
    
    echo -e "${YELLOW}📋 Логи AWG бота (последние 50 строк):${NC}"
    docker logs --tail=50 awgbot 2>&1
    
    echo -e "\n${YELLOW}Для просмотра в реальном времени:${NC}"
    echo -e "${BLUE}docker logs -f awgbot${NC}"
}

# Функция обновления AWG бота
update_awgbot() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Пересборка AWG Бота${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    echo -e "${YELLOW}🔄 Пересборка AWG бота...${NC}"
    
    # Проверка наличия git
    if command -v git &> /dev/null; then
        echo -e "${YELLOW}📥 Получение обновлений из репозитория...${NC}"
        
        # Сохраняем текущую ветку
        CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || echo "main")
        
        # Проверяем есть ли изменения
        if git status --porcelain | grep -q .; then
            echo -e "${YELLOW}⚠️  Обнаружены локальные изменения${NC}"
            echo -e "${YELLOW}Создаем резервную копию...${NC}"
            git stash push -m "Auto-stash before update $(date +%Y%m%d_%H%M%S)" 2>/dev/null || true
        fi
        
        # Выполняем git pull
        if git pull origin "$CURRENT_BRANCH" 2>&1 | tee /tmp/git-pull.log; then
            echo -e "${GREEN}✅ Код успешно обновлен${NC}"
        else
            echo -e "${YELLOW}⚠️  Не удалось обновить код из репозитория${NC}"
            echo -e "${YELLOW}Продолжаем с текущей версией...${NC}"
        fi
    else
        echo -e "${YELLOW}⚠️  Git не установлен, пропускаем обновление кода${NC}"
        echo -e "${YELLOW}Пересобираем с текущей версией...${NC}"
    fi
    
    # Проверка и добавление ALLOW_USER_DNS_QUERIES если его нет
    echo -e "\n${BLUE}📋 Проверка параметра ALLOW_USER_DNS_QUERIES${NC}"
    if grep -q "^ALLOW_USER_DNS_QUERIES=" .env 2>/dev/null; then
        CURRENT_VALUE=$(get_env_value "ALLOW_USER_DNS_QUERIES")
        echo -e "${GREEN}✓ Параметр уже существует: ${CURRENT_VALUE}${NC}"
        echo -e "${BLUE}ℹ️  Оставляем текущее значение без изменений${NC}"
    else
        echo -e "${YELLOW}⚠️  Параметр ALLOW_USER_DNS_QUERIES не найден${NC}"
        echo -e "${YELLOW}🔧 Добавляем с значением по умолчанию: true${NC}"
        echo "" >> .env
        echo "# Разрешить обычным пользователям делать DNS запросы" >> .env
        echo "ALLOW_USER_DNS_QUERIES=true" >> .env
        echo -e "${GREEN}✅ Параметр ALLOW_USER_DNS_QUERIES добавлен: true${NC}"
    fi
    
    # Остановка контейнера
    echo -e "\n${YELLOW}🛑 Остановка контейнера...${NC}"
    docker stop awgbot 2>/dev/null || true
    docker rm awgbot 2>/dev/null || true
    
    # Пересборка образа
    echo -e "${YELLOW}🐳 Пересборка образа...${NC}"
    $DOCKER_COMPOSE_CMD -f docker-compose.awgbot.yml build --no-cache
    
    # Запуск
    echo -e "${YELLOW}🚀 Запуск пересобранного контейнера...${NC}"
    $DOCKER_COMPOSE_CMD -f docker-compose.awgbot.yml up -d
    
    sleep 5
    echo -e "\n${GREEN}✅ AWG Бот пересобран!${NC}"
    echo -e "${GREEN}📊 Статус:${NC}"
    docker ps --filter name=awgbot
}

# Функция удаления AWG бота
remove_awgbot() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Удаление AWG Бота${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    read -p "⚠️  Вы уверены что хотите удалить AWG бот? (нажмите Enter для подтверждения или 0 для отмены): " confirm
    
    if [[ "$confirm" == "0" ]]; then
        echo -e "${YELLOW}Отменено${NC}"
        return
    fi
    
    echo -e "${YELLOW}🗑️  Удаление AWG бота...${NC}"
    
    # Остановка и удаление контейнера
    echo -e "${YELLOW}🛑 Остановка контейнера...${NC}"
    docker stop awgbot 2>/dev/null || true
    docker rm awgbot 2>/dev/null || true
    
    # Удаление образа
    echo -e "${YELLOW}🗑️  Удаление образа...${NC}"
    docker rmi awgxuibot-awgbot 2>/dev/null || true
    
    # Очистка AWG_BOT_TOKEN из .env
    if [ -f ".env" ]; then
        echo -e "${YELLOW}🧹 Очистка AWG_BOT_TOKEN из .env...${NC}"
        if grep -q "^AWG_BOT_TOKEN=" .env; then
            sed -i '/^AWG_BOT_TOKEN=/d' .env
            echo -e "${GREEN}✅ AWG_BOT_TOKEN удален из .env${NC}"
        fi
    fi
    
    echo -e "${GREEN}✅ AWG Бот удален!${NC}"
}


# Объединенная функция удаления AWG
remove_awg() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Удаление AWG Сервера${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    # Проверка установленных серверов
    local awg_v1_exists=false
    local awg_v2_exists=false
    
    if docker ps -a --format '{{.Names}}' | grep -q "^amnezia-awg$"; then
        awg_v1_exists=true
    fi
    
    if docker ps -a --format '{{.Names}}' | grep -q "^amnezia-awg2$"; then
        awg_v2_exists=true
    fi
    
    if [ "$awg_v1_exists" = false ] && [ "$awg_v2_exists" = false ]; then
        echo -e "${YELLOW}⚠️  AWG серверы не установлены${NC}"
        return
    fi
    
    # Показываем что установлено
    echo -e "${YELLOW}Установленные AWG серверы:${NC}"
    if [ "$awg_v1_exists" = true ]; then
        echo -e "${GREEN}  ✓ AWG v1${NC}"
    fi
    if [ "$awg_v2_exists" = true ]; then
        echo -e "${GREEN}  ✓ AWG v2${NC}"
    fi
    
    echo -e "\n${YELLOW}Выберите что удалить:${NC}"
    echo -e "${GREEN}1)${NC} Удалить AWG v1"
    echo -e "${GREEN}2)${NC} Удалить AWG v2"
    echo -e "${GREEN}3)${NC} Удалить оба сервера"
    echo -e "${GREEN}0)${NC} Вернуться в главное меню"
    read -p "Введите номер (0-3): " remove_choice
    
    case $remove_choice in
        1)
            if [ "$awg_v1_exists" = false ]; then
                echo -e "${YELLOW}AWG v1 не установлен${NC}"
                return
            fi
            remove_awg_version "v1"
            ;;
        2)
            if [ "$awg_v2_exists" = false ]; then
                echo -e "${YELLOW}AWG v2 не установлен${NC}"
                return
            fi
            remove_awg_version "v2"
            ;;
        3)
            read -p "⚠️  Вы уверены что хотите удалить ВСЕ AWG серверы? (нажмите Enter для подтверждения или 0 для отмены): " confirm
            if [[ "$confirm" == "0" ]]; then
                echo -e "${YELLOW}Отменено${NC}"
                return
            fi
            if [ "$awg_v1_exists" = true ]; then
                remove_awg_version "v1"
            fi
            if [ "$awg_v2_exists" = true ]; then
                remove_awg_version "v2"
            fi
            ;;
        0)
            echo -e "${YELLOW}Отменено${NC}"
            return
            ;;
        *)
            echo -e "${RED}❌ Неверный выбор${NC}"
            return
            ;;
    esac
}

# Функция удаления конкретной версии AWG
remove_awg_version() {
    local version=$1
    local container_name="amnezia-awg"
    local config_path="/opt/amnezia/amnezia-awg"
    
    if [ "$version" = "v2" ]; then
        container_name="amnezia-awg2"
        config_path="/opt/amnezia/amnezia-awg2"
    fi
    
    echo -e "\n${YELLOW}🗑️  Удаление AWG $version...${NC}"
    
    # Остановка и удаление контейнера
    docker stop $container_name 2>/dev/null || true
    docker rm $container_name 2>/dev/null || true
    
    # Удаление конфигурации
    if [ -d "$config_path" ]; then
        rm -rf "$config_path"
        echo -e "${GREEN}✅ Конфигурация AWG $version удалена${NC}"
    fi
    
    echo -e "${GREEN}✅ AWG $version удален!${NC}"
}

# Функция перезапуска XUIBOT
restart_xuibot() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Перезапуск XUIBOT${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    if ! docker ps --filter name=xuibot --format "{{.Names}}" | grep -q xuibot; then
        echo -e "${RED}❌ Контейнер xuibot не запущен${NC}"
        return
    fi
    
    echo -e "${YELLOW}🔄 Перезапуск xuibot...${NC}"
    docker restart xuibot
    
    sleep 3
    echo -e "${GREEN}✅ XUIBOT перезапущен!${NC}"
    docker logs --tail=10 xuibot
}

# Функция перезапуска AWGBOT
restart_awgbot() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Перезапуск AWGBOT${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    if ! docker ps --filter name=awgbot --format "{{.Names}}" | grep -q awgbot; then
        echo -e "${RED}❌ Контейнер awgbot не запущен${NC}"
        return
    fi
    
    echo -e "${YELLOW}🔄 Перезапуск awgbot...${NC}"
    docker restart awgbot
    
    sleep 3
    echo -e "${GREEN}✅ AWGBOT перезапущен!${NC}"
    docker logs --tail=10 awgbot
}

# Функция перезапуска контейнера XUIBOT с rebuild
rebuild_xuibot() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Перезапуск контейнера XUIBOT${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    # Извлекаем параметры из панели перед пересборкой
    extract_inbound_params
    echo ""
    
    echo -e "${YELLOW}🛑 Остановка контейнера xuibot...${NC}"
    $DOCKER_COMPOSE_CMD -f docker-compose.xuibot.yml down 2>/dev/null || true
    
    echo -e "${YELLOW}🔨 Пересборка образа xuibot...${NC}"
    $DOCKER_COMPOSE_CMD -f docker-compose.xuibot.yml build --no-cache
    
    echo -e "${YELLOW}🚀 Запуск контейнера xuibot...${NC}"
    $DOCKER_COMPOSE_CMD -f docker-compose.xuibot.yml up -d
    
    sleep 5
    echo -e "${GREEN}✅ Контейнер XUIBOT перезапущен!${NC}"
    echo -e "\n${YELLOW}📋 Логи (последние 15 строк):${NC}"
    docker logs --tail=15 xuibot
}

# Функция перезапуска контейнера AWGBOT с rebuild
rebuild_awgbot() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Перезапуск контейнера AWGBOT${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    echo -e "${YELLOW}🛑 Остановка контейнера awgbot...${NC}"
    $DOCKER_COMPOSE_CMD -f docker-compose.awgbot.yml down 2>/dev/null || true
    
    echo -e "${YELLOW}🔨 Пересборка образа awgbot...${NC}"
    $DOCKER_COMPOSE_CMD -f docker-compose.awgbot.yml build --no-cache
    
    echo -e "${YELLOW}🚀 Запуск контейнера awgbot...${NC}"
    $DOCKER_COMPOSE_CMD -f docker-compose.awgbot.yml up -d
    
    sleep 5
    echo -e "${GREEN}✅ Контейнер AWGBOT перезапущен!${NC}"
    echo -e "\n${YELLOW}📋 Логи (последние 15 строк):${NC}"
    docker logs --tail=15 awgbot
}

# Функция синхронизации репозитория
sync_repository() {
    echo -e "\n${BLUE}🔄 Синхронизация репозитория...${NC}"
    
    # Проверка наличия git
    if ! command -v git &> /dev/null; then
        echo -e "${YELLOW}⚠️  Git не установлен, пропускаем синхронизацию${NC}"
        return 0
    fi
    
    # Проверка, является ли текущая директория git репозиторием
    if ! git rev-parse --git-dir > /dev/null 2>&1; then
        echo -e "${YELLOW}⚠️  Текущая директория не является git репозиторием${NC}"
        return 0
    fi
    
    # Сохранение локальных изменений (если есть)
    if ! git diff-index --quiet HEAD -- 2>/dev/null; then
        echo -e "${YELLOW}⚠️  Обнаружены локальные изменения, сохраняем...${NC}"
        git stash push -m "Auto-stash before sync $(date +%Y-%m-%d_%H:%M:%S)" 2>/dev/null || true
    fi
    
    # Получение текущей ветки
    local current_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
    if [ -z "$current_branch" ]; then
        current_branch="main"
    fi
    
    # Выполнение git pull
    echo -e "${BLUE}Выполняется git pull origin ${current_branch}...${NC}"
    if git pull origin "$current_branch" 2>&1; then
        echo -e "${GREEN}✅ Репозиторий успешно синхронизирован${NC}"
        return 0
    else
        echo -e "${RED}❌ Ошибка синхронизации репозитория${NC}"
        return 1
    fi
}

# Функция получения username бота через API
get_bot_username() {
    local token=$1
    local bot_name=$2
    
    if [ -z "$token" ]; then
        echo "Unknown"
        return
    fi
    
    # Пробуем получить через API
    local username=$(curl -s "https://api.telegram.org/bot${token}/getMe" 2>/dev/null | grep -o '"username":"[^"]*"' | cut -d'"' -f4)
    
    if [ -n "$username" ]; then
        echo "$username"
    else
        echo "Unknown"
    fi
}

# Функция показа статуса системы
show_status() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   СТАТУС СИСТЕМЫ${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    # ============================================
    # 3X-UI PANEL
    # ============================================
    echo -e "${YELLOW}${BOLD}3X-UI PANEL:${NC}"
    
    if systemctl is-active --quiet x-ui; then
        # Получаем версию несколькими способами
        local xui_version=""
        
        # Способ 1: Из исполняемого файла x-ui (основной метод)
        if [ -f "/usr/local/x-ui/x-ui" ]; then
            xui_version=$(/usr/local/x-ui/x-ui -v 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1)
            [ -n "$xui_version" ] && xui_version="v${xui_version}"
        fi
        
        # Способ 2: Из .env файла
        if [ -z "$xui_version" ] && [ -f ".env" ]; then
            xui_version=$(grep "^XUI_VERSION=" .env 2>/dev/null | cut -d'=' -f2)
            # Добавляем v если его нет
            [[ -n "$xui_version" && ! "$xui_version" =~ ^v ]] && xui_version="v${xui_version}"
        fi
        
        # Способ 3: Из бинарного файла в bin/
        if [ -z "$xui_version" ] && [ -f "/usr/local/x-ui/bin/x-ui" ]; then
            xui_version=$(/usr/local/x-ui/bin/x-ui -v 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1)
            [ -n "$xui_version" ] && xui_version="v${xui_version}"
        fi
        
        # Если ничего не нашли
        [ -z "$xui_version" ] && xui_version="Unknown"
        
        # Получаем данные из .env
        if [ -f ".env" ]; then
            local xui_url=$(grep "^XUI_URL=" .env 2>/dev/null | cut -d'=' -f2)
            local xui_user=$(grep "^XUI_USERNAME=" .env 2>/dev/null | cut -d'=' -f2)
            local xui_pass=$(grep "^XUI_PASSWORD=" .env 2>/dev/null | cut -d'=' -f2)
            local transport=$(grep "^TRANSPORT=" .env 2>/dev/null | cut -d'=' -f2)
            local security=$(grep "^SECURITY=" .env 2>/dev/null | cut -d'=' -f2)
            local inbound_id=$(grep "^INBOUND_ID=" .env 2>/dev/null | cut -d'=' -f2)
            local xui_db_path=$(grep "^XUI_DB_PATH=" .env 2>/dev/null | cut -d'=' -f2)
            
            # Значения по умолчанию
            [ -z "$transport" ] && transport="tcp"
            [ -z "$security" ] && security="tls"
            [ -z "$inbound_id" ] && inbound_id="1"
            [ -z "$xui_db_path" ] && xui_db_path="/etc/x-ui/x-ui.db"
            
            # Получаем количество ключей из базы данных
            local total_keys=0
            if [ -f "$xui_db_path" ]; then
                local settings=$(sqlite3 "$xui_db_path" "SELECT settings FROM inbounds WHERE id=${inbound_id};" 2>/dev/null)
                if [ -n "$settings" ]; then
                    # Подсчитываем количество клиентов в JSON
                    total_keys=$(echo "$settings" | grep -o '"id"' | wc -l)
                fi
            fi
            
            echo -e "  ${GREEN}✅ Установлена${NC}"
            echo -e "  Версия: ${xui_version}"
            echo -e "  URL: ${xui_url}"
            echo -e "  Логин: ${xui_user}"
            echo -e "  Пароль: ${xui_pass}"
            echo -e "  Состояние: ${GREEN}Запущена${NC}"
            echo -e "  Подключение №: ${inbound_id}"
            echo -e "         Транспорт: ${transport}"
            echo -e "         Безопасность: ${security}"
            echo -e "         Всего ключей: ${total_keys}"
        else
            echo -e "  ${GREEN}✅ Установлена${NC}"
            echo -e "  Версия: ${xui_version}"
            echo -e "  Состояние: ${GREEN}Запущена${NC}"
        fi
    else
        echo -e "  ${RED}❌ Не установлена${NC}"
    fi
    
    # ============================================
    # AWG SERVERS
    # ============================================
    echo -e "\n${YELLOW}${BOLD}AWG SERVERS:${NC}"
    
    # AWG v1
    if docker ps -a --filter name=^amnezia-awg$ --format "{{.Names}}" | grep -q "amnezia-awg"; then
        # Контейнер существует, проверяем запущен ли он
        if docker ps --filter name=^amnezia-awg$ --format "{{.Names}}" | grep -q "amnezia-awg"; then
            local awg1_port=$(docker port amnezia-awg 2>/dev/null | grep -oP '\d+$' | head -1)
            [ -z "$awg1_port" ] && awg1_port="Unknown"
            local awg1_clients=$(docker exec amnezia-awg grep -c "\[Peer\]" /opt/amnezia/*/awg0.conf /opt/amnezia/*/wg0.conf 2>/dev/null | head -1 | cut -d: -f2 2>/dev/null || echo "0")
            awg1_clients=$(echo "$awg1_clients" | tr -d '[:space:]')
            echo -e "  AWG v1: ${GREEN}✅ Запущен${NC}"
            echo -e "    📦 Контейнер: amnezia-awg"
            echo -e "    🔌 Порт: ${awg1_port}"
            if [ -n "$awg1_clients" ] && [ "$awg1_clients" != "0" ]; then
                echo -e "    👥 Клиентов: ${awg1_clients}"
            fi
        else
            echo -e "  AWG v1: ${YELLOW}⚠️  Остановлен${NC} (Контейнер: amnezia-awg)"
        fi
    else
        echo -e "  AWG v1: ${RED}❌ Не установлен${NC}"
    fi
    
    # AWG v2
    if docker ps -a --filter name=^amnezia-awg2$ --format "{{.Names}}" | grep -q "amnezia-awg2"; then
        # Контейнер существует, проверяем запущен ли он
        if docker ps --filter name=^amnezia-awg2$ --format "{{.Names}}" | grep -q "amnezia-awg2"; then
            local awg2_port=$(docker port amnezia-awg2 2>/dev/null | grep -oP '\d+$' | head -1)
            [ -z "$awg2_port" ] && awg2_port="Unknown"
            local awg2_clients=$(docker exec amnezia-awg2 grep -c "\[Peer\]" /opt/amnezia/*/awg0.conf /opt/amnezia/*/wg0.conf 2>/dev/null | head -1 | cut -d: -f2 2>/dev/null || echo "0")
            awg2_clients=$(echo "$awg2_clients" | tr -d '[:space:]')
            echo -e "  AWG v2: ${GREEN}✅ Запущен${NC}"
            echo -e "    📦 Контейнер: amnezia-awg2"
            echo -e "    🔌 Порт: ${awg2_port}"
            if [ -n "$awg2_clients" ] && [ "$awg2_clients" != "0" ]; then
                echo -e "    👥 Клиентов: ${awg2_clients}"
            fi
        else
            echo -e "  AWG v2: ${YELLOW}⚠️  Остановлен${NC} (Контейнер: amnezia-awg2)"
        fi
    else
        echo -e "  AWG v2: ${RED}❌ Не установлен${NC}"
    fi
    
    # ============================================
    # XUIBOT
    # ============================================
    echo -e "\n${YELLOW}${BOLD}XUIBOT:${NC}"
    
    if docker ps --filter name=xuibot --format "{{.Names}}" | grep -q xuibot; then
        local xui_token=$(grep "^XUI_BOT_TOKEN=" .env 2>/dev/null | cut -d'=' -f2)
        local xui_bot_username=$(get_bot_username "$xui_token" "xuibot")
        local db_path=$(grep "^DB_PATH=" .env 2>/dev/null | cut -d'=' -f2)
        
        # Значение по умолчанию для DB_PATH
        [ -z "$db_path" ] && db_path="/app/data/bot_users.db"
        
        # Получаем количество пользователей из базы данных
        local user_count=0
        local admin_ids=$(grep "^ADMIN_IDS=" .env 2>/dev/null | cut -d'=' -f2)
        local main_admin=$(echo "$admin_ids" | cut -d',' -f1)
        
        # Проверяем базу данных внутри контейнера
        if [ -n "$main_admin" ]; then
            user_count=$(docker exec xuibot sqlite3 "$db_path" "SELECT COUNT(*) FROM allowed_users WHERE user_id != ${main_admin};" 2>/dev/null || echo "0")
        fi
        
        if [ "$xui_bot_username" != "Unknown" ]; then
            echo -e "  Ссылка: https://t.me/${xui_bot_username}"
        fi
        echo -e "  XUI Bot: ${GREEN}✅ Запущен${NC}"
        echo -e "  Пользователей: ${user_count}"
    else
        echo -e "  XUI Bot: ${RED}❌ Не установлен${NC}"
    fi
    
    # ============================================
    # AWGBOT
    # ============================================
    echo -e "\n${YELLOW}${BOLD}AWGBOT:${NC}"
    
    if docker ps --filter name=awgbot --format "{{.Names}}" | grep -q awgbot; then
        local awg_token=$(grep "^AWG_BOT_TOKEN=" .env 2>/dev/null | cut -d'=' -f2)
        local awg_bot_username=$(get_bot_username "$awg_token" "awgbot")
        
        if [ "$awg_bot_username" != "Unknown" ]; then
            echo -e "  Ссылка: https://t.me/${awg_bot_username}"
        fi
        echo -e "  AWG Bot: ${GREEN}✅ Запущен${NC}"
    else
        echo -e "  AWG Bot: ${RED}❌ Не установлен${NC}"
    fi
    
    echo -e "\n${BLUE}========================================${NC}"
}


# Функция удаления всего
remove_all() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Удалить ВСЁ${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    echo -e "${RED}⚠️  ВНИМАНИЕ! Это удалит:${NC}"
    echo -e "  - AWG Бот"
    echo -e "  - XUI Бот"
    echo -e "  - 3x-ui панель"
    echo -e "  - AWG v1 сервер"
    echo -e "  - AWG v2 сервер"
    echo -e "  - Все конфигурации и данные"
    echo -e ""
    read -p "Вы уверены? (нажмите Enter для подтверждения или 0 для отмены): " confirm
    
    if [[ "$confirm" == "0" ]]; then
        echo -e "${YELLOW}Отменено${NC}"
        return
    fi
    
    echo -e "${YELLOW}🗑️  Удаление всех компонентов...${NC}"
    
    # Остановка всех контейнеров
    echo -e "${YELLOW}🛑 Остановка контейнеров...${NC}"
    docker stop awgbot xuibot amnezia-awg amnezia-awg2 2>/dev/null || true
    docker rm awgbot xuibot amnezia-awg amnezia-awg2 2>/dev/null || true
    
    # Удаление образов
    echo -e "${YELLOW}🗑️  Удаление образов...${NC}"
    docker rmi awgxuibot-awgbot awgxuibot-xuibot 2>/dev/null || true
    
    # Удаление конфигураций AWG и каталога amnezia
    echo -e "${YELLOW}🗑️  Удаление конфигураций AWG...${NC}"
    rm -rf /opt/amnezia/amnezia-awg 2>/dev/null || true
    rm -rf /opt/amnezia/amnezia-awg2 2>/dev/null || true
    
    # Удаление всего каталога /opt/amnezia если он пустой или содержит только AWG данные
    if [ -d "/opt/amnezia" ]; then
        echo -e "${YELLOW}🗑️  Удаление каталога /opt/amnezia...${NC}"
        rm -rf /opt/amnezia 2>/dev/null || true
        echo -e "${GREEN}✅ Каталог /opt/amnezia удален${NC}"
    fi
    
    # Удаление 3x-ui панели
    echo -e "${YELLOW}🗑️  Удаление 3x-ui панели...${NC}"
    systemctl stop x-ui 2>/dev/null || true
    systemctl disable x-ui 2>/dev/null || true
    rm -rf /usr/local/x-ui 2>/dev/null || true
    rm -rf /etc/x-ui 2>/dev/null || true
    rm -f /etc/systemd/system/x-ui.service 2>/dev/null || true
    systemctl daemon-reload
    
    # Удаление каталога проекта
    if [ -d "$WORK_DIR" ]; then
        echo -e "${YELLOW}🗑️  Удаление каталога проекта...${NC}"
        cd /root
        rm -rf "$WORK_DIR"
        echo -e "${GREEN}✅ Каталог ${WORK_DIR} удален${NC}"
    fi
    
    echo -e "${GREEN}✅ Все компоненты удалены!${NC}"
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}Для повторной установки выполните:${NC}"
    echo -e "${YELLOW}git clone https://github.com/4539617/awgxuibot.git ${WORK_DIR}${NC}"
    echo -e "${YELLOW}cd ${WORK_DIR}${NC}"
    echo -e "${YELLOW}bash install.sh${NC}"
    echo -e "${BLUE}========================================${NC}"
}

# Функция генерации случайного пароля
generate_random_string() {
    local length=$1
    local chars='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
    local result=''
    for i in $(seq 1 $length); do
        result="${result}${chars:RANDOM%${#chars}:1}"
    done
    echo "$result"
}

# Функция генерации случайного пароля без спецсимволов
generate_random_password() {
    local length=$1
    local chars='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
    local result=''
    for i in $(seq 1 $length); do
        result="${result}${chars:RANDOM%${#chars}:1}"
    done
    echo "$result"
}

# Функция определения версии установленной 3x-ui панели
detect_xui_version() {
    if ! systemctl is-active --quiet x-ui; then
        echo ""
        return
    fi
    
    # Пробуем получить версию через команду x-ui
    local version=$(x-ui version 2>/dev/null | grep -oP 'v\K[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    
    if [ -z "$version" ]; then
        # Альтернативный метод - проверяем структуру API
        local xui_url=$(get_env_value "XUI_URL" 2>/dev/null)
        if [ -n "$xui_url" ]; then
            # Проверяем наличие /panel/ в URL (характерно для v3.x)
            if echo "$xui_url" | grep -q "/panel"; then
                version="3.x"
            else
                version="2.9.4"
            fi
        fi
    fi
    
    echo "$version"
}

# Функция выбора версии для установки
select_xui_version() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Выбор версии 3x-ui Panel${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    echo -e "${GREEN}1)${NC} Стабильная версия v2.9.4 (для работы через БД)"
    echo -e "${YELLOW}2)${NC} Последняя версия v2.x (Latest v2.x)"
    echo -e "${GREEN}3)${NC} Последняя версия v3.x (Latest v3.x) ${YELLOW}[НОВОЕ - с API токеном]${NC}"
    echo -e "${GREEN}0)${NC} Вернуться в главное меню"
    echo -e "\n${BLUE}Рекомендации:${NC}"
    echo -e "  ${YELLOW}v2.9.4${NC} - работает через прямой доступ к БД"
    echo -e "  ${YELLOW}v3.x${NC}   - работает через API (требуется API токен)"
    echo -e "\n${YELLOW}Выберите версию для установки [1]:${NC} "
    read -p "" version_choice
    version_choice=${version_choice:-1}
    
    case $version_choice in
        1)
            install_3xui_v294
            ;;
        2)
            echo -e "\n${RED}⚠️  ВНИМАНИЕ!${NC}"
            echo -e "${YELLOW}Последняя версия v2.x может быть нестабильной!${NC}"
            echo -e "${YELLOW}Рекомендуется использовать v2.9.4 или v3.x${NC}"
            read -p "Вы уверены что хотите продолжить? (нажмите Enter для подтверждения или 0 для отмены): " confirm_latest
            if [[ "$confirm_latest" != "0" ]]; then
                install_3xui_latest
            else
                echo -e "${GREEN}Отменено. Устанавливаем v2.9.4...${NC}"
                install_3xui_v294
            fi
            ;;
        3)
            echo -e "\n${GREEN}✓ Установка 3x-ui v3.x с поддержкой API${NC}"
            echo -e "${YELLOW}Эта версия полностью поддерживается ботом через API${NC}"
            echo -e "${YELLOW}API токен будет автоматически извлечен и сохранен${NC}\n"
            install_3xui_v3
            ;;
        0)
            echo -e "${YELLOW}Отменено${NC}"
            return
            ;;
        *)
            echo -e "${YELLOW}Неверный выбор. Устанавливаем v2.9.4 по умолчанию...${NC}"
            sleep 2
            install_3xui_v294
            ;;
    esac
}

# Функция установки последней версии 3x-ui панели
install_3xui_latest() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Установка 3x-ui Panel (Latest)${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    # Проверка установлена ли уже панель
    if systemctl is-active --quiet x-ui; then
        echo -e "${YELLOW}⚠ 3x-ui панель уже установлена${NC}"
        read -p "Переустановить? (нажмите Enter для подтверждения или 0 для отмены): " reinstall
        if [[ "$reinstall" == "0" ]]; then
            echo -e "${YELLOW}Отменено${NC}"
            return
        fi
    fi
    
    SERVER_IP=$(curl -s ifconfig.me)
    
    # Генерируем случайный пароль для панели
    GENERATED_PASSWORD=$(openssl rand -base64 16 | tr -d "=+/" | cut -c1-16)
    
    echo -e "${YELLOW}📦 Загрузка и установка 3x-ui (последняя версия)...${NC}"
    echo -e "${BLUE}Будет сгенерирован случайный пароль для панели${NC}\n"
    
    # Установка с автоматической генерацией параметров (новая версия установщика)
    # Передаем ответы на все промпты:
    # y - подтверждение установки
    # 1 - SQLite база данных
    # 2 - Let's Encrypt для IP
    # (пусто) - IPv6 address (skip)
    # (пусто) - Port для ACME (default 80)
    
    # Захватываем вывод установщика
    INSTALL_OUTPUT=$(bash <(curl -Ls https://raw.githubusercontent.com/MHSanaei/3x-ui/master/install.sh) 2>&1 << EOF
y
1
2


EOF
)
    
    # Извлекаем версию из вывода установщика
    XUI_VERSION=$(echo "$INSTALL_OUTPUT" | grep -oP 'x-ui v\K[0-9.]+' | head -1)
    
    # Выводим результат установки (скрываем блок с учетными данными установщика)
    echo "$INSTALL_OUTPUT" | grep -v "═══" | grep -v "Panel Installation Complete" | grep -v "Username:" | grep -v "Password:" | grep -v "Port:" | grep -v "WebBasePath:" | grep -v "Access URL:" | grep -v "API Token:" | grep -v "Database:" | grep -v "IMPORTANT: Save these credentials"
    
    # Проверяем успешность установки
    if echo "$INSTALL_OUTPUT" | grep -q "installation finished"; then
        echo -e "\n${GREEN}✅ 3x-ui установлен успешно${NC}"
        
        # Установщик 3x-ui НЕ выводит plaintext пароль в консоль
        # Он только сохраняет bcrypt хеш в базу данных
        # Поэтому мы должны установить свой пароль после установки
        
        XUI_USERNAME=""
        XUI_PASSWORD=""
        XUI_PORT=""
        XUI_PATH=""
        # Исправление проблемы с базой данных x-ui.db
        echo -e "${YELLOW}🔧 Проверка базы данных...${NC}"
        if [ -d "/etc/x-ui/x-ui.db" ]; then
            echo -e "${YELLOW}⚠ Обнаружена проблема: x-ui.db создана как директория${NC}"
            echo -e "${YELLOW}🔧 Исправление...${NC}"
            systemctl stop x-ui
            rm -rf /etc/x-ui/x-ui.db
            touch /etc/x-ui/x-ui.db
            chmod 644 /etc/x-ui/x-ui.db
            systemctl start x-ui
            sleep 2
            echo -e "${GREEN}✅ База данных исправлена${NC}"
        fi
        
        # Если учетные данные не были извлечены из вывода установщика
        if [ -z "$XUI_USERNAME" ] || [ -z "$XUI_PASSWORD" ] || [ -z "$XUI_PORT" ] || [ -z "$XUI_PATH" ]; then
            echo -e "${YELLOW}🔍 Получение данных из системы...${NC}"
            
            # Устанавливаем sqlite3 если не установлен
            if ! command -v sqlite3 &> /dev/null; then
                echo -e "${YELLOW}📦 Установка sqlite3...${NC}"
                apt-get update -qq && apt-get install -y sqlite3 -qq > /dev/null 2>&1
            fi
            
            # Получаем настройки из x-ui settings
            sleep 2
            XUI_SETTINGS=$(echo "n" | timeout 5 x-ui settings 2>/dev/null || echo "")
            
            if [ -n "$XUI_SETTINGS" ]; then
                if [ -z "$XUI_PORT" ]; then
                    XUI_PORT=$(echo "$XUI_SETTINGS" | grep "port:" | awk '{print $2}')
                fi
                if [ -z "$XUI_PATH" ]; then
                    XUI_PATH=$(echo "$XUI_SETTINGS" | grep "webBasePath:" | awk '{print $2}' | sed 's/\/$//')
                    # Добавляем leading slash если нужно
                    if [ -n "$XUI_PATH" ] && [[ "$XUI_PATH" != /* ]] && [ "$XUI_PATH" != "/" ]; then
                        XUI_PATH="/${XUI_PATH}"
                    fi
                fi
            fi
            
            # Получаем username из базы данных
            if [ -f "/etc/x-ui/x-ui.db" ]; then
                echo -e "${YELLOW}🔐 Получение username из базы данных...${NC}"
                XUI_USERNAME=$(sqlite3 /etc/x-ui/x-ui.db "SELECT username FROM users LIMIT 1;" 2>/dev/null || echo "")
                
                if [ -n "$XUI_USERNAME" ]; then
                    echo -e "${GREEN}✅ Username: ${YELLOW}${XUI_USERNAME}${NC}"
                fi
            fi
            
            # Устанавливаем сгенерированный пароль напрямую в базу данных
            if [ -n "$XUI_USERNAME" ] && [ -n "$GENERATED_PASSWORD" ]; then
                echo -e "${YELLOW}🔐 Установка нового пароля для панели...${NC}"
                
                # Устанавливаем bcrypt для генерации хеша
                if ! command -v htpasswd &> /dev/null; then
                    echo -e "${YELLOW}📦 Установка apache2-utils для bcrypt...${NC}"
                    apt-get update -qq && apt-get install -y apache2-utils -qq > /dev/null 2>&1
                fi
                
                # Генерируем bcrypt хеш пароля (cost 10, как в 3x-ui)
                PASSWORD_HASH=$(htpasswd -nbBC 10 "" "$GENERATED_PASSWORD" | cut -d: -f2)
                
                # Обновляем пароль в базе данных
                sqlite3 /etc/x-ui/x-ui.db "UPDATE users SET password='${PASSWORD_HASH}' WHERE username='${XUI_USERNAME}';" 2>/dev/null
                
                if [ $? -eq 0 ]; then
                    XUI_PASSWORD="$GENERATED_PASSWORD"
                    echo -e "${GREEN}✅ Пароль успешно установлен в базу данных${NC}"
                    
                    # Перезапускаем панель для применения изменений
                    systemctl restart x-ui
                    sleep 2
                else
                    echo -e "${YELLOW}⚠ Не удалось обновить пароль в базе данных${NC}"
                    XUI_PASSWORD="$GENERATED_PASSWORD"
                fi
            else
                # Fallback
                XUI_USERNAME="${XUI_USERNAME:-admin}"
                XUI_PASSWORD="$GENERATED_PASSWORD"
            fi
            if [ -z "$XUI_PORT" ]; then
                XUI_PORT="2053"
            fi
            if [ -z "$XUI_PATH" ]; then
                XUI_PATH="/"
            fi
        fi
        
        # Формируем URL для v2.9.4 (всегда HTTP для старой установки)
        # Используем корневой путь для упрощения
        XUI_PATH="/"
        XUI_URL="http://${SERVER_IP}:${XUI_PORT}"
        
        echo -e "\n${GREEN}═══════════════════════════════════════════${NC}"
        echo -e "${GREEN}     Панель 3x-ui успешно установлена!${NC}"
        echo -e "${GREEN}═══════════════════════════════════════════${NC}"
        echo -e "${BLUE}📍 URL панели: ${YELLOW}${XUI_URL}${NC}"
        echo -e "${BLUE}👤 Логин:      ${YELLOW}${XUI_USERNAME}${NC}"
        echo -e "${BLUE}🔑 Пароль:     ${YELLOW}${XUI_PASSWORD}${NC}"
        echo -e "${BLUE}🔌 Порт:       ${YELLOW}${XUI_PORT}${NC}"
        echo -e "${GREEN}═══════════════════════════════════════════${NC}\n"
        
        # Генерация Reality ключей
        echo -e "${YELLOW}🔑 Генерация Reality ключей...${NC}"
        
        # Установка xray если не установлен
        if ! command -v xray &> /dev/null; then
            echo -e "${YELLOW}📦 Установка xray...${NC}"
            bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
        fi
        
        # Генерация ключей Reality
        REALITY_KEYS=$(xray x25519)
        # Поддержка обоих форматов вывода xray (старый и новый)
        REALITY_PRIVATE_KEY=$(echo "$REALITY_KEYS" | grep -E "(Private key:|PrivateKey:)" | awk '{print $NF}')
        REALITY_PUBLIC_KEY=$(echo "$REALITY_KEYS" | grep -E "(Public key:|Password \(PublicKey\):)" | awk '{print $NF}')
        
        # Генерация Short IDs
        REALITY_SHORT_ID=$(openssl rand -hex 8)
        
        # Создание .env файла с учетными данными 3x-ui
        create_env_if_not_exists
        
        echo -e "${YELLOW}💾 Сохранение учетных данных в .env...${NC}"
        update_env_value "XUI_URL" "${XUI_URL}"
        update_env_value "XUI_USERNAME" "${XUI_USERNAME}"
        update_env_value "XUI_PASSWORD" "${XUI_PASSWORD}"
        update_env_value "REALITY_PUBLIC_KEY" "${REALITY_PUBLIC_KEY}"
        update_env_value "REALITY_PRIVATE_KEY" "${REALITY_PRIVATE_KEY}"
        update_env_value "REALITY_SHORT_ID" "${REALITY_SHORT_ID}"
        update_env_value "SERVER_ADDRESS" "${SERVER_IP}"
        update_env_value "SERVER_IP" "${SERVER_IP}"
        update_env_value "SERVER_PORT" "443"
        
        # Сохраняем версию панели
        if [ -n "$XUI_VERSION" ]; then
            update_env_value "XUI_VERSION" "${XUI_VERSION}"
        else
            update_env_value "XUI_VERSION" "latest"
        fi
        
        # Автоматическое создание inbound
        echo -e "\n${YELLOW}🔧 Создание VLESS Reality inbound...${NC}"
        
        # Извлекаем API токен из вывода установщика (если есть)
        XUI_API_TOKEN=$(echo "$INSTALL_OUTPUT" | grep -oP '(?<=API Token:\s{3})\S+' | head -1)
        
        if [ -n "$XUI_API_TOKEN" ]; then
            echo -e "${GREEN}✅ API Token извлечен: ${XUI_API_TOKEN:0:20}...${NC}"
            update_env_value "XUI_API_TOKEN" "${XUI_API_TOKEN}"
        fi
        
        # Даем панели время на запуск
        echo -e "${YELLOW}⏳ Ожидание запуска панели (15 секунд)...${NC}"
        sleep 15
        
        # Получаем cookie для авторизации
        echo -e "${YELLOW}🔐 Авторизация в панели...${NC}"
        
        COOKIE_FILE=$(mktemp)
        
        # Пробуем авторизоваться (3x-ui использует form-urlencoded)
        # URL уже содержит путь, добавляем /login к нему
        LOGIN_URL="${XUI_URL%/}/login"
        
        echo -e "${YELLOW}Попытка авторизации: ${LOGIN_URL}${NC}"
        echo -e "${YELLOW}Username: ${XUI_USERNAME}${NC}"
        echo -e "${YELLOW}Password length: ${#XUI_PASSWORD}${NC}"
        
        # Пробуем несколько раз с задержкой
        for attempt in 1 2 3; do
            echo -e "${YELLOW}Попытка ${attempt}/3...${NC}"
            
            LOGIN_RESPONSE=$(curl -s -w "\n%{http_code}" -c "$COOKIE_FILE" -L -X POST "${LOGIN_URL}" \
                -H "Content-Type: application/x-www-form-urlencoded" \
                -H "Accept: application/json, text/plain, */*" \
                -H "User-Agent: Mozilla/5.0" \
                -H "Origin: ${XUI_URL%/}" \
                -H "Referer: ${XUI_URL%/}/" \
                -d "username=${XUI_USERNAME}&password=${XUI_PASSWORD}" 2>&1)
            
            HTTP_CODE=$(echo "$LOGIN_RESPONSE" | tail -n1)
            RESPONSE_BODY=$(echo "$LOGIN_RESPONSE" | head -n-1)
            
            # Извлекаем session cookie из файла
            COOKIE=$(grep -oP '(?<=session\s)[^\s]+' "$COOKIE_FILE" 2>/dev/null || echo "")
            
            if [ -n "$COOKIE" ] || [ "$HTTP_CODE" = "200" ]; then
                break
            fi
            
            if [ $attempt -lt 3 ]; then
                echo -e "${YELLOW}Ожидание 5 секунд перед следующей попыткой...${NC}"
                sleep 5
            fi
        done
        
        # Отладочная информация
        if [ -n "$COOKIE" ]; then
            echo -e "${GREEN}✅ Cookie получен: ${COOKIE:0:20}...${NC}"
            echo -e "${GREEN}✅ HTTP код: ${HTTP_CODE}${NC}"
            USE_API_TOKEN=false
        else
            echo -e "${YELLOW}🔧 Попытка создания inbound через SQL...${NC}"
            
            # Пробуем альтернативный метод - через API токен если есть
            if [ -n "$XUI_API_TOKEN" ]; then
                USE_API_TOKEN=true
            else
                USE_API_TOKEN=false
            fi
        fi
        
        rm -f "$COOKIE_FILE"
        
        # Создаем JSON конфигурацию для inbound (на основе рабочего примера)
        INBOUND_JSON=$(cat <<'INBOUND_EOF'
{
  "enable": true,
  "port": 443,
  "protocol": "vless",
  "settings": "{\n  \"clients\": [],\n  \"decryption\": \"none\",\n  \"encryption\": \"none\"\n}",
  "streamSettings": "{\n  \"network\": \"xhttp\",\n  \"security\": \"reality\",\n  \"externalProxy\": [],\n  \"realitySettings\": {\n    \"show\": false,\n    \"xver\": 0,\n    \"target\": \"www.nvidia.com:443\",\n    \"serverNames\": [\n      \"www.nvidia.com\"\n    ],\n    \"privateKey\": \"REALITY_PRIVATE_KEY_PLACEHOLDER\",\n    \"minClientVer\": \"\",\n    \"maxClientVer\": \"\",\n    \"maxTimediff\": 0,\n    \"shortIds\": [\n      \"REALITY_SHORT_ID_PLACEHOLDER\"\n    ],\n    \"settings\": {\n      \"publicKey\": \"REALITY_PUBLIC_KEY_PLACEHOLDER\",\n      \"fingerprint\": \"edge\",\n      \"serverName\": \"\",\n      \"spiderX\": \"/\"\n    }\n  },\n  \"xhttpSettings\": {\n    \"path\": \"/\",\n    \"host\": \"\",\n    \"headers\": {},\n    \"scMaxBufferedPosts\": 30,\n    \"scMaxEachPostBytes\": \"1000000\",\n    \"scStreamUpServerSecs\": \"20-80\",\n    \"noSSEHeader\": false,\n    \"xPaddingBytes\": \"100-1000\",\n    \"mode\": \"auto\",\n    \"xPaddingObfsMode\": false,\n    \"scMinPostsIntervalMs\": \"30\"\n  }\n}",
  "tag": "inbound-443",
  "sniffing": "{\n  \"enabled\": false,\n  \"destOverride\": [\n    \"http\",\n    \"tls\",\n    \"quic\",\n    \"fakedns\"\n  ],\n  \"metadataOnly\": false,\n  \"routeOnly\": false\n}",
  "remark": "VLESS-Reality-xHTTP"
}
INBOUND_EOF
)
        
        # Заменяем плейсхолдеры на реальные значения
        INBOUND_JSON=$(echo "$INBOUND_JSON" | sed "s/REALITY_PRIVATE_KEY_PLACEHOLDER/${REALITY_PRIVATE_KEY}/g")
        INBOUND_JSON=$(echo "$INBOUND_JSON" | sed "s/REALITY_PUBLIC_KEY_PLACEHOLDER/${REALITY_PUBLIC_KEY}/g")
        INBOUND_JSON=$(echo "$INBOUND_JSON" | sed "s/REALITY_SHORT_ID_PLACEHOLDER/${REALITY_SHORT_ID}/g")
        
        # Альтернативный метод: создание inbound напрямую через SQL
        # Проверяем структуру таблицы inbounds
        INBOUND_TABLE_EXISTS=$(sqlite3 /etc/x-ui/x-ui.db "SELECT name FROM sqlite_master WHERE type='table' AND name='inbounds';" 2>/dev/null)
        
        if [ -n "$INBOUND_TABLE_EXISTS" ]; then
            echo -e "${GREEN}✅ Таблица inbounds найдена${NC}"
            
            # Проверяем структуру таблицы (скрыто от пользователя)
            # sqlite3 /etc/x-ui/x-ui.db "PRAGMA table_info(inbounds);" 2>/dev/null > /dev/null
            
            # Создаем JSON конфигурации для settings и streamSettings
            SETTINGS_JSON='{"clients":[],"decryption":"none","fallbacks":[]}'
            
            STREAM_SETTINGS_JSON=$(cat <<STREAMEOF
{
  "network": "xhttp",
  "security": "reality",
  "externalProxy": [],
  "realitySettings": {
    "show": false,
    "xver": 0,
    "target": "www.nvidia.com:443",
    "serverNames": ["www.nvidia.com"],
    "privateKey": "${REALITY_PRIVATE_KEY}",
    "minClientVer": "",
    "maxClientVer": "",
    "maxTimediff": 0,
    "shortIds": ["${REALITY_SHORT_ID}"],
    "settings": {
      "publicKey": "${REALITY_PUBLIC_KEY}",
      "fingerprint": "edge",
      "serverName": "",
      "spiderX": "/"
    }
  },
  "xhttpSettings": {
    "path": "/",
    "host": "",
    "headers": {},
    "scMaxBufferedPosts": 30,
    "scMaxEachPostBytes": "1000000",
    "scStreamUpServerSecs": "20-80",
    "noSSEHeader": false,
    "xPaddingBytes": "100-1000",
    "mode": "auto",
    "xPaddingObfsMode": false,
    "scMinPostsIntervalMs": "30"
  }
}
STREAMEOF
)
            
            SNIFFING_JSON='{"enabled":true,"destOverride":["http","tls","quic","fakedns"],"metadataOnly":false,"routeOnly":false}'
            
            # Экранируем JSON для SQL
            SETTINGS_JSON_ESCAPED=$(echo "$SETTINGS_JSON" | sed "s/'/''/g")
            STREAM_SETTINGS_JSON_ESCAPED=$(echo "$STREAM_SETTINGS_JSON" | sed "s/'/''/g")
            SNIFFING_JSON_ESCAPED=$(echo "$SNIFFING_JSON" | sed "s/'/''/g")
            
            # Проверяем и удаляем существующий inbound с таким же тегом
            echo -e "${YELLOW}🔍 Проверка существующих inbounds...${NC}"
            EXISTING_INBOUND=$(sqlite3 /etc/x-ui/x-ui.db "SELECT id FROM inbounds WHERE tag='inbound-443' OR remark='VLESS-Reality-xHTTP';" 2>/dev/null)
            
            if [ -n "$EXISTING_INBOUND" ]; then
                echo -e "${YELLOW}⚠ Найден существующий inbound (ID: ${EXISTING_INBOUND}), удаляем...${NC}"
                sqlite3 /etc/x-ui/x-ui.db "DELETE FROM inbounds WHERE tag='inbound-443' OR remark='VLESS-Reality-xHTTP';" 2>/dev/null
                echo -e "${GREEN}✅ Старый inbound удален${NC}"
            fi
            
            # Вставляем inbound в базу данных
            SQL_INSERT="INSERT INTO inbounds (user_id, up, down, total, remark, enable, expiry_time, listen, port, protocol, settings, stream_settings, tag, sniffing) VALUES (1, 0, 0, 0, 'VLESS-Reality-xHTTP', 1, 0, '', 443, 'vless', '${SETTINGS_JSON_ESCAPED}', '${STREAM_SETTINGS_JSON_ESCAPED}', 'inbound-443', '${SNIFFING_JSON_ESCAPED}');"
            
            echo -e "${YELLOW}📝 Создание нового inbound...${NC}"
            set +e  # Временно отключаем exit on error
            SQL_RESULT=$(sqlite3 /etc/x-ui/x-ui.db "${SQL_INSERT}" 2>&1)
            SQL_EXIT_CODE=$?
            set -e  # Включаем обратно
            
            if [ $SQL_EXIT_CODE -eq 0 ]; then
                echo -e "${GREEN}✅ SQL запрос выполнен успешно${NC}"
                # Получаем ID созданного inbound
                INBOUND_ID=$(sqlite3 /etc/x-ui/x-ui.db "SELECT id FROM inbounds WHERE remark='VLESS-Reality-xHTTP' ORDER BY id DESC LIMIT 1;" 2>/dev/null)
                
                if [ -n "$INBOUND_ID" ]; then
                    echo -e "${GREEN}✅ Inbound создан через SQL!${NC}"
                    echo -e "${GREEN}   ID: ${INBOUND_ID}${NC}"
                    echo -e "${GREEN}   Порт: 443${NC}"
                    echo -e "${GREEN}   Protocol: VLESS${NC}"
                    echo -e "${GREEN}   Network: xhttp${NC}"
                    echo -e "${GREEN}   Security: reality${NC}"
                    
                    # Сохраняем ID в .env
                    update_env_value "INBOUND_ID" "${INBOUND_ID}"
                    
                    # Извлекаем реальные Reality ключи из созданного inbound
                    echo -e "${YELLOW}🔑 Извлечение Reality ключей из inbound...${NC}"
                    ACTUAL_PUBLIC_KEY=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.realitySettings.settings.publicKey') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
                    ACTUAL_SHORT_ID=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.realitySettings.shortIds[0]') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
                    ACTUAL_FINGERPRINT=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.realitySettings.settings.fingerprint') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
                    ACTUAL_SNI=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.realitySettings.serverNames[0]') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
                    
                    if [ -n "$ACTUAL_PUBLIC_KEY" ] && [ -n "$ACTUAL_SHORT_ID" ]; then
                        echo -e "${GREEN}✅ Reality ключи извлечены из inbound${NC}"
                        echo -e "${GREEN}   Public Key: ${ACTUAL_PUBLIC_KEY:0:30}...${NC}"
                        echo -e "${GREEN}   Short ID: ${ACTUAL_SHORT_ID}${NC}"
                        echo -e "${GREEN}   Fingerprint: ${ACTUAL_FINGERPRINT}${NC}"
                        echo -e "${GREEN}   SNI: ${ACTUAL_SNI}${NC}"
                        
                        # Обновляем .env с реальными ключами из inbound
                        update_env_value "REALITY_PUBLIC_KEY" "${ACTUAL_PUBLIC_KEY}"
                        update_env_value "REALITY_SHORT_ID" "${ACTUAL_SHORT_ID}"
                        update_env_value "REALITY_FINGERPRINT" "${ACTUAL_FINGERPRINT}"
                        update_env_value "REALITY_SNI" "${ACTUAL_SNI}"
                        
                        echo -e "${GREEN}✅ Ключи сохранены в .env${NC}"
                    else
                        echo -e "${YELLOW}⚠ Не удалось извлечь ключи из inbound, используем сгенерированные${NC}"
                    fi
                    
                    # Отключаем WAL режим для совместимости с Docker
                    echo -e "${YELLOW}🔧 Оптимизация базы данных для Docker...${NC}"
                    systemctl stop x-ui
                    sleep 2
                    
                    # Выполняем checkpoint и отключаем WAL
                    sqlite3 /etc/x-ui/x-ui.db "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true
                    sqlite3 /etc/x-ui/x-ui.db "PRAGMA journal_mode=DELETE;" 2>/dev/null || true
                    
                    echo -e "${GREEN}✅ База данных оптимизирована${NC}"
                    
                    # Перезапускаем панель для применения изменений
                    echo -e "${YELLOW}🔄 Перезапуск панели для применения изменений...${NC}"
                    systemctl start x-ui
                    sleep 5
                    
                    # Проверяем что панель запустилась
                    if systemctl is-active --quiet x-ui; then
                        echo -e "${GREEN}✅ Панель успешно перезапущена${NC}"
                        INBOUND_CREATED=true
                    else
                        echo -e "${RED}⚠ Панель не запустилась после перезапуска${NC}"
                        echo -e "${YELLOW}Проверьте логи: journalctl -u x-ui -n 20${NC}"
                        INBOUND_CREATED=true  # Inbound все равно создан
                    fi
                else
                    echo -e "${YELLOW}⚠ Inbound создан, но не удалось получить ID${NC}"
                    INBOUND_CREATED=false
                fi
            else
                echo -e "${RED}❌ Ошибка выполнения SQL запроса${NC}"
                echo -e "${RED}Exit code: ${SQL_EXIT_CODE}${NC}"
                echo -e "${RED}Ошибка: ${SQL_RESULT}${NC}"
                echo -e "${YELLOW}Пробуем через API...${NC}"
                INBOUND_CREATED=false
            fi
        else
            echo -e "${YELLOW}⚠ Таблица inbounds не найдена, пробуем через API...${NC}"
            INBOUND_CREATED=false
        fi
        
        # Если SQL не сработал, пытаемся создать inbound через API
        if [ "$INBOUND_CREATED" != true ] && ([ -n "$COOKIE" ] || [ "$USE_API_TOKEN" = true ]); then
            echo -e "${YELLOW}📤 Отправка запроса на создание inbound через API...${NC}"
            
            if [ "$USE_API_TOKEN" = true ] && [ -n "$XUI_API_TOKEN" ]; then
                # Используем API Token
                CREATE_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${XUI_URL%/}/panel/api/inbounds/add" \
                    -H "Content-Type: application/json" \
                    -H "Accept: application/json" \
                    -H "Authorization: Bearer ${XUI_API_TOKEN}" \
                    -d "${INBOUND_JSON}" 2>&1)
            else
                # Используем Cookie
                CREATE_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${XUI_URL%/}/panel/api/inbounds/add" \
                    -H "Content-Type: application/json" \
                    -H "Accept: application/json" \
                    -H "Cookie: session=${COOKIE}" \
                    -d "${INBOUND_JSON}" 2>&1)
            fi
            
            API_HTTP_CODE=$(echo "$CREATE_RESPONSE" | tail -n1)
            API_RESPONSE_BODY=$(echo "$CREATE_RESPONSE" | head -n-1)
            
            echo -e "${YELLOW}API HTTP код: ${API_HTTP_CODE}${NC}"
            echo -e "${YELLOW}API ответ: ${API_RESPONSE_BODY:0:300}${NC}"
        else
            CREATE_RESPONSE=""
            API_RESPONSE_BODY=""
        fi
        
        # Проверяем результат (только если пытались через API и SQL не сработал)
        if [ "$INBOUND_CREATED" != true ]; then
            if [ -n "$API_RESPONSE_BODY" ] && echo "$API_RESPONSE_BODY" | grep -q '"success":true'; then
                echo -e "${GREEN}✅ VLESS Reality inbound успешно создан через API!${NC}"
                echo -e "${GREEN}   Порт: 443${NC}"
                echo -e "${GREEN}   Protocol: VLESS${NC}"
                echo -e "${GREEN}   Network: xhttp${NC}"
                echo -e "${GREEN}   Security: reality${NC}"
                
                # Извлекаем ID созданного inbound
                INBOUND_ID=$(echo "$API_RESPONSE_BODY" | grep -oP '(?<="id":)\d+' | head -1)
                if [ -n "$INBOUND_ID" ]; then
                    echo -e "${GREEN}   Inbound ID: ${INBOUND_ID}${NC}"
                    update_env_value "INBOUND_ID" "${INBOUND_ID}"
                fi
                
                INBOUND_CREATED=true
            elif [ -n "$API_RESPONSE_BODY" ]; then
                # Показываем ошибку только если реально пытались через API
                echo -e "${YELLOW}⚠ Не удалось автоматически создать inbound через API${NC}"
                
                echo -e "${YELLOW}Возможные причины:${NC}"
                if [ -z "$COOKIE" ] && [ "$USE_API_TOKEN" != true ]; then
                    echo -e "  - ${RED}Не удалось авторизоваться (нет cookie)${NC}"
                fi
                if [ "$API_HTTP_CODE" = "401" ] || [ "$API_HTTP_CODE" = "403" ]; then
                    echo -e "  - ${RED}Ошибка авторизации (код ${API_HTTP_CODE})${NC}"
                fi
                if echo "$API_RESPONSE_BODY" | grep -q "port.*already"; then
                    echo -e "  - ${RED}Порт 443 уже занят${NC}"
                fi
                if [ "$API_HTTP_CODE" = "000" ] || [ -z "$API_HTTP_CODE" ]; then
                    echo -e "  - ${RED}API не отвечает (панель не готова)${NC}"
                fi
                echo -e "  - Требуется ручное создание через веб-интерфейс${NC}"
            fi
        fi
        
        # Финальный перезапуск панели для применения всех изменений
        echo -e "\n${YELLOW}🔄 Финальный перезапуск панели...${NC}"
        systemctl restart x-ui
        sleep 5
        
        # Проверяем что панель запустилась
        if systemctl is-active --quiet x-ui; then
            echo -e "${GREEN}✅ Панель успешно запущена и работает${NC}"
        else
            echo -e "${RED}⚠ ОШИБКА: Панель не запустилась!${NC}"
            echo -e "${YELLOW}Проверьте: journalctl -u x-ui -n 30${NC}"
        fi
        
        echo -e "\n${BLUE}========================================${NC}"
        echo -e "${GREEN}   ВАШИ ДАННЫЕ ДЛЯ ВХОДА${NC}"
        echo -e "${BLUE}========================================${NC}"
        echo -e "${GREEN}URL панели:${NC} ${YELLOW}${XUI_URL}${NC}"
        echo -e "${GREEN}Username:${NC}   ${YELLOW}${XUI_USERNAME}${NC}"
        echo -e "${GREEN}Password:${NC}   ${YELLOW}${XUI_PASSWORD}${NC}"
        if [ -n "$XUI_API_TOKEN" ]; then
            echo -e "${GREEN}API Token:${NC}  ${YELLOW}${XUI_API_TOKEN}${NC}"
        fi
        echo -e "${BLUE}========================================${NC}"
        echo -e "${YELLOW}💾 Также эти данные сохранены в:${NC}"
        echo -e "   ${YELLOW}${WORK_DIR}/.env${NC}"
        echo -e "${BLUE}========================================${NC}"
        
        if [ -n "$XUI_VERSION" ]; then
            echo -e "\n${GREEN}✅ Установка 3x-ui v${XUI_VERSION} панели завершена!${NC}"
        else
            echo -e "\n${GREEN}✅ Установка 3x-ui панели завершена!${NC}"
        fi
    else
        echo -e "\n${RED}❌ Ошибка установки 3x-ui панели${NC}"
    fi
}
# Функция создания XHTTP Reality inbound
create_xhttp_reality_inbound() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Создание XHTTP Reality Inbound${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    # Загружаем Reality ключи из .env
    REALITY_PRIVATE_KEY=$(get_env_value "REALITY_PRIVATE_KEY")
    REALITY_PUBLIC_KEY=$(get_env_value "REALITY_PUBLIC_KEY")
    REALITY_SHORT_ID=$(get_env_value "REALITY_SHORT_ID")
    
    # Проверяем наличие необходимых данных
    if [ -z "$REALITY_PRIVATE_KEY" ] || [ -z "$REALITY_PUBLIC_KEY" ] || [ -z "$REALITY_SHORT_ID" ]; then
        echo -e "${RED}❌ Ошибка: Reality ключи не найдены${NC}"
        echo -e "${YELLOW}Запустите установку 3x-ui заново${NC}"
        return 1
    fi
    
    # Создаем JSON конфигурации для settings и streamSettings
    SETTINGS_JSON='{"clients":[],"decryption":"none","fallbacks":[]}'
    
    STREAM_SETTINGS_JSON=$(cat <<STREAMEOF
{
  "network": "xhttp",
  "security": "reality",
  "externalProxy": [],
  "realitySettings": {
    "show": false,
    "xver": 0,
    "target": "${DEFAULT_REALITY_SNI}:443",
    "serverNames": ["${DEFAULT_REALITY_SNI}"],
    "privateKey": "${REALITY_PRIVATE_KEY}",
    "minClientVer": "",
    "maxClientVer": "",
    "maxTimediff": 0,
    "shortIds": ["${REALITY_SHORT_ID}"],
    "settings": {
      "publicKey": "${REALITY_PUBLIC_KEY}",
      "fingerprint": "${DEFAULT_REALITY_FINGERPRINT}",
      "serverName": "",
      "spiderX": "/"
    }
  },
  "xhttpSettings": {
    "path": "/",
    "host": "",
    "headers": {},
    "scMaxBufferedPosts": 30,
    "scMaxEachPostBytes": "1000000",
    "scStreamUpServerSecs": "20-80",
    "noSSEHeader": false,
    "xPaddingBytes": "100-1000",
    "mode": "auto",
    "xPaddingObfsMode": false,
    "scMinPostsIntervalMs": "30"
  }
}
STREAMEOF
)
    
    SNIFFING_JSON='{"enabled":true,"destOverride":["http","tls","quic","fakedns"],"metadataOnly":false,"routeOnly":false}'
    
    # Экранируем JSON для SQL
    SETTINGS_JSON_ESCAPED=$(echo "$SETTINGS_JSON" | sed "s/'/''/g")
    STREAM_SETTINGS_JSON_ESCAPED=$(echo "$STREAM_SETTINGS_JSON" | sed "s/'/''/g")
    SNIFFING_JSON_ESCAPED=$(echo "$SNIFFING_JSON" | sed "s/'/''/g")
    
    # Проверяем и удаляем существующий inbound
    EXISTING_INBOUND=$(sqlite3 /etc/x-ui/x-ui.db "SELECT id FROM inbounds WHERE tag='inbound-443' OR remark='VLESS-Reality-xHTTP';" 2>/dev/null)
    
    if [ -n "$EXISTING_INBOUND" ]; then
        echo -e "${YELLOW}⚠ Найден существующий inbound (ID: ${EXISTING_INBOUND}), удаляем...${NC}"
        sqlite3 /etc/x-ui/x-ui.db "DELETE FROM inbounds WHERE tag='inbound-443' OR remark='VLESS-Reality-xHTTP';" 2>/dev/null
    fi
    
    # Вставляем inbound в базу данных
    SQL_INSERT="INSERT INTO inbounds (user_id, up, down, total, remark, enable, expiry_time, listen, port, protocol, settings, stream_settings, tag, sniffing) VALUES (1, 0, 0, 0, 'VLESS-Reality-xHTTP', 1, 0, '', 443, 'vless', '${SETTINGS_JSON_ESCAPED}', '${STREAM_SETTINGS_JSON_ESCAPED}', 'inbound-443', '${SNIFFING_JSON_ESCAPED}');"
    
    set +e
    SQL_RESULT=$(sqlite3 /etc/x-ui/x-ui.db "${SQL_INSERT}" 2>&1)
    SQL_EXIT_CODE=$?
    set -e
    
    if [ $SQL_EXIT_CODE -eq 0 ]; then
        INBOUND_ID=$(sqlite3 /etc/x-ui/x-ui.db "SELECT id FROM inbounds WHERE remark='VLESS-Reality-xHTTP' ORDER BY id DESC LIMIT 1;" 2>/dev/null)
        
        if [ -n "$INBOUND_ID" ]; then
            echo -e "${GREEN}✅ XHTTP Reality inbound создан успешно!${NC}"
            echo -e "${GREEN}   ID: ${INBOUND_ID}${NC}"
            echo -e "${GREEN}   Порт: 443${NC}"
            echo -e "${GREEN}   Protocol: VLESS${NC}"
            echo -e "${GREEN}   Network: xhttp${NC}"
            echo -e "${GREEN}   Security: reality${NC}"
            
            update_env_value "INBOUND_ID" "${INBOUND_ID}"
            update_env_value "TRANSPORT" "xhttp"
            update_env_value "SECURITY" "reality"
            
            # Извлекаем реальные Reality ключи из созданного inbound
            echo -e "${YELLOW}🔑 Извлечение Reality ключей из inbound...${NC}"
            ACTUAL_PUBLIC_KEY=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.realitySettings.settings.publicKey') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
            ACTUAL_SHORT_ID=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.realitySettings.shortIds[0]') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
            ACTUAL_FINGERPRINT=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.realitySettings.settings.fingerprint') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
            ACTUAL_SNI=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.realitySettings.serverNames[0]') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
            
            if [ -n "$ACTUAL_PUBLIC_KEY" ] && [ -n "$ACTUAL_SHORT_ID" ]; then
                echo -e "${GREEN}✅ Reality ключи извлечены из inbound${NC}"
                echo -e "${GREEN}   Public Key: ${ACTUAL_PUBLIC_KEY:0:30}...${NC}"
                echo -e "${GREEN}   Short ID: ${ACTUAL_SHORT_ID}${NC}"
                echo -e "${GREEN}   Fingerprint: ${ACTUAL_FINGERPRINT}${NC}"
                echo -e "${GREEN}   SNI: ${ACTUAL_SNI}${NC}"
                
                # Обновляем .env с реальными ключами из inbound
                update_env_value "REALITY_PUBLIC_KEY" "${ACTUAL_PUBLIC_KEY}"
                update_env_value "REALITY_SHORT_ID" "${ACTUAL_SHORT_ID}"
                update_env_value "REALITY_FINGERPRINT" "${ACTUAL_FINGERPRINT}"
                update_env_value "REALITY_SNI" "${ACTUAL_SNI}"
                
                echo -e "${GREEN}✅ Ключи сохранены в .env${NC}"
            else
                echo -e "${YELLOW}⚠ Не удалось извлечь ключи из inbound, используем сгенерированные${NC}"
            fi
            
            # Перезапускаем панель
            systemctl stop x-ui > /dev/null 2>&1
            sleep 2
            sqlite3 /etc/x-ui/x-ui.db "PRAGMA wal_checkpoint(TRUNCATE);" > /dev/null 2>&1 || true
            sqlite3 /etc/x-ui/x-ui.db "PRAGMA journal_mode=DELETE;" > /dev/null 2>&1 || true
            systemctl start x-ui > /dev/null 2>&1
            sleep 3
            
            return 0
        fi
    fi
    
    echo -e "${RED}❌ Ошибка создания inbound${NC}"
    return 1
}

# Функция создания TCP Reality inbound
create_tcp_reality_inbound() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Создание TCP Reality Inbound${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    # Загружаем Reality ключи из .env
    REALITY_PRIVATE_KEY=$(get_env_value "REALITY_PRIVATE_KEY")
    REALITY_PUBLIC_KEY=$(get_env_value "REALITY_PUBLIC_KEY")
    REALITY_SHORT_ID=$(get_env_value "REALITY_SHORT_ID")
    
    # Проверяем наличие необходимых данных
    if [ -z "$REALITY_PRIVATE_KEY" ] || [ -z "$REALITY_PUBLIC_KEY" ] || [ -z "$REALITY_SHORT_ID" ]; then
        echo -e "${RED}❌ Ошибка: Reality ключи не найдены${NC}"
        echo -e "${YELLOW}Запустите установку 3x-ui заново${NC}"
        return 1
    fi
    
    # Создаем JSON конфигурации для settings и streamSettings
    SETTINGS_JSON='{"clients":[],"decryption":"none","fallbacks":[]}'
    
    STREAM_SETTINGS_JSON=$(cat <<STREAMEOF
{
  "network": "tcp",
  "security": "reality",
  "externalProxy": [],
  "realitySettings": {
    "show": false,
    "xver": 0,
    "target": "${DEFAULT_REALITY_SNI}:443",
    "serverNames": ["${DEFAULT_REALITY_SNI}"],
    "privateKey": "${REALITY_PRIVATE_KEY}",
    "minClientVer": "",
    "maxClientVer": "",
    "maxTimediff": 0,
    "shortIds": ["${REALITY_SHORT_ID}"],
    "settings": {
      "publicKey": "${REALITY_PUBLIC_KEY}",
      "fingerprint": "${DEFAULT_REALITY_FINGERPRINT}",
      "serverName": "",
      "spiderX": "/"
    }
  },
  "tcpSettings": {
    "acceptProxyProtocol": false,
    "header": {
      "type": "none"
    }
  }
}
STREAMEOF
)
    
    SNIFFING_JSON='{"enabled":true,"destOverride":["http","tls","quic","fakedns"],"metadataOnly":false,"routeOnly":false}'
    
    # Экранируем JSON для SQL
    SETTINGS_JSON_ESCAPED=$(echo "$SETTINGS_JSON" | sed "s/'/''/g")
    STREAM_SETTINGS_JSON_ESCAPED=$(echo "$STREAM_SETTINGS_JSON" | sed "s/'/''/g")
    SNIFFING_JSON_ESCAPED=$(echo "$SNIFFING_JSON" | sed "s/'/''/g")
    
    # Проверяем и удаляем существующий inbound
    EXISTING_INBOUND=$(sqlite3 /etc/x-ui/x-ui.db "SELECT id FROM inbounds WHERE tag='inbound-443' OR remark='VLESS-Reality-TCP';" 2>/dev/null)
    
    if [ -n "$EXISTING_INBOUND" ]; then
        echo -e "${YELLOW}⚠ Найден существующий inbound (ID: ${EXISTING_INBOUND}), удаляем...${NC}"
        sqlite3 /etc/x-ui/x-ui.db "DELETE FROM inbounds WHERE tag='inbound-443' OR remark='VLESS-Reality-TCP';" 2>/dev/null
    fi
    
    # Вставляем inbound в базу данных
    SQL_INSERT="INSERT INTO inbounds (user_id, up, down, total, remark, enable, expiry_time, listen, port, protocol, settings, stream_settings, tag, sniffing) VALUES (1, 0, 0, 0, 'VLESS-Reality-TCP', 1, 0, '', 443, 'vless', '${SETTINGS_JSON_ESCAPED}', '${STREAM_SETTINGS_JSON_ESCAPED}', 'inbound-443', '${SNIFFING_JSON_ESCAPED}');"
    
    set +e
    SQL_RESULT=$(sqlite3 /etc/x-ui/x-ui.db "${SQL_INSERT}" 2>&1)
    SQL_EXIT_CODE=$?
    set -e
    
    if [ $SQL_EXIT_CODE -eq 0 ]; then
        INBOUND_ID=$(sqlite3 /etc/x-ui/x-ui.db "SELECT id FROM inbounds WHERE remark='VLESS-Reality-TCP' ORDER BY id DESC LIMIT 1;" 2>/dev/null)
        
        if [ -n "$INBOUND_ID" ]; then
            echo -e "${GREEN}✅ TCP Reality inbound создан успешно!${NC}"
            echo -e "${GREEN}   ID: ${INBOUND_ID}${NC}"
            echo -e "${GREEN}   Порт: 443${NC}"
            echo -e "${GREEN}   Protocol: VLESS${NC}"
            echo -e "${GREEN}   Network: tcp${NC}"
            echo -e "${GREEN}   Security: reality${NC}"
            
            update_env_value "INBOUND_ID" "${INBOUND_ID}"
            update_env_value "TRANSPORT" "tcp"
            update_env_value "SECURITY" "reality"
            
            # Извлекаем реальные Reality ключи из созданного inbound
            echo -e "${YELLOW}🔑 Извлечение Reality ключей из inbound...${NC}"
            ACTUAL_PUBLIC_KEY=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.realitySettings.settings.publicKey') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
            ACTUAL_SHORT_ID=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.realitySettings.shortIds[0]') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
            ACTUAL_FINGERPRINT=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.realitySettings.settings.fingerprint') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
            ACTUAL_SNI=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.realitySettings.serverNames[0]') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
            
            if [ -n "$ACTUAL_PUBLIC_KEY" ] && [ -n "$ACTUAL_SHORT_ID" ]; then
                echo -e "${GREEN}✅ Reality ключи извлечены из inbound${NC}"
                echo -e "${GREEN}   Public Key: ${ACTUAL_PUBLIC_KEY:0:30}...${NC}"
                echo -e "${GREEN}   Short ID: ${ACTUAL_SHORT_ID}${NC}"
                echo -e "${GREEN}   Fingerprint: ${ACTUAL_FINGERPRINT}${NC}"
                echo -e "${GREEN}   SNI: ${ACTUAL_SNI}${NC}"
                
                # Обновляем .env с реальными ключами из inbound
                update_env_value "REALITY_PUBLIC_KEY" "${ACTUAL_PUBLIC_KEY}"
                update_env_value "REALITY_SHORT_ID" "${ACTUAL_SHORT_ID}"
                update_env_value "REALITY_FINGERPRINT" "${ACTUAL_FINGERPRINT}"
                update_env_value "REALITY_SNI" "${ACTUAL_SNI}"
                
                echo -e "${GREEN}✅ Ключи сохранены в .env${NC}"
            else
                echo -e "${YELLOW}⚠ Не удалось извлечь ключи из inbound, используем сгенерированные${NC}"
            fi
            
            # Перезапускаем панель
            systemctl stop x-ui > /dev/null 2>&1
            sleep 2
            sqlite3 /etc/x-ui/x-ui.db "PRAGMA wal_checkpoint(TRUNCATE);" > /dev/null 2>&1 || true
            sqlite3 /etc/x-ui/x-ui.db "PRAGMA journal_mode=DELETE;" > /dev/null 2>&1 || true
            systemctl start x-ui > /dev/null 2>&1
            sleep 3
            
            return 0
        fi
    fi
    
    echo -e "${RED}❌ Ошибка создания inbound${NC}"
    return 1
}

# Функция создания TCP TLS inbound
create_tcp_tls_inbound() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Создание TCP TLS Inbound${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    # Проверяем наличие сертификата
    if [ ! -f "/root/cert/${SERVER_IP}/fullchain.pem" ] || [ ! -f "/root/cert/${SERVER_IP}/privkey.pem" ]; then
        echo -e "${RED}❌ Ошибка: TLS сертификаты не найдены${NC}"
        echo -e "${YELLOW}Сертификаты должны быть в: /root/cert/${SERVER_IP}/${NC}"
        echo -e "${YELLOW}Запустите установку сертификата сначала${NC}"
        return 1
    fi
    
    echo -e "${GREEN}✅ Найдены TLS сертификаты${NC}"
    
    # Создаем JSON конфигурации для settings и streamSettings
    SETTINGS_JSON='{"clients":[],"decryption":"none","fallbacks":[{"alpn":"","dest":"8080","name":"","path":"","xver":0}]}'
    
    STREAM_SETTINGS_JSON=$(cat <<STREAMEOF
{
  "network": "tcp",
  "security": "tls",
  "externalProxy": [],
  "tlsSettings": {
    "serverName": "",
    "minVersion": "1.2",
    "maxVersion": "1.3",
    "cipherSuites": "",
    "rejectUnknownSni": false,
    "disableSystemRoot": false,
    "enableSessionResumption": false,
    "certificates": [
      {
        "certificateFile": "/root/cert/${SERVER_IP}/fullchain.pem",
        "keyFile": "/root/cert/${SERVER_IP}/privkey.pem",
        "oneTimeLoading": false,
        "usage": "encipherment",
        "buildChain": false
      }
    ],
    "alpn": ["http/1.1"],
    "echServerKeys": "",
    "echForceQuery": "none",
    "settings": {
      "fingerprint": "firefox",
      "echConfigList": ""
    }
  },
  "tcpSettings": {
    "acceptProxyProtocol": false,
    "header": {
      "type": "none"
    }
  }
}
STREAMEOF
)
    
    SNIFFING_JSON='{"enabled":false,"destOverride":["http","tls","quic","fakedns"],"metadataOnly":false,"routeOnly":false}'
    
    # Экранируем JSON для SQL
    SETTINGS_JSON_ESCAPED=$(echo "$SETTINGS_JSON" | sed "s/'/''/g")
    STREAM_SETTINGS_JSON_ESCAPED=$(echo "$STREAM_SETTINGS_JSON" | sed "s/'/''/g")
    SNIFFING_JSON_ESCAPED=$(echo "$SNIFFING_JSON" | sed "s/'/''/g")
    
    # Проверяем и удаляем существующий inbound
    EXISTING_INBOUND=$(sqlite3 /etc/x-ui/x-ui.db "SELECT id FROM inbounds WHERE tag='inbound-443' OR remark='VLESS-TLS-TCP';" 2>/dev/null)
    
    if [ -n "$EXISTING_INBOUND" ]; then
        echo -e "${YELLOW}⚠ Найден существующий inbound (ID: ${EXISTING_INBOUND}), удаляем...${NC}"
        sqlite3 /etc/x-ui/x-ui.db "DELETE FROM inbounds WHERE tag='inbound-443' OR remark='VLESS-TLS-TCP';" 2>/dev/null
    fi
    
    # Вставляем inbound в базу данных
    SQL_INSERT="INSERT INTO inbounds (user_id, up, down, total, remark, enable, expiry_time, listen, port, protocol, settings, stream_settings, tag, sniffing) VALUES (1, 0, 0, 0, 'VLESS-TLS-TCP', 1, 0, '', 443, 'vless', '${SETTINGS_JSON_ESCAPED}', '${STREAM_SETTINGS_JSON_ESCAPED}', 'inbound-443', '${SNIFFING_JSON_ESCAPED}');"
    
    set +e
    SQL_RESULT=$(sqlite3 /etc/x-ui/x-ui.db "${SQL_INSERT}" 2>&1)
    SQL_EXIT_CODE=$?
    set -e
    
    if [ $SQL_EXIT_CODE -eq 0 ]; then
        INBOUND_ID=$(sqlite3 /etc/x-ui/x-ui.db "SELECT id FROM inbounds WHERE remark='VLESS-TLS-TCP' ORDER BY id DESC LIMIT 1;" 2>/dev/null)
        
        if [ -n "$INBOUND_ID" ]; then
            echo -e "${GREEN}✅ TCP TLS inbound создан успешно!${NC}"
            echo -e "${GREEN}   ID: ${INBOUND_ID}${NC}"
            echo -e "${GREEN}   Порт: 443${NC}"
            echo -e "${GREEN}   Protocol: VLESS${NC}"
            echo -e "${GREEN}   Network: tcp${NC}"
            echo -e "${GREEN}   Security: tls${NC}"
            
            update_env_value "INBOUND_ID" "${INBOUND_ID}"
            update_env_value "TRANSPORT" "tcp"
            update_env_value "SECURITY" "tls"
            
            # Извлекаем TLS параметры из созданного inbound
            echo -e "${YELLOW}🔑 Извлечение TLS параметров из inbound...${NC}"
            ACTUAL_FINGERPRINT=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.tlsSettings.settings.fingerprint') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
            ACTUAL_ALPN=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.tlsSettings.alpn[0]') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
            
            if [ -n "$ACTUAL_FINGERPRINT" ]; then
                echo -e "${GREEN}✅ TLS параметры извлечены из inbound${NC}"
                echo -e "${GREEN}   Fingerprint: ${ACTUAL_FINGERPRINT}${NC}"
                echo -e "${GREEN}   ALPN: ${ACTUAL_ALPN}${NC}"
                echo -e "${GREEN}   SNI: ${SERVER_IP}${NC}"
                
                # Обновляем .env с реальными параметрами из inbound
                update_env_value "TLS_FINGERPRINT" "${ACTUAL_FINGERPRINT}"
                update_env_value "TLS_ALPN" "${ACTUAL_ALPN}"
                update_env_value "TLS_SNI" "${SERVER_IP}"
                
                echo -e "${GREEN}✅ Параметры сохранены в .env${NC}"
            else
                echo -e "${YELLOW}⚠ Не удалось извлечь параметры из inbound${NC}"
            fi
            
            # Перезапускаем панель
            systemctl stop x-ui > /dev/null 2>&1
            sleep 2
            sqlite3 /etc/x-ui/x-ui.db "PRAGMA wal_checkpoint(TRUNCATE);" > /dev/null 2>&1 || true
            sqlite3 /etc/x-ui/x-ui.db "PRAGMA journal_mode=DELETE;" > /dev/null 2>&1 || true
            systemctl start x-ui > /dev/null 2>&1
            sleep 3
            
            return 0
        fi
    fi
    
    echo -e "${RED}❌ Ошибка создания inbound${NC}"
    return 1
}

# Функция меню после установки 3x-ui
post_install_menu() {
    while true; do
        echo -e "\n${BLUE}========================================${NC}"
        echo -e "${BLUE}   Создать подключение?${NC}"
        echo -e "${BLUE}========================================${NC}"
        echo -e "${GREEN}Enter${NC} - Да, создать подключение"
        echo -e "${GREEN}n${NC}     - Нет, вернуться в главное меню"
        echo -e "${BLUE}========================================${NC}"
        read -p "Ваш выбор: " create_inbound_choice
        
        if [[ "$create_inbound_choice" =~ ^[Nn]$ ]]; then
            echo -e "${YELLOW}Возврат в главное меню...${NC}"
            return
        fi
        
        # Меню выбора типа подключения
        while true; do
            echo -e "\n${BLUE}========================================${NC}"
            echo -e "${BLUE}   Выберите тип подключения${NC}"
            echo -e "${BLUE}========================================${NC}"
            echo -e "${GREEN}1${NC} - XHTTP Reality (рекомендуется)"
            echo -e "${GREEN}2${NC} - TCP Reality"
            echo -e "${GREEN}3${NC} - TCP TLS"
            echo -e "${GREEN}0${NC} - Вернуться в главное меню"
            echo -e "${BLUE}========================================${NC}"
            read -p "Ваш выбор: " inbound_type
            
            if [[ "$inbound_type" == "0" ]]; then
                echo -e "${YELLOW}Возврат в главное меню...${NC}"
                break 2
            fi
            
            case $inbound_type in
                1)
                    if create_xhttp_reality_inbound; then
                        # Предлагаем установить бота
                        echo -e "\n${BLUE}========================================${NC}"
                        echo -e "${BLUE}   Установить xuibot?${NC}"
                        echo -e "${BLUE}========================================${NC}"
                        echo -e "${GREEN}Enter${NC} - Да, установить бота"
                        echo -e "${GREEN}0${NC}     - Нет, вернуться в главное меню"
                        echo -e "${BLUE}========================================${NC}"
                        read -p "Ваш выбор: " install_bot_choice
                        
                        if [[ "$install_bot_choice" != "0" ]]; then
                            install_bot
                        fi
                        return
                    fi
                    ;;
                2)
                    if create_tcp_reality_inbound; then
                        # Предлагаем установить бота
                        echo -e "\n${BLUE}========================================${NC}"
                        echo -e "${BLUE}   Установить xuibot?${NC}"
                        echo -e "${BLUE}========================================${NC}"
                        echo -e "${GREEN}Enter${NC} - Да, установить бота"
                        echo -e "${GREEN}0${NC}     - Нет, вернуться в главное меню"
                        echo -e "${BLUE}========================================${NC}"
                        read -p "Ваш выбор: " install_bot_choice
                        
                        if [[ "$install_bot_choice" != "0" ]]; then
                            install_bot
                        fi
                        return
                    fi
                    ;;
                3)
                    if create_tcp_tls_inbound; then
                        # Предлагаем установить бота
                        echo -e "\n${BLUE}========================================${NC}"
                        echo -e "${BLUE}   Установить xuibot?${NC}"
                        echo -e "${BLUE}========================================${NC}"
                        echo -e "${GREEN}Enter${NC} - Да, установить бота"
                        echo -e "${GREEN}0${NC}     - Нет, вернуться в главное меню"
                        echo -e "${BLUE}========================================${NC}"
                        read -p "Ваш выбор: " install_bot_choice
                        
                        if [[ "$install_bot_choice" != "0" ]]; then
                            install_bot
                        fi
                        return
                    fi
                    ;;
                *)
                    echo -e "${RED}Неверный выбор. Попробуйте снова.${NC}"
                    ;;
            esac
        done
    done
}
# Функция проверки существующего сертификата
check_existing_certificate() {
    local server_ip=$1
    local cert_dir="/root/.acme.sh/${server_ip}_ecc"
    
    # Проверяем наличие сертификата
    if [ -d "$cert_dir" ] && [ -f "$cert_dir/fullchain.cer" ] && [ -f "$cert_dir/${server_ip}.key" ]; then
        echo -e "${YELLOW}🔍 Найден существующий сертификат для ${server_ip}${NC}"
        
        # Проверяем срок действия сертификата
        local expiry_date=$(openssl x509 -enddate -noout -in "$cert_dir/fullchain.cer" 2>/dev/null | cut -d= -f2)
        if [ -n "$expiry_date" ]; then
            local expiry_epoch=$(date -d "$expiry_date" +%s 2>/dev/null)
            local current_epoch=$(date +%s)
            local days_left=$(( ($expiry_epoch - $current_epoch) / 86400 ))
            
            if [ $days_left -gt 0 ]; then
                echo -e "${GREEN}✅ Сертификат действителен ещё ${days_left} дней${NC}"
                echo -e "${BLUE}Срок действия до: ${expiry_date}${NC}"
                
                read -p "Использовать существующий сертификат? (Enter - да, 0 - запросить новый): " use_existing
                
                if [[ "$use_existing" != "0" ]]; then
                    return 0  # Использовать существующий
                else
                    return 1  # Запросить новый
                fi
            else
                echo -e "${RED}⚠️  Сертификат истёк ${days_left#-} дней назад${NC}"
                return 1  # Запросить новый
            fi
        else
            echo -e "${YELLOW}⚠️  Не удалось проверить срок действия сертификата${NC}"
            return 1
        fi
    else
        echo -e "${YELLOW}ℹ️  Существующий сертификат не найден${NC}"
        return 1  # Запросить новый
    fi
}

# Функция установки существующего сертификата в 3x-ui
install_existing_certificate() {
    local server_ip=$1
    local cert_dir="/root/.acme.sh/${server_ip}_ecc"
    local target_dir="/root/cert/ip"
    
    echo -e "${YELLOW}📦 Установка существующего сертификата...${NC}"
    
    # Проверяем что директория с сертификатом существует
    if [ ! -d "$cert_dir" ]; then
        echo -e "${RED}❌ Директория с сертификатом не найдена: $cert_dir${NC}"
        return 1
    fi
    
    # Проверяем что файлы сертификата существуют
    if [ ! -f "$cert_dir/${server_ip}.key" ]; then
        echo -e "${RED}❌ Файл ключа не найден: $cert_dir/${server_ip}.key${NC}"
        return 1
    fi
    
    if [ ! -f "$cert_dir/fullchain.cer" ]; then
        echo -e "${RED}❌ Файл сертификата не найден: $cert_dir/fullchain.cer${NC}"
        return 1
    fi
    
    echo -e "${BLUE}📂 Источник: $cert_dir${NC}"
    echo -e "${BLUE}📂 Назначение: $target_dir${NC}"
    
    # Создаём целевую директорию
    mkdir -p "$target_dir"
    
    # Удаляем старые файлы/симлинки если существуют
    rm -f "$target_dir/privkey.pem" "$target_dir/fullchain.pem"
    
    # Создаём символические ссылки вместо копирования
    echo -e "${YELLOW}🔗 Создание символических ссылок...${NC}"
    if ln -sf "$cert_dir/${server_ip}.key" "$target_dir/privkey.pem" && \
       ln -sf "$cert_dir/fullchain.cer" "$target_dir/fullchain.pem"; then
        
        echo -e "${GREEN}✅ Символические ссылки созданы${NC}"
        echo -e "${BLUE}   $target_dir/privkey.pem -> $cert_dir/${server_ip}.key${NC}"
        echo -e "${BLUE}   $target_dir/fullchain.pem -> $cert_dir/fullchain.cer${NC}"
        echo -e "${GREEN}ℹ️  Сертификат будет автоматически обновляться через acme.sh${NC}"
        
        # Настраиваем пути в 3x-ui через базу данных
        if [ -f "/etc/x-ui/x-ui.db" ]; then
            echo -e "${YELLOW}🔧 Настройка путей к сертификатам в панели...${NC}"
            
            # Устанавливаем sqlite3 если не установлен
            if ! command -v sqlite3 &> /dev/null; then
                apt-get update -qq && apt-get install -y sqlite3 -qq > /dev/null 2>&1
            fi
            
            # Останавливаем панель для безопасной работы с базой
            systemctl stop x-ui 2>/dev/null || true
            sleep 1
            
            # Добавляем пути к сертификатам с несколькими попытками
            local max_attempts=3
            local attempt=1
            local success=false
            
            while [ $attempt -le $max_attempts ]; do
                # Удаляем старые записи если есть
                sqlite3 /etc/x-ui/x-ui.db "DELETE FROM settings WHERE key IN ('webCertFile', 'webKeyFile');" 2>/dev/null
                
                # Добавляем новые записи
                sqlite3 /etc/x-ui/x-ui.db "INSERT INTO settings (key, value) VALUES ('webCertFile', '/root/cert/ip/fullchain.pem');" 2>/dev/null
                sqlite3 /etc/x-ui/x-ui.db "INSERT INTO settings (key, value) VALUES ('webKeyFile', '/root/cert/ip/privkey.pem');" 2>/dev/null
                
                # Проверяем что записи добавлены
                local cert_file=$(sqlite3 /etc/x-ui/x-ui.db "SELECT value FROM settings WHERE key='webCertFile';" 2>/dev/null)
                local key_file=$(sqlite3 /etc/x-ui/x-ui.db "SELECT value FROM settings WHERE key='webKeyFile';" 2>/dev/null)
                
                if [ -n "$cert_file" ] && [ -n "$key_file" ]; then
                    echo -e "${GREEN}✅ Пути к сертификатам настроены (попытка $attempt)${NC}"
                    echo -e "${BLUE}   Certificate: $cert_file${NC}"
                    echo -e "${BLUE}   Private Key: $key_file${NC}"
                    success=true
                    break
                else
                    echo -e "${YELLOW}⚠️  Попытка $attempt не удалась, повторяю...${NC}"
                    attempt=$((attempt + 1))
                    sleep 1
                fi
            done
            
            if [ "$success" = false ]; then
                echo -e "${RED}⚠️  Не удалось настроить пути к сертификатам после $max_attempts попыток${NC}"
                echo -e "${YELLOW}💡 Настройте вручную через веб-интерфейс панели:${NC}"
                echo -e "${YELLOW}   Certificate: /root/cert/ip/fullchain.pem${NC}"
                echo -e "${YELLOW}   Private Key: /root/cert/ip/privkey.pem${NC}"
            fi
            
            # Запускаем панель обратно
            systemctl start x-ui 2>/dev/null || true
            sleep 2
        fi
        
        echo -e "${GREEN}✅ Существующий сертификат успешно установлен!${NC}"
        return 0
    else
        echo -e "${RED}❌ Ошибка копирования сертификата${NC}"
        return 1
    fi
}


# Функция установки 3x-ui панели версии 2.9.4
install_3xui_v294() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Установка 3x-ui Panel v2.9.4${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    # Проверка установлена ли уже панель
    if systemctl is-active --quiet x-ui; then
        echo -e "${YELLOW}⚠ 3x-ui панель уже установлена${NC}"
        read -p "Переустановить? (нажмите Enter для подтверждения или 0 для отмены): " reinstall
        if [[ "$reinstall" == "0" ]]; then
            echo -e "${YELLOW}Отменено${NC}"
            return
        fi
        
        # Удаляем старую панель перед переустановкой
        echo -e "\n${YELLOW}🗑️  Удаление старой панели перед переустановкой...${NC}"
        
        # Остановка сервиса
        systemctl stop x-ui 2>/dev/null || true
        systemctl disable x-ui 2>/dev/null || true
        
        # Удаление файлов и конфигурации
        echo -e "${YELLOW}📁 Удаление файлов программы...${NC}"
        rm -rf /usr/local/x-ui 2>/dev/null || true
        
        echo -e "${YELLOW}🗄️  Удаление базы данных и конфигурации...${NC}"
        rm -rf /etc/x-ui 2>/dev/null || true
        
        echo -e "${YELLOW}🔧 Удаление systemd сервиса...${NC}"
        rm -f /etc/systemd/system/x-ui.service 2>/dev/null || true
        systemctl daemon-reload
        
        # Удаление из .env
        if [ -f "${WORK_DIR}/.env" ]; then
            echo -e "${YELLOW}🔑 Очистка данных из .env...${NC}"
            sed -i '/^XUI_/d' "${WORK_DIR}/.env" 2>/dev/null || true
            sed -i '/^REALITY_/d' "${WORK_DIR}/.env" 2>/dev/null || true
            sed -i '/^INBOUND_ID=/d' "${WORK_DIR}/.env" 2>/dev/null || true
            sed -i '/^TRANSPORT=/d' "${WORK_DIR}/.env" 2>/dev/null || true
            sed -i '/^SECURITY=/d' "${WORK_DIR}/.env" 2>/dev/null || true
        fi
        
        echo -e "${GREEN}✅ Старая панель удалена${NC}\n"
    fi
    
    SERVER_IP=$(curl -s ifconfig.me)
    
    # Проверяем существующий сертификат перед установкой (если включено)
    USE_EXISTING_CERT=false
    if [ "$ENABLE_CERT_REUSE" = "true" ]; then
        echo -e "\n${BLUE}═══════════════════════════════════════════${NC}"
        echo -e "${BLUE}     Проверка SSL сертификата${NC}"
        echo -e "${BLUE}═══════════════════════════════════════════${NC}\n"
        
        if check_existing_certificate "$SERVER_IP"; then
            USE_EXISTING_CERT=true
            echo -e "${GREEN}✓ Будет использован существующий сертификат${NC}\n"
        else
            echo -e "${YELLOW}ℹ️  Будет запрошен новый сертификат при установке${NC}\n"
        fi
    else
        echo -e "\n${YELLOW}ℹ️  Проверка существующих сертификатов отключена (ENABLE_CERT_REUSE=false)${NC}"
        echo -e "${YELLOW}ℹ️  Будет запрошен новый сертификат при установке${NC}\n"
    fi
    
    echo -e "${YELLOW}📦 Загрузка и установка 3x-ui v2.9.4...${NC}\n"
    
    # Запускаем установку с выводом на экран и в файл одновременно
    INSTALL_LOG="/tmp/xui_install_$$.log"
    
    # Если есть существующий сертификат, подготавливаем его для установщика
    if [ "$USE_EXISTING_CERT" = true ]; then
        # Создаём символические ссылки на существующий сертификат
        TARGET_CERT_DIR="/root/cert/ip"
        mkdir -p "$TARGET_CERT_DIR"
        
        CERT_SOURCE="/root/.acme.sh/${SERVER_IP}_ecc"
        
        # Удаляем старые файлы/симлинки если существуют
        rm -f "$TARGET_CERT_DIR/privkey.pem" "$TARGET_CERT_DIR/fullchain.pem"
        
        # Создаём символические ссылки
        ln -sf "$CERT_SOURCE/${SERVER_IP}.key" "$TARGET_CERT_DIR/privkey.pem"
        ln -sf "$CERT_SOURCE/fullchain.cer" "$TARGET_CERT_DIR/fullchain.pem"
        
        echo -e "${GREEN}✓ Символические ссылки на сертификат созданы в $TARGET_CERT_DIR${NC}"
        echo -e "${GREEN}ℹ️  Сертификат будет автоматически обновляться через acme.sh${NC}"
        echo -e "${YELLOW}ℹ️  Установщик попытается получить сертификат (получит Rate Limit), затем мы настроим пути${NC}\n"
    fi
    
    # Передаем пустые ответы (Enter) на все вопросы через stdin
    # Установщик попытается получить сертификат и получит Rate Limit (это нормально)
    printf '\n\n\n\n\n' | bash <(curl -Ls "https://raw.githubusercontent.com/MHSanaei/3x-ui/v2.9.4/install.sh") v2.9.4 2>&1 | tee "$INSTALL_LOG"
    
    # Читаем вывод из лог-файла
    INSTALL_OUTPUT=$(cat "$INSTALL_LOG" 2>/dev/null || echo "")
    
    # Удаляем временный лог-файл
    rm -f "$INSTALL_LOG"
    
    # Извлекаем версию из вывода установщика
    XUI_VERSION="2.9.4"
    
    # Проверяем успешность установки
    if echo "$INSTALL_OUTPUT" | grep -q "installation finished"; then
        # Извлекаем учетные данные из вывода инсталятора и очищаем от ANSI кодов
        XUI_USERNAME=$(echo "$INSTALL_OUTPUT" | grep -oP 'Username:\s*\K\S+' | head -1 | sed 's/\x1b\[[0-9;]*m//g')
        XUI_PASSWORD=$(echo "$INSTALL_OUTPUT" | grep -oP 'Password:\s*\K\S+' | head -1 | sed 's/\x1b\[[0-9;]*m//g')
        XUI_PORT=$(echo "$INSTALL_OUTPUT" | grep -oP 'Port:\s*\K\d+' | head -1 | sed 's/\x1b\[[0-9;]*m//g')
        XUI_PATH=$(echo "$INSTALL_OUTPUT" | grep -oP 'WebBasePath:\s*\K\S+' | head -1 | sed 's/\x1b\[[0-9;]*m//g')
        
        # Исправление проблемы с базой данных x-ui.db
        if [ -d "/etc/x-ui/x-ui.db" ]; then
            systemctl stop x-ui
            rm -rf /etc/x-ui/x-ui.db
            touch /etc/x-ui/x-ui.db
            chmod 644 /etc/x-ui/x-ui.db
            systemctl start x-ui
            sleep 2
        fi
        
        # Проверяем успешность установки SSL сертификата
        echo -e "\n${YELLOW}🔍 Проверка SSL сертификата...${NC}"
        
        # Устанавливаем sqlite3 если не установлен
        if ! command -v sqlite3 &> /dev/null; then
            apt-get update -qq && apt-get install -y sqlite3 -qq > /dev/null 2>&1
        fi
        
        # Проверяем наличие сертификата в установщике
        SSL_SETUP_FAILED=false
        if echo "$INSTALL_OUTPUT" | grep -q "IP certificate setup failed\|certificate setup failed\|Failed to issue\|rateLimited\|too many certificates\|rate.*limit"; then
            SSL_SETUP_FAILED=true
            
            # Проверяем конкретную причину ошибки
            if echo "$INSTALL_OUTPUT" | grep -qi "rateLimited\|too many certificates\|rate.*limit"; then
                echo -e "${YELLOW}⚠️  Достигнут лимит Let's Encrypt (rate limit)${NC}"
                echo -e "${YELLOW}ℹ️  Панель будет настроена для работы по HTTP${NC}"
            else
                echo -e "${YELLOW}⚠️  Установщик не смог получить SSL сертификат${NC}"
            fi
        fi
        
        # Если использовали существующий сертификат, устанавливаем его
        if [ "$USE_EXISTING_CERT" = true ]; then
            echo -e "${GREEN}✓ Установка существующего сертификата...${NC}"
            
            if install_existing_certificate "$SERVER_IP"; then
                echo -e "${GREEN}✓ Существующий сертификат успешно установлен${NC}"
                SSL_SETUP_FAILED=false
            else
                echo -e "${YELLOW}⚠️  Не удалось установить существующий сертификат${NC}"
                SSL_SETUP_FAILED=true
            fi
        fi
        
        # Если SSL не удалось настроить, удаляем пути к сертификатам для работы по HTTP
        if [ "$SSL_SETUP_FAILED" = true ]; then
            echo -e "${YELLOW}⚠️  Настройка панели для работы по HTTP...${NC}"
            
            if [ -f "/etc/x-ui/x-ui.db" ]; then
                # Останавливаем панель
                systemctl stop x-ui 2>/dev/null || true
                sleep 1
                
                # Удаляем пути к сертификатам из базы данных
                sqlite3 /etc/x-ui/x-ui.db "DELETE FROM settings WHERE key IN ('webCertFile', 'webKeyFile');" 2>/dev/null
                
                # Сбрасываем webBasePath на корень для упрощения доступа
                echo -e "${YELLOW}ℹ️  Сброс webBasePath на корневой путь для упрощения доступа...${NC}"
                sqlite3 /etc/x-ui/x-ui.db "UPDATE settings SET value='/' WHERE key='webBasePath';" 2>/dev/null
                
                # Запускаем панель
                systemctl start x-ui 2>/dev/null || true
                sleep 2
                
                echo -e "${GREEN}✅ Панель настроена для работы по HTTP${NC}"
                echo -e "${GREEN}✅ webBasePath сброшен на корневой путь (/)${NC}"
            fi
        fi
        
        # Проверяем что данные получены от инсталятора
        if [ -z "$XUI_USERNAME" ] || [ -z "$XUI_PASSWORD" ]; then
            # Устанавливаем sqlite3 если не установлен
            if ! command -v sqlite3 &> /dev/null; then
                apt-get update -qq && apt-get install -y sqlite3 -qq > /dev/null 2>&1
            fi
            
            # Получаем username из базы данных
            if [ -f "/etc/x-ui/x-ui.db" ]; then
                XUI_USERNAME=$(sqlite3 /etc/x-ui/x-ui.db "SELECT username FROM users LIMIT 1;" 2>/dev/null || echo "")
            fi
            
            # Если пароль не получен, используем дефолтный
            if [ -z "$XUI_PASSWORD" ]; then
                XUI_PASSWORD="admin"
            fi
        fi
        
        # Получаем порт и путь если не извлечены
        if [ -z "$XUI_PORT" ] || [ -z "$XUI_PATH" ]; then
            sleep 2
            
            # Если SSL не удалось настроить, принудительно устанавливаем путь в корень
            if [ "$SSL_SETUP_FAILED" = true ]; then
                XUI_PATH="/"
                echo -e "${YELLOW}ℹ️  Используется корневой путь (/) для HTTP режима${NC}"
            fi
            
            XUI_SETTINGS=$(echo "n" | timeout 5 x-ui settings 2>/dev/null || echo "")
            
            if [ -n "$XUI_SETTINGS" ]; then
                if [ -z "$XUI_PORT" ]; then
                    XUI_PORT=$(echo "$XUI_SETTINGS" | grep "port:" | awk '{print $2}')
                fi
                # Получаем путь только если SSL настроен успешно
                if [ -z "$XUI_PATH" ] && [ "$SSL_SETUP_FAILED" = false ]; then
                    XUI_PATH=$(echo "$XUI_SETTINGS" | grep "webBasePath:" | awk '{print $2}' | sed 's/\/$//')
                    # Добавляем leading slash если нужно
                    if [ -n "$XUI_PATH" ] && [[ "$XUI_PATH" != /* ]] && [ "$XUI_PATH" != "/" ]; then
                        XUI_PATH="/${XUI_PATH}"
                    fi
                fi
            fi
            
            # Дефолтные значения если не получены
            if [ -z "$XUI_PORT" ]; then
                XUI_PORT="2053"
            fi
            if [ -z "$XUI_PATH" ]; then
                XUI_PATH="/"
            fi
        fi
        
        # Формируем URL для v2.9.4 (БЕЗ /panel в конце)
        # Используем HTTP если SSL не настроен, иначе HTTPS
        PROTOCOL="https"
        if [ "$SSL_SETUP_FAILED" = true ]; then
            PROTOCOL="http"
            # Для HTTP режима всегда используем корневой путь
            XUI_PATH="/"
        fi
        
        if [ -z "$XUI_PATH" ] || [ "$XUI_PATH" = "/" ]; then
            XUI_URL="${PROTOCOL}://${SERVER_IP}:${XUI_PORT}"
        else
            # Добавляем leading slash если нужно
            if [[ "$XUI_PATH" != /* ]]; then
                XUI_PATH="/${XUI_PATH}"
            fi
            # Для HTTPS с webBasePath используем путь как есть (с trailing slash если он есть)
            # Для HTTP режима путь уже установлен в "/" выше
            XUI_URL="${PROTOCOL}://${SERVER_IP}:${XUI_PORT}${XUI_PATH}"
        fi
        
        echo -e "\n${GREEN}═══════════════════════════════════════════${NC}"
        echo -e "${GREEN}     Панель 3x-ui успешно установлена!${NC}"
        echo -e "${GREEN}═══════════════════════════════════════════${NC}"
        echo -e "${BLUE}📍 URL панели: ${YELLOW}${XUI_URL}${NC}"
        echo -e "${BLUE}👤 Логин:      ${YELLOW}${XUI_USERNAME}${NC}"
        echo -e "${BLUE}🔑 Пароль:     ${YELLOW}${XUI_PASSWORD}${NC}"
        echo -e "${BLUE}🔌 Порт:       ${YELLOW}${XUI_PORT}${NC}"
        
        if [ "$SSL_SETUP_FAILED" = true ]; then
            echo -e "\n${YELLOW}⚠️  Панель работает по HTTP (без SSL)${NC}"
            echo -e "${YELLOW}ℹ️  SSL сертификат не был получен (rate limit или другая ошибка)${NC}"
            echo -e "${YELLOW}ℹ️  Для безопасности рекомендуется получить SSL сертификат позже${NC}"
        else
            echo -e "\n${GREEN}✅ SSL сертификат настроен, панель работает по HTTPS${NC}"
            echo -e "${YELLOW}ℹ️  Бот автоматически попробует HTTP если HTTPS не работает${NC}"
        fi
        echo -e "${GREEN}═══════════════════════════════════════════${NC}\n"
        
        # Генерация Reality ключей
        # Установка xray если не установлен
        if ! command -v xray &> /dev/null; then
            bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install > /dev/null 2>&1
        fi
        
        # Генерация ключей Reality
        REALITY_KEYS=$(xray x25519)
        REALITY_PRIVATE_KEY=$(echo "$REALITY_KEYS" | grep -E "(Private key:|PrivateKey:)" | awk '{print $NF}')
        REALITY_PUBLIC_KEY=$(echo "$REALITY_KEYS" | grep -E "(Public key:|Password \(PublicKey\):)" | awk '{print $NF}')
        
        # Генерация Short IDs
        REALITY_SHORT_ID=$(openssl rand -hex 8)
        
        # Создание .env файла с учетными данными 3x-ui
        create_env_if_not_exists
        
        # Сохранение учетных данных в .env
        update_env_value "XUI_URL" "${XUI_URL}"
        update_env_value "XUI_USERNAME" "${XUI_USERNAME}"
        update_env_value "XUI_PASSWORD" "${XUI_PASSWORD}"
        update_env_value "REALITY_PUBLIC_KEY" "${REALITY_PUBLIC_KEY}"
        update_env_value "REALITY_PRIVATE_KEY" "${REALITY_PRIVATE_KEY}"
        update_env_value "REALITY_SHORT_ID" "${REALITY_SHORT_ID}"
        update_env_value "SERVER_ADDRESS" "${SERVER_IP}"
        update_env_value "SERVER_IP" "${SERVER_IP}"
        update_env_value "SERVER_PORT" "443"
        update_env_value "XUI_VERSION" "2.9.4"
        
        # Финальное сообщение
        echo -e "\n${GREEN}✅ Установка 3x-ui панели завершена!${NC}\n"
        echo -e "\n${BLUE}Также можно установить вручную:${NC}\n"
        echo -e "${YELLOW}VERSION=v2.9.4 && bash <(curl -Ls "https://raw.githubusercontent.com/mhsanaei/3x-ui/$VERSION/install.sh") $VERSION${NC}\n"
        


        # Интерактивное меню после установки
        post_install_menu
    else
        echo -e "\n${RED}❌ Ошибка установки 3x-ui v2.9.4 панели${NC}"
    fi
}

# Обёртка для установки 3x-ui - сразу устанавливаем v2.9.4
install_3xui() {
    install_3xui_v294
}

# Функция установки 3x-ui Panel версии 3.x (Latest) с API токеном
install_3xui_v3() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Установка 3x-ui Panel v3.x (Latest)${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    # Проверка установленной панели
    if systemctl is-active --quiet x-ui; then
        echo -e "${YELLOW}⚠ 3x-ui панель уже установлена${NC}"
        read -p "Переустановить? (нажмите Enter для продолжения или 0 для отмены): " reinstall
        if [[ "$reinstall" == "0" ]]; then
            echo -e "${YELLOW}Отменено${NC}"
            return
        fi
        echo -e "${YELLOW}⚠ Остановка панели...${NC}"
        systemctl stop x-ui
    fi
    
    echo -e "${YELLOW}⚠ Установка 3x-ui v3.x (latest версия)...${NC}"
    echo -e "${BLUE}Панель будет установлена с автоматической настройкой${NC}"
    echo -e "${GREEN}Для v3 бот работает через API, поэтому можно выбрать любую БД${NC}"
    echo -e "${GREEN}SQLite - для небольших нагрузок (< 500 клиентов)${NC}"
    echo -e "${GREEN}PostgreSQL - для высоких нагрузок и множества узлов${NC}\n"
    
    # Получаем IP сервера для проверки сертификата
    SERVER_IP=$(curl -s https://api4.ipify.org 2>/dev/null || curl -s https://ipv4.icanhazip.com 2>/dev/null || echo "")
    
    # Проверка существующего SSL сертификата
    USE_EXISTING_CERT=false
    if [ "$ENABLE_CERT_REUSE" = "true" ] && [ -n "$SERVER_IP" ]; then
        echo -e "\n${BLUE}═══════════════════════════════════════════${NC}"
        echo -e "${BLUE}     Проверка SSL сертификата${NC}"
        echo -e "${BLUE}═══════════════════════════════════════════${NC}\n"
        
        if check_existing_certificate "$SERVER_IP"; then
            USE_EXISTING_CERT=true
            echo -e "${GREEN}✓ Будет использован существующий сертификат${NC}\n"
            
            # Создаём символические ссылки на существующий сертификат
            TARGET_CERT_DIR="/root/cert/ip"
            mkdir -p "$TARGET_CERT_DIR"
            
            CERT_SOURCE="/root/.acme.sh/${SERVER_IP}_ecc"
            
            # Удаляем старые файлы/симлинки если существуют
            rm -f "$TARGET_CERT_DIR/privkey.pem" "$TARGET_CERT_DIR/fullchain.pem"
            
            # Создаём символические ссылки
            ln -sf "$CERT_SOURCE/${SERVER_IP}.key" "$TARGET_CERT_DIR/privkey.pem"
            ln -sf "$CERT_SOURCE/fullchain.cer" "$TARGET_CERT_DIR/fullchain.pem"
            
            echo -e "${GREEN}✓ Символические ссылки на сертификат созданы в $TARGET_CERT_DIR${NC}"
            echo -e "${GREEN}ℹ️  Сертификат будет автоматически обновляться через acme.sh${NC}"
            echo -e "${YELLOW}ℹ️  Установщик попытается получить сертификат (получит Rate Limit), затем мы настроим пути${NC}\n"
        else
            echo -e "${YELLOW}ℹ️  Будет запрошен новый сертификат при установке${NC}\n"
        fi
    else
        if [ "$ENABLE_CERT_REUSE" != "true" ]; then
            echo -e "\n${YELLOW}ℹ️  Проверка существующих сертификатов отключена (ENABLE_CERT_REUSE не установлен)${NC}"
        fi
        echo -e "${YELLOW}ℹ️  SSL сертификат будет пропущен (можно настроить позже)${NC}\n"
    fi
    
    # Установка через официальный скрипт
    echo -e "${YELLOW}⚠ Запуск установщика 3x-ui...${NC}"
    echo -e "${YELLOW}⚠ Будет автоматически выбрана база данных SQLite${NC}"
    
    # Создаем временный файл для сохранения вывода установщика
    INSTALL_OUTPUT=$(mktemp)
    
    # Устанавливаем переменную окружения для пропуска SSL
    export ENABLE_CERT_REUSE="true"
    
    # Автоматически отвечаем на вопросы установщика:
    # 1 - выбор SQLite
    # 4 - пропуск SSL (Skip SSL)
    # Добавляем больше пустых строк для обработки всех возможных вопросов
    printf '1\n4\n\n\n\n\n\n\n\n\n\n' | bash <(curl -Ls https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh) 2>&1 | tee "$INSTALL_OUTPUT"
    
    # Проверка успешности установки
    if systemctl is-active --quiet x-ui; then
        echo -e "\n${GREEN}✓ 3x-ui v3.x установлена успешно${NC}"
        
        # Проверяем наличие ошибок SSL в установщике
        SSL_SETUP_FAILED=false
        if echo "$INSTALL_OUTPUT" | grep -q "IP certificate setup failed\|certificate setup failed\|Failed to issue\|rateLimited\|too many certificates\|rate.*limit"; then
            SSL_SETUP_FAILED=true
            
            # Проверяем конкретную причину ошибки
            if echo "$INSTALL_OUTPUT" | grep -qi "rateLimited\|too many certificates\|rate.*limit"; then
                echo -e "${YELLOW}⚠️  Достигнут лимит Let's Encrypt (rate limit)${NC}"
            else
                echo -e "${YELLOW}⚠️  Установщик не смог получить SSL сертификат${NC}"
            fi
        fi
        
        # Если использовали существующий сертификат, устанавливаем его
        if [ "$USE_EXISTING_CERT" = true ]; then
            echo -e "${GREEN}✓ Установка существующего сертификата...${NC}"
            
            if install_existing_certificate "$SERVER_IP"; then
                echo -e "${GREEN}✓ Существующий сертификат успешно установлен${NC}"
                SSL_SETUP_FAILED=false
                
                # Настраиваем пути к сертификату в панели через x-ui CLI
                x-ui cert -webCert /root/cert/ip/fullchain.pem -webCertKey /root/cert/ip/privkey.pem >/dev/null 2>&1
                systemctl restart x-ui
                sleep 2
            else
                echo -e "${YELLOW}⚠️  Не удалось установить существующий сертификат${NC}"
                SSL_SETUP_FAILED=true
            fi
        fi
        
        # Ожидание запуска панели
        echo -e "${YELLOW}⚠ Ожидание запуска панели...${NC}"
        sleep 5
        
        # Проверка что панель запустилась
        MAX_WAIT=30
        WAIT_COUNT=0
        while ! systemctl is-active --quiet x-ui && [ $WAIT_COUNT -lt $MAX_WAIT ]; do
            sleep 1
            WAIT_COUNT=$((WAIT_COUNT + 1))
        done
        
        if systemctl is-active --quiet x-ui; then
            echo -e "${GREEN}✓ Панель успешно запущена${NC}"
        else
            echo -e "${RED}✗ Панель не запустилась в течение ${MAX_WAIT} секунд${NC}"
        fi
        
        # Извлечение учетных данных из вывода установщика
        echo -e "${YELLOW}⚠ Извлечение учетных данных панели...${NC}"
        
        # Парсим вывод установщика для получения данных
        XUI_USERNAME=$(grep -oP 'Username:\s+\K\S+' "$INSTALL_OUTPUT" | tail -1)
        XUI_PASSWORD=$(grep -oP 'Password:\s+\K\S+' "$INSTALL_OUTPUT" | tail -1)
        XUI_PORT=$(grep -oP 'Port:\s+\K\d+' "$INSTALL_OUTPUT" | tail -1)
        XUI_WEB_BASE_PATH=$(grep -oP 'WebBasePath:\s+\K\S+' "$INSTALL_OUTPUT" | tail -1)
        XUI_API_TOKEN=$(grep -oP 'API Token:\s+\K\S+' "$INSTALL_OUTPUT" | tail -1)
        
        # Удаляем временный файл
        rm -f "$INSTALL_OUTPUT"
        
        # Определение версии
        XUI_VERSION=$(x-ui version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1)
        if [ -z "$XUI_VERSION" ]; then
            XUI_VERSION="latest"
        fi
        
        # Получение IP сервера
        SERVER_IP=$(curl -s https://api4.ipify.org 2>/dev/null || curl -s https://ipv4.icanhazip.com 2>/dev/null || echo "YOUR_SERVER_IP")
        XUI_URL="http://${SERVER_IP}:${XUI_PORT}"
        
        # Сохранение в .env
        create_env_if_not_exists
        
        echo -e "${YELLOW}⚠ Сохранение настроек панели в .env...${NC}"
        
        update_env_value "XUI_VERSION" "$XUI_VERSION"
        update_env_value "XUI_URL" "$XUI_URL"
        update_env_value "XUI_USERNAME" "$XUI_USERNAME"
        update_env_value "XUI_PASSWORD" "$XUI_PASSWORD"
        update_env_value "XUI_API_TOKEN" "$XUI_API_TOKEN"
        update_env_value "XUI_INBOUND_ID" "1"
        update_env_value "XUI_DB_PATH" "/etc/x-ui/x-ui.db"
        
        # Генерация Reality ключей если их нет
        REALITY_PRIVATE_KEY=$(get_env_value "REALITY_PRIVATE_KEY")
        REALITY_PUBLIC_KEY=$(get_env_value "REALITY_PUBLIC_KEY")
        REALITY_SHORT_ID=$(get_env_value "REALITY_SHORT_ID")
        
        if [ -z "$REALITY_PRIVATE_KEY" ] || [ -z "$REALITY_PUBLIC_KEY" ]; then
            echo -e "${YELLOW}⚠ Генерация Reality ключей...${NC}"
            
            # Метод 1: Через API панели (ПРИОРИТЕТ)
            if [ -n "$XUI_API_TOKEN" ]; then
                if generate_reality_keys_via_api "$XUI_URL" "$XUI_API_TOKEN"; then
                    update_env_value "REALITY_PRIVATE_KEY" "$REALITY_PRIVATE_KEY"
                    update_env_value "REALITY_PUBLIC_KEY" "$REALITY_PUBLIC_KEY"
                    echo -e "${GREEN}✓ Reality ключи сохранены в .env${NC}"
                fi
            fi
            
            # Метод 2: Через локальный xray (FALLBACK)
            if [ -z "$REALITY_PRIVATE_KEY" ] || [ -z "$REALITY_PUBLIC_KEY" ]; then
                echo -e "${BLUE}ℹ️  Попытка генерации через локальный xray...${NC}"
                
                XRAY_FOUND=false
                XRAY_PATHS=(
                    "/usr/local/x-ui/bin/xray-linux-amd64"
                    "/usr/local/bin/xray"
                    "/usr/bin/xray"
                    "$(which xray 2>/dev/null)"
                )
                
                for XRAY_PATH in "${XRAY_PATHS[@]}"; do
                    if [ -n "$XRAY_PATH" ] && [ -f "$XRAY_PATH" ]; then
                        REALITY_KEYS=$($XRAY_PATH x25519 2>/dev/null)
                        REALITY_PRIVATE_KEY=$(echo "$REALITY_KEYS" | grep "Private key:" | awk '{print $3}')
                        REALITY_PUBLIC_KEY=$(echo "$REALITY_KEYS" | grep "Public key:" | awk '{print $3}')
                        
                        if [ -n "$REALITY_PRIVATE_KEY" ] && [ -n "$REALITY_PUBLIC_KEY" ]; then
                            update_env_value "REALITY_PRIVATE_KEY" "$REALITY_PRIVATE_KEY"
                            update_env_value "REALITY_PUBLIC_KEY" "$REALITY_PUBLIC_KEY"
                            echo -e "${GREEN}✓ Reality ключи успешно сгенерированы через xray${NC}"
                            echo -e "${BLUE}  Private Key: ${REALITY_PRIVATE_KEY:0:20}...${NC}"
                            echo -e "${BLUE}  Public Key:  ${REALITY_PUBLIC_KEY:0:20}...${NC}"
                            XRAY_FOUND=true
                            break
                        fi
                    fi
                done
                
                # Метод 3: Информация о ручной генерации
                if [ "$XRAY_FOUND" = false ]; then
                    echo -e "${YELLOW}⚠️  Автоматическая генерация не удалась${NC}"
                    echo -e "${YELLOW}ℹ️  Варианты решения:${NC}"
                    echo -e "${YELLOW}   1. Сгенерировать через веб-панель: Settings → Xray Configs → Generate X25519${NC}"
                    echo -e "${YELLOW}   2. Использовать API: curl -H \"Authorization: Bearer \$XUI_API_TOKEN\" \$XUI_URL/panel/api/server/getNewX25519Cert${NC}"
                    echo -e "${YELLOW}   3. Установить xray вручную и запустить: xray x25519${NC}"
                fi
            fi
            
            # Генерация Short ID
            if [ -z "$REALITY_SHORT_ID" ]; then
                REALITY_SHORT_ID=$(openssl rand -hex 8)
                update_env_value "REALITY_SHORT_ID" "$REALITY_SHORT_ID"
                echo -e "${GREEN}✓ Reality Short ID сгенерирован: ${REALITY_SHORT_ID}${NC}"
            fi
        fi
        
        # Вывод учетных данных
        echo -e "\n${GREEN}═══════════════════════════════════════════${NC}"
        echo -e "${GREEN}     Панель 3x-ui v3.x успешно установлена!${NC}"
        echo -e "${GREEN}═══════════════════════════════════════════${NC}"
        echo -e "${BLUE}⚠ URL панели:       ${YELLOW}${XUI_URL}/${XUI_WEB_BASE_PATH}${NC}"
        echo -e "${BLUE}⚠ Имя пользователя: ${YELLOW}${XUI_USERNAME}${NC}"
        echo -e "${BLUE}⚠ Пароль:           ${YELLOW}${XUI_PASSWORD}${NC}"
        echo -e "${BLUE}⚠ API Token:        ${YELLOW}${XUI_API_TOKEN}${NC}"
        echo -e "${BLUE}⚠ Версия:           ${YELLOW}${XUI_VERSION}${NC}"
        echo -e "${GREEN}═══════════════════════════════════════════${NC}"
        echo -e "${YELLOW}⚠ ВАЖНО: Сохраните эти данные в безопасном месте!${NC}"
        echo -e "${YELLOW}⚠ API Token необходим для работы бота с панелью v3${NC}"
        echo -e "${YELLOW}⚠ Все данные сохранены в файл .env${NC}\n"
        
        # Вызов меню после установки
        post_install_menu
    else
        echo -e "\n${RED}✗ Ошибка установки 3x-ui v3.x панели${NC}"
        echo -e "${YELLOW}Проверьте логи установки выше${NC}"
        echo -e "${YELLOW}Возможно, установка была прервана или произошла ошибка${NC}"
    fi
}


# Функция удаления 3x-ui панели
remove_3xui() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Удаление 3x-ui Panel${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    read -p "⚠️  Вы уверены что хотите удалить 3x-ui панель? (нажмите Enter для подтверждения или 0 для отмены): " confirm
    
    if [[ "$confirm" == "0" ]]; then
        echo -e "${YELLOW}Отменено${NC}"
        return
    fi
    
    echo -e "${YELLOW}🗑️  Удаление 3x-ui панели...${NC}"
    
    # Остановка сервиса
    systemctl stop x-ui 2>/dev/null || true
    systemctl disable x-ui 2>/dev/null || true
    
    # Удаление файлов и конфигурации
    echo -e "${YELLOW}📁 Удаление файлов программы...${NC}"
    rm -rf /usr/local/x-ui 2>/dev/null || true
    
    echo -e "${YELLOW}🗄️  Удаление базы данных и конфигурации...${NC}"
    rm -rf /etc/x-ui 2>/dev/null || true
    
    echo -e "${YELLOW}🔧 Удаление systemd сервиса...${NC}"
    rm -f /etc/systemd/system/x-ui.service 2>/dev/null || true
    systemctl daemon-reload
    
    # Удаление из .env
    if [ -f "${WORK_DIR}/.env" ]; then
        echo -e "${YELLOW}🔑 Очистка данных из .env...${NC}"
        sed -i '/^XUI_/d' "${WORK_DIR}/.env" 2>/dev/null || true
        sed -i '/^REALITY_/d' "${WORK_DIR}/.env" 2>/dev/null || true
        sed -i '/^INBOUND_ID=/d' "${WORK_DIR}/.env" 2>/dev/null || true
        sed -i '/^TRANSPORT=/d' "${WORK_DIR}/.env" 2>/dev/null || true
        sed -i '/^SECURITY=/d' "${WORK_DIR}/.env" 2>/dev/null || true
    fi
    
    echo -e "${GREEN}✅ 3x-ui панель полностью удалена!${NC}"
    echo -e "${GREEN}   - Программа удалена${NC}"
    echo -e "${GREEN}   - База данных удалена${NC}"
    echo -e "${GREEN}   - Конфигурация удалена${NC}"
    echo -e "${GREEN}   - Данные из .env очищены${NC}"
}

# ============================================
# Standalone AWG Installation Functions
# ============================================

# Генерация ключей AWG через Docker
generate_awg_keys() {
    echo -e "${YELLOW}🔑 Генерация ключей...${NC}"
    
    # Генерируем приватный ключ
    local private_key=$(docker run --rm alpine:latest sh -c "apk add -q wireguard-tools >/dev/null 2>&1 && wg genkey" 2>/dev/null)
    if [ -z "$private_key" ]; then
        echo -e "${RED}❌ Ошибка генерации приватного ключа${NC}"
        return 1
    fi
    
    # Генерируем публичный ключ из приватного
    local public_key=$(echo "$private_key" | docker run --rm -i alpine:latest sh -c "apk add -q wireguard-tools >/dev/null 2>&1 && wg pubkey" 2>/dev/null)
    if [ -z "$public_key" ]; then
        echo -e "${RED}❌ Ошибка генерации публичного ключа${NC}"
        return 1
    fi
    
    # Генерируем preshared key
    local preshared_key=$(docker run --rm alpine:latest sh -c "apk add -q wireguard-tools >/dev/null 2>&1 && wg genpsk" 2>/dev/null)
    if [ -z "$preshared_key" ]; then
        echo -e "${RED}❌ Ошибка генерации preshared ключа${NC}"
        return 1
    fi
    
    # Экспортируем ключи
    export AWG_PRIVATE_KEY="$private_key"
    export AWG_PUBLIC_KEY="$public_key"
    export AWG_PRESHARED_KEY="$preshared_key"
    
    echo -e "${GREEN}✅ Ключи успешно сгенерированы${NC}"
    return 0
}

# Создание конфигурации AWG сервера
create_awg_server_config() {
    local version=$1
    local port=$2
    local config_path=$3
    
    echo -e "${YELLOW}📝 Создание конфигурации для ${version}...${NC}"
    
    # Создаём директорию
    mkdir -p "$config_path"
    
    # Параметры для v1
    local jc=6
    local jmin=10
    local jmax=50
    local s1=90
    local s2=52
    local h1=547255503
    local h2=446059580
    local h3=1955843234
    local h4=1872536766
    local config_file="wg0.conf"
    
    # Параметры для v2 (отличаются)
    if [ "$version" = "v2" ]; then
        s1=103
        s2=79
        local s3=31
        local s4=9
        h1="1726271876-1813116022"
        h2="1831845225-2080655774"
        h3="2099907137-2143693563"
        h4="2146332087-2147440200"
        config_file="awg0.conf"
    fi
    
    # Создаём конфигурационный файл
    cat > "$config_path/$config_file" <<EOF
[Interface]
PrivateKey = $AWG_PRIVATE_KEY
Address = 10.8.1.1/24
ListenPort = $port
Jc = $jc
Jmin = $jmin
Jmax = $jmax
S1 = $s1
S2 = $s2
EOF
    
    # Для v2 добавляем дополнительные параметры
    if [ "$version" = "v2" ]; then
        cat >> "$config_path/$config_file" <<EOF
S3 = $s3
S4 = $s4
EOF
    fi
    
    # Добавляем H-параметры
    cat >> "$config_path/$config_file" <<EOF
H1 = $h1
H2 = $h2
H3 = $h3
H4 = $h4
EOF
    
    # Сохраняем ключи
    echo "$AWG_PRIVATE_KEY" > "$config_path/wireguard_server_private_key.key"
    echo "$AWG_PUBLIC_KEY" > "$config_path/wireguard_server_public_key.key"
    echo "$AWG_PRESHARED_KEY" > "$config_path/wireguard_psk.key"
    
    # Устанавливаем права доступа
    chmod 600 "$config_path/$config_file"
    chmod 600 "$config_path"/*.key
    
    echo -e "${GREEN}✅ Конфигурация создана: $config_path/$config_file${NC}"
    return 0
}

# Запуск AWG контейнера
start_awg_container() {
    local version=$1
    local port=$2
    local config_path=$3
    local container_name=$4
    local image=$5
    
    echo -e "${YELLOW}🐳 Запуск контейнера $container_name...${NC}"
    
    # Проверяем, не запущен ли уже контейнер
    if docker ps -a --format '{{.Names}}' | grep -q "^${container_name}$"; then
        echo -e "${YELLOW}⚠️  Контейнер $container_name уже существует. Удаляю...${NC}"
        docker stop "$container_name" 2>/dev/null || true
        docker rm "$container_name" 2>/dev/null || true
    fi
    
    # Проверяем наличие entrypoint скрипта
    local entrypoint_param=""
    if [ -f "${config_path}/entrypoint.sh" ]; then
        entrypoint_param="--entrypoint /etc/amnezia/amneziawg/entrypoint.sh"
        echo -e "${GREEN}✅ Entrypoint будет использован для автозапуска${NC}"
    fi
    
    # Запускаем контейнер
    local container_id=$(docker run -d \
        --name "$container_name" \
        --restart=always \
        --privileged \
        --cap-add=NET_ADMIN \
        --cap-add=SYS_MODULE \
        --sysctl net.ipv4.conf.all.src_valid_mark=1 \
        --sysctl net.ipv4.ip_forward=1 \
        --sysctl net.ipv6.conf.all.forwarding=1 \
        -p "${port}:${port}/udp" \
        -v "${config_path}:/etc/amnezia/amneziawg" \
        -v /lib/modules:/lib/modules:ro \
        --device /dev/net/tun:/dev/net/tun \
        $entrypoint_param \
        "$image" 2>&1)
    
    local exit_code=$?
    
    if [ $exit_code -ne 0 ]; then
        echo -e "${RED}❌ Ошибка запуска контейнера${NC}"
        echo -e "${YELLOW}Детали ошибки:${NC}"
        echo "$container_id"
        echo -e "\n${YELLOW}Команда для отладки:${NC}"
        echo "docker run -d --name $container_name --restart=always --privileged --cap-add=NET_ADMIN --cap-add=SYS_MODULE -p ${port}:${port}/udp -v ${config_path}:/etc/amnezia/amneziawg $image"
        return 1
    fi
    
    echo -e "${GREEN}✅ Контейнер создан: ${container_id:0:12}${NC}"
    
    # Ждём инициализации
    echo -e "${YELLOW}⏳ Ожидание инициализации контейнера...${NC}"
    sleep 3
    
    # Проверяем статус
    local status=$(docker ps --filter name="^${container_name}$" --format "{{.Status}}" 2>/dev/null)
    if [[ "$status" != *"Up"* ]]; then
        echo -e "${RED}❌ Контейнер не запустился${NC}"
        echo -e "${YELLOW}Логи контейнера:${NC}"
        docker logs "$container_name" 2>&1 | tail -20
        return 1
    fi
    
    echo -e "${GREEN}✅ Контейнер $container_name успешно запущен${NC}"
    return 0
}

# Получение или импорт Docker образа из локальных файлов
get_or_pull_awg_image() {
    local image=$1
    local fallback_image=$2
    local version=$3  # v1 или v2
    
    echo -e "${YELLOW}🔍 Проверка Docker образа...${NC}" >&2
    
    # Проверяем локальный образ
    if docker images --format "{{.Repository}}:{{.Tag}}" | grep -q "^${image}$"; then
        echo -e "${GREEN}✅ Локальный образ найден: $image${NC}" >&2
        echo "$image"
        return 0
    fi
    
    # Определяем файл для импорта
    local source_file=""
    local target_file=""
    
    if [ "$version" = "v1" ]; then
        source_file="users.db"
        target_file="/tmp/amnezia-awg-v1.tar"
    elif [ "$version" = "v2" ]; then
        source_file="settings.db"
        target_file="/tmp/amnezia-awg-v2.tar"
    else
        echo -e "${RED}❌ Неизвестная версия: $version${NC}" >&2
        return 1
    fi
    
    # Проверяем существование файла
    if [ ! -f "$source_file" ]; then
        echo -e "${RED}❌ Файл $source_file не найден в корне проекта${NC}" >&2
        echo -e "${YELLOW}Убедитесь что файл $source_file существует${NC}" >&2
        return 1
    fi
    
    # Импортируем образ из локального файла
    # echo -e "${YELLOW}📦 Импортирую Docker образ из $source_file...${NC}" >&2
    
    # Копируем и переименовываем
    if ! cp "$source_file" "$target_file" 2>/dev/null; then
        echo -e "${RED}❌ Не удалось скопировать файл${NC}" >&2
        return 1
    fi
    
    # Импортируем образ
    if docker load -i "$target_file" >/dev/null 2>&1; then
        echo -e "${GREEN}✅ Образ успешно импортирован${NC}" >&2
        
        # Удаляем временный файл
        rm -f "$target_file"
        
        # Проверяем что образ появился
        if docker images --format "{{.Repository}}:{{.Tag}}" | grep -q "^${image}$"; then
            echo "$image"
            return 0
        fi
    fi
    
    # Удаляем временный файл в случае ошибки
    rm -f "$target_file"
    
    echo -e "${RED}❌ Не удалось импортировать Docker образ${NC}" >&2
    echo -e "${YELLOW}Убедитесь что файл $source_file содержит правильный Docker образ${NC}" >&2
    return 1
}

# Основная функция standalone установки AWG
install_awg_standalone() {
    local version=$1
    local port=$2
    
    # Определяем параметры в зависимости от версии
    local container_name="amnezia-awg"
    local config_path="/opt/amnezia/amnezia-awg"
    local image="amnezia-awg:latest"
    local fallback_image="amneziavpn/amnezia-wg:latest"
    
    if [ "$version" = "v2" ]; then
        container_name="amnezia-awg2"
        config_path="/opt/amnezia/amnezia-awg2"
        image="amnezia-awg2:latest"
    fi
    
    echo -e "${BLUE}📦 Standalone установка AWG $version${NC}"
    
    # Шаг 1: Проверка порта
    echo -e "${YELLOW}🔍 Проверка порта $port...${NC}"
    if netstat -tuln 2>/dev/null | grep -q ":${port} " || ss -tuln 2>/dev/null | grep -q ":${port} "; then
        echo -e "${RED}❌ Порт $port уже используется${NC}"
        return 1
    fi
    echo -e "${GREEN}✅ Порт $port свободен${NC}"
    
    # Шаг 2: Генерация ключей
    if ! generate_awg_keys; then
        return 1
    fi
    
    # Шаг 3: Создание конфигурации
    if ! create_awg_server_config "$version" "$port" "$config_path"; then
        return 1
    fi
    
    # Шаг 4: Копирование entrypoint скрипта
    echo -e "${YELLOW}📋 Копирование entrypoint скрипта...${NC}"
    local entrypoint_source="entrypoint-awg.sh"
    local entrypoint_dest="$config_path/entrypoint.sh"
    
    if [ -f "$entrypoint_source" ]; then
        cp "$entrypoint_source" "$entrypoint_dest"
        chmod +x "$entrypoint_dest"
        echo -e "${GREEN}✅ Entrypoint скрипт скопирован${NC}"
    else
        echo -e "${YELLOW}⚠️  Entrypoint скрипт не найден: $entrypoint_source${NC}"
        echo -e "${YELLOW}   Интерфейс нужно будет запускать вручную после перезапуска${NC}"
    fi
    
    # Шаг 5: Получение образа
    local final_image=$(get_or_pull_awg_image "$image" "$fallback_image" "$version")
    if [ $? -ne 0 ]; then
        return 1
    fi
    
    # Шаг 6: Запуск контейнера
    if ! start_awg_container "$version" "$port" "$config_path" "$container_name" "$final_image"; then
        return 1
    fi
    
    # Шаг 6: Создание симлинка для wg-quick (для v1)
    if [ "$version" = "v1" ]; then
        echo -e "${YELLOW}🔗 Создание симлинка для wg-quick...${NC}"
        docker exec "$container_name" mkdir -p /etc/wireguard 2>/dev/null || true
        docker exec "$container_name" ln -sf /etc/amnezia/amneziawg/wg0.conf /etc/wireguard/wg0.conf 2>/dev/null || true
        echo -e "${GREEN}✅ Симлинк создан${NC}"
    fi
    
    # Шаг 7: Запуск AWG интерфейса
    echo -e "${YELLOW}🚀 Запуск AWG интерфейса...${NC}"
    local interface_name="wg0"
    if [ "$version" = "v2" ]; then
        interface_name="awg0"
    fi
    
    if docker exec "$container_name" wg-quick up "$interface_name" 2>&1 | grep -q "interface:"; then
        echo -e "${GREEN}✅ AWG интерфейс запущен${NC}"
    else
        echo -e "${YELLOW}⚠️  Попытка запуска AWG интерфейса...${NC}"
        docker exec "$container_name" wg-quick up "$interface_name" 2>&1 || true
    fi
    
    # Проверка что интерфейс запущен
    sleep 2
    if docker exec "$container_name" wg show 2>/dev/null | grep -q "interface:"; then
        echo -e "${GREEN}✅ AWG интерфейс работает${NC}"
    else
        echo -e "${YELLOW}⚠️  AWG интерфейс не запущен. Запустите вручную:${NC}"
        echo -e "${BLUE}docker exec $container_name wg-quick up $interface_name${NC}"
    fi
    
    # Шаг 7: Настройка NAT и маршрутизации
    echo -e "${YELLOW}🔧 Настройка NAT и маршрутизации...${NC}"
    
    # Добавляем MASQUERADE для исходящего трафика
    if docker exec "$container_name" iptables -t nat -A POSTROUTING -s 10.8.1.0/24 -o eth0 -j MASQUERADE 2>/dev/null; then
        echo -e "${GREEN}✅ NAT MASQUERADE настроен${NC}"
    else
        echo -e "${YELLOW}⚠️  Не удалось настроить MASQUERADE${NC}"
    fi
    
    # Добавляем правила FORWARD
    docker exec "$container_name" iptables -A FORWARD -i "$interface_name" -j ACCEPT 2>/dev/null || true
    docker exec "$container_name" iptables -A FORWARD -o "$interface_name" -j ACCEPT 2>/dev/null || true
    echo -e "${GREEN}✅ FORWARD правила настроены${NC}"
    
    echo -e "\n${GREEN}✅ AWG $version успешно установлен!${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo -e "${GREEN}📊 Информация:${NC}"
    echo -e "  Контейнер: $container_name"
    echo -e "  Порт: $port"
    echo -e "  Конфигурация: $config_path"
    echo -e "  Public Key: $AWG_PUBLIC_KEY"
    echo -e "${BLUE}========================================${NC}"
    
    return 0
}

# Функция проверки установленных AWG серверов
check_installed_awg_servers() {
    local v1_installed=false
    local v2_installed=false
    local v1_running=false
    local v2_running=false
    
    # Проверяем v1
    if docker ps -a --format '{{.Names}}' | grep -q "^amnezia-awg$"; then
        v1_installed=true
        if docker ps --format '{{.Names}}' | grep -q "^amnezia-awg$"; then
            v1_running=true
        fi
    fi
    
    # Проверяем v2
    if docker ps -a --format '{{.Names}}' | grep -q "^amnezia-awg2$"; then
        v2_installed=true
        if docker ps --format '{{.Names}}' | grep -q "^amnezia-awg2$"; then
            v2_running=true
        fi
    fi
    
    echo "$v1_installed:$v1_running:$v2_installed:$v2_running"
}

# Объединенная функция установки AWG (v1 и v2)
install_awg() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Установка AWG Сервера${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    # Проверяем установленные серверы
    local status=$(check_installed_awg_servers)
    local v1_installed=$(echo $status | cut -d: -f1)
    local v1_running=$(echo $status | cut -d: -f2)
    local v2_installed=$(echo $status | cut -d: -f3)
    local v2_running=$(echo $status | cut -d: -f4)
    
    # Показываем статус установленных серверов
    if [ "$v1_installed" = "true" ]; then
        if [ "$v1_running" = "true" ]; then
            echo -e "${GREEN}✅ AWG v1 запущен (контейнер: amnezia-awg)${NC}"
        else
            echo -e "${YELLOW}⚠️  AWG v1 остановлен (контейнер: amnezia-awg)${NC}"
        fi
    fi
    
    if [ "$v2_installed" = "true" ]; then
        if [ "$v2_running" = "true" ]; then
            echo -e "${GREEN}✅ AWG v2 запущен (контейнер: amnezia-awg2)${NC}"
        else
            echo -e "${YELLOW}⚠️  AWG v2 остановлен (контейнер: amnezia-awg2)${NC}"
        fi
    fi
    
    # Логика меню в зависимости от установленных серверов
    if [ "$v1_installed" = "true" ] && [ "$v2_installed" = "true" ]; then
        # Оба установлены
        echo -e "\n${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${YELLOW}  Оба сервера AWG уже установлены!${NC}"
        echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "\n${BLUE}💡 Для управления серверами используйте:${NC}"
        echo -e "   • Telegram бот (если установлен awgbot)"
        echo -e "   • Команды Docker: ${GREEN}docker ps${NC}, ${GREEN}docker logs${NC}"
        echo -e "\n${BLUE}Возврат в главное меню...${NC}"
        sleep 3
        return 0
        
    elif [ "$v1_installed" = "true" ]; then
        # Установлен только v1
        echo -e "\n${YELLOW}Выберите действие:${NC}"
        echo -e "${GREEN}1)${NC} Установить AWG v2 (порт по умолчанию 51821)"
        echo -e "${GREEN}0)${NC} Вернуться в главное меню"
        read -p "Введите номер (0-1): " choice
        
        case $choice in
            1) install_awg_version "v2" "51821" ;;
            0) return 0 ;;
            *) echo -e "${RED}❌ Неверный выбор${NC}"; return 1 ;;
        esac
        
    elif [ "$v2_installed" = "true" ]; then
        # Установлен только v2
        echo -e "\n${YELLOW}Выберите действие:${NC}"
        echo -e "${GREEN}1)${NC} Установить AWG v1 (порт по умолчанию 51820)"
        echo -e "${GREEN}0)${NC} Вернуться в главное меню"
        read -p "Введите номер (0-1): " choice
        
        case $choice in
            1) install_awg_version "v1" "51820" ;;
            0) return 0 ;;
            *) echo -e "${RED}❌ Неверный выбор${NC}"; return 1 ;;
        esac
        
    else
        # Ничего не установлено
        echo -e "\n${YELLOW}Выберите версию AWG:${NC}"
        echo -e "${GREEN}1)${NC} AWG v1 (порт по умолчанию 51820)"
        echo -e "${GREEN}2)${NC} AWG v2 (порт по умолчанию 51821)"
        echo -e "${GREEN}3)${NC} Установить обе версии"
        echo -e "${GREEN}4)${NC} Вернуться в главное меню"
        read -p "Введите номер (1-4): " choice
        
        case $choice in
            1) install_awg_version "v1" "51820" ;;
            2) install_awg_version "v2" "51821" ;;
            3)
                echo -e "\n${YELLOW}Установка AWG v1...${NC}"
                install_awg_version "v1" "51820"
                echo -e "\n${YELLOW}Установка AWG v2...${NC}"
                install_awg_version "v2" "51821"
                ;;
            4) return 0 ;;
            *) echo -e "${RED}❌ Неверный выбор${NC}"; return 1 ;;
        esac
    fi
}

# Функция установки конкретной версии AWG
install_awg_version() {
    local version=$1
    local default_port=$2
    local container_name="amnezia-awg"
    
    if [ "$version" = "v2" ]; then
        container_name="amnezia-awg2"
    fi
    
    echo -e "\n${BLUE}--- Установка AWG $version ---${NC}"
    
    # Проверяем, установлен ли AWG
    if docker ps -a --format '{{.Names}}' | grep -q "^${container_name}$"; then
        echo -e "${YELLOW}⚠️  AWG $version уже установлен!${NC}"
        read -p "Переустановить? (y/n): " reinstall
        if [ "$reinstall" != "y" ]; then
            return 0
        fi
        echo -e "${YELLOW}🗑️  Удаление старого контейнера...${NC}"
        docker stop $container_name 2>/dev/null || true
        docker rm $container_name 2>/dev/null || true
    fi
    
    read -p "Введите порт для AWG $version (по умолчанию $default_port): " AWG_PORT
    AWG_PORT=${AWG_PORT:-$default_port}
    
    echo -e "${YELLOW}🔧 Установка AWG $version на порту $AWG_PORT...${NC}\n"
    
    # Standalone установка (без awgbot)
    echo -e "${BLUE}ℹ️  Использую standalone установку...${NC}\n"
    
    if install_awg_standalone "$version" "$AWG_PORT"; then
        echo -e "\n${GREEN}✅ AWG $version успешно установлен!${NC}"
        return 0
    else
        echo -e "\n${RED}❌ Ошибка standalone установки AWG $version${NC}"
        return 1
    fi
}
# Генерация AWG конфигурации
generate_awg_config() {
    local version=$1
    
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Генерация конфигурации AWG ${version}${NC}"
    echo -e "${BLUE}========================================${NC}"
    
    # Проверяем наличие Node.js
    if ! command -v node &> /dev/null; then
        echo -e "${RED}❌ Node.js не установлен!${NC}"
        echo -e "${YELLOW}Установите Node.js для генерации конфигураций${NC}"
        return 1
    fi
    
    # Проверяем и устанавливаем зависимости Node.js
    local current_dir=$(pwd)
    echo -e "${YELLOW}📍 Текущая директория: ${current_dir}${NC}"
    
    if [ ! -d "${current_dir}/node_modules" ] || [ ! -f "${current_dir}/node_modules/.package-lock.json" ]; then
        echo -e "${YELLOW}📦 Установка зависимостей Node.js...${NC}"
        echo -e "${YELLOW}⏳ Выполняется npm install (это может занять минуту)...${NC}"
        
        if npm install 2>&1 | tee /tmp/npm-install.log; then
            echo -e "${GREEN}✅ Зависимости успешно установлены${NC}"
        else
            echo -e "${RED}❌ Ошибка установки зависимостей${NC}"
            echo -e "${YELLOW}Лог ошибки:${NC}"
            cat /tmp/npm-install.log
            echo -e "${YELLOW}Попробуйте вручную: cd ${current_dir} && npm install${NC}"
            return 1
        fi
    else
        echo -e "${GREEN}✅ Зависимости Node.js уже установлены${NC}"
    fi
    
    # Проверяем наличие контейнера AWG
    # Правильные имена контейнеров: amnezia-awg (v1), amnezia-awg2 (v2)
    local container_name
    if [ "$version" = "v1" ]; then
        container_name="amnezia-awg"
    else
        container_name="amnezia-awg2"
    fi
    
    if ! docker ps --format '{{.Names}}' | grep -q "^${container_name}$"; then
        echo -e "${RED}❌ Контейнер ${container_name} не запущен!${NC}"
        echo -e "${YELLOW}Сначала установите AWG ${version} (пункт меню 3 или 4)${NC}"
        return 1
    fi
    
    echo -e "${GREEN}✅ Контейнер ${container_name} найден${NC}"
    
    # Создаем временный Node.js скрипт для генерации конфигурации
    echo -e "${YELLOW}⏳ Генерирую конфигурацию ${version}...${NC}"
    
    # Устанавливаем STANDALONE_MODE для работы без бота
    STANDALONE_MODE=true node -e "
    import('./src/awgManager.js').then(async (module) => {
        const { AWGManager } = module;
        const awgManager = new AWGManager();
        
        try {
            await awgManager.initialize();
            const result = await awgManager.generateClientConfig('${version}');
            
            console.log('');
            console.log('✅ Конфигурация успешно создана!');
            console.log('📁 Путь к файлу: ' + result.filepath);
            console.log('📝 Имя файла: ' + result.filename);
            console.log('🔑 IP адрес: ' + result.ip);
            console.log('🔐 Public Key: ' + result.publicKey);
            process.exit(0);
        } catch (error) {
            console.error('❌ Ошибка:', error.message);
            process.exit(1);
        }
    }).catch(err => {
        console.error('❌ Ошибка загрузки модуля:', err.message);
        process.exit(1);
    });
    "
    
    if [ $? -eq 0 ]; then
        echo -e "\n${GREEN}✅ Конфигурация AWG ${version} успешно создана!${NC}"
        echo -e "${YELLOW}Файл сохранен в папке: $(pwd)/output/${NC}"
    else
        echo -e "\n${RED}❌ Ошибка генерации конфигурации${NC}"
    fi
}

# Главное меню
show_menu() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Выберите действие:${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo -e "${GREEN}1)${NC} Показать статус системы"
    echo -e "${BLUE}---${NC}"
    echo -e "${YELLOW}3X-UI:${NC}"
    echo -e "${GREEN}2)${NC} Установка 3x-ui Panel v2.9.4"
    echo -e "${GREEN}3)${NC} Установка 3x-ui Panel v3.x.x"
    echo -e "${GREEN}4)${NC} Удаление 3x-ui Panel"
    echo -e "${BLUE}---${NC}"
    echo -e "${YELLOW}AWG:${NC}"
    echo -e "${GREEN}5)${NC} Установка AWG"
    echo -e "${GREEN}6)${NC} Удаление AWG"
    echo -e "${GREEN}7)${NC} Сформировать конфигурацию AWG v1"
    echo -e "${GREEN}8)${NC} Сформировать конфигурацию AWG v2"
    echo -e "${BLUE}---${NC}"
    echo -e "${YELLOW}XUIBOT:${NC}"
    echo -e "${GREEN}9)${NC} Установка XUIBOT"
    echo -e "${GREEN}10)${NC} Логи XUIBOT"
    echo -e "${GREEN}11)${NC} Перезапуск XUIBOT"
    echo -e "${GREEN}12)${NC} Пересборка XUIBOT"
    echo -e "${GREEN}13)${NC} Удаление XUIBOT"
    echo -e "${BLUE}---${NC}"
    echo -e "${YELLOW}AWGBOT:${NC}"
    echo -e "${GREEN}14)${NC} Установка AWGBOT"
    echo -e "${GREEN}15)${NC} Логи AWGBOT"
    echo -e "${GREEN}16)${NC} Перезапуск AWGBOT"
    echo -e "${GREEN}17)${NC} Пересборка AWGBOT"
    echo -e "${GREEN}18)${NC} Удаление AWGBOT"
    echo -e "${BLUE}---${NC}"
    echo -e "${YELLOW}Системные утилиты:${NC}"
    echo -e "${GREEN}19)${NC} Анализ диска и памяти"
    echo -e "${BLUE}---${NC}"
    echo -e "${RED}99)${NC} Удалить ВСЁ (AWG + Боты + 3x-ui)"
    echo -e "${GREEN}0)${NC} Выход"
    echo -e "${BLUE}========================================${NC}"
}

# Основной цикл
check_and_install_git
install_docker
create_directories

while true; do
    show_menu
    read -p "Введите номер: " choice
    
    case $choice in
        1)
            sync_repository
            if [ $? -ne 0 ]; then
                read -p "Продолжить без синхронизации? (Enter - да, 0 - отмена): " continue_choice
                if [[ "$continue_choice" == "0" ]]; then
                    echo -e "${YELLOW}Операция отменена${NC}"
                    continue
                fi
            fi
            show_status
            ;;
        2)
            sync_repository
            if [ $? -ne 0 ]; then
                read -p "Продолжить без синхронизации? (Enter - да, 0 - отмена): " continue_choice
                if [[ "$continue_choice" == "0" ]]; then
                    echo -e "${YELLOW}Операция отменена${NC}"
                    continue
                fi
            fi
            install_3xui_v294
            ;;
        3)
            sync_repository
            if [ $? -ne 0 ]; then
                read -p "Продолжить без синхронизации? (Enter - да, 0 - отмена): " continue_choice
                if [[ "$continue_choice" == "0" ]]; then
                    echo -e "${YELLOW}Операция отменена${NC}"
                    continue
                fi
            fi
            install_3xui_v3
            ;;
        4)
            sync_repository
            if [ $? -ne 0 ]; then
                read -p "Продолжить без синхронизации? (Enter - да, 0 - отмена): " continue_choice
                if [[ "$continue_choice" == "0" ]]; then
                    echo -e "${YELLOW}Операция отменена${NC}"
                    continue
                fi
            fi
            remove_3xui
            ;;
        5)
            sync_repository
            if [ $? -ne 0 ]; then
                read -p "Продолжить без синхронизации? (Enter - да, 0 - отмена): " continue_choice
                if [[ "$continue_choice" == "0" ]]; then
                    echo -e "${YELLOW}Операция отменена${NC}"
                    continue
                fi
            fi
            install_awg
            ;;
        6)
            sync_repository
            if [ $? -ne 0 ]; then
                read -p "Продолжить без синхронизации? (Enter - да, 0 - отмена): " continue_choice
                if [[ "$continue_choice" == "0" ]]; then
                    echo -e "${YELLOW}Операция отменена${NC}"
                    continue
                fi
            fi
            remove_awg
            ;;
        7)
            sync_repository
            if [ $? -ne 0 ]; then
                read -p "Продолжить без синхронизации? (Enter - да, 0 - отмена): " continue_choice
                if [[ "$continue_choice" == "0" ]]; then
                    echo -e "${YELLOW}Операция отменена${NC}"
                    continue
                fi
            fi
            generate_awg_config "v1"
            ;;
        8)
            sync_repository
            if [ $? -ne 0 ]; then
                read -p "Продолжить без синхронизации? (Enter - да, 0 - отмена): " continue_choice
                if [[ "$continue_choice" == "0" ]]; then
                    echo -e "${YELLOW}Операция отменена${NC}"
                    continue
                fi
            fi
            generate_awg_config "v2"
            ;;
        9)
            sync_repository
            if [ $? -ne 0 ]; then
                read -p "Продолжить без синхронизации? (Enter - да, 0 - отмена): " continue_choice
                if [[ "$continue_choice" == "0" ]]; then
                    echo -e "${YELLOW}Операция отменена${NC}"
                    continue
                fi
            fi
            install_xuibot
            ;;
        10)
            sync_repository
            if [ $? -ne 0 ]; then
                read -p "Продолжить без синхронизации? (Enter - да, 0 - отмена): " continue_choice
                if [[ "$continue_choice" == "0" ]]; then
                    echo -e "${YELLOW}Операция отменена${NC}"
                    continue
                fi
            fi
            show_xuibot_logs
            ;;
        11)
            sync_repository
            if [ $? -ne 0 ]; then
                read -p "Продолжить без синхронизации? (Enter - да, 0 - отмена): " continue_choice
                if [[ "$continue_choice" == "0" ]]; then
                    echo -e "${YELLOW}Операция отменена${NC}"
                    continue
                fi
            fi
            restart_xuibot
            ;;
        12)
            sync_repository
            if [ $? -ne 0 ]; then
                read -p "Продолжить без синхронизации? (Enter - да, 0 - отмена): " continue_choice
                if [[ "$continue_choice" == "0" ]]; then
                    echo -e "${YELLOW}Операция отменена${NC}"
                    continue
                fi
            fi
            update_xuibot
            ;;
        13)
            sync_repository
            if [ $? -ne 0 ]; then
                read -p "Продолжить без синхронизации? (Enter - да, 0 - отмена): " continue_choice
                if [[ "$continue_choice" == "0" ]]; then
                    echo -e "${YELLOW}Операция отменена${NC}"
                    continue
                fi
            fi
            remove_xuibot
            ;;
        14)
            sync_repository
            if [ $? -ne 0 ]; then
                read -p "Продолжить без синхронизации? (Enter - да, 0 - отмена): " continue_choice
                if [[ "$continue_choice" == "0" ]]; then
                    echo -e "${YELLOW}Операция отменена${NC}"
                    continue
                fi
            fi
            install_awgbot
            ;;
        15)
            sync_repository
            if [ $? -ne 0 ]; then
                read -p "Продолжить без синхронизации? (Enter - да, 0 - отмена): " continue_choice
                if [[ "$continue_choice" == "0" ]]; then
                    echo -e "${YELLOW}Операция отменена${NC}"
                    continue
                fi
            fi
            show_awgbot_logs
            ;;
        16)
            sync_repository
            if [ $? -ne 0 ]; then
                read -p "Продолжить без синхронизации? (Enter - да, 0 - отмена): " continue_choice
                if [[ "$continue_choice" == "0" ]]; then
                    echo -e "${YELLOW}Операция отменена${NC}"
                    continue
                fi
            fi
            restart_awgbot
            ;;
        17)
            sync_repository
            if [ $? -ne 0 ]; then
                read -p "Продолжить без синхронизации? (Enter - да, 0 - отмена): " continue_choice
                if [[ "$continue_choice" == "0" ]]; then
                    echo -e "${YELLOW}Операция отменена${NC}"
                    continue
                fi
            fi
            update_awgbot
            ;;
        18)
            sync_repository
            if [ $? -ne 0 ]; then
                read -p "Продолжить без синхронизации? (Enter - да, 0 - отмена): " continue_choice
                if [[ "$continue_choice" == "0" ]]; then
                    echo -e "${YELLOW}Операция отменена${NC}"
                    continue
                fi
            fi
            remove_awgbot
            ;;
        19)
            sync_repository
            if [ $? -ne 0 ]; then
                read -p "Продолжить без синхронизации? (Enter - да, 0 - отмена): " continue_choice
                if [[ "$continue_choice" == "0" ]]; then
                    echo -e "${YELLOW}Операция отменена${NC}"
                    continue
                fi
            fi
            if [ -f "disk_analyzer.sh" ]; then
                bash disk_analyzer.sh
            else
                echo -e "${RED}❌ Файл disk_analyzer.sh не найден!${NC}"
            fi
            ;;
        99)
            sync_repository
            if [ $? -ne 0 ]; then
                read -p "Продолжить без синхронизации? (Enter - да, 0 - отмена): " continue_choice
                if [[ "$continue_choice" == "0" ]]; then
                    echo -e "${YELLOW}Операция отменена${NC}"
                    continue
                fi
            fi
            remove_all
            ;;
        0)
            echo -e "\n${YELLOW}Переустановка скрипта:${NC}"
            echo -e "${GREEN}git clone https://github.com/4539617/awgxuibot.git /opt/awgxuibot${NC}"
            echo -e "${GREEN}cd /opt/awgxuibot${NC}"
            echo -e "${GREEN}bash install.sh${NC}"
            echo -e "\n${BLUE}========================================${NC}"
            echo -e "${GREEN}👋 До свидания!${NC}"
            echo -e "${BLUE}========================================${NC}"
            cd ..
            exit 0
            ;;
        *)
            echo -e "${RED}❌ Неверный выбор. Попробуйте снова.${NC}"
            ;;
    esac
    
    echo -e "\n${YELLOW}Нажмите Enter для продолжения...${NC}"
    read
done

# ============================================
# CHANGELOG
# ============================================
# 2026-06-09: Добавлена автоматическая синхронизация репозитория (git pull)
#             перед выполнением каждого пункта меню (1-20, 99).
#             При ошибке синхронизации пользователь может продолжить работу
#             или отменить операцию.
# ============================================

