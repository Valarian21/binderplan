#!/usr/bin/env python3
"""Vorab-Set „30th Celebration" (cel30) aus der Serebii-Setliste anlegen.

Warum von Hand: TCGdex und pokemontcg.io kennen das Set am 11.09.2026 noch nicht
(neuestes Set dort: me05 „Pitch Black", 17.07.2026). Erscheinungstag ist der
16.09.2026 — bis dahin gibt es die Karten nur über die vorab veröffentlichten
Scans. Serebii führt 184 der 199 Karten mit vollem Scan (660x920).

Der Eintrag ist bewusst so gebaut, dass der reguläre Sync ihn später *überschreibt*
statt zu verdoppeln — vorausgesetzt, TCGdex vergibt dieselbe Set-ID (`cel30`).
`_sync_sets` und `_sync_cards` fassen bestehende Zeilen per ON CONFLICT an und
löschen nie ein Set. Sobald echte TCGdex-Bilder da sind, gewinnt `image_de`/
`image_en` gegenüber unserem `image_alt` — dafür muss aber der Bild-Cache einmal
geleert werden (`--cache-leeren`), weil `card_image()` eine vorhandene Datei nie
erneut holt.

Aufrufe:
  python3 scripts/vorab_30th.py --trocken       nur anzeigen, nichts schreiben
  python3 scripts/vorab_30th.py                 Set + Karten in die DB schreiben
  python3 scripts/vorab_30th.py --bilder        Scans in den Bild-Cache vorladen
  python3 scripts/vorab_30th.py --pruefen       hat TCGdex das Set inzwischen?
  python3 scripts/vorab_30th.py --verweise      wie viele Sammlungen/Binder hängen dran?
  python3 scripts/vorab_30th.py --ersetzen      Ablösung durch den echten Katalog (Probelauf)
  python3 scripts/vorab_30th.py --ersetzen --wirklich   … und ausführen
  python3 scripts/vorab_30th.py --cache-leeren  Bild-Cache des Sets verwerfen
  python3 scripts/vorab_30th.py --entfernen     Set + Karten wieder löschen (ohne Zuordnung!)

`--entfernen` ist der grobe Weg und verliert die Verweise der Kunden. Der richtige Weg
ab dem 16.09.2026 ist `--ersetzen`: der ordnet die Vorab-IDs den echten zu und biegt
Sammlung, Wunschliste, Binderfächer und Preisalarme mit um.
"""
import html
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
# BP_DB erlaubt einen Probelauf auf einer Kopie der Datenbank — die Ersetzung schreibt
# in fremde Tabellen (Sammlung, Binder, Wunschliste), das will vorher geprüft sein.
DB = Path(os.environ.get("BP_DB") or (BASE / "app.db"))
CACHE = Path(os.environ.get("BP_CACHE") or (BASE / "cache"))
API = os.environ.get("BP_API") or "http://127.0.0.1:8103"

SET_ID = "cel30"
QUELLE = "https://www.serebii.net/card/30thcelebration/"
BILD = "https://www.serebii.net/card/30thcelebration/{n}.jpg"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"}

# Serebii-Symbole -> Seltenheiten in der Schreibweise von TCGdex. Die drei neuen
# Sorten dieses Sets (Pikachu-Reihe, Futuristic Rare, Classic Collection) haben dort
# noch keinen festen Namen; sobald der Sync läuft, korrigiert er sie selbst.
SELTENHEIT = {
    "common": "Common",
    "uncommon": "Uncommon",
    "rare": "Rare",
    "holographic": "Rare Holo",
    "twostar": "Double rare",
    "lvxrare": "Ultra Rare",
    "silvertwostar": "Illustration rare",
    "superrare": "Special illustration rare",
    "goldonestar": "Secret Rare",
    "shinyrare": "Shiny rare",
    "futurerare": "Futuristic Rare",
    "pikarare": "Pikachu Rare",
    "promo": "Promo",
}
TYPEN = {
    "grass": "Grass", "fire": "Fire", "water": "Water", "electric": "Lightning",
    "lightning": "Lightning", "psychic": "Psychic", "fighting": "Fighting",
    "darkness": "Darkness", "dark": "Darkness", "metal": "Metal", "steel": "Metal",
    "dragon": "Dragon", "colorless": "Colorless", "normal": "Colorless", "fairy": "Fairy",
}


def _holen(url, versuche=3):
    for i in range(versuche):
        try:
            req = urllib.request.Request(url, headers=UA)
            return urllib.request.urlopen(req, timeout=30).read()
        except Exception:
            if i == versuche - 1:
                raise
            time.sleep(2 * (i + 1))


def _text(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def karten_lesen():
    """Setliste parsen. Liefert je Karte: Nummer, Bildname, Name, HP, Typen, Seltenheit."""
    seite = _holen(QUELLE).decode("utf-8", "replace")
    zeilen = [z for z in re.findall(r"<tr[^>]*>.*?</tr>", seite, re.S)
              if "/card/30thcelebration/" in z]
    karten = []
    for z in zeilen:
        zellen = z.split("</td>")
        kopf = zellen[0]
        bild = re.search(r"/card/th/30thcelebration/([^\"]+)\.jpg", z)
        nummer = re.search(r"/card/30thcelebration/([^\"/]+)\.shtml", z)
        if not bild or not nummer:
            continue
        # „1 / 128" bzw. „203/193" steht zwischen <br /> und dem Seltenheits-Symbol
        aufdruck = re.search(r"<br />\s*([0-9A-Za-z/ ]+?)\s*<img", kopf)
        symbol = re.search(r"/card/image/([a-z0-9]+)\.png", kopf)
        klassik = "Classic Collection" in _text(kopf)
        name = _text(zellen[2]) if len(zellen) > 2 else ""
        hp_zelle = zellen[3] if len(zellen) > 3 else ""
        hp = re.search(r"(\d+)HP", hp_zelle)
        typen = [TYPEN[t] for t in re.findall(r"/card/image/([a-z]+)\.png", hp_zelle)
                 if t in TYPEN]
        karten.append({
            "local_id": nummer.group(1),
            "bild": bild.group(1),
            "name": name,
            "aufdruck": (aufdruck.group(1).strip() if aufdruck else ""),
            "hp": int(hp.group(1)) if hp else None,
            "typen": typen[:1],          # das erste Symbol ist der Kartentyp
            "rarity": ("Classic Collection" if klassik
                       else SELTENHEIT.get(symbol.group(1) if symbol else "", None)),
            "klassik": klassik,
        })
    return karten


def _form(name_en, kategorie):
    """Stufe, Suffix und Kartenarten aus dem Kartennamen ableiten.

    TCGdex liefert `stage`/`suffix` mit; Serebii nicht. Ohne die beiden Felder stünde
    jede Karte als schlichtes „pokemon" da und die Filter-Chips (ex, VMAX, VSTAR …)
    fänden im Set nichts. Die Regeln sind dieselben, die `_compute_kinds()` in main.py
    auf diese Felder anwendet — ein späterer Sync rechnet sie ohnehin neu."""
    if kategorie == "Trainer":
        return None, None, ["trainer"]
    n = (name_en or "").strip()
    if n.endswith(" VMAX"):
        return "VMAX", None, ["vmax"]
    if n.endswith(" VSTAR"):
        return "VSTAR", None, ["vstar"]
    if n.endswith("-GX") or n.endswith(" GX"):
        return None, "GX", (["gx", "tagteam"] if "&" in n else ["gx"])
    if n.endswith(" ex"):
        return None, "ex", ["ex"]
    if n.endswith(" EX"):
        return None, "EX", ["exgross"]
    if n.endswith(" V"):
        return None, "V", ["v"]
    if n.startswith("M ") or n.startswith("Mega "):
        return "MEGA", None, ["mega"]
    return None, None, ["pokemon"]


def _local_num(local_id):
    m = re.match(r"^(\d+)", local_id or "")
    if m:
        return int(m.group(1))
    m = re.match(r"^[A-Za-z]+(\d+)$", local_id or "")      # H1 … H30
    return 1000 + int(m.group(1)) if m else None


def schreiben(karten, trocken=False):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    # Deutsche Pokémon-Namen aus dem eigenen Bestand: deckt alle Pokémon-Karten ab,
    # Trainer und Energien bleiben englisch, bis TCGdex nachzieht.
    def schluessel(n):
        n = (n or "").lower().replace("\u2640", "f").replace("\u2642", "m")
        return re.sub(r"[^a-z0-9]", "", n)

    de = {}
    for r in con.execute("SELECT dex_id, name_en, name_de FROM pokemon WHERE name_en IS NOT NULL"):
        de[schluessel(r["name_en"])] = (r["name_en"], r["name_de"], r["dex_id"])

    def treffer_fuer(name_en):
        """Deutscher Name + Pokédex-Nummer zu einem Kartennamen.

        Kartennamen tragen Zusätze („Charizard ex", „Alolan Exeggutor", „Arceus VSTAR").
        Deshalb erst exakt, dann der längste enthaltene Pokémon-Name — längste zuerst,
        damit „Charizard" nicht auf „Charmander" fällt."""
        t = de.get(schluessel(name_en))
        if t:
            return t[1], t[2]
        kern = schluessel(name_en)
        for k in sorted(de, key=len, reverse=True):
            if len(k) >= 3 and k in kern:
                orig, deutsch, dex = de[k]
                return re.sub(re.escape(orig), deutsch, name_en, flags=re.I), dex
        return None, None

    zeilen = []
    for k in karten:
        kategorie = "Pokemon" if k["hp"] else "Trainer"
        deutsch, dex = treffer_fuer(k["name"]) if k["hp"] else (None, None)
        stage, suffix, kinds = _form(k["name"], kategorie)
        zeilen.append((
            f"{SET_ID}-{k['local_id']}", SET_ID, k["local_id"], _local_num(k["local_id"]),
            deutsch, k["name"],
            BILD.format(n=k["bild"]),
            kategorie,
            k["rarity"], json.dumps(k["typen"]), k["hp"],
            # In diesem Set ist jede Karte foliert — es gibt keine normale und keine
            # Reverse-Fassung. Genau so steht schon Celebrations 2021 in der Datenbank.
            0, 0, 1,
            "2026-09-16", json.dumps(kinds), kinds[0],
            json.dumps([dex] if dex else []), dex, stage, suffix,
        ))

    if trocken:
        for z in zeilen[:6] + zeilen[-4:]:
            print(z)
        fehlend = [k["name"] for k in karten if k["hp"] and not treffer_fuer(k["name"])[0]]
        print(f"\n{len(zeilen)} Karten, ohne deutschen Namen: {len(fehlend)}")
        print("  z. B.", ", ".join(fehlend[:10]))
        con.close()
        return

    con.execute(
        "INSERT INTO sets (id,name,serie_id,serie_name,release_date,total,official,"
        " name_en,serie_name_en,region) VALUES (?,?,?,?,?,?,?,?,?,'intl')"
        " ON CONFLICT(id) DO UPDATE SET name=excluded.name, release_date=excluded.release_date,"
        " total=excluded.total, official=excluded.official, name_en=excluded.name_en",
        (SET_ID, "30th Celebration", "me", "Mega-Entwicklung", "2026-09-16",
         199, 128, "30th Celebration", "Mega Evolution"))
    con.executemany(
        "INSERT INTO cards (id,set_id,local_id,local_num,name_de,name_en,image_alt,"
        " category,rarity,types,hp,has_normal,has_reverse,has_holo,release_date,kinds,kind,"
        " dex_ids,first_dex,stage,suffix)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(id) DO UPDATE SET name_de=COALESCE(cards.name_de, excluded.name_de),"
        " name_en=COALESCE(cards.name_en, excluded.name_en), image_alt=excluded.image_alt,"
        " rarity=COALESCE(cards.rarity, excluded.rarity), hp=COALESCE(cards.hp, excluded.hp),"
        " dex_ids=COALESCE(cards.dex_ids, excluded.dex_ids),"
        " first_dex=COALESCE(cards.first_dex, excluded.first_dex),"
        " kinds=excluded.kinds, kind=excluded.kind,"
        " stage=COALESCE(cards.stage, excluded.stage),"
        " suffix=COALESCE(cards.suffix, excluded.suffix)",
        zeilen)
    con.commit()
    print(f"{len(zeilen)} Karten in {SET_ID} geschrieben.")
    con.close()


def bilder_vorladen(karten):
    """Scans einmal holen und unter allen vier Cache-Namen ablegen.

    `card_image()` legt je Karte vier Dateien an (low/high x de/en) und holt eine
    vorhandene Datei nie erneut. Wir laden also einmal und kopieren — sonst zöge
    jede Kachelansicht erneut an Serebii."""
    geholt = fehler = 0
    for k in karten:
        cid = f"{SET_ID}-{k['local_id']}"
        ziele = [CACHE / "cards" / v / f"{cid}{s}.webp"
                 for v in ("low", "high") for s in ("", ".en")]
        if all(z.exists() for z in ziele):
            continue
        try:
            daten = _holen(BILD.format(n=k["bild"]))
        except Exception as exc:
            print(f"  {cid}: {exc}")
            fehler += 1
            continue
        ziele[0].parent.mkdir(parents=True, exist_ok=True)
        ziele[0].write_bytes(daten)
        for z in ziele[1:]:
            z.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ziele[0], z)
        geholt += 1
        time.sleep(0.2)
    print(f"Bilder geladen: {geholt}, Fehler: {fehler}")


def cache_leeren():
    weg = 0
    for v in ("low", "high", "print"):
        ordner = CACHE / "cards" / v
        if not ordner.exists():
            continue
        for f in ordner.glob(f"{SET_ID}-*"):
            f.unlink()
            weg += 1
    print(f"{weg} Cache-Dateien entfernt.")


def entfernen():
    con = sqlite3.connect(DB)
    n = con.execute("DELETE FROM cards WHERE set_id = ?", (SET_ID,)).rowcount
    con.execute("DELETE FROM sets WHERE id = ?", (SET_ID,))
    con.commit()
    con.close()
    cache_leeren()
    print(f"{n} Karten und das Set {SET_ID} entfernt.")


def quellen_pruefen():
    """Haben TCGdex oder pokemontcg.io das Set inzwischen? Dann ist Handarbeit vorbei.

    Danach: --entfernen, anschließend den regulären Sync laufen lassen
    (POST /api/admin/sync?key=…). Erst löschen, dann syncen — sonst stünden die
    Karten unter zwei Nummernkreisen doppelt im Katalog."""
    treffer = False
    for name, url, feld in (
            ("TCGdex (en)", "https://api.tcgdex.net/v2/en/sets", "name"),
            ("TCGdex (de)", "https://api.tcgdex.net/v2/de/sets", "name"),
            ("pokemontcg.io", "https://api.pokemontcg.io/v2/sets?pageSize=250", "name")):
        try:
            daten = json.loads(_holen(url))
            liste = daten.get("data", daten) if isinstance(daten, dict) else daten
            gefunden = [s for s in liste
                        if "30th" in (s.get(feld) or "") or "Celebration" in (s.get(feld) or "")]
            gefunden = [s for s in gefunden if "2021" not in str(s) and s.get("id") not in ("cel25", "cel25c", "cel25cc")]
            print(f"{name}: {[ (s.get('id'), s.get(feld)) for s in gefunden ] or 'noch nicht da'}")
            treffer = treffer or bool(gefunden)
        except Exception as exc:
            print(f"{name}: nicht erreichbar ({exc})")
    if treffer:
        print("\n→ Vorab-Satz kann weg: scripts/vorab_30th.py --entfernen, dann Sync starten.")
    return treffer


# --- Ablösung durch den echten Katalog -------------------------------------

VERWEISE = (("sammlung", "card_id"), ("wants", "card_id"), ("card_prices", "card_id"),
            ("price_history", "card_id"), ("card_hashes", "card_id"),
            ("card_art_tags", "card_id"), ("card_art_analysis", "card_id"),
            ("card_art_aufdrucke", "card_id"))


def verweise_zaehlen(con, praefix=SET_ID + "-"):
    """Wo überall stecken unsere Vorab-IDs? Das ist der eigentliche Grund für die
    Zuordnung: Sammlung, Wunschliste und Binderfächer zeigen auf Karten-IDs, und die
    überleben ein blindes Löschen nicht."""
    zahlen = {}
    for tabelle, feld in VERWEISE:
        try:
            zahlen[tabelle] = con.execute(
                f"SELECT COUNT(*) FROM {tabelle} WHERE {feld} LIKE ?", (praefix + "%",)).fetchone()[0]
        except sqlite3.OperationalError:
            pass
    zahlen["binders"] = con.execute(
        "SELECT COUNT(*) FROM binders WHERE items LIKE ?", ("%" + praefix + "%",)).fetchone()[0]
    zahlen["preis_alarme"] = con.execute(
        "SELECT COUNT(*) FROM preis_alarme WHERE ziel LIKE ?", (praefix + "%",)).fetchone()[0]
    return {k: v for k, v in zahlen.items() if v}


def _nummer(local_id):
    """„001" und „1" sind dieselbe Karte — führende Nullen weg, Rest in Großbuchstaben."""
    t = (local_id or "").strip().upper()
    m = re.match(r"^0*(\d+)$", t)
    return m.group(1) if m else t


def echtes_set_finden(con):
    """Welches Set ist nach dem Sync das echte? Alles außer unserem, das denselben
    Erscheinungstag oder „30th"/„Celebration" im Namen trägt (Celebrations 2021 raus)."""
    treffer = []
    for r in con.execute(
        "SELECT id, name, name_en, release_date, total FROM sets WHERE id <> ?", (SET_ID,)
    ):
        name = f"{r[1] or ''} {r[2] or ''}"
        if r[0].startswith("cel25"):
            continue
        if "30th" in name or (("Celebration" in name) and (r[3] or "").startswith("2026")):
            treffer.append(dict(zip(("id", "name", "name_en", "release_date", "total"), r)))
        elif (r[3] or "") == "2026-09-16":
            treffer.append(dict(zip(("id", "name", "name_en", "release_date", "total"), r)))
    return treffer


FELDER = ("id", "local_id", "name_en", "hp")


def vorab_karten(con):
    """Unsere Handarbeit: Karten in cel30, die noch kein TCGdex-Bild tragen."""
    return [dict(zip(FELDER, r)) for r in con.execute(
        "SELECT id, local_id, name_en, hp FROM cards WHERE set_id = ?"
        " AND image_de IS NULL AND image_en IS NULL", (SET_ID,))]


def echte_karten(con, set_ids):
    """Alles, was aus dem Katalog kam: übernommene cel30-Zeilen plus fremde Sets."""
    frage = ",".join("?" * len(set_ids)) if set_ids else "''"
    return [dict(zip(FELDER, r)) for r in con.execute(
        "SELECT id, local_id, name_en, hp FROM cards"
        f" WHERE (set_id = ? AND (image_de IS NOT NULL OR image_en IS NOT NULL))"
        f"    OR set_id IN ({frage})", [SET_ID] + list(set_ids))]


def zuordnung_bauen(alt, neu):
    """alt -> neu. Erst über die aufgedruckte Nummer (deckt 001–158 ab), dann über
    Name + KP (für die Klassische Kollektion, die bei uns H1–H30 heißt und im echten
    Katalog mit Sicherheit anders nummeriert ist)."""

    nach_nummer = {}
    nach_name = {}
    for n in neu:
        nach_nummer.setdefault(_nummer(n["local_id"]), []).append(n)
        nach_name.setdefault(((n["name_en"] or "").lower(), n["hp"]), []).append(n)

    karte, mehrdeutig = {}, {}
    for a in alt:
        kandidaten = nach_nummer.get(_nummer(a["local_id"]), [])
        if len(kandidaten) != 1:
            kandidaten = nach_name.get(((a["name_en"] or "").lower(), a["hp"]), [])
        if len(kandidaten) == 1:
            if kandidaten[0]["id"] != a["id"]:
                karte[a["id"]] = kandidaten[0]["id"]
        else:
            mehrdeutig.setdefault(((a["name_en"] or "").lower(), a["hp"]), []).append(a)

    # Gleicher Name, gleiche KP, mehrfach im Set (im Vorab-Satz z. B. „Darkrai & Cresselia"
    # zweimal): wenn drüben genauso viele stehen, paaren wir sie in Nummernreihenfolge.
    # Sonst bleiben sie offen — eine falsche Zuordnung wäre schlimmer als eine Karteileiche.
    offen = []
    for schluessel, gruppe in mehrdeutig.items():
        gegenueber = [n for n in nach_name.get(schluessel, []) if n["id"] not in karte.values()]
        if len(gegenueber) == len(gruppe):
            for a, n in zip(sorted(gruppe, key=lambda x: _nummer(x["local_id"])),
                            sorted(gegenueber, key=lambda x: _nummer(x["local_id"]))):
                if n["id"] != a["id"]:
                    karte[a["id"]] = n["id"]
        else:
            offen.extend(gruppe)
    return karte, offen


def verweise_umschreiben(con, karte, wirklich):
    """Sammlung, Wunschliste, Binderfächer, Alarme auf die neuen IDs umbiegen."""
    geaendert = {}
    for tabelle, feld in VERWEISE:
        n = 0
        for alt, neu in karte.items():
            try:
                r = con.execute(f"UPDATE OR REPLACE {tabelle} SET {feld} = ? WHERE {feld} = ?",
                                (neu, alt)) if wirklich else None
                n += r.rowcount if r else con.execute(
                    f"SELECT COUNT(*) FROM {tabelle} WHERE {feld} = ?", (alt,)).fetchone()[0]
            except sqlite3.OperationalError:
                break
        if n:
            geaendert[tabelle] = n
    # Binderfächer liegen als JSON-Liste [{"type":"card","id":"…"}] in einer Spalte
    n = 0
    for bid, items in con.execute(
            "SELECT id, items FROM binders WHERE items LIKE ?", ("%" + SET_ID + "-%",)):
        try:
            liste = json.loads(items or "[]")
        except ValueError:
            continue
        treffer = 0
        for fach in liste:
            if isinstance(fach, dict) and fach.get("id") in karte:
                fach["id"] = karte[fach["id"]]
                treffer += 1
        if treffer and wirklich:
            con.execute("UPDATE binders SET items = ? WHERE id = ?",
                        (json.dumps(liste, ensure_ascii=False), bid))
        n += treffer
    if n:
        geaendert["binders (Fächer)"] = n
    m = 0
    for alt, neu in karte.items():
        r = con.execute("UPDATE preis_alarme SET ziel = ? WHERE ziel = ?", (neu, alt)) \
            if wirklich else None
        m += r.rowcount if r else con.execute(
            "SELECT COUNT(*) FROM preis_alarme WHERE ziel = ?", (alt,)).fetchone()[0]
    if m:
        geaendert["preis_alarme"] = m
    return geaendert


def sync_anstossen():
    """Regulären Katalog-Sync starten und warten, bis er durch ist."""
    env = (BASE / ".env").read_text(encoding="utf-8") if (BASE / ".env").exists() else ""
    m = re.search(r"^ADMIN_KEY=(.+)$", env, re.M)
    if not m:
        print("Kein ADMIN_KEY in der .env — Sync bitte von Hand starten.")
        return False
    req = urllib.request.Request(f"{API}/api/admin/sync?key={m.group(1).strip()}",
                                 headers=UA, method="POST")
    urllib.request.urlopen(req, timeout=30).read()
    print("Sync gestartet …", end="", flush=True)
    for _ in range(180):
        time.sleep(5)
        try:
            stand = json.loads(_holen(f"{API}/api/meta")).get("sync") or {}
        except Exception:
            continue
        if not stand.get("running"):
            print(f" fertig ({stand.get('step')})")
            return not stand.get("error")
        print(".", end="", flush=True)
    print(" Zeitüberschreitung.")
    return False


def ersetzen(wirklich=False):
    """Vorab-Satz durch den echten Katalog ablösen.

    Zwei Fälle, und beide sind hier abgedeckt:

    a) TCGdex vergibt dieselbe Set-ID `cel30`. Dann hat der Sync unsere Zeilen schon in
       Ruhe überschrieben (ON CONFLICT), sie tragen jetzt `image_de`/`image_en`, und es
       bleibt nur der Bild-Cache: `card_image()` liefert eine einmal gespeicherte Datei
       für immer weiter, die echten Scans kämen sonst nie an.
    b) TCGdex vergibt eine andere ID (oder trennt die Klassische Kollektion ab, wie 2021
       bei `cel25cc`). Dann stehen die echten Karten daneben, und unsere Vorab-IDs müssen
       zugeordnet werden, bevor sie verschwinden — Sammlung, Wunschliste, Binderfächer
       und Preisalarme zeigen darauf.

    Reihenfolge: Sync → Zuordnung → Verweise umbiegen → Reste löschen → Cache räumen.
    Ohne `--wirklich` wird nichts geschrieben."""
    con = sqlite3.connect(DB)
    print("Verweise auf den Vorab-Satz:", verweise_zaehlen(con) or "keine")

    if not echtes_set_finden(con) and not echte_karten(con, []):
        if not quellen_pruefen():
            print("\nDas echte Set ist noch nirgends da — nichts zu tun.")
            con.close()
            return
        con.close()
        if not wirklich:
            print("\nDie Quelle hat es. Jetzt mit --ersetzen --wirklich ausführen.")
            return
        if not sync_anstossen():
            print("Sync nicht sauber durchgelaufen — Abbruch, es wurde nichts gelöscht.")
            return
        con = sqlite3.connect(DB)

    fremde = echtes_set_finden(con)
    alt = vorab_karten(con)
    neu = echte_karten(con, [s["id"] for s in fremde])
    uebernommen = len(neu) - sum(1 for n in neu if not n["id"].startswith(SET_ID + "-"))
    print(f"Aus dem Katalog: {len(neu)} Karten "
          f"({uebernommen} davon unter unserer Set-ID übernommen"
          + (", fremde Sets: " + ", ".join(s["id"] for s in fremde) if fremde else "") + ")")
    print(f"Noch handgemacht: {len(alt)} Karten")

    if not neu:
        print("Nach dem Sync ist kein echtes 30th-Set in der Datenbank — Abbruch.")
        con.close()
        return

    if not alt:
        print("Alles übernommen — es bleibt nur der Bild-Cache.")
        if wirklich:
            con.execute("UPDATE cards SET image_alt = NULL WHERE set_id = ?", (SET_ID,))
            con.commit()
            con.close()
            cache_leeren()
        else:
            con.close()
            print("\nProbelauf — nichts geschrieben. Mit --ersetzen --wirklich ausführen.")
        return

    karte, offen = zuordnung_bauen(alt, neu)
    print(f"Zuordnung: {len(karte)} Vorab-Karten bekommen eine neue ID, "
          f"{len(alt) - len(karte) - len(offen)} behalten ihre, {len(offen)} ohne Gegenstück.")
    if offen:
        print("  ohne Gegenstück:", ", ".join(f"{o['local_id']} {o['name_en']}" for o in offen[:12]))

    print("Umgeschriebene Verweise:", verweise_umschreiben(con, karte, wirklich) or "keine")

    # Nur löschen, was wirklich ersetzt ist. Karten ohne Gegenstück bleiben stehen —
    # lieber eine Karteileiche als ein leeres Binderfach beim Kunden.
    offene_ids = {o["id"] for o in offen}
    if wirklich:
        weg = 0
        for a in alt:
            if a["id"] not in offene_ids:
                con.execute("DELETE FROM cards WHERE id = ?", (a["id"],))
                weg += 1
        con.execute("UPDATE cards SET image_alt = NULL WHERE set_id = ?", (SET_ID,))
        rest = con.execute("SELECT COUNT(*) FROM cards WHERE set_id = ?", (SET_ID,)).fetchone()[0]
        if not rest:
            con.execute("DELETE FROM sets WHERE id = ?", (SET_ID,))
        con.commit()
        con.close()
        print(f"{weg} Vorab-Karten gelöscht" +
              (f", {len(offene_ids)} ohne Gegenstück behalten (Set {SET_ID} bleibt stehen)"
               if offene_ids else ""))
        cache_leeren()
    else:
        con.close()
        print("\nProbelauf — nichts geschrieben. Mit --ersetzen --wirklich ausführen.")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--verweise":
        con = sqlite3.connect(DB)
        print(verweise_zaehlen(con) or "keine Verweise auf den Vorab-Satz")
        con.close()
    elif arg == "--ersetzen":
        ersetzen(wirklich="--wirklich" in sys.argv)
    elif arg == "--pruefen":
        quellen_pruefen()
    elif arg == "--entfernen":
        entfernen()
    elif arg == "--cache-leeren":
        cache_leeren()
    elif arg == "--bilder":
        bilder_vorladen(karten_lesen())
    else:
        karten = karten_lesen()
        schreiben(karten, trocken=(arg == "--trocken"))
