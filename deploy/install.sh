#!/usr/bin/env bash
# Majlis davomati botini toza Ubuntu (22.04 / 24.04) serverga o'rnatadi.
# Ishlatish:
#   git clone <repo-url> /opt/davomat-bot
#   sudo bash /opt/davomat-bot/deploy/install.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="davomat"
SERVICE="davomat"

green() { printf '\033[32m%s\033[0m\n' "$*"; }
red()   { printf '\033[31m%s\033[0m\n' "$*"; }
ask()   { local v; read -rp "$1" v </dev/tty; echo "$v"; }

[[ $EUID -eq 0 ]] || { red "Root huquqi kerak: sudo bash $0"; exit 1; }
grep -qi ubuntu /etc/os-release || red "Ogohlantirish: skript Ubuntu uchun yozilgan."

# ---------- 1. Ma'lumotlarni so'rash ----------
if [[ -f "$APP_DIR/.env" ]]; then
  green ".env allaqachon bor — undagi sozlamalar ishlatiladi."
  DOMAIN="$(grep -E '^WEBAPP_URL=' "$APP_DIR/.env" | sed -E 's#^WEBAPP_URL=https://([^/]+).*#\1#')"
else
  echo
  green "=== Bot sozlamalari ==="
  MY_IP="$(curl -4fsS https://api.ipify.org || true)"
  echo "O'z domeningiz bo'lmasa, Enter bosing — bepul manzil beriladi: ${MY_IP//./-}.sslip.io"
  DOMAIN="$(ask 'Domen (masalan davomat.kompaniya.uz): ')"
  if [[ -z "$DOMAIN" && -n "$MY_IP" ]]; then
    DOMAIN="${MY_IP//./-}.sslip.io"
    green "Manzil: $DOMAIN"
  fi
  BOT_TOKEN="$(ask 'BotFather bergan token: ')"
  ADMIN_IDS="$(ask 'Admin Telegram ID(lar)i, vergul bilan (@userinfobot dan): ')"
  [[ -n "$DOMAIN" && -n "$BOT_TOKEN" && -n "$ADMIN_IDS" ]] || { red "Hamma maydonlar to'ldirilishi kerak."; exit 1; }
  cat > "$APP_DIR/.env" <<EOF
BOT_TOKEN=$BOT_TOKEN
ADMIN_IDS=$ADMIN_IDS
WEBAPP_URL=https://$DOMAIN/app
WEB_HOST=127.0.0.1
WEB_PORT=8080
DB_PATH=$APP_DIR/davomat.db
TIMEZONE=Asia/Tashkent
MAX_GPS_ACCURACY=300
EOF
fi
[[ -n "${DOMAIN:-}" ]] || { red "Domen aniqlanmadi (.env dagi WEBAPP_URL ni tekshiring)."; exit 1; }

SERVER_IP="$(curl -4fsS https://api.ipify.org || true)"
DOMAIN_IP="$(getent ahostsv4 "$DOMAIN" | awk 'NR==1{print $1}' || true)"
if [[ -n "$SERVER_IP" && "$SERVER_IP" != "$DOMAIN_IP" ]]; then
  red "DIQQAT: $DOMAIN -> ${DOMAIN_IP:-topilmadi}, server IP esa $SERVER_IP."
  red "DNS'da A-yozuvni $SERVER_IP ga yo'naltiring, aks holda HTTPS sertifikat olinmaydi."
  [[ "$(ask 'Baribir davom etilsinmi? (y/N): ')" =~ ^[yY]$ ]] || exit 1
fi

# ---------- 2. Paketlar ----------
green "=== Paketlar o'rnatilmoqda ==="
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git curl debian-keyring debian-archive-keyring apt-transport-https gnupg

if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y
  apt-get install -y caddy
fi

# ---------- 3. Foydalanuvchi va Python muhiti ----------
green "=== Python muhiti ==="
id "$APP_USER" &>/dev/null || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q
mkdir -p "$APP_DIR/backups"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/.env"
chmod +x "$APP_DIR"/deploy/*.sh

# ---------- 4. systemd xizmati ----------
green "=== Xizmat (systemd) ==="
cat > "/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=Majlis davomati Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python -m bot
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null
systemctl restart "$SERVICE"

# ---------- 5. HTTPS (Caddy) ----------
green "=== HTTPS (Caddy) ==="
CADDYFILE=/etc/caddy/Caddyfile
BLOCK="$DOMAIN {
    encode gzip
    reverse_proxy 127.0.0.1:8080
}"
if [[ ! -s $CADDYFILE ]] || grep -qE '^:80\b' "$CADDYFILE"; then
  echo "$BLOCK" > "$CADDYFILE"          # standart (bo'sh) konfiguratsiyani almashtiramiz
elif ! grep -q "^$DOMAIN " "$CADDYFILE"; then
  printf '\n%s\n' "$BLOCK" >> "$CADDYFILE"  # boshqa saytlar bo'lsa, oxiriga qo'shamiz
fi
caddy validate --config "$CADDYFILE" --adapter caddyfile >/dev/null
systemctl enable caddy >/dev/null
systemctl reload caddy || systemctl restart caddy

if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
fi

# ---------- 6. Kunlik zaxira nusxa (03:30) ----------
cat > /etc/cron.d/davomat-backup <<EOF
30 3 * * * $APP_USER $APP_DIR/deploy/backup.sh >/dev/null 2>&1
EOF

# ---------- 7. Tekshirish ----------
green "=== Tekshirish ==="
sleep 4
if systemctl is-active --quiet "$SERVICE"; then green "✔ Bot ishlayapti"; else red "✘ Bot ishga tushmadi:"; journalctl -u "$SERVICE" -n 30 --no-pager; exit 1; fi
if curl -fsS http://127.0.0.1:8080/health >/dev/null; then green "✔ Ichki server javob beryapti"; fi
for i in {1..12}; do
  if curl -fsS "https://$DOMAIN/health" >/dev/null 2>&1; then green "✔ https://$DOMAIN ishlayapti"; break; fi
  [[ $i -eq 12 ]] && red "HTTPS hali tayyor emas — DNS yangilanishini kuting va tekshiring: journalctl -u caddy -n 50"
  sleep 5
done

echo
green "Tayyor! Endi botga /start yozing, keyin /admin."
echo "Loglar:    journalctl -u $SERVICE -f"
echo "Yangilash: sudo bash $APP_DIR/deploy/update.sh"
