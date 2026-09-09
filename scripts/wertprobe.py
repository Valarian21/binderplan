"""Zeigt alle Stellen, die den Wert einer Sammlung nennen, nebeneinander.

Nach dem Zusammenführen auf wert.py müssen sie übereinstimmen. Aufruf:
    venv/bin/python scripts/wertprobe.py <token> [basis]
Der Token ist eine Session aus der Tabelle `sessions`; die Basis ist voreingestellt
http://127.0.0.1:8103 (Produktion auf dem VPS).
"""
import json
import sys
import urllib.request

basis = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8103"
kopf = {"Authorization": "Bearer " + sys.argv[1]}


def hol(pfad):
    r = urllib.request.Request(basis + pfad, headers=kopf)
    with urllib.request.urlopen(r, timeout=60) as a:
        return json.load(a)


werte = {}
werte["Sammlung · Kopfband"] = hol("/api/sammlung/kopf").get("wert")
werte["Sammlung · Übersicht (Startseite)"] = hol("/api/sammlung/uebersicht").get("wert")
werte["Auswertung"] = hol("/api/analytics/sammlung?tage=30").get("wert")
d = hol("/api/digest").get("digest")
if d:
    werte["Wochenrückblick"] = d.get("wert")
sets = hol("/api/sammlung/sets").get("sets", [])
werte["Sammlung · Summe der Set-Werte"] = round(sum(s["wert"] for s in sets), 2)

echt = [v for v in werte.values() if v is not None]
spanne = (max(echt) - min(echt)) if echt else 0
breite = max(len(k) for k in werte)
for k, v in werte.items():
    print(f"{k:<{breite}}  {v if v is not None else '—':>12}")
print("-" * (breite + 14))
print(f"{'Spanne':<{breite}}  {spanne:>12.2f}")
if spanne > 0.05:
    print("\nFEHLER: Die Werte laufen auseinander. Jede Stelle muss wert.py benutzen.")
    sys.exit(1)
print("\nAlle Stellen nennen denselben Wert.")
