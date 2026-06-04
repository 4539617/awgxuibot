#!/bin/bash
# install.sh - универсальный установщик бота

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}         Xuibot Installer${NC}"
echo -e "${BLUE}========================================${NC}\n"

# Проверка прав
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}❌ Запустите с правами root (sudo ./install.sh)${NC}"
    exit 1
fi

# Определяем директорию скрипта
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${GREEN}📁 Рабочая директория: ${SCRIPT_DIR}${NC}"

# Проверка Docker
if ! command -v docker &> /dev/null; then
    echo -e "${YELLOW}📦 Установка Docker...${NC}"
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
    systemctl enable docker
    systemctl start docker
    echo -e "${GREEN}✅ Docker установлен${NC}"
fi

# Создаём папки для логов и данных
mkdir -p logs data

# Проверяем наличие .env файла
if [ ! -f ".env" ]; then
    echo -e "${RED}❌ Файл .env не найден!${NC}"
    echo -e "${YELLOW}Создайте файл .env с настройками. Пример:${NC}"
    cat > .env.example << 'EOF'
# Telegram Bot
BOT_TOKEN=your_bot_token
ADMIN_IDS=your_telegram_id
ADMIN_USERNAME=@username

# X-UI Panel
XUI_URL=https://localhost:12345/your-path
XUI_USERNAME=admin
XUI_PASSWORD=password
INBOUND_ID=1

# VPN Server
SERVER_ADDRESS=your-server.com
SERVER_PORT=443

# Транспорт и безопасность
TRANSPORT=tcp
SECURITY=tls

# TLS (если SECURITY=tls)
TLS_SNI=your-server.com
TLS_FINGERPRINT=chrome
TLS_ALPN=http/1.1

# Reality (если SECURITY=reality)
REALITY_SNI=google.com
REALITY_FINGERPRINT=firefox
REALITY_PUBLIC_KEY=your_public_key
REALITY_SHORT_ID=your_short_id

# xHTTP (если TRANSPORT=xhttp)
XHTTP_MODE=auto
EOF
    echo -e "${YELLOW}Скопируйте .env.example в .env и заполните${NC}"
    exit 1
fi

# Читаем настройки из .env
BOT_TOKEN=$(grep "^BOT_TOKEN=" .env | cut -d'=' -f2 | head -1)
ADMIN_IDS=$(grep "^ADMIN_IDS=" .env | cut -d'=' -f2 | head -1)
XUI_URL=$(grep "^XUI_URL=" .env | cut -d'=' -f2 | head -1)
XUI_USERNAME=$(grep "^XUI_USERNAME=" .env | cut -d'=' -f2 | head -1)
XUI_PASSWORD=$(grep "^XUI_PASSWORD=" .env | cut -d'=' -f2 | head -1)
INBOUND_ID=$(grep "^INBOUND_ID=" .env | cut -d'=' -f2 | head -1)
SERVER_ADDRESS=$(grep "^SERVER_ADDRESS=" .env | cut -d'=' -f2 | head -1)
TRANSPORT=$(grep "^TRANSPORT=" .env | cut -d'=' -f2 | head -1)
SECURITY=$(grep "^SECURITY=" .env | cut -d'=' -f2 | head -1)

echo -e "\n${GREEN}✅ Настройки из .env:${NC}"
echo -e "  Транспорт: ${TRANSPORT:-tcp}"
echo -e "  Безопасность: ${SECURITY:-tls}"
echo -e "  Сервер: ${SERVER_ADDRESS}"

# Проверка обязательных полей
if [ -z "$BOT_TOKEN" ] || [ -z "$ADMIN_IDS" ] || [ -z "$XUI_URL" ] || [ -z "$SERVER_ADDRESS" ]; then
    echo -e "${RED}❌ В .env заполнены не все обязательные поля!${NC}"
    exit 1
fi

# Запуск бота
echo -e "\n${YELLOW}🐳 Запуск Docker контейнера...${NC}"

# Останавливаем и удаляем старый контейнер
docker stop xuibot 2>/dev/null || true
docker rm xuibot 2>/dev/null || true

# Сборка образа
echo -e "${YELLOW}Сборка Docker образа...${NC}"
docker build -t xuibot . 2>&1

# Запуск контейнера
echo -e "${YELLOW}Запуск контейнера...${NC}"
docker run -d \
  --name xuibot \
  --restart always \
  --network host \
  --env-file .env \
  -v ./logs:/app/logs \
  -v ./data:/app/data \
  -v /etc/x-ui/x-ui.db:/etc/x-ui/x-ui.db:ro \
  xuibot

# Проверка
sleep 3

echo -e "\n${GREEN}✅ Установка завершена!${NC}"
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}📊 Статус бота:${NC}"
docker ps --filter name=xuibot

echo -e "\n${YELLOW}📋 Последние логи:${NC}"
docker logs --tail=20 xuibot

echo -e "\n${GREEN}🎉 Бот успешно установлен!${NC}"
echo -e "${YELLOW}Для просмотра логов: docker logs -f xuibot${NC}"
echo -e "${YELLOW}Для перезапуска: docker restart xuibot${NC}"
echo -e "${YELLOW}Для остановки: docker stop xuibot${NC}"
echo -e "${YELLOW}Для удаления: docker rm -f xuibot${NC}"