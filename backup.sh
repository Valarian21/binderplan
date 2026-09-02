#!/usr/bin/env bash
# Binderplan – Sicherung der App-Datenbank samt Rotation.
#
# Warum ein eigenes Skript: app.db liegt in /root/apps/binderplan und wird vom Dashboard-Backup
# nicht erfasst. Sie enthält Konten, Binder, Sammlungen, Bestellungen und das Credit-Journal —
# also alles, was ein Kunde bezahlt hat. Ohne Sicherung wäre ein Plattenfehler das Ende.
#
# Cron (root):  15 3 * * * /root/apps/binderplan/backup.sh >> /root/apps/binderplan/backup.log 2>&1

set -euo pipefail

APP_DIR="/root/apps/binderplan"
ZIEL="$APP_DIR/backups"
TAG="$ZIEL/taeglich"
WOCHE="$ZIEL/woechentlich"
STAND=$(date +%Y%m%d_%H%M%S)

mkdir -p "$TAG" "$WOCHE"
chmod 700 "$ZIEL" "$TAG" "$WOCHE"

DATEI="$TAG/app_${STAND}.db"

echo "[$(date '+%F %T')] Sicherung startet"

# SQLite-Online-Backup: konsistent, auch während der Dienst schreibt.
"$APP_DIR/venv/bin/python" - "$APP_DIR/app.db" "$DATEI" <<'PY'
import sqlite3, sys
quelle, ziel = sys.argv[1], sys.argv[2]
q = sqlite3.connect(f"file:{quelle}?mode=ro", uri=True)
z = sqlite3.connect(ziel)
with z:
    q.backup(z)
z.close(); q.close()
print("  Datenbank gesichert")
PY

# Die .env enthält Stripe- und Mailzugang; sie wird mitgesichert, aber nur für root lesbar.
cp "$APP_DIR/.env" "$TAG/env_${STAND}.txt"
chmod 600 "$TAG/env_${STAND}.txt" "$DATEI"

gzip -f "$DATEI"
echo "  $(du -h "${DATEI}.gz" | cut -f1) → ${DATEI}.gz"

# Sonntags eine Wochenkopie zurücklegen
if [ "$(date +%u)" = "7" ]; then
    cp "${DATEI}.gz" "$WOCHE/app_${STAND}.db.gz"
fi

# Rotation: 14 Tage, 8 Wochen
find "$TAG" -name 'app_*.db.gz' -mtime +14 -delete
find "$TAG" -name 'env_*.txt' -mtime +14 -delete
find "$WOCHE" -name 'app_*.db.gz' -mtime +56 -delete

# Off-Site: Kopie auf die zweite Platte bzw. in das Dashboard-Backupverzeichnis, das
# bereits vom Empire-Backup mitgenommen wird. Ein zweiter Ort auf demselben Server ist
# kein echtes Off-Site — er schützt aber gegen versehentliches Löschen im App-Ordner.
EXTERN="/home/developer/ai_empire/backups/binderplan"
if [ -d "/home/developer/ai_empire/backups" ]; then
    mkdir -p "$EXTERN"
    cp "${DATEI}.gz" "$EXTERN/"
    chown -R developer:developer "$EXTERN" 2>/dev/null || true
    find "$EXTERN" -name 'app_*.db.gz' -mtime +14 -delete
    echo "  Zweitkopie unter $EXTERN"
fi

echo "[$(date '+%F %T')] Sicherung fertig ($(ls -1 "$TAG"/app_*.db.gz | wc -l) Stände)"
