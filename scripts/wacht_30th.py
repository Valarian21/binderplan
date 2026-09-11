#!/usr/bin/env python3
"""Wächter für das Vorab-Set „30th Celebration": neue Karten holen, sobald sie auftauchen.

Sieben Karten des Sets waren am 11.09.2026 noch nicht enthüllt (143, 151, 152, 154, 159,
160, 161); vier davon stehen als bildlose Einträge im Katalog, drei sind nicht einmal dem
Namen nach bekannt. Bis zum Erscheinen am 16.09.2026 werden sie nach und nach gezeigt.
Dieser Wächter fragt stündlich die Serebii-Setliste ab und trägt alles Neue sofort ein:
Karte in die Datenbank, Scan in den Bild-Cache, kurze Nachricht über den Jarvis-Bot.

Gemeldet wird nur, wenn sich etwas geändert hat — ein Wächter, der stündlich „nichts Neues"
schreibt, wird nach zwei Tagen ignoriert.

Cron (root):  17 * * * * /root/apps/binderplan/venv/bin/python \
                  /root/apps/binderplan/scripts/wacht_30th.py \
                  >> /root/apps/binderplan/wacht_30th.log 2>&1

Aufrufe:
  wacht_30th.py            prüfen, eintragen, bei Änderung melden
  wacht_30th.py --trocken  nur zeigen, was passieren würde
  wacht_30th.py --melden   auch ohne Änderung eine Nachricht schicken (Probe)
"""
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vorab_30th as V          # noqa: E402  – Setliste, Schreibweg und Bild-Cache

ENV = V.BASE / ".env"


def _env(name, rueckfall=""):
    """Eine Zeile aus der .env lesen. Bewusst ohne Abhängigkeit zur App: der Wächter soll
    auch dann noch melden können, wenn der Dienst selbst nicht läuft."""
    try:
        for zeile in ENV.read_text(encoding="utf-8").splitlines():
            zeile = zeile.strip()
            if zeile.startswith(name + "="):
                return zeile.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return rueckfall


def melden(text):
    """Kurze Nachricht über denselben Telegram-Bot wie Jarvis (siehe auth.betreiber_melden)."""
    import os
    if os.environ.get("BP_DB"):
        # Probelauf auf einer Kopie — der soll aussehen wie der Ernstfall, aber niemanden
        # anpiepen. Eine Meldung über Karten, die nur in einer Kopie stehen, wäre eine Lüge.
        print("  (Probelauf auf BP_DB — keine Telegram-Nachricht)")
        return False
    token, chat = _env("TELEGRAM_TOKEN"), _env("TELEGRAM_CHAT")
    if not token or not chat:
        print("  keine Telegram-Zugangsdaten in der .env — nichts gemeldet")
        return False
    daten = json.dumps({"chat_id": chat, "text": text[:3500],
                        "disable_web_page_preview": True}).encode()
    anfrage = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=daten,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(anfrage, timeout=15) as antwort:
            antwort.read()
        return True
    except Exception as e:                      # eine Meldung darf den Lauf nie abbrechen
        print("  Telegram fehlgeschlagen:", e)
        return False


def stand():
    """Was steht heute in der Datenbank: Nummer → hat einen Scan?"""
    con = sqlite3.connect(V.DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT local_id, name_en, image_alt FROM cards WHERE set_id = ?",
                       (V.SET_ID,)).fetchall()
    con.close()
    return {r["local_id"]: (r["name_en"], bool(r["image_alt"])) for r in rows}


def main(trocken=False, immer_melden=False):
    alt = stand()
    karten = V.karten_lesen()
    if len(karten) < 100:                       # abgeschnittene oder umgebaute Seite
        print(f"Serebii lieferte nur {len(karten)} Karten — verdächtig, Lauf abgebrochen")
        return 1

    # Neu ist beides: eine Nummer, die es noch gar nicht gab, und eine, die bisher nur als
    # angekündigter Eintrag ohne Scan im Katalog stand und jetzt ein Bild hat.
    neu = [k for k in karten if k["local_id"] not in alt]
    bebildert = [k for k in karten
                 if k["local_id"] in alt and not alt[k["local_id"]][1]]
    if not neu and not bebildert and not immer_melden:
        print(f"[{V.SET_ID}] nichts Neues ({len(karten)} Karten auf Serebii, "
              f"{len(alt)} im Katalog)")
        return 0

    if trocken:
        for k in neu:
            print(f"  NEU        {k['local_id']} {k['name']} ({k['rarity']})")
        for k in bebildert:
            print(f"  MIT SCAN   {k['local_id']} {k['name']} ({k['rarity']})")
        print("Probelauf — nichts geschrieben.")
        return 0

    if neu or bebildert:
        V.schreiben(karten)
        V.bilder_vorladen(neu + bebildert)

    jetzt = stand()
    offen = sorted(n for n, (_, hat_scan) in jetzt.items() if not hat_scan)
    zeilen = []
    for k in neu + bebildert:
        zeilen.append(f"• {k['local_id']} {k['name']} – {k['rarity'] or 'Seltenheit unklar'}")
    anzahl = len(neu) + len(bebildert)
    text = "30th Celebration: "
    if zeilen:
        text += (f"{anzahl} neue Karte im Katalog\n\n" if anzahl == 1
                 else f"{anzahl} neue Karten im Katalog\n\n") + "\n".join(zeilen) + "\n"
    else:
        text += "Stand unverändert\n"
    text += f"\nIm Katalog: {len(jetzt)} Karten."
    if offen:
        text += " Ohne Scan: " + ", ".join(offen) + "."
    else:
        text += (" Alle bekannten Karten haben jetzt einen Scan — ab dem 16.09. "
                 "scripts/vorab_30th.py --ersetzen.")
    print(text)
    if zeilen or immer_melden:
        melden(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(trocken="--trocken" in sys.argv, immer_melden="--melden" in sys.argv))
