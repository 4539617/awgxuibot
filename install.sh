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

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   awgbot + xuibot Installer${NC}"
echo -e "${BLUE}   AWG + XUI Management${NC}"
echo -e "${BLUE}========================================${NC}\n"

# Проверка прав root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}❌ Запустите с правами root (sudo ./install.sh)${NC}"
    exit 1
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
# Telegram Bot Configuration
XUI_BOT_TOKEN=
AWG_BOT_TOKEN=
ADMIN_IDS=

# Server Configuration
SERVER_ADDRESS=${SERVER_IP}
SERVER_IP=${SERVER_IP}
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
    
    # Остановка старых контейнеров
    echo -e "\n${YELLOW}🛑 Остановка старых контейнеров...${NC}"
    docker stop xuibot 2>/dev/null || true
    docker rm xuibot 2>/dev/null || true
    
    # Проверка docker-compose.xuibot.yml
    echo -e "\n${YELLOW}🔍 Проверка конфигурации...${NC}"
    if ! docker compose -f docker-compose.xuibot.yml config > /dev/null 2>&1; then
        echo -e "${RED}❌ Ошибка в docker-compose.xuibot.yml${NC}"
        echo -e "${YELLOW}Запуск диагностики:${NC}"
        docker compose -f docker-compose.xuibot.yml config
        exit 1
    fi
    echo -e "${GREEN}✅ Конфигурация корректна${NC}"
    
    # Запуск XUIBot
    echo -e "\n${YELLOW}🐳 Сборка и запуск XUIBot...${NC}"
    echo -e "${BLUE}Это может занять несколько минут...${NC}\n"
    
    if ! docker compose -f docker-compose.xuibot.yml up -d --build; then
        echo -e "\n${RED}❌ Ошибка при запуске контейнера${NC}"
        echo -e "${YELLOW}Проверьте логи:${NC}"
        echo -e "  docker compose -f docker-compose.xuibot.yml logs"
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
    echo -e "  Перезапуск: ${YELLOW}docker compose -f docker-compose.xuibot.yml restart${NC}"
    echo -e "  Остановка: ${YELLOW}docker compose -f docker-compose.xuibot.yml down${NC}"
}

# Функция показа логов
show_logs() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Логи XUIBot${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    echo -e "${YELLOW}📋 Логи XUIBot (последние 50 строк):${NC}"
    docker logs --tail=50 xuibot 2>/dev/null || echo -e "${RED}Контейнер xuibot не запущен${NC}"
}

# Функция обновления бота
update_bot() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Обновление XUIBot${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    echo -e "${YELLOW}🔄 Обновление бота...${NC}"
    
    # Остановка контейнера
    echo -e "${YELLOW}🛑 Остановка контейнера...${NC}"
    docker stop xuibot 2>/dev/null || true
    docker rm xuibot 2>/dev/null || true
    
    # Пересборка образа
    echo -e "${YELLOW}🐳 Пересборка образа...${NC}"
    docker compose -f docker-compose.xuibot.yml build --no-cache
    
    # Запуск
    echo -e "${YELLOW}🚀 Запуск обновленного контейнера...${NC}"
    docker compose -f docker-compose.xuibot.yml up -d
    
    sleep 5
    echo -e "\n${GREEN}✅ Бот обновлен!${NC}"
    echo -e "${GREEN}📊 Статус контейнера:${NC}"
    docker ps --filter name=xuibot
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
    docker compose -f docker-compose.xuibot.yml down
    
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
    
    if ! docker compose -f docker-compose.xuibot.yml up -d --build; then
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
    
    # Остановка контейнера
    echo -e "${YELLOW}🛑 Остановка контейнера...${NC}"
    docker stop xuibot 2>/dev/null || true
    docker rm xuibot 2>/dev/null || true
    
    # Пересборка образа
    echo -e "${YELLOW}🐳 Пересборка образа...${NC}"
    docker compose -f docker-compose.xuibot.yml build --no-cache
    
    # Запуск
    echo -e "${YELLOW}🚀 Запуск обновленного контейнера...${NC}"
    docker compose -f docker-compose.xuibot.yml up -d
    
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
    
    # Очистка XUI_BOT_TOKEN из .env
    if [ -f ".env" ]; then
        echo -e "${YELLOW}🧹 Очистка XUI_BOT_TOKEN из .env...${NC}"
        if grep -q "^XUI_BOT_TOKEN=" .env; then
            sed -i '/^XUI_BOT_TOKEN=/d' .env
            echo -e "${GREEN}✅ XUI_BOT_TOKEN удален из .env${NC}"
        fi
        # Также удаляем старый TELEGRAM_BOT_TOKEN если он есть (для обратной совместимости)
        if grep -q "^TELEGRAM_BOT_TOKEN=" .env; then
            sed -i '/^TELEGRAM_BOT_TOKEN=/d' .env
            echo -e "${GREEN}✅ TELEGRAM_BOT_TOKEN удален из .env${NC}"
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
    
    if ! docker compose -f docker-compose.awgbot.yml up -d --build; then
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
    echo -e "${BLUE}   Обновление AWG Бота${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    echo -e "${YELLOW}🔄 Обновление AWG бота...${NC}"
    
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
    
    # Остановка контейнера
    echo -e "${YELLOW}🛑 Остановка контейнера...${NC}"
    docker stop awgbot 2>/dev/null || true
    docker rm awgbot 2>/dev/null || true
    
    # Пересборка образа
    echo -e "${YELLOW}🐳 Пересборка образа...${NC}"
    docker compose -f docker-compose.awgbot.yml build --no-cache
    
    # Запуск
    echo -e "${YELLOW}🚀 Запуск обновленного контейнера...${NC}"
    docker compose -f docker-compose.awgbot.yml up -d
    
    sleep 5
    echo -e "\n${GREEN}✅ AWG Бот обновлен!${NC}"
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
    echo -e "${GREEN}0)${NC} Отмена"
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
    
    echo -e "${YELLOW}🛑 Остановка контейнера xuibot...${NC}"
    docker compose -f docker-compose.xuibot.yml down 2>/dev/null || true
    
    echo -e "${YELLOW}🔨 Пересборка образа xuibot...${NC}"
    docker compose -f docker-compose.xuibot.yml build --no-cache
    
    echo -e "${YELLOW}🚀 Запуск контейнера xuibot...${NC}"
    docker compose -f docker-compose.xuibot.yml up -d
    
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
    docker compose -f docker-compose.awgbot.yml down 2>/dev/null || true
    
    echo -e "${YELLOW}🔨 Пересборка образа awgbot...${NC}"
    docker compose -f docker-compose.awgbot.yml build --no-cache
    
    echo -e "${YELLOW}🚀 Запуск контейнера awgbot...${NC}"
    docker compose -f docker-compose.awgbot.yml up -d
    
    sleep 5
    echo -e "${GREEN}✅ Контейнер AWGBOT перезапущен!${NC}"
    echo -e "\n${YELLOW}📋 Логи (последние 15 строк):${NC}"
    docker logs --tail=15 awgbot
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
        # Получаем версию
        local xui_version=$(x-ui version 2>/dev/null | grep -oP 'v\d+\.\d+\.\d+' | head -1)
        [ -z "$xui_version" ] && xui_version="Unknown"
        
        # Получаем данные из .env
        if [ -f ".env" ]; then
            local xui_url=$(grep "^XUI_URL=" .env 2>/dev/null | cut -d'=' -f2)
            local xui_user=$(grep "^XUI_USERNAME=" .env 2>/dev/null | cut -d'=' -f2)
            local xui_pass=$(grep "^XUI_PASSWORD=" .env 2>/dev/null | cut -d'=' -f2)
            
            echo -e "  ${GREEN}✅ Установлена${NC}"
            echo -e "  Версия: ${xui_version}"
            echo -e "  URL: ${xui_url}"
            echo -e "  Логин: ${xui_user}"
            echo -e "  Пароль: ${xui_pass}"
            echo -e "  Состояние: ${GREEN}Запущена${NC}"
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
    if docker ps --filter name=amnezia-awg --format "{{.Names}}" | grep -q "amnezia-awg"; then
        local awg1_port=$(docker port amnezia-awg 2>/dev/null | grep -oP '\d+$' | head -1)
        [ -z "$awg1_port" ] && awg1_port="Unknown"
        echo -e "  AWG v1: ${GREEN}✅ Запущен${NC} (Порт: ${awg1_port})"
    else
        echo -e "  AWG v1: ${RED}❌ Не установлен${NC}"
    fi
    
    # AWG v2
    if docker ps --filter name=amnezia-awg2 --format "{{.Names}}" | grep -q "amnezia-awg2"; then
        local awg2_port=$(docker port amnezia-awg2 2>/dev/null | grep -oP '\d+$' | head -1)
        [ -z "$awg2_port" ] && awg2_port="Unknown"
        echo -e "  AWG v2: ${GREEN}✅ Запущен${NC} (Порт: ${awg2_port})"
    else
        echo -e "  AWG v2: ${RED}❌ Не установлен${NC}"
    fi
    
    # ============================================
    # TELEGRAM BOTS
    # ============================================
    echo -e "\n${YELLOW}${BOLD}TELEGRAM BOTS:${NC}"
    
    # XUI Bot
    if docker ps --filter name=xuibot --format "{{.Names}}" | grep -q xuibot; then
        local xui_token=$(grep "^XUI_BOT_TOKEN=" .env 2>/dev/null | cut -d'=' -f2)
        local xui_bot_username=$(get_bot_username "$xui_token" "xuibot")
        
        echo -e "  XUI Bot: ${GREEN}✅ Запущен${NC}"
        if [ "$xui_bot_username" != "Unknown" ]; then
            echo -e "  Ссылка: https://t.me/${xui_bot_username}"
        fi
        echo -e "  Состояние: ${GREEN}Running${NC}"
    else
        echo -e "  XUI Bot: ${RED}❌ Не установлен${NC}"
    fi
    
    # AWG Bot
    if docker ps --filter name=awgbot --format "{{.Names}}" | grep -q awgbot; then
        local awg_token=$(grep "^AWG_BOT_TOKEN=" .env 2>/dev/null | cut -d'=' -f2)
        local awg_bot_username=$(get_bot_username "$awg_token" "awgbot")
        
        echo -e "  AWG Bot: ${GREEN}✅ Запущен${NC}"
        if [ "$awg_bot_username" != "Unknown" ]; then
            echo -e "  Ссылка: https://t.me/${awg_bot_username}"
        fi
        echo -e "  Состояние: ${GREEN}Running${NC}"
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
    echo -e "${RED}⚠️  ВАЖНО: Бот совместим только с версией 2.9.4!${NC}"
    echo -e "${YELLOW}Версии 3.0.0+ не поддерживают прямую работу с базой данных${NC}\n"
    echo -e "${GREEN}1)${NC} Стабильная версия v2.9.4 (рекомендуется для бота)"
    echo -e "${YELLOW}2)${NC} Последняя версия (Latest - НЕ совместима с ботом)"
    echo -e "${GREEN}0)${NC} Отмена"
    echo -e "\n${YELLOW}Выберите версию для установки [1]:${NC} "
    read -p "" version_choice
    version_choice=${version_choice:-1}
    
    case $version_choice in
        1)
            install_3xui_v294
            ;;
        2)
            echo -e "\n${RED}⚠️  ВНИМАНИЕ!${NC}"
            echo -e "${YELLOW}Последняя версия 3x-ui НЕ совместима с ботом!${NC}"
            echo -e "${YELLOW}Клиенты, созданные через бота, не будут работать.${NC}"
            read -p "Вы уверены что хотите продолжить? (нажмите Enter для подтверждения или 0 для отмены): " confirm_latest
            if [[ "$confirm_latest" != "0" ]]; then
                install_3xui_latest
            else
                echo -e "${GREEN}Отменено. Устанавливаем v2.9.4...${NC}"
                install_3xui_v294
            fi
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
        
        echo -e "${GREEN}✅ Настройки панели получены:${NC}"
        echo -e "  Порт: ${YELLOW}${XUI_PORT}${NC}"
        echo -e "  Путь: ${YELLOW}${XUI_PATH}${NC}"
        
        # Формируем URL (добавляем /panel для 3x-ui v3.2.8+)
        if [ -z "$XUI_PATH" ] || [ "$XUI_PATH" = "/" ]; then
            XUI_URL="http://${SERVER_IP}:${XUI_PORT}/panel"
        else
            # Убираем trailing slash если есть и добавляем leading slash если нужно
            XUI_PATH_CLEAN="${XUI_PATH%/}"
            # Проверяем есть ли leading slash
            if [[ "$XUI_PATH_CLEAN" != /* ]]; then
                XUI_PATH_CLEAN="/${XUI_PATH_CLEAN}"
            fi
            XUI_URL="http://${SERVER_IP}:${XUI_PORT}${XUI_PATH_CLEAN}/panel"
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
            echo -e "${GREEN}n${NC} - Вернуться в главное меню"
            echo -e "${BLUE}========================================${NC}"
            read -p "Ваш выбор: " inbound_type
            
            if [[ "$inbound_type" =~ ^[Nn]$ ]]; then
                echo -e "${YELLOW}Возврат в главное меню...${NC}"
                return
            fi
            
            case $inbound_type in
                1)
                    if create_xhttp_reality_inbound; then
                        # Предлагаем установить бота
                        echo -e "\n${BLUE}========================================${NC}"
                        echo -e "${BLUE}   Установить xuibot?${NC}"
                        echo -e "${BLUE}========================================${NC}"
                        echo -e "${GREEN}Enter${NC} - Да, установить бота"
                        echo -e "${GREEN}n${NC}     - Нет, вернуться в главное меню"
                        echo -e "${BLUE}========================================${NC}"
                        read -p "Ваш выбор: " install_bot_choice
                        
                        if [[ ! "$install_bot_choice" =~ ^[Nn]$ ]]; then
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
                        echo -e "${GREEN}n${NC}     - Нет, вернуться в главное меню"
                        echo -e "${BLUE}========================================${NC}"
                        read -p "Ваш выбор: " install_bot_choice
                        
                        if [[ ! "$install_bot_choice" =~ ^[Nn]$ ]]; then
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
    fi
    
    SERVER_IP=$(curl -s ifconfig.me)
    
    echo -e "${YELLOW}📦 Загрузка и установка 3x-ui v2.9.4...${NC}\n"
    
    # Запускаем установку с выводом на экран и в файл одновременно
    INSTALL_LOG="/tmp/xui_install_$$.log"
    # Передаем пустые ответы (Enter) на все вопросы через stdin
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
        if [ -z "$XUI_PATH" ] || [ "$XUI_PATH" = "/" ]; then
            XUI_URL="http://${SERVER_IP}:${XUI_PORT}"
        else
            # Убираем trailing slash если есть и добавляем leading slash если нужно
            XUI_PATH_CLEAN="${XUI_PATH%/}"
            # Проверяем есть ли leading slash
            if [[ "$XUI_PATH_CLEAN" != /* ]]; then
                XUI_PATH_CLEAN="/${XUI_PATH_CLEAN}"
            fi
            XUI_URL="http://${SERVER_IP}:${XUI_PORT}${XUI_PATH_CLEAN}"
        fi
        
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
        echo -e "\n${GREEN}✅ Установка 3x-ui v2.9.4 панели завершена!${NC}\n"
        
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
    echo -e "${YELLOW}📦 Импортирую Docker образ из $source_file...${NC}" >&2
    
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

# Объединенная функция установки AWG (v1 и v2)
install_awg() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Установка AWG Сервера${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    # Выбор версии
    echo -e "${YELLOW}Выберите версию AWG:${NC}"
    echo -e "${GREEN}1)${NC} AWG v1 (порт по умолчанию 51820)"
    echo -e "${GREEN}2)${NC} AWG v2 (порт по умолчанию 51821)"
    echo -e "${GREEN}3)${NC} Установить обе версии"
    read -p "Введите номер (1-3): " version_choice
    
    case $version_choice in
        1)
            install_awg_version "v1" "51820"
            ;;
        2)
            install_awg_version "v2" "51821"
            ;;
        3)
            echo -e "\n${YELLOW}Установка AWG v1...${NC}"
            install_awg_version "v1" "51820"
            echo -e "\n${YELLOW}Установка AWG v2...${NC}"
            install_awg_version "v2" "51821"
            ;;
        *)
            echo -e "${RED}❌ Неверный выбор${NC}"
            return 1
            ;;
    esac
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
        echo -e "${GREEN}💡 Совет: Вы можете установить awgbot (пункт 12) для удобного управления через Telegram${NC}"
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
    echo -e "${YELLOW}3X-UI:${NC}"
    echo -e "${GREEN}1)${NC} Установка 3x-ui Panel v2.9.4"
    echo -e "${GREEN}2)${NC} Удаление 3x-ui Panel"
    echo -e "${BLUE}---${NC}"
    echo -e "${YELLOW}AWG:${NC}"
    echo -e "${GREEN}3)${NC} Установка AWG (v1/v2)"
    echo -e "${GREEN}4)${NC} Удаление AWG (v1/v2)"
    echo -e "${GREEN}5)${NC} Сформировать конфигурацию AWG v1"
    echo -e "${GREEN}6)${NC} Сформировать конфигурацию AWG v2"
    echo -e "${BLUE}---${NC}"
    echo -e "${YELLOW}XUIBOT:${NC}"
    echo -e "${GREEN}7)${NC} Установка XUIBOT"
    echo -e "${GREEN}8)${NC} Логи XUIBOT"
    echo -e "${GREEN}9)${NC} Перезапуск XUIBOT"
    echo -e "${GREEN}10)${NC} Пересборка XUIBOT"
    echo -e "${GREEN}11)${NC} Обновление XUIBOT"
    echo -e "${GREEN}12)${NC} Удаление XUIBOT"
    echo -e "${BLUE}---${NC}"
    echo -e "${YELLOW}AWGBOT:${NC}"
    echo -e "${GREEN}13)${NC} Установка AWGBOT"
    echo -e "${GREEN}14)${NC} Логи AWGBOT"
    echo -e "${GREEN}15)${NC} Перезапуск AWGBOT"
    echo -e "${GREEN}16)${NC} Пересборка AWGBOT"
    echo -e "${GREEN}17)${NC} Обновление AWGBOT"
    echo -e "${GREEN}18)${NC} Удаление AWGBOT"
    echo -e "${BLUE}---${NC}"
    echo -e "${YELLOW}Системные утилиты:${NC}"
    echo -e "${GREEN}19)${NC} Анализ диска и памяти"
    echo -e "${GREEN}20)${NC} Показать статус системы"
    echo -e "${BLUE}---${NC}"
    echo -e "${RED}99)${NC} Удалить ВСЁ (AWG + Боты + 3x-ui)"
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
            install_3xui
            ;;
        2)
            remove_3xui
            ;;
        3)
            install_awg
            ;;
        4)
            remove_awg
            ;;
        5)
            generate_awg_config "v1"
            ;;
        6)
            generate_awg_config "v2"
            ;;
        7)
            install_xuibot
            ;;
        8)
            show_xuibot_logs
            ;;
        9)
            restart_xuibot
            ;;
        10)
            rebuild_xuibot
            ;;
        11)
            update_xuibot
            ;;
        12)
            remove_xuibot
            ;;
        13)
            install_awgbot
            ;;
        14)
            show_awgbot_logs
            ;;
        15)
            restart_awgbot
            ;;
        16)
            rebuild_awgbot
            ;;
        17)
            update_awgbot
            ;;
        18)
            remove_awgbot
            ;;
        19)
            if [ -f "disk_analyzer.sh" ]; then
                bash disk_analyzer.sh
            else
                echo -e "${RED}❌ Файл disk_analyzer.sh не найден!${NC}"
            fi
            ;;
        20)
            show_status
            ;;
        99)
            remove_all
            ;;
        0)
            echo -e "\n${YELLOW}Каталог awgxuibot удалён, вернитесь на уровень назад командой:${NC}"
            echo -e "${GREEN}cd ..${NC}"
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

# Made with Bob
