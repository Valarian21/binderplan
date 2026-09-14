#!/usr/bin/env python3
"""Binderplan – Farbpaletten der Kartenbilder messen (`card_palette`).

Grundlage für „passende Karten": die Seite soll sich mit Karten füllen lassen, deren
Farbgebung zu dem passt, was schon im Fach liegt. Gemessen wird lokal aus dem Kartenbild,
ohne Modellaufruf — 20 ms je Karte.

Warum nicht `card_art_tags.farben` aus dem Bildmotiv-Index? Das Sichtmodell sollte drei
Farben je Karte liefern; gemessen am 14.09.2026 haben 8.570 Karten drei und **14.881 nur
eine**. Für einen Abstand zwischen zwei Paletten ist das zu dünn, und die Werte sind
geschätzt statt gemessen.

Zuschnitt: `AUSSCHNITT` nimmt nur den Bereich, der auf JEDEM Layout Illustration ist —
innerhalb des Bildfensters gerahmter Karten und mitten im Bild bei Vollbildkarten. Der
Kartenrand darf nicht hinein: mit ihm sortiert die Suche nach *Rahmenfarbe*, und zu einem
Base-Set-Glurak kommen acht beliebige gelb gerahmte Karten derselben Ära zurück (gemessen,
14.09.2026: ganze Karte 5,0 % gleiches Set / 13,8 % gleiches Jahr, dieser Ausschnitt
2,2 % / 9,1 %).

Aufruf (als root im Repo):
    python3 scripts/paletten.py                 # alle wichtigen Seltenheiten, holt fehlende Bilder
    python3 scripts/paletten.py --nur-cache     # nur Karten, deren Bild schon da ist
    python3 scripts/paletten.py --alle          # auch Common/Uncommon/Rare/Holo
    python3 scripts/paletten.py --neu           # vorhandene Messungen überschreiben
    python3 scripts/paletten.py --limit 500
"""
import argparse
import json
import math
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from urllib.request import urlopen

from PIL import Image

BASIS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASIS, "app.db")
CACHE = os.path.join(BASIS, "cache", "cards", "low")
DIENST = os.environ.get("BP_BASIS", "http://127.0.0.1:8103")

# Anteile der Kartenkante: links, oben, rechts, unten.
AUSSCHNITT = (0.12, 0.14, 0.88, 0.45)
FARBEN = 4                      # so viele Farben je Karte werden behalten
RASTER = 72                     # Kantenlänge, auf die vor dem Quantisieren verkleinert wird

# Die vier Seltenheiten, die draußen bleiben (Marcel, 14.09.2026): sie machen zwei Drittel des
# Katalogs aus, landen aber selten auf einer gestalteten Seite. „Rare" deckt Holo und Nicht-Holo
# zugleich ab — beide sind gemeint.
RAUS = ("Common", "Uncommon", "Rare", "Rare Holo", "Holo Rare")


def lab(r, g, b):
    """sRGB (0-255) → CIE-Lab. Abstände in Lab entsprechen ungefähr dem, was das Auge sieht;
    in RGB wären Grün und Blau gleich weit auseinander wie zwei Grüntöne."""
    def f(u):
        u /= 255
        return u / 12.92 if u <= 0.04045 else ((u + 0.055) / 1.055) ** 2.4
    r, g, b = f(r), f(g), f(b)
    x = (r * .4124 + g * .3576 + b * .1805) / .95047
    y = r * .2126 + g * .7152 + b * .0722
    z = (r * .0193 + g * .1192 + b * .9505) / 1.08883

    def h(t):
        return t ** (1 / 3) if t > .008856 else 7.787 * t + 16 / 116
    x, y, z = h(x), h(y), h(z)
    return round(116 * y - 16, 2), round(500 * (x - y), 2), round(200 * (y - z), 2)


def palette(pfad):
    """→ (lab-Liste [[L,a,b,anteil]], hex-Liste, Helligkeit, Buntheit) oder None."""
    with Image.open(pfad) as roh:
        im = roh.convert("RGB")
        w, h = im.size
        if w < 40 or h < 56:
            return None
        im = im.crop((int(w * AUSSCHNITT[0]), int(h * AUSSCHNITT[1]),
                      int(w * AUSSCHNITT[2]), int(h * AUSSCHNITT[3])))
        im.thumbnail((RASTER, RASTER))
        q = im.quantize(colors=FARBEN + 2, method=Image.MEDIANCUT)
    pal = q.getpalette()
    zaehl = sorted(q.getcolors() or [], reverse=True)
    if not zaehl:
        return None
    gesamt = sum(n for n, _ in zaehl)
    labs, hexe = [], []
    for n, i in zaehl[:FARBEN]:
        rgb = tuple(pal[i * 3:i * 3 + 3])
        L, a, b = lab(*rgb)
        labs.append([L, a, b, round(n / gesamt, 4)])
        hexe.append("#%02x%02x%02x" % rgb)
    hell = sum(x[0] * x[3] for x in labs) / max(1e-6, sum(x[3] for x in labs))
    bunt = sum(math.hypot(x[1], x[2]) * x[3] for x in labs) / max(1e-6, sum(x[3] for x in labs))
    return labs, hexe, round(hell, 2), round(bunt, 2)


def bildpfad(card_id):
    return os.path.join(CACHE, re.sub(r"[^A-Za-z0-9._%-]", "_", card_id) + ".webp")


def bild_holen(card_id, versuche=3):
    """Den laufenden Dienst das Bild holen lassen — er kennt die Zweitquellen und erzwingt IPv4
    (siehe bilder.py). Ein eigener Abruf hier würde beides noch einmal nachbauen.

    Mit Wiederholung: im ersten Lauf am 14.09.2026 haben die Bildquellen nach rund 1.150
    Abrufen für einige Minuten am Stück 404 geliefert und danach wieder normal geantwortet.
    Ein einziger Versuch je Karte hat deshalb 5.000 Karten als „ohne Bild" abgehakt, die es
    sehr wohl gibt. Ein 404 heißt hier also erst nach dem dritten Versuch etwas."""
    for n in range(versuche):
        try:
            with urlopen(f"{DIENST}/api/img/card/{quote(card_id, safe='')}?lang=de", timeout=45) as r:
                r.read(1)
            if os.path.exists(bildpfad(card_id)):
                return True
        except Exception:
            pass
        if n + 1 < versuche:
            time.sleep(1.5 * (n + 1))
    return False


def tabelle(con):
    con.execute("""CREATE TABLE IF NOT EXISTS card_palette (
        card_id TEXT PRIMARY KEY,
        lab TEXT, hex TEXT,
        helligkeit REAL, buntheit REAL,
        ausschnitt TEXT, gemessen_am TEXT)""")
    con.commit()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--alle", action="store_true", help="auch Common/Uncommon/Rare/Holo")
    p.add_argument("--nur-cache", action="store_true", help="keine Bilder nachladen")
    p.add_argument("--neu", action="store_true", help="vorhandene Messungen überschreiben")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--spuren", type=int, default=6, help="gleichzeitige Bildabrufe")
    a = p.parse_args()

    con = sqlite3.connect(DB, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    tabelle(con)

    wo = "(image_de IS NOT NULL OR image_en IS NOT NULL OR image_alt IS NOT NULL)"
    par = []
    if not a.alle:
        wo += " AND rarity NOT IN (%s)" % ",".join("?" * len(RAUS))
        par += list(RAUS)
    if not a.neu:
        wo += " AND id NOT IN (SELECT card_id FROM card_palette)"
    ids = [r[0] for r in con.execute(f"SELECT id FROM cards WHERE {wo} ORDER BY id", par)]
    if a.limit:
        ids = ids[:a.limit]
    da = [i for i in ids if os.path.exists(bildpfad(i))]
    fehlt = [i for i in ids if not os.path.exists(bildpfad(i))]
    print(f"zu messen: {len(ids)}  ·  Bild da: {len(da)}  ·  zu holen: {len(fehlt)}", flush=True)

    def messen(card_id):
        try:
            e = palette(bildpfad(card_id))
        except Exception:
            e = None
        if not e:
            return False
        labs, hexe, hell, bunt = e
        con.execute("INSERT OR REPLACE INTO card_palette"
                    " (card_id, lab, hex, helligkeit, buntheit, ausschnitt, gemessen_am)"
                    " VALUES (?,?,?,?,?,?,datetime('now'))",
                    (card_id, json.dumps(labs), json.dumps(hexe), hell, bunt,
                     ",".join(str(x) for x in AUSSCHNITT)))
        # Sofort abschließen, nicht erst nach 100 Karten sammeln. Zwischen zwei Karten wartet
        # dieser Lauf auf ein Bild aus dem Netz — eine offene Schreibtransaktion sperrt so
        # lange die Datenbank, und der Dienst kam beim Neustart nicht mehr durch seine
        # Migrationen (14.09.2026: uvicorn hing 90 s im Import, die App war offline).
        con.commit()
        return True

    t0 = time.time()
    ok = schlecht = 0
    for n, cid in enumerate(da, 1):
        if messen(cid):
            ok += 1
        else:
            schlecht += 1
        if n % 500 == 0:
            print(f"  aus dem Cache: {n}/{len(da)}  ({time.time() - t0:.0f} s)", flush=True)

    if fehlt and not a.nur_cache:
        print(f"hole {len(fehlt)} Bilder über den Dienst, {a.spuren} gleichzeitig …", flush=True)
        hintereinander = 0
        with ThreadPoolExecutor(max_workers=a.spuren) as pool:
            for n, (cid, geholt) in enumerate(zip(fehlt, pool.map(bild_holen, fehlt)), 1):
                if geholt and messen(cid):
                    ok += 1
                    hintereinander = 0
                else:
                    schlecht += 1
                    hintereinander += 1
                if n % 100 == 0:
                    print(f"  geholt: {n}/{len(fehlt)}  ({time.time() - t0:.0f} s, "
                          f"{ok} gemessen, {schlecht} ohne Bild)", flush=True)
                # Reißt die Quelle für längere Zeit ab, ist Weitermachen verlorene Zeit —
                # der Lauf ist wiederaufnehmbar, gemessene Karten bleiben in der Tabelle.
                if hintereinander >= 250:
                    print("  250 Fehlschläge am Stück — die Bildquelle antwortet nicht mehr. "
                          "Später noch einmal starten, der Lauf setzt fort.", flush=True)
                    break

    gesamt = con.execute("SELECT COUNT(*) FROM card_palette").fetchone()[0]
    con.close()
    print(f"fertig: {ok} gemessen, {schlecht} ohne Bild · in der Tabelle stehen {gesamt} "
          f"· {time.time() - t0:.0f} s", flush=True)
    return 0 if ok or not ids else 1


if __name__ == "__main__":
    sys.exit(main())
