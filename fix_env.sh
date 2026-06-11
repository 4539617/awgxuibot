#!/bin/bash

# Скрипт для быстрого исправления .env файла

BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   Исправление .env файла${NC}"
echo -e "${BLUE}========================================${NC}\n"

# Проверяем наличие .env
if [ ! -f ".env" ]; then
    echo -e "${RED}❌ Файл .env не найден${NC}"
    exit 1
fi

# Извлекаем XUI_URL
XUI_URL=$(grep "^XUI_URL=" .env 2>/dev/null | cut -d'=' -f2)

if [ -z "$XUI_URL" ]; then
    echo -e "${RED}❌ XUI_URL не найден в .env${NC}"
    exit 1
fi

echo -e "${YELLOW}Текущий XUI_URL: ${XUI_URL}${NC}"

# Извлекаем домен из URL
DOMAIN=$(echo "$XUI_URL" | sed -E 's|^https?://([^:/]+).*|\1|')

if [ -z "$DOMAIN" ]; then
    echo -e "${RED}❌ Не удалось извлечь домен из XUI_URL${NC}"
    exit 1
fi

echo -e "${GREEN}Извлечённый домен: ${DOMAIN}${NC}\n"

# Обновляем SERVER_ADDRESS
echo -e "${YELLOW}Обновление SERVER_ADDRESS...${NC}"
if grep -q "^SERVER_ADDRESS=" .env; then
    sed -i "s|^SERVER_ADDRESS=.*|SERVER_ADDRESS=${DOMAIN}|" .env
    echo -e "${GREEN}✅ SERVER_ADDRESS обновлён: ${DOMAIN}${NC}"
else
    echo "SERVER_ADDRESS=${DOMAIN}" >> .env
    echo -e "${GREEN}✅ SERVER_ADDRESS добавлен: ${DOMAIN}${NC}"
fi

# Обновляем TLS_SNI
echo -e "${YELLOW}Обновление TLS_SNI...${NC}"
if grep -q "^TLS_SNI=" .env; then
    sed -i "s|^TLS_SNI=.*|TLS_SNI=${DOMAIN}|" .env
    echo -e "${GREEN}✅ TLS_SNI обновлён: ${DOMAIN}${NC}"
else
    echo "TLS_SNI=${DOMAIN}" >> .env
    echo -e "${GREEN}✅ TLS_SNI добавлен: ${DOMAIN}${NC}"
fi

# Извлекаем параметры из inbound
echo -e "\n${YELLOW}Извлечение параметров из inbound...${NC}"

if [ ! -f "/etc/x-ui/x-ui.db" ]; then
    echo -e "${YELLOW}⚠️  База данных 3x-ui не найдена${NC}"
else
    # Получаем INBOUND_ID из .env
    INBOUND_ID=$(grep "^INBOUND_ID=" .env 2>/dev/null | cut -d'=' -f2)
    
    if [ -z "$INBOUND_ID" ]; then
        # Если не указан, берём первый
        INBOUND_ID=$(sqlite3 /etc/x-ui/x-ui.db "SELECT id FROM inbounds ORDER BY id ASC LIMIT 1;" 2>/dev/null)
    fi
    
    if [ -n "$INBOUND_ID" ]; then
        echo -e "${GREEN}Используем inbound ID: ${INBOUND_ID}${NC}"
        
        # Извлекаем security
        SECURITY=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.security') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
        
        if [ "$SECURITY" = "tls" ]; then
            echo -e "${YELLOW}Обнаружен TLS inbound${NC}"
            
            # Извлекаем TLS fingerprint
            TLS_FP=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.tlsSettings.settings.fingerprint') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
            
            if [ -n "$TLS_FP" ] && [ "$TLS_FP" != "null" ]; then
                if grep -q "^TLS_FINGERPRINT=" .env; then
                    sed -i "s|^TLS_FINGERPRINT=.*|TLS_FINGERPRINT=${TLS_FP}|" .env
                else
                    echo "TLS_FINGERPRINT=${TLS_FP}" >> .env
                fi
                echo -e "${GREEN}✅ TLS_FINGERPRINT обновлён: ${TLS_FP}${NC}"
            fi
            
            # Извлекаем ALPN
            TLS_ALPN=$(sqlite3 /etc/x-ui/x-ui.db "SELECT json_extract(stream_settings, '$.tlsSettings.alpn[0]') FROM inbounds WHERE id=${INBOUND_ID};" 2>/dev/null)
            
            if [ -n "$TLS_ALPN" ] && [ "$TLS_ALPN" != "null" ]; then
                if grep -q "^TLS_ALPN=" .env; then
                    sed -i "s|^TLS_ALPN=.*|TLS_ALPN=${TLS_ALPN}|" .env
                else
                    echo "TLS_ALPN=${TLS_ALPN}" >> .env
                fi
                echo -e "${GREEN}✅ TLS_ALPN обновлён: ${TLS_ALPN}${NC}"
            fi
        fi
    fi
fi

echo -e "\n${BLUE}========================================${NC}"
echo -e "${GREEN}✅ Обновление завершено!${NC}"
echo -e "${BLUE}========================================${NC}\n"

echo -e "${YELLOW}Текущие параметры:${NC}"
echo -e "${BLUE}SERVER_ADDRESS:${NC} $(grep "^SERVER_ADDRESS=" .env | cut -d'=' -f2)"
echo -e "${BLUE}TLS_SNI:${NC} $(grep "^TLS_SNI=" .env | cut -d'=' -f2)"
echo -e "${BLUE}TLS_FINGERPRINT:${NC} $(grep "^TLS_FINGERPRINT=" .env | cut -d'=' -f2)"
echo -e "${BLUE}TLS_ALPN:${NC} $(grep "^TLS_ALPN=" .env | cut -d'=' -f2)"

echo -e "\n${YELLOW}Перезапустите бота:${NC}"
echo -e "${GREEN}docker restart xuibot${NC}"

# Made with Bob
