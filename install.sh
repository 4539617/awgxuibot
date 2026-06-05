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
        echo -e "${YELLOW}📝 Создание .env файла с дефолтными значениями...${NC}"
        cat > .env << 'EOF'
# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN=
ADMIN_IDS=

# Server Configuration
SERVER_ADDRESS=
SERVER_IP=
SERVER_PORT=443

# 3x-ui Panel Configuration
XUI_URL=
XUI_USERNAME=
XUI_PASSWORD=
XUI_DB_PATH=/etc/x-ui/x-ui.db
API_TIMEOUT=30

# Reality Configuration
REALITY_PUBLIC_KEY=
REALITY_PRIVATE_KEY=
REALITY_SHORT_ID=
REALITY_SNI=google.com
REALITY_FINGERPRINT=firefox

# Transport Configuration
TRANSPORT=xhttp
SECURITY=reality
XHTTP_MODE=auto
INBOUND_ID=1

# TLS Configuration
TLS_FINGERPRINT=firefox
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

# AWG Configuration
AWG_VERSION=v1
AWG_PORT=51820
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
    
    SERVER_IP=$(curl -s ifconfig.me)
    
    # Генерируем случайный пароль для панели
    GENERATED_PASSWORD=$(openssl rand -base64 16 | tr -d "=+/" | cut -c1-16)
    
    echo -e "${YELLOW}📦 Загрузка и установка 3x-ui...${NC}"
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
    
    # Выводим результат установки
    echo "$INSTALL_OUTPUT"
    
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
        
        echo -e "${GREEN}✅ Настройки панели получены:${NC}"
        echo -e "  Порт: ${YELLOW}${XUI_PORT}${NC}"
        echo -e "  Путь: ${YELLOW}${XUI_PATH}${NC}"
        
        # Формируем URL
        if [ -z "$XUI_PATH" ] || [ "$XUI_PATH" = "/" ]; then
            XUI_URL="http://${SERVER_IP}:${XUI_PORT}/"
        else
            XUI_URL="http://${SERVER_IP}:${XUI_PORT}${XUI_PATH}"
        fi
        
        echo -e "${GREEN}✅ Настройки панели получены:${NC}"
        echo -e "  Порт: ${YELLOW}${XUI_PORT}${NC}"
        echo -e "  Путь: ${YELLOW}${XUI_PATH:-/}${NC}"
        
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
            echo -e "${YELLOW}⚠ Cookie не получен после 3 попыток${NC}"
            echo -e "${YELLOW}HTTP код: ${HTTP_CODE}${NC}"
            echo -e "${YELLOW}Ответ: ${RESPONSE_BODY:0:300}${NC}"
            
            # Пробуем альтернативный метод - через API токен если есть
            if [ -n "$XUI_API_TOKEN" ]; then
                echo -e "${YELLOW}⚠ Пробуем использовать API Token...${NC}"
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
  "streamSettings": "{\n  \"network\": \"xhttp\",\n  \"security\": \"reality\",\n  \"externalProxy\": [],\n  \"realitySettings\": {\n    \"show\": false,\n    \"xver\": 0,\n    \"target\": \"google.com:443\",\n    \"serverNames\": [\n      \"google.com\",\n      \"www.google.com\"\n    ],\n    \"privateKey\": \"REALITY_PRIVATE_KEY_PLACEHOLDER\",\n    \"minClientVer\": \"\",\n    \"maxClientVer\": \"\",\n    \"maxTimediff\": 0,\n    \"shortIds\": [\n      \"REALITY_SHORT_ID_PLACEHOLDER\"\n    ],\n    \"settings\": {\n      \"publicKey\": \"REALITY_PUBLIC_KEY_PLACEHOLDER\",\n      \"fingerprint\": \"edge\",\n      \"serverName\": \"\",\n      \"spiderX\": \"/\"\n    }\n  },\n  \"xhttpSettings\": {\n    \"path\": \"/\",\n    \"host\": \"\",\n    \"headers\": {},\n    \"scMaxBufferedPosts\": 30,\n    \"scMaxEachPostBytes\": \"1000000\",\n    \"scStreamUpServerSecs\": \"20-80\",\n    \"noSSEHeader\": false,\n    \"xPaddingBytes\": \"100-1000\",\n    \"mode\": \"auto\",\n    \"xPaddingObfsMode\": false\n  }\n}",
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
        echo -e "${YELLOW}🔧 Попытка создания inbound через SQL...${NC}"
        
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
    "dest": "google.com:443",
    "serverNames": ["google.com","www.google.com"],
    "privateKey": "${REALITY_PRIVATE_KEY}",
    "minClientVer": "",
    "maxClientVer": "",
    "maxTimediff": 0,
    "shortIds": ["${REALITY_SHORT_ID}"],
    "settings": {
      "publicKey": "${REALITY_PUBLIC_KEY}",
      "fingerprint": "chrome",
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
    "xPaddingObfsMode": false
  }
}
STREAMEOF
)
            
            SNIFFING_JSON='{"enabled":true,"destOverride":["http","tls","quic","fakedns"],"metadataOnly":false,"routeOnly":false}'
            
            # Экранируем JSON для SQL
            SETTINGS_JSON_ESCAPED=$(echo "$SETTINGS_JSON" | sed "s/'/''/g")
            STREAM_SETTINGS_JSON_ESCAPED=$(echo "$STREAM_SETTINGS_JSON" | sed "s/'/''/g")
            SNIFFING_JSON_ESCAPED=$(echo "$SNIFFING_JSON" | sed "s/'/''/g")
            
            # Вставляем inbound в базу данных
            SQL_INSERT="INSERT INTO inbounds (user_id, up, down, total, remark, enable, expiry_time, listen, port, protocol, settings, stream_settings, tag, sniffing) VALUES (1, 0, 0, 0, 'VLESS-Reality-xHTTP', 1, 0, '', 443, 'vless', '${SETTINGS_JSON_ESCAPED}', '${STREAM_SETTINGS_JSON_ESCAPED}', 'inbound-443', '${SNIFFING_JSON_ESCAPED}');"
            
            echo -e "${YELLOW}Выполнение SQL запроса...${NC}"
            SQL_RESULT=$(sqlite3 /etc/x-ui/x-ui.db "${SQL_INSERT}" 2>&1)
            
            if [ $? -eq 0 ]; then
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
                    
                    # Перезапускаем панель для применения изменений
                    echo -e "${YELLOW}🔄 Перезапуск панели для применения изменений...${NC}"
                    systemctl restart x-ui
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
                echo -e "${YELLOW}⚠ Ошибка SQL: ${SQL_RESULT}${NC}"
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
        
        echo -e "\n${GREEN}✅ 3x-ui панель успешно установлена!${NC}"
        echo -e "${BLUE}========================================${NC}"
        echo -e "${GREEN}📋 Информация о панели:${NC}"
        echo -e "  URL: ${YELLOW}${XUI_URL}${NC}"
        echo -e "  Порт: ${YELLOW}${XUI_PORT}${NC}"
        echo -e "  Путь: ${YELLOW}${XUI_PATH:-/}${NC}"
        echo -e "${BLUE}========================================${NC}"
        echo -e "${GREEN}🔐 Учетные данные для входа:${NC}"
        echo -e "  Username: ${YELLOW}${XUI_USERNAME}${NC}"
        echo -e "  Password: ${YELLOW}${XUI_PASSWORD}${NC}"
        echo -e "${BLUE}========================================${NC}"
        echo -e "${RED}⚠ ВАЖНО: Сохраните эти данные!${NC}"
        echo -e "${YELLOW}Пароль был сгенерирован автоматически и установлен в панель${NC}"
        echo -e "${BLUE}========================================${NC}"
        echo -e "${GREEN}🔑 Reality ключи:${NC}"
        echo -e "  Public Key: ${YELLOW}${REALITY_PUBLIC_KEY}${NC}"
        echo -e "  Private Key: ${YELLOW}${REALITY_PRIVATE_KEY}${NC}"
        echo -e "  Short ID: ${YELLOW}${REALITY_SHORT_ID}${NC}"
        echo -e "${BLUE}========================================${NC}"
        echo -e "${GREEN}💾 Все данные сохранены в:${NC}"
        echo -e "  ${YELLOW}${WORK_DIR}/.env${NC}"
        echo -e "${BLUE}========================================${NC}"
        echo -e "${YELLOW}📝 Следующие шаги:${NC}"
        if [ "$INBOUND_CREATED" = true ]; then
            echo -e "  1. ${GREEN}✓${NC} Inbound создан автоматически"
            echo -e "  2. Откройте панель: ${YELLOW}${XUI_URL}${NC}"
            echo -e "  3. Проверьте inbound в разделе Inbounds"
            echo -e "  4. Запустите бот (пункт 1 в меню)"
        else
            echo -e "  1. Откройте панель в браузере: ${YELLOW}${XUI_URL}${NC}"
            echo -e "  2. Войдите с учетными данными из вывода установщика выше"
            echo -e "  3. Создайте inbound вручную с настройками:"
            echo -e "     - Protocol: ${YELLOW}VLESS${NC}"
            echo -e "     - Port: ${YELLOW}443${NC}"
            echo -e "     - Network: ${YELLOW}xhttp${NC}"
            echo -e "     - Security: ${YELLOW}reality${NC}"
            echo -e "     - Private Key: ${YELLOW}${REALITY_PRIVATE_KEY}${NC}"
            echo -e "     - Short ID: ${YELLOW}${REALITY_SHORT_ID}${NC}"
            echo -e "     - SNI: ${YELLOW}google.com${NC}"
            echo -e "  4. После создания inbound запустите бот (пункт 1 в меню)"
        fi
        echo -e "${BLUE}========================================${NC}"
        echo -e "${RED}⚠ ВАЖНО: Сохраните эти данные в надежном месте!${NC}"
        echo -e "${BLUE}========================================${NC}"
        
        echo -e "\n${BLUE}========================================${NC}"
        echo -e "${GREEN}   ВАШИ ДАННЫЕ ДЛЯ ВХОДА${NC}"
        echo -e "${BLUE}========================================${NC}"
        echo -e "${GREEN}URL панели:${NC} ${YELLOW}${XUI_URL}${NC}"
        echo -e "${GREEN}Username:${NC}   ${YELLOW}${XUI_USERNAME}${NC}"
        echo -e "${GREEN}Password:${NC}   ${YELLOW}${XUI_PASSWORD}${NC}"
        echo -e "${BLUE}========================================${NC}"
        echo -e "${YELLOW}💾 Также эти данные сохранены в:${NC}"
        echo -e "   ${YELLOW}${WORK_DIR}/.env${NC}"
        echo -e "${BLUE}========================================${NC}"
        
        echo -e "\n${GREEN}✅ Установка 3x-ui панели завершена!${NC}"
        echo -e "${YELLOW}Нажмите Enter для возврата в меню...${NC}"
        read
    else
        echo -e "\n${RED}❌ Ошибка установки 3x-ui панели${NC}"
        echo -e "${YELLOW}Нажмите Enter для возврата в меню...${NC}"
        read
    fi
}

# Функция удаления 3x-ui панели
remove_3xui() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Удаление 3x-ui Panel${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    read -p "⚠️  Вы уверены что хотите удалить 3x-ui панель? (нажмите Enter для подтверждения или n для отмены): " confirm
    
    if [[ "$confirm" =~ ^[Nn]$ ]]; then
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
