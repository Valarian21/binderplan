# Binderplan – Pokémon-Binder-Planer (isolierte App, Port 8103)
#
# Datenquellen:
#   - TCGdex (api.tcgdex.net): deutsche Kartendaten + Kartenbilder (Fallback: englisch)
#   - PokéAPI (pokeapi.co): deutsche Pokémon-Namen für den Pokédex-Modus
#   - Sprites: PokeAPI-GitHub (official artwork), lokal gecacht
#
# Der komplette Katalog wird einmalig in die lokale app.db synchronisiert
# (POST /api/admin/sync bzw. automatisch beim ersten Start). Bilder werden
# erst bei Bedarf geladen und auf Platte gecacht.

import io
import json
import re
import secrets
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse

BASE = Path(__file__).parent
DB = BASE / "app.db"
CACHE = BASE / "cache"
(CACHE / "cards" / "low").mkdir(parents=True, exist_ok=True)
(CACHE / "cards" / "high").mkdir(parents=True, exist_ok=True)
(CACHE / "dex").mkdir(parents=True, exist_ok=True)
(CACHE / "cards" / "print").mkdir(parents=True, exist_ok=True)

TCGDEX = "https://api.tcgdex.net/v2"
UA = {"User-Agent": "Binderplan/1.0 (privates Sammler-Tool)"}

# Deutsche Übersetzungen der TCGdex-Energietypen (DB speichert englisch)
TYPES_DE = {
    "Grass": "Pflanze", "Fire": "Feuer", "Water": "Wasser", "Lightning": "Elektro",
    "Psychic": "Psycho", "Fighting": "Kampf", "Darkness": "Finsternis",
    "Metal": "Metall", "Fairy": "Fee", "Dragon": "Drache", "Colorless": "Farblos",
}

# Generationsgrenzen nach Pokédex-Nummer
GEN_RANGES = [
    (1, 1, 151), (2, 152, 251), (3, 252, 386), (4, 387, 493), (5, 494, 649),
    (6, 650, 721), (7, 722, 809), (8, 810, 905), (9, 906, 1025),
]

# TCGdex übersetzt manche alten Set-/Serien-Namen wörtlich („Grundset“) — hier
# stehen die im deutschen Sammlerraum tatsächlich üblichen Namen. Bei weiteren
# falschen Namen einfach Zeilen ergänzen (Set-ID → Name).
SET_NAME_FIX_DE = {
    "base1": "Base Set",
    "base4": "Base Set 2",
}
SERIE_NAME_FIX_DE = {
    "base": "Base",
}

# --- Ären -------------------------------------------------------------------
# TCGdex-Serien entsprechen nicht den echten TCG-Ären (Platin ist dort eigene
# Serie, e-Card/Gym/Legendary Collection fehlen der Klassik, Ruf der Legenden
# gehört zu HGSS). Hier die im Sammlerraum üblichen Ären; Quer-Serien wie POP,
# Trainer-Kits und McDonald's werden über das Erscheinungsdatum der jeweils
# laufenden Ära zugeschlagen. TCG Pocket (nur digital) bleibt eigener Eintrag.
AEREN = [
    {"id": "klassik", "name": "Klassik (WotC)", "name_en": "Classic (WotC)",
     "von": "1999", "bis": "2003", "start": "0000-00-00"},
    {"id": "ex", "name": "EX (Rubin & Saphir)", "name_en": "EX (Ruby & Sapphire)",
     "von": "2003", "bis": "2007", "start": "2003-06-15"},
    {"id": "dp", "name": "Diamant & Perl", "name_en": "Diamond & Pearl",
     "von": "2007", "bis": "2009", "start": "2007-05-01"},
    {"id": "pl", "name": "Platin", "name_en": "Platinum",
     "von": "2009", "bis": "2010", "start": "2009-02-11"},
    {"id": "hgss", "name": "HeartGold & SoulSilver", "name_en": "HeartGold & SoulSilver",
     "von": "2010", "bis": "2011", "start": "2010-02-10"},
    {"id": "bw", "name": "Schwarz & Weiß", "name_en": "Black & White",
     "von": "2011", "bis": "2013", "start": "2011-03-01"},
    {"id": "xy", "name": "XY", "name_en": "XY",
     "von": "2013", "bis": "2016", "start": "2013-10-12"},
    {"id": "sm", "name": "Sonne & Mond", "name_en": "Sun & Moon",
     "von": "2017", "bis": "2019", "start": "2017-02-03"},
    {"id": "swsh", "name": "Schwert & Schild", "name_en": "Sword & Shield",
     "von": "2020", "bis": "2023", "start": "2019-11-15"},
    {"id": "sv", "name": "Karmesin & Purpur", "name_en": "Scarlet & Violet",
     "von": "2023", "bis": "2025", "start": "2023-03-01"},
    {"id": "me", "name": "Mega-Entwicklung", "name_en": "Mega Evolution",
     "von": "2025", "bis": "", "start": "2025-09-01"},
]
# Japanische Serien → lesbare Ären (TCGdex-ja-Serien-IDs)
JP_AEREN = {
    "PMCG": ("Original (1996–2000)", "Original (1996–2000)"), "neo": ("neo", "neo"), "VS": ("VS", "VS"), "web": ("web", "web"),
    "e": ("Karte e", "Card e"), "ADV": ("ADV (Rubin & Saphir)", "ADV (Ruby & Sapphire)"), "PCG": ("PCG", "PCG"),
    "DP": ("DP", "DP"), "DPt": ("DPt (Platin)", "DPt (Platinum)"), "L": ("LEGEND", "LEGEND"), "BW": ("BW", "BW"),
    "XY": ("XY", "XY"), "XYb": ("XY BREAK", "XY BREAK"), "SM": ("Sonne & Mond", "Sun & Moon"),
    "S": ("Schwert & Schild", "Sword & Shield"), "SV": ("Karmesin & Purpur", "Scarlet & Violet"), "M": ("MEGA", "MEGA"),
}

# TCG Pocket (Handy-App, nur digital) wird bewusst NICHT geführt – es gibt keine physischen Karten.
POCKET_SERIEN = {"tcgp"}
AERA_ORDNUNG = {a["id"]: i for i, a in enumerate(AEREN)}
# Feste Serie→Ära-Zuordnung; alles andere (pop, tk, mc, …) läuft übers Datum.
AERA_SERIEN = {
    "base": "klassik", "gym": "klassik", "neo": "klassik", "lc": "klassik",
    "ecard": "klassik", "misc": "klassik",
    "ex": "ex", "dp": "dp", "pl": "pl", "hgss": "hgss", "col": "hgss",
    "bw": "bw", "xy": "xy", "sm": "sm", "swsh": "swsh", "sv": "sv",
    "me": "me",
}


def _aera_fuer_set(serie_id, release_date):
    fest = AERA_SERIEN.get(serie_id or "")
    if fest:
        return fest
    datum = release_date or "0000-00-00"
    passend = "klassik"
    for a in AEREN:
        if a["start"] is not None and datum >= a["start"]:
            passend = a["id"]
    return passend


def _aera_sql(aera_id):
    """WHERE-Fragment + Parameter für den Ären-Filter der Kartensuche."""
    serien = [s for s, a in AERA_SERIEN.items() if a == aera_id]
    a = next((x for x in AEREN if x["id"] == aera_id), None)
    frag = "serie_id IN (%s)" % ",".join("?" * len(serien))
    params = list(serien)
    if a and a["start"] is not None:
        # Quer-Serien (POP, Trainer-Kits, McDonald's) über das Datum einfangen
        idx = AERA_ORDNUNG[aera_id]
        ende = next((x["start"] for x in AEREN[idx + 1:] if x["start"] is not None), "9999-99-99")
        quer = [s for s in ("pop", "tk", "mc") if s not in serien]
        frag += (" OR (serie_id IN (%s) AND release_date >= ? AND release_date < ?)"
                 % ",".join("?" * len(quer)))
        params += quer + [a["start"], ende]
    return "set_id IN (SELECT id FROM sets WHERE %s)" % frag, params

# Shiny-/Baby-Shiny-Raritäten (Shiny Vault, Schillerndes Schicksal, SV-Ära …)
SHINY_RARITIES = {
    "Shiny rare", "Shiny rare V", "Shiny rare VMAX", "Shiny Ultra Rare",
    "One Shiny", "Two Shiny",
}


def _compute_kinds(category, stage, suffix, rarity, name_en, name_de, local_id=""):
    """Alle zutreffenden Kartenarten (Mehrfach-Label) für die Filter-Chips."""
    kinds = []
    # DP/Platinum-Secret-Shinies (SH1–SH12) — TCGdex führt sie teils mit falscher Rarität
    if str(local_id or "").upper().startswith("SH"):
        kinds.append("shiny")
    stage_u = (stage or "").upper()
    suffix_s = (suffix or "").strip()
    name = name_en or ""
    name_d = name_de or ""
    if category == "Trainer":
        kinds.append("trainer")
    if category == "Energy":
        kinds.append("energie")
    if stage_u == "LEVEL-UP" or "Lv.X" in name or "Lv.X" in name_d:
        kinds.append("lvx")
    if stage_u == "VMAX":
        kinds.append("vmax")
    if stage_u == "VSTAR":
        kinds.append("vstar")
    if stage_u == "V-UNION":
        kinds.append("vunion")
    if stage_u == "MEGA" or (rarity or "").startswith("Mega "):
        kinds.append("mega")
    if stage_u == "BREAK":
        kinds.append("break")
    if suffix_s == "ex":
        kinds.append("ex")
    if suffix_s == "EX":
        kinds.append("exgross")
    if suffix_s in ("GX", "TAG TEAM-GX"):
        kinds.append("gx")
    if suffix_s == "TAG TEAM-GX":
        kinds.append("tagteam")
    if suffix_s == "V":
        kinds.append("v")
    if suffix_s == "Prime":
        kinds.append("prime")
    if suffix_s == "Legend" or rarity == "LEGEND":
        kinds.append("legend")
    if suffix_s == "SP":
        kinds.append("sp")
    if category == "Pokemon" or category == "Pokémon":
        if name.startswith("Shining ") or name_d.startswith("Schillernde") or name_d.startswith("Schimmernde"):
            kinds.append("shining")
        if "☆" in name or "☆" in name_d or name.endswith(" Star") or " Star δ" in name:
            kinds.append("goldstar")
        if name.startswith("Dark ") or name_d.startswith("Dunkle"):
            kinds.append("dark")
        if name.startswith("Light ") or name_d.startswith("Helle"):
            kinds.append("light")
    if rarity in SHINY_RARITIES and "shiny" not in kinds:
        kinds.append("shiny")
    if rarity == "Radiant Rare":
        kinds.append("radiant")
    if rarity == "Amazing Rare":
        kinds.append("amazing")
    if rarity == "ACE SPEC Rare":
        kinds.append("acespec")
    if "δ" in name or "δ" in name_d:
        kinds.append("delta")
    if "◇" in name or "◇" in name_d:
        kinds.append("prism")
    if not kinds:
        kinds.append("pokemon")
    return kinds

app = FastAPI(title="Binderplan", docs_url=None, redoc_url=None)


def get_db():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init_db():
    con = get_db()
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS sets (
            id TEXT PRIMARY KEY, name TEXT, serie_id TEXT, serie_name TEXT,
            release_date TEXT, total INTEGER, official INTEGER, symbol TEXT
        );
        CREATE TABLE IF NOT EXISTS cards (
            id TEXT PRIMARY KEY, set_id TEXT, local_id TEXT, local_num INTEGER,
            name_de TEXT, name_en TEXT, image_de TEXT, image_en TEXT,
            category TEXT, rarity TEXT, stage TEXT, suffix TEXT, kind TEXT,
            dex_ids TEXT, first_dex INTEGER, types TEXT,
            has_normal INTEGER DEFAULT 1, has_reverse INTEGER DEFAULT 0,
            has_holo INTEGER DEFAULT 0, release_date TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_cards_set ON cards(set_id, local_num);
        CREATE INDEX IF NOT EXISTS idx_cards_kind ON cards(kind);
        CREATE INDEX IF NOT EXISTS idx_cards_dex ON cards(first_dex);
        CREATE TABLE IF NOT EXISTS pokemon (
            dex_id INTEGER PRIMARY KEY, name_de TEXT, name_en TEXT, gen INTEGER
        );
        CREATE TABLE IF NOT EXISTS binders (
            id TEXT PRIMARY KEY, name TEXT, mode TEXT, layout TEXT,
            options TEXT, items TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS card_prices (
            card_id TEXT PRIMARY KEY, eur REAL, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            pw_hash TEXT NOT NULL, salt TEXT NOT NULL,
            plan TEXT DEFAULT 'free',            -- free | pro | lifetime
            stripe_customer TEXT, stripe_sub TEXT,
            exports_monat TEXT DEFAULT '',       -- 'JJJJ-MM:anzahl'
            preise_tag TEXT DEFAULT '',          -- letzter Preis-Abruf (frei: 1x/Tag)
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS price_history (
            card_id TEXT, datum TEXT, eur REAL,
            PRIMARY KEY (card_id, datum)
        );
        """
    )
    # Spätere Spalten additiv nachziehen
    for alter in (
        "ALTER TABLE sets ADD COLUMN name_en TEXT",
        "ALTER TABLE sets ADD COLUMN serie_name_en TEXT",
        "ALTER TABLE cards ADD COLUMN kinds TEXT",
        "ALTER TABLE cards ADD COLUMN image_alt TEXT",
        "ALTER TABLE binders ADD COLUMN user_id INTEGER",
        "ALTER TABLE users ADD COLUMN reset_token TEXT",
        "ALTER TABLE users ADD COLUMN reset_bis TEXT",
        # 2026-08-27: Anzeigename, Export-Kulanz, Holo-Preise, Japan-Region, Erstauflage
        "ALTER TABLE users ADD COLUMN name TEXT",
        "ALTER TABLE users ADD COLUMN letzter_export TEXT",   # 'binder_id:JJJJ-MM-TT HH:MM:SS'
        "ALTER TABLE card_prices ADD COLUMN eur_holo REAL",
        "ALTER TABLE sets ADD COLUMN region TEXT DEFAULT 'intl'",
        "ALTER TABLE cards ADD COLUMN region TEXT DEFAULT 'intl'",
        "ALTER TABLE cards ADD COLUMN has_first INTEGER DEFAULT 0",
        "ALTER TABLE pokemon ADD COLUMN name_ja TEXT",
        "ALTER TABLE cards ADD COLUMN name_ja TEXT",
        # 2026-08-27 (Filter-Ausbau): Illustrator, Regulation Mark, HP, Entwicklung, Trainer-/Energie-Typ
        "ALTER TABLE cards ADD COLUMN illustrator TEXT",
        "ALTER TABLE cards ADD COLUMN regulation_mark TEXT",
        "ALTER TABLE cards ADD COLUMN hp INTEGER",
        "ALTER TABLE cards ADD COLUMN evolve_from TEXT",
        "ALTER TABLE cards ADD COLUMN trainer_type TEXT",
        "ALTER TABLE cards ADD COLUMN energy_type TEXT",
        "ALTER TABLE pokemon ADD COLUMN familie INTEGER",     # PokéAPI-Entwicklungskette
        "ALTER TABLE pokemon ADD COLUMN evo_stufe INTEGER",
        "ALTER TABLE sets ADD COLUMN symbol_alt TEXT",      # pokemontcg.io-Symbol, wenn TCGdex keins hat
    ):
        try:
            con.execute(alter)
        except sqlite3.OperationalError:
            pass
    con.execute("CREATE INDEX IF NOT EXISTS idx_cards_region ON cards(region)")   # erst nach dem ALTER möglich
    con.execute("CREATE INDEX IF NOT EXISTS idx_cards_illu ON cards(illustrator)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_pokemon_familie ON pokemon(familie)")
    con.commit()
    con.close()


init_db()


def recompute_kinds():
    """Kartenarten aus den lokalen Feldern neu ableiten (kein API-Zugriff nötig)."""
    con = get_db()
    # Datenkorrektur: DP-Ära-Secret-Shinies tragen bei TCGdex fälschlich die Lv.X-Rarität
    con.execute(
        "UPDATE cards SET rarity = 'Secret Rare' WHERE local_id LIKE 'SH%' AND rarity = 'Rare Holo LV.X'"
    )
    rows = con.execute(
        "SELECT id, category, stage, suffix, rarity, name_en, name_de, local_id FROM cards"
    ).fetchall()
    for r in rows:
        kinds = _compute_kinds(r["category"], r["stage"], r["suffix"], r["rarity"],
                               r["name_en"], r["name_de"], r["local_id"])
        con.execute("UPDATE cards SET kinds = ?, kind = ? WHERE id = ?",
                    (json.dumps(kinds), kinds[0], r["id"]))
    con.commit()
    con.close()


def backfill_en_names():
    """Englische Set-/Seriennamen nachladen (2 Requests, einmalig)."""
    with httpx.Client(timeout=30, headers=UA) as client:
        en_sets = {s["id"]: s["name"] for s in client.get(f"{TCGDEX}/en/sets").json()}
        en_series = {s["id"]: s["name"] for s in client.get(f"{TCGDEX}/en/series").json()}
    con = get_db()
    for sid, name in en_sets.items():
        con.execute("UPDATE sets SET name_en = ? WHERE id = ?", (name, sid))
    for r in con.execute("SELECT id, serie_id FROM sets").fetchall():
        if r["serie_id"] in en_series:
            con.execute("UPDATE sets SET serie_name_en = ? WHERE id = ?",
                        (en_series[r["serie_id"]], r["id"]))
    # Sets ohne deutschen Namen: englischen übernehmen
    con.execute("UPDATE sets SET name = name_en WHERE (name IS NULL OR name = '') AND name_en IS NOT NULL")
    con.commit()
    con.close()


def _maybe_backfill():
    con = get_db()
    fehlt_en = con.execute(
        "SELECT COUNT(*) c FROM sets WHERE name_en IS NULL"
    ).fetchone()["c"]
    fehlt_kinds = con.execute(
        "SELECT COUNT(*) c FROM cards WHERE kinds IS NULL"
    ).fetchone()["c"]
    con.close()
    if fehlt_en:
        try:
            backfill_en_names()
        except Exception:
            pass
    if fehlt_kinds:
        recompute_kinds()


# --- Katalog-Sync -----------------------------------------------------------

SYNC = {"running": False, "step": "", "done": 0, "total": 0, "error": None}
_sync_lock = threading.Lock()


def _card_kind(category, stage, suffix, name_en):
    """Grobe Kartenart für die Filter-Chips ableiten."""
    if category == "Trainer":
        return "trainer"
    if category == "Energy":
        return "energie"
    stage = (stage or "").upper()
    suffix = (suffix or "").strip()
    name = name_en or ""
    if stage == "LEVEL-UP" or "Lv.X" in name:
        return "lvx"
    if stage == "VMAX":
        return "vmax"
    if stage == "VSTAR":
        return "vstar"
    if stage in ("MEGA", "MEGA-EX"):
        return "mega"
    if stage == "BREAK":
        return "break"
    if suffix == "GX":
        return "gx"
    if suffix == "V":
        return "v"
    if suffix in ("EX", "ex"):
        return "ex"
    return "pokemon"


def _local_num(local_id):
    m = re.search(r"\d+", str(local_id or ""))
    return int(m.group()) if m else 100000


def _sync_sets(client, con):
    SYNC["step"] = "Sets laden"
    de_sets = client.get(f"{TCGDEX}/de/sets").json()
    en_sets = client.get(f"{TCGDEX}/en/sets").json()
    en_names = {s["id"]: s["name"] for s in en_sets}
    en_series = {s["id"]: s["name"] for s in client.get(f"{TCGDEX}/en/series").json()}
    de_ids = {s["id"] for s in de_sets}
    todo = [("de", s["id"]) for s in de_sets] + [
        ("en", s["id"]) for s in en_sets if s["id"] not in de_ids
    ]
    pocket = {s["id"] for s in de_sets + en_sets if (s.get("serie") or {}).get("id") in POCKET_SERIEN}
    todo = [x for x in todo if x[1] not in pocket]
    SYNC["total"] = len(todo)
    SYNC["done"] = 0

    def fetch(item):
        lang, sid = item
        try:
            return client.get(f"{TCGDEX}/{lang}/sets/{sid}").json()
        except Exception:
            return None

    with ThreadPoolExecutor(6) as pool:
        for detail in pool.map(fetch, todo):
            SYNC["done"] += 1
            if not detail or "id" not in detail:
                continue
            serie = detail.get("serie") or {}
            cc = detail.get("cardCount") or {}
            con.execute(
                "INSERT OR REPLACE INTO sets (id,name,serie_id,serie_name,release_date,total,official,symbol,name_en,serie_name_en)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (detail["id"], detail.get("name"), serie.get("id"), serie.get("name"),
                 detail.get("releaseDate"), cc.get("total"), cc.get("official"),
                 detail.get("symbol"), en_names.get(detail["id"]),
                 en_series.get(serie.get("id"))),
            )
    con.commit()


def _sync_cards(client, con):
    # 1) Deutsche Kurzliste: Namen + deutsche Bild-URLs
    SYNC["step"] = "Deutsche Kartenliste laden"
    de_brief = {c["id"]: c for c in client.get(f"{TCGDEX}/de/cards").json()}

    # 2) GraphQL-Massenabfrage: Typen, Pokédex-Nr., Stage, Varianten usw.
    SYNC["step"] = "Kartendetails laden"
    page = 1
    seen = 0
    query = (
        "query($p: Int!) { cards(pagination: {page: $p, itemsPerPage: 1000}, filters: {}) "
        "{ id localId name image category rarity stage suffix dexId types "
        "set { id } variants { normal reverse holo } } }"
    )
    while True:
        r = client.post(
            f"{TCGDEX}/graphql",
            json={"query": query, "variables": {"p": page}},
        )
        data = r.json()
        cards = (data.get("data") or {}).get("cards") or []
        if not cards:
            break
        for c in cards:
            de = de_brief.get(c["id"], {})
            dex = c.get("dexId") or []
            variants = c.get("variants") or {}
            set_id = (c.get("set") or {}).get("id") or c["id"].rsplit("-", 1)[0]
            kind = _card_kind(c.get("category"), c.get("stage"), c.get("suffix"), c.get("name"))
            con.execute(
                "INSERT OR REPLACE INTO cards (id,set_id,local_id,local_num,name_de,name_en,"
                "image_de,image_en,category,rarity,stage,suffix,kind,dex_ids,first_dex,types,"
                "has_normal,has_reverse,has_holo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (c["id"], set_id, c.get("localId"), _local_num(c.get("localId")),
                 de.get("name"), c.get("name"), de.get("image"), c.get("image"),
                 c.get("category"), c.get("rarity"), c.get("stage"), c.get("suffix"), kind,
                 json.dumps(dex), dex[0] if dex else None, json.dumps(c.get("types") or []),
                 1 if variants.get("normal") else 0, 1 if variants.get("reverse") else 0,
                 1 if variants.get("holo") else 0),
            )
        seen += len(cards)
        SYNC["done"] = seen
        SYNC["total"] = max(SYNC["total"], seen)
        con.commit()
        if len(cards) < 1000:
            break
        page += 1

    # Karten, die es nur auf Deutsch gibt (GraphQL läuft englisch)
    have = {row["id"] for row in con.execute("SELECT id FROM cards")}
    for cid, c in de_brief.items():
        if cid in have:
            continue
        set_id = cid.rsplit("-", 1)[0]
        con.execute(
            "INSERT OR REPLACE INTO cards (id,set_id,local_id,local_num,name_de,image_de,kind)"
            " VALUES (?,?,?,?,?,?, 'pokemon')",
            (cid, set_id, c.get("localId"), _local_num(c.get("localId")),
             c.get("name"), c.get("image")),
        )

    # Erscheinungsdatum vom Set auf die Karte denormalisieren (schnelle Sortierung)
    con.execute(
        "UPDATE cards SET release_date = (SELECT release_date FROM sets WHERE sets.id = cards.set_id)"
    )
    con.commit()


def _sync_pokedex(client, con):
    SYNC["step"] = "Pokédex-Namen laden"
    listing = client.get("https://pokeapi.co/api/v2/pokemon-species?limit=1400").json()
    ids = []
    for entry in listing.get("results", []):
        m = re.search(r"/(\d+)/?$", entry["url"])
        if m:
            ids.append(int(m.group(1)))
    ids = sorted(i for i in ids if i <= GEN_RANGES[-1][2])
    SYNC["total"] = len(ids)
    SYNC["done"] = 0

    def fetch(dex_id):
        try:
            d = client.get(f"https://pokeapi.co/api/v2/pokemon-species/{dex_id}").json()
            name_de = next((n["name"] for n in d.get("names", []) if n["language"]["name"] == "de"), None)
            name_en = next((n["name"] for n in d.get("names", []) if n["language"]["name"] == "en"), None)
            name_ja = next((n["name"] for n in d.get("names", []) if n["language"]["name"] == "ja"), None)
            return dex_id, name_de, name_en or (d.get("name") or "").capitalize(), name_ja
        except Exception:
            return dex_id, None, None, None

    with ThreadPoolExecutor(8) as pool:
        for dex_id, name_de, name_en, name_ja in pool.map(fetch, ids):
            SYNC["done"] += 1
            gen = next((g for g, lo, hi in GEN_RANGES if lo <= dex_id <= hi), None)
            con.execute(
                "INSERT OR REPLACE INTO pokemon (dex_id,name_de,name_en,gen,name_ja) VALUES (?,?,?,?,?)",
                (dex_id, name_de or (name_en or "").capitalize(), name_en, gen, name_ja),
            )
    con.commit()


def run_sync():
    if not _sync_lock.acquire(blocking=False):
        return
    con = get_db()
    try:
        SYNC.update(running=True, error=None, step="Start", done=0, total=0)
        with httpx.Client(timeout=60, headers=UA) as client:
            _sync_sets(client, con)
            _sync_cards(client, con)
            _sync_pokedex(client, con)
        SYNC["step"] = "Kartenarten ableiten"
        # digitale Pocket-Karten, die über die Kartenliste hereinkamen, wieder entfernen
        con.execute("DELETE FROM cards WHERE set_id IN (SELECT id FROM sets WHERE serie_id IN (%s))" % ",".join("?" * len(POCKET_SERIEN)), list(POCKET_SERIEN))
        con.execute("DELETE FROM cards WHERE set_id NOT IN (SELECT id FROM sets) AND COALESCE(region,'intl') = 'intl'")
        con.execute("DELETE FROM sets WHERE serie_id IN (%s)" % ",".join("?" * len(POCKET_SERIEN)), list(POCKET_SERIEN))
        con.commit()
        recompute_kinds()
        con.execute(
            "INSERT OR REPLACE INTO kv (key,value) VALUES ('last_sync', datetime('now'))"
        )
        con.commit()
        SYNC["step"] = "fertig"
    except Exception as exc:  # Sync darf die App nie mitreißen
        SYNC["error"] = str(exc)[:500]
    finally:
        SYNC["running"] = False
        con.close()
        _sync_lock.release()


# --- Japan (TCGdex „ja“) ----------------------------------------------------
# Japanische Sets laufen als eigener Set-Baum (region='jp'): Namen japanisch,
# Bilder über dieselbe CDN, Pokédex-Nummer per Namensabgleich mit den
# japanischen Pokémon-Namen aus der PokéAPI (TCGdex liefert für ja keine
# GraphQL-Details). Cardmarket-Preise kommen über dasselbe pricing-Feld.

def _ja_pokemon_namen(con):
    rows = con.execute("SELECT dex_id, name_ja FROM pokemon WHERE name_ja IS NOT NULL").fetchall()
    # längste Namen zuerst, damit „リザードン“ nicht auf „リザード“ matcht
    return sorted(((r["name_ja"], r["dex_id"]) for r in rows), key=lambda x: -len(x[0]))


def _ja_dex_fuer_name(name, namen):
    n = (name or "")
    for jn, dex in namen:
        if jn and jn in n:
            return dex
    return None


def _ja_kinds(name):
    n = name or ""
    kinds = []
    if "ex" in n and n.endswith("ex"):
        kinds.append("ex")
    if n.endswith("V"):
        kinds.append("v")
    if n.endswith("VMAX"):
        kinds = ["vmax"]
    if n.endswith("VSTAR"):
        kinds = ["vstar"]
    if n.endswith("GX"):
        kinds.append("gx")
    if "エネルギー" in n:
        kinds = ["energie"]
    return kinds or ["pokemon"]


def run_sync_ja():
    """Japanische Sets + Karten einmalig laden (Admin-Endpunkt / automatisch, wenn leer)."""
    if not _sync_lock.acquire(blocking=False):
        return
    con = get_db()
    try:
        SYNC.update(running=True, error=None, step="Japan: Sets laden", done=0, total=0)
        namen = _ja_pokemon_namen(con)
        with httpx.Client(timeout=60, headers=UA) as client:
            sets = client.get(f"{TCGDEX}/ja/sets").json()
            SYNC["total"] = len(sets)

            def fetch(sid):
                try:
                    return client.get(f"{TCGDEX}/ja/sets/{sid}").json()
                except Exception:
                    return None

            with ThreadPoolExecutor(4) as pool:
                for d in pool.map(fetch, [x["id"] for x in sets]):
                    SYNC["done"] += 1
                    if not d or "id" not in d:
                        continue
                    serie = d.get("serie") or {}
                    cc = d.get("cardCount") or {}
                    con.execute(
                        "INSERT OR REPLACE INTO sets (id,name,serie_id,serie_name,release_date,total,official,symbol,name_en,serie_name_en,region)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,'jp')",
                        (d["id"], d.get("name"), serie.get("id"), serie.get("name"), d.get("releaseDate"),
                         cc.get("total"), cc.get("official"), d.get("symbol"), d.get("name"), serie.get("name")))
                    for c in d.get("cards") or []:
                        dex = _ja_dex_fuer_name(c.get("name"), namen)
                        kinds = _ja_kinds(c.get("name"))
                        con.execute(
                            "INSERT OR REPLACE INTO cards (id,set_id,local_id,local_num,name_de,name_en,name_ja,image_de,image_en,"
                            "category,kind,kinds,dex_ids,first_dex,types,release_date,region)"
                            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'jp')",
                            (c["id"], d["id"], c.get("localId"), _local_num(c.get("localId")),
                             None, None, c.get("name"), None, c.get("image"),
                             "Energy" if "energie" in kinds else "Pokemon", kinds[0], json.dumps(kinds),
                             json.dumps([dex] if dex else []), dex, "[]", d.get("releaseDate")))
                    con.commit()
        # lateinische Namen (DE/EN) aus dem Pokédex nachziehen, damit die Suche „Glurak“ auch JP-Karten findet
        con.execute(
            "UPDATE cards SET name_de = (SELECT name_de FROM pokemon WHERE pokemon.dex_id = cards.first_dex),"
            " name_en = (SELECT name_en FROM pokemon WHERE pokemon.dex_id = cards.first_dex)"
            " WHERE region = 'jp' AND first_dex IS NOT NULL")
        con.commit()
        SYNC["step"] = "fertig"
    except Exception as exc:
        SYNC["error"] = str(exc)[:500]
    finally:
        SYNC["running"] = False
        con.close()
        _sync_lock.release()


@app.post("/api/admin/sync_ja")
def admin_sync_ja(key: str = ""):
    if not _admin_key() or key != _admin_key():
        raise HTTPException(403, "Falscher Schlüssel")
    threading.Thread(target=run_sync_ja, daemon=True).start()
    return {"gestartet": True}


# --- Kartendetails nachladen (Illustrator, Regulation Mark, HP, Entwicklung, Trainer-/Energie-Typ) -------
# Einmalig per GraphQL-Massenabfrage (24 Seiten à 1.000 Karten). Japanische Karten haben das nicht.

def _norm_illustrator(name):
    """TCGdex führt Künstler in mehreren Schreibweisen („miki kudo“/„Miki Kudo“, doppelte Anführungszeichen)."""
    n = re.sub(r"\s+", " ", (name or "").replace('"', "").replace("“", "").replace("”", "")).strip()
    if not n:
        return None
    if n == n.lower() or n == n.upper():
        n = " ".join(w.capitalize() for w in n.split(" "))
    n = re.sub(r"\bAky ?CG Works\b", "aky CG Works", n, flags=re.I)
    return n


def run_backfill_details():
    if not _sync_lock.acquire(blocking=False):
        return
    con = get_db()
    try:
        SYNC.update(running=True, error=None, step="Kartendetails (Künstler …)", done=0, total=24)
        query = ("query($p: Int!) { cards(pagination: {page: $p, itemsPerPage: 1000}, filters: {}) "
                 "{ id illustrator regulationMark hp evolveFrom trainerType energyType variants { firstEdition } } }")
        page = 1
        with httpx.Client(timeout=90, headers=UA) as client:
            while True:
                r = client.post(f"{TCGDEX}/graphql", json={"query": query, "variables": {"p": page}})
                cards = ((r.json().get("data") or {}).get("cards") or [])
                if not cards:
                    break
                for c in cards:
                    con.execute(
                        "UPDATE cards SET illustrator=?, regulation_mark=?, hp=?, evolve_from=?, trainer_type=?, energy_type=?,"
                        " has_first=? WHERE id=?",
                        (_norm_illustrator(c.get("illustrator")), c.get("regulationMark"), c.get("hp"), c.get("evolveFrom"),
                         c.get("trainerType"), c.get("energyType"),
                         1 if (c.get("variants") or {}).get("firstEdition") else 0, c["id"]))
                con.commit()
                SYNC["done"] = page
                if len(cards) < 1000:
                    break
                page += 1
        # Entwicklungsketten (PokéAPI): Familie + Stufe je Pokémon
        SYNC["step"] = "Entwicklungsketten"
        with httpx.Client(timeout=60, headers=UA) as client:
            liste = client.get("https://pokeapi.co/api/v2/evolution-chain?limit=1000").json().get("results", [])
            SYNC["total"] = len(liste); SYNC["done"] = 0

            def fetch(url):
                try:
                    return client.get(url).json()
                except Exception:
                    return None

            def walk(knoten, stufe, out):
                m = re.search(r"/(\d+)/?$", (knoten.get("species") or {}).get("url", ""))
                if m:
                    out.append((int(m.group(1)), stufe))
                for e in knoten.get("evolves_to") or []:
                    walk(e, stufe + 1, out)

            with ThreadPoolExecutor(6) as pool:
                for d in pool.map(fetch, [x["url"] for x in liste]):
                    SYNC["done"] += 1
                    if not d or "chain" not in d:
                        continue
                    out = []
                    walk(d["chain"], 0, out)
                    for dex, stufe in out:
                        con.execute("UPDATE pokemon SET familie=?, evo_stufe=? WHERE dex_id=?", (d["id"], stufe, dex))
            con.commit()
        con.execute("INSERT OR REPLACE INTO kv (key,value) VALUES ('details_backfill', datetime('now'))")
        con.commit()
        SYNC["step"] = "fertig"
    except Exception as exc:
        SYNC["error"] = str(exc)[:500]
    finally:
        SYNC["running"] = False
        con.close()
        _sync_lock.release()


@app.post("/api/admin/backfill_details")
def admin_backfill_details(key: str = ""):
    if not _admin_key() or key != _admin_key():
        raise HTTPException(403, "Falscher Schlüssel")
    threading.Thread(target=run_backfill_details, daemon=True).start()
    return {"gestartet": True}


def _symbole_job():
    """Alle Set-Symbole in den Cache holen; Sets ohne TCGdex-Symbol bekommen das pokemontcg.io-Symbol
    (Name-Abgleich wie beim Bild-Fallback). Läuft beim Start im Hintergrund."""
    con = get_db()
    ohne = [dict(r) for r in con.execute("SELECT id, name_en, name FROM sets WHERE region='intl' AND (symbol IS NULL OR symbol='') AND symbol_alt IS NULL")]
    con.close()
    if ohne:
        def norm(x):
            return re.sub(r"[^a-z0-9]", "", (x or "").lower())
        headers = dict(UA)
        key = _env().get("PTCGIO_KEY")
        if key:
            headers["X-Api-Key"] = key
        try:
            with httpx.Client(timeout=30, headers=headers) as client:
                ptc = _ptc_get(client, "https://api.pokemontcg.io/v2/sets?pageSize=250&select=id,name,images").get("data", [])
            nach_name = {norm(x.get("name")): (x.get("images") or {}).get("symbol") for x in ptc}
            con = get_db()
            for st in ohne:
                sym = nach_name.get(norm(st["name_en"])) or nach_name.get(norm(st["name"]))
                if sym:
                    con.execute("UPDATE sets SET symbol_alt = ? WHERE id = ?", (sym, st["id"]))
            con.commit()
            con.close()
        except Exception:
            pass
    con = get_db()
    ids = [r["id"] for r in con.execute("SELECT id FROM sets WHERE (symbol IS NOT NULL AND symbol != '') OR symbol_alt IS NOT NULL")]
    con.close()

    def hole(sid):
        try:
            set_symbol_image(sid)
        except Exception:
            pass

    with ThreadPoolExecutor(6) as pool:
        list(pool.map(hole, ids))


def _preishistorie_job():
    """Täglich: alle bekannten Preise auffrischen und in die Historie schreiben."""
    con = get_db()
    ids = [r["card_id"] for r in con.execute("SELECT card_id FROM card_prices ORDER BY updated_at LIMIT 3000")]
    con.close()
    if not ids:
        return
    with httpx.Client(timeout=20, headers=UA) as client:
        with ThreadPoolExecutor(6) as pool:
            ergebnisse = list(pool.map(lambda c: _fetch_price(client, c), ids))
    con = get_db()
    for cid, eur, holo in ergebnisse:
        con.execute("INSERT OR REPLACE INTO card_prices (card_id, eur, eur_holo, updated_at) VALUES (?,?,?,datetime('now'))",
                    (cid, eur, holo))
        if eur is not None:
            con.execute("INSERT OR REPLACE INTO price_history (card_id, datum, eur) VALUES (?,?,?)", (cid, _heute(), eur))
    con.execute("INSERT OR REPLACE INTO kv (key,value) VALUES ('preishistorie_lauf', datetime('now'))")
    con.commit()
    con.close()


def _aufraeumen_job():
    """Leere Gast-Binder (ohne Konto, ohne Karten, älter als 2 Tage) entfernen."""
    con = get_db()
    con.execute("DELETE FROM binders WHERE user_id IS NULL AND (items IS NULL OR items = '[]')"
                " AND created_at < datetime('now', '-2 days')")
    con.commit()
    con.close()


def _hintergrund_takt():
    import time as _time
    while True:
        try:
            _aufraeumen_job()
            con = get_db()
            letzter = con.execute("SELECT value FROM kv WHERE key='preishistorie_lauf'").fetchone()
            con.close()
            if not letzter or letzter["value"] < datetime_str_vor(23):
                _preishistorie_job()
        except Exception:
            pass
        _time.sleep(3600)


def _maybe_autosync():
    con = get_db()
    n = con.execute("SELECT COUNT(*) c FROM cards").fetchone()["c"]
    con.close()
    if n == 0:
        threading.Thread(target=run_sync, daemon=True).start()
    else:
        _maybe_backfill()
        con = get_db()
        fehlt = con.execute("SELECT COUNT(*) c FROM cards WHERE region='intl' AND illustrator IS NULL").fetchone()["c"]
        done = con.execute("SELECT value FROM kv WHERE key='details_backfill'").fetchone()
        con.close()
        if fehlt > 5000 and not done:
            threading.Thread(target=run_backfill_details, daemon=True).start()
        threading.Thread(target=_symbole_job, daemon=True).start()
    threading.Thread(target=_hintergrund_takt, daemon=True).start()


threading.Thread(target=_maybe_autosync, daemon=True).start()


def _admin_key():
    env = (BASE / ".env").read_text() if (BASE / ".env").exists() else ""
    m = re.search(r"^ADMIN_KEY=(.+)$", env, re.M)
    return m.group(1).strip() if m else None


# --- Basis-Endpunkte --------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/admin/sync")
def admin_sync(key: str = ""):
    if not _admin_key() or key != _admin_key():
        raise HTTPException(403, "Falscher Schlüssel")
    threading.Thread(target=run_sync, daemon=True).start()
    return {"gestartet": True}


def _ptc_get(client, url, params=None):
    """pokemontcg.io antwortet sporadisch mit leeren 500ern — mit Backoff wiederholen."""
    import time as _time
    for versuch in range(5):
        try:
            r = client.get(url, params=params)
            if r.status_code == 200 and r.content:
                return r.json()
        except Exception:
            pass
        _time.sleep(2 * (versuch + 1))
    return {}


def _bilder_fallback_job():
    """Bildlücken (TCGdex ohne Scan) über pokemontcg.io füllen.
    Set-Zuordnung über den englischen Set-Namen, Karten über die Setnummer."""
    import time as _time
    def norm(s):
        return re.sub(r"[^a-z0-9]", "", (s or "").lower())

    con = get_db()
    luecken = con.execute(
        "SELECT set_id, COUNT(*) n FROM cards WHERE image_de IS NULL AND image_en IS NULL"
        " AND image_alt IS NULL GROUP BY set_id"
    ).fetchall()
    unsere = {r["id"]: norm(r["name_en"]) for r in con.execute("SELECT id, name_en FROM sets")}
    con.close()
    if not luecken:
        return
    headers = dict(UA)
    key = _env().get("PTCGIO_KEY")
    if key:
        headers["X-Api-Key"] = key
    try:
        with httpx.Client(timeout=30, headers=headers) as client:
            ptc_sets = _ptc_get(client, "https://api.pokemontcg.io/v2/sets?pageSize=250").get("data", [])
            nach_name = {norm(s.get("name")): s["id"] for s in ptc_sets}
            gefunden = 0
            for lk in luecken:
                sid = lk["set_id"]
                ptc_id = nach_name.get(unsere.get(sid, ""))
                if not ptc_id:
                    continue
                seite = 1
                nummern = {}
                namen = {}
                try:
                    while True:
                        # Achtung: "select" MUSS "id" enthalten, sonst antwortet die API mit 500
                        r = _ptc_get(
                            client, "https://api.pokemontcg.io/v2/cards",
                            params={"q": f"set.id:{ptc_id}", "pageSize": 250, "page": seite,
                                    "select": "id,number,images,name"},
                        )
                        daten = r.get("data", [])
                        for karte in daten:
                            bild = (karte.get("images") or {}).get("small")
                            if bild:
                                nummern[norm(str(karte.get("number", "")))] = bild
                                namen.setdefault(norm(karte.get("name")), bild)
                        if len(daten) < 250:
                            break
                        seite += 1
                        _time.sleep(1)
                except Exception:
                    continue  # einzelnes Set überspringen, Job läuft weiter
                if not nummern:
                    continue
                con = get_db()
                for r2 in con.execute(
                    "SELECT id, local_id, name_en FROM cards WHERE set_id = ? AND image_de IS NULL"
                    " AND image_en IS NULL AND image_alt IS NULL", (sid,)
                ).fetchall():
                    # erst Setnummer, sonst Kartenname (z. B. Classic Collection: CC001 vs. Originalnummern)
                    bild = nummern.get(norm(str(r2["local_id"] or ""))) or namen.get(norm(r2["name_en"]))
                    if bild:
                        con.execute("UPDATE cards SET image_alt = ? WHERE id = ?", (bild, r2["id"]))
                        gefunden += 1
                con.commit()
                con.close()
                _time.sleep(2)
        con = get_db()
        con.execute("INSERT OR REPLACE INTO kv (key,value) VALUES ('bilder_fallback', ?)", (str(gefunden),))
        con.commit()
        con.close()
    except Exception:
        pass


@app.post("/api/admin/bilder_fallback")
def admin_bilder_fallback(key: str = ""):
    if not _admin_key() or key != _admin_key():
        raise HTTPException(403, "Falscher Schlüssel")
    threading.Thread(target=_bilder_fallback_job, daemon=True).start()
    return {"gestartet": True}


@app.get("/api/admin/stats")
def admin_stats(key: str = ""):
    """Interne Kennzahlen fürs Empire-Dashboard (Venture Lab), nur mit ADMIN_KEY."""
    if not _admin_key() or key != _admin_key():
        raise HTTPException(403, "Falscher Schlüssel")
    con = get_db()
    binder = con.execute("SELECT COUNT(*) c FROM binders").fetchone()["c"]
    neu7 = con.execute(
        "SELECT COUNT(*) c FROM binders WHERE created_at >= datetime('now','-7 days')"
    ).fetchone()["c"]
    karten = con.execute("SELECT COUNT(*) c FROM cards").fetchone()["c"]
    pdfs = con.execute("SELECT value FROM kv WHERE key='pdf_exports'").fetchone()
    con.close()
    return {"kpis": [
        {"label": "Binder", "value": binder, "color": "blue"},
        {"label": "Neu (7T)", "value": neu7, "color": "green" if neu7 else None},
        {"label": "PDF-Exporte", "value": int(pdfs["value"]) if pdfs else 0},
        {"label": "Karten im Katalog", "value": karten},
    ]}


@app.get("/api/meta")
def meta():
    con = get_db()
    counts = {
        "cards": con.execute("SELECT COUNT(*) c FROM cards").fetchone()["c"],
        "sets": con.execute("SELECT COUNT(*) c FROM sets").fetchone()["c"],
        "pokemon": con.execute("SELECT COUNT(*) c FROM pokemon").fetchone()["c"],
    }
    sets = []
    vorhandene_aeren = set()
    for r in con.execute(
        "SELECT id,name,name_en,serie_id,serie_name,serie_name_en,release_date,total,official,symbol,region,symbol_alt"
        " FROM sets ORDER BY release_date IS NULL, release_date"
    ):
        d = dict(r)
        d["region"] = d.get("region") or "intl"
        # Promos, Jumbo-Karten, McDonald's & Co. sind keine Sammel-Sets – sie wandern
        # in der Set-Liste ans Ende ihrer Ära (vorher stand „Miscellaneous Promos“ ganz oben)
        d["promo"] = bool(re.search(r"promo|jumbo|misc|mcdonald|trainer kit|pop series|deck|starter", (d.get("name_en") or d.get("name") or ""), re.I)
                          or (d["serie_id"] in ("pop", "tk", "mc", "misc")))
        if d["region"] == "jp":
            jn = JP_AEREN.get(d["serie_id"] or "", (d["serie_name"] or d["serie_id"] or "?",) * 2)
            d["aera"] = "jp:" + (d["serie_id"] or "?"); d["aera_name"] = jn[0]; d["aera_name_en"] = jn[1]
            d["symbol"] = d["symbol"] or None
            sets.append(d)
            continue
        d["symbol"] = d["symbol"] or (r["symbol_alt"] if "symbol_alt" in r.keys() and r["symbol_alt"] else None)
        d["name"] = SET_NAME_FIX_DE.get(d["id"], d["name"]) or d["name_en"]
        d["serie_name"] = SERIE_NAME_FIX_DE.get(d["serie_id"], d["serie_name"]) or d["serie_name_en"]
        aera = _aera_fuer_set(d["serie_id"], d["release_date"])
        info = next(a for a in AEREN if a["id"] == aera)
        d["aera"] = aera
        d["aera_name"] = info["name"]
        d["aera_name_en"] = info["name_en"]
        vorhandene_aeren.add(aera)
        sets.append(d)
    # Ären-Reihenfolge, innerhalb einer Ära erst Sammel-Sets chronologisch, dann Promos; Japan als eigener Baum
    jp_start = {}
    for x in sets:
        if x["region"] == "jp":
            jp_start[x["aera"]] = min(jp_start.get(x["aera"], "9999"), x["release_date"] or "9999")
    sets.sort(key=lambda s: (AERA_ORDNUNG.get(s["aera"], 99) if s["region"] != "jp" else 100, jp_start.get(s["aera"], ""), s["promo"], s["release_date"] or "9999"))
    series = [
        {"id": a["id"], "name": a["name"], "name_en": a["name_en"],
         "von": a["von"], "bis": a["bis"], "region": "intl"}
        for a in AEREN if a["id"] in vorhandene_aeren
    ]
    for aera, start in sorted(jp_start.items(), key=lambda x: x[1]):
        bsp = next(x for x in sets if x["aera"] == aera)
        jahre = [x["release_date"][:4] for x in sets if x["aera"] == aera and x["release_date"]]
        series.append({"id": aera, "name": bsp["aera_name"], "name_en": bsp["aera_name_en"], "von": min(jahre) if jahre else "", "bis": max(jahre) if jahre else "", "region": "jp"})
    rarities = [
        {"rarity": r["rarity"], "anzahl": r["c"]}
        for r in con.execute(
            "SELECT rarity, COUNT(*) c FROM cards WHERE rarity IS NOT NULL AND rarity != 'None'"
            " GROUP BY rarity ORDER BY c DESC"
        )
    ]
    last_sync = con.execute("SELECT value FROM kv WHERE key='last_sync'").fetchone()
    alle_illu = {r["illustrator"]: r["c"] for r in con.execute(
        "SELECT illustrator, COUNT(*) c FROM cards WHERE illustrator IS NOT NULL GROUP BY illustrator HAVING c >= 3")}
    nach_klein = {n.lower(): n for n in alle_illu}
    top_namen = [nach_klein[t.lower()] for t in TOP_ARTISTS if t.lower() in nach_klein]
    illustrators = [{"name": n, "anzahl": alle_illu[n], "top": True} for n in top_namen]
    illustrators += [{"name": n, "anzahl": c, "top": False} for n, c in sorted(alle_illu.items(), key=lambda x: x[0].lower()) if n not in top_namen]
    regmarks = [r["regulation_mark"] for r in con.execute(
        "SELECT DISTINCT regulation_mark FROM cards WHERE regulation_mark IS NOT NULL AND regulation_mark != 'None' ORDER BY regulation_mark")]
    trainer_types = [r["trainer_type"] for r in con.execute(
        "SELECT trainer_type FROM cards WHERE trainer_type IS NOT NULL GROUP BY trainer_type ORDER BY COUNT(*) DESC")]
    jahre = con.execute("SELECT MIN(substr(release_date,1,4)) a, MAX(substr(release_date,1,4)) b FROM cards WHERE release_date IS NOT NULL AND region='intl'").fetchone()
    familien = con.execute("SELECT COUNT(*) c FROM pokemon WHERE familie IS NOT NULL").fetchone()["c"]
    con.close()
    return {
        "sync": {**SYNC, "last": last_sync["value"] if last_sync else None},
        "illustrators": illustrators,
        "rarity_groups": [{"id": k, **{x: v[x] for x in ("name", "name_en")}} for k, v in RARITY_GROUPS.items()],
        "presets": [{"id": k, "name": v["name"], "name_en": v["name_en"]} for k, v in PRESETS.items()],
        "regmarks": regmarks, "trainer_types": trainer_types,
        "jahre": [int(jahre["a"] or 1999), int(jahre["b"] or 2026)], "familien": familien,
        "counts": counts,
        "sets": sets,
        "series": series,
        "rarities": rarities,
        "types": TYPES_DE,
        "gens": [{"gen": g, "von": lo, "bis": hi} for g, lo, hi in GEN_RANGES],
    }


# --- Seltenheits-Gruppen & Sammel-Schnellauswahlen ---------------------------
# 39 Roh-Seltenheiten (inkl. TCG Pocket „One Diamond“ …) sind kein Filter, den ein Sammler
# versteht – die Gruppen entsprechen dem Sprachgebrauch: Illustration Rares, Full Arts, Secret/Gold …
RARITY_GROUPS = {
    "common":       {"name": "Common", "name_en": "Common", "werte": ["Common"]},
    "uncommon":     {"name": "Uncommon", "name_en": "Uncommon", "werte": ["Uncommon"]},
    "rare":         {"name": "Rare / Holo", "name_en": "Rare / Holo", "werte": ["Rare", "Rare Holo", "Holo Rare", "Double rare", "Holo Rare V", "Holo Rare VMAX", "Holo Rare VSTAR", "Rare Holo LV.X", "Rare PRIME", "LEGEND", "Classic Collection"]},
    "ultra":        {"name": "Ultra Rare / Full Art", "name_en": "Ultra Rare / Full Art", "werte": ["Ultra Rare", "Full Art Trainer"]},
    "illustration": {"name": "Illustration Rare / Alt Art", "name_en": "Illustration Rare / Alt Art", "werte": ["Illustration rare", "Special illustration rare"]},
    "secret":       {"name": "Secret / Gold / Rainbow", "name_en": "Secret / Gold / Rainbow", "werte": ["Secret Rare", "Hyper rare", "Mega Hyper Rare", "Black White Rare"]},
    "shiny":        {"name": "Shiny", "name_en": "Shiny", "werte": ["Shiny rare", "Shiny rare V", "Shiny rare VMAX", "Shiny Ultra Rare"]},
    "special":      {"name": "Radiant / Amazing / ACE SPEC", "name_en": "Radiant / Amazing / ACE SPEC", "werte": ["Radiant Rare", "Amazing Rare", "ACE SPEC Rare"]},
    "promo":        {"name": "Promo", "name_en": "Promo", "werte": ["Promo"]},
}
# Meistgesammelte Illustratoren (Recherche 2026-08-27, siehe TOP_ARTISTS_QUELLEN) – stehen im Künstler-
# Dropdown ganz oben; danach alle übrigen alphabetisch.
TOP_ARTISTS = [
    "Mitsuhiro Arita", "Shinji Kanda", "Akira Egawa", "Yuka Morii", "Tomokazu Komiya", "Sowsow",
    "Kagemaru Himeno", "Atsuko Nishida", "Ken Sugimori", "Kouki Saitou", "Masakazu Fukuda", "Ryo Ueda",
    "Naoyo Kimura", "Tokiya", "Asako Ito", "Naoki Saito", "Shin Nagasawa", "Oswaldo Kato", "Narumi Sato", "Mugi Hamada",
]
TOP_ARTISTS_QUELLEN = "snkrdunk.com, woahpoke.com, thegamer.com, crispycards.de"

# Beliebte Sammelthemen als Pokédex-Listen (Grundformen; Entwicklungen kommen über die Familie dazu)
PRESETS = {
    "starter":   {"name": "Starter", "name_en": "Starters", "dex": [1, 4, 7, 152, 155, 158, 252, 255, 258, 387, 390, 393, 495, 498, 501, 650, 653, 656, 722, 725, 728, 810, 813, 816, 906, 909, 912], "familie": True},
    "legendary": {"name": "Legendäre & Mysteriöse", "name_en": "Legendary & Mythical", "dex": [144, 145, 146, 150, 151, 243, 244, 245, 249, 250, 251, 377, 378, 379, 380, 381, 382, 383, 384, 385, 386, 480, 481, 482, 483, 484, 485, 486, 487, 488, 489, 490, 491, 492, 493, 494, 638, 639, 640, 641, 642, 643, 644, 645, 646, 647, 648, 649, 716, 717, 718, 719, 720, 721, 772, 773, 785, 786, 787, 788, 789, 790, 791, 792, 793, 794, 795, 796, 797, 798, 799, 800, 801, 802, 803, 804, 805, 806, 807, 808, 809, 888, 889, 890, 891, 892, 893, 894, 895, 896, 897, 898, 905, 1001, 1002, 1003, 1004, 1007, 1008, 1014, 1015, 1016, 1017, 1020, 1021, 1022, 1023, 1024, 1025], "familie": False},
    "eevee":     {"name": "Evoli & Entwicklungen", "name_en": "Eeveelutions", "dex": [133, 134, 135, 136, 196, 197, 470, 471, 700], "familie": False},
    "baby":      {"name": "Baby-Pokémon", "name_en": "Baby Pokémon", "dex": [172, 173, 174, 175, 236, 238, 239, 240, 298, 360, 406, 433, 438, 439, 440, 446, 447, 458, 848], "familie": False},
    "pikachu":   {"name": "Pikachu-Familie", "name_en": "Pikachu family", "dex": [25, 26, 172], "familie": False},
}


def _familie_dex(con, dex_ids):
    """Alle Pokédex-Nummern der Entwicklungsfamilien der gegebenen Pokémon."""
    if not dex_ids:
        return []
    fams = [r["familie"] for r in con.execute(
        "SELECT DISTINCT familie FROM pokemon WHERE dex_id IN (%s) AND familie IS NOT NULL" % ",".join("?" * len(dex_ids)), list(dex_ids))]
    if not fams:
        return list(dex_ids)
    return [r["dex_id"] for r in con.execute(
        "SELECT dex_id FROM pokemon WHERE familie IN (%s) ORDER BY familie, evo_stufe, dex_id" % ",".join("?" * len(fams)), fams)]


def _dex_frag(dexe):
    """WHERE-Fragment: Karte gehört zu einem der Pokémon (first_dex reicht – Mehrfach-Dex sind selten)."""
    dexe = [int(d) for d in dexe]
    if not dexe:
        return "0", []
    return "first_dex IN (%s)" % ",".join("?" * len(dexe)), dexe


# --- Kartensuche ------------------------------------------------------------

SORTS = {
    "datum": "release_date IS NULL, release_date, set_id, local_num",
    "dex": "first_dex IS NULL, first_dex, release_date",
    "name": "COALESCE(name_de, name_en) COLLATE NOCASE",
    "nummer": "set_id, local_num",
    "typ": "types, COALESCE(name_de, name_en) COLLATE NOCASE",
}


def _card_query(q, set_id, serie, typ, kind, sort, richtung, rarity="", dex=0, region="intl",
                illustrator="", rgroup="", trainer_type="", regmark="", first=0, jahr_von=0, jahr_bis=0,
                preset="", familie=0):
    where, params = [], []
    if illustrator:
        where.append("illustrator = ?"); params.append(illustrator)
    if rgroup:
        werte = []
        for g in rgroup.split(","):
            werte += RARITY_GROUPS.get(g, {}).get("werte", [])
        if werte:
            where.append("rarity IN (%s)" % ",".join("?" * len(werte))); params += werte
    if trainer_type:
        where.append("trainer_type = ?"); params.append(trainer_type)
    if regmark:
        marks = [m for m in regmark.split(",") if m]
        where.append("regulation_mark IN (%s)" % ",".join("?" * len(marks))); params += marks
    if first:
        where.append("has_first = 1")
    if jahr_von:
        where.append("release_date >= ?"); params.append(f"{int(jahr_von)}-01-01")
    if jahr_bis:
        where.append("release_date <= ?"); params.append(f"{int(jahr_bis)}-12-31")
    if preset in PRESETS or familie:
        con = get_db()
        if familie:
            dexe = _familie_dex(con, [int(familie)])
        else:
            pr = PRESETS[preset]
            dexe = _familie_dex(con, pr["dex"]) if pr["familie"] else pr["dex"]
        con.close()
        frag, dp = _dex_frag(dexe)
        where.append(frag); params += dp
    if q:
        where.append("(name_de LIKE ? OR name_en LIKE ? OR name_ja LIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if serie == "jp":
        region = "jp"; serie = ""
    if serie.startswith("jp:"):
        region = "jp"
        where.append("set_id IN (SELECT id FROM sets WHERE serie_id = ? AND region = 'jp')"); params.append(serie[3:])
        serie = ""
    if region in ("intl", "jp"):
        where.append("COALESCE(cards.region,'intl') = ?")
        params.append(region)
    if set_id:
        where.append("set_id = ?")
        params.append(set_id)
    if serie:
        if serie in AERA_ORDNUNG:
            frag, aera_params = _aera_sql(serie)
            where.append(frag)
            params += aera_params
        else:  # Rückfall: alte Links mit TCGdex-Serien-ID
            where.append("set_id IN (SELECT id FROM sets WHERE serie_id = ?)")
            params.append(serie)
    if typ:
        where.append("types LIKE ?")
        params.append(f'%"{typ}"%')
    if rarity:
        where.append("rarity = ?")
        params.append(rarity)
    if dex:
        where.append("dex_ids LIKE ?")
        params.append(f"%{int(dex)}%")
        # LIKE %25% träfe auch 251 — exakt nachprüfen über JSON-Ränder
        where[-1] = "(dex_ids LIKE ? OR dex_ids LIKE ? OR dex_ids LIKE ? OR dex_ids = ?)"
        params[-1:] = [f"[{int(dex)},%", f"%, {int(dex)},%", f"%, {int(dex)}]", f"[{int(dex)}]"]
    if kind:
        kinds = [k for k in kind.split(",") if k]
        if kinds:
            where.append("(" + " OR ".join("kinds LIKE ?" for _ in kinds) + ")")
            params += [f'%"{k}"%' for k in kinds]
    sql_where = (" WHERE " + " AND ".join(where)) if where else ""
    order = SORTS.get(sort, SORTS["datum"])
    if region == "jp" and sort == "datum" and richtung == "asc":
        richtung = "desc"   # Japan: neueste zuerst – die 1996er-Sets haben kaum Scans
    if richtung == "desc":
        order = ", ".join(
            part.strip() + " DESC" if "IS NULL" not in part else part.strip()
            for part in order.split(",")
        )
    # Karten ohne Bild immer ans Ende – ein Raster voller Text-Platzhalter wirkt kaputt
    order = "(image_de IS NULL AND image_en IS NULL AND image_alt IS NULL), " + order
    return sql_where, params, order


def _card_brief(row):
    keys = row.keys()
    name_ja = row["name_ja"] if "name_ja" in keys else None
    region = (row["region"] if "region" in keys else None) or "intl"
    return {
        "id": row["id"],
        "name": (row["name_de"] or row["name_en"] or name_ja) if region != "jp" else (f"{name_ja} · {row['name_de']}" if row["name_de"] else name_ja),
        "name_en": (row["name_en"] or row["name_de"] or name_ja) if region != "jp" else (f"{name_ja} · {row['name_en']}" if row["name_en"] else name_ja),
        "region": region,
        # Welche Sprache das Bild hat: alte WotC-Sets haben bei TCGdex keine deutschen Scans
        "img_lang": "de" if row["image_de"] else ("en" if row["image_en"] else ("alt" if ("image_alt" in keys and row["image_alt"]) else None)),
        "holo": bool(row["has_holo"]), "first": bool(row["has_first"]) if "has_first" in keys else False,
        "illustrator": row["illustrator"] if "illustrator" in keys else None,
        "regmark": row["regulation_mark"] if "regulation_mark" in keys else None,
        "set_id": row["set_id"],
        "set_name": SET_NAME_FIX_DE.get(row["set_id"], row["set_name"]) or row["set_name_en"],
        "set_name_en": row["set_name_en"] or row["set_name"],
        "local_id": row["local_id"],
        "rarity": row["rarity"],
        "kinds": json.loads(row["kinds"] or "[]"),
        "types": json.loads(row["types"] or "[]"),
        "dex": row["first_dex"],
        "datum": row["release_date"],
        "reverse": bool(row["has_reverse"]),
        "img": bool(row["image_de"] or row["image_en"] or ("image_alt" in keys and row["image_alt"])),
    }


_CARD_SELECT = (
    "SELECT cards.*, (SELECT name FROM sets WHERE sets.id = cards.set_id) set_name,"
    " (SELECT name_en FROM sets WHERE sets.id = cards.set_id) set_name_en FROM cards"
)


@app.get("/api/cards")
def cards(q: str = "", set_id: str = "", serie: str = "", typ: str = "",
          kind: str = "", rarity: str = "", dex: int = 0,
          sort: str = "datum", richtung: str = "asc",
          limit: int = 60, offset: int = 0, region: str = "intl",
          illustrator: str = "", rgroup: str = "", trainer_type: str = "", regmark: str = "", first: int = 0,
          jahr_von: int = 0, jahr_bis: int = 0, preset: str = "", familie: int = 0):
    limit = max(1, min(limit, 300))
    sql_where, params, order = _card_query(q, set_id, serie, typ, kind, sort, richtung, rarity, dex, region,
                                           illustrator, rgroup, trainer_type, regmark, first, jahr_von, jahr_bis, preset, familie)
    con = get_db()
    total = con.execute(f"SELECT COUNT(*) c FROM cards{sql_where}", params).fetchone()["c"]
    rows = con.execute(
        f"{_CARD_SELECT}{sql_where} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    con.close()
    return {"total": total, "karten": [_card_brief(r) for r in rows]}


@app.get("/api/cards/ids")
def card_ids(q: str = "", set_id: str = "", serie: str = "", typ: str = "",
             kind: str = "", rarity: str = "", dex: int = 0,
             sort: str = "datum", richtung: str = "asc",
             limit: int = 1000, region: str = "intl",
             illustrator: str = "", rgroup: str = "", trainer_type: str = "", regmark: str = "", first: int = 0,
             jahr_von: int = 0, jahr_bis: int = 0, preset: str = "", familie: int = 0):
    limit = max(1, min(limit, 2000))
    sql_where, params, order = _card_query(q, set_id, serie, typ, kind, sort, richtung, rarity, dex, region,
                                           illustrator, rgroup, trainer_type, regmark, first, jahr_von, jahr_bis, preset, familie)
    con = get_db()
    rows = con.execute(
        f"SELECT id FROM cards{sql_where} ORDER BY {order} LIMIT ?",
        params + [limit],
    ).fetchall()
    con.close()
    return {"ids": [r["id"] for r in rows]}


@app.post("/api/cards/nach_ids")
async def cards_nach_ids(request: Request):
    """Kartendaten in gegebener Reihenfolge — für Planer-Sortierung und Preise."""
    data = await request.json()
    ids = [str(i) for i in (data.get("ids") or [])][:5000]
    con = get_db()
    by_id = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        for r in con.execute(
            f"{_CARD_SELECT} WHERE cards.id IN ({','.join('?' * len(chunk))})", chunk
        ):
            by_id[r["id"]] = _card_brief(r)
    con.close()
    return {"karten": by_id}


@app.get("/api/sets/{set_id}/cards")
def set_cards(set_id: str):
    con = get_db()
    rows = con.execute(
        f"{_CARD_SELECT} WHERE set_id = ? ORDER BY local_num, local_id",
        (set_id,),
    ).fetchall()
    con.close()
    if not rows:
        raise HTTPException(404, "Set unbekannt oder noch nicht synchronisiert")
    return {"karten": [_card_brief(r) for r in rows]}


@app.get("/api/cards/{card_id}/detail")
def card_detail(card_id: str):
    """Alles für das Detail-Panel: Karte, Set, Preise (normal/holo), Verlauf, andere Drucke desselben Pokémon."""
    con = get_db()
    r = con.execute(f"{_CARD_SELECT} WHERE cards.id = ?", (card_id,)).fetchone()
    if not r:
        con.close()
        raise HTTPException(404, "Karte unbekannt")
    k = _card_brief(r)
    k["category"] = r["category"]; k["stage"] = r["stage"]; k["suffix"] = r["suffix"]
    k["hp"] = r["hp"] if "hp" in r.keys() else None
    k["evolve_from"] = r["evolve_from"] if "evolve_from" in r.keys() else None
    k["trainer_type"] = r["trainer_type"] if "trainer_type" in r.keys() else None
    if r["first_dex"]:
        fam = _familie_dex(con, [r["first_dex"]])
        k["familie"] = [{"dex": d, "name": n} for d, n in con.execute(
            "SELECT dex_id, name_de FROM pokemon WHERE dex_id IN (%s) ORDER BY evo_stufe, dex_id" % ",".join("?" * len(fam)), fam)] if len(fam) > 1 else []
    k["reverse"] = bool(r["has_reverse"]); k["normal"] = bool(r["has_normal"])
    sr = con.execute("SELECT id, name, name_en, release_date, total, official, serie_id, region FROM sets WHERE id = ?", (r["set_id"],)).fetchone()
    k["set"] = dict(sr) if sr else None
    if k["set"]:
        k["set"]["name"] = SET_NAME_FIX_DE.get(k["set"]["id"], k["set"]["name"]) or k["set"]["name_en"]
    pr = con.execute("SELECT eur, eur_holo, updated_at FROM card_prices WHERE card_id = ?", (card_id,)).fetchone()
    k["preis"] = {"eur": pr["eur"], "eur_holo": pr["eur_holo"], "stand": pr["updated_at"]} if pr else None
    k["verlauf"] = [{"datum": h["datum"], "eur": h["eur"]} for h in con.execute(
        "SELECT datum, eur FROM price_history WHERE card_id = ? ORDER BY datum", (card_id,))]
    andere = []
    if r["first_dex"]:
        for a in con.execute(
            f"{_CARD_SELECT} WHERE first_dex = ? AND cards.id != ? AND COALESCE(cards.region,'intl') = ?"
            " ORDER BY release_date DESC LIMIT 24", (r["first_dex"], card_id, k["region"])):
            andere.append(_card_brief(a))
        k["andere_gesamt"] = con.execute("SELECT COUNT(*) c FROM cards WHERE first_dex = ?", (r["first_dex"],)).fetchone()["c"]
    k["andere"] = andere
    con.close()
    return k


# --- Import (Listen aus anderen Tools) ---------------------------------------
# Versteht je Zeile: „sv1 25“, „SV1-025“, „4/102 Charizard“, „Charizard 4/102“,
# „1x Glurak (Base Set) 4“, TCG-Collector/Collectr-CSV (Name;Set;Nummer …) und
# Cardmarket-Wants („1 Charizard (Base Set)“). Ergebnis: Treffer + unklare Zeilen.

_SET_CODES = None


def _set_codes():
    """Set-ID ↔ gängige Kürzel/Namen (klein, ohne Sonderzeichen)."""
    global _SET_CODES
    if _SET_CODES is not None:
        return _SET_CODES
    con = get_db()
    codes = {}
    # japanische Sets zuerst, internationale überschreiben: „sv1“ meint das internationale Set, nicht SV1 (JP)
    for r in con.execute("SELECT id, name, name_en, region FROM sets ORDER BY CASE WHEN region='jp' THEN 0 ELSE 1 END"):
        for key in (r["id"], r["name"], r["name_en"], SET_NAME_FIX_DE.get(r["id"])):
            if key:
                codes[re.sub(r"[^a-z0-9]", "", key.lower())] = r["id"]
    con.close()
    _SET_CODES = codes
    return codes


def _import_zeile(zeile, con):
    z = zeile.strip()
    if not z or z.lower().startswith(("name;", "name,", "card name", "quantity", "menge")):
        return None
    z = re.sub(r"^\s*\d+\s*[x×]\s*", "", z)          # „2x “ vorne weg
    z = re.sub(r"^\s*\d+\s+(?=[A-Za-zÄÖÜäöü])", "", z)  # „1 Charizard …“
    codes = _set_codes()
    sep = ";" if z.count(";") >= 2 else ("," if z.count(",") >= 2 else ("\t" if "\t" in z else None))
    name = setname = nummer = None
    total = None   # „4/102“: die Set-Größe grenzt das Set ein, wenn kein Setname dabeisteht
    mt = re.search(r"\b(\d{1,3})\s*/\s*(\d{2,3})\b", z)
    if mt:
        total = int(mt.group(2))
    if sep:
        teile = [p.strip().strip('"') for p in z.split(sep)]
        name = teile[0]
        for p in teile[1:]:
            if re.fullmatch(r"[A-Za-z]{0,4}\d{1,3}[a-z]?(/\d+)?", p) and nummer is None:
                nummer = p.split("/")[0]
            elif p and setname is None and not re.fullmatch(r"[\d.,€$ ]+", p):
                setname = p
    else:
        m = re.match(r"^([a-z0-9]{2,8})[-\s](\d{1,3}[a-z]?)$", z, re.I)     # sv1-025 / sv1 25
        if m:
            setname, nummer = m.group(1), m.group(2)
        else:
            m = re.match(r"^(.*?)\s*\((.+?)\)\s*(\d{1,3})?(?:/\d+)?\s*$", z)  # Name (Set) 4
            if m:
                name, setname, nummer = m.group(1).strip(), m.group(2).strip(), m.group(3)
            else:
                m = re.match(r"^(?:(\d{1,3})/\d+\s+)?(.*?)(?:\s+(\d{1,3})/\d+)?$", z)  # 4/102 Name | Name 4/102
                if m:
                    nummer = m.group(1) or m.group(3)
                    name = m.group(2).strip()
    set_id = codes.get(re.sub(r"[^a-z0-9]", "", (setname or "").lower())) if setname else None
    where, params = [], []
    if set_id:
        where.append("set_id = ?"); params.append(set_id)
    else:
        where.append("COALESCE(cards.region,'intl') = 'intl'")
        if total:
            where.append("set_id IN (SELECT id FROM sets WHERE official = ? OR total = ?)"); params += [total, total]
    if nummer:
        where.append("local_num = ?"); params.append(_local_num(nummer))
    if name:
        where.append("(name_de LIKE ? OR name_en LIKE ?)"); params += [f"%{name}%", f"%{name}%"]
    if not where:
        return {"zeile": zeile, "id": None}
    rows = con.execute(f"{_CARD_SELECT} WHERE {' AND '.join(where)} ORDER BY release_date DESC LIMIT 3", params).fetchall()
    if not rows and name and set_id:   # Name passt nicht zur Nummer → Nummer + Set reicht
        rows = con.execute(f"{_CARD_SELECT} WHERE set_id = ? AND local_num = ? LIMIT 1", (set_id, _local_num(nummer or ""))).fetchall()
    if not rows:
        return {"zeile": zeile, "id": None}
    k = _card_brief(rows[0])
    return {"zeile": zeile, "id": k["id"], "name": k["name"], "set_name": k["set_name"], "local_id": k["local_id"],
            "sicher": bool(set_id and nummer) or len(rows) == 1}


@app.post("/api/import/parse")
async def import_parse(request: Request):
    data = await request.json()
    text = str(data.get("text") or "")[:200000]
    con = get_db()
    treffer, unklar = [], []
    for zeile in text.splitlines()[:2000]:
        e = _import_zeile(zeile, con)
        if e is None:
            continue
        (treffer if e["id"] else unklar).append(e)
    con.close()
    return {"treffer": treffer, "unklar": [u["zeile"] for u in unklar]}


@app.get("/api/pokedex")
def pokedex(gens: str = ""):
    wanted = {int(g) for g in gens.split(",") if g.strip().isdigit()} if gens else None
    con = get_db()
    rows = con.execute("SELECT * FROM pokemon ORDER BY dex_id").fetchall()
    con.close()
    result = [
        {"dex": r["dex_id"], "name": r["name_de"], "name_en": r["name_en"], "gen": r["gen"]}
        for r in rows
        if wanted is None or r["gen"] in wanted
    ]
    return {"pokemon": result}


# --- Konten, Limits & Abos --------------------------------------------------
#
# Free-Konto: 2 gespeicherte Binder, 1 Karten-PDF pro Monat, Preis-Abruf 1x/Tag.
# Pro/Lifetime: alles unbegrenzt + Kaufliste. Checklisten-PDF (ohne Kartenbilder)
# zählt bewusst nicht als Export. Anonyme Binder (user_id NULL) bleiben frei
# planbar — das Gate sitzt am PDF-Export.

import hashlib  # noqa: E402

FREE_BINDER_LIMIT = 2
FREE_EXPORT_LIMIT = 1

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _env():
    text = (BASE / ".env").read_text() if (BASE / ".env").exists() else ""
    out = {}
    for line in text.splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _hash_pw(pw: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 120_000).hex()


def _current_user(request: Request):
    token = ""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        token = request.cookies.get("bp_token", "")
    if not token:
        return None
    con = get_db()
    row = con.execute(
        "SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id WHERE sessions.token = ?",
        (token,),
    ).fetchone()
    con.close()
    return dict(row) if row else None


def _require_user(request: Request):
    user = _current_user(request)
    if not user:
        raise HTTPException(401, detail={"code": "login"})
    return user


def _ist_pro(user) -> bool:
    return (user or {}).get("plan") in ("pro", "lifetime")


def _monat_key():
    from datetime import datetime as _dt
    return _dt.utcnow().strftime("%Y-%m")


def _heute():
    from datetime import datetime as _dt
    return _dt.utcnow().strftime("%Y-%m-%d")


def _exporte_benutzt(user) -> int:
    teil = (user.get("exports_monat") or "").split(":")
    return int(teil[1]) if len(teil) == 2 and teil[0] == _monat_key() else 0


def _user_info(user):
    con = get_db()
    anzahl = con.execute("SELECT COUNT(*) c FROM binders WHERE user_id = ?", (user["id"],)).fetchone()["c"]
    con.close()
    pro = _ist_pro(user)
    return {
        "email": user["email"], "plan": user["plan"], "name": user.get("name") or "",
        "binder_anzahl": anzahl, "binder_limit": None if pro else FREE_BINDER_LIMIT,
        "exporte_benutzt": _exporte_benutzt(user),
        "exporte_limit": None if pro else FREE_EXPORT_LIMIT,
        "stripe": bool(_env().get("STRIPE_SECRET_KEY")),
    }


def _neue_session(con, user_id) -> str:
    token = secrets.token_urlsafe(32)
    con.execute("INSERT INTO sessions (token, user_id) VALUES (?,?)", (token, user_id))
    return token


# --- E-Mail-Versand (SMTP aus .env: SMTP_HOST/PORT/USER/PASS/FROM) ----------

def _mail_senden(an: str, betreff: str, text: str) -> bool:
    import smtplib
    from email.message import EmailMessage
    env = _env()
    host = env.get("SMTP_HOST")
    user = env.get("SMTP_USER")
    if not host or not user:
        return False
    msg = EmailMessage()
    msg["Subject"] = betreff
    msg["From"] = f'{env.get("SMTP_FROM_NAME", "Binderplan")} <{env.get("SMTP_FROM", user)}>'
    msg["To"] = an
    msg.set_content(text)
    try:
        port = int(env.get("SMTP_PORT", "587") or 587)
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20) as s:
                s.login(user, env.get("SMTP_PASS", ""))
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.ehlo()
                s.starttls()
                s.login(user, env.get("SMTP_PASS", ""))
                s.send_message(msg)
        return True
    except Exception:
        return False


def _mail_konfiguriert() -> bool:
    env = _env()
    return bool(env.get("SMTP_HOST") and env.get("SMTP_USER"))


@app.post("/api/auth/passwort_vergessen")
async def passwort_vergessen(request: Request):
    """Reset-Link mailen. Antwortet immer gleich, verrät also nicht, ob die E-Mail existiert."""
    if not _mail_konfiguriert():
        raise HTTPException(503, detail={"code": "mail"})
    data = await request.json()
    email = str(data.get("email") or "").strip().lower()
    con = get_db()
    row = con.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if row:
        token = secrets.token_urlsafe(24)
        con.execute(
            "UPDATE users SET reset_token = ?, reset_bis = datetime('now', '+2 hours') WHERE id = ?",
            (token, row["id"]),
        )
        con.commit()
        app_url = _env().get("APP_URL", "https://binderplan.app")
        _mail_senden(
            email, "Binderplan – Passwort zurücksetzen",
            "Hallo,\n\n"
            "für dein Binderplan-Konto wurde ein neues Passwort angefordert. "
            "Klicke auf diesen Link (2 Stunden gültig):\n\n"
            f"{app_url}/?reset={token}\n\n"
            "Wenn du das nicht warst, kannst du diese E-Mail einfach ignorieren.\n\n"
            "Viele Grüße\nBinderplan",
        )
    con.close()
    return {"ok": True}


@app.post("/api/auth/passwort_neu")
async def passwort_neu(request: Request):
    data = await request.json()
    token = str(data.get("token") or "")
    pw = str(data.get("passwort") or "")
    if len(pw) < 8:
        raise HTTPException(400, "Das Passwort braucht mindestens 8 Zeichen.")
    con = get_db()
    row = con.execute(
        "SELECT id FROM users WHERE reset_token = ? AND reset_token != ''"
        " AND reset_bis > datetime('now')", (token,),
    ).fetchone()
    if not row:
        con.close()
        raise HTTPException(400, "Der Link ist ungültig oder abgelaufen — bitte neu anfordern.")
    salt = secrets.token_hex(16)
    con.execute(
        "UPDATE users SET pw_hash = ?, salt = ?, reset_token = '', reset_bis = '' WHERE id = ?",
        (_hash_pw(pw, salt), salt, row["id"]),
    )
    con.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
    session = _neue_session(con, row["id"])
    con.commit()
    user = dict(con.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone())
    con.close()
    return {"token": session, "user": _user_info(user)}


@app.post("/api/auth/register")
async def auth_register(request: Request):
    data = await request.json()
    email = str(data.get("email") or "").strip().lower()
    pw = str(data.get("passwort") or "")
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, "Bitte eine gültige E-Mail-Adresse angeben.")
    if len(pw) < 8:
        raise HTTPException(400, "Das Passwort braucht mindestens 8 Zeichen.")
    salt = secrets.token_hex(16)
    con = get_db()
    try:
        cur = con.execute(
            "INSERT INTO users (email, pw_hash, salt) VALUES (?,?,?)",
            (email, _hash_pw(pw, salt), salt),
        )
    except sqlite3.IntegrityError:
        con.close()
        raise HTTPException(400, "Für diese E-Mail gibt es schon ein Konto — bitte anmelden.")
    token = _neue_session(con, cur.lastrowid)
    con.commit()
    user = dict(con.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone())
    con.close()
    return {"token": token, "user": _user_info(user)}


@app.post("/api/auth/login")
async def auth_login(request: Request):
    data = await request.json()
    email = str(data.get("email") or "").strip().lower()
    pw = str(data.get("passwort") or "")
    con = get_db()
    row = con.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not row or _hash_pw(pw, row["salt"]) != row["pw_hash"]:
        con.close()
        raise HTTPException(401, "E-Mail oder Passwort stimmt nicht.")
    token = _neue_session(con, row["id"])
    con.commit()
    con.close()
    return {"token": token, "user": _user_info(dict(row))}


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    token = request.headers.get("authorization", "")[7:].strip() or request.cookies.get("bp_token", "")
    if token:
        con = get_db()
        con.execute("DELETE FROM sessions WHERE token = ?", (token,))
        con.commit()
        con.close()
    return {"ok": True}


@app.post("/api/auth/passwort_aendern")
async def passwort_aendern(request: Request):
    """Passwort im Profil ändern; meldet alle anderen Sitzungen ab."""
    user = _require_user(request)
    data = await request.json()
    alt = str(data.get("alt") or "")
    neu = str(data.get("neu") or "")
    if len(neu) < 8:
        raise HTTPException(400, "Das neue Passwort braucht mindestens 8 Zeichen.")
    if _hash_pw(alt, user["salt"]) != user["pw_hash"]:
        raise HTTPException(400, "Das aktuelle Passwort stimmt nicht.")
    salt = secrets.token_hex(16)
    con = get_db()
    con.execute(
        "UPDATE users SET pw_hash = ?, salt = ? WHERE id = ?",
        (_hash_pw(neu, salt), salt, user["id"]),
    )
    con.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
    token = _neue_session(con, user["id"])
    con.commit()
    con.close()
    return {"ok": True, "token": token}


@app.post("/api/auth/konto_loeschen")
async def konto_loeschen(request: Request):
    """Konto samt aller Binder und Sitzungen endgültig löschen (DSGVO)."""
    user = _require_user(request)
    data = await request.json()
    pw = str(data.get("passwort") or "")
    if _hash_pw(pw, user["salt"]) != user["pw_hash"]:
        raise HTTPException(400, "Das Passwort stimmt nicht.")
    if user.get("plan") == "pro" and user.get("stripe_sub"):
        raise HTTPException(400, "Bitte zuerst das Abo über „Abo verwalten“ kündigen, dann das Konto löschen.")
    con = get_db()
    con.execute("DELETE FROM binders WHERE user_id = ?", (user["id"],))
    con.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
    con.execute("DELETE FROM users WHERE id = ?", (user["id"],))
    con.commit()
    con.close()
    return {"ok": True}


@app.post("/api/auth/profil")
async def profil_aendern(request: Request):
    """Anzeigename (statt E-Mail-Präfix in Begrüßung und Kopfzeile)."""
    user = _require_user(request)
    data = await request.json()
    name = str(data.get("name") or "").strip()[:40]
    con = get_db()
    con.execute("UPDATE users SET name = ? WHERE id = ?", (name, user["id"]))
    con.commit()
    user = dict(con.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone())
    con.close()
    return {"user": _user_info(user)}


@app.get("/api/auth/me")
def auth_me(request: Request):
    user = _current_user(request)
    return {"user": _user_info(user) if user else None}


@app.post("/api/auth/claim")
async def auth_claim(request: Request):
    """Anonyme Binder aus dem Browser dem frisch angemeldeten Konto zuordnen."""
    user = _require_user(request)
    data = await request.json()
    ids = [str(i) for i in (data.get("ids") or [])][:50]
    if not ids:
        return {"uebernommen": 0}
    con = get_db()
    cur = con.execute(
        "UPDATE binders SET user_id = ? WHERE user_id IS NULL AND id IN (%s)"
        % ",".join("?" * len(ids)),
        [user["id"]] + ids,
    )
    con.commit()
    con.close()
    return {"uebernommen": cur.rowcount}


# --- Stripe (Checkout, Portal, Webhook) -------------------------------------

TARIFE = {"monat": "STRIPE_PRICE_MONAT", "jahr": "STRIPE_PRICE_JAHR", "lifetime": "STRIPE_PRICE_LIFETIME"}


def _stripe():
    env = _env()
    key = env.get("STRIPE_SECRET_KEY")
    if not key:
        raise HTTPException(503, "Zahlungen sind noch nicht eingerichtet.")
    import stripe as stripe_lib
    stripe_lib.api_key = key
    return stripe_lib, env


@app.post("/api/stripe/checkout")
async def stripe_checkout(request: Request):
    user = _require_user(request)
    data = await request.json()
    tarif = data.get("tarif")
    if tarif not in TARIFE:
        raise HTTPException(400, "Unbekannter Tarif.")
    stripe_lib, env = _stripe()
    price = env.get(TARIFE[tarif])
    if not price:
        raise HTTPException(503, "Zahlungen sind noch nicht eingerichtet.")
    app_url = env.get("APP_URL", "https://agi-empire.com/binderplan")
    kunde = user.get("stripe_customer")
    if not kunde:
        kunde = stripe_lib.Customer.create(email=user["email"], metadata={"binderplan_user": user["id"]}).id
        con = get_db()
        con.execute("UPDATE users SET stripe_customer = ? WHERE id = ?", (kunde, user["id"]))
        con.commit()
        con.close()
    extra = {}
    if tarif == "lifetime":
        # Einmalzahlung: Verwendungszweck auf dem Kontoauszug explizit setzen
        extra["payment_intent_data"] = {"statement_descriptor": "BINDERPLAN"}
    session = stripe_lib.checkout.Session.create(
        customer=kunde,
        mode="payment" if tarif == "lifetime" else "subscription",
        line_items=[{"price": price, "quantity": 1}],
        success_url=f"{app_url}/?zahlung=ok",
        cancel_url=f"{app_url}/?zahlung=abbruch",
        client_reference_id=str(user["id"]),
        allow_promotion_codes=True,
        **extra,
    )
    return {"url": session.url}


@app.post("/api/stripe/portal")
def stripe_portal(request: Request):
    user = _require_user(request)
    if not user.get("stripe_customer"):
        raise HTTPException(400, "Kein Zahlungskonto vorhanden.")
    stripe_lib, env = _stripe()
    session = stripe_lib.billing_portal.Session.create(
        customer=user["stripe_customer"],
        return_url=env.get("APP_URL", "https://agi-empire.com/binderplan") + "/",
    )
    return {"url": session.url}


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    env = _env()
    secret = env.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(503, "Webhook nicht konfiguriert.")
    import stripe as stripe_lib
    try:
        event = stripe_lib.Webhook.construct_event(payload, sig, secret)
    except Exception:
        raise HTTPException(400, "Ungültige Signatur.")
    typ = event["type"]
    obj = event["data"]["object"]
    con = get_db()
    if typ == "checkout.session.completed":
        user_id = obj.get("client_reference_id")
        if user_id:
            if obj.get("mode") == "payment":
                con.execute("UPDATE users SET plan = 'lifetime' WHERE id = ?", (user_id,))
            else:
                con.execute("UPDATE users SET plan = 'pro', stripe_sub = ? WHERE id = ?",
                            (obj.get("subscription"), user_id))
            # Kaufbestätigung (best effort — Zahlung ist unabhängig davon gültig)
            try:
                zeile = con.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
                if zeile and _mail_konfiguriert():
                    lifetime = obj.get("mode") == "payment"
                    _mail_senden(
                        zeile["email"], "Binderplan – Kaufbestätigung",
                        "Hallo,\n\n"
                        "danke für deinen Kauf! Dein Konto wurde soeben freigeschaltet:\n\n"
                        + ("• Binderplan Lifetime — einmalig bezahlt, dauerhaft alle Pro-Funktionen\n"
                           if lifetime else
                           "• Binderplan Pro — dein Abo ist aktiv und jederzeit über \"Konto → Abo verwalten\" kündbar\n")
                        + "\nAb sofort: unbegrenzte Binder & Exporte, tagesaktuelle Preise und die Kaufliste."
                        "\nDie Rechnung bekommst du separat von unserem Zahlungsdienstleister Stripe."
                        "\n\nViel Spaß beim Sammeln!\nBinderplan – https://binderplan.app"
                        "\n\nImpressum & AGB: https://binderplan.app/recht",
                    )
            except Exception:
                pass
    elif typ == "customer.subscription.deleted":
        con.execute(
            "UPDATE users SET plan = 'free', stripe_sub = NULL WHERE stripe_sub = ? AND plan != 'lifetime'",
            (obj.get("id"),),
        )
    elif typ == "customer.subscription.updated":
        status = obj.get("status")
        if status in ("canceled", "unpaid", "incomplete_expired"):
            con.execute(
                "UPDATE users SET plan = 'free' WHERE stripe_sub = ? AND plan != 'lifetime'",
                (obj.get("id"),),
            )
        elif status == "active":
            con.execute("UPDATE users SET plan = 'pro' WHERE stripe_sub = ?", (obj.get("id"),))
    con.commit()
    con.close()
    return {"ok": True}


# --- Kartenpreise (Cardmarket-Trend via TCGdex, 24h-Cache) ------------------

def _fetch_price(client, card_id):
    """→ (card_id, eur, eur_holo). Japanische IDs (Großbuchstaben) laufen über den ja-Pfad."""
    lang = "ja" if card_id[:1].isupper() else "en"
    try:
        d = client.get(f"{TCGDEX}/{lang}/cards/{card_id}").json()
        cm = (d.get("pricing") or {}).get("cardmarket") or {}
        eur = holo = None
        for key in ("trend", "avg30", "avg", "low"):
            if cm.get(key) is not None:
                eur = round(float(cm[key]), 2); break
        for key in ("trend-holo", "avg30-holo", "avg-holo", "low-holo"):
            if cm.get(key):
                holo = round(float(cm[key]), 2); break
        return card_id, eur, holo
    except Exception:
        pass
    return card_id, None, None


@app.post("/api/preise")
async def preise(request: Request):
    """EUR-Preise (Cardmarket-Trend) für Karten-IDs; fehlende werden nachgeladen.

    Preise sind der Kern-Nutzen und deshalb auch ohne Konto sichtbar: Gäste bekommen
    den Cache plus bis zu 120 frische Preise je Anfrage (globales Tagesbudget),
    Free-Konten aktualisieren 1x pro Tag, Pro sofort und unbegrenzt."""
    user = _current_user(request)
    data = await request.json()
    ids = list(dict.fromkeys(str(i) for i in (data.get("ids") or [])))[:1500]
    frei_gedrosselt = bool(user) and not _ist_pro(user) and user.get("preise_tag") == _heute()
    con = get_db()
    result, holo, fehlt = {}, {}, []
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        rows = con.execute(
            "SELECT card_id, eur, eur_holo, updated_at FROM card_prices WHERE card_id IN (%s)"
            % ",".join("?" * len(chunk)), chunk).fetchall()
        alle = {r["card_id"]: r for r in rows}
        for cid in chunk:
            r = alle.get(cid)
            if r and (r["updated_at"] or "") >= datetime_str_vor(24):
                result[cid] = r["eur"]; holo[cid] = r["eur_holo"]
            else:
                if r:   # alter Preis ist besser als keiner, bis der frische da ist
                    result[cid] = r["eur"]; holo[cid] = r["eur_holo"]
                fehlt.append(cid)
    if not user:
        tag = con.execute("SELECT value FROM kv WHERE key='gast_preise'").fetchone()
        heute, zaehler = (tag["value"].split(":") + ["0"])[:2] if tag and ":" in tag["value"] else (_heute(), "0")
        budget = 2500 - (int(zaehler) if heute == _heute() else 0)
        nachgeladen = fehlt[:max(0, min(120, budget))]
    else:
        nachgeladen = [] if frei_gedrosselt else fehlt[:400]
    if nachgeladen:
        with httpx.Client(timeout=20, headers=UA) as client:
            with ThreadPoolExecutor(8) as pool:
                for cid, eur, eur_holo in pool.map(lambda c: _fetch_price(client, c), nachgeladen):
                    result[cid] = eur; holo[cid] = eur_holo
                    con.execute(
                        "INSERT OR REPLACE INTO card_prices (card_id, eur, eur_holo, updated_at)"
                        " VALUES (?,?,?,datetime('now'))", (cid, eur, eur_holo))
                    if eur is not None:
                        con.execute(
                            "INSERT OR REPLACE INTO price_history (card_id, datum, eur) VALUES (?,?,?)",
                            (cid, _heute(), eur))
        if user:
            con.execute("UPDATE users SET preise_tag = ? WHERE id = ?", (_heute(), user["id"]))
        else:
            tag = con.execute("SELECT value FROM kv WHERE key='gast_preise'").fetchone()
            heute, zaehler = (tag["value"].split(":") + ["0"])[:2] if tag and ":" in tag["value"] else (_heute(), "0")
            neu = (int(zaehler) if heute == _heute() else 0) + len(nachgeladen)
            con.execute("INSERT OR REPLACE INTO kv (key,value) VALUES ('gast_preise', ?)", (f"{_heute()}:{neu}",))
        con.commit()
    con.close()
    return {"preise": result, "holo": holo, "offen": max(0, len(fehlt) - len(nachgeladen)),
            "gedrosselt": frei_gedrosselt}


def datetime_str_vor(stunden):
    from datetime import datetime, timedelta
    return (datetime.utcnow() - timedelta(hours=stunden)).strftime("%Y-%m-%d %H:%M:%S")


# --- Bild-Cache -------------------------------------------------------------

def _fetch_asset(urls, target: Path):
    for url in urls:
        if not url:
            continue
        try:
            r = httpx.get(url, timeout=30, headers=UA, follow_redirects=True)
            if r.status_code == 200 and r.content:
                target.write_bytes(r.content)
                return True
        except Exception:
            continue
    return False


IMG_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}


def _alt_urls(image_alt, variante):
    """pokemontcg.io-Fallback: gespeichert ist die small-URL (…/2.png)."""
    if not image_alt:
        return []
    if variante == "high" and image_alt.endswith(".png"):
        return [image_alt[:-4] + "_hires.png", image_alt]
    return [image_alt]


@app.get("/api/img/card/{card_id}")
def card_image(card_id: str, variante: str = "low", lang: str = "de"):
    variante = "high" if variante == "high" else "low"
    lang = "en" if lang == "en" else "de"
    safe = re.sub(r"[^A-Za-z0-9._%-]", "_", card_id)
    suffix = "" if lang == "de" else ".en"
    target = CACHE / "cards" / variante / f"{safe}{suffix}.webp"
    if not target.exists():
        con = get_db()
        row = con.execute("SELECT image_de, image_en, image_alt FROM cards WHERE id = ?", (card_id,)).fetchone()
        con.close()
        if not row:
            raise HTTPException(404, "Karte unbekannt")
        urls = [
            f"{row['image_de']}/{variante}.webp" if row["image_de"] else None,
            f"{row['image_en']}/{variante}.webp" if row["image_en"] else None,
        ]
        if lang == "en":
            urls.reverse()
        urls += _alt_urls(row["image_alt"], variante)
        if not _fetch_asset(urls, target):
            raise HTTPException(404, "Kein Bild verfügbar")
    media = "image/png" if target.read_bytes()[:4] == b"\x89PNG" else "image/webp"
    return FileResponse(target, media_type=media, headers=IMG_HEADERS)


@app.get("/api/img/dex/{dex_id}")
def dex_image(dex_id: int):
    target = CACHE / "dex" / f"{dex_id}.png"
    if not target.exists():
        urls = [
            f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/{dex_id}.png",
            f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{dex_id}.png",
        ]
        if not _fetch_asset(urls, target):
            raise HTTPException(404, "Kein Bild verfügbar")
    return FileResponse(target, media_type="image/png", headers=IMG_HEADERS)


@app.get("/api/img/set/{set_id}")
def set_symbol_image(set_id: str):
    """Set-Symbol (TCGdex) für die Set-Filteransicht – gecacht wie die übrigen Bilder."""
    target = CACHE / "sym" / f"{set_id}.png"
    if not target.exists():
        con = get_db()
        row = con.execute("SELECT symbol, symbol_alt FROM sets WHERE id = ?", (set_id,)).fetchone()
        con.close()
        sym = ((row["symbol"] if row else "") or "").strip()
        alt = ((row["symbol_alt"] if row else "") or "").strip()
        if not sym and not alt:
            raise HTTPException(404, "Kein Symbol")
        target.parent.mkdir(parents=True, exist_ok=True)
        # TCGdex-Symbol-URL braucht eine Endung (png bevorzugt, webp als Fallback); sonst pokemontcg.io
        urls = ([sym + ".png", sym + ".webp"] if sym else []) + ([alt] if alt else [])
        if not _fetch_asset(urls, target):
            raise HTTPException(404, "Kein Symbol verfügbar")
    return FileResponse(target, media_type="image/png", headers=IMG_HEADERS)


# --- Binder -----------------------------------------------------------------

# Gängige Binder-Raster: 4er (2×2), 9er (3×3), 12er hoch (3×4) und quer (4×3),
# 16er (4×4), 20er (4×5 bzw. 5×4) und 25er-Jumbo (5×5)
LAYOUTS = {"2x2": 4, "3x3": 9, "3x4": 12, "4x3": 12, "4x4": 16, "4x5": 20, "5x4": 20, "5x5": 25}


def _binder_payload(data):
    layout = data.get("layout") if data.get("layout") in LAYOUTS else "3x3"
    items = data.get("items") or []
    if not isinstance(items, list) or len(items) > 5000:
        raise HTTPException(400, "Ungültige Kartenliste")
    return {
        "name": str(data.get("name") or "Mein Binder")[:80],
        "mode": data.get("mode") if data.get("mode") in ("master", "dex", "custom") else "custom",
        "layout": layout,
        "options": json.dumps(data.get("options") or {}),
        "items": json.dumps(items),
    }


@app.post("/api/binders")
async def binder_create(request: Request):
    data = await request.json()
    p = _binder_payload(data)
    user = _current_user(request)
    if user and not _ist_pro(user):
        con = get_db()
        anzahl = con.execute("SELECT COUNT(*) c FROM binders WHERE user_id = ?", (user["id"],)).fetchone()["c"]
        con.close()
        if anzahl >= FREE_BINDER_LIMIT:
            raise HTTPException(402, detail={"code": "limit_binder"})
    binder_id = secrets.token_urlsafe(8)
    con = get_db()
    con.execute(
        "INSERT INTO binders (id,name,mode,layout,options,items,user_id) VALUES (?,?,?,?,?,?,?)",
        (binder_id, p["name"], p["mode"], p["layout"], p["options"], p["items"],
         user["id"] if user else None),
    )
    con.commit()
    con.close()
    return {"id": binder_id}


def _binder_schreibrecht(binder_id: str, request: Request):
    """Konto-Binder darf nur der Besitzer ändern; anonyme Binder bleiben offen."""
    con = get_db()
    row = con.execute("SELECT user_id FROM binders WHERE id = ?", (binder_id,)).fetchone()
    con.close()
    if not row:
        raise HTTPException(404, "Binder nicht gefunden")
    if row["user_id"] is not None:
        user = _current_user(request)
        if not user or user["id"] != row["user_id"]:
            raise HTTPException(403, detail={"code": "fremder_binder"})


@app.put("/api/binders/{binder_id}")
async def binder_update(binder_id: str, request: Request):
    _binder_schreibrecht(binder_id, request)
    data = await request.json()
    p = _binder_payload(data)
    con = get_db()
    cur = con.execute(
        "UPDATE binders SET name=?, mode=?, layout=?, options=?, items=?,"
        " updated_at=datetime('now') WHERE id=?",
        (p["name"], p["mode"], p["layout"], p["options"], p["items"], binder_id),
    )
    con.commit()
    con.close()
    if cur.rowcount == 0:
        raise HTTPException(404, "Binder nicht gefunden")
    return {"ok": True}


def _load_binder(binder_id):
    con = get_db()
    row = con.execute("SELECT * FROM binders WHERE id = ?", (binder_id,)).fetchone()
    con.close()
    if not row:
        raise HTTPException(404, "Binder nicht gefunden")
    return {
        "id": row["id"], "name": row["name"], "mode": row["mode"],
        "layout": row["layout"], "options": json.loads(row["options"] or "{}"),
        "items": json.loads(row["items"] or "[]"), "updated_at": row["updated_at"],
    }


@app.get("/api/binders/{binder_id}")
def binder_get(binder_id: str):
    return _load_binder(binder_id)


@app.delete("/api/binders/{binder_id}")
def binder_delete(binder_id: str, request: Request):
    _binder_schreibrecht(binder_id, request)
    con = get_db()
    con.execute("DELETE FROM binders WHERE id = ?", (binder_id,))
    con.commit()
    con.close()
    return {"ok": True}


@app.get("/api/binders")
def binder_list(request: Request, ids: str = ""):
    """Konto-Binder (falls angemeldet) plus lokal gemerkte anonyme Binder."""
    wanted = [i for i in ids.split(",") if i][:50]
    user = _current_user(request)
    con = get_db()
    rows = []
    if user:
        rows += con.execute(
            "SELECT id,name,mode,layout,items,updated_at FROM binders WHERE user_id = ?"
            " ORDER BY updated_at DESC", (user["id"],)).fetchall()
    if wanted:
        rows += con.execute(
            "SELECT id,name,mode,layout,items,updated_at FROM binders WHERE user_id IS NULL"
            " AND id IN (%s)" % ",".join("?" * len(wanted)),
            wanted,
        ).fetchall()
    con.close()
    gesehen = set()
    result = []
    reihenfolge = [r["id"] for r in rows if user] + wanted
    by_id = {r["id"]: r for r in rows}
    for bid in reihenfolge:
        r = by_id.get(bid)
        if not r or bid in gesehen:
            continue
        gesehen.add(bid)
        items = json.loads(r["items"] or "[]")
        result.append({
            "id": r["id"], "name": r["name"], "mode": r["mode"], "layout": r["layout"],
            "anzahl": len(items),
            "gesammelt": sum(1 for i in items if i.get("have")),
            "updated_at": r["updated_at"],
            "vorschau": [i.get("id") for i in items if i.get("type") == "card" and i.get("id")][:3],
            "dex_vorschau": [i.get("dex") for i in items if i.get("type") == "dex"][:3],
        })
    return {"binder": result}


# --- PDF-Export -------------------------------------------------------------

from PIL import Image, ImageOps  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.lib.utils import ImageReader  # noqa: E402
from reportlab.pdfgen import canvas as pdfcanvas  # noqa: E402

# Varianten je Fach (Kurzbezeichnung für Druck und Listen)
VARIANT_LABELS = {"reverse": "Reverse Holo", "holo": "Holo", "first": "1st Edition",
                  "pokeball": "Poké Ball", "masterball": "Master Ball"}

CARD_W = 63 * mm
CARD_H = 88 * mm
GUTTER = 4 * mm
COLS, ROWS = 3, 3


PRINT_W = 744   # 63 mm bei 300 dpi


def _print_image_path(card_id, lang="de"):
    """Graustufen-JPEG in Druckauflösung, gecacht. Vorher wurde jedes Bild im PDF-Lauf
    in voller Auflösung konvertiert und verlustfrei eingebettet (26 MB, 34 s für 64 Karten)."""
    safe = re.sub(r"[^A-Za-z0-9._%-]", "_", card_id)
    suffix = "" if lang != "en" else ".en"
    target = CACHE / "cards" / "print" / f"{safe}{suffix}.jpg"
    if target.exists():
        return target
    quelle = _card_image_path(card_id, lang)
    if not quelle:
        return None
    try:
        img = Image.open(quelle)
        if img.mode in ("RGBA", "P", "LA"):
            bg = Image.new("RGB", img.size, "white")
            bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
            img = bg
        gray = ImageOps.autocontrast(img.convert("L"), cutoff=1)
        if gray.width > PRINT_W:
            gray = gray.resize((PRINT_W, int(gray.height * PRINT_W / gray.width)), Image.LANCZOS)
        gray.save(target, "JPEG", quality=84, optimize=True)
        return target
    except Exception:
        return None


def _grayscale_reader(path: Path):
    img = Image.open(path)
    if img.mode in ("RGBA", "P", "LA"):
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
        img = bg
    gray = ImageOps.autocontrast(img.convert("L"), cutoff=1)
    return ImageReader(gray)


def _card_image_path(card_id, lang="de"):
    """Hochauflösendes Kartenbild besorgen (nutzt denselben Cache wie /api/img)."""
    safe = re.sub(r"[^A-Za-z0-9._%-]", "_", card_id)
    suffix = "" if lang != "en" else ".en"
    target = CACHE / "cards" / "high" / f"{safe}{suffix}.webp"
    if target.exists():
        return target
    con = get_db()
    row = con.execute("SELECT image_de, image_en, image_alt FROM cards WHERE id = ?", (card_id,)).fetchone()
    con.close()
    if not row:
        return None
    urls = [
        f"{row['image_de']}/high.webp" if row["image_de"] else None,
        f"{row['image_en']}/high.webp" if row["image_en"] else None,
    ]
    if lang == "en":
        urls.reverse()
    urls += _alt_urls(row["image_alt"], "high")
    return target if _fetch_asset(urls, target) else None


def _dex_image_path(dex_id):
    target = CACHE / "dex" / f"{dex_id}.png"
    if target.exists():
        return target
    urls = [
        f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/{dex_id}.png",
        f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{dex_id}.png",
    ]
    return target if _fetch_asset(urls, target) else None


def _draw_placeholder(c, x, y, lines):
    c.setLineWidth(0.6)
    c.setStrokeGray(0.55)
    c.roundRect(x + 4 * mm, y + 4 * mm, CARD_W - 8 * mm, CARD_H - 8 * mm, 3 * mm)
    c.setFillGray(0.2)
    ty = y + CARD_H / 2 + (len(lines) * 6) / 2
    for i, (text, size, bold) in enumerate(lines):
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawCentredString(x + CARD_W / 2, ty - i * 14, text[:34])


def _draw_dex_cell(c, x, y, item, pokemon_names):
    dex = item.get("dex")
    name = pokemon_names.get(dex) or f"#{dex}"
    c.setLineWidth(0.6)
    c.setStrokeGray(0.4)
    c.roundRect(x + 2 * mm, y + 2 * mm, CARD_W - 4 * mm, CARD_H - 4 * mm, 3 * mm)
    c.setFillGray(0.35)
    c.setFont("Helvetica", 9)
    c.drawCentredString(x + CARD_W / 2, y + CARD_H - 10 * mm, "#%03d" % dex)
    path = _dex_image_path(dex)
    if path:
        try:
            size = 40 * mm
            c.drawImage(
                _grayscale_reader(path), x + (CARD_W - size) / 2, y + (CARD_H - size) / 2 + 2 * mm,
                size, size, preserveAspectRatio=True, anchor="c", mask="auto",
            )
        except Exception:
            pass
    c.setFillGray(0.1)
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(x + CARD_W / 2, y + 9 * mm, str(name)[:24])


def _pdf_titelseite(c, binder, lang, stats):
    """Deckblatt mit Eckdaten und Rechtshinweis."""
    page_w, page_h = A4
    c.setFillGray(0.1)
    c.setFont("Helvetica-Bold", 26)
    c.drawCentredString(page_w / 2, page_h - 70 * mm, binder["name"][:48])
    c.setFont("Helvetica", 12)
    c.setFillGray(0.4)
    c.drawCentredString(page_w / 2, page_h - 80 * mm,
                        "Binderplan" + (" · Sammlungs-Checkliste" if lang == "de" else " · Collection plan"))
    c.setFillGray(0.2)
    c.setFont("Helvetica", 13)
    y = page_h - 105 * mm
    for zeile in stats:
        c.drawCentredString(page_w / 2, y, zeile)
        y -= 9 * mm
    c.setFont("Helvetica", 8.5)
    c.setFillGray(0.45)
    if lang == "de":
        hinweise = [
            "Nur für die private Sammlungsplanung. Die Ausdrucke sind Platzhalter,",
            "dürfen nicht verkauft, getauscht oder als echte Karten ausgegeben werden.",
            "Inoffizielles Fan-Werkzeug ohne Verbindung zu The Pokémon Company / Nintendo.",
        ]
    else:
        hinweise = [
            "For private collection planning only. Prints are placeholders and must not be",
            "sold, traded or passed off as real cards.",
            "Unofficial fan tool, not affiliated with The Pokémon Company / Nintendo.",
        ]
    y = 30 * mm
    for zeile in hinweise:
        c.drawCentredString(page_w / 2, y, zeile)
        y -= 4.5 * mm
    c.showPage()


def _pdf_wasserzeichen(c, x, y, lang):
    c.saveState()
    try:
        c.setFillAlpha(0.30)
    except Exception:
        pass
    c.setFillGray(0.30)
    c.setFont("Helvetica-Bold", 13)
    c.translate(x + CARD_W / 2, y + CARD_H / 2)
    c.rotate(36)
    c.drawCentredString(0, 0, "PLATZHALTER · KEIN ORIGINAL" if lang == "de" else "PLACEHOLDER · NOT ORIGINAL")
    c.restoreState()


def _zaehle_export(user, binder_id):
    """Free-Export abbuchen – aber nicht, wenn derselbe Binder in den letzten 30 Minuten
    schon exportiert wurde (abgebrochener Download, zweiter Versuch, Drucker-Panne)."""
    if _ist_pro(user):
        return
    letzter = (user.get("letzter_export") or "").split(":", 1)
    if len(letzter) == 2 and letzter[0] == binder_id and letzter[1] >= datetime_str_vor(0.5):
        return
    con = get_db()
    con.execute("UPDATE users SET exports_monat = ?, letzter_export = ? WHERE id = ?",
                (f"{_monat_key()}:{_exporte_benutzt(user) + 1}",
                 f"{binder_id}:{datetime_str_vor(0)}", user["id"]))
    con.commit()
    con.close()


def _bilder_vorladen(card_ids, lang):
    """Hochauflösende Bilder parallel in den Cache holen (vorher lief das im PDF
    sequenziell: 64 Karten ≈ 40 s). → Anzahl fehlender Bilder."""
    ids = list(dict.fromkeys(i for i in card_ids if i))
    with ThreadPoolExecutor(8) as pool:
        pfade = list(pool.map(lambda c: _print_image_path(c, lang), ids))
    return sum(1 for p in pfade if p is None)


@app.post("/api/binders/{binder_id}/pdf_vorbereiten")
def binder_pdf_vorbereiten(binder_id: str, request: Request):
    """Schritt 1 des Exports: Bilder laden (parallel), damit das PDF danach in Sekunden kommt."""
    _require_user(request)
    binder = _load_binder(binder_id)
    lang = "en" if (binder.get("options") or {}).get("sprache") == "en" else "de"
    ids = [i.get("id") for i in binder["items"] if i.get("type") == "card" and i.get("id")]
    fehlend = _bilder_vorladen(ids, lang) if ids else 0
    return {"karten": len(ids), "ohne_bild": fehlend}


@app.get("/api/binders/{binder_id}/pdf")
def binder_pdf(binder_id: str, request: Request, variante: str = "karten", nur_fehlende: int = 0):
    user = _require_user(request)
    binder = _load_binder(binder_id)
    per_page = LAYOUTS.get(binder["layout"], 9)
    lang = "en" if (binder.get("options") or {}).get("sprache") == "en" else "de"
    if variante == "checkliste":
        return _checkliste_pdf(binder, lang, bool(nur_fehlende))
    # Karten-PDF: zählt gegen das Monats-Limit von Free-Konten – ein zweiter Abruf desselben Binders
    # innerhalb von 30 Minuten (Download abgebrochen, nochmal drucken) bleibt frei
    letzter = (user.get("letzter_export") or "").split(":", 1)
    kulanz = len(letzter) == 2 and letzter[0] == binder_id and letzter[1] >= datetime_str_vor(0.5)
    if not _ist_pro(user) and not kulanz and _exporte_benutzt(user) >= FREE_EXPORT_LIMIT:
        raise HTTPException(402, detail={"code": "limit_export"})

    con = get_db()
    card_ids = [i.get("id") for i in binder["items"] if i.get("type") == "card" and i.get("id")]
    card_rows = {}
    for chunk_start in range(0, len(card_ids), 500):
        chunk = card_ids[chunk_start:chunk_start + 500]
        for r in con.execute(
            "SELECT id, name_de, name_en, local_id, set_id,"
            " (SELECT name FROM sets WHERE sets.id = cards.set_id) set_name"
            " FROM cards WHERE id IN (%s)" % ",".join("?" * len(chunk)),
            chunk,
        ):
            card_rows[r["id"]] = r
    namensspalte = "name_en" if lang == "en" else "name_de"
    pokemon_names = {r["dex_id"]: (r[namensspalte] or r["name_de"])
                     for r in con.execute("SELECT dex_id, name_de, name_en FROM pokemon")}
    con.close()
    _bilder_vorladen(card_ids, lang)

    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    page_w, page_h = A4
    grid_w = COLS * CARD_W + (COLS - 1) * GUTTER
    grid_h = ROWS * CARD_H + (ROWS - 1) * GUTTER
    ox = (page_w - grid_w) / 2
    oy = (page_h - grid_h) / 2

    printable = [
        (idx, item) for idx, item in enumerate(binder["items"])
        if item.get("type") != "empty" and not (nur_fehlende and item.get("have"))
    ]

    gesammelt = sum(1 for i in binder["items"] if i.get("have"))
    gesamt = sum(1 for i in binder["items"] if i.get("type") != "empty")
    if lang == "de":
        stats = [
            f"{gesamt} Karten geplant · {gesammelt} bereits gesammelt",
            f"{len(printable)} Proxys in diesem Druck · {max(1, -(-len(printable) // 9))} A4-Blätter",
            f"Raster {binder['layout'].replace('x', ' × ')} · {max(1, -(-len(binder['items']) // per_page))} Binderseiten",
        ]
    else:
        stats = [
            f"{gesamt} cards planned · {gesammelt} already collected",
            f"{len(printable)} proxies in this print · {max(1, -(-len(printable) // 9))} A4 sheets",
            f"Grid {binder['layout'].replace('x', ' × ')} · {max(1, -(-len(binder['items']) // per_page))} binder pages",
        ]
    _pdf_titelseite(c, binder, lang, stats)

    cell = 0
    for idx, item in printable:
        if cell == COLS * ROWS:
            c.showPage()
            cell = 0
        col = cell % COLS
        row = cell // COLS
        x = ox + col * (CARD_W + GUTTER)
        y = oy + grid_h - (row + 1) * CARD_H - row * GUTTER

        binder_page = idx // per_page + 1
        slot = idx % per_page + 1
        variant = item.get("variant") or "normal"

        if item.get("type") == "dex":
            _draw_dex_cell(c, x, y, item, pokemon_names)
        else:
            card = card_rows.get(item.get("id"))
            path = _print_image_path(item.get("id"), lang) if card else None
            if path:
                try:
                    c.drawImage(str(path), x, y, CARD_W, CARD_H)   # JPEG-Pfad → DCT direkt eingebettet
                except Exception:
                    path = None
            if not path:
                if lang == "en":
                    name = (card["name_en"] or card["name_de"]) if card else item.get("id", "?")
                else:
                    name = (card["name_de"] or card["name_en"]) if card else item.get("id", "?")
                setline = f"{card['set_name'] or card['set_id']} · {card['local_id']}" if card else ""
                _draw_placeholder(c, x, y, [(str(name), 12, True), (setline, 9, False)])
            _pdf_wasserzeichen(c, x, y, lang)

        # Schnittkante + Fach-Beschriftung in der Fuge (wird mit abgeschnitten)
        c.setLineWidth(0.4)
        c.setStrokeGray(0.75)
        c.rect(x, y, CARD_W, CARD_H)
        c.setFillGray(0.45)
        c.setFont("Helvetica", 6.5)
        if lang == "en":
            label = f"Page {binder_page} · Slot {slot}"
        else:
            label = f"Seite {binder_page} · Fach {slot}"
        vl = VARIANT_LABELS.get(variant)
        if vl:
            label += " · " + vl
        if item.get("zustand"):
            label += " · " + str(item["zustand"])[:12]
        c.drawCentredString(x + CARD_W / 2, y - 2.6 * mm, label)
        cell += 1

    if not printable:
        c.setFont("Helvetica", 14)
        c.drawCentredString(page_w / 2, page_h / 2,
                            "Dieser Binder ist noch leer." if lang == "de" else "This binder is still empty.")
    c.save()

    con = get_db()
    con.execute(
        "INSERT INTO kv (key,value) VALUES ('pdf_exports','1')"
        " ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1"
    )
    con.commit()
    con.close()
    _zaehle_export(user, binder_id)

    fname = re.sub(r"[^A-Za-z0-9äöüÄÖÜß _-]", "", binder["name"]) or "binder"
    return Response(
        buf.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{fname}.pdf"'},
    )


def _binder_zeilen(binder, lang):
    """Alle Nicht-Leer-Fächer mit Anzeigedaten (für Checkliste und Kaufliste)."""
    con = get_db()
    card_ids = [i.get("id") for i in binder["items"] if i.get("type") == "card" and i.get("id")]
    karten = {}
    for start in range(0, len(card_ids), 500):
        chunk = card_ids[start:start + 500]
        for r in con.execute(
            f"{_CARD_SELECT} WHERE cards.id IN ({','.join('?' * len(chunk))})", chunk
        ):
            karten[r["id"]] = _card_brief(r)
    spalte = "name_en" if lang == "en" else "name_de"
    pokemon_names = {r["dex_id"]: (r[spalte] or r["name_de"])
                     for r in con.execute("SELECT dex_id, name_de, name_en FROM pokemon")}
    preise = {r["card_id"]: r["eur"] for r in con.execute("SELECT card_id, eur FROM card_prices")}
    con.close()
    per_page = LAYOUTS.get(binder["layout"], 9)
    zeilen = []
    for idx, item in enumerate(binder["items"]):
        if item.get("type") == "empty":
            continue
        pos = f"{idx // per_page + 1}·{idx % per_page + 1}"
        if item.get("type") == "dex":
            zeilen.append({"pos": pos, "name": f"#{item.get('dex'):03d} {pokemon_names.get(item.get('dex'), '')}",
                           "set": "Pokédex", "nr": "", "eur": None, "have": bool(item.get("have")),
                           "variant": ""})
        else:
            k = karten.get(item.get("id")) or {}
            name = (k.get("name_en") if lang == "en" else k.get("name")) or item.get("id", "?")
            setn = (k.get("set_name_en") if lang == "en" else k.get("set_name")) or ""
            zeilen.append({"pos": pos, "name": name, "set": setn, "nr": k.get("local_id") or "",
                           "eur": preise.get(item.get("id")), "have": bool(item.get("have")),
                           "variant": item.get("variant") or "", "zustand": item.get("zustand") or ""})
    return zeilen


def _checkliste_pdf(binder, lang, nur_fehlende):
    """Karteiliste ohne Kartenbilder — zählt nicht als Export."""
    zeilen = _binder_zeilen(binder, lang)
    if nur_fehlende:
        zeilen = [z for z in zeilen if not z["have"]]
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    page_w, page_h = A4
    kopf = ("Checkliste" if lang == "de" else "Checklist") + " – " + binder["name"][:40]
    y = 0

    def neue_seite():
        nonlocal y
        c.setFont("Helvetica-Bold", 14)
        c.setFillGray(0.1)
        c.drawString(18 * mm, page_h - 18 * mm, kopf)
        c.setFont("Helvetica", 8)
        c.setFillGray(0.5)
        c.drawRightString(page_w - 18 * mm, page_h - 18 * mm, "Binderplan")
        y = page_h - 28 * mm

    neue_seite()
    gesamt = len(zeilen); hab = sum(1 for z in zeilen if z["have"])
    c.setFont("Helvetica", 9)
    c.setFillGray(0.45)
    c.drawString(18 * mm, y, (f"{hab} von {gesamt} gesammelt · Preise: Cardmarket-Trend" if lang == "de"
                              else f"{hab} of {gesamt} collected · prices: Cardmarket trend"))
    y -= 8 * mm
    letzte_seite = None
    seiten_summe = 0.0
    c.setFont("Helvetica", 9.5)
    for z in zeilen:
        seite = z["pos"].split("·")[0]
        if seite != letzte_seite:
            # Kopfzeile je Binderseite – so hakt man Seite für Seite ab
            if y < 30 * mm:
                c.showPage(); neue_seite()
            y -= 2 * mm
            c.setFillGray(0.93); c.rect(18 * mm, y - 1.6 * mm, page_w - 36 * mm, 6 * mm, fill=1, stroke=0)
            c.setFillGray(0.2); c.setFont("Helvetica-Bold", 9.5)
            c.drawString(20 * mm, y, ("Seite " if lang == "de" else "Page ") + seite)
            c.setFont("Helvetica", 9.5)
            y -= 7 * mm
            letzte_seite = seite
        if y < 18 * mm:
            c.showPage()
            neue_seite()
            c.setFont("Helvetica", 9.5)
        c.setFillGray(0.15)
        c.setLineWidth(0.7)
        c.setStrokeGray(0.3)
        c.rect(20 * mm, y - 1, 3.4 * mm, 3.4 * mm)
        if z["have"]:
            c.setFont("Helvetica-Bold", 9)
            c.drawString(20.6 * mm, y - 0.4, "X")
            c.setFont("Helvetica", 9.5)
        c.drawString(27 * mm, y, z["pos"].split("·")[1])
        extra = VARIANT_LABELS.get(z["variant"], "")
        name = z["name"] + (f" ({extra})" if extra else "") + (f" · {z['zustand']}" if z.get("zustand") else "")
        c.drawString(36 * mm, y, name[:46])
        c.setFillGray(0.45)
        c.drawString(122 * mm, y, ((z["set"] or "") + (" " + z["nr"] if z["nr"] else ""))[:30])
        if z["eur"] is not None:
            c.drawRightString(page_w - 18 * mm, y, f"{z['eur']:.2f} €")
            seiten_summe += z["eur"]
        y -= 6.2 * mm
    summe = sum(z["eur"] or 0 for z in zeilen); fehlend = sum((z["eur"] or 0) for z in zeilen if not z["have"])
    if y < 26 * mm:
        c.showPage(); neue_seite()
    y -= 4 * mm
    c.setFont("Helvetica-Bold", 9.5); c.setFillGray(0.2)
    c.drawRightString(page_w - 18 * mm, y, (f"Gesamt {summe:.2f} € · noch zu kaufen {fehlend:.2f} €" if lang == "de"
                                          else f"Total {summe:.2f} € · still to buy {fehlend:.2f} €"))
    c.save()
    fname = re.sub(r"[^A-Za-z0-9äöüÄÖÜß _-]", "", binder["name"]) or "binder"
    return Response(buf.getvalue(), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{fname}-checkliste.pdf"'})


@app.get("/api/binders/{binder_id}/kaufliste")
def binder_kaufliste(binder_id: str, request: Request, format: str = "csv"):
    """Fehlende Karten samt Preisen als Einkaufsliste (Pro-Funktion)."""
    user = _require_user(request)
    if not _ist_pro(user):
        raise HTTPException(402, detail={"code": "limit_pro"})
    binder = _load_binder(binder_id)
    lang = "en" if (binder.get("options") or {}).get("sprache") == "en" else "de"
    zeilen = [z for z in _binder_zeilen(binder, lang) if not z["have"]]
    summe = sum(z["eur"] or 0 for z in zeilen)
    ohne = sum(1 for z in zeilen if z["eur"] is None)
    if format == "txt":
        out = [f"Kaufliste – {binder['name']}", ""]
        for z in zeilen:
            preis = f"{z['eur']:.2f} €" if z["eur"] is not None else "?"
            out.append(f"- {z['name']}{' (Reverse)' if z['variant'] == 'reverse' else ''} · {z['set']} · Nr. {z['nr']} · {preis}")
        out += ["", f"Summe (Cardmarket-Trend): {summe:.2f} €" + (f" · {ohne} ohne Preis" if ohne else "")]
        text = "\n".join(out)
        media, ext = "text/plain; charset=utf-8", "txt"
    else:
        out = ["Name;Set;Nummer;Variante;Preis EUR"]
        for z in zeilen:
            preis = f"{z['eur']:.2f}".replace(".", ",") if z["eur"] is not None else ""
            out.append(f"{z['name']};{z['set']};{z['nr']};{z['variant']};{preis}")
        summe_txt = f"{summe:.2f}".replace(".", ",")
        out.append(f"Summe;;;;{summe_txt}")
        text = "\n".join(out)
        media, ext = "text/csv; charset=utf-8", "csv"
    fname = re.sub(r"[^A-Za-z0-9äöüÄÖÜß _-]", "", binder["name"]) or "binder"
    return Response(text.encode("utf-8-sig"), media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{fname}-kaufliste.{ext}"'})


# --- Frontend, Rechtsseite & PWA --------------------------------------------

@app.get("/")
def landing():
    """Startseite: erklärt das Werkzeug (SEO, Teilen); Rückkehrer leitet sie per JS in die App."""
    return FileResponse(BASE / "landing.html", media_type="text/html")


@app.get("/app")
def index():
    return FileResponse(BASE / "index.html", media_type="text/html")


@app.get("/assets/{name}")
def asset(name: str):
    """Bilder der Startseite (assets/ im Repo)."""
    if not re.fullmatch(r"[a-z0-9_-]+\.(png|webp|jpg|svg)", name):
        raise HTTPException(404)
    f = BASE / "assets" / name
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, headers=IMG_HEADERS)


@app.get("/recht")
def recht():
    return FileResponse(BASE / "recht.html", media_type="text/html")


@app.get("/manifest.webmanifest")
def manifest():
    return Response(json.dumps({
        "name": "Binderplan", "short_name": "Binderplan",
        "description": "Pokémon-Binder planen und als Schwarz-Weiß-Checkliste drucken",
        "start_url": ".", "scope": ".", "display": "standalone",
        "background_color": "#fdf8f1", "theme_color": "#e85d43",
        "icons": [
            {"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }), media_type="application/manifest+json")


def _app_icon(groesse: int) -> Path:
    """Einfaches App-Icon (Binder-Glyphe) einmalig mit Pillow erzeugen."""
    ziel = CACHE / f"icon-{groesse}.png"
    if ziel.exists():
        return ziel
    from PIL import ImageDraw
    img = Image.new("RGB", (groesse, groesse), "#e85d43")
    d = ImageDraw.Draw(img)
    g = groesse
    d.rounded_rectangle([g * 0.2, g * 0.16, g * 0.8, g * 0.84], radius=g * 0.06,
                        outline="white", width=max(3, g // 28))
    d.line([g * 0.34, g * 0.16, g * 0.34, g * 0.84], fill="white", width=max(3, g // 28))
    d.ellipse([g * 0.47, g * 0.32, g * 0.67, g * 0.52], outline="white", width=max(3, g // 32))
    img.save(ziel)
    return ziel


@app.get("/icon-{groesse}.png")
def icon(groesse: int):
    if groesse not in (192, 512):
        raise HTTPException(404)
    return FileResponse(_app_icon(groesse), media_type="image/png", headers=IMG_HEADERS)
