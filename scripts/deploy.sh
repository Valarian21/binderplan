#!/bin/bash
# Deploy an Ort und Stelle (das Repo ist die Produktion): Bauprüfung → Asset-Version → Neustart → Rauchtest.
# Aufruf als root im Repo: scripts/deploy.sh      (BP_TOKEN=… für die Konto-Prüfungen des Rauchtests)
set -e
cd "$(dirname "$0")/.."
python3 scripts/pruefen.py .
V=$(date +%Y%m%d%H%M)
sed -i -E "s#(assets/[a-z_]+\.(js|css))\?v=[A-Za-z0-9]+#\1?v=$V#g" index.html landing.html landing_en.html
systemctl restart app-binderplan
for i in 1 2 3 4 5 6 7 8; do sleep 1; code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8103/app || true); [ "$code" = "200" ] && break; done
[ "$code" = "200" ] || { echo "DEPLOY FEHLGESCHLAGEN"; journalctl -u app-binderplan -n 30 --no-pager; exit 1; }
python3 scripts/rauchtest.py http://127.0.0.1:8103
echo "Deploy ok · Assets ?v=$V"
