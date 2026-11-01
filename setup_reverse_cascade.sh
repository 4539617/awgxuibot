#!/usr/bin/env bash
# =============================================================================
# setup_reverse_cascade.sh
# Автоматическое создание обратного каскада между двумя 3x-ui серверами:
#   - RU_SERVER  : российский сервер (входящее + клиент)
#   - VPS_SERVER : исходящий VPS (исходящее + маршрутизация)
#
# Требует 3x-ui версии 3.x (API: /panel/api/...)
# =============================================================================

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# НАСТРОЙКИ — переопределяются через переменные окружения
# ─────────────────────────────────────────────────────────────────────────────

RU_URL="${RU_URL:-https://157.22.173.47:2053}"       # адрес панели российского сервера
RU_TOKEN="${RU_TOKEN:-}"                               # API токен (рекомендуется)
RU_USER="${RU_USER:-admin}"                            # логин (если токен не задан)
RU_PASS="${RU_PASS:-password}"                         # пароль (если токен не задан)

VPS_URL="${VPS_URL:-https://VPS_IP:2053}"             # адрес панели VPS
VPS_TOKEN="${VPS_TOKEN:-}"                             # API токен (рекомендуется)
VPS_USER="${VPS_USER:-admin}"
VPS_PASS="${VPS_PASS:-password}"

NEW_EMAIL="${NEW_EMAIL:-newclient}"                    # email нового клиента (уникальный)
NEW_PORT="${NEW_PORT:-0}"                              # порт входящего; 0 = выбрать случайный
SERVER_NAME="${SERVER_NAME:-nvidia.com}"               # camouflage serverName (для Reality)
CAMOUFLAGE_TARGET="${CAMOUFLAGE_TARGET:-melsu.ru:443}" # реальная TLS-цель
OUTBOUND_TAG="torus_${NEW_EMAIL}"                     # тег исходящего на VPS
FINGERPRINT="${FINGERPRINT:-edge}"

# Убираем trailing slash из URL
RU_URL="${RU_URL%/}"
VPS_URL="${VPS_URL%/}"

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

random_port() {
    shuf -i 30000-60000 -n 1
}

random_short_id() {
    openssl rand -hex 4
}

random_spider_x() {
    echo "/$(openssl rand -hex 8)"
}

CURL_BASE=(-sk)

# ─────────────────────────────────────────────────────────────────────────────
# API ФУНКЦИИ — российский сервер
# ─────────────────────────────────────────────────────────────────────────────

RU_AUTH_HEADER=()
VPS_AUTH_HEADER=()

ru_auth() {
    if [[ -n "$RU_TOKEN" ]]; then
        log "Авторизация на российском сервере по токену ..."
        RU_AUTH_HEADER=(-H "Authorization: Bearer $RU_TOKEN")
        local resp
        resp=$(curl "${CURL_BASE[@]}" "${RU_AUTH_HEADER[@]}" "$RU_URL/panel/api/inbounds/list" -H "Accept: application/json")
        echo "$resp" | jq -e '.success == true' &>/dev/null \
            || die "Токен российского сервера не принят: $resp"
        log "  ✓ Авторизован по токену"
    else
        log "Логин на российском сервере по логину/паролю ..."
        local resp
        resp=$(curl "${CURL_BASE[@]}" --cookie-jar /tmp/3xui_ru.cook --cookie /tmp/3xui_ru.cook \
            -X POST "$RU_URL/login" \
            -H "Content-Type: application/json" \
            -d "{\"username\":\"$RU_USER\",\"password\":\"$RU_PASS\"}")
        echo "$resp" | jq -e '.success == true' &>/dev/null \
            || die "Не удалось войти на российский сервер: $resp"
        RU_AUTH_HEADER=(--cookie /tmp/3xui_ru.cook)
        log "  ✓ Авторизован по логину/паролю"
    fi
}

ru_get() {
    curl "${CURL_BASE[@]}" "${RU_AUTH_HEADER[@]}" -X GET "$RU_URL$1" -H "Accept: application/json"
}

ru_post() {
    curl "${CURL_BASE[@]}" "${RU_AUTH_HEADER[@]}" -X POST "$RU_URL$1" \
        -H "Content-Type: application/json" \
        -d "$2"
}

# Найти свободный порт на российском сервере
ru_free_port() {
    local used_ports
    used_ports=$(ru_get "/panel/api/inbounds/list" | jq '[.obj[].port]')
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

# Генерация Reality keypair через API российского сервера
ru_gen_keypair() {
    log "Генерация Reality keypair через российский сервер ..."
    local resp
    resp=$(ru_get "/panel/api/server/getNewX25519Cert")
    local priv pub
    priv=$(echo "$resp" | jq -r '.obj.privateKey // empty')
    pub=$(echo  "$resp" | jq -r '.obj.publicKey  // empty')
    if [[ -z "$priv" || -z "$pub" ]]; then
        log "  API getNewX25519Cert недоступен, генерируем локально..."
        if command -v xray &>/dev/null; then
            local kp
            kp=$(xray x25519)
            priv=$(echo "$kp" | grep -i private | awk '{print $NF}')
            pub=$(echo  "$kp" | grep -i public  | awk '{print $NF}')
        else
            priv=$(openssl genpkey -algorithm X25519 2>/dev/null \
                   | openssl pkey -outform DER 2>/dev/null \
                   | tail -c 32 | base64 | tr '+/' '-_' | tr -d '=')
            pub=$(openssl pkey -algorithm X25519 \
                   -key <(openssl genpkey -algorithm X25519 2>/dev/null) \
                   -pubout -outform DER 2>/dev/null \
                   | tail -c 32 | base64 | tr '+/' '-_' | tr -d '=')
        fi
    fi
    echo "${priv}:${pub}"
}

# Создать входящее на российском сервере
ru_create_inbound() {
    local port=$1 uuid=$2 short_id=$3 priv_key=$4 pub_key=$5 spider_x=$6

    log "Создание входящего на российском сервере (порт $port) ..."

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
            enable: true,
            remark: $email,
            listen: "",
            port: $port,
            protocol: "vless",
            expiryTime: 0,
            total: 0,
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
    resp=$(ru_post "/panel/api/inbounds/add" "$body")
    echo "$resp" | jq -e '.success == true' &>/dev/null \
        || die "Не удалось создать входящее: $resp"
    log "  ✓ Входящее создано"
}

# ─────────────────────────────────────────────────────────────────────────────
# API ФУНКЦИИ — VPS сервер
# ─────────────────────────────────────────────────────────────────────────────

vps_auth() {
    if [[ -n "$VPS_TOKEN" ]]; then
        log "Авторизация на VPS по токену ..."
        VPS_AUTH_HEADER=(-H "Authorization: Bearer $VPS_TOKEN")
        local resp
        resp=$(curl "${CURL_BASE[@]}" "${VPS_AUTH_HEADER[@]}" "$VPS_URL/panel/api/inbounds/list" -H "Accept: application/json")
        echo "$resp" | jq -e '.success == true' &>/dev/null \
            || die "Токен VPS не принят: $resp"
        log "  ✓ Авторизован по токену"
    else
        log "Логин на VPS по логину/паролю ..."
        local resp
        resp=$(curl "${CURL_BASE[@]}" --cookie-jar /tmp/3xui_vps.cook --cookie /tmp/3xui_vps.cook \
            -X POST "$VPS_URL/login" \
            -H "Content-Type: application/json" \
            -d "{\"username\":\"$VPS_USER\",\"password\":\"$VPS_PASS\"}")
        echo "$resp" | jq -e '.success == true' &>/dev/null \
            || die "Не удалось войти на VPS: $resp"
        VPS_AUTH_HEADER=(--cookie /tmp/3xui_vps.cook)
        log "  ✓ Авторизован по логину/паролю"
    fi
}

vps_get() {
    curl "${CURL_BASE[@]}" "${VPS_AUTH_HEADER[@]}" -X GET "$VPS_URL$1" -H "Accept: application/json"
}

vps_post_json() {
    curl "${CURL_BASE[@]}" "${VPS_AUTH_HEADER[@]}" -X POST "$VPS_URL$1" \
        -H "Content-Type: application/json" \
        -d "$2"
}

vps_post_form() {
    curl "${CURL_BASE[@]}" "${VPS_AUTH_HEADER[@]}" -X POST "$VPS_URL$1" \
        --data-urlencode "$2"
}

# Получить текущий xray config с VPS
vps_get_xray_config() {
    local resp
    resp=$(curl "${CURL_BASE[@]}" "${VPS_AUTH_HEADER[@]}" -X POST "$VPS_URL/panel/api/xray/" -H "Accept: application/json")
    echo "$resp" | jq -e '.success == true' &>/dev/null \
        || die "Не удалось получить xray конфиг VPS: $resp"
    # xraySetting — строка содержащая JSON, парсим её
    echo "$resp" | jq -r '.obj.xraySetting'
}

# Добавить outbound + routing в xray конфиг VPS и сохранить
vps_update_xray() {
    local ru_ip=$1 port=$2 uuid=$3 short_id=$4 pub_key=$5 spider_x=$6

    log "Получение текущего xray конфига VPS ..."
    local xray_json
    xray_json=$(vps_get_xray_config)

    # Проверяем что outbound с таким тегом ещё не существует
    local already
    already=$(echo "$xray_json" | jq --arg t "$OUTBOUND_TAG" \
        '[.outbounds // [] | .[] | select(.tag == $t)] | length')
    if [[ "$already" -gt 0 ]]; then
        log "  ℹ Outbound '$OUTBOUND_TAG' уже существует, пропускаем создание"
    else
        log "Добавление исходящего на VPS (→ $ru_ip:$port) ..."
        local new_outbound
        new_outbound=$(jq -n \
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
                    vnext: [{
                        address: $addr,
                        port: $port,
                        users: [{
                            id: $uuid,
                            flow: "xtls-rprx-vision",
                            encryption: "none"
                        }]
                    }]
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
        xray_json=$(echo "$xray_json" | jq --argjson ob "$new_outbound" \
            '.outbounds = (.outbounds // []) + [$ob]')
        log "  ✓ Исходящее добавлено (tag: $OUTBOUND_TAG)"
    fi

    # Проверяем routing правила
    local already_rules
    already_rules=$(echo "$xray_json" | jq --arg t "$OUTBOUND_TAG" \
        '[.routing.rules // [] | .[] | select(.outboundTag == $t)] | length')
    if [[ "$already_rules" -gt 0 ]]; then
        log "  ℹ Правила маршрутизации для '$OUTBOUND_TAG' уже существуют, пропускаем"
    else
        log "Добавление правил маршрутизации на VPS ..."
        local new_rules
        new_rules=$(jq -n \
            --arg  tag        "$OUTBOUND_TAG" \
            --argjson domains "$ROUTE_DOMAINS" \
            --argjson ips     "$ROUTE_IPS" \
            '[
                { type: "field", enabled: true, domain: $domains, outboundTag: $tag },
                { type: "field", enabled: true, ip: $ips, outboundTag: $tag }
            ]')

        # Вставляем новые правила перед blocked/bittorrent
        xray_json=$(echo "$xray_json" | jq \
            --argjson newrules "$new_rules" \
            --arg t "$OUTBOUND_TAG" \
            '.routing.rules = (
                (.routing.rules // [] | map(select(.outboundTag != "blocked" and ((.protocol // []) | contains(["bittorrent"]) | not))))
                + $newrules
                + (.routing.rules // [] | map(select(.outboundTag == "blocked" or ((.protocol // []) | contains(["bittorrent"])))))
            )')
        log "  ✓ Правила маршрутизации добавлены"
    fi

    # Сохраняем xray конфиг обратно (form-urlencoded)
    log "Сохранение xray конфига на VPS ..."
    local xray_str
    xray_str=$(echo "$xray_json" | jq -c .)
    local resp
    resp=$(curl "${CURL_BASE[@]}" "${VPS_AUTH_HEADER[@]}" -X POST "$VPS_URL/panel/api/xray/update" \
        --data-urlencode "xraySetting=$xray_str")
    echo "$resp" | jq -e '.success == true' &>/dev/null \
        || die "Не удалось сохранить xray конфиг VPS: $resp"
    log "  ✓ Xray конфиг VPS сохранён"
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
    ru_auth
    vps_auth

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

    # Шаг 2 + 3: исходящее и маршрутизация на VPS (через xray config)
    vps_update_xray "$RU_IP" "$PORT" "$UUID" "$SHORT_ID" "$PUB_KEY" "$SPIDER_X"

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
