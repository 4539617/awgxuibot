#!/usr/bin/env bash
# =============================================================================
# setup_reverse_cascade.sh
# Автоматическое создание обратного каскада между двумя 3x-ui серверами:
#   - RU_SERVER  : российский сервер (входящее + клиент)
#   - VPS_SERVER : исходящий VPS (исходящее + маршрутизация)
# =============================================================================

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# НАСТРОЙКИ — заполни перед запуском
# ─────────────────────────────────────────────────────────────────────────────

RU_URL="https://157.22.173.47:2053"      # адрес панели российского сервера
RU_USER="admin"                           # логин
RU_PASS="password"                        # пароль

VPS_URL="https://VPS_IP:2053"            # адрес панели VPS
VPS_USER="admin"
VPS_PASS="password"

NEW_EMAIL="newclient"                     # email нового клиента (уникальный)
NEW_PORT=0                                # порт входящего; 0 = выбрать случайный
SERVER_NAME="nvidia.com"                  # camouflage serverName (для Reality)
CAMOUFLAGE_TARGET="melsu.ru:443"          # реальная TLS-цель
OUTBOUND_TAG="torus_${NEW_EMAIL}"         # тег исходящего на VPS
FINGERPRINT="edge"

# Если SERVER_NAME совпадает с CAMOUFLAGE_TARGET (как у nicegoods: оба melsu.ru),
# то в serverNames НЕ нужно дублировать — скрипт определяет автоматически.
# Если SERVER_NAME отличается (как у websrvinfo: nvidia.com vs melsu.ru),
# то nvidia.com добавляется в serverNames входящего автоматически.

# Правила маршрутизации которые направляются в новый outbound
ROUTE_DOMAINS='["ext:geosite_RU.dat:ru-available-only-inside","regexp:.*\\.ru$","regexp:.*\\.su$","regexp:.*\\.xn--p1ai$"]'
ROUTE_IPS='["ext:geoip_RU.dat:ru"]'

# ─────────────────────────────────────────────────────────────────────────────
# УТИЛИТЫ
# ─────────────────────────────────────────────────────────────────────────────

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
die()  { echo "[ERROR] $*" >&2; exit 1; }

require() {
    for cmd in "$@"; do
        command -v "$cmd" &>/dev/null || die "Требуется '$cmd' (apt install $cmd)"
    done
}

require curl jq uuidgen openssl

# Генерация случайного порта в диапазоне 30000-60000 если не задан
random_port() {
    shuf -i 30000-60000 -n 1
}

# Генерация случайного shortId (4 байта = 8 hex символов)
random_short_id() {
    openssl rand -hex 4
}

# Генерация spiderX
random_spider_x() {
    echo "/$(openssl rand -hex 8)"
}

# curl с сессионными куками
CURL_OPTS=(-sk --cookie-jar /tmp/3xui_ru.cook --cookie /tmp/3xui_ru.cook)
CURL_OPTS_VPS=(-sk --cookie-jar /tmp/3xui_vps.cook --cookie /tmp/3xui_vps.cook)

# ─────────────────────────────────────────────────────────────────────────────
# API ФУНКЦИИ — российский сервер
# ─────────────────────────────────────────────────────────────────────────────

ru_login() {
    log "Логин на российском сервере $RU_URL ..."
    local resp
    resp=$(curl "${CURL_OPTS[@]}" -X POST "$RU_URL/login" \
        -H "Content-Type: application/json" \
        -d "{\"username\":\"$RU_USER\",\"password\":\"$RU_PASS\"}")
    echo "$resp" | jq -e '.success == true' &>/dev/null \
        || die "Не удалось войти на российский сервер: $resp"
    log "  ✓ Авторизован"
}

ru_get() {
    curl "${CURL_OPTS[@]}" -X GET "$RU_URL$1" -H "Accept: application/json"
}

ru_post() {
    curl "${CURL_OPTS[@]}" -X POST "$RU_URL$1" \
        -H "Content-Type: application/json" \
        -d "$2"
}

# Получить список inbounds и найти первый свободный порт
ru_free_port() {
    local used_ports
    used_ports=$(ru_get "/xui/API/inbounds" | jq '[.obj[].port]')
    local port=${NEW_PORT}
    if [[ $port -eq 0 ]]; then
        while true; do
            port=$(random_port)
            if ! echo "$used_ports" | jq -e "index($port) != null" &>/dev/null; then
                break
            fi
        done
    fi
    echo "$port"
}

# Генерация Reality keypair через xray на российском сервере (через API x25519)
ru_gen_keypair() {
    log "Генерация Reality keypair через российский сервер ..."
    local resp
    resp=$(ru_get "/xui/API/x25519Key")
    local priv pub
    priv=$(echo "$resp" | jq -r '.obj.privateKey // empty')
    pub=$(echo  "$resp" | jq -r '.obj.publicKey  // empty')
    if [[ -z "$priv" || -z "$pub" ]]; then
        # Fallback: генерируем локально через openssl (X25519)
        log "  API x25519 недоступен, генерируем локально..."
        priv=$(openssl genpkey -algorithm X25519 2>/dev/null \
               | openssl pkey -outform DER 2>/dev/null \
               | tail -c 32 | base64 | tr '+/' '-_' | tr -d '=')
        pub=$(openssl pkey -algorithm X25519 -key <(openssl genpkey -algorithm X25519 2>/dev/null) \
               -pubout -outform DER 2>/dev/null \
               | tail -c 32 | base64 | tr '+/' '-_' | tr -d '=')
        # Используем xray x25519 если доступен
        if command -v xray &>/dev/null; then
            local kp
            kp=$(xray x25519)
            priv=$(echo "$kp" | grep -i private | awk '{print $NF}')
            pub=$(echo  "$kp" | grep -i public  | awk '{print $NF}')
        fi
    fi
    echo "${priv}:${pub}"
}

# Создать входящее на российском сервере
ru_create_inbound() {
    local port=$1 uuid=$2 short_id=$3 priv_key=$4 pub_key=$5 spider_x=$6

    log "Создание входящего на российском сервере (порт $port) ..."

    # Извлекаем хост из CAMOUFLAGE_TARGET (убираем :порт)
    local camouflage_host="${CAMOUFLAGE_TARGET%%:*}"

    local body
    body=$(jq -n \
        --arg  email            "$NEW_EMAIL" \
        --arg  uuid             "$uuid" \
        --argjson port          "$port" \
        --arg  short_id         "$short_id" \
        --arg  priv_key         "$priv_key" \
        --arg  pub_key          "$pub_key" \
        --arg  spider_x         "$spider_x" \
        --arg  sname            "$SERVER_NAME" \
        --arg  target           "$CAMOUFLAGE_TARGET" \
        --arg  camouflage_host  "$camouflage_host" \
        '{
            listen: "",
            port: $port,
            protocol: "vless",
            tag: ("in-" + ($port|tostring) + "-tcp"),
            settings: {
                clients: [{
                    id: $uuid,
                    email: $email,
                    flow: "xtls-rprx-vision",
                    limitIp: 0,
                    totalGB: 0,
                    expiryTime: 0,
                    enable: true,
                    tgId: 0,
                    subId: "",
                    comment: "",
                    reset: 0
                }],
                decryption: "none",
                encryption: "none"
            },
            sniffing: { enabled: false },
            streamSettings: {
                network: "tcp",
                tcpSettings: {
                    acceptProxyProtocol: false,
                    header: { type: "none" }
                },
                security: "reality",
                realitySettings: {
                    show: false,
                    xver: 0,
                    target: $target,
                    serverNames: ([$camouflage_host, ("www." + $camouflage_host)]
                                  + (if $sname != $camouflage_host
                                     then [$sname, ("www." + $sname)]
                                     else [] end)),
                    privateKey: $priv_key,
                    minClientVer: "",
                    maxClientVer: "",
                    maxTimediff: 0,
                    shortIds: [$short_id],
                    mldsa65Seed: "",
                    settings: {
                        publicKey: $pub_key,
                        fingerprint: "edge",
                        serverName: "",
                        spiderX: $spider_x,
                        mldsa65Verify: ""
                    }
                },
                externalProxy: []
            }
        }')

    local resp
    resp=$(ru_post "/xui/API/inbounds/add" "$body")
    echo "$resp" | jq -e '.success == true' &>/dev/null \
        || die "Не удалось создать входящее: $resp"
    log "  ✓ Входящее создано"

    # Получаем ID нового inbound
    local inbound_id
    inbound_id=$(ru_get "/xui/API/inbounds" \
        | jq --argjson p "$port" '.obj[] | select(.port == $p) | .id')
    echo "$inbound_id"
}

# ─────────────────────────────────────────────────────────────────────────────
# API ФУНКЦИИ — VPS сервер
# ─────────────────────────────────────────────────────────────────────────────

vps_login() {
    log "Логин на VPS $VPS_URL ..."
    local resp
    resp=$(curl "${CURL_OPTS_VPS[@]}" -X POST "$VPS_URL/login" \
        -H "Content-Type: application/json" \
        -d "{\"username\":\"$VPS_USER\",\"password\":\"$VPS_PASS\"}")
    echo "$resp" | jq -e '.success == true' &>/dev/null \
        || die "Не удалось войти на VPS: $resp"
    log "  ✓ Авторизован"
}

vps_get() {
    curl "${CURL_OPTS_VPS[@]}" -X GET "$VPS_URL$1" -H "Accept: application/json"
}

vps_post() {
    curl "${CURL_OPTS_VPS[@]}" -X POST "$VPS_URL$1" \
        -H "Content-Type: application/json" \
        -d "$2"
}

# Создать исходящее на VPS
vps_create_outbound() {
    local ru_ip=$1 port=$2 uuid=$3 short_id=$4 pub_key=$5 spider_x=$6

    log "Создание исходящего на VPS (→ $ru_ip:$port) ..."

    local body
    body=$(jq -n \
        --arg  tag       "$OUTBOUND_TAG" \
        --arg  addr      "$ru_ip" \
        --argjson port   "$port" \
        --arg  uuid      "$uuid" \
        --arg  pub_key   "$pub_key" \
        --arg  short_id  "$short_id" \
        --arg  spider_x  "$spider_x" \
        --arg  sname     "$SERVER_NAME" \
        '{
            protocol: "vless",
            tag: $tag,
            settings: {
                address: $addr,
                port: $port,
                id: $uuid,
                flow: "xtls-rprx-vision",
                encryption: "none"
            },
            streamSettings: {
                network: "tcp",
                security: "reality",
                realitySettings: {
                    publicKey: $pub_key,
                    fingerprint: "edge",
                    serverName: $sname,
                    shortId: $short_id,
                    spiderX: $spider_x
                },
                tcpSettings: {
                    header: { type: "none" }
                }
            }
        }')

    local resp
    resp=$(vps_post "/xui/API/outbounds/add" "$body")
    echo "$resp" | jq -e '.success == true' &>/dev/null \
        || die "Не удалось создать исходящее: $resp"
    log "  ✓ Исходящее создано (tag: $OUTBOUND_TAG)"
}

# Обновить маршрутизацию на VPS — добавить правила для нового outbound
vps_update_routing() {
    log "Обновление маршрутизации на VPS ..."

    # Получаем текущие правила
    local current_rules
    current_rules=$(vps_get "/xui/API/routing" | jq '.obj.rules // []')

    # Новые правила для домена и IP (enabled: true как в nicegoods)
    local new_rules
    new_rules=$(jq -n \
        --arg  tag        "$OUTBOUND_TAG" \
        --argjson domains "$ROUTE_DOMAINS" \
        --argjson ips     "$ROUTE_IPS" \
        '[
            {
                type: "field",
                enabled: true,
                domain: $domains,
                outboundTag: $tag
            },
            {
                type: "field",
                enabled: true,
                ip: $ips,
                outboundTag: $tag
            }
        ]')

    # Проверяем что правила ещё не существуют
    local already
    already=$(echo "$current_rules" | jq --arg t "$OUTBOUND_TAG" \
        '[.[] | select(.outboundTag == $t)] | length')
    if [[ "$already" -gt 0 ]]; then
        log "  ℹ Правила для '$OUTBOUND_TAG' уже существуют, пропускаем"
        return
    fi

    # Объединяем: новые правила вставляем ПЕРЕД blocked/bittorrent
    local merged_rules
    merged_rules=$(jq -n \
        --argjson current "$current_rules" \
        --argjson newrules "$new_rules" \
        '($current | map(select(.outboundTag != "blocked" and (.protocol // []) != ["bittorrent"])))
         + $newrules
         + ($current | map(select(.outboundTag == "blocked" or (.protocol // []) == ["bittorrent"])))')

    local body
    body=$(jq -n --argjson rules "$merged_rules" \
        '{ rules: $rules }')

    local resp
    resp=$(vps_post "/xui/API/routing/update" "$body")
    echo "$resp" | jq -e '.success == true' &>/dev/null \
        || die "Не удалось обновить маршрутизацию: $resp"
    log "  ✓ Маршрутизация обновлена"
}

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

main() {
    log "======================================================"
    log "  Настройка обратного каскада 3x-ui"
    log "  Email клиента : $NEW_EMAIL"
    log "  ServerName    : $SERVER_NAME"
    log "  Camouflage    : $CAMOUFLAGE_TARGET"
    log "======================================================"

    # Извлекаем IP российского сервера из URL
    RU_IP=$(echo "$RU_URL" | sed -E 's|https?://([^:/]+).*|\1|')

    # Авторизация
    ru_login
    vps_login

    # Генерируем параметры
    UUID=$(uuidgen | tr '[:upper:]' '[:lower:]')
    SHORT_ID=$(random_short_id)
    SPIDER_X=$(random_spider_x)
    PORT=$(ru_free_port)

    log "Параметры:"
    log "  UUID     : $UUID"
    log "  Port     : $PORT"
    log "  ShortId  : $SHORT_ID"
    log "  SpiderX  : $SPIDER_X"

    # Генерируем Reality keypair
    KEYPAIR=$(ru_gen_keypair)
    PRIV_KEY="${KEYPAIR%%:*}"
    PUB_KEY="${KEYPAIR##*:}"
    log "  PrivKey  : $PRIV_KEY"
    log "  PubKey   : $PUB_KEY"

    # Шаг 1: входящее на российском сервере
    ru_create_inbound "$PORT" "$UUID" "$SHORT_ID" "$PRIV_KEY" "$PUB_KEY" "$SPIDER_X"

    # Шаг 2: исходящее на VPS
    vps_create_outbound "$RU_IP" "$PORT" "$UUID" "$SHORT_ID" "$PUB_KEY" "$SPIDER_X"

    # Шаг 3: маршрутизация на VPS
    vps_update_routing

    # Итог
    log "======================================================"
    log "  ✅ Готово!"
    log ""
    log "  Входящее  : $RU_URL (порт $PORT)"
    log "  Исходящее : tag=$OUTBOUND_TAG → $RU_IP:$PORT"
    log "  Клиент    : $NEW_EMAIL / UUID=$UUID"
    log "======================================================"
    log ""
    log "  Перезапусти xray на обоих серверах в панели 3x-ui"
    log "======================================================"
}

main "$@"
