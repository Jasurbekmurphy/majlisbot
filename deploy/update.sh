#!/usr/bin/env bash
# GitHub'dagi oxirgi versiyani olib, botni qayta ishga tushiradi.
#   sudo bash /opt/davomat-bot/deploy/update.sh
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ $EUID -eq 0 ]] || { echo "sudo bilan ishga tushiring"; exit 1; }

"$APP_DIR/deploy/backup.sh" || true
git -C "$APP_DIR" -c safe.directory="$APP_DIR" pull --ff-only
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q
chown -R davomat:davomat "$APP_DIR"
chmod +x "$APP_DIR"/deploy/*.sh
systemctl restart davomat
sleep 3
systemctl is-active --quiet davomat && echo "✔ Yangilandi va ishlayapti" || journalctl -u davomat -n 30 --no-pager
