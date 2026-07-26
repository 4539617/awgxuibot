#!/bin/bash
# First-time setup: install acme.sh and issue a short-lived Let's Encrypt
# certificate for a bare public IP address.
#
# Usage (run from project root or caddy/ directory):
#   sudo bash caddy/scripts/acme-install.sh <PUBLIC_IP> <EMAIL>
#
# Idempotent — safe to run multiple times:
#   - Cert in acme.sh but not in /etc/ssl/cascade/ → installs it, starts Caddy
#   - Cert already in /etc/ssl/cascade/            → starts Caddy only
#   - No cert anywhere                             → full issue → install → start
#
# Requirements:
#   - Port 80 must be reachable from the internet during FIRST issuance
#   - Run from project root directory
#   - caddy/.env must exist with ADMIN_PATH and CASCADE_PORT set
#   - Docker must be installed and running

set -euo pipefail

IP="${1:?Usage: $0 <PUBLIC_IP> <EMAIL>}"
EMAIL="${2:?Usage: $0 <PUBLIC_IP> <EMAIL>}"

CERT_DIR="/etc/ssl/cascade"
ACME_WEBROOT="/srv/acme"
CADDY_CONTAINER="cascade-caddy"
ACME_CERT_DIR="$HOME/.acme.sh/${IP}_ecc"

echo "==> Creating directories..."
mkdir -p "$CERT_DIR" "$ACME_WEBROOT"
chmod 755 "$ACME_WEBROOT"
chmod 700 "$CERT_DIR"

# ── Install acme.sh if not present ───────────────────────────────────────────
if [ ! -f "$HOME/.acme.sh/acme.sh" ]; then
    echo "==> Installing acme.sh..."
    curl https://get.acme.sh | sh -s email="$EMAIL"
fi
# shellcheck disable=SC1090
source "$HOME/.acme.sh/acme.sh.env" 2>/dev/null || export PATH="$HOME/.acme.sh:$PATH"

# ── RENEW_DAYS=1 for 6-day shortlived certs ──────────────────────────────────
echo "==> Setting RENEW_DAYS=1 for shortlived certs..."
if grep -q "^RENEW_DAYS=" "$HOME/.acme.sh/account.conf" 2>/dev/null; then
    sed -i 's/^RENEW_DAYS=.*/RENEW_DAYS=1/' "$HOME/.acme.sh/account.conf"
else
    echo 'RENEW_DAYS=1' >> "$HOME/.acme.sh/account.conf"
fi

# ── Determine state ───────────────────────────────────────────────────────────
# Case A: cert already installed in CERT_DIR → just (re)start Caddy
# Case B: cert in acme.sh store but not in CERT_DIR → install-cert then start
# Case C: no cert anywhere → full issue → install → start → switch to webroot

ACME_HAS_CERT=false
if [ -f "$ACME_CERT_DIR/${IP}.cer" ] && [ -f "$ACME_CERT_DIR/${IP}.key" ]; then
    ACME_HAS_CERT=true
fi

DEST_HAS_CERT=false
if [ -f "$CERT_DIR/server.crt" ] && [ -f "$CERT_DIR/server.key" ]; then
    DEST_HAS_CERT=true
fi

echo "==> State: acme_has_cert=$ACME_HAS_CERT  dest_has_cert=$DEST_HAS_CERT"

if [ "$DEST_HAS_CERT" = "true" ]; then
    # ── Case A: cert already installed → just start Caddy ────────────────────
    echo "==> Certificate already in $CERT_DIR — skipping issuance and install."

elif [ "$ACME_HAS_CERT" = "true" ]; then
    # ── Case B: cert in acme.sh but not copied to CERT_DIR ───────────────────
    echo "==> Certificate found in acme.sh store — installing to $CERT_DIR..."
    # reloadcmd may fail if Caddy container doesn't exist yet — that's OK,
    # we start it explicitly below. Use "|| true" to prevent set -e from exiting.
    ~/.acme.sh/acme.sh \
        --install-cert -d "$IP" --ecc \
        --key-file       "$CERT_DIR/server.key" \
        --fullchain-file "$CERT_DIR/server.crt" \
        --reloadcmd      "docker restart $CADDY_CONTAINER || true"
    chmod 600 "$CERT_DIR/server.key"
    chmod 644 "$CERT_DIR/server.crt"

else
    # ── Case C: no cert anywhere → full issue ────────────────────────────────
    echo "==> Issuing short-lived certificate for $IP (standalone mode)..."
    ~/.acme.sh/acme.sh \
        --issue \
        --server letsencrypt \
        -d "$IP" \
        --standalone \
        --cert-profile shortlived \
        --days 1

    echo "==> Installing certificate to $CERT_DIR..."
    # Same: reloadcmd is best-effort here — Caddy starts explicitly below.
    ~/.acme.sh/acme.sh \
        --install-cert -d "$IP" --ecc \
        --key-file       "$CERT_DIR/server.key" \
        --fullchain-file "$CERT_DIR/server.crt" \
        --reloadcmd      "docker restart $CADDY_CONTAINER || true"
    chmod 600 "$CERT_DIR/server.key"
    chmod 644 "$CERT_DIR/server.crt"
fi

# ── Start Caddy ───────────────────────────────────────────────────────────────
echo "==> Starting Caddy..."
docker compose -f docker-compose.caddy.yml up -d --build
echo "==> Waiting for Caddy to start..."
sleep 3

# Verify Caddy is actually running
if ! docker ps --format '{{.Names}}' | grep -q "^${CADDY_CONTAINER}$"; then
    echo "ERROR: Caddy container failed to start. Logs:"
    docker logs "$CADDY_CONTAINER" 2>&1 | tail -20
    exit 1
fi

# ── Switch to webroot for future renewals (Case C only) ──────────────────────
# After standalone issue Le_Webroot='no' — renewals would try to bind port 80
# again (conflict with running Caddy). Re-issue via webroot fixes this.
if [ "$DEST_HAS_CERT" = "false" ] && [ "$ACME_HAS_CERT" = "false" ]; then
    echo "==> Switching to webroot mode for future renewals..."
    ~/.acme.sh/acme.sh \
        --issue \
        --server letsencrypt \
        -d "$IP" \
        --webroot "$ACME_WEBROOT" \
        --cert-profile shortlived \
        --days 1 \
        --force
fi

echo ""
echo "Done. Certificate installed to $CERT_DIR"
echo ""
echo "Renewal schedule:"
echo "  cron: $(crontab -l 2>/dev/null | grep acme || echo 'not found — run: acme.sh --install-cronjob')"
echo "  next: $(grep Le_NextRenewTimeStr "$HOME/.acme.sh/${IP}_ecc/${IP}.conf" 2>/dev/null || echo 'check ~/.acme.sh/')"
echo ""
echo "Access Cascade at: https://$IP/<ADMIN_PATH>/"
