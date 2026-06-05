#!/bin/bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Константы
WORK_DIR="/opt/awgxuibot"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   awgxuibot Installer${NC}"
echo -e "${BLUE}   NetCrazy + XUI Management${NC}"
echo -e "${BLUE}========================================${NC}\n"

# Проверка прав root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}❌ Запустите с правами root (sudo ./install.sh)${NC}"
    exit 1
fi

# Проверка наличия файлов проекта
if [ ! -f "docker-compose.yml" ] || [ ! -f "Dockerfile" ] || [ ! -f "package.json" ]; then
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
        read -p "Переместить файлы в ${WORK_DIR}? (y/n): " move_files
        if [[ "$move_files" =~ ^[Yy]$ ]]; then
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
}

# Функция создания директорий
create_directories() {
    echo -e "${GREEN}📁 Создание директорий...${NC}"
    mkdir -p output logs data
}

# Функция создания .env если не существует
create_env_if_not_exists() {
    if [ ! -f ".env" ]; then
        echo -e "${YELLOW}📝 Создание .env файла...${NC}"
        touch .env
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
}

# Функция получения значения из .env
get_env_value() {
    local key=$1
    grep "^${key}=" .env 2>/dev/null | cut -d'=' -f2 | head -1
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
    update_env_value "TLS_FINGERPRINT" "firefox"
    update_env_value "TLS_ALPN" "http/1.1"
    
    # Reality статические параметры
    update_env_value "REALITY_SNI" "google.com"
    update_env_value "REALITY_FINGERPRINT" "firefox"
    
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
    
    TELEGRAM_BOT_TOKEN=$(get_env_value "TELEGRAM_BOT_TOKEN")
    if [ -z "$TELEGRAM_BOT_TOKEN" ]; then
        read -p "Введите TELEGRAM_BOT_TOKEN: " TELEGRAM_BOT_TOKEN
        update_env_value "TELEGRAM_BOT_TOKEN" "$TELEGRAM_BOT_TOKEN"
    else
        echo -e "TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN:0:10}... ${GREEN}✓${NC}"
    fi
    
    ADMIN_IDS=$(get_env_value "ADMIN_IDS")
    if [ -z "$ADMIN_IDS" ]; then
        read -p "Введите ADMIN_IDS (ID администраторов через запятую): " ADMIN_IDS
        update_env_value "ADMIN_IDS" "$ADMIN_IDS"
    else
        echo -e "ADMIN_IDS: $ADMIN_IDS ${GREEN}✓${NC}"
    fi
    
    # ==================== Автоматическое заполнение ====================
    echo -e "\n${GREEN}🔧 Автоматическое заполнение параметров...${NC}\n"
    
    # IP сервера
    update_env_value "SERVER_ADDRESS" "$SERVER_IP"
    update_env_value "SERVER_IP" "$SERVER_IP"
    echo -e "SERVER_ADDRESS: $SERVER_IP ${GREEN}✓${NC}"
    echo -e "SERVER_IP: $SERVER_IP ${GREEN}✓${NC}"
    
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
    
    echo -e "XUI_URL: $XUI_URL ${GREEN}✓${NC}"
    echo -e "XUI_USERNAME: $XUI_USERNAME ${GREEN}✓${NC}"
    echo -e "XUI_PASSWORD: ******** ${GREEN}✓${NC}"
    echo -e "REALITY_PUBLIC_KEY: ${REALITY_PUBLIC_KEY:0:20}... ${GREEN}✓${NC}"
    echo -e "REALITY_SHORT_ID: $REALITY_SHORT_ID ${GREEN}✓${NC}"
    
    # Устанавливаем фиксированные значения
    update_env_value "TRANSPORT" "xhttp"
    update_env_value "SECURITY" "reality"
    update_env_value "INBOUND_ID" "1"
    
    echo -e "\nTRANSPORT: xhttp ${GREEN}✓${NC}"
    echo -e "SECURITY: reality ${GREEN}✓${NC}"
    echo -e "INBOUND_ID: 1 ${GREEN}✓${NC}"
    
    echo -e "\n${GREEN}✅ Все параметры настроены!${NC}"
}

# Функция установки бота
install_bot() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Установка awgxuibot${NC}"
    echo -e "${BLUE}   NetCrazy + XUI Management${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    # Интерактивная настройка параметров
    interactive_setup
    
    # Остановка старых контейнеров
    echo -e "\n${YELLOW}🛑 Остановка старых контейнеров...${NC}"
    docker stop netcrazybot xuibot 2>/dev/null || true
    docker rm netcrazybot xuibot 2>/dev/null || true
    
    # Проверка docker-compose.yml
    echo -e "\n${YELLOW}🔍 Проверка конфигурации...${NC}"
    if ! docker compose config > /dev/null 2>&1; then
        echo -e "${RED}❌ Ошибка в docker-compose.yml${NC}"
        echo -e "${YELLOW}Запуск диагностики:${NC}"
        docker compose config
        exit 1
    fi
    echo -e "${GREEN}✅ Конфигурация корректна${NC}"
    
    # Запуск обоих ботов
    echo -e "\n${YELLOW}🐳 Сборка и запуск объединенного бота...${NC}"
    echo -e "${BLUE}Это может занять несколько минут...${NC}\n"
    
    if ! docker compose up -d --build; then
        echo -e "\n${RED}❌ Ошибка при запуске контейнеров${NC}"
        echo -e "${YELLOW}Проверьте логи:${NC}"
        echo -e "  docker compose logs"
        exit 1
    fi
    
    # Проверка
    echo -e "\n${YELLOW}⏳ Ожидание запуска контейнеров...${NC}"
    sleep 5
    
    # Проверка статуса контейнеров
    NETCRAZY_STATUS=$(docker ps --filter name=netcrazybot --format "{{.Status}}" 2>/dev/null || echo "not found")
    XUI_STATUS=$(docker ps --filter name=xuibot --format "{{.Status}}" 2>/dev/null || echo "not found")
    
    echo -e "\n${GREEN}✅ awgxuibot установлен!${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo -e "${GREEN}📊 Статус контейнеров:${NC}"
    
    if [[ "$NETCRAZY_STATUS" == *"Up"* ]]; then
        echo -e "  NetCrazyBot: ${GREEN}✓ Работает${NC}"
    else
        echo -e "  NetCrazyBot: ${RED}✗ Не запущен ($NETCRAZY_STATUS)${NC}"
    fi
    
    if [[ "$XUI_STATUS" == *"Up"* ]]; then
        echo -e "  XUIBot: ${GREEN}✓ Работает${NC}"
    else
        echo -e "  XUIBot: ${RED}✗ Не запущен ($XUI_STATUS)${NC}"
    fi
    
    echo -e "\n${YELLOW}📋 Логи NetCrazyBot (последние 15 строк):${NC}"
    docker logs --tail=15 netcrazybot 2>&1 || echo -e "${RED}Не удалось получить логи${NC}"
    
    echo -e "\n${YELLOW}📋 Логи XUIBot (последние 15 строк):${NC}"
    docker logs --tail=15 xuibot 2>&1 || echo -e "${RED}Не удалось получить логи${NC}"
    
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${GREEN}💡 Полезные команды:${NC}"
    echo -e "  Логи: ${YELLOW}docker logs -f netcrazybot${NC}"
    echo -e "  Логи: ${YELLOW}docker logs -f xuibot${NC}"
    echo -e "  Статус: ${YELLOW}docker ps${NC}"
    echo -e "  Перезапуск: ${YELLOW}docker compose restart${NC}"
}

# Функция показа логов
show_logs() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Логи Бота${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    echo -e "${YELLOW}📋 Логи NetCrazyBot (последние 30 строк):${NC}"
    docker logs --tail=30 netcrazybot 2>/dev/null || echo -e "${RED}Контейнер netcrazybot не запущен${NC}"
    
    echo -e "\n${YELLOW}📋 Логи XUIBot (последние 30 строк):${NC}"
    docker logs --tail=30 xuibot 2>/dev/null || echo -e "${RED}Контейнер xuibot не запущен${NC}"
}

# Функция обновления бота
update_bot() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Обновление awgxuibot${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    echo -e "${YELLOW}🔄 Обновление бота...${NC}"
    
    # Остановка контейнеров
    echo -e "${YELLOW}🛑 Остановка контейнеров...${NC}"
    docker stop netcrazybot xuibot 2>/dev/null || true
    docker rm netcrazybot xuibot 2>/dev/null || true
    
    # Пересборка образов
    echo -e "${YELLOW}🐳 Пересборка образов...${NC}"
    docker compose build --no-cache
    
    # Запуск
    echo -e "${YELLOW}🚀 Запуск обновленных контейнеров...${NC}"
    docker compose up -d
    
    sleep 5
    echo -e "\n${GREEN}✅ Бот обновлен!${NC}"
    echo -e "${GREEN}📊 Статус контейнеров:${NC}"
    docker ps --filter name=netcrazybot
    docker ps --filter name=xuibot
}

# Функция удаления бота
remove_bot() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Удаление awgxuibot${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    read -p "⚠️  Вы уверены что хотите удалить бот? (y/n): " confirm
    
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Отменено${NC}"
        return
    fi
    
    echo -e "${YELLOW}🗑️  Удаление бота...${NC}"
    
    # Остановка и удаление контейнеров
    echo -e "${YELLOW}🛑 Остановка контейнеров...${NC}"
    docker stop netcrazybot xuibot 2>/dev/null || true
    docker rm netcrazybot xuibot 2>/dev/null || true
    
    # Удаление образов
    echo -e "${YELLOW}🗑️  Удаление образов...${NC}"
    docker rmi netcrazexuibot-netcrazybot 2>/dev/null || true
    docker rmi netcrazexuibot-xuibot 2>/dev/null || true
    
    echo -e "${GREEN}✅ Бот удален!${NC}"
}

# Функция удаления AWG v1
remove_awg_v1() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Удаление AWG v1${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    read -p "⚠️  Вы уверены что хотите удалить AWG v1 сервер? (y/n): " confirm
    
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Отменено${NC}"
        return
    fi
    
    echo -e "${YELLOW}🗑️  Удаление AWG v1...${NC}"
    
    # Остановка и удаление контейнера
    docker stop amnezia-awg 2>/dev/null || true
    docker rm amnezia-awg 2>/dev/null || true
    
    # Удаление конфигурации
    if [ -d "/opt/amnezia/amnezia-awg" ]; then
        rm -rf /opt/amnezia/amnezia-awg
        echo -e "${GREEN}✅ Конфигурация AWG v1 удалена${NC}"
    fi
    
    echo -e "${GREEN}✅ AWG v1 удален!${NC}"
}

# Функция удаления AWG v2
remove_awg_v2() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Удаление AWG v2${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    read -p "⚠️  Вы уверены что хотите удалить AWG v2 сервер? (y/n): " confirm
    
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Отменено${NC}"
        return
    fi
    
    echo -e "${YELLOW}🗑️  Удаление AWG v2...${NC}"
    
    # Остановка и удаление контейнера
    docker stop amnezia-awg2 2>/dev/null || true
    docker rm amnezia-awg2 2>/dev/null || true
    
    # Удаление конфигурации
    if [ -d "/opt/amnezia/amnezia-awg2" ]; then
        rm -rf /opt/amnezia/amnezia-awg2
        echo -e "${GREEN}✅ Конфигурация AWG v2 удалена${NC}"
    fi
    
    echo -e "${GREEN}✅ AWG v2 удален!${NC}"
}

# Функция удаления всего
remove_all() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Удалить ВСЁ${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    echo -e "${RED}⚠️  ВНИМАНИЕ! Это удалит:${NC}"
    echo -e "  - awgxuibot (NetCrazy + XUI)"
    echo -e "  - 3x-ui панель"
    echo -e "  - AWG v1 сервер"
    echo -e "  - AWG v2 сервер"
    echo -e "  - Все конфигурации и данные"
    echo -e ""
    read -p "Вы уверены? (y/n): " confirm
    
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Отменено${NC}"
        return
    fi
    
    echo -e "${YELLOW}🗑️  Удаление всех компонентов...${NC}"
    
    # Остановка всех контейнеров
    echo -e "${YELLOW}🛑 Остановка контейнеров...${NC}"
    docker stop netcrazybot xuibot amnezia-awg amnezia-awg2 2>/dev/null || true
    docker rm netcrazybot xuibot amnezia-awg amnezia-awg2 2>/dev/null || true
    
    # Удаление образов
    echo -e "${YELLOW}🗑️  Удаление образов...${NC}"
    docker rmi netcrazexuibot-netcrazybot netcrazexuibot-xuibot 2>/dev/null || true
    
    # Удаление конфигураций AWG
    echo -e "${YELLOW}🗑️  Удаление конфигураций AWG...${NC}"
    rm -rf /opt/amnezia/amnezia-awg 2>/dev/null || true
    rm -rf /opt/amnezia/amnezia-awg2 2>/dev/null || true
    
    # Удаление 3x-ui панели
    echo -e "${YELLOW}🗑️  Удаление 3x-ui панели...${NC}"
    systemctl stop x-ui 2>/dev/null || true
    systemctl disable x-ui 2>/dev/null || true
    rm -rf /usr/local/x-ui 2>/dev/null || true
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

# Функция генерации случайного пароля со спецсимволами
generate_random_password() {
    local length=$1
    local chars='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*'
    local result=''
    for i in $(seq 1 $length); do
        result="${result}${chars:RANDOM%${#chars}:1}"
    done
    echo "$result"
}

# Функция установки 3x-ui панели
install_3xui() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Установка 3x-ui Panel${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    # Проверка установлена ли уже панель
    if systemctl is-active --quiet x-ui; then
        echo -e "${YELLOW}⚠ 3x-ui панель уже установлена${NC}"
        read -p "Переустановить? (y/n): " reinstall
        if [[ ! "$reinstall" =~ ^[Yy]$ ]]; then
            echo -e "${YELLOW}Отменено${NC}"
            return
        fi
    fi
    
    # Генерация случайных параметров
    XUI_USERNAME=$(generate_random_string 10)
    XUI_PASSWORD=$(generate_random_password 10)
    XUI_PORT=$((RANDOM % 55535 + 10000))  # Случайный порт от 10000 до 65535
    XUI_PATH="/$(generate_random_string 18)"  # Случайный путь
    SERVER_IP=$(curl -s ifconfig.me)
    XUI_URL="https://${SERVER_IP}:${XUI_PORT}${XUI_PATH}"
    
    echo -e "${YELLOW}🔐 Генерация учетных данных...${NC}"
    echo -e "${GREEN}Логин: ${YELLOW}${XUI_USERNAME}${NC}"
    echo -e "${GREEN}Пароль: ${YELLOW}${XUI_PASSWORD}${NC}"
    echo -e "${GREEN}Порт: ${YELLOW}${XUI_PORT}${NC}"
    echo -e ""
    
    echo -e "${YELLOW}📦 Загрузка установочного скрипта 3x-ui...${NC}"
    
    # Установка в silent режиме
    if bash <(curl -Ls https://raw.githubusercontent.com/MHSanaei/3x-ui/master/install.sh) << EOF
y
${XUI_USERNAME}
${XUI_PASSWORD}
${XUI_PORT}
${XUI_PATH}
EOF
    then
        # Ожидание запуска панели
        echo -e "${YELLOW}⏳ Ожидание запуска панели...${NC}"
        sleep 5
        
        # Генерация Reality ключей
        echo -e "${YELLOW}🔑 Генерация Reality ключей...${NC}"
        
        # Установка xray если не установлен
        if ! command -v xray &> /dev/null; then
            echo -e "${YELLOW}📦 Установка xray...${NC}"
            bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
        fi
        
        # Генерация ключей Reality
        REALITY_KEYS=$(xray x25519)
        REALITY_PRIVATE_KEY=$(echo "$REALITY_KEYS" | grep "Private key:" | awk '{print $3}')
        REALITY_PUBLIC_KEY=$(echo "$REALITY_KEYS" | grep "Public key:" | awk '{print $3}')
        
        # Генерация Short IDs
        REALITY_SHORT_ID=$(openssl rand -hex 8)
        
        # Генерация mldsa65 ключей
        echo -e "${YELLOW}🔑 Генерация mldsa65 ключей...${NC}"
        MLDSA65_SEED=$(openssl rand -base64 32)
        MLDSA65_VERIFY=$(openssl rand -base64 32)
        
        # Создание inbound через API
        echo -e "${YELLOW}🔧 Создание inbound xhttp + reality...${NC}"
        
        # Получение cookie для авторизации
        COOKIE=$(curl -s -X POST "http://localhost:${XUI_PORT}/login" \
            -H "Content-Type: application/x-www-form-urlencoded" \
            -d "username=${XUI_USERNAME}&password=${XUI_PASSWORD}" \
            -c - | grep "session" | awk '{print $7}')
        
        # Создание inbound
        INBOUND_JSON=$(cat <<EOF
{
  "enable": true,
  "port": 443,
  "protocol": "vless",
  "settings": {
    "clients": [],
    "decryption": "none",
    "fallbacks": []
  },
  "streamSettings": {
    "network": "xhttp",
    "security": "reality",
    "realitySettings": {
      "show": false,
      "dest": "google.com:443",
      "xver": 0,
      "serverNames": ["google.com", "www.google.com"],
      "privateKey": "${REALITY_PRIVATE_KEY}",
      "shortIds": ["${REALITY_SHORT_ID}"],
      "mldsa65Seed": "${MLDSA65_SEED}",
      "mldsa65Verify": "${MLDSA65_VERIFY}"
    },
    "xhttpSettings": {
      "mode": "auto"
    }
  },
  "sniffing": {
    "enabled": true,
    "destOverride": ["http", "tls"]
  },
  "remark": "xhttp-reality-auto"
}
EOF
)
        
        curl -s -X POST "http://localhost:${XUI_PORT}/panel/api/inbounds/add" \
            -H "Content-Type: application/json" \
            -H "Cookie: session=${COOKIE}" \
            -d "${INBOUND_JSON}" > /dev/null
        
        echo -e "${GREEN}✅ Inbound создан успешно!${NC}"
        
        # Создание .env файла с учетными данными 3x-ui
        create_env_if_not_exists
        
        echo -e "${YELLOW}💾 Сохранение учетных данных в .env...${NC}"
        update_env_value "XUI_URL" "${XUI_URL}"
        update_env_value "XUI_USERNAME" "${XUI_USERNAME}"
        update_env_value "XUI_PASSWORD" "${XUI_PASSWORD}"
        update_env_value "REALITY_PUBLIC_KEY" "${REALITY_PUBLIC_KEY}"
        update_env_value "REALITY_SHORT_ID" "${REALITY_SHORT_ID}"
        
        echo -e "\n${GREEN}✅ 3x-ui панель успешно установлена!${NC}"
        echo -e "${BLUE}========================================${NC}"
        echo -e "${GREEN}📋 Информация о панели:${NC}"
        echo -e "  URL: ${YELLOW}${XUI_URL}${NC}"
        echo -e "  Логин: ${YELLOW}${XUI_USERNAME}${NC}"
        echo -e "  Пароль: ${YELLOW}${XUI_PASSWORD}${NC}"
        echo -e "${BLUE}========================================${NC}"
        echo -e "${GREEN}🔑 Reality ключи:${NC}"
        echo -e "  Public Key: ${YELLOW}${REALITY_PUBLIC_KEY}${NC}"
        echo -e "  Short ID: ${YELLOW}${REALITY_SHORT_ID}${NC}"
        echo -e "${BLUE}========================================${NC}"
        echo -e "${GREEN}💾 Все данные сохранены в:${NC}"
        echo -e "  ${YELLOW}${WORK_DIR}/.env${NC}"
        echo -e "${RED}⚠ ВАЖНО: Сохраните эти данные в надежном месте!${NC}"
        echo -e "${BLUE}========================================${NC}"
    else
        echo -e "\n${RED}❌ Ошибка установки 3x-ui панели${NC}"
    fi
}

# Функция удаления 3x-ui панели
remove_3xui() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Удаление 3x-ui Panel${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    read -p "⚠️  Вы уверены что хотите удалить 3x-ui панель? (y/n): " confirm
    
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Отменено${NC}"
        return
    fi
    
    echo -e "${YELLOW}🗑️  Удаление 3x-ui панели...${NC}"
    
    # Остановка сервиса
    systemctl stop x-ui 2>/dev/null || true
    systemctl disable x-ui 2>/dev/null || true
    
    # Удаление файлов
    rm -rf /usr/local/x-ui 2>/dev/null || true
    rm -f /etc/systemd/system/x-ui.service 2>/dev/null || true
    systemctl daemon-reload
    
    echo -e "${GREEN}✅ 3x-ui панель удалена!${NC}"
}

# Функция установки AWG v1
install_awg_v1() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Установка AWG v1${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    # Проверка что бот установлен
    if ! docker ps --filter name=netcrazybot --format "{{.Names}}" | grep -q netcrazybot; then
        echo -e "${RED}❌ NetCrazyBot не установлен!${NC}"
        echo -e "${YELLOW}Сначала установите бот (пункт 1)${NC}"
        return
    fi
    
    read -p "Введите порт для AWG v1 (по умолчанию 51820): " AWG_PORT
    AWG_PORT=${AWG_PORT:-51820}
    
    echo -e "${YELLOW}🔧 Установка AWG v1 на порту $AWG_PORT...${NC}"
    
    # Запускаем установку через бот
    docker exec netcrazybot node -e "
    import('./src/awgInstaller.js').then(async (module) => {
        const result = await module.installServer('v1', $AWG_PORT, (msg) => console.log(msg));
        if (result.success) {
            console.log('✅ AWG v1 установлен успешно!');
            console.log('Порт:', result.port);
            console.log('Путь к конфигурации:', result.configPath);
            process.exit(0);
        } else {
            console.error('❌ Ошибка установки:', result.error);
            process.exit(1);
        }
    }).catch(err => {
        console.error('❌ Ошибка:', err.message);
        process.exit(1);
    });
    "
    
    if [ $? -eq 0 ]; then
        echo -e "\n${GREEN}✅ AWG v1 успешно установлен!${NC}"
    else
        echo -e "\n${RED}❌ Ошибка установки AWG v1${NC}"
    fi
}

# Функция установки AWG v2
install_awg_v2() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Установка AWG v2${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    # Проверка что бот установлен
    if ! docker ps --filter name=netcrazybot --format "{{.Names}}" | grep -q netcrazybot; then
        echo -e "${RED}❌ NetCrazyBot не установлен!${NC}"
        echo -e "${YELLOW}Сначала установите бот (пункт 1)${NC}"
        return
    fi
    
    read -p "Введите порт для AWG v2 (по умолчанию 51821): " AWG_PORT
    AWG_PORT=${AWG_PORT:-51821}
    
    echo -e "${YELLOW}🔧 Установка AWG v2 на порту $AWG_PORT...${NC}"
    
    # Запускаем установку через бот
    docker exec netcrazybot node -e "
    import('./src/awgInstaller.js').then(async (module) => {
        const result = await module.installServer('v2', $AWG_PORT, (msg) => console.log(msg));
        if (result.success) {
            console.log('✅ AWG v2 установлен успешно!');
            console.log('Порт:', result.port);
            console.log('Путь к конфигурации:', result.configPath);
            process.exit(0);
        } else {
            console.error('❌ Ошибка установки:', result.error);
            process.exit(1);
        }
    }).catch(err => {
        console.error('❌ Ошибка:', err.message);
        process.exit(1);
    });
    "
    
    if [ $? -eq 0 ]; then
        echo -e "\n${GREEN}✅ AWG v2 успешно установлен!${NC}"
    else
        echo -e "\n${RED}❌ Ошибка установки AWG v2${NC}"
    fi
}

# Главное меню
show_menu() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Выберите действие:${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo -e "${GREEN}1)${NC} Установка Бота"
    echo -e "${GREEN}2)${NC} Логи Бота"
    echo -e "${GREEN}3)${NC} Обновление Бота"
    echo -e "${GREEN}4)${NC} Удаление Бота"
    echo -e "${BLUE}---${NC}"
    echo -e "${GREEN}5)${NC} Установка 3x-ui Panel"
    echo -e "${GREEN}6)${NC} Удаление 3x-ui Panel"
    echo -e "${BLUE}---${NC}"
    echo -e "${GREEN}7)${NC} Установка AWG v1"
    echo -e "${GREEN}8)${NC} Установка AWG v2"
    echo -e "${GREEN}9)${NC} Удаление AWG v1"
    echo -e "${GREEN}10)${NC} Удаление AWG v2"
    echo -e "${BLUE}---${NC}"
    echo -e "${GREEN}11)${NC} Удалить ВСЁ (AWG + Бот + 3x-ui)"
    echo -e "${GREEN}0)${NC} Выход"
    echo -e "${BLUE}========================================${NC}"
}

# Основной цикл
install_docker
create_directories

while true; do
    show_menu
    read -p "Введите номер: " choice
    
    case $choice in
        1)
            install_bot
            ;;
        2)
            show_logs
            ;;
        3)
            update_bot
            ;;
        4)
            remove_bot
            ;;
        5)
            install_3xui
            ;;
        6)
            remove_3xui
            ;;
        7)
            install_awg_v1
            ;;
        8)
            install_awg_v2
            ;;
        9)
            remove_awg_v1
            ;;
        10)
            remove_awg_v2
            ;;
        11)
            remove_all
            ;;
        0)
            echo -e "\n${GREEN}👋 До свидания!${NC}"
            exit 0
            ;;
        *)
            echo -e "${RED}❌ Неверный выбор. Попробуйте снова.${NC}"
            ;;
    esac
    
    echo -e "\n${YELLOW}Нажмите Enter для продолжения...${NC}"
    read
done

# Made with Bob
