#!/usr/bin/env bash
# Binderplan – einfacher Erreichbarkeitswächter.
#
# Prüft alle fünf Minuten, ob die App antwortet. Zwei Fehlschläge hintereinander lösen einen
# Neustart aus und melden das per Telegram über das Hermes-Gateway (dieselbe Nachrichtenkette
# wie Jarvis). Ohne diesen Wächter merkt niemand, wenn der Dienst nachts stehen bleibt.
#
# Cron (root):  */5 * * * * /root/apps/binderplan/wacht.sh >> /root/apps/binderplan/wacht.log 2>&1

set -uo pipefail
MARKE="/tmp/binderplan_wacht_fehler"
URL="http://127.0.0.1:8103/api/health"

if curl -fsS --max-time 8 "$URL" | grep -q '"ok"'; then
    if [ -f "$MARKE" ]; then
        echo "[$(date '+%F %T')] wieder erreichbar"
        rm -f "$MARKE"
    fi
    exit 0
fi

FEHLER=$(( $(cat "$MARKE" 2>/dev/null || echo 0) + 1 ))
echo "$FEHLER" > "$MARKE"
echo "[$(date '+%F %T')] keine Antwort (Fehlversuch $FEHLER)"

if [ "$FEHLER" -ge 2 ]; then
    echo "  Dienst wird neu gestartet"
    systemctl restart app-binderplan
    sleep 5
    if curl -fsS --max-time 8 "$URL" >/dev/null; then
        echo "  Neustart hat geholfen"
        rm -f "$MARKE"
    else
        echo "  Neustart hat NICHT geholfen"
    fi
fi
