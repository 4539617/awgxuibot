#!/bin/bash
# AWG Container Entrypoint Script
# Автоматически настраивает AWG интерфейс и NAT при запуске контейнера

set -e

# Определяем версию по наличию конфига
if [ -f "/etc/amnezia/amneziawg/awg0.conf" ]; then
    INTERFACE="awg0"
    CONFIG_PATH="/etc/amnezia/amneziawg/awg0.conf"
    VERSION="v2"
elif [ -f "/etc/amnezia/amneziawg/wg0.conf" ]; then
    INTERFACE="wg0"
    CONFIG_PATH="/etc/amnezia/amneziawg/wg0.conf"
    VERSION="v1"
    # Создаём симлинк для wg-quick
    mkdir -p /etc/wireguard
    ln -sf "$CONFIG_PATH" "/etc/wireguard/wg0.conf"
else
    echo "❌ Конфигурационный файл не найден!"
    exit 1
fi

echo "🚀 Запуск AWG $VERSION (интерфейс: $INTERFACE)"

# Запускаем AWG интерфейс
echo "📡 Запуск интерфейса $INTERFACE..."
if wg-quick up "$INTERFACE"; then
    echo "✅ Интерфейс $INTERFACE запущен"
else
    echo "⚠️  Интерфейс уже запущен или ошибка запуска"
fi

# Настраиваем NAT
echo "🔧 Настройка NAT правил..."

# MASQUERADE для исходящего трафика
if iptables -t nat -C POSTROUTING -s 10.8.1.0/24 -o eth0 -j MASQUERADE 2>/dev/null; then
    echo "ℹ️  NAT MASQUERADE уже настроен"
else
    iptables -t nat -A POSTROUTING -s 10.8.1.0/24 -o eth0 -j MASQUERADE
    echo "✅ NAT MASQUERADE настроен"
fi

# FORWARD правила
if iptables -C FORWARD -i "$INTERFACE" -j ACCEPT 2>/dev/null; then
    echo "ℹ️  FORWARD правила уже настроены"
else
    iptables -A FORWARD -i "$INTERFACE" -j ACCEPT
    iptables -A FORWARD -o "$INTERFACE" -j ACCEPT
    echo "✅ FORWARD правила настроены"
fi

echo "✅ AWG $VERSION готов к работе!"
echo "📊 Статус интерфейса:"
wg show "$INTERFACE" 2>/dev/null || echo "⚠️  Не удалось получить статус"

# Держим контейнер запущенным
echo "🔄 Контейнер работает..."
tail -f /dev/null

