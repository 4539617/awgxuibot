#!/bin/bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   Unified VPN Bot Installer${NC}"
echo -e "${BLUE}   NetCrazyBot + XUIBot${NC}"
echo -e "${BLUE}========================================${NC}\n"

# Проверка прав root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}❌ Запустите с правами root (sudo ./install.sh)${NC}"
    exit 1
fi

# Определяем рабочую директорию
WORK_DIR="/opt/unified-vpn-bot"

# Проверка наличия необходимых файлов в текущей директории
if [ -f "docker-compose.yml" ] && [ -f "Dockerfile" ]; then
    # Файлы найдены в текущей директории
    WORK_DIR="$(pwd)"
    echo -e "${GREEN}📁 Рабочая директория: $WORK_DIR${NC}"
    echo -e "${GREEN}✅ Все необходимые файлы найдены${NC}\n"
else
    # Файлы не найдены - нужно клонировать репозиторий
    echo -e "${YELLOW}📦 Файлы проекта не найдены в текущей директории${NC}"
    echo -e "${YELLOW}Клонирование репозитория...${NC}\n"
    
    # Проверка наличия git
    if ! command -v git &> /dev/null; then
        echo -e "${YELLOW}📦 Установка git...${NC}"
        apt-get update -qq
        apt-get install -y git
    fi
    
    # Клонирование репозитория
    if [ -d "$WORK_DIR" ]; then
        echo -e "${YELLOW}Директория $WORK_DIR уже существует${NC}"
        read -p "Удалить и клонировать заново? (y/n): " confirm
        if [[ "$confirm" =~ ^[Yy]$ ]]; then
            rm -rf "$WORK_DIR"
        else
            echo -e "${YELLOW}Использую существующую директорию${NC}"
        fi
    fi
    
    if [ ! -d "$WORK_DIR" ]; then
        echo -e "${YELLOW}Клонирование из GitHub...${NC}"
        git clone https://github.com/your-repo/unified-vpn-bot.git "$WORK_DIR" || {
            echo -e "${RED}❌ Не удалось клонировать репозиторий${NC}"
            echo -e "${YELLOW}Попробуйте вручную:${NC}"
            echo -e "  git clone https://github.com/your-repo/unified-vpn-bot.git"
            echo -e "  cd unified-vpn-bot"
            echo -e "  sudo ./install.sh"
            exit 1
        }
    fi
    
    cd "$WORK_DIR"
    echo -e "${GREEN}✅ Репозиторий клонирован: $WORK_DIR${NC}\n"
fi

# Переходим в рабочую директорию
cd "$WORK_DIR"
echo -e "${GREEN}📁 Текущая директория: $(pwd)${NC}\n"

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
    echo -e "${BLUE}   Настройка Секретных Параметров${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    create_env_if_not_exists
    
    # Создаем статические параметры
    create_static_params
    
    # ==================== Telegram Bot (Объединенный) ====================
    echo -e "\n${GREEN}📱 Настройка Telegram Bot${NC}"
    echo -e "${BLUE}Один токен используется для обоих ботов (NetCrazyBot + XUIBot)${NC}\n"
    
    TELEGRAM_BOT_TOKEN=$(get_env_value "TELEGRAM_BOT_TOKEN")
    if [ -z "$TELEGRAM_BOT_TOKEN" ]; then
        read -p "Введите TELEGRAM_BOT_TOKEN (токен Telegram бота): " TELEGRAM_BOT_TOKEN
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
    
    # ==================== 3x-ui Panel ====================
    echo -e "\n${GREEN}🌐 Настройка 3x-ui Panel${NC}\n"
    
    XUI_URL=$(get_env_value "XUI_URL")
    if [ -z "$XUI_URL" ]; then
        read -p "Введите XUI_URL (адрес панели 3x-ui): " XUI_URL
        update_env_value "XUI_URL" "$XUI_URL"
    else
        echo -e "XUI_URL: $XUI_URL ${GREEN}✓${NC}"
    fi
    
    XUI_USERNAME=$(get_env_value "XUI_USERNAME")
    if [ -z "$XUI_USERNAME" ]; then
        read -p "Введите XUI_USERNAME (логин панели): " XUI_USERNAME
        update_env_value "XUI_USERNAME" "$XUI_USERNAME"
    else
        echo -e "XUI_USERNAME: $XUI_USERNAME ${GREEN}✓${NC}"
    fi
    
    XUI_PASSWORD=$(get_env_value "XUI_PASSWORD")
    if [ -z "$XUI_PASSWORD" ]; then
        read -p "Введите XUI_PASSWORD (пароль панели): " XUI_PASSWORD
        update_env_value "XUI_PASSWORD" "$XUI_PASSWORD"
    else
        echo -e "XUI_PASSWORD: ******** ${GREEN}✓${NC}"
    fi
    
    INBOUND_ID=$(get_env_value "INBOUND_ID")
    if [ -z "$INBOUND_ID" ]; then
        read -p "Введите INBOUND_ID (ID inbound, по умолчанию 1): " INBOUND_ID
        INBOUND_ID=${INBOUND_ID:-1}
        update_env_value "INBOUND_ID" "$INBOUND_ID"
    else
        echo -e "INBOUND_ID: $INBOUND_ID ${GREEN}✓${NC}"
    fi
    
    # ==================== VPN Server ====================
    echo -e "\n${GREEN}🖥️  Настройка VPN Server${NC}\n"
    
    SERVER_ADDRESS=$(get_env_value "SERVER_ADDRESS")
    if [ -z "$SERVER_ADDRESS" ]; then
        read -p "Введите SERVER_ADDRESS (если вы используете Transport=TCP): " SERVER_ADDRESS
        update_env_value "SERVER_ADDRESS" "$SERVER_ADDRESS"
    else
        echo -e "SERVER_ADDRESS: $SERVER_ADDRESS ${GREEN}✓${NC}"
    fi
    
    SERVER_IP=$(get_env_value "SERVER_IP")
    if [ -z "$SERVER_IP" ]; then
        read -p "Введите SERVER_IP (IP сервера, по умолчанию = SERVER_ADDRESS): " SERVER_IP
        SERVER_IP=${SERVER_IP:-$SERVER_ADDRESS}
        update_env_value "SERVER_IP" "$SERVER_IP"
    else
        echo -e "SERVER_IP: $SERVER_IP ${GREEN}✓${NC}"
    fi
    
    # ==================== Транспорт и безопасность ====================
    echo -e "\n${GREEN}🔐 Настройка Транспорта и Безопасности${NC}\n"
    
    TRANSPORT=$(get_env_value "TRANSPORT")
    if [ -z "$TRANSPORT" ]; then
        read -p "Введите TRANSPORT (tcp/xhttp, по умолчанию xhttp): " TRANSPORT
        TRANSPORT=${TRANSPORT:-xhttp}
        update_env_value "TRANSPORT" "$TRANSPORT"
    else
        echo -e "TRANSPORT: $TRANSPORT ${GREEN}✓${NC}"
    fi
    
    SECURITY=$(get_env_value "SECURITY")
    if [ -z "$SECURITY" ]; then
        read -p "Введите SECURITY (tls/reality, по умолчанию reality): " SECURITY
        SECURITY=${SECURITY:-reality}
        update_env_value "SECURITY" "$SECURITY"
    else
        echo -e "SECURITY: $SECURITY ${GREEN}✓${NC}"
    fi
    
    # ==================== TLS или Reality ====================
    if [ "$SECURITY" = "tls" ]; then
        echo -e "\n${GREEN}🔑 Настройка TLS${NC}\n"
        
        TLS_SNI=$(get_env_value "TLS_SNI")
        if [ -z "$TLS_SNI" ]; then
            read -p "Введите TLS_SNI (домен или IP): " TLS_SNI
            update_env_value "TLS_SNI" "$TLS_SNI"
        else
            echo -e "TLS_SNI: $TLS_SNI ${GREEN}✓${NC}"
        fi
    elif [ "$SECURITY" = "reality" ]; then
        echo -e "\n${GREEN}🔑 Настройка Reality${NC}\n"
        
        REALITY_PUBLIC_KEY=$(get_env_value "REALITY_PUBLIC_KEY")
        if [ -z "$REALITY_PUBLIC_KEY" ]; then
            read -p "Введите REALITY_PUBLIC_KEY: " REALITY_PUBLIC_KEY
            update_env_value "REALITY_PUBLIC_KEY" "$REALITY_PUBLIC_KEY"
        else
            echo -e "REALITY_PUBLIC_KEY: ${REALITY_PUBLIC_KEY:0:20}... ${GREEN}✓${NC}"
        fi
        
        REALITY_SHORT_ID=$(get_env_value "REALITY_SHORT_ID")
        if [ -z "$REALITY_SHORT_ID" ]; then
            read -p "Введите REALITY_SHORT_ID: " REALITY_SHORT_ID
            update_env_value "REALITY_SHORT_ID" "$REALITY_SHORT_ID"
        else
            echo -e "REALITY_SHORT_ID: $REALITY_SHORT_ID ${GREEN}✓${NC}"
        fi
    fi
    
    echo -e "\n${GREEN}✅ Все параметры настроены!${NC}"
}

# Функция установки объединенного бота
install_bot() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}   Установка Объединенного Бота${NC}"
    echo -e "${BLUE}   NetCrazyBot (AWG) + XUIBot (3x-ui)${NC}"
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
    
    echo -e "\n${GREEN}✅ Объединенный бот установлен!${NC}"
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
    echo -e "${BLUE}   Обновление Объединенного Бота${NC}"
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
    echo -e "${BLUE}   Удаление Объединенного Бота${NC}"
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
    echo -e "${BLUE}   Удалить Всё (AWG + Бот)${NC}"
    echo -e "${BLUE}========================================${NC}\n"
    
    echo -e "${RED}⚠️  ВНИМАНИЕ! Это удалит:${NC}"
    echo -e "  - Объединенный бот (NetCrazyBot + XUIBot)"
    echo -e "  - AWG v1 сервер"
    echo -e "  - AWG v2 сервер"
    echo -e "  - Все конфигурации"
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
    
    echo -e "${GREEN}✅ Все компоненты удалены!${NC}"
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
    echo -e "${GREEN}5)${NC} Установка AWG v1"
    echo -e "${GREEN}6)${NC} Установка AWG v2"
    echo -e "${GREEN}7)${NC} Удаление AWG v1"
    echo -e "${GREEN}8)${NC} Удаление AWG v2"
    echo -e "${GREEN}9)${NC} Удалить AWG + Бот"
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
            install_awg_v1
            ;;
        6)
            install_awg_v2
            ;;
        7)
            remove_awg_v1
            ;;
        8)
            remove_awg_v2
            ;;
        9)
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
