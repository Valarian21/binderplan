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
# Für die Geheimkarten (ab 129) taugen die Serebii-Symbole nicht: 129–146 tragen dort
# „goldonestar" (= Secret Rare), sind aber die Illustration Rares des Sets; 147–156
# erscheinen gemischt als „silvertwostar", „twostar" und „promo", obwohl es durchweg die
# Special Illustration Rares sind. Einzelne Karten haben gar kein Symbol und landeten als
# „Common" (144 Meowth). Die Blockgrenzen sind sicher, weil die japanische Fassung (M6a)
# genau so aufgebaut ist — AR 20, SAR 10, FUR 2 — und das Pikachu-ex-Paar „Tag/Nacht"
# dort auf SAR 126/127 liegt, hier auf 149/150 (Versatz 23 über den ganzen Block).
# Belegt über pokecottage.com und cardrake.com (englische Liste) sowie die offizielle
# japanische Setliste vom 09.09.2026 (PokéBeach).
GEHEIM_SELTENHEIT = (
    (129, 146, "Illustration rare"),
    (147, 156, "Special illustration rare"),
    (157, 158, "Futuristic Rare"),
)


def _seltenheit(local_id, aus_symbol, klassik):
    """Seltenheit einer Karte — im Geheimbereich nach Nummernblock statt nach Symbol."""
    if klassik:
        return "Classic Collection"
    if local_id.isdigit():
        n = int(local_id)
        for von, bis, name in GEHEIM_SELTENHEIT:
            if von <= n <= bis:
                return name
    return aus_symbol
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
            "rarity": _seltenheit(nummer.group(1),
                                  SELTENHEIT.get(symbol.group(1) if symbol else "", None),
                                  klassik),
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
        # Die Seltenheit wird überschrieben, nicht bewahrt: bis 11.09. standen die
        # Geheimkarten mit den falschen Serebii-Symbolen drin, ein zweiter Lauf muss das
        # heilen können. Alles andere an diesem Set schreibt ohnehin nur dieses Skript.
        " rarity=excluded.rarity, hp=COALESCE(cards.hp, excluded.hp),"
        " dex_ids=COALESCE(cards.dex_ids, excluded.dex_ids),"
        " first_dex=COALESCE(cards.first_dex, excluded.first_dex),"
        " kinds=excluded.kinds, kind=excluded.kind,"
        " stage=COALESCE(cards.stage, excluded.stage),"
        " suffix=COALESCE(cards.suffix, excluded.suffix)",
        zeilen)
    con.commit()
    print(f"{len(zeilen)} Karten in {SET_ID} geschrieben.")
    print(angekuendigte_ergaenzen(con))
    con.close()


# Karten, die auf der offiziellen Liste stehen, von denen aber noch niemand ein Bild
# gezeigt hat (Stand 11.09.2026). Die Namen sind über zwei unabhängige Wege belegt:
# tcgscreener nennt sie ausdrücklich, und die japanische Liste bestätigt sie über die
# Blockzuordnung (JP AR 119 → EN 143, JP SAR 128/129/131 → EN 151/152/154; der Versatz ist
# an sieben Karten des SAR-Blocks nachgerechnet — Fuecoco ex JP 124 → EN 147, Greninja ex
# 125→148, Pikachu ex Tag/Nacht 126/127→149/150, Sylveon ex 130→153, Jirachi ex 132→155,
# Salamence ex 133→156). Sie kommen ohne Scan in den Katalog, damit die Fächer schon stehen;
# `_ersetzen` ordnet sie am 16.09. über die aufgedruckte Nummer den echten Karten zu.
# HP, Typ und Pokédex-Nummer werden von der Hauptset-Fassung derselben Karte übernommen —
# eine Illustration Rare ist dieselbe Karte mit anderem Bild.
ANGEKUENDIGT = {
    "143": "Kommo-o",
    "151": "Mewtwo ex",
    "152": "Mew ex",
    "154": "Gengar ex",
}


def angekuendigte_ergaenzen(con):
    """Die bekannten, aber noch nicht gezeigten Geheimkarten als bildlose Einträge anlegen.

    Sobald eine davon einen Scan hat, ist sie richtig enthüllt und wird nicht mehr angefasst:
    der angenommene Name aus `ANGEKUENDIGT` darf den echten nicht überschreiben."""
    neu = 0
    for local_id, name_en in ANGEKUENDIGT.items():
        da = con.execute("SELECT image_alt FROM cards WHERE id = ?",
                         (f"{SET_ID}-{local_id}",)).fetchone()
        if da and da["image_alt"]:
            continue
        vorlage = con.execute(
            "SELECT name_de, name_en, category, rarity, types, hp, dex_ids, first_dex, stage, suffix,"
            " kinds, kind FROM cards WHERE set_id = ? AND name_en = ? AND local_id <> ?"
            " ORDER BY local_num LIMIT 1", (SET_ID, name_en, local_id)).fetchone()
        if not vorlage:
            print(f"  {local_id} {name_en}: keine Hauptset-Fassung gefunden, übersprungen")
            continue
        rarity = _seltenheit(local_id, vorlage["rarity"], False)
        con.execute(
            "INSERT INTO cards (id,set_id,local_id,local_num,name_de,name_en,image_alt,"
            " category,rarity,types,hp,has_normal,has_reverse,has_holo,release_date,kinds,kind,"
            " dex_ids,first_dex,stage,suffix)"
            " VALUES (?,?,?,?,?,?,NULL,?,?,?,?,0,0,1,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET name_de=excluded.name_de, name_en=excluded.name_en,"
            " rarity=excluded.rarity, hp=COALESCE(cards.hp, excluded.hp)",
            (f"{SET_ID}-{local_id}", SET_ID, local_id, _local_num(local_id),
             vorlage["name_de"], name_en, vorlage["category"], rarity, vorlage["types"],
             vorlage["hp"], "2026-09-16", vorlage["kinds"], vorlage["kind"],
             vorlage["dex_ids"], vorlage["first_dex"], vorlage["stage"], vorlage["suffix"]))
        neu += 1
    con.commit()
    return (f"{neu} angekündigte Karten ohne Scan ergänzt ({', '.join(ANGEKUENDIGT)})."
            if neu else "Keine angekündigten Karten zu ergänzen.")


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


QUELLEN = (("TCGdex (en)", "https://api.tcgdex.net/v2/en/sets"),
           ("TCGdex (de)", "https://api.tcgdex.net/v2/de/sets"),
           ("pokemontcg.io", "https://api.pokemontcg.io/v2/sets?pageSize=250"))


def quelle_befragen():
    """Führen TCGdex oder pokemontcg.io das 30th-Set inzwischen? Still, ohne Ausgabe.

    Rückgabe: {Quellenname: [(Set-ID, Name), …] oder None bei Fehler}. `None` ist wichtig
    und nicht dasselbe wie eine leere Liste — pokemontcg.io fällt zu etwa zwei Dritteln
    aus, und „nicht erreichbar" darf nie als „das Set gibt es nicht" durchgehen."""
    ergebnis = {}
    for name, url in QUELLEN:
        try:
            daten = json.loads(_holen(url))
            liste = daten.get("data", daten) if isinstance(daten, dict) else daten
            gefunden = [(s.get("id"), s.get("name")) for s in liste
                        if ("30th" in (s.get("name") or "") or "Celebration" in (s.get("name") or ""))
                        and "2021" not in str(s)
                        and (s.get("id") or "") not in ("cel25", "cel25c", "cel25cc")
                        and (s.get("id") or "") != SET_ID]
            ergebnis[name] = gefunden
        except Exception as exc:
            print(f"  {name}: nicht erreichbar ({exc})") if os.environ.get("BP_LAUT") else None
            ergebnis[name] = None
    return ergebnis


def quellen_pruefen():
    """Haben TCGdex oder pokemontcg.io das Set inzwischen? Dann ist die Handarbeit vorbei.

    Danach **nicht** `--entfernen` — das ist der grobe Weg und verliert die Verweise der
    Kunden (Binderfächer, Kunstseiten, Sammlung). Der richtige Weg ist `--ersetzen`,
    der den Sync selbst anstößt und die Vorab-IDs auf die echten umbiegt."""
    treffer = False
    for name, gefunden in quelle_befragen().items():
        if gefunden is None:
            print(f"{name}: nicht erreichbar")
            continue
        print(f"{name}: {gefunden or 'noch nicht da'}")
        treffer = treffer or bool(gefunden)
    if treffer:
        print("\n→ Jetzt ablösen: scripts/vorab_30th.py --ersetzen  (dann --ersetzen --wirklich).")
    return treffer


# --- Ablösung durch den echten Katalog -------------------------------------

# Kundendaten: was der Nutzer selbst angelegt hat. Die werden immer umgebogen, notfalls
# über eine bestehende Zeile hinweg — bei einem Widerspruch gewinnt der Kunde.
VERWEISE_KUNDE = (("sammlung", "card_id"), ("wants", "card_id"))
# Abgeleitetes gehört dem Katalog (Preise, Bildmotiv-Index, Farbmessung). Steht beim echten
# Nachfolger schon eine Zeile, ist die frischer als unsere aus den Serebii-Scans gewonnene —
# dann gewinnt sie, und die Vorab-Zeile fällt weg (UPDATE OR IGNORE, danach DELETE).
VERWEISE_ABGELEITET = (("card_prices", "card_id"), ("price_history", "card_id"),
                       ("card_hashes", "card_id"), ("card_palette", "card_id"),
                       ("card_art_tags", "card_id"), ("card_art_fts", "card_id"),
                       ("card_art_analysis", "card_id"), ("card_art_aufdrucke", "card_id"))
VERWEISE = VERWEISE_KUNDE + VERWEISE_ABGELEITET
# Karten-IDs, die in einer JSON-Spalte stecken statt in einer eigenen Zeile. Beide tragen
# Kundenarbeit: `binders.items` die Fächer des Binders, `artworks.anker` die Karten, aus
# denen eine Kunstseite gemalt wurde. Ohne diese beiden Stellen zeigten Fächer und
# Kunstseiten nach der Ablösung auf gelöschte Karten.
JSON_VERWEISE = (("binders", "items", "Fächer"), ("artworks", "anker", "Kunstseiten"))


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
    for tabelle, feld, _ in JSON_VERWEISE:
        zahlen[tabelle] = con.execute(
            f"SELECT COUNT(*) FROM {tabelle} WHERE {feld} LIKE ?",
            ("%" + praefix + "%",)).fetchone()[0]
    zahlen["preis_alarme"] = con.execute(
        "SELECT COUNT(*) FROM preis_alarme WHERE ziel LIKE ?", (praefix + "%",)).fetchone()[0]
    zahlen["profile (Avatar)"] = con.execute(
        "SELECT COUNT(*) FROM profile WHERE avatar_card LIKE ?", (praefix + "%",)).fetchone()[0]
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
    Katalog mit Sicherheit anders nummeriert ist).

    Eiserne Regel: **jede echte Karte wird höchstens einmal vergeben.** Bewerben sich
    mehrere Vorab-Karten um dieselbe, bleiben alle offen und damit als Karteileiche
    stehen. Das ist nicht theoretisch — die drei „RGB Mew", die der Wächter von Serebii
    aufgelesen hat, heißen alle „Mew" mit 60 KP und trugen sonst alle drei die Nummer der
    einen echten Mew-Karte davon. Drei Binderfächer hätten danach auf dieselbe Karte
    gezeigt, und in der Sammlung hätte `UPDATE OR REPLACE` zwei Posten gelöscht.
    Eine Karteileiche ist immer besser als eine falsche Zuordnung."""

    nach_nummer = {}
    nach_name = {}
    for n in neu:
        nach_nummer.setdefault(_nummer(n["local_id"]), []).append(n)
        nach_name.setdefault(((n["name_en"] or "").lower(), n["hp"]), []).append(n)

    karte = {}
    vergeben = set()          # echte IDs, die schon einen Vorbesitzer haben
    rest = []

    # Runde 1: die aufgedruckte Nummer. Eindeutig und exklusiv — wer hier trifft, hat den
    # Platz sicher, auch wenn später jemand über den Namen danach greift.
    for a in alt:
        kandidaten = nach_nummer.get(_nummer(a["local_id"]), [])
        if len(kandidaten) == 1 and kandidaten[0]["id"] not in vergeben:
            if kandidaten[0]["id"] != a["id"]:
                karte[a["id"]] = kandidaten[0]["id"]
            vergeben.add(kandidaten[0]["id"])
        else:
            rest.append(a)

    # Runde 2: Name + KP, aber nur auf noch freie Plätze. Bewerben sich mehrere um
    # denselben freien Platz, bekommt ihn keiner.
    bewerber = {}
    for a in rest:
        frei = [n for n in nach_name.get(((a["name_en"] or "").lower(), a["hp"]), [])
                if n["id"] not in vergeben]
        bewerber.setdefault(tuple(sorted(n["id"] for n in frei)), []).append((a, frei))

    offen = []
    for _, gruppe in bewerber.items():
        frei = gruppe[0][1]
        gleiche = [a for a, _ in gruppe]
        if len(frei) == len(gleiche):
            # Gleich viele auf beiden Seiten: paarweise in Nummernreihenfolge. Deckt den
            # Fall „dieselbe Karte zweimal im Set" ab (z. B. „Darkrai & Cresselia").
            for a, n in zip(sorted(gleiche, key=lambda x: _nummer(x["local_id"])),
                            sorted(frei, key=lambda x: _nummer(x["local_id"]))):
                if n["id"] != a["id"]:
                    karte[a["id"]] = n["id"]
                vergeben.add(n["id"])
        else:
            offen.extend(gleiche)
    return karte, offen


def verweise_umschreiben(con, karte, wirklich):
    """Alles, was auf eine Vorab-ID zeigt, auf die neue ID umbiegen.

    Drei Sorten, die unterschiedlich behandelt werden müssen:

    * **Kundendaten** (Sammlung, Wunschliste) — `UPDATE OR REPLACE`: gäbe es drüben schon
      eine Zeile, gewinnt der Kunde. Praktisch kann das nicht auftreten, solange das echte
      Set neu ist, aber ein Posten des Kunden darf nie stillschweigend verschwinden.
    * **Abgeleitetes** (Preise, Bildmotiv-Index, Farben) — `UPDATE OR IGNORE`, dann die
      Vorab-Zeile löschen: hat der Katalog schon eigene Werte, sind die besser als unsere.
    * **JSON-Spalten** (`binders.items`, `artworks.anker`) — Zeile für Zeile aufmachen und
      jede Karten-ID darin ersetzen. Das sind die beiden Stellen, an denen Kundenarbeit
      steckt, die kein `UPDATE` erreicht.
    """
    geaendert = {}
    for tabelle, feld in VERWEISE_KUNDE:
        n = 0
        for alt, neu in karte.items():
            if wirklich:
                n += con.execute(f"UPDATE OR REPLACE {tabelle} SET {feld} = ? WHERE {feld} = ?",
                                 (neu, alt)).rowcount
            else:
                n += con.execute(f"SELECT COUNT(*) FROM {tabelle} WHERE {feld} = ?",
                                 (alt,)).fetchone()[0]
        if n:
            geaendert[tabelle] = n
    for tabelle, feld in VERWEISE_ABGELEITET:
        n = 0
        try:
            for alt, neu in karte.items():
                if wirklich:
                    n += con.execute(f"UPDATE OR IGNORE {tabelle} SET {feld} = ? WHERE {feld} = ?",
                                     (neu, alt)).rowcount
                    con.execute(f"DELETE FROM {tabelle} WHERE {feld} = ?", (alt,))
                else:
                    n += con.execute(f"SELECT COUNT(*) FROM {tabelle} WHERE {feld} = ?",
                                     (alt,)).fetchone()[0]
        except sqlite3.OperationalError:
            continue
        if n:
            geaendert[tabelle] = n

    # Binderfächer liegen als JSON-Liste [{"type":"card","id":"…"}] in einer Spalte,
    # Kunstseiten-Anker als JSON-Objekt {"<Fach>": "<Karten-ID>"}. Beide Formen hier.
    for tabelle, feld, wort in JSON_VERWEISE:
        n = 0
        for zid, roh in con.execute(
                f"SELECT id, {feld} FROM {tabelle} WHERE {feld} LIKE ?",
                ("%" + SET_ID + "-%",)).fetchall():
            try:
                daten = json.loads(roh or "null")
            except ValueError:
                continue
            treffer = 0
            if isinstance(daten, list):          # binders.items
                for fach in daten:
                    if isinstance(fach, dict) and fach.get("id") in karte:
                        fach["id"] = karte[fach["id"]]
                        treffer += 1
            elif isinstance(daten, dict):        # artworks.anker
                for fach, cid in list(daten.items()):
                    if cid in karte:
                        daten[fach] = karte[cid]
                        treffer += 1
            if treffer and wirklich:
                con.execute(f"UPDATE {tabelle} SET {feld} = ? WHERE id = ?",
                            (json.dumps(daten, ensure_ascii=False), zid))
            n += treffer
        if n:
            geaendert[f"{tabelle} ({wort})"] = n

    m = 0
    for alt, neu in karte.items():
        if wirklich:
            m += con.execute("UPDATE preis_alarme SET ziel = ? WHERE ziel = ?",
                             (neu, alt)).rowcount
        else:
            m += con.execute("SELECT COUNT(*) FROM preis_alarme WHERE ziel = ?",
                             (alt,)).fetchone()[0]
    if m:
        geaendert["preis_alarme"] = m

    # Wer eine cel30-Karte als Profilbild gewählt hat, behält sie.
    a = 0
    for alt, neu in karte.items():
        if wirklich:
            a += con.execute("UPDATE profile SET avatar_card = ? WHERE avatar_card = ?",
                             (neu, alt)).rowcount
        else:
            a += con.execute("SELECT COUNT(*) FROM profile WHERE avatar_card = ?",
                             (alt,)).fetchone()[0]
    if a:
        geaendert["profile (Avatar)"] = a
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
