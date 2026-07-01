#!/bin/bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Константы
WORK_DIR="/opt/awgxuibot"
DEFAULT_REALITY_SNI="www.nvidia.com"
DEFAULT_REALITY_FINGERPRINT="edge"  # Варианты: edge, chrome, firefox, safari


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
        echo -e "${GREEN}✅ Docker установлен и добавлен в автозагрузку${NC}"
    else
        echo -e "${GREEN}✅ Docker уже установлен${NC}"
        
        # Проверяем и включаем автозагрузку если не включена
        if ! systemctl is-enabled docker &>/dev/null; then
            echo -e "${YELLOW}🔄 Включение Docker в автозагрузку...${NC}"
            systemctl enable docker
            echo -e "${GREEN}✅ Docker добавлен в автозагрузку${NC}"
        fi
        
        # Проверяем запущен ли Docker
        if ! systemctl is-active --quiet docker; then
            echo -e "${YELLOW}🚀 Запуск Docker...${NC}"
            systemctl start docker
        fi
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

# Функция установки Node.js
install_nodejs() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Установка Node.js${NC}"
    echo -e "${BLUE}========================================${NC}"
    
    # Проверяем, установлен ли Node.js
    if command -v node &> /dev/null; then
        NODE_VERSION=$(node --version)
        echo -e "${GREEN}✅ Node.js уже установлен: ${NODE_VERSION}${NC}"
        
        # Проверяем npm
        if command -v npm &> /dev/null; then
            NPM_VERSION=$(npm --version)
            echo -e "${GREEN}✅ npm установлен: v${NPM_VERSION}${NC}"
        else
            echo -e "${YELLOW}⚠️  npm не найден, переустановка Node.js...${NC}"
        fi
        
        # Если всё в порядке, выходим
        if command -v npm &> /dev/null; then
            return 0
        fi
    fi
    
    echo -e "${YELLOW}📦 Установка Node.js LTS...${NC}"
    
    # Определяем систему
    if command -v apt-get &> /dev/null; then
        # Debian/Ubuntu
        echo -e "${YELLOW}🔍 Обнаружена система на базе Debian/Ubuntu${NC}"
        
        # Устанавливаем curl если не установлен
        if ! command -v curl &> /dev/null; then
            echo -e "${YELLOW}📦 Установка curl...${NC}"
            apt-get update -qq && apt-get install -y curl -qq
        fi
        
        # Добавляем репозиторий NodeSource для Node.js 20.x LTS
        echo -e "${YELLOW}📦 Добавление репозитория NodeSource...${NC}"
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
        
        # Устанавливаем Node.js
        echo -e "${YELLOW}📦 Установка Node.js...${NC}"
        apt-get install -y nodejs
        
    elif command -v yum &> /dev/null; then
        # CentOS/RHEL
        echo -e "${YELLOW}🔍 Обнаружена система на базе CentOS/RHEL${NC}"
        
        # Добавляем репозиторий NodeSource
        curl -fsSL https://rpm.nodesource.com/setup_20.x | bash -
        
        # Устанавливаем Node.js
        yum install -y nodejs
        
    elif command -v dnf &> /dev/null; then
        # Fedora
        echo -e "${YELLOW}🔍 Обнаружена система Fedora${NC}"
        
        # Добавляем репозиторий NodeSource
        curl -fsSL https://rpm.nodesource.com/setup_20.x | bash -
        
        # Устанавливаем Node.js
        dnf install -y nodejs
        
    else
        echo -e "${RED}❌ Неподдерживаемая система${NC}"
        echo -e "${YELLOW}Установите Node.js вручную:${NC}"
        echo -e "${BLUE}  https://nodejs.org/en/download/${NC}"
        return 1
    fi
    
    # Проверяем установку
    if command -v node &> /dev/null && command -v npm &> /dev/null; then
        NODE_VERSION=$(node --version)
        NPM_VERSION=$(npm --version)
        echo -e "${GREEN}✅ Node.js успешно установлен: ${NODE_VERSION}${NC}"
        echo -e "${GREEN}✅ npm установлен: v${NPM_VERSION}${NC}"
        
        # Устанавливаем зависимости проекта если находимся в рабочей директории
        if [ -f "package.json" ]; then
            echo -e "${YELLOW}📦 Установка зависимостей проекта...${NC}"
            npm install
            echo -e "${GREEN}✅ Зависимости проекта установлены${NC}"
        fi
        
        return 0
    else
        echo -e "${RED}❌ Не удалось установить Node.js${NC}"
        return 1
    fi
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

# Функция создания config.yaml если не существует
create_config_if_not_exists() {
    if [ ! -f "config.yaml" ]; then
        echo -e "${YELLOW}📝 Создание config.yaml из примера...${NC}"
        
        if [ ! -f "config.yaml.example" ]; then
            echo -e "${RED}❌ config.yaml.example не найден${NC}"
            return 1
        fi
        
        # Копируем пример
        cp config.yaml.example config.yaml
        
        # Получаем IP сервера
        SERVER_IP=$(curl -s -4 https://api4.ipify.org 2>/dev/null || curl -s -4 https://ipv4.icanhazip.com 2>/dev/null || curl -s -4 ifconfig.me 2>/dev/null || echo "")
        
        # Обновляем server_address и server_ip в локальной панели
        check_yq || return 1
        
        local panel_id=$(get_local_panel_id)
        if [ -n "$panel_id" ]; then
            yq eval -i ".panels.${panel_id}.server_address = \"${SERVER_IP}\"" config.yaml
            yq eval -i ".panels.${panel_id}.server_ip = \"${SERVER_IP}\"" config.yaml
            echo -e "${GREEN}✅ config.yaml создан с IP сервера: ${SERVER_IP}${NC}"
        else
            echo -e "${GREEN}✅ config.yaml создан из примера${NC}"
        fi
    fi
}


# ============================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С CONFIG.YAML
# ============================================

# Проверка наличия yq (YAML processor)
check_yq() {
    if ! command -v yq &> /dev/null; then
        echo -e "${YELLOW}📦 Установка yq (YAML processor)...${NC}"
        
        # Определяем архитектуру
        ARCH=$(uname -m)
        case $ARCH in
            x86_64)
                YQ_ARCH="amd64"
                ;;
            aarch64|arm64)
                YQ_ARCH="arm64"
                ;;
            armv7l)
                YQ_ARCH="arm"
                ;;
            *)
                echo -e "${RED}❌ Неподдерживаемая архитектура: $ARCH${NC}"
                return 1
                ;;
        esac
        
        # Скачиваем и устанавливаем yq
        YQ_VERSION="v4.35.1"
        wget -q "https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_${YQ_ARCH}" -O /usr/local/bin/yq
        chmod +x /usr/local/bin/yq
        
        if command -v yq &> /dev/null; then
            echo -e "${GREEN}✅ yq установлен${NC}"
        else
            echo -e "${RED}❌ Не удалось установить yq${NC}"
            return 1
        fi
    fi
    return 0
}

# Получить ID локальной панели из config.yaml
get_local_panel_id() {
    if [ ! -f "config.yaml" ]; then
        echo ""
        return 1
    fi
    
    check_yq || return 1
    
    # Ищем панель с is_local: true
    local panel_id=$(yq eval '.panels | to_entries | .[] | select(.value.is_local == true) | .key' config.yaml 2>/dev/null | head -1)
    
    # ВАЖНО: НЕ используем default_panel как fallback!
    # Инсталлятор должен работать ТОЛЬКО с локальной панелью
    if [ -z "$panel_id" ]; then
        echo -e "${YELLOW}⚠️  Локальная (is_local: true) не найдена в config.yaml${NC}" >&2
        echo ""
        return 1
    fi
    
    echo "$panel_id"
}

# Оборачивает IP в скобки если это IPv6, чтобы URL был валидным (RFC 3986)
format_host_for_url() {
    local ip="$1"
    if [[ "$ip" == *:* ]]; then
        echo "[${ip}]"
    else
        echo "$ip"
    fi
}

# Добавить локальную панель в config.yaml
add_local_panel_to_config() {
    local xui_version=$1
    local xui_url=$2
    local xui_username=$3
    local xui_password=$4
    local server_ip=$5
    
    # Проверяем наличие config.yaml
    if [ ! -f "config.yaml" ]; then
        echo -e "${YELLOW}⚠️  config.yaml не найден, создаем из примера...${NC}"
        if [ -f "config.yaml.example" ]; then
            cp config.yaml.example config.yaml
        else
            echo -e "${RED}❌ config.yaml.example не найден${NC}"
            return 1
        fi
    fi
    
    check_yq || return 1
    
    # Проверяем, есть ли уже локальная панель
    local existing_panel=$(get_local_panel_id 2>/dev/null)
    
    if [ -n "$existing_panel" ]; then
        echo -e "${YELLOW}ℹ️  Локальная панель уже существует: ${existing_panel}${NC}"
        echo -e "${YELLOW}ℹ️  Обновляем данные существующей панели${NC}"
        return 0
    fi
    
    # Генерируем уникальный ID для новой панели
    local panel_id="local_panel"
    local counter=1
    
    # Проверяем, существует ли панель с таким ID
    while yq eval ".panels.${panel_id}" config.yaml 2>/dev/null | grep -qv "null"; do
        panel_id="local_panel${counter}"
        counter=$((counter + 1))
    done
    
    echo -e "${GREEN}✅ Создание новой локальной панели: ${panel_id}${NC}"
    
    # Добавляем новую панель в config.yaml
    yq eval -i ".panels.${panel_id}.alias = \"Локальная\"" config.yaml
    yq eval -i ".panels.${panel_id}.enabled = true" config.yaml
    yq eval -i ".panels.${panel_id}.is_local = true" config.yaml
    yq eval -i ".panels.${panel_id}.xui_version = \"${xui_version}\"" config.yaml
    yq eval -i ".panels.${panel_id}.xui_url = \"${xui_url}\"" config.yaml
    yq eval -i ".panels.${panel_id}.xui_username = \"${xui_username}\"" config.yaml
    yq eval -i ".panels.${panel_id}.xui_password = \"${xui_password}\"" config.yaml
    yq eval -i ".panels.${panel_id}.xui_api_token = \"\"" config.yaml
    yq eval -i ".panels.${panel_id}.xui_db_path = \"/etc/x-ui/x-ui.db\"" config.yaml
    yq eval -i ".panels.${panel_id}.inbound_id = \"1\"" config.yaml
    yq eval -i ".panels.${panel_id}.server_address = \"${server_ip}\"" config.yaml
    yq eval -i ".panels.${panel_id}.server_ip = \"${server_ip}\"" config.yaml
    yq eval -i ".panels.${panel_id}.transport = \"tcp\"" config.yaml
    yq eval -i ".panels.${panel_id}.security = \"reality\"" config.yaml
    yq eval -i ".panels.${panel_id}.tls_sni = \"\"" config.yaml
    yq eval -i ".panels.${panel_id}.tls_fingerprint = \"chrome\"" config.yaml
    yq eval -i ".panels.${panel_id}.reality_sni = \"www.nvidia.com\"" config.yaml
    yq eval -i ".panels.${panel_id}.reality_fingerprint = \"edge\"" config.yaml
    yq eval -i ".panels.${panel_id}.reality_public_key = \"\"" config.yaml
    yq eval -i ".panels.${panel_id}.reality_private_key = \"\"" config.yaml
    yq eval -i ".panels.${panel_id}.reality_short_id = \"\"" config.yaml
    
    # Устанавливаем эту панель как default_panel если default_panel не установлен
    local current_default=$(yq eval ".default_panel" config.yaml 2>/dev/null)
    if [ -z "$current_default" ] || [ "$current_default" = "null" ]; then
        yq eval -i ".default_panel = \"${panel_id}\"" config.yaml
        echo -e "${GREEN}✅ Панель ${panel_id} установлена как default_panel${NC}"
    fi
    
    echo -e "${GREEN}✅ Локальная панель ${panel_id} успешно добавлена в config.yaml${NC}"
    return 0
}

# Обновить значение в config.yaml для локальной панели
update_config_yaml_value() {
    local key=$1
    local value=$2
    
    # Проверяем наличие config.yaml
    if [ ! -f "config.yaml" ]; then
        echo -e "${YELLOW}⚠️  config.yaml не найден, создаем из примера...${NC}"
        if [ -f "config.yaml.example" ]; then
            cp config.yaml.example config.yaml
        else
            echo -e "${RED}❌ config.yaml.example не найден${NC}"
            return 1
        fi
    fi
    
    check_yq || return 1
    
    # Получаем ID локальной панели
    local panel_id=$(get_local_panel_id)
    
    if [ -z "$panel_id" ]; then
        echo -e "${RED}❌ Локальная панель не найдена в config.yaml${NC}"
        return 1
    fi
    
    # Обновляем значение для локальной панели
    yq eval -i ".panels.${panel_id}.${key} = \"${value}\"" config.yaml
    
    echo -e "${GREEN}✅ Обновлено: panels.${panel_id}.${key} = ${value}${NC}"
}

# Получить значение из config.yaml для локальной панели
get_config_yaml_value() {
    local key=$1
    
    if [ ! -f "config.yaml" ]; then
        echo ""
        return 1
    fi
    
    check_yq || return 1
    
    # Получаем ID локальной панели
    local panel_id=$(get_local_panel_id)
    
    if [ -z "$panel_id" ]; then
        echo ""
        return 1
    fi
    
    # Получаем значение
    yq eval ".panels.${panel_id}.${key}" config.yaml 2>/dev/null
}

# Универсальная функция обновления конфигурации
update_config_value() {
    local key=$1
    local value=$2
    
    # Проверяем наличие config.yaml
    if [ -f "config.yaml" ]; then
        # Используем config.yaml
        local panel_id=$(get_local_panel_id)
        
        if [ -n "$panel_id" ]; then
            # Маппинг ключей для config.yaml
            local yaml_key="$key"
            case "$key" in
                # Параметры панели
                "XUI_VERSION") yaml_key="xui_version" ;;
                "XUI_URL") yaml_key="xui_url" ;;
                "XUI_USERNAME") yaml_key="xui_username" ;;
                "XUI_PASSWORD") yaml_key="xui_password" ;;
                "XUI_API_TOKEN") yaml_key="xui_api_token" ;;
                "XUI_DB_PATH") yaml_key="xui_db_path" ;;
                "INBOUND_ID") yaml_key="inbound_id" ;;
                "SERVER_ADDRESS") yaml_key="server_address" ;;
                "SERVER_IP") yaml_key="server_ip" ;;
                "TRANSPORT") yaml_key="transport" ;;
                "SECURITY") yaml_key="security" ;;
                "TLS_SNI") yaml_key="tls_sni" ;;
                "TLS_FINGERPRINT") yaml_key="tls_fingerprint" ;;
                "TLS_ALPN") yaml_key="tls_alpn" ;;
                "REALITY_SNI") yaml_key="reality_sni" ;;
                "REALITY_FINGERPRINT") yaml_key="reality_fingerprint" ;;
                "REALITY_PUBLIC_KEY") yaml_key="reality_public_key" ;;
                "REALITY_PRIVATE_KEY") yaml_key="reality_private_key" ;;
                "REALITY_SHORT_ID") yaml_key="reality_short_id" ;;
                
                # Параметры common (сохраняются в common секцию)
                "XUI_BOT_TOKEN")
                    check_yq && yq eval -i ".common.xui_bot_token = \"${value}\"" config.yaml
                    echo -e "${GREEN}✅ Обновлено: common.xui_bot_token${NC}"
                    return 0
                    ;;
                "AWG_BOT_TOKEN")
                    check_yq && yq eval -i ".common.awg_bot_token = \"${value}\"" config.yaml
                    echo -e "${GREEN}✅ Обновлено: common.awg_bot_token${NC}"
                    return 0
                    ;;
                "ADMIN_IDS")
                    check_yq && yq eval -i ".common.admin_ids = [$(echo $value | sed 's/,/, /g')]" config.yaml
                    echo -e "${GREEN}✅ Обновлено: common.admin_ids${NC}"
                    return 0
                    ;;
                "SERVER_PORT")
                    check_yq && yq eval -i ".common.server_port = ${value}" config.yaml
                    echo -e "${GREEN}✅ Обновлено: common.server_port${NC}"
                    return 0
                    ;;
                "API_TIMEOUT")
                    check_yq && yq eval -i ".common.api_timeout = ${value}" config.yaml
                    echo -e "${GREEN}✅ Обновлено: common.api_timeout${NC}"
                    return 0
                    ;;
                "XHTTP_MODE")
                    check_yq && yq eval -i ".common.xhttp_mode = \"${value}\"" config.yaml
                    echo -e "${GREEN}✅ Обновлено: common.xhttp_mode${NC}"
                    return 0
                    ;;
                "MAX_TRAFFIC_GB")
                    check_yq && yq eval -i ".common.max_traffic_gb = ${value}" config.yaml
                    echo -e "${GREEN}✅ Обновлено: common.max_traffic_gb${NC}"
                    return 0
                    ;;
                "MAX_DAYS")
                    check_yq && yq eval -i ".common.max_days = ${value}" config.yaml
                    echo -e "${GREEN}✅ Обновлено: common.max_days${NC}"
                    return 0
                    ;;
                "MIN_DAYS")
                    check_yq && yq eval -i ".common.min_days = ${value}" config.yaml
                    echo -e "${GREEN}✅ Обновлено: common.min_days${NC}"
                    return 0
                    ;;
                "DEFAULT_TRAFFIC_GB")
                    check_yq && yq eval -i ".common.default_traffic_gb = ${value}" config.yaml
                    echo -e "${GREEN}✅ Обновлено: common.default_traffic_gb${NC}"
                    return 0
                    ;;
                "DEFAULT_DAYS")
                    check_yq && yq eval -i ".common.default_days = ${value}" config.yaml
                    echo -e "${GREEN}✅ Обновлено: common.default_days${NC}"
                    return 0
                    ;;
                "DB_PATH")
                    check_yq && yq eval -i ".common.db_path = \"${value}\"" config.yaml
                    echo -e "${GREEN}✅ Обновлено: common.db_path${NC}"
                    return 0
                    ;;
                "DB_BACKUP_ENABLED")
                    check_yq && yq eval -i ".common.db_backup_enabled = ${value}" config.yaml
                    echo -e "${GREEN}✅ Обновлено: common.db_backup_enabled${NC}"
                    return 0
                    ;;
                "DB_BACKUP_INTERVAL")
                    check_yq && yq eval -i ".common.db_backup_interval = ${value}" config.yaml
                    echo -e "${GREEN}✅ Обновлено: common.db_backup_interval${NC}"
                    return 0
                    ;;
                "LOG_LEVEL")
                    check_yq && yq eval -i ".common.log_level = \"${value}\"" config.yaml
                    echo -e "${GREEN}✅ Обновлено: common.log_level${NC}"
                    return 0
                    ;;
                "LOG_FILE_ENABLED")
                    check_yq && yq eval -i ".common.log_file_enabled = ${value}" config.yaml
                    echo -e "${GREEN}✅ Обновлено: common.log_file_enabled${NC}"
                    return 0
                    ;;
                "LOG_FILE_PATH")
                    check_yq && yq eval -i ".common.log_file_path = \"${value}\"" config.yaml
                    echo -e "${GREEN}✅ Обновлено: common.log_file_path${NC}"
                    return 0
                    ;;
                "LOG_MAX_SIZE_MB")
                    check_yq && yq eval -i ".common.log_max_size_mb = ${value}" config.yaml
                    echo -e "${GREEN}✅ Обновлено: common.log_max_size_mb${NC}"
                    return 0
                    ;;
                "LOG_BACKUP_COUNT")
                    check_yq && yq eval -i ".common.log_backup_count = ${value}" config.yaml
                    echo -e "${GREEN}✅ Обновлено: common.log_backup_count${NC}"
                    return 0
                    ;;
            esac
            
            # Обновляем значение в config.yaml
            update_config_yaml_value "$yaml_key" "$value"
        else
            echo -e "${YELLOW}⚠️  Локальная панель не найдена в config.yaml${NC}"
            echo -e "${YELLOW}⚠️  Параметр ${key} не будет сохранен${NC}"
        fi
    else
        echo -e "${YELLOW}⚠️  config.yaml не найден${NC}"
        echo -e "${YELLOW}⚠️  Параметр ${key} не будет сохранен${NC}"
    fi
}

# Универсальная функция получения значения конфигурации
get_config_value() {
    local key=$1
    
    # Проверяем наличие config.yaml
    if [ ! -f "config.yaml" ]; then
        echo -e "${RED}❌ config.yaml не найден${NC}"
        return 1
    fi
    
    local panel_id=$(get_local_panel_id)
    
    if [ -z "$panel_id" ]; then
        echo -e "${YELLOW}⚠️  Локальная панель не найдена в config.yaml${NC}"
        return 1
    fi
    
    # Маппинг ключей
    local yaml_key="$key"
    case "$key" in
        "XUI_VERSION") yaml_key="xui_version" ;;
        "XUI_URL") yaml_key="xui_url" ;;
        "XUI_USERNAME") yaml_key="xui_username" ;;
        "XUI_PASSWORD") yaml_key="xui_password" ;;
        "XUI_API_TOKEN") yaml_key="xui_api_token" ;;
        "XUI_DB_PATH") yaml_key="xui_db_path" ;;
        "INBOUND_ID") yaml_key="inbound_id" ;;
        "SERVER_ADDRESS") yaml_key="server_address" ;;
        "SERVER_IP") yaml_key="server_ip" ;;
        "TRANSPORT") yaml_key="transport" ;;
        "SECURITY") yaml_key="security" ;;
        "TLS_SNI") yaml_key="tls_sni" ;;
        "REALITY_SNI") yaml_key="reality_sni" ;;
        "REALITY_FINGERPRINT") yaml_key="reality_fingerprint" ;;
        "REALITY_PUBLIC_KEY") yaml_key="reality_public_key" ;;
        "REALITY_PRIVATE_KEY") yaml_key="reality_private_key" ;;
        "REALITY_SHORT_ID") yaml_key="reality_short_id" ;;
        "XUI_BOT_TOKEN")
            check_yq && yq eval ".common.xui_bot_token" config.yaml 2>/dev/null
            return 0
            ;;
        "AWG_BOT_TOKEN")
            check_yq && yq eval ".common.awg_bot_token" config.yaml 2>/dev/null
            return 0
            ;;
        "ADMIN_IDS")
            check_yq && yq eval ".common.admin_ids | join(\",\")" config.yaml 2>/dev/null
            return 0
            ;;
    esac
    
    # Получаем значение из config.yaml
    get_config_yaml_value "$yaml_key"
}

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

# Функция извлечения параметров из существующего инбаунда панели
extract_inbound_params() {
    echo -e "${YELLOW}🔍 Извлечение параметров из панели...${NC}"
    
    # Проверяем is_local для panel1
    local IS_LOCAL=$(yq eval '.panels.panel1.is_local' config.yaml 2>/dev/null)
    if [ "$IS_LOCAL" = "false" ]; then
        echo -e "${BLUE}ℹ️  Панель удаленная (is_local: false)${NC}"
        echo -e "${BLUE}ℹ️  Параметры будут обновлены ботом через API при подключении${NC}"
        return 0
    fi
    
    # Проверка наличия базы данных
    if [ ! -f "/etc/x-ui/x-ui.db" ]; then
        echo -e "${YELLOW}⚠️  База данных 3x-ui не найдена, пропускаем извлечение${NC}"
        return 1
    fi
    
    # Получаем INBOUND_ID из config.yaml (если указан)
    local INBOUND_ID=$(yq eval '.panels.panel1.inbound_id' config.yaml 2>/dev/null)
    
    # Если не указан или пустой, берем первый
    if [ -z "$INBOUND_ID" ] || [ "$INBOUND_ID" = "null" ]; then
        echo -e "${BLUE}  INBOUND_ID не указан в config.yaml, ищем первый...${NC}"
        INBOUND_ID=$(sqlite3 /etc/x-ui/x-ui.db "SELECT id FROM inbounds ORDER BY id ASC LIMIT 1;" 2>/dev/null)
    else
        echo -e "${BLUE}  Используем INBOUND_ID из config.yaml: ${INBOUND_ID}${NC}"
    fi
    
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
        update_config_value "INBOUND_ID" "${INBOUND_ID}"
        update_config_value "TRANSPORT" "${TRANSPORT}"
        update_config_value "SECURITY" "${SECURITY}"
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
            update_config_value "REALITY_PUBLIC_KEY" "${REALITY_PUBLIC}"
            echo -e "${GREEN}  ✓ Public Key обновлен${NC}"
        fi
        
        if [ -n "$REALITY_PRIVATE" ]; then
            update_config_value "REALITY_PRIVATE_KEY" "${REALITY_PRIVATE}"
            echo -e "${GREEN}  ✓ Private Key обновлен${NC}"
        fi
        
        if [ -n "$REALITY_SHORT" ]; then
            update_config_value "REALITY_SHORT_ID" "${REALITY_SHORT}"
            echo -e "${GREEN}  ✓ Short ID обновлен${NC}"
        fi
        
        if [ -n "$REALITY_SNI" ]; then
            update_config_value "REALITY_SNI" "${REALITY_SNI}"
            echo -e "${GREEN}  ✓ SNI обновлен: ${REALITY_SNI}${NC}"
        fi
        
        if [ -n "$REALITY_FINGERPRINT" ]; then
            update_config_value "REALITY_FINGERPRINT" "${REALITY_FINGERPRINT}"
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
            update_config_value "TLS_FINGERPRINT" "${TLS_FINGERPRINT}"
            echo -e "${GREEN}  ✓ TLS Fingerprint обновлен: ${TLS_FINGERPRINT}${NC}"
        fi
        
        if [ -n "$TLS_ALPN" ]; then
            update_config_value "TLS_ALPN" "${TLS_ALPN}"
            echo -e "${GREEN}  ✓ TLS ALPN обновлен: ${TLS_ALPN}${NC}"
        fi
        
        if [ -n "$TLS_SNI" ] && [ "$TLS_SNI" != "null" ]; then
            update_config_value "TLS_SNI" "${TLS_SNI}"
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
    update_config_value "XUI_DB_PATH" "/etc/x-ui/x-ui.db"
    update_config_value "API_TIMEOUT" "30"
    
    # VPN Server статические параметры
    update_config_value "SERVER_PORT" "443"
    
    # TLS статические параметры
    update_config_value "TLS_FINGERPRINT" "${DEFAULT_REALITY_FINGERPRINT}"
    update_config_value "TLS_ALPN" "http/1.1"
    
    # Reality статические параметры
    update_config_value "REALITY_SNI" "${DEFAULT_REALITY_SNI}"
    update_config_value "REALITY_FINGERPRINT" "${DEFAULT_REALITY_FINGERPRINT}"
    
    # xHTTP статические параметры
    update_config_value "XHTTP_MODE" "auto"
    
    # Лимиты
    update_config_value "MAX_TRAFFIC_GB" "1000"
    update_config_value "MAX_DAYS" "3650"
    update_config_value "MIN_DAYS" "1"
    update_config_value "DEFAULT_TRAFFIC_GB" "100"
    update_config_value "DEFAULT_DAYS" "30"
    
    # База данных
    update_config_value "DB_PATH" "/app/data/bot_users.db"
    update_config_value "DB_BACKUP_ENABLED" "true"
    update_config_value "DB_BACKUP_INTERVAL" "24"
    
    # Логирование
    update_config_value "LOG_LEVEL" "INFO"
    update_config_value "LOG_FILE_ENABLED" "true"
    update_config_value "LOG_FILE_PATH" "/app/logs/bot.log"
    update_config_value "LOG_MAX_SIZE_MB" "10"
    update_config_value "LOG_BACKUP_COUNT" "5"
    
    echo -e "${GREEN}✅ Статические параметры созданы${NC}"
}

# Функция интерактивного ввода секретных параметров
interactive_setup() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Настройка Параметров Бота${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    # Создаем config.yaml если не существует
    create_config_if_not_exists
    
    # Создаем статические параметры
    create_static_params
    
    # Получаем IP сервера
    SERVER_IP=$(curl -s -4 https://api4.ipify.org 2>/dev/null || curl -s -4 https://ipv4.icanhazip.com 2>/dev/null || curl -s -4 ifconfig.me 2>/dev/null || echo "")
    
    # ==================== Telegram Bot ====================
    echo -e "\n${GREEN}📱 Настройка Telegram Bot${NC}\n"
    
    XUI_BOT_TOKEN=$(get_config_value "XUI_BOT_TOKEN" | tr -d '"')
    if [ -z "$XUI_BOT_TOKEN" ]; then
        read -p "Введите XUI_BOT_TOKEN: " XUI_BOT_TOKEN
        update_config_value "XUI_BOT_TOKEN" "$XUI_BOT_TOKEN"
    else
        echo -e "XUI_BOT_TOKEN: ${XUI_BOT_TOKEN:0:10}... ${GREEN}✓${NC}"
    fi
    
    ADMIN_IDS=$(get_config_value "ADMIN_IDS")
    if [ -z "$ADMIN_IDS" ]; then
        read -p "Введите ADMIN_IDS (ID администраторов через запятую): " ADMIN_IDS
        update_config_value "ADMIN_IDS" "$ADMIN_IDS"
    else
        echo -e "ADMIN_IDS: $ADMIN_IDS ${GREEN}✓${NC}"
    fi
    
    # ==================== Автоматическое заполнение ====================
    echo -e "\n${GREEN}🔧 Автоматическое заполнение параметров...${NC}"
    
    # IP сервера
    update_config_value "SERVER_ADDRESS" "$SERVER_IP"
    update_config_value "SERVER_IP" "$SERVER_IP"
    
    # ==================== Проверка данных 3x-ui ====================
    XUI_URL=$(get_config_value "XUI_URL")
    XUI_USERNAME=$(get_config_value "XUI_USERNAME")
    XUI_PASSWORD=$(get_config_value "XUI_PASSWORD")
    REALITY_PUBLIC_KEY=$(get_config_value "REALITY_PUBLIC_KEY")
    REALITY_SHORT_ID=$(get_config_value "REALITY_SHORT_ID")
    
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
    XUI_URL=$(get_config_value "XUI_URL")
    if [ -n "$XUI_URL" ]; then
        DOMAIN=$(echo "$XUI_URL" | sed -E 's|^https?://([^:/]+).*|\1|')
        if [ -n "$DOMAIN" ] && [[ ! "$DOMAIN" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            echo -e "${YELLOW}🔄 Обнаружен домен в XUI_URL: ${DOMAIN}${NC}"
            CURRENT_SERVER=$(get_config_value "SERVER_ADDRESS")
            if [ "$CURRENT_SERVER" != "$DOMAIN" ]; then
                update_config_value "SERVER_ADDRESS" "$DOMAIN"
                echo -e "${GREEN}✅ SERVER_ADDRESS обновлён: ${DOMAIN}${NC}"
            fi
            CURRENT_TLS_SNI=$(get_config_value "TLS_SNI")
            if [ "$CURRENT_TLS_SNI" != "$DOMAIN" ]; then
                update_config_value "TLS_SNI" "$DOMAIN"
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

# Функция проверки и создания инбаунда при необходимости
check_and_create_inbound_if_needed() {
    echo -e "${YELLOW}🔍 Проверка наличия инбаундов в панели...${NC}"
    
    # Проверка наличия базы данных
    if [ ! -f "/etc/x-ui/x-ui.db" ]; then
        echo -e "${RED}❌ База данных 3x-ui не найдена!${NC}"
        return 1
    fi
    
    # Получаем количество инбаундов
    local INBOUND_COUNT=$(sqlite3 /etc/x-ui/x-ui.db "SELECT COUNT(*) FROM inbounds;" 2>/dev/null)
    
    if [ -z "$INBOUND_COUNT" ] || [ "$INBOUND_COUNT" -eq 0 ]; then
        echo -e "${YELLOW}⚠️  В панели 3x-ui не найдено ни одного инбаунда!${NC}"
        echo -e "${BLUE}Для работы бота необходимо создать хотя бы один инбаунд.${NC}"
        
        # Предлагаем создать инбаунд
        while true; do
            echo -e "\n${BLUE}========================================${NC}"
            echo -e "${BLUE}   Создать подключение?${NC}"
            echo -e "${BLUE}========================================${NC}"
            echo -e "${GREEN}Enter${NC} - Да, создать подключение"
            echo -e "${GREEN}0${NC}     - Нет, вернуться в главное меню"
            echo -e "${BLUE}========================================${NC}"
            read -p "Ваш выбор: " create_inbound_choice
            
            if [[ "$create_inbound_choice" == "0" ]]; then
                echo -e "${YELLOW}Возврат в главное меню...${NC}"
                return 1
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
                    return 1
                fi
                
                case $inbound_type in
                    1)
                        if create_xhttp_reality_inbound; then
                            echo -e "${GREEN}✅ Инбаунд успешно создан!${NC}"
                            return 0
                        else
                            echo -e "${RED}❌ Не удалось создать инбаунд${NC}"
                            return 1
                        fi
                        ;;
                    2)
                        if create_tcp_reality_inbound; then
                            echo -e "${GREEN}✅ Инбаунд успешно создан!${NC}"
                            return 0
                        else
                            echo -e "${RED}❌ Не удалось создать инбаунд${NC}"
                            return 1
                        fi
                        ;;
                    3)
                        if create_tcp_tls_inbound; then
                            echo -e "${GREEN}✅ Инбаунд успешно создан!${NC}"
                            return 0
                        else
                            echo -e "${RED}❌ Не удалось создать инбаунд${NC}"
                            return 1
                        fi
                        ;;
                    *)
                        echo -e "${RED}Неверный выбор. Попробуйте снова.${NC}"
                        ;;
                esac
            done
        done
    else
        echo -e "${GREEN}✅ Найдено инбаундов: ${INBOUND_COUNT}${NC}"
        return 0
    fi
}

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
    
    # Проверка наличия инбаундов и создание при необходимости
    if ! check_and_create_inbound_if_needed; then
        echo -e "\n${CYAN}Нажмите Enter для возврата в главное меню...${NC}"
        read
        return
    fi
    
    # Создание config.yaml если не существует
    create_config_if_not_exists
    
    # Проверка XUI_URL, XUI_USERNAME, XUI_PASSWORD
    echo -e "\n${YELLOW}🔍 Проверка параметров 3x-ui панели...${NC}"
    
    XUI_URL=$(get_config_value "XUI_URL")
    if [ -z "$XUI_URL" ]; then
        echo -e "${YELLOW}📝 Настройка параметров 3x-ui панели${NC}\n"
        read -p "Введите XUI_URL: " xui_url
        update_config_value "XUI_URL" "$xui_url"
        XUI_URL="$xui_url"
    fi
    
    XUI_USERNAME=$(get_config_value "XUI_USERNAME")
    if [ -z "$XUI_USERNAME" ]; then
        read -p "Введите XUI_USERNAME: " xui_username
        update_config_value "XUI_USERNAME" "$xui_username"
    fi
    
    XUI_PASSWORD=$(get_config_value "XUI_PASSWORD")
    if [ -z "$XUI_PASSWORD" ]; then
        read -p "Введите XUI_PASSWORD: " xui_password
        update_config_value "XUI_PASSWORD" "$xui_password"
    fi
    
    echo -e "${GREEN}✅ Параметры 3x-ui панели настроены${NC}\n"
    
    # Обновляем SERVER_ADDRESS и TLS_SNI из XUI_URL если он уже установлен
    if [ -n "$XUI_URL" ]; then
        # Извлекаем домен/IP из URL
        DOMAIN=$(echo "$XUI_URL" | sed -E 's|^https?://([^:/]+).*|\1|')
        
        # Проверяем, является ли это доменом (не IP адресом)
        if [ -n "$DOMAIN" ] && [[ ! "$DOMAIN" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            echo -e "${YELLOW}🔄 Обнаружен домен в XUI_URL: ${DOMAIN}${NC}"
            
            # Обновляем SERVER_ADDRESS
            CURRENT_SERVER=$(get_config_value "SERVER_ADDRESS")
            if [ "$CURRENT_SERVER" != "$DOMAIN" ]; then
                update_config_value "SERVER_ADDRESS" "$DOMAIN"
                echo -e "${GREEN}✅ SERVER_ADDRESS обновлён: ${DOMAIN}${NC}"
            fi
            
            # Обновляем TLS_SNI
            CURRENT_TLS_SNI=$(get_config_value "TLS_SNI")
            if [ "$CURRENT_TLS_SNI" != "$DOMAIN" ]; then
                update_config_value "TLS_SNI" "$DOMAIN"
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
        update_config_value "INBOUND_ID" "${FIRST_INBOUND_ID}"
        update_config_value "TRANSPORT" "${TRANSPORT}"
        update_config_value "SECURITY" "${SECURITY}"
        
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
                update_config_value "REALITY_PUBLIC_KEY" "${REALITY_PUBLIC}"
                update_config_value "REALITY_PRIVATE_KEY" "${REALITY_PRIVATE}"
                update_config_value "REALITY_SHORT_ID" "${REALITY_SHORT}"
                
                echo -e "${BLUE}  Public Key: ${REALITY_PUBLIC:0:20}...${NC}"
                echo -e "${BLUE}  Short ID: ${REALITY_SHORT}${NC}"
                
                # Сохраняем SNI и Fingerprint (обязательно)
                if [ -n "$REALITY_SNI" ]; then
                    update_config_value "REALITY_SNI" "${REALITY_SNI}"
                    echo -e "${BLUE}  SNI: ${REALITY_SNI}${NC}"
                fi
                
                if [ -n "$REALITY_FINGERPRINT" ]; then
                    update_config_value "REALITY_FINGERPRINT" "${REALITY_FINGERPRINT}"
                    echo -e "${BLUE}  Fingerprint: ${REALITY_FINGERPRINT}${NC}"
                fi
            else
                echo -e "${YELLOW}⚠️  Не удалось извлечь Reality ключи, запрашиваем вручную...${NC}\n"
                read -p "Введите REALITY_PUBLIC_KEY: " reality_pub
                read -p "Введите REALITY_PRIVATE_KEY: " reality_priv
                read -p "Введите REALITY_SHORT_ID: " reality_short
                
                update_config_value "REALITY_PUBLIC_KEY" "${reality_pub}"
                update_config_value "REALITY_PRIVATE_KEY" "${reality_priv}"
                update_config_value "REALITY_SHORT_ID" "${reality_short}"
            fi
        fi
        
        # Если security = tls - извлекаем TLS параметры
        if [ "$SECURITY" = "tls" ]; then
            echo -e "${YELLOW}🔑 Обнаружен TLS, извлекаем параметры...${NC}"
            
            TLS_FINGERPRINT=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.tlsSettings.settings.fingerprint') FROM inbounds WHERE id=${FIRST_INBOUND_ID};" 2>/dev/null)
            TLS_ALPN=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.tlsSettings.alpn[0]') FROM inbounds WHERE id=${FIRST_INBOUND_ID};" 2>/dev/null)
            TLS_SNI=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.tlsSettings.serverName') FROM inbounds WHERE id=${FIRST_INBOUND_ID};" 2>/dev/null)
            
            if [ -n "$TLS_FINGERPRINT" ] && [ "$TLS_FINGERPRINT" != "null" ]; then
                update_config_value "TLS_FINGERPRINT" "${TLS_FINGERPRINT}"
                echo -e "${GREEN}✅ TLS Fingerprint: ${TLS_FINGERPRINT}${NC}"
            fi
            
            if [ -n "$TLS_ALPN" ] && [ "$TLS_ALPN" != "null" ]; then
                update_config_value "TLS_ALPN" "${TLS_ALPN}"
                echo -e "${GREEN}✅ TLS ALPN: ${TLS_ALPN}${NC}"
            fi
            
            if [ -n "$TLS_SNI" ] && [ "$TLS_SNI" != "null" ]; then
                update_config_value "TLS_SNI" "${TLS_SNI}"
                echo -e "${GREEN}✅ TLS SNI: ${TLS_SNI}${NC}"
            fi
        fi
    else
        echo -e "${YELLOW}⚠️  Инбаунды не найдены${NC}"
    fi
    
    echo ""
    
    # Проверка XUI_BOT_TOKEN
    XUI_BOT_TOKEN=$(get_config_value "XUI_BOT_TOKEN" | tr -d '"')
    if [ -z "$XUI_BOT_TOKEN" ]; then
        echo -e "${YELLOW}📱 Настройка Telegram Bot для XUI${NC}\n"
        read -p "Введите XUI_BOT_TOKEN для XUI бота: " xui_token
        update_config_value "XUI_BOT_TOKEN" "$xui_token"
    fi
    
    # Проверка ADMIN_IDS
    ADMIN_IDS=$(get_config_value "ADMIN_IDS")
    if [ -z "$ADMIN_IDS" ]; then
        read -p "Введите ADMIN_IDS (через запятую): " admin_ids
        update_config_value "ADMIN_IDS" "$admin_ids"
    fi
    
    # Определяем и сохраняем версию панели
    echo -e "${YELLOW}🔍 Определение версии панели...${NC}"
    XUI_VERSION=""
    
    # Способ 1: Из исполняемого файла x-ui
    if [ -f "/usr/local/x-ui/x-ui" ]; then
        XUI_VERSION=$(/usr/local/x-ui/x-ui -v 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1)
        if [ -n "$XUI_VERSION" ]; then
            echo -e "${GREEN}✅ Версия панели определена: ${XUI_VERSION}${NC}"
        fi
    fi
    
    # Способ 2: Из бинарного файла в bin/
    if [ -z "$XUI_VERSION" ] && [ -f "/usr/local/x-ui/bin/x-ui" ]; then
        XUI_VERSION=$(/usr/local/x-ui/bin/x-ui -v 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1)
        if [ -n "$XUI_VERSION" ]; then
            echo -e "${GREEN}✅ Версия панели определена: ${XUI_VERSION}${NC}"
        fi
    fi
    
    # Сохраняем в config.yaml
    if [ -n "$XUI_VERSION" ]; then
        echo -e "${YELLOW}📝 Сохранение версии в config.yaml...${NC}"
        update_config_value "XUI_VERSION" "${XUI_VERSION}"
        echo -e "${GREEN}✅ XUI_VERSION обновлён: ${XUI_VERSION}${NC}"
    else
        echo -e "${YELLOW}⚠️  Не удалось определить версию панели${NC}"
        echo -e "${BLUE}ℹ️  Будет использоваться значение из config.yaml или 'latest'${NC}"
    fi
    echo ""
    
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
        
        # Обновляем политику перезапуска на always
        docker update --restart=always xuibot >/dev/null 2>&1
        
        # Проверка автозагрузки
        local restart_policy=$(docker inspect xuibot --format='{{.HostConfig.RestartPolicy.Name}}' 2>/dev/null)
        if [ "$restart_policy" = "always" ]; then
            echo -e "${GREEN}🔄 Автозагрузка: ✓ Включена (бот будет автоматически запускаться при перезагрузке сервера)${NC}"
        fi
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
    echo -e "${BLUE}📋 Шаг 1: Чтение XUI_URL из config.yaml${NC}"
    XUI_URL=$(get_config_value "XUI_URL")
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
            CURRENT_SERVER=$(get_config_value "SERVER_ADDRESS")
            echo -e "${YELLOW}  Текущее значение: ${CURRENT_SERVER}${NC}"
            echo -e "${YELLOW}  Новое значение: ${DOMAIN}${NC}"
            update_config_value "SERVER_ADDRESS" "${DOMAIN}"
            NEW_SERVER=$(get_config_value "SERVER_ADDRESS")
            echo -e "${GREEN}✅ SERVER_ADDRESS обновлён: ${NEW_SERVER}${NC}"
            
            echo -e "\n${BLUE}📋 Шаг 5: Обновление TLS_SNI${NC}"
            CURRENT_TLS_SNI=$(get_config_value "TLS_SNI")
            echo -e "${YELLOW}  Текущее значение: ${CURRENT_TLS_SNI:-<пусто>}${NC}"
            echo -e "${YELLOW}  Новое значение: ${DOMAIN}${NC}"
            update_config_value "TLS_SNI" "${DOMAIN}"
            NEW_TLS_SNI=$(get_config_value "TLS_SNI")
            echo -e "${GREEN}✅ TLS_SNI обновлён: ${NEW_TLS_SNI}${NC}"
        else
            echo -e "${YELLOW}⚠️  Это IP АДРЕС (не домен)${NC}"
            echo -e "${BLUE}ℹ️  SERVER_ADDRESS и TLS_SNI НЕ изменяются${NC}"
        fi
    else
        echo -e "${RED}❌ XUI_URL не найден в config.yaml${NC}"
    fi
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
    
    # Проверка наличия инбаундов и создание при необходимости
    if ! check_and_create_inbound_if_needed; then
        echo -e "\n${CYAN}Нажмите Enter для возврата в главное меню...${NC}"
        read
        return
    fi
    
    # Извлекаем параметры из панели (TLS_FINGERPRINT, TLS_ALPN и т.д.)
    echo ""
    extract_inbound_params
    echo ""
    
    # Определяем и сохраняем версию панели
    echo -e "${YELLOW}🔍 Определение версии панели...${NC}"
    XUI_VERSION=""
    
    # Способ 1: Из исполняемого файла x-ui (основной метод)
    if [ -f "/usr/local/x-ui/x-ui" ]; then
        XUI_VERSION=$(/usr/local/x-ui/x-ui -v 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1)
        if [ -n "$XUI_VERSION" ]; then
            echo -e "${GREEN}✅ Версия панели определена: ${XUI_VERSION}${NC}"
        fi
    fi
    
    # Способ 2: Из бинарного файла в bin/
    if [ -z "$XUI_VERSION" ] && [ -f "/usr/local/x-ui/bin/x-ui" ]; then
        XUI_VERSION=$(/usr/local/x-ui/bin/x-ui -v 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1)
        if [ -n "$XUI_VERSION" ]; then
            echo -e "${GREEN}✅ Версия панели определена: ${XUI_VERSION}${NC}"
        fi
    fi
    
    # Если версия определена, сохраняем в config.yaml
    if [ -n "$XUI_VERSION" ]; then
        echo -e "${YELLOW}📝 Сохранение версии в config.yaml...${NC}"
        update_config_value "XUI_VERSION" "${XUI_VERSION}"
        echo -e "${GREEN}✅ XUI_VERSION обновлён: ${XUI_VERSION}${NC}"
    else
        echo -e "${YELLOW}⚠️  Не удалось определить версию панели${NC}"
        echo -e "${BLUE}ℹ️  Будет использоваться значение из config.yaml или 'latest'${NC}"
    fi
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
    
    echo -e "\n${YELLOW}⏳ Ожидание запуска контейнера...${NC}"
    sleep 8
    
    echo -e "\n${GREEN}✅ XUI Бот обновлен!${NC}"
    echo -e "${GREEN}📊 Статус:${NC}"
    docker ps --filter name=xuibot
    
    echo -e "\n${GREEN}📋 Логи XUI бота (последние 50 строк):${NC}"
    docker logs --tail 50 xuibot 2>&1 || echo -e "${RED}⚠️  Не удалось получить логи. Контейнер может еще запускаться.${NC}"
    
    echo -e "\n${YELLOW}Для просмотра в реальном времени:${NC}"
    echo -e "${YELLOW}docker logs -f xuibot${NC}"
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
    
    # Очистка XUI_BOT_TOKEN и XUI credentials из config.yaml
    if [ -f "config.yaml" ]; then
        echo -e "${YELLOW}🧹 Очистка XUI настроек из config.yaml...${NC}"
        
        if check_yq; then
            # Очищаем XUI_BOT_TOKEN из common
            yq eval -i '.common.xui_bot_token = ""' config.yaml
            echo -e "${GREEN}✅ XUI_BOT_TOKEN очищен${NC}"
            
            # Очищаем credentials локальной панели
            local panel_id=$(get_local_panel_id)
            if [ -n "$panel_id" ]; then
                yq eval -i ".panels.${panel_id}.xui_url = \"\"" config.yaml
                yq eval -i ".panels.${panel_id}.xui_username = \"\"" config.yaml
                yq eval -i ".panels.${panel_id}.xui_password = \"\"" config.yaml
                echo -e "${GREEN}✅ XUI credentials очищены${NC}"
            fi
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
    
    # Создание config.yaml если не существует
    create_config_if_not_exists
    
    # Проверка AWG_BOT_TOKEN
    AWG_BOT_TOKEN=$(get_config_value "AWG_BOT_TOKEN" | tr -d '"')
    if [ -z "$AWG_BOT_TOKEN" ]; then
        echo -e "${YELLOW}📱 Настройка Telegram Bot для AWG${NC}\n"
        read -p "Введите AWG_BOT_TOKEN для AWG бота: " awg_token
        update_config_value "AWG_BOT_TOKEN" "$awg_token"
    fi
    
    # Проверка ADMIN_IDS
    ADMIN_IDS=$(get_config_value "ADMIN_IDS")
    if [ -z "$ADMIN_IDS" ]; then
        read -p "Введите ADMIN_IDS (через запятую): " admin_ids
        update_config_value "ADMIN_IDS" "$admin_ids"
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
        
        # Обновляем политику перезапуска на always
        docker update --restart=always awgbot >/dev/null 2>&1
        
        # Проверка автозагрузки
        local restart_policy=$(docker inspect awgbot --format='{{.HostConfig.RestartPolicy.Name}}' 2>/dev/null)
        if [ "$restart_policy" = "always" ]; then
            echo -e "${GREEN}🔄 Автозагрузка: ✓ Включена (бот будет автоматически запускаться при перезагрузке сервера)${NC}"
        fi
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
    if [ -f "config.yaml" ]; then
        CURRENT_VALUE=$(yq eval '.common.allow_user_dns_queries' config.yaml 2>/dev/null)
        if [ "$CURRENT_VALUE" != "null" ] && [ -n "$CURRENT_VALUE" ]; then
            echo -e "${GREEN}✓ Параметр уже существует: ${CURRENT_VALUE}${NC}"
            echo -e "${BLUE}ℹ️  Оставляем текущее значение без изменений${NC}"
        else
            echo -e "${YELLOW}⚠️  Параметр ALLOW_USER_DNS_QUERIES не найден${NC}"
            echo -e "${YELLOW}🔧 Добавляем с значением по умолчанию: true${NC}"
            update_config_value "ALLOW_USER_DNS_QUERIES" "true"
        fi
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
    
    echo -e "\n${GREEN}📋 Логи AWG бота (последние 50 строк):${NC}"
    docker logs --tail 50 awgbot
    
    echo -e "\n${YELLOW}Для просмотра в реальном времени:${NC}"
    echo -e "${YELLOW}docker logs -f awgbot${NC}"
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
    
    # Очистка AWG_BOT_TOKEN из config.yaml
    if [ -f "config.yaml" ]; then
        echo -e "${YELLOW}🧹 Очистка AWG_BOT_TOKEN из config.yaml...${NC}"
        if check_yq; then
            yq eval -i '.common.awg_bot_token = ""' config.yaml
            echo -e "${GREEN}✅ AWG_BOT_TOKEN очищен${NC}"
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
    
    echo -e "\n${YELLOW}⏳ Ожидание запуска контейнера...${NC}"
    sleep 8
    
    echo -e "\n${GREEN}✅ XUI Бот пересобран!${NC}"
    echo -e "${GREEN}📊 Статус:${NC}"
    docker ps --filter name=xuibot
    
    echo -e "\n${GREEN}📋 Логи XUI бота (последние 50 строк):${NC}"
    docker logs --tail 50 xuibot 2>&1 || echo -e "${RED}⚠️  Не удалось получить логи. Контейнер может еще запускаться.${NC}"
    
    echo -e "\n${YELLOW}Для просмотра в реальном времени:${NC}"
    echo -e "${YELLOW}docker logs -f xuibot${NC}"
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
    echo -e "\n${GREEN}✅ AWG Бот пересобран!${NC}"
    echo -e "${GREEN}📊 Статус:${NC}"
    docker ps --filter name=awgbot
    
    echo -e "\n${GREEN}📋 Логи AWG бота (последние 50 строк):${NC}"
    docker logs --tail 50 awgbot
    
    echo -e "\n${YELLOW}Для просмотра в реальном времени:${NC}"
    echo -e "${YELLOW}docker logs -f awgbot${NC}"
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
        
        # Способ 2: Из config.yaml
        if [ -z "$xui_version" ] && [ -f "config.yaml" ]; then
            xui_version=$(get_config_value "XUI_VERSION" 2>/dev/null)
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
        
        # Получаем данные из config.yaml
        if [ -f "config.yaml" ]; then
            local inbound_id=$(get_config_value "INBOUND_ID" 2>/dev/null)
            local xui_db_path=$(get_config_value "XUI_DB_PATH" 2>/dev/null)
            
            # Значения по умолчанию
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
            echo -e "  Состояние: ${GREEN}Запущена${NC}"
            echo -e "  Всего ключей: ${total_keys}"
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
        local xui_token=$(get_config_value "XUI_BOT_TOKEN" | tr -d '"')
        local xui_bot_username=$(get_bot_username "$xui_token" "xuibot")
        local db_path=$(get_config_value "DB_PATH")
        
        # Значение по умолчанию для DB_PATH
        [ -z "$db_path" ] && db_path="/app/data/bot_users.db"
        
        # Получаем количество пользователей из базы данных
        local user_count=0
        local admin_ids=$(get_config_value "ADMIN_IDS")
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
        
        # Проверка автозагрузки
        local xui_restart_policy=$(docker inspect xuibot --format='{{.HostConfig.RestartPolicy.Name}}' 2>/dev/null)
        if [ "$xui_restart_policy" = "always" ]; then
            echo -e "  Автозагрузка: ${GREEN}✅ Включена${NC}"
        elif [ "$xui_restart_policy" = "unless-stopped" ]; then
            echo -e "  Автозагрузка: ${GREEN}✅ Включена${NC} (unless-stopped - кроме ручной остановки)"
        else
            echo -e "  Автозагрузка: ${RED}❌ Отключена${NC} (${xui_restart_policy})"
        fi
    else
        echo -e "  XUI Bot: ${RED}❌ Не установлен${NC}"
    fi
    
    # ============================================
    # AWGBOT
    # ============================================
    echo -e "\n${YELLOW}${BOLD}AWGBOT:${NC}"
    
    if docker ps --filter name=awgbot --format "{{.Names}}" | grep -q awgbot; then
        local awg_token=$(get_config_value "AWG_BOT_TOKEN" | tr -d '"')
        local awg_bot_username=$(get_bot_username "$awg_token" "awgbot")
        
        if [ "$awg_bot_username" != "Unknown" ]; then
            echo -e "  Ссылка: https://t.me/${awg_bot_username}"
        fi
        echo -e "  AWG Bot: ${GREEN}✅ Запущен${NC}"
        
        # Проверка автозагрузки
        local awg_restart_policy=$(docker inspect awgbot --format='{{.HostConfig.RestartPolicy.Name}}' 2>/dev/null)
        if [ "$awg_restart_policy" = "always" ]; then
            echo -e "  Автозагрузка: ${GREEN}✅ Включена${NC}"
        elif [ "$awg_restart_policy" = "unless-stopped" ]; then
            echo -e "  Автозагрузка: ${GREEN}✅ Включена${NC} (unless-stopped - кроме ручной остановки)"
        else
            echo -e "  Автозагрузка: ${RED}❌ Отключена${NC} (${awg_restart_policy})"
        fi
    else
        echo -e "  AWG Bot: ${RED}❌ Не установлен${NC}"
    fi
    
    
    # ============================================
    # SYSTEM AUTOSTART
    # ============================================
    echo -e "\n${YELLOW}${BOLD}SYSTEM AUTOSTART:${NC}"
    
    # Проверка Docker в автозагрузке
    if systemctl is-enabled docker &>/dev/null; then
        echo -e "  Docker: ${GREEN}✅ Включен в автозагрузку${NC}"
    else
        echo -e "  Docker: ${RED}❌ Не включен в автозагрузку${NC}"
        echo -e "  ${YELLOW}Для включения выполните: systemctl enable docker${NC}"
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
        local xui_url=$(get_config_value "XUI_URL" 2>/dev/null)
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
    if [ -n "$NONINTERACTIVE" ]; then
        version_choice="${XUI_VERSION_CHOICE:-3}"
        echo -e "${BLUE}ℹ️  Автоматический режим: выбрана версия ${version_choice}${NC}"
    else
        echo -e "\n${YELLOW}Выберите версию для установки [3]:${NC} "
        read -p "" version_choice
        version_choice=${version_choice:-3}
    fi
    
    case $version_choice in
        1)
            install_3xui_v294
            ;;
        2)
            echo -e "\n${RED}⚠️  ВНИМАНИЕ!${NC}"
            echo -e "${YELLOW}Последняя версия v2.x может быть нестабильной!${NC}"
            echo -e "${YELLOW}Рекомендуется использовать v2.9.4 или v3.x${NC}"
            if [ -z "$NONINTERACTIVE" ]; then
                read -p "Вы уверены что хотите продолжить? (нажмите Enter для подтверждения или 0 для отмены): " confirm_latest
            else
                confirm_latest=""
            fi
            if [[ "$confirm_latest" != "0" ]]; then
                install_3xui_latest
            else
                echo -e "${GREEN}Отменено. Устанавливаем v3.x...${NC}"
                NONINTERACTIVE=1
                install_3xui_v3
                return
            fi
            ;;
        3)
            echo -e "\n${GREEN}✓ Установка 3x-ui v3.x с поддержкой API${NC}"
            echo -e "${YELLOW}Эта версия полностью поддерживается ботом через API${NC}"
            echo -e "${YELLOW}API токен будет автоматически извлечен и сохранен${NC}\n"
            NONINTERACTIVE=1
            install_3xui_v3
            return
            ;;
        0)
            echo -e "${YELLOW}Отменено${NC}"
            return
            ;;
        *)
            echo -e "${YELLOW}Неверный выбор. Устанавливаем v3.x по умолчанию...${NC}"
            sleep 2
            NONINTERACTIVE=1
            install_3xui_v3
            return
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
        if [ -z "$NONINTERACTIVE" ]; then
            read -p "Переустановить? (нажмите Enter для подтверждения или 0 для отмены): " reinstall
        else
            reinstall=""
            echo -e "${BLUE}ℹ️  Автоматический режим: продолжаем переустановку${NC}"
        fi
        if [[ "$reinstall" == "0" ]]; then
            echo -e "${YELLOW}Отменено${NC}"
            return
        fi
    fi
    
    SERVER_IP=$(curl -s -4 https://api4.ipify.org 2>/dev/null || curl -s -4 https://ipv4.icanhazip.com 2>/dev/null || curl -s -4 ifconfig.me 2>/dev/null || echo "")
    
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
            
            # Получаем настройки напрямую из базы данных для версии 3.x
            if [ -f "/etc/x-ui/x-ui.db" ]; then
                echo -e "${YELLOW}🔐 Получение данных из базы данных...${NC}"
                
                # Получаем username
                XUI_USERNAME=$(sqlite3 /etc/x-ui/x-ui.db "SELECT username FROM users LIMIT 1;" 2>/dev/null || echo "")
                
                # Получаем webPort
                if [ -z "$XUI_PORT" ]; then
                    XUI_PORT=$(sqlite3 /etc/x-ui/x-ui.db "SELECT value FROM settings WHERE key='webPort';" 2>/dev/null || echo "")
                fi
                
                # Получаем webBasePath
                if [ -z "$XUI_PATH" ]; then
                    XUI_PATH=$(sqlite3 /etc/x-ui/x-ui.db "SELECT value FROM settings WHERE key='webBasePath';" 2>/dev/null || echo "/")
                    # Удаляем trailing slash если это не корень
                    if [ "$XUI_PATH" != "/" ]; then
                        XUI_PATH=$(echo "$XUI_PATH" | sed 's/\/$//')
                    fi
                fi
                
                if [ -n "$XUI_USERNAME" ]; then
                    echo -e "${GREEN}✅ Username: ${YELLOW}${XUI_USERNAME}${NC}"
                fi
                if [ -n "$XUI_PORT" ]; then
                    echo -e "${GREEN}✅ Port: ${YELLOW}${XUI_PORT}${NC}"
                fi
                if [ -n "$XUI_PATH" ] && [ "$XUI_PATH" != "/" ]; then
                    echo -e "${GREEN}✅ webBasePath: ${YELLOW}${XUI_PATH}${NC}"
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
        XUI_URL="http://$(format_host_for_url "${SERVER_IP}"):${XUI_PORT}"
        
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
        
        # Создание config.yaml если не существует (БЕЗ попытки обновить локальную панель)
        if [ ! -f "config.yaml" ]; then
            echo -e "${YELLOW}📝 Создание config.yaml из примера...${NC}"
            if [ -f "config.yaml.example" ]; then
                cp config.yaml.example config.yaml
                echo -e "${GREEN}✅ config.yaml создан из примера${NC}"
            else
                echo -e "${RED}❌ config.yaml.example не найден${NC}"
            fi
        fi
        
        # Определяем версию для добавления в config.yaml
        XUI_VERSION_FOR_CONFIG="${XUI_VERSION:-latest}"
        
        # Добавляем локальную панель в config.yaml ПЕРЕД сохранением данных
        echo -e "${YELLOW}📝 Добавление локальной панели в config.yaml...${NC}"
        if add_local_panel_to_config "$XUI_VERSION_FOR_CONFIG" "${XUI_URL}" "${XUI_USERNAME}" "${XUI_PASSWORD}" "${SERVER_IP}"; then
            echo -e "${GREEN}✅ Локальная панель добавлена в config.yaml${NC}"
        else
            echo -e "${RED}❌ Не удалось добавить локальную панель в config.yaml${NC}"
            echo -e "${YELLOW}⚠️  Продолжаем без config.yaml${NC}"
        fi
        
        echo -e "${YELLOW}💾 Сохранение учетных данных...${NC}"
        update_config_value "XUI_URL" "${XUI_URL}"
        update_config_value "XUI_USERNAME" "${XUI_USERNAME}"
        update_config_value "XUI_PASSWORD" "${XUI_PASSWORD}"
        update_config_value "REALITY_PUBLIC_KEY" "${REALITY_PUBLIC_KEY}"
        update_config_value "REALITY_PRIVATE_KEY" "${REALITY_PRIVATE_KEY}"
        update_config_value "REALITY_SHORT_ID" "${REALITY_SHORT_ID}"
        update_config_value "SERVER_ADDRESS" "${SERVER_IP}"
        update_config_value "SERVER_IP" "${SERVER_IP}"
        update_config_value "SERVER_PORT" "443"
        
        # Сохраняем версию панели
        if [ -n "$XUI_VERSION" ]; then
            update_config_value "XUI_VERSION" "${XUI_VERSION}"
        else
            update_config_value "XUI_VERSION" "latest"
        fi
        
        echo -e "${GREEN}✅ Все данные успешно сохранены${NC}"
        
        # Автоматическое создание inbound
        echo -e "\n${YELLOW}🔧 Создание VLESS Reality inbound...${NC}"
        
        # Извлекаем API токен из вывода установщика (если есть)
        XUI_API_TOKEN=$(echo "$INSTALL_OUTPUT" | grep -oP '(?<=API Token:\s{3})\S+' | head -1)
        
        if [ -n "$XUI_API_TOKEN" ]; then
            echo -e "${GREEN}✅ API Token извлечен: ${XUI_API_TOKEN:0:20}...${NC}"
            update_config_value "XUI_API_TOKEN" "${XUI_API_TOKEN}"
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
                    
                    # Сохраняем ID в config.yaml
                    update_config_value "INBOUND_ID" "${INBOUND_ID}"
                    
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
                        
                        # Обновляем config.yaml с реальными ключами из inbound
                        update_config_value "REALITY_PUBLIC_KEY" "${ACTUAL_PUBLIC_KEY}"
                        update_config_value "REALITY_SHORT_ID" "${ACTUAL_SHORT_ID}"
                        update_config_value "REALITY_FINGERPRINT" "${ACTUAL_FINGERPRINT}"
                        update_config_value "REALITY_SNI" "${ACTUAL_SNI}"
                        
                        echo -e "${GREEN}✅ Ключи сохранены в config.yaml${NC}"
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
                    update_config_value "INBOUND_ID" "${INBOUND_ID}"
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
        echo -e "   ${YELLOW}${WORK_DIR}/config.yaml${NC}"
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
    
    # Загружаем Reality ключи из config.yaml
    REALITY_PRIVATE_KEY=$(get_config_value "REALITY_PRIVATE_KEY")
    REALITY_PUBLIC_KEY=$(get_config_value "REALITY_PUBLIC_KEY")
    REALITY_SHORT_ID=$(get_config_value "REALITY_SHORT_ID")
    
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
    
    # Получаем API токен и URL из config.yaml
    local API_TOKEN=$(get_config_value "XUI_API_TOKEN")
    local PANEL_URL=$(get_config_value "XUI_URL")
    
    # Пробуем создать через API (приоритет для v3)
    local INBOUND_CREATED_API=false
    if [ -n "$API_TOKEN" ] && [ -n "$PANEL_URL" ]; then
        echo -e "${YELLOW}📤 Создание inbound через API...${NC}"
        
        local API_INBOUND_JSON=$(cat <<APIJSON
{
  "enable": true,
  "remark": "VLESS-Reality-xHTTP",
  "listen": "",
  "port": 443,
  "protocol": "vless",
  "settings": {"clients":[],"decryption":"none","fallbacks":[]},
  "streamSettings": $(echo "$STREAM_SETTINGS_JSON"),
  "sniffing": {"enabled":true,"destOverride":["http","tls","quic","fakedns"],"metadataOnly":false,"routeOnly":false},
  "tag": "inbound-443"
}
APIJSON
)
        local API_RESP=$(curl -sk -w "\n%{http_code}" -X POST "${PANEL_URL%/}/panel/api/inbounds/add" \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${API_TOKEN}" \
            -d "$API_INBOUND_JSON" 2>/dev/null)
        local API_CODE=$(echo "$API_RESP" | tail -1)
        local API_BODY=$(echo "$API_RESP" | head -n-1)
        
        if echo "$API_BODY" | grep -q '"success":true'; then
            INBOUND_ID=$(echo "$API_BODY" | grep -oP '"id":\K\d+' | head -1)
            echo -e "${GREEN}✅ XHTTP Reality inbound создан через API! ID: ${INBOUND_ID}${NC}"
            INBOUND_CREATED_API=true
        else
            echo -e "${YELLOW}⚠ API не сработал (${API_CODE}), пробуем через SQL...${NC}"
        fi
    fi
    
    if [ "$INBOUND_CREATED_API" = false ]; then
    # Проверяем и удаляем существующий inbound через SQL
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
    fi
    fi # конец блока SQL
    
    if [ -n "$INBOUND_ID" ]; then
            echo -e "${GREEN}✅ XHTTP Reality inbound создан успешно!${NC}"
            echo -e "${GREEN}   ID: ${INBOUND_ID}${NC}"
            echo -e "${GREEN}   Порт: 443${NC}"
            echo -e "${GREEN}   Protocol: VLESS${NC}"
            echo -e "${GREEN}   Network: xhttp${NC}"
            echo -e "${GREEN}   Security: reality${NC}"
            
            update_config_value "INBOUND_ID" "${INBOUND_ID}"
            update_config_value "TRANSPORT" "xhttp"
            update_config_value "SECURITY" "reality"
            
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
                
                # Обновляем config.yaml с реальными ключами из inbound
                update_config_value "REALITY_PUBLIC_KEY" "${ACTUAL_PUBLIC_KEY}"
                update_config_value "REALITY_SHORT_ID" "${ACTUAL_SHORT_ID}"
                update_config_value "REALITY_FINGERPRINT" "${ACTUAL_FINGERPRINT}"
                update_config_value "REALITY_SNI" "${ACTUAL_SNI}"
                
                echo -e "${GREEN}✅ Ключи сохранены в config.yaml${NC}"
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
    
    echo -e "${RED}❌ Ошибка создания inbound${NC}"
    return 1
}

# Функция создания TCP Reality inbound
create_tcp_reality_inbound() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Создание TCP Reality Inbound${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    # Загружаем Reality ключи из config.yaml
    REALITY_PRIVATE_KEY=$(get_config_value "REALITY_PRIVATE_KEY")
    REALITY_PUBLIC_KEY=$(get_config_value "REALITY_PUBLIC_KEY")
    REALITY_SHORT_ID=$(get_config_value "REALITY_SHORT_ID")
    
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
    
    # Получаем API токен и URL из config.yaml
    local API_TOKEN=$(get_config_value "XUI_API_TOKEN")
    local PANEL_URL=$(get_config_value "XUI_URL")
    
    # Пробуем создать через API
    local INBOUND_CREATED_API=false
    if [ -n "$API_TOKEN" ] && [ -n "$PANEL_URL" ]; then
        echo -e "${YELLOW}📤 Создание inbound через API...${NC}"
        local API_INBOUND_JSON=$(cat <<APIJSON
{
  "enable": true,
  "remark": "VLESS-Reality-TCP",
  "listen": "",
  "port": 443,
  "protocol": "vless",
  "settings": {"clients":[],"decryption":"none","fallbacks":[]},
  "streamSettings": $(echo "$STREAM_SETTINGS_JSON"),
  "sniffing": {"enabled":true,"destOverride":["http","tls","quic","fakedns"],"metadataOnly":false,"routeOnly":false},
  "tag": "inbound-443"
}
APIJSON
)
        local API_RESP=$(curl -sk -w "\n%{http_code}" -X POST "${PANEL_URL%/}/panel/api/inbounds/add" \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${API_TOKEN}" \
            -d "$API_INBOUND_JSON" 2>/dev/null)
        local API_CODE=$(echo "$API_RESP" | tail -1)
        local API_BODY=$(echo "$API_RESP" | head -n-1)
        
        if echo "$API_BODY" | grep -q '"success":true'; then
            INBOUND_ID=$(echo "$API_BODY" | grep -oP '"id":\K\d+' | head -1)
            echo -e "${GREEN}✅ TCP Reality inbound создан через API! ID: ${INBOUND_ID}${NC}"
            INBOUND_CREATED_API=true
        else
            echo -e "${YELLOW}⚠ API не сработал (${API_CODE}), пробуем через SQL...${NC}"
        fi
    fi
    
    if [ "$INBOUND_CREATED_API" = false ]; then
    EXISTING_INBOUND=$(sqlite3 /etc/x-ui/x-ui.db "SELECT id FROM inbounds WHERE tag='inbound-443' OR remark='VLESS-Reality-TCP';" 2>/dev/null)
    if [ -n "$EXISTING_INBOUND" ]; then
        sqlite3 /etc/x-ui/x-ui.db "DELETE FROM inbounds WHERE tag='inbound-443' OR remark='VLESS-Reality-TCP';" 2>/dev/null
    fi
    SQL_INSERT="INSERT INTO inbounds (user_id, up, down, total, remark, enable, expiry_time, listen, port, protocol, settings, stream_settings, tag, sniffing) VALUES (1, 0, 0, 0, 'VLESS-Reality-TCP', 1, 0, '', 443, 'vless', '${SETTINGS_JSON_ESCAPED}', '${STREAM_SETTINGS_JSON_ESCAPED}', 'inbound-443', '${SNIFFING_JSON_ESCAPED}');"
    set +e
    SQL_RESULT=$(sqlite3 /etc/x-ui/x-ui.db "${SQL_INSERT}" 2>&1)
    SQL_EXIT_CODE=$?
    set -e
    if [ $SQL_EXIT_CODE -eq 0 ]; then
        INBOUND_ID=$(sqlite3 /etc/x-ui/x-ui.db "SELECT id FROM inbounds WHERE remark='VLESS-Reality-TCP' ORDER BY id DESC LIMIT 1;" 2>/dev/null)
    fi
    fi # конец блока SQL
    
    if [ -n "$INBOUND_ID" ]; then
            echo -e "${GREEN}✅ TCP Reality inbound создан успешно!${NC}"
            echo -e "${GREEN}   ID: ${INBOUND_ID}${NC}"
            echo -e "${GREEN}   Порт: 443${NC}"
            echo -e "${GREEN}   Protocol: VLESS${NC}"
            echo -e "${GREEN}   Network: tcp${NC}"
            echo -e "${GREEN}   Security: reality${NC}"
            
            update_config_value "INBOUND_ID" "${INBOUND_ID}"
            update_config_value "TRANSPORT" "tcp"
            update_config_value "SECURITY" "reality"
            
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
                
                # Обновляем config.yaml с реальными ключами из inbound
                update_config_value "REALITY_PUBLIC_KEY" "${ACTUAL_PUBLIC_KEY}"
                update_config_value "REALITY_SHORT_ID" "${ACTUAL_SHORT_ID}"
                update_config_value "REALITY_FINGERPRINT" "${ACTUAL_FINGERPRINT}"
                update_config_value "REALITY_SNI" "${ACTUAL_SNI}"
                
                echo -e "${GREEN}✅ Ключи сохранены в config.yaml${NC}"
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
            
            update_config_value "INBOUND_ID" "${INBOUND_ID}"
            update_config_value "TRANSPORT" "tcp"
            update_config_value "SECURITY" "tls"
            
            # Извлекаем TLS параметры из созданного inbound
            echo -e "${YELLOW}🔑 Извлечение TLS параметров из inbound...${NC}"
            ACTUAL_FINGERPRINT=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.tlsSettings.settings.fingerprint') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
            ACTUAL_ALPN=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.tlsSettings.alpn[0]') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
            
            if [ -n "$ACTUAL_FINGERPRINT" ]; then
                echo -e "${GREEN}✅ TLS параметры извлечены из inbound${NC}"
                echo -e "${GREEN}   Fingerprint: ${ACTUAL_FINGERPRINT}${NC}"
                echo -e "${GREEN}   ALPN: ${ACTUAL_ALPN}${NC}"
                echo -e "${GREEN}   SNI: ${SERVER_IP}${NC}"
                
                # Обновляем config.yaml с реальными параметрами из inbound
                update_config_value "TLS_FINGERPRINT" "${ACTUAL_FINGERPRINT}"
                update_config_value "TLS_ALPN" "${ACTUAL_ALPN}"
                update_config_value "TLS_SNI" "${SERVER_IP}"
                
                echo -e "${GREEN}✅ Параметры сохранены в config.yaml${NC}"
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
        echo -e "${GREEN}0${NC}     - Нет, вернуться в главное меню"
        echo -e "${BLUE}========================================${NC}"
        read -p "Ваш выбор: " create_inbound_choice
        
        if [[ "$create_inbound_choice" == "0" ]]; then
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

# Функция установки 3x-ui панели версии 2.9.4
install_3xui_v294() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Установка 3x-ui Panel v2.9.4${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    # Проверка установлена ли уже панель
    if systemctl is-active --quiet x-ui; then
        echo -e "${YELLOW}⚠ 3x-ui панель уже установлена${NC}"
        if [ -z "$NONINTERACTIVE" ]; then
            read -p "Переустановить? (нажмите Enter для подтверждения или 0 для отмены): " reinstall
        else
            reinstall=""
            echo -e "${BLUE}ℹ️  Автоматический режим: продолжаем переустановку${NC}"
        fi
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
        
        # Удаление из config.yaml
        if [ -f "${WORK_DIR}/config.yaml" ]; then
            echo -e "${YELLOW}🔑 Очистка данных из config.yaml...${NC}"
            if check_yq; then
                local panel_id=$(get_local_panel_id)
                if [ -n "$panel_id" ]; then
                    yq eval -i "del(.panels.${panel_id})" "${WORK_DIR}/config.yaml" 2>/dev/null || true
                    echo -e "${GREEN}✅ Панель удалена из config.yaml${NC}"
                fi
            fi
        fi
        
        echo -e "${GREEN}✅ Старая панель удалена${NC}\n"
    fi
    
    SERVER_IP=$(curl -s -4 https://api4.ipify.org 2>/dev/null || curl -s -4 https://ipv4.icanhazip.com 2>/dev/null || curl -s -4 ifconfig.me 2>/dev/null || echo "")
    
    echo -e "${YELLOW}📦 Загрузка и установка 3x-ui v2.9.4...${NC}\n"
    
    # Запускаем установку с выводом на экран и в файл одновременно
    INSTALL_LOG="/tmp/xui_install_$$.log"
    
    # Передаем пустые ответы (Enter) на все вопросы через stdin для автоматической установки
    # Установщик будет использовать дефолтные значения (случайный порт, логин, пароль, SSL)
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
            
            XUI_SETTINGS=$(echo "n" | timeout 5 x-ui settings 2>/dev/null || echo "")
            
            if [ -n "$XUI_SETTINGS" ]; then
                if [ -z "$XUI_PORT" ]; then
                    XUI_PORT=$(echo "$XUI_SETTINGS" | grep "port:" | awk '{print $2}')
                fi
                # Получаем путь независимо от SSL статуса
                if [ -z "$XUI_PATH" ]; then
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
        # Определяем протокол из вывода установщика
        XUI_ACCESS_URL=$(echo "$INSTALL_OUTPUT" | grep -oP 'Access URL:\s+\K\S+' | tail -1 | sed 's/\x1b\[[0-9;]*m//g')
        
        if [ -n "$XUI_ACCESS_URL" ]; then
            # Используем URL напрямую из установщика
            XUI_URL="$XUI_ACCESS_URL"
            
            # ВАЖНО: Если SSL не установился, принудительно меняем https на http
            if [ "$SSL_SETUP_FAILED" = true ]; then
                XUI_URL=$(echo "$XUI_URL" | sed 's|^https://|http://|')
                echo -e "${YELLOW}⚠️  SSL не установлен, URL изменен на HTTP: ${XUI_URL}${NC}"
            fi
        else
            # Fallback: определяем протокол по наличию сертификата
            local PROTOCOL="http"
            # Проверяем сертификат только если SSL_SETUP_FAILED != true
            if [ "$SSL_SETUP_FAILED" != true ] && [ -f "/root/cert/ip/fullchain.pem" ] && [ -f "/root/cert/ip/privkey.pem" ]; then
                PROTOCOL="https"
            fi
            
            if [ -z "$XUI_PATH" ] || [ "$XUI_PATH" = "/" ]; then
                XUI_URL="${PROTOCOL}://$(format_host_for_url "${SERVER_IP}"):${XUI_PORT}"
            else
                # Добавляем leading slash если нужно
                if [[ "$XUI_PATH" != /* ]]; then
                    XUI_PATH="/${XUI_PATH}"
                fi
                XUI_URL="${PROTOCOL}://$(format_host_for_url "${SERVER_IP}"):${XUI_PORT}${XUI_PATH}"
            fi
        fi
        
        echo -e "\n${GREEN}═══════════════════════════════════════════${NC}"
        echo -e "${GREEN}     Панель 3x-ui успешно установлена!${NC}"
        echo -e "${GREEN}═══════════════════════════════════════════${NC}"
        echo -e "${BLUE}📍 URL панели: ${YELLOW}${XUI_URL}${NC}"
        echo -e "${BLUE}👤 Логин:      ${YELLOW}${XUI_USERNAME}${NC}"
        echo -e "${BLUE}🔑 Пароль:     ${YELLOW}${XUI_PASSWORD}${NC}"
        echo -e "${BLUE}🔌 Порт:       ${YELLOW}${XUI_PORT}${NC}"
        echo -e "${BLUE}📂 WebBasePath:${YELLOW}${XUI_PATH}${NC}"
        
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
        
        # Создание config.yaml если не существует (БЕЗ попытки обновить локальную панель)
        if [ ! -f "config.yaml" ]; then
            echo -e "${YELLOW}📝 Создание config.yaml из примера...${NC}"
            if [ -f "config.yaml.example" ]; then
                cp config.yaml.example config.yaml
                echo -e "${GREEN}✅ config.yaml создан из примера${NC}"
            else
                echo -e "${RED}❌ config.yaml.example не найден${NC}"
            fi
        fi
        
        # Добавляем локальную панель в config.yaml ПЕРЕД сохранением данных
        echo -e "${YELLOW}📝 Добавление локальной панели в config.yaml...${NC}"
        if add_local_panel_to_config "2.9.4" "${XUI_URL}" "${XUI_USERNAME}" "${XUI_PASSWORD}" "${SERVER_IP}"; then
            echo -e "${GREEN}✅ Локальная панель добавлена в config.yaml${NC}"
        else
            echo -e "${RED}❌ Не удалось добавить локальную панель в config.yaml${NC}"
            echo -e "${YELLOW}⚠️  Продолжаем без config.yaml${NC}"
        fi
        
        # Сохранение учетных данных
        echo -e "${YELLOW}💾 Сохранение учетных данных...${NC}"
        update_config_value "XUI_URL" "${XUI_URL}"
        update_config_value "XUI_USERNAME" "${XUI_USERNAME}"
        update_config_value "XUI_PASSWORD" "${XUI_PASSWORD}"
        update_config_value "REALITY_PUBLIC_KEY" "${REALITY_PUBLIC_KEY}"
        update_config_value "REALITY_PRIVATE_KEY" "${REALITY_PRIVATE_KEY}"
        update_config_value "REALITY_SHORT_ID" "${REALITY_SHORT_ID}"
        update_config_value "SERVER_ADDRESS" "${SERVER_IP}"
        update_config_value "SERVER_IP" "${SERVER_IP}"
        update_config_value "SERVER_PORT" "443"
        update_config_value "XUI_VERSION" "2.9.4"
        
        echo -e "${GREEN}✅ Все данные успешно сохранены${NC}"
        
        # Финальное сообщение
        echo -e "\n${GREEN}✅ Установка 3x-ui панели завершена!${NC}\n"
        
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
        if [ -z "$NONINTERACTIVE" ]; then
            read -p "Переустановить? (нажмите Enter для продолжения или 0 для отмены): " reinstall
        else
            reinstall=""
            echo -e "${BLUE}ℹ️  Автоматический режим: продолжаем переустановку${NC}"
        fi
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
    
    # Установка через официальный скрипт
    echo -e "${YELLOW}⚠ Запуск установщика 3x-ui...${NC}"
    echo -e "${YELLOW}⚠ Будет автоматически выбрана база данных SQLite${NC}"

    # Создаем временный файл для сохранения вывода установщика
    INSTALL_OUTPUT=$(mktemp)

    # Автоматически отвечаем на вопросы установщика:
    # 1 - выбор SQLite
    # 4 - пропуск SSL (Skip SSL)
    # Добавляем больше пустых строк для обработки всех возможных вопросов
    printf '1\n4\n\n\n\n\n\n\n\n\n\n' | bash <(curl -Ls https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh) 2>&1 | tee "$INSTALL_OUTPUT"
    
    # Проверка успешности установки
    if systemctl is-active --quiet x-ui; then
        echo -e "\n${GREEN}✓ 3x-ui v3.x установлена успешно${NC}"
        
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
        
        # Парсим вывод установщика для получения данных и очищаем от ANSI кодов
        XUI_USERNAME=$(grep -oP 'Username:\s+\K\S+' "$INSTALL_OUTPUT" | tail -1 | sed 's/\x1b\[[0-9;]*m//g')
        XUI_PASSWORD=$(grep -oP 'Password:\s+\K\S+' "$INSTALL_OUTPUT" | tail -1 | sed 's/\x1b\[[0-9;]*m//g')
        XUI_PORT=$(grep -oP 'Port:\s+\K\d+' "$INSTALL_OUTPUT" | tail -1 | sed 's/\x1b\[[0-9;]*m//g')
        XUI_WEB_BASE_PATH=$(grep -oP 'WebBasePath:\s+\K\S+' "$INSTALL_OUTPUT" | tail -1 | sed 's/\x1b\[[0-9;]*m//g')
        XUI_API_TOKEN=$(grep -oP 'API Token:\s+\K\S+' "$INSTALL_OUTPUT" | tail -1 | sed 's/\x1b\[[0-9;]*m//g')
        
        # Извлекаем версию из вывода установщика (например: "Got x-ui latest version: v3.3.1")
        XUI_VERSION=$(grep -oP 'Got x-ui latest version:\s*v?\K[\d.]+' "$INSTALL_OUTPUT" | head -1 | sed 's/\x1b\[[0-9;]*m//g')
        
        # Удаляем временный файл
        rm -f "$INSTALL_OUTPUT"
        
        # Если версия не извлечена из установщика, пробуем через x-ui version
        if [ -z "$XUI_VERSION" ]; then
            XUI_VERSION=$(x-ui version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1)
        fi
        
        # Если всё ещё не определена, ставим 3.0.0 как fallback
        if [ -z "$XUI_VERSION" ]; then
            XUI_VERSION="3.0.0"
        fi
        
        # Получение IP сервера
        SERVER_IP=$(curl -s -4 https://api4.ipify.org 2>/dev/null || curl -s -4 https://ipv4.icanhazip.com 2>/dev/null || curl -s -4 ifconfig.me 2>/dev/null || echo "YOUR_SERVER_IP")
        
        # Определяем протокол: читаем Access URL из вывода установщика
        # Установщик сам знает был ли настроен SSL
        XUI_ACCESS_URL=$(echo "$INSTALL_OUTPUT" | grep -oP 'Access URL:\s+\K\S+' | tail -1 | sed 's/\x1b\[[0-9;]*m//g')
        
        if [ -n "$XUI_ACCESS_URL" ]; then
            # Используем URL напрямую из установщика
            XUI_URL="$XUI_ACCESS_URL"
            
            # ВАЖНО: Мы пропускаем SSL (выбор 4), поэтому принудительно используем HTTP
            # Если установщик вернул https, меняем на http
            if [[ "$XUI_URL" == https://* ]]; then
                XUI_URL=$(echo "$XUI_URL" | sed 's|^https://|http://|')
                echo -e "${YELLOW}⚠️  SSL пропущен при установке, URL изменен на HTTP: ${XUI_URL}${NC}"
            fi
        else
            # Fallback: используем HTTP так как мы пропустили SSL при установке
            local PROTOCOL="http"
            
            if [ -n "$XUI_WEB_BASE_PATH" ] && [ "$XUI_WEB_BASE_PATH" != "/" ]; then
                if [[ "$XUI_WEB_BASE_PATH" != /* ]]; then
                    XUI_WEB_BASE_PATH="/${XUI_WEB_BASE_PATH}"
                fi
                XUI_URL="${PROTOCOL}://$(format_host_for_url "${SERVER_IP}"):${XUI_PORT}${XUI_WEB_BASE_PATH}"
            else
                XUI_URL="${PROTOCOL}://$(format_host_for_url "${SERVER_IP}"):${XUI_PORT}"
            fi
        fi
        
        # Создание config.yaml если не существует (БЕЗ попытки обновить локальную панель)
        if [ ! -f "config.yaml" ]; then
            echo -e "${YELLOW}📝 Создание config.yaml из примера...${NC}"
            if [ -f "config.yaml.example" ]; then
                cp config.yaml.example config.yaml
                echo -e "${GREEN}✅ config.yaml создан из примера${NC}"
            else
                echo -e "${RED}❌ config.yaml.example не найден${NC}"
            fi
        fi
        
        # Добавляем локальную панель в config.yaml ПЕРЕД сохранением данных
        echo -e "${YELLOW}📝 Добавление локальной панели в config.yaml...${NC}"
        if add_local_panel_to_config "$XUI_VERSION" "$XUI_URL" "$XUI_USERNAME" "$XUI_PASSWORD" "$SERVER_IP"; then
            echo -e "${GREEN}✅ Локальная панель добавлена в config.yaml${NC}"
        else
            echo -e "${RED}❌ Не удалось добавить локальную панель в config.yaml${NC}"
            echo -e "${YELLOW}⚠️  Продолжаем без config.yaml${NC}"
        fi
        
        echo -e "${YELLOW}💾 Сохранение настроек панели...${NC}"
        
        update_config_value "XUI_VERSION" "$XUI_VERSION"
        update_config_value "XUI_URL" "$XUI_URL"
        update_config_value "XUI_USERNAME" "$XUI_USERNAME"
        update_config_value "XUI_PASSWORD" "$XUI_PASSWORD"
        update_config_value "XUI_API_TOKEN" "$XUI_API_TOKEN"
        update_config_value "INBOUND_ID" "1"
        update_config_value "XUI_DB_PATH" "/etc/x-ui/x-ui.db"
        
        # Генерация Reality ключей если их нет
        REALITY_PRIVATE_KEY=$(get_config_value "REALITY_PRIVATE_KEY")
        REALITY_PUBLIC_KEY=$(get_config_value "REALITY_PUBLIC_KEY")
        REALITY_SHORT_ID=$(get_config_value "REALITY_SHORT_ID")
        
        if [ -z "$REALITY_PRIVATE_KEY" ] || [ -z "$REALITY_PUBLIC_KEY" ]; then
            echo -e "${YELLOW}⚠ Генерация Reality ключей...${NC}"
            
            # Метод 1: Через API панели (ПРИОРИТЕТ)
            if [ -n "$XUI_API_TOKEN" ]; then
                if generate_reality_keys_via_api "$XUI_URL" "$XUI_API_TOKEN"; then
                    update_config_value "REALITY_PRIVATE_KEY" "$REALITY_PRIVATE_KEY"
                    update_config_value "REALITY_PUBLIC_KEY" "$REALITY_PUBLIC_KEY"
                    echo -e "${GREEN}✓ Reality ключи сохранены в config.yaml${NC}"
                fi
            fi
            
            # Метод 2: Установка и использование xray (FALLBACK)
            if [ -z "$REALITY_PRIVATE_KEY" ] || [ -z "$REALITY_PUBLIC_KEY" ]; then
                echo -e "${BLUE}ℹ️  Попытка генерации через xray...${NC}"
                
                # Установка xray если не установлен
                if ! command -v xray &> /dev/null; then
                    echo -e "${YELLOW}📦 Установка xray...${NC}"
                    bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install > /dev/null 2>&1
                fi
                
                # Генерация ключей через xray
                if command -v xray &> /dev/null; then
                    REALITY_KEYS=$(xray x25519 2>/dev/null)
                    REALITY_PRIVATE_KEY=$(echo "$REALITY_KEYS" | grep -E "(Private key:|PrivateKey:)" | awk '{print $NF}')
                    REALITY_PUBLIC_KEY=$(echo "$REALITY_KEYS" | grep -E "(Public key:|Password \(PublicKey\):)" | awk '{print $NF}')
                    
                    if [ -n "$REALITY_PRIVATE_KEY" ] && [ -n "$REALITY_PUBLIC_KEY" ]; then
                        update_config_value "REALITY_PRIVATE_KEY" "$REALITY_PRIVATE_KEY"
                        update_config_value "REALITY_PUBLIC_KEY" "$REALITY_PUBLIC_KEY"
                        echo -e "${GREEN}✓ Reality ключи успешно сгенерированы через xray${NC}"
                        echo -e "${BLUE}  Private Key: ${REALITY_PRIVATE_KEY:0:20}...${NC}"
                        echo -e "${BLUE}  Public Key:  ${REALITY_PUBLIC_KEY:0:20}...${NC}"
                    else
                        echo -e "${YELLOW}⚠️  Не удалось извлечь ключи из вывода xray${NC}"
                    fi
                else
                    echo -e "${YELLOW}⚠️  Не удалось установить xray${NC}"
                fi
            fi
            
            # Метод 3: Поиск в стандартных путях (дополнительный fallback)
            if [ -z "$REALITY_PRIVATE_KEY" ] || [ -z "$REALITY_PUBLIC_KEY" ]; then
                echo -e "${BLUE}ℹ️  Поиск xray в стандартных путях...${NC}"
                
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
                            update_config_value "REALITY_PRIVATE_KEY" "$REALITY_PRIVATE_KEY"
                            update_config_value "REALITY_PUBLIC_KEY" "$REALITY_PUBLIC_KEY"
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
                update_config_value "REALITY_SHORT_ID" "$REALITY_SHORT_ID"
                echo -e "${GREEN}✓ Reality Short ID сгенерирован: ${REALITY_SHORT_ID}${NC}"
            fi
        fi
        
        # Сохраняем дополнительные параметры
        update_config_value "SERVER_ADDRESS" "${SERVER_IP}"
        update_config_value "SERVER_IP" "${SERVER_IP}"
        update_config_value "SERVER_PORT" "443"
        
        echo -e "${GREEN}✅ Все данные успешно сохранены${NC}"
        
        # Вывод учетных данных
        echo -e "\n${GREEN}═══════════════════════════════════════════${NC}"
        echo -e "${GREEN}     Панель 3x-ui v3.x успешно установлена!${NC}"
        echo -e "${GREEN}═══════════════════════════════════════════${NC}"
        echo -e "${BLUE}⚠ URL панели:       ${YELLOW}${XUI_URL}${NC}"
        echo -e "${BLUE}⚠ Имя пользователя: ${YELLOW}${XUI_USERNAME}${NC}"
        echo -e "${BLUE}⚠ Пароль:           ${YELLOW}${XUI_PASSWORD}${NC}"
        echo -e "${BLUE}⚠ API Token:        ${YELLOW}${XUI_API_TOKEN}${NC}"
        echo -e "${BLUE}⚠ Версия:           ${YELLOW}${XUI_VERSION}${NC}"
        echo -e "${GREEN}═══════════════════════════════════════════${NC}"
        echo -e "${YELLOW}⚠ ВАЖНО: Сохраните эти данные в безопасном месте!${NC}"
        echo -e "${YELLOW}⚠ API Token необходим для работы бота с панелью v3${NC}"
        echo -e "${YELLOW}⚠ Все данные сохранены в файл config.yaml${NC}\n"
        
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
    
    # Удаление из config.yaml
    if [ -f "${WORK_DIR}/config.yaml" ]; then
        echo -e "${YELLOW}🔑 Очистка данных из config.yaml...${NC}"
        if check_yq; then
            local panel_id=$(get_local_panel_id)
            if [ -n "$panel_id" ]; then
                yq eval -i "del(.panels.${panel_id})" "${WORK_DIR}/config.yaml" 2>/dev/null || true
                echo -e "${GREEN}✅ Панель удалена из config.yaml${NC}"
            fi
        fi
    fi
    
    echo -e "${GREEN}✅ 3x-ui панель полностью удалена!${NC}"
    echo -e "${GREEN}   - Программа удалена${NC}"
    echo -e "${GREEN}   - База данных удалена${NC}"
    echo -e "${GREEN}   - Конфигурация удалена${NC}"
    echo -e "${GREEN}   - Данные из config.yaml очищены${NC}"
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
        echo -e "${YELLOW}Для генерации конфигураций требуется Node.js${NC}"
        echo -e ""
        read -p "Установить Node.js сейчас? (y/n): " install_choice
        
        if [[ "$install_choice" =~ ^[Yy]$ ]]; then
            install_nodejs
            if [ $? -ne 0 ]; then
                echo -e "${RED}❌ Не удалось установить Node.js${NC}"
                return 1
            fi
        else
            echo -e "${YELLOW}Установка отменена${NC}"
            echo -e "${YELLOW}Node.js не установлен. Установите его вручную для работы AWGBOT${NC}"
            return 1
        fi
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
# Функция запуска AWG v1
start_awg_v1() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Запуск AWG v1${NC}"
    echo -e "${BLUE}========================================${NC}"
    
    local container_name="amnezia-awg"
    
    # Проверяем существование контейнера
    if ! docker ps -a --format '{{.Names}}' | grep -q "^${container_name}$"; then
        echo -e "${RED}❌ Контейнер ${container_name} не найден!${NC}"
        echo -e "${YELLOW}AWG v1 не установлен${NC}"
        return 1
    fi
    
    # Проверяем запущен ли контейнер
    if docker ps --format '{{.Names}}' | grep -q "^${container_name}$"; then
        echo -e "${YELLOW}⚠️  Контейнер ${container_name} уже запущен${NC}"
        return 0
    fi
    
    # Запускаем контейнер
    echo -e "${YELLOW}🚀 Запуск контейнера ${container_name}...${NC}"
    if docker start "${container_name}" 2>/dev/null; then
        echo -e "${GREEN}✅ AWG v1 успешно запущен${NC}"
        return 0
    else
        echo -e "${RED}❌ Ошибка запуска контейнера${NC}"
        return 1
    fi
}

# Функция запуска AWG v2
start_awg_v2() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Запуск AWG v2${NC}"
    echo -e "${BLUE}========================================${NC}"
    
    local container_name="amnezia-awg2"
    
    # Проверяем существование контейнера
    if ! docker ps -a --format '{{.Names}}' | grep -q "^${container_name}$"; then
        echo -e "${RED}❌ Контейнер ${container_name} не найден!${NC}"
        echo -e "${YELLOW}AWG v2 не установлен${NC}"
        return 1
    fi
    
    # Проверяем запущен ли контейнер
    if docker ps --format '{{.Names}}' | grep -q "^${container_name}$"; then
        echo -e "${YELLOW}⚠️  Контейнер ${container_name} уже запущен${NC}"
        return 0
    fi
    
    # Запускаем контейнер
    echo -e "${YELLOW}🚀 Запуск контейнера ${container_name}...${NC}"
    if docker start "${container_name}" 2>/dev/null; then
        echo -e "${GREEN}✅ AWG v2 успешно запущен${NC}"
        return 0
    else
        echo -e "${RED}❌ Ошибка запуска контейнера${NC}"
        return 1
    fi
}

# Функция остановки AWG v1
stop_awg_v1() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Остановка AWG v1${NC}"
    echo -e "${BLUE}========================================${NC}"
    
    local container_name="amnezia-awg"
    
    # Проверяем существование контейнера
    if ! docker ps -a --format '{{.Names}}' | grep -q "^${container_name}$"; then
        echo -e "${RED}❌ Контейнер ${container_name} не найден!${NC}"
        echo -e "${YELLOW}AWG v1 не установлен${NC}"
        return 1
    fi
    
    # Проверяем запущен ли контейнер
    if ! docker ps --format '{{.Names}}' | grep -q "^${container_name}$"; then
        echo -e "${YELLOW}⚠️  Контейнер ${container_name} уже остановлен${NC}"
        return 0
    fi
    
    # Останавливаем контейнер
    echo -e "${YELLOW}🛑 Остановка контейнера ${container_name}...${NC}"
    if docker stop "${container_name}" 2>/dev/null; then
        echo -e "${GREEN}✅ AWG v1 успешно остановлен${NC}"
        echo -e "${YELLOW}Для запуска используйте: docker start ${container_name}${NC}"
        return 0
    else
        echo -e "${RED}❌ Ошибка остановки контейнера${NC}"
        return 1
    fi
}

# Функция остановки AWG v2
stop_awg_v2() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Остановка AWG v2${NC}"
    echo -e "${BLUE}========================================${NC}"
    
    local container_name="amnezia-awg2"
    
    # Проверяем существование контейнера
    if ! docker ps -a --format '{{.Names}}' | grep -q "^${container_name}$"; then
        echo -e "${RED}❌ Контейнер ${container_name} не найден!${NC}"
        echo -e "${YELLOW}AWG v2 не установлен${NC}"
        return 1
    fi
    
    # Проверяем запущен ли контейнер
    if ! docker ps --format '{{.Names}}' | grep -q "^${container_name}$"; then
        echo -e "${YELLOW}⚠️  Контейнер ${container_name} уже остановлен${NC}"
        return 0
    fi
    
    # Останавливаем контейнер
    echo -e "${YELLOW}🛑 Остановка контейнера ${container_name}...${NC}"
    if docker stop "${container_name}" 2>/dev/null; then
        echo -e "${GREEN}✅ AWG v2 успешно остановлен${NC}"
        echo -e "${YELLOW}Для запуска используйте: docker start ${container_name}${NC}"
        return 0
    else
        echo -e "${RED}❌ Ошибка остановки контейнера${NC}"
        return 1
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
    echo -e "${GREEN}9)${NC} Запустить AWG v1"
    echo -e "${GREEN}10)${NC} Запустить AWG v2"
    echo -e "${GREEN}11)${NC} Остановить AWG v1"
    echo -e "${GREEN}12)${NC} Остановить AWG v2"
    echo -e "${BLUE}---${NC}"
    echo -e "${YELLOW}XUIBOT:${NC}"
    echo -e "${GREEN}13)${NC} Установка XUIBOT"
    echo -e "${GREEN}14)${NC} Логи XUIBOT"
    echo -e "${GREEN}15)${NC} Пересборка XUIBOT"
    echo -e "${GREEN}16)${NC} Удаление XUIBOT"
    echo -e "${BLUE}---${NC}"
    echo -e "${YELLOW}AWGBOT:${NC}"
    echo -e "${GREEN}17)${NC} Установка AWGBOT"
    echo -e "${GREEN}18)${NC} Логи AWGBOT"
    echo -e "${GREEN}19)${NC} Пересборка AWGBOT"
    echo -e "${GREEN}20)${NC} Удаление AWGBOT"
    echo -e "${BLUE}---${NC}"
    echo -e "${YELLOW}Системные утилиты:${NC}"
    echo -e "${GREEN}21)${NC} Анализ диска и памяти"
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
            NONINTERACTIVE=1; install_3xui_v294; unset NONINTERACTIVE
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
            start_awg_v1
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
            start_awg_v2
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
            stop_awg_v1
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
            stop_awg_v2
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
            install_xuibot
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
            show_xuibot_logs
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
            update_xuibot
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
            remove_xuibot
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
            install_awgbot
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
            show_awgbot_logs
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
            update_awgbot
            ;;
        20)
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
        21)
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
    
    if [ -z "$NONINTERACTIVE" ]; then
        echo -e "\n${YELLOW}Нажмите Enter для продолжения...${NC}"
        read
    fi
done

# ============================================
# CHANGELOG
# ============================================
# 2026-06-09: Добавлена автоматическая синхронизация репозитория (git pull)
#             перед выполнением каждого пункта меню (1-20, 99).
#             При ошибке синхронизации пользователь может продолжить работу
#             или отменить операцию.
# ============================================

