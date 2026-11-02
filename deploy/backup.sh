#!/usr/bin/env bash
# Bazaning zaxira nusxasini backups/ ga oladi, 30 kundan eskilarini o'chiradi.
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="$APP_DIR/davomat.db"
[[ -f "$DB" ]] || exit 0
mkdir -p "$APP_DIR/backups"
OUT="$APP_DIR/backups/davomat_$(date +%Y%m%d_%H%M).db"
"$APP_DIR/venv/bin/python" -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()" "$DB" "$OUT"
find "$APP_DIR/backups" -name 'davomat_*.db' -mtime +30 -delete
