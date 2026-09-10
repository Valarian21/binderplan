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

import html
import datetime
import io
import json
import re
import secrets
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

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
# Die japanischen Sets vor 2015 kennt der Cardmarket-Katalog nicht (dort beginnen die
# japanischen Erweiterungen erst mit „Double Crisis"). Für die 27 Sets, in denen wir Karten
# führen, steht der eingebürgerte englische Name deshalb von Hand hier — „拡張パック" sagt
# niemandem etwas, „Expansion Pack" schon.
JP_SET_NAMEN = {
    "PMCG1": "Expansion Pack", "PMCG2": "Pokémon Jungle", "PMCG3": "Mystery of the Fossils",
    "PMCG4": "Rocket Gang", "PMCG5": "Leaders' Stadium", "PMCG6": "Challenge from the Darkness",
    "jp-neo1": "Gold, Silver, to a New World...", "jp-neo2": "Crossing the Ruins...",
    "jp-neo3": "Awakening Legends", "jp-neo4": "Darkness, and to Light...",
    "VS1": "Pokémon Card VS", "web1": "Pokémon Card web",
    "E1": "Base Expansion Pack", "E2": "The Town on No Map", "E3": "Wind from the Sea",
    "E4": "Split Earth", "E5": "Mysterious Mountains",
    "ADV1": "Expansion Pack ADV", "ADV2": "Miracle of the Desert", "ADV3": "Rulers of the Heavens",
    "ADV5": "Undone Seal",
    "PCG1": "Flight of Legends", "PCG2": "Clash of the Blue Sky", "PCG3": "Rocket Gang Strikes Back",
    "PCG4": "Golden Sky, Silvery Ocean", "PCG5": "Mirage Forest", "PCG6": "Holon Research Tower",
    "PCG7": "Holon Phantom", "PCG8": "Miracle Crystal", "PCG9": "Offense and Defense of the Furthest Ends",
    "SM1M": "Collection Moon", "SM1S": "Collection Sun",
    "SVK": "Deck Build Box: Stellar Miracle",
    "SVLS": "Starter Set Terastal: Skeledirge ex", "SVLN": "Starter Set Terastal: Sylveon ex",
}

# Was der Cardmarket-Katalog nicht abdeckt und der Pokédex-Abgleich nicht kann: die
# Basisenergien und eine Handvoll Trainerkarten, die in fast jedem Set stecken.
JP_KARTEN_NAMEN = {
    "基本草エネルギー": "Basic Grass Energy", "基本炎エネルギー": "Basic Fire Energy",
    "基本水エネルギー": "Basic Water Energy", "基本雷エネルギー": "Basic Lightning Energy",
    "基本超エネルギー": "Basic Psychic Energy", "基本闘エネルギー": "Basic Fighting Energy",
    "基本悪エネルギー": "Basic Darkness Energy", "基本鋼エネルギー": "Basic Metal Energy",
    "基本妖エネルギー": "Basic Fairy Energy", "基本無色エネルギー": "Basic Colorless Energy",
    "レインボーエネルギー": "Rainbow Energy", "闇のエネルギー": "Darkness Energy",
    "金属エネルギー": "Metal Energy", "ポケモンいれかえ": "Switch", "ボスの指令": "Boss's Orders",
    "博士の研究": "Professor's Research", "ハイパーボール": "Ultra Ball", "ネストボール": "Nest Ball",
    "ワープポイント": "Warp Point", "すごいつりざお": "Super Rod",
    "スーパースクープアップ": "Super Scoop Up", "クリスタルシャード": "Crystal Shard",
    "ナンジャモ": "Iono", "ネモ": "Nemona", "ペパー": "Arven",
}

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


# --- Druckvarianten ---------------------------------------------------------
# Welche Ausprägungen es von einer Karte gibt, stand bisher allein in der Momentaufnahme
# `variants` aus dem Katalog-Sync. Gegen die tatsächlich gehandelten TCGplayer-Varianten
# stimmte davon am 03.09.2026 nur ein Drittel. Drei Regeln räumen das auf:
#
# 1. Der nächtliche Preislauf liest die TCGplayer-Schlüssel derselben Antwort mit
#    (`normal`, `holofoil`, `reverse-holofoil`, `1st-edition…`). Wer einen Preis für
#    `reverse-holofoil` führt, handelt Reverse Holos — verlässlicher als jedes Flag.
# 2. Reverse Holos gibt es erst ab Legendary Collection. Was die Quelle davor behauptet,
#    ist falsch, egal wie oft sie es wiederholt.
# 3. Poké-Ball- und Master-Ball-Muster gehören zu genau zwei Sets. Sie wurden vorher bei
#    jeder der 33.732 Karten zur Wahl gestellt, auch beim Grundset-Glurak von 1999.
REVERSE_AB = "2002-05-24"          # Legendary Collection, das erste Set mit Reverse Holos
# Promo-Reihen laufen über Jahre; ihr Erscheinungsdatum ist das der ersten Karte. Die
# Wizards Black Star Promos etwa stehen mit 1999 in der Datenbank, ihre Nummern 33 bis 53
# kamen 2002 und 2003. Für solche Sets darf die Datumsregel nicht greifen — sonst nimmt sie
# echten Karten eine Ausprägung weg.
PROMO_SETS = {"basep", "np", "bwp", "dpp", "hgssp", "xyp", "smp", "swshp", "svp", "sv-p",
              "colp", "ecardp", "miscp", "bog", "si1", "pop1", "pop2", "pop3", "pop4",
              "pop5", "pop6", "pop7", "pop8", "pop9"}

# Sets mit Sondermuster statt eigener Karten-IDs. Kommt ein neues dazu, gehört es hierhin
# — und nur hierhin: Kartendetail, Sammlung und Trefferkachel lesen dieselbe Liste.
MUSTER_SETS = {
    "sv03.5": ["pokeball", "masterball"],      # 151
    "sv08.5": ["pokeball", "masterball"],      # Prismatische Entwicklungen
}

# TCGplayer-Schlüssel → unsere Variante. Beide Schreibweisen: TCGdex trennt mit
# Bindestrich, pokemontcg.io schreibt zusammen. Das hat einmal 11.000 Reverse Holos
# gekostet — die Zweitquelle schrieb `reverseHolofoil`, die Tabelle kannte nur
# `reverse-holofoil`, und die Variante galt als nicht gehandelt.
TP_VARIANTE = {
    "normal": "normal", "unlimited": "normal",
    "holofoil": "holo", "unlimited-holofoil": "holo", "unlimitedholofoil": "holo",
    "reverse-holofoil": "reverse", "reverseholofoil": "reverse",
    "1st-edition": "first", "1stedition": "first",
    "1st-edition-normal": "first", "1steditionnormal": "first",
    "1st-edition-holofoil": "first", "1steditionholofoil": "first",
}


def varianten_aus_tp(keys):
    """→ ({normal, reverse, holo, first}, alles_bekannt).

    Das zweite Ergebnis ist die Sicherung: taucht ein Schlüssel auf, den diese Tabelle
    nicht kennt, wird die Karte nicht angefasst. Sonst löscht eine neue Schreibweise der
    Quelle stillschweigend Ausprägungen — genau das ist am 03.09. passiert."""
    out = {"normal": False, "reverse": False, "holo": False, "first": False}
    bekannt = True
    for k in keys or []:
        if not k:
            continue
        v = TP_VARIANTE.get(k.strip().lower())
        if v:
            out[v] = True
        else:
            bekannt = False
    return out, bekannt


def muster_fuer_set(set_id):
    return MUSTER_SETS.get(set_id or "", [])


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

app = FastAPI(title="Binderplan", docs_url=None, redoc_url=None, openapi_url=None)


def _abschnitt(name):
    """Ein herausgelöster Abschnitt dieser Datei (auth, bilder, binder, pdf, katalog): wird an
    genau dieser Stelle im Namensraum von main.py ausgeführt – wie vorher der Text hier, nur in
    eigener Datei. Tracebacks nennen die Datei, pyflakes prüft alle zusammen (scripts/pruefen.py)."""
    pfad = BASE / f"{name}.py"
    exec(compile(pfad.read_text(encoding="utf-8"), str(pfad), "exec"), globals())


# --- Logging & Zugriffsprotokoll ---------------------------------------------------------
# Vorher: print() und die Zugriffszeilen von uvicorn ohne Dauer. Jetzt eine Zeile je API-Aufruf
# mit Status, Dauer und ob ein Konto dran hing; Bilder bleiben außen vor (zu viele, zu gleich).
import logging  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("binderplan")
logging.getLogger("httpx").setLevel(logging.WARNING)


def _log(*teile):
    """Ersatz für die alten print()-Zeilen der Hintergrundjobs – gleiche Aufrufform."""
    log.info(" ".join(str(t) for t in teile))


@app.middleware("http")
async def _zugriffsprotokoll(request: Request, call_next):
    start = time.perf_counter()
    try:
        antwort = await call_next(request)
    except Exception:
        log.exception("%s %s abgebrochen", request.method, request.url.path)
        raise
    pfad = request.url.path
    if pfad.startswith("/api/") and not pfad.startswith("/api/img"):
        dauer = (time.perf_counter() - start) * 1000
        konto = "K" if request.headers.get("authorization") or request.cookies.get("bp_token") else "-"
        stufe = log.warning if antwort.status_code >= 500 or dauer > 2000 else log.info
        stufe("%s %s%s %s %.0fms %s", request.method, pfad,
              ("?" + request.url.query) if request.url.query else "", antwort.status_code, dauer, konto)
    return antwort



def get_db():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


# Was eine Karte wert ist, entscheidet ein einziges Modul: wert.py. Vorher rechnete jede
# Ansicht selbst, und dieselbe Sammlung hatte fünf Werte — bei gebrauchten Karten lagen 79 %
# dazwischen. Die Namen hier bleiben, damit die Aufrufer unverändert weiterlaufen.
import wert as _wert  # noqa: E402

ZUSTAND_FAKTOR = _wert.ZUSTAND_FAKTOR
preis_fuer_posten = _wert.posten_wert


def _spalte_da(con, tabelle, spalte):
    """Gibt es die Spalte schon? Der Pruefstein fuer additive Migrationen."""
    try:
        return any(r[1] == spalte for r in con.execute(f"PRAGMA table_info({tabelle})"))
    except Exception:
        return False


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
        # Fähigkeiten und Attacken, mit „|" verbunden. Das ist kein Spielwissen, sondern
        # das Merkmal, mit dem der Cardmarket-Katalog Karten gleichen Namens innerhalb
        # einer Erweiterung auseinanderhält: „Ampharos [Acceleration Bolt | Thunder]"
        # gegen „Ampharos [Conductivity | Lightning Crush | Prime]".
        "ALTER TABLE cards ADD COLUMN merkmale TEXT",
        # Cardmarket-Erweiterung des zugeordneten Produkts. Sie macht die Suche als
        # Rückfall brauchbar: mit `idExpansion` bleibt genau eine Karte übrig, statt
        # 75 Palkia aus zwanzig Jahren.
        "ALTER TABLE card_prices ADD COLUMN cm_expansion INTEGER",
    ):
        try:
            con.execute(alter)
        except sqlite3.OperationalError:
            pass
    con.execute("CREATE INDEX IF NOT EXISTS idx_cards_region ON cards(region)")   # erst nach dem ALTER möglich
    con.execute("CREATE INDEX IF NOT EXISTS idx_cards_illu ON cards(illustrator)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_binders_user ON binders(user_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_binders_sichtbar ON binders(sichtbar)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
    # Zustand und Sprache gehören zum Exemplar, nicht zur Karte: wer dieselbe Karte einmal
    # auf Deutsch in NM und einmal auf Englisch in GD besitzt, führt zwei Posten. Der alte
    # Schlüssel (user_id, card_id, variante) ließ nur einen zu — Zustand war ein Feld, das
    # beim zweiten Exemplar den ersten überschrieb, Sprache gab es gar nicht.
    if not _spalte_da(con, "sammlung", "sprache"):
        con.execute("""CREATE TABLE IF NOT EXISTS sammlung_neu (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, card_id TEXT, variante TEXT DEFAULT 'normal',
            zustand TEXT DEFAULT '', sprache TEXT DEFAULT '',
            anzahl INTEGER DEFAULT 1, kaufpreis REAL, gekauft_am TEXT, notiz TEXT,
            created_at TEXT, updated_at TEXT)""")
        try:
            con.execute("""INSERT INTO sammlung_neu
                (user_id, card_id, variante, zustand, sprache, anzahl, kaufpreis, gekauft_am,
                 notiz, created_at, updated_at)
                SELECT user_id, card_id, variante, COALESCE(zustand,''), '', anzahl, kaufpreis,
                       gekauft_am, notiz, created_at, updated_at FROM sammlung""")
            con.execute("DROP TABLE sammlung")
            con.execute("ALTER TABLE sammlung_neu RENAME TO sammlung")
        except Exception as _e:
            _log("Sammlungs-Migration übersprungen:", _e)
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sammlung_pos"
                " ON sammlung(user_id, card_id, variante, zustand, sprache)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_sammlung_user ON sammlung(user_id)")
    # Amerikanische Preise (TCGplayer) neben den europäischen (Cardmarket). Beide kommen
    # aus derselben TCGdex-Antwort; der Vergleich der zwei Märkte ist eine Zahl, die sonst
    # niemand zeigt, und sie kostet keinen zusätzlichen Abruf.
    # Zustandspreise gibt es bei keiner der beiden Börsen — Cardmarket rechnet den Trend
    # über alle Zustände, TCGplayer nennt Near-Mint-Preise. Was es gibt, ist die Spanne:
    # der niedrigste Angebotspreis und der 30-Tage-Schnitt in Europa, Tief/Mitte/Hoch in
    # den USA. Zusammen sagen sie mehr über den Handelsraum einer Karte als eine Zahl.
    for befehl in ("ALTER TABLE card_prices ADD COLUMN eur_low REAL",
                   "ALTER TABLE card_prices ADD COLUMN eur_avg30 REAL",
                   "ALTER TABLE card_prices ADD COLUMN usd_low REAL",
                   "ALTER TABLE card_prices ADD COLUMN usd_mid REAL",
                   "ALTER TABLE card_prices ADD COLUMN usd_high REAL",
                   "ALTER TABLE card_prices ADD COLUMN cm_produkt INTEGER",
                   "ALTER TABLE card_prices ADD COLUMN usd REAL",
                   "ALTER TABLE card_prices ADD COLUMN usd_holo REAL",
                   "ALTER TABLE card_prices ADD COLUMN tcgplayer_id INTEGER",
                   # 2026-09-03: was TCGdex in `variants_detailed` und im TCGplayer-Block
                   # ohnehin mitliefert — Preis je Druckvariante, die tatsächlich
                   # gehandelten Varianten und das Urteil des Börsenvergleichs.
                   "ALTER TABLE card_prices ADD COLUMN preise_json TEXT",
                   "ALTER TABLE card_prices ADD COLUMN tp_keys TEXT",
                   "ALTER TABLE card_prices ADD COLUMN status TEXT",
                   "ALTER TABLE card_prices ADD COLUMN kurs REAL",
                   "ALTER TABLE card_prices ADD COLUMN eur_geschaetzt REAL",
                   # Die aufgelöste Cardmarket-Produktseite. Vorher führte der Link im
                   # Kartendetail auf eine Namenssuche — bei „Guardevoir" 60 Treffer,
                   # von denen keiner die gemeinte Karte war.
                   "ALTER TABLE card_prices ADD COLUMN cm_url TEXT",
                   # Die Kennung derselben Karte bei pokemontcg.io. Sie weicht bei einigen
                   # Sets ab (sv03.5-1 gegen sv3pt5-1) und ist der Schlüssel zur
                   # Cardmarket-Weiterleitung.
                   "ALTER TABLE card_prices ADD COLUMN ptc_id TEXT",
                   # Aus dem offiziellen Cardmarket-Preisverzeichnis übernommen: Zeitpunkt
                   # und woher die Produktnummer stammt (tcgdex, katalog, hand).
                   "ALTER TABLE card_prices ADD COLUMN cm_import_am TEXT",
                   "ALTER TABLE card_prices ADD COLUMN cm_quelle TEXT",
                   # Cardmarket liefert im Preisverzeichnis drei Durchschnitte: über einen,
                   # sieben und dreißig Tage. Der 7-Tage-Wert neben dem 30-Tage-Wert ist
                   # eine echte Bewegung, ohne dass wir dafür eine eigene Reihe brauchen.
                   "ALTER TABLE card_prices ADD COLUMN eur_avg7 REAL",
                   "ALTER TABLE price_history ADD COLUMN usd REAL",
                   # Der Name, unter dem Cardmarket diese Karte selbst führt, und ob er
                   # in seiner Erweiterung eindeutig ist. Beides kommt aus dem offiziellen
                   # Produktkatalog und macht den Rückfall-Link brauchbar: mit dem eigenen
                   # Namen plus Erweiterung springt die Suche auf die Produktseite, statt
                   # zwanzig Jahre Palkia aufzulisten.
                   "ALTER TABLE card_prices ADD COLUMN cm_name TEXT",
                   "ALTER TABLE card_prices ADD COLUMN cm_eindeutig INTEGER"):
        try:
            con.execute(befehl)
        except Exception:
            pass
    # Ein Tag der Preisreihe, samt Zahl der erfassten Karten. Seit die Historie nur noch
    # Änderungen speichert, lässt sich aus ihr allein nicht mehr ablesen, ob ein Tag
    # gemessen wurde oder ob nur nichts passiert ist — ein ausgefallener Lauf sähe aus wie
    # ein ruhiger Markt. Diese Tabelle hält das auseinander; sie kostet eine Zeile am Tag.
    con.execute("CREATE TABLE IF NOT EXISTS price_tage ("
                "datum TEXT PRIMARY KEY, karten INTEGER, quelle TEXT)")
    # Die Tage, die schon in der Historie stehen, einmalig nachtragen: sie waren alle
    # vollständig erfasst und sollen weiter als Messpunkt gelten.
    if not con.execute("SELECT 1 FROM price_tage LIMIT 1").fetchone():
        con.execute("INSERT OR REPLACE INTO price_tage (datum, karten, quelle)"
                    " SELECT datum, COUNT(*), 'historie' FROM price_history GROUP BY datum")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ph_datum ON price_history(datum)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_pokemon_familie ON pokemon(familie)")
    con.commit()
    con.close()


init_db()


# --- Japanische Set-Kennungen, die es auch international gibt -----------------
# TCGdex führt beide Kataloge unter denselben Kennungen, wenn ein Set in beiden Regionen
# so heißt: `neo1` bis `neo4` gibt es einmal als Neo Genesis/Discovery/Revelation/Destiny
# und einmal als japanische Neo-Reihe. Der japanische Sync hat die internationalen Zeilen
# überschrieben — vier Sets standen danach unter japanischem Namen in der japanischen Ära,
# und bei Neo Destiny verdrängten japanische Karten die Nummern 100 bis 113. Verschwunden
# war dabei unter anderem Shining Charizard (`neo4-107`), an dessen Stelle ein japanisches
# Trainerkärtchen mit dessen Preis von 1.477 € stand.
#
# Seither trägt jede japanische Zeile, deren Kennung international belegt ist, den Präfix
# `jp-`. Welche das sind, wird beim Sync gemessen und nicht gepflegt.
JP_PRAEFIX = "jp-"

# Tabellen, die eine Karten-ID führen — beim Umbenennen müssen alle mit.
KARTEN_TABELLEN = ("card_prices", "price_history", "sammlung", "card_art_tags",
                   "card_art_analysis", "card_hashes")


def jp_id(kennung, kollision):
    return JP_PRAEFIX + kennung if kennung in kollision else kennung


def _jp_kollision_umbenennen():
    """Einmalige Reparatur: bereits gespeicherte japanische Zeilen mit belegter Kennung
    umbenennen, damit der internationale Sync sie danach zurückholen kann.

    Läuft genau einmal (Merker in `kv`) und rührt nur Zeilen mit region='jp' an."""
    con = get_db()
    try:
        if con.execute("SELECT value FROM kv WHERE key='jp_praefix'").fetchone():
            return
        kollision = [r["id"] for r in con.execute(
            "SELECT j.id FROM sets j WHERE j.region = 'jp'"
            " AND EXISTS (SELECT 1 FROM cards c WHERE c.set_id = j.id AND c.region = 'intl')")]
        for sid in kollision:
            neu_sid = JP_PRAEFIX + sid
            karten = [r["id"] for r in con.execute(
                "SELECT id FROM cards WHERE set_id = ? AND region = 'jp'", (sid,))]
            for cid in karten:
                neu_cid = JP_PRAEFIX + cid
                for tab in KARTEN_TABELLEN:
                    try:
                        con.execute(f"UPDATE OR IGNORE {tab} SET card_id = ? WHERE card_id = ?",
                                    (neu_cid, cid))
                    except sqlite3.OperationalError:
                        pass
                con.execute("UPDATE cards SET id = ?, set_id = ? WHERE id = ?",
                            (neu_cid, neu_sid, cid))
            # Die Set-Zeile selbst gehört jetzt wieder dem internationalen Katalog; die
            # japanische bekommt eine eigene. Der nächste intl-Sync füllt die alte auf.
            row = con.execute("SELECT * FROM sets WHERE id = ?", (sid,)).fetchone()
            if row and row["region"] == "jp":
                spalten = [k for k in row.keys() if k != "id"]
                con.execute(
                    f"INSERT OR REPLACE INTO sets (id,{','.join(spalten)})"
                    f" VALUES (?,{','.join('?' * len(spalten))})",
                    (neu_sid, *[row[k] for k in spalten]))
                con.execute("DELETE FROM sets WHERE id = ?", (sid,))
            _binderitems_umbenennen(con, {JP_PRAEFIX + c: c for c in karten})
        con.execute("INSERT OR REPLACE INTO kv (key,value) VALUES ('jp_praefix', ?)",
                    (",".join(kollision) or "-",))
        con.commit()
        if kollision:
            _log(f"Japan-Präfix gesetzt für {kollision}")
            threading.Thread(target=_intl_sets_nachladen, args=(list(kollision),),
                             daemon=True).start()
    except Exception as exc:
        _log("JP-Umbenennung übersprungen:", exc)
    finally:
        con.close()


def _binderitems_umbenennen(con, neu_nach_alt):
    """Karten-IDs in gespeicherten Bindern und Artwork-Ankern mitziehen."""
    alt_nach_neu = {v: k for k, v in neu_nach_alt.items()}
    if not alt_nach_neu:
        return
    for row in con.execute("SELECT id, items FROM binders").fetchall():
        try:
            items = json.loads(row["items"] or "[]")
        except Exception:
            continue
        geaendert = False
        for i in items:
            if isinstance(i, dict) and i.get("id") in alt_nach_neu:
                i["id"] = alt_nach_neu[i["id"]]; geaendert = True
        if geaendert:
            con.execute("UPDATE binders SET items = ? WHERE id = ?",
                        (json.dumps(items), row["id"]))
    for row in con.execute("SELECT id, anker FROM artworks").fetchall():
        try:
            anker = json.loads(row["anker"] or "[]")
        except Exception:
            continue
        geaendert = False
        for a in anker:
            if isinstance(a, dict) and a.get("card_id") in alt_nach_neu:
                a["card_id"] = alt_nach_neu[a["card_id"]]; geaendert = True
        if geaendert:
            con.execute("UPDATE artworks SET anker = ? WHERE id = ?",
                        (json.dumps(anker), row["id"]))


def _intl_sets_nachladen(ids):
    """Internationale Sets samt fehlender Karten aus TCGdex nachholen.

    Gegenstück zur Umbenennung: die vier Neo-Sets hatten danach keine Set-Zeile mehr,
    und Neo Destiny fehlten vierzehn Karten — die Nummern 100 bis 113, die der japanische
    Sync verdrängt hatte. Darunter Shining Charizard. Läuft im Hintergrund, damit ein
    langsamer Netzabruf den Start nicht aufhält."""
    if not ids:
        return
    try:
        with httpx.Client(timeout=40, headers=UA) as client:
            en_series = {}
            try:
                en_series = {x["id"]: x["name"] for x in client.get(f"{TCGDEX}/en/series").json()}
            except Exception:
                pass
            con = get_db()
            neu_karten = 0
            for sid in ids:
                de = en = None
                for lang, ziel in (("de", "de"), ("en", "en")):
                    try:
                        r = client.get(f"{TCGDEX}/{lang}/sets/{sid}")
                        if r.status_code == 200:
                            if ziel == "de":
                                de = r.json()
                            else:
                                en = r.json()
                    except Exception:
                        pass
                haupt = de or en
                if not haupt:
                    continue
                serie = haupt.get("serie") or {}
                cc = haupt.get("cardCount") or {}
                con.execute(
                    "INSERT INTO sets (id,name,serie_id,serie_name,release_date,total,official,"
                    "symbol,name_en,serie_name_en,region) VALUES (?,?,?,?,?,?,?,?,?,?,'intl')"
                    " ON CONFLICT(id) DO UPDATE SET name=excluded.name, serie_id=excluded.serie_id,"
                    " serie_name=excluded.serie_name, release_date=excluded.release_date,"
                    " total=excluded.total, official=excluded.official,"
                    " symbol=COALESCE(excluded.symbol, sets.symbol), name_en=excluded.name_en,"
                    " serie_name_en=excluded.serie_name_en, region='intl'",
                    (sid, haupt.get("name"), serie.get("id"), serie.get("name"),
                     haupt.get("releaseDate"), cc.get("total"), cc.get("official"),
                     haupt.get("symbol"), (en or haupt).get("name"),
                     en_series.get(serie.get("id")) or serie.get("name")))
                de_karten = {c["id"]: c for c in (de or {}).get("cards") or []}
                da = {r["id"] for r in con.execute(
                    "SELECT id FROM cards WHERE set_id = ? AND COALESCE(region,'intl') = 'intl'", (sid,))}
                for c in (en or haupt).get("cards") or []:
                    if c["id"] in da:
                        continue
                    d = {}
                    try:
                        rr = client.get(f"{TCGDEX}/en/cards/{c['id']}")
                        if rr.status_code == 200:
                            d = rr.json()
                    except Exception:
                        pass
                    dex = d.get("dexId") or []
                    v = d.get("variants") or {}
                    dek = de_karten.get(c["id"], {})
                    con.execute(
                        "INSERT OR IGNORE INTO cards (id,set_id,local_id,local_num,name_de,name_en,"
                        "image_de,image_en,category,rarity,stage,suffix,kind,kinds,dex_ids,first_dex,"
                        "types,has_normal,has_reverse,has_holo,has_first,illustrator,hp,release_date,region)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'intl')",
                        (c["id"], sid, c.get("localId"), _local_num(c.get("localId")),
                         dek.get("name"), c.get("name"), dek.get("image"), c.get("image"),
                         d.get("category"), d.get("rarity"), d.get("stage"), d.get("suffix"),
                         _card_kind(d.get("category"), d.get("stage"), d.get("suffix"), c.get("name")),
                         json.dumps(_compute_kinds(d.get("category"), d.get("stage"), d.get("suffix"),
                                                   d.get("rarity"), c.get("name"), dek.get("name"),
                                                   c.get("localId") or "")),
                         json.dumps(dex), dex[0] if dex else None, json.dumps(d.get("types") or []),
                         1 if v.get("normal") else 0, 1 if v.get("reverse") else 0,
                         1 if v.get("holo") else 0, 1 if v.get("firstEdition") else 0,
                         d.get("illustrator"), d.get("hp"), haupt.get("releaseDate")))
                    neu_karten += 1
                con.commit()
            con.close()
            _log(f"Internationale Sets wiederhergestellt: {ids}, {neu_karten} Karten nachgeladen")
    except Exception as exc:
        _log("Set-Reparatur fehlgeschlagen:", exc)


_jp_kollision_umbenennen()


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
                # ON CONFLICT statt REPLACE: REPLACE löscht die Zeile und legt sie neu an,
                # dabei fielen symbol_alt und region auf ihren Standard zurück.
                "INSERT INTO sets (id,name,serie_id,serie_name,release_date,total,official,symbol,name_en,serie_name_en)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET name=excluded.name, serie_id=excluded.serie_id,"
                " serie_name=excluded.serie_name, release_date=excluded.release_date,"
                " total=excluded.total, official=excluded.official,"
                " symbol=COALESCE(excluded.symbol, sets.symbol), name_en=excluded.name_en,"
                " serie_name_en=excluded.serie_name_en"
                " WHERE COALESCE(sets.region,'intl') = 'intl'",
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
                # Nur die Spalten anfassen, die aus diesem Abruf stammen. Mit REPLACE waren nach
                # jedem Sync Illustrator, HP, Regulation Mark, Trainer-Typ, Erstauflage,
                # Ersatzbild und der japanische Name leer — die Filter darauf liefen ins Nichts.
                "INSERT INTO cards (id,set_id,local_id,local_num,name_de,name_en,"
                "image_de,image_en,category,rarity,stage,suffix,kind,dex_ids,first_dex,types,"
                "has_normal,has_reverse,has_holo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET set_id=excluded.set_id, local_id=excluded.local_id,"
                " local_num=excluded.local_num, name_de=excluded.name_de, name_en=excluded.name_en,"
                " image_de=excluded.image_de, image_en=excluded.image_en, category=excluded.category,"
                " rarity=excluded.rarity, stage=excluded.stage, suffix=excluded.suffix,"
                " kind=excluded.kind, dex_ids=excluded.dex_ids, first_dex=excluded.first_dex,"
                " types=excluded.types, has_normal=excluded.has_normal,"
                " has_reverse=excluded.has_reverse, has_holo=excluded.has_holo"
                # Gegenstück zum Präfix: der internationale Sync fasst japanische Zeilen nicht an.
                " WHERE COALESCE(cards.region,'intl') = 'intl'",
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
            "INSERT INTO cards (id,set_id,local_id,local_num,name_de,image_de,kind)"
            " VALUES (?,?,?,?,?,?, 'pokemon')"
            " ON CONFLICT(id) DO UPDATE SET set_id=excluded.set_id, local_id=excluded.local_id,"
            " local_num=excluded.local_num, name_de=excluded.name_de, image_de=excluded.image_de",
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
            # Kennungen, die es international auch gibt, bekommen den Präfix. Gemessen,
            # nicht gepflegt: ein neues Set fällt damit von selbst richtig herum.
            intl = {r["id"] for r in con.execute("SELECT id FROM sets WHERE region = 'intl'")}
            kollision = {x["id"] for x in sets if x["id"] in intl}
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
                    sid = jp_id(d["id"], kollision)
                    # ON CONFLICT statt REPLACE, und nur auf japanischen Zeilen: REPLACE hat
                    # 2026 die vier Neo-Sets des internationalen Katalogs überschrieben.
                    con.execute(
                        "INSERT INTO sets (id,name,serie_id,serie_name,release_date,total,official,symbol,name_en,serie_name_en,region)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,'jp')"
                        " ON CONFLICT(id) DO UPDATE SET name=excluded.name, serie_id=excluded.serie_id,"
                        " serie_name=excluded.serie_name, release_date=excluded.release_date,"
                        " total=excluded.total, official=excluded.official,"
                        " symbol=COALESCE(excluded.symbol, sets.symbol),"
                        # Der englische Setname kommt aus dem Cardmarket-Katalog und darf
                        # nicht bei jedem Japan-Sync wieder auf den japanischen zurückfallen.
                        " name_en=CASE WHEN sets.name_en IS NULL OR sets.name_en = sets.name"
                        "   THEN excluded.name_en ELSE sets.name_en END,"
                        " serie_name_en=excluded.serie_name_en"
                        " WHERE sets.region = 'jp'",
                        (sid, d.get("name"), serie.get("id"), serie.get("name"), d.get("releaseDate"),
                         cc.get("total"), cc.get("official"), d.get("symbol"), d.get("name"), serie.get("name")))
                    for c in d.get("cards") or []:
                        dex = _ja_dex_fuer_name(c.get("name"), namen)
                        kinds = _ja_kinds(c.get("name"))
                        con.execute(
                            "INSERT INTO cards (id,set_id,local_id,local_num,name_de,name_en,name_ja,image_de,image_en,"
                            "category,kind,kinds,dex_ids,first_dex,types,release_date,region)"
                            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'jp')"
                            " ON CONFLICT(id) DO UPDATE SET set_id=excluded.set_id,"
                            " local_id=excluded.local_id, local_num=excluded.local_num,"
                            " name_ja=excluded.name_ja, image_en=excluded.image_en,"
                            " category=excluded.category, kind=excluded.kind, kinds=excluded.kinds,"
                            " dex_ids=excluded.dex_ids, first_dex=excluded.first_dex,"
                            " release_date=excluded.release_date"
                            " WHERE cards.region = 'jp'",
                            (jp_id(c["id"], kollision), sid, c.get("localId"), _local_num(c.get("localId")),
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


def jp_quell_id(card_id):
    """Für den Abruf bei TCGdex zählt die Kennung ohne unseren Präfix."""
    return card_id[len(JP_PRAEFIX):] if card_id.startswith(JP_PRAEFIX) else card_id


def run_backfill_ja_details():
    """Japan: Einzelkarten-Details (Künstler, Seltenheit, Pokédex, HP, Regulation Mark, Varianten) nachladen –
    die ja-Listen liefern das nicht, die Einzelabfrage schon. ~12.800 Anfragen, gedrosselt, im Hintergrund."""
    if not _sync_lock.acquire(blocking=False):
        return
    import time as _time
    con = get_db()
    try:
        ids = [r["id"] for r in con.execute("SELECT id FROM cards WHERE region='jp' AND rarity IS NULL ORDER BY release_date DESC")]
        SYNC.update(running=True, error=None, step="Japan: Kartendetails", done=0, total=len(ids))
        namen = {r["dex_id"]: (r["name_de"], r["name_en"]) for r in con.execute("SELECT dex_id, name_de, name_en FROM pokemon")}

        def fetch(cid):
            for versuch in range(3):
                try:
                    with httpx.Client(timeout=30, headers=UA) as client:
                        r = client.get(f"{TCGDEX}/ja/cards/{jp_quell_id(cid)}")
                    if r.status_code == 200:
                        return cid, r.json()
                    if r.status_code == 404:
                        return cid, {}
                except Exception:
                    pass
                _time.sleep(1.5 * (versuch + 1))
            return cid, None

        with ThreadPoolExecutor(4) as pool:
            for cid, d in pool.map(fetch, ids):
                SYNC["done"] += 1
                if not d:
                    if d == {}:
                        con.execute("UPDATE cards SET rarity='None' WHERE id=?", (cid,))
                    continue
                dex = d.get("dexId") or []
                v = d.get("variants") or {}
                kinds = _compute_kinds(d.get("category"), d.get("stage"), d.get("suffix"), d.get("rarity"),
                                       d.get("name"), None, d.get("localId"))
                nde, nen = namen.get(dex[0], (None, None)) if dex else (None, None)
                con.execute(
                    "UPDATE cards SET category=?, rarity=?, stage=?, suffix=?, illustrator=?, hp=?, regulation_mark=?, types=?,"
                    " dex_ids=?, first_dex=COALESCE(?, first_dex), name_de=COALESCE(?, name_de), name_en=COALESCE(?, name_en),"
                    " has_normal=?, has_reverse=?, has_holo=?, has_first=?, kinds=?, kind=?, trainer_type=?, energy_type=?,"
                    " image_en=COALESCE(image_en, ?) WHERE id=?",
                    (d.get("category"), d.get("rarity") or "None", d.get("stage"), d.get("suffix"), _norm_illustrator(d.get("illustrator")),
                     d.get("hp"), d.get("regulationMark"), json.dumps(d.get("types") or []),
                     json.dumps(dex), dex[0] if dex else None, nde, nen,
                     1 if v.get("normal", True) else 0, 1 if v.get("reverse") else 0, 1 if v.get("holo") else 0, 1 if v.get("firstEdition") else 0,
                     json.dumps(kinds), kinds[0], d.get("trainerType"), d.get("energyType"), d.get("image"), cid))
                if SYNC["done"] % 200 == 0:
                    con.commit()
        con.commit()
        con.execute("INSERT OR REPLACE INTO kv (key,value) VALUES ('ja_details', datetime('now'))")
        con.commit()
        SYNC["step"] = "fertig"
    except Exception as exc:
        SYNC["error"] = str(exc)[:500]
    finally:
        SYNC["running"] = False
        con.close()
        _sync_lock.release()


@app.post("/api/admin/backfill_ja")
def admin_backfill_ja(key: str = ""):
    if not _admin_key() or key != _admin_key():
        raise HTTPException(403, "Falscher Schlüssel")
    threading.Thread(target=run_backfill_ja_details, daemon=True).start()
    return {"gestartet": True}


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


# Gemessen: 8.500 Karten in 60 s, also rund 140 je Sekunde. Der ganze bepreiste Katalog
# läuft damit in gut zwei Minuten durch — deshalb wird täglich alles aufgefrischt statt
# einen Teil zu rotieren. Das ist nicht nur bequemer, es ist die Voraussetzung dafür, dass
# die Auswertungen überhaupt etwas aussagen: Index und Bewegungsliste vergleichen dieselbe
# Karte an zwei Tagen. Bei rotierender Auffrischung hätte jede Karte nur alle acht Tage
# einen Messpunkt, und keine zwei Karten dieselben zwei Tage.
PREIS_TAGESLAUF = 40000      # obere Schranke; deckt den ganzen bepreisten Katalog ab
PREIS_STAPEL = 6000          # Erstbefüllung: so viele noch unbepreiste Karten je Lauf


# --- Preisverlauf: nur Bewegungen speichern ---------------------------------
#
# Eine Zeile je Karte und Tag klingt harmlos und ist es nicht: 30.600 Karten mal 365 Tage
# sind 11 Millionen Zeilen, rund 840 MB — und jedes nächtliche Backup trägt sie mit.
# Gemessen am 02. gegen den 03.09.2026: von 27.995 Preisen waren 16.631 Cent für Cent
# dieselben, nur 10.315 hatten sich um mehr als ein Prozent bewegt. Gespeichert wird
# deshalb nur, was sich bewegt hat; ein Preis gilt fort, bis der nächste Eintrag kommt.
# Jeder Leser der Reihe muss ihn deshalb fortschreiben (siehe `_historie_stand`).
HIST_SCHWELLE = 0.01        # kleinste Bewegung in Euro, die eine Zeile wert ist
HIST_ANTEIL = 0.01          # … oder ein Prozent vom alten Preis, je nachdem was größer ist
HIST_ANKER_TAGE = 30        # spätestens so oft bekommt jede Karte wieder einen Punkt
HIST_TAEGLICH = 60          # bis hierher bleibt jeder Punkt stehen
HIST_WOECHENTLICH = 180     # danach einer je Woche, dahinter einer je Monat


def _tag_minus(tage, ab=None):
    d = datetime.date.fromisoformat(ab or _heute()) - datetime.timedelta(days=tage)
    return d.isoformat()


def _spuerbar(alt, neu):
    """Ist die Bewegung von `alt` nach `neu` eine Zeile wert?"""
    if neu is None:
        return False                      # ein fehlender Wert löscht keinen vorhandenen
    if alt is None:
        return True                       # von „kein Preis" zu „Preis" ist immer eine
    return abs(neu - alt) >= max(HIST_SCHWELLE, abs(alt) * HIST_ANTEIL)


def _historie_stand(con, bis, ids=None):
    """Der letzte gespeicherte Preis je Karte bis einschließlich `bis`.

    → {card_id: (eur, usd, datum)}. Das ist der Ersatz für „nimm die Zeile von Tag X":
    seit die Historie nur Bewegungen führt, hat nicht jede Karte an jedem Tag eine Zeile,
    und der zuletzt gemeldete Preis ist der, der an diesem Tag galt."""
    bed, args = "", (bis,)
    if ids is not None:
        marken = ",".join("?" * len(ids))
        bed = f" AND card_id IN ({marken})"
        args = (bis, *ids)
    return {r["card_id"]: (r["eur"], r["usd"], r["datum"]) for r in con.execute(
        "SELECT card_id, eur, usd, datum FROM (SELECT card_id, eur, usd, datum,"
        " ROW_NUMBER() OVER (PARTITION BY card_id ORDER BY datum DESC) rn"
        f" FROM price_history WHERE datum <= ?{bed}) WHERE rn = 1", args)}


def _historie_eintragen(con, werte, quelle="", messtag=True, ids=None):
    """Den heutigen Punkt setzen, wo er etwas Neues sagt.

    `werte` ist eine Liste (card_id, eur, usd). Sagt der Preis dasselbe wie zuletzt, wird
    keine Zeile geschrieben — und eine, die ein früherer Lauf desselben Tages aus einer
    schwächeren Quelle gesetzt hat, wieder weggeräumt. So steht je Tag höchstens ein
    Punkt je Karte, und der stammt von der besten Quelle des Tages."""
    heute = _heute()
    stand = _historie_stand(con, _tag_minus(1, heute), ids)
    anker = _tag_minus(HIST_ANKER_TAGE, heute)
    heute_hat = {r["card_id"] for r in con.execute(
        "SELECT card_id FROM price_history WHERE datum = ?", (heute,))}
    n = 0
    for cid, eur, usd in werte:
        if eur is None and usd is None:
            continue
        alt = stand.get(cid)
        if alt and alt[2] > anker and not _spuerbar(alt[0], eur) and not _spuerbar(alt[1], usd):
            if cid in heute_hat:
                con.execute("DELETE FROM price_history WHERE card_id = ? AND datum = ?",
                            (cid, heute))
            continue
        con.execute("INSERT INTO price_history (card_id, datum, eur, usd) VALUES (?,?,?,?)"
                    " ON CONFLICT(card_id, datum) DO UPDATE SET"
                    " eur=COALESCE(excluded.eur, price_history.eur),"
                    " usd=COALESCE(excluded.usd, price_history.usd)",
                    (cid, heute, eur, usd))
        n += 1
    if messtag:
        _messtag_merken(con, len(werte), quelle)
    return n


def _messtag_merken(con, karten, quelle=""):
    """Festhalten, dass heute gemessen wurde und über wie viele Karten.

    Ohne diese Zeile wäre ein ausgefallener Lauf von einem ruhigen Markt nicht zu
    unterscheiden — beide hinterlassen keine Bewegung."""
    con.execute("INSERT INTO price_tage (datum, karten, quelle) VALUES (?,?,?)"
                " ON CONFLICT(datum) DO UPDATE SET karten = MAX(price_tage.karten, excluded.karten),"
                " quelle = excluded.quelle",
                (_heute(), int(karten or 0), quelle or None))


def _historie_verdichten(con):
    """Alte Punkte ausdünnen.

    Für den Verlauf einer Karte braucht das vergangene Jahr keine Tagesauflösung. Älter
    als 60 Tage bleibt je Woche der letzte Punkt stehen, älter als 180 Tage je Monat
    einer. Die Kurve behält ihre Form, die Tabelle bleibt klein — und weil jeder Leser
    ohnehin fortschreibt, ändert das an keiner Auswertung etwas.

    Damit läuft die Tabelle auf gut 1,5 Millionen Zeilen zu (rund 120 MB) und wächst
    danach nur noch um etwa 30 MB im Jahr, statt um 840 MB — und jedes nächtliche
    Backup wird um denselben Betrag kleiner."""
    g_tag = _tag_minus(HIST_TAEGLICH)
    g_woche = _tag_minus(HIST_WOECHENTLICH)
    weg = con.execute(
        "DELETE FROM price_history WHERE datum < ? AND datum >= ? AND datum NOT IN"
        " (SELECT MAX(p2.datum) FROM price_history p2 WHERE p2.card_id = price_history.card_id"
        "  AND strftime('%Y-%W', p2.datum) = strftime('%Y-%W', price_history.datum))",
        (g_tag, g_woche)).rowcount
    weg += con.execute(
        "DELETE FROM price_history WHERE datum < ? AND datum NOT IN"
        " (SELECT MAX(p2.datum) FROM price_history p2 WHERE p2.card_id = price_history.card_id"
        "  AND strftime('%Y-%m', p2.datum) = strftime('%Y-%m', price_history.datum))",
        (g_woche,)).rowcount
    con.commit()
    if weg:
        _log(f"Preisverlauf verdichtet: {weg} Punkte zusammengefasst")
    return weg


def _preis_schreiben(con, reihen):
    """Ergebnisse eines Laufs ablegen. Fehlgeschlagene Abrufe werden übergangen —
    der alte Preis bleibt lieber stehen, als durch einen Ausfall gelöscht zu werden.

    **Der Euro-Preis dieser Quelle gilt nur, wo das offizielle Preisverzeichnis nichts
    sagt.** TCGdex reicht Cardmarket-Trends aus zweiter Hand weiter und hängt dabei
    gleichnamige Karten an dasselbe Produkt; seit die Datei von Cardmarket selbst kommt,
    wäre es ein Rückschritt, sie jede Nacht mit der schwächeren Quelle zu überschreiben
    und erst im nächsten Schritt wieder zu berichtigen. Fällt der Import einmal aus,
    friert der Preis lieber ein (`cm_import_am` zeigt, wie alt er ist), als still auf die
    zweite Wahl zurückzufallen. Nach einer Woche ohne Datei übernimmt TCGdex wieder.
    Der amerikanische Preis kommt weiterhin nur von hier — Cardmarket führt keinen."""
    heute = _heute()
    aus_datei = {r["card_id"] for r in con.execute(
        "SELECT card_id FROM card_prices WHERE cm_import_am >= ?", (_tag_minus(7, heute),))}
    geschrieben = 0
    verlauf = []
    for e in reihen:
        if not e["ok"]:
            continue
        geschrieben += 1
        sp = e.get("spanne") or {}
        eigene_zahl = e["id"] not in aus_datei
        con.execute(
            "INSERT INTO card_prices (card_id, eur, eur_holo, usd, usd_holo, tcgplayer_id,"
            " cm_produkt, eur_low, eur_avg30, usd_low, usd_mid, usd_high, preise_json,"
            " tp_keys, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))"
            " ON CONFLICT(card_id) DO UPDATE SET"
            " eur=CASE WHEN ? THEN excluded.eur ELSE card_prices.eur END,"
            " eur_holo=CASE WHEN ? THEN excluded.eur_holo ELSE card_prices.eur_holo END,"
            " eur_low=CASE WHEN ? THEN excluded.eur_low ELSE card_prices.eur_low END,"
            " eur_avg30=CASE WHEN ? THEN excluded.eur_avg30 ELSE card_prices.eur_avg30 END,"
            " usd=COALESCE(excluded.usd, card_prices.usd),"
            " usd_holo=COALESCE(excluded.usd_holo, card_prices.usd_holo),"
            " tcgplayer_id=COALESCE(excluded.tcgplayer_id, card_prices.tcgplayer_id),"
            # Eine von Hand oder über den Katalog berichtigte Produktnummer bleibt stehen.
            # Vorher setzte jeder Nachtlauf die Zuordnung von TCGdex zurück, und der
            # Import musste dieselbe Arbeit jede Nacht neu machen.
            " cm_produkt=CASE WHEN card_prices.cm_quelle IS NULL THEN excluded.cm_produkt"
            "  ELSE card_prices.cm_produkt END,"
            " usd_low=excluded.usd_low,"
            " usd_mid=excluded.usd_mid, usd_high=excluded.usd_high,"
            " preise_json=excluded.preise_json, tp_keys=excluded.tp_keys,"
            " updated_at=excluded.updated_at",
            (e["id"], e["eur"], e["eur_holo"], e["usd"], e["usd_holo"], e["tcgplayer_id"],
             e["cm_produkt"], sp.get("eur_low"), sp.get("eur_avg30"), sp.get("usd_low"),
             sp.get("usd_mid"), sp.get("usd_high"),
             json.dumps(e.get("varianten") or {}) if e.get("varianten") else None,
             ",".join(e.get("tp_keys") or []) or None,
             eigene_zahl, eigene_zahl, eigene_zahl, eigene_zahl))
        verlauf.append((e["id"], e["eur"] if eigene_zahl else None, e["usd"]))
    _historie_eintragen(con, verlauf, "tcgdex")
    return geschrieben


def _mehrdeutige_produkte(reihen):
    """Karten aussortieren, deren Cardmarket-Produkt noch anderen Karten gehört.

    TCGdex ordnet gleichnamige Karten desselben Sets gern demselben Produkt zu — die
    vier Mewtwo/Mew-Promos aus „XY Black Star Promos" hingen alle an Produkt 554275 und
    trugen deshalb denselben Preis von 5.550 €, während die eigentliche Karte bei knapp
    170 € steht. Welche der Karten den Preis zu Recht trägt, lässt sich aus dieser Quelle
    nicht entscheiden, also bekommt keine einen: eine leere Stelle ist ehrlich, eine
    falsche Zahl nicht. Der amerikanische Preis bleibt davon unberührt — er hängt an einer
    eigenen Produktnummer je Karte und ist die Grundlage der Schätzung.

    → Menge der Karten-IDs, deren Europreis verworfen werden muss."""
    nach_produkt = {}
    for e in reihen:
        if e["ok"] and e["cm_produkt"] is not None and e["eur"] is not None:
            nach_produkt.setdefault(e["cm_produkt"], []).append(e["id"])
    raus = set()
    for karten in nach_produkt.values():
        if len(karten) > 1:
            raus.update(karten)
    return raus


def _varianten_schreiben(con, reihen):
    """Die gehandelten TCGplayer-Varianten in `cards` übernehmen.

    **Ersetzen, nicht vereinigen.** Die erste Fassung hat vereinigt — eine Ausprägung galt,
    wenn der Katalog *oder* die Börse sie kannte. Das konnte nur hinzufügen und nie
    berichtigen, und genau die falschen Behauptungen des Katalogs blieben stehen: Guardevoir
    LV.X aus Rätselhafte Wunder stand mit `normal=1` da, obwohl es die Karte nur als Lv.X
    gibt, und trug deshalb neben ihrem Preis von 42,50 € noch einen „Holo-Trend" von
    10,09 €, der zu nichts gehörte.

    Wo TCGplayer eine Antwort hat, gilt sie: was dort gehandelt wird, gibt es. Der Katalog
    darf nur noch ergänzen, was die Börse gar nicht führen kann — die Erstauflage. Karten
    ohne TCGplayer-Schlüssel (japanische, sehr alte) bleiben unangetastet.

    Sammlungseinträge werden nie angefasst — wer eine Ausprägung erfasst hat, behält sie,
    sie wird nur nicht mehr neu angeboten."""
    geaendert = 0
    for e in reihen:
        if not e["ok"] or not e.get("tp_keys"):
            continue
        row = con.execute("SELECT has_normal, has_reverse, has_holo, has_first, release_date,"
                          " set_id FROM cards WHERE id = ?", (e["id"],)).fetchone()
        if not row:
            continue
        tp, bekannt = varianten_aus_tp(e["tp_keys"])
        if not bekannt or not any(tp.values()):
            continue          # unbekannte oder leere Schlüssel: lieber gar nichts ändern
        neu = {
            "normal": 1 if tp["normal"] else 0,
            "reverse": 1 if tp["reverse"] else 0,
            "holo": 1 if tp["holo"] else 0,
            # Die Erstauflage ist der einzige Fall, in dem der Katalog mehr weiß: TCGplayer
            # führt sie nur, wenn gerade jemand eine anbietet.
            "first": 1 if (row["has_first"] or tp["first"]) else 0,
        }
        if (row["release_date"] or "9999") < REVERSE_AB and row["set_id"] not in PROMO_SETS:
            neu["reverse"] = 0
        if (neu["normal"], neu["reverse"], neu["holo"], neu["first"]) == (
                row["has_normal"], row["has_reverse"], row["has_holo"], row["has_first"]):
            continue
        con.execute("UPDATE cards SET has_normal=?, has_reverse=?, has_holo=?, has_first=?"
                    " WHERE id = ?",
                    (neu["normal"], neu["reverse"], neu["holo"], neu["first"], e["id"]))
        geaendert += 1
    return geaendert


# Abstand vom Median-Wechselkurs. Ein großer Abstand hat zwei mögliche Ursachen, und von
# außen sind sie nicht zu trennen: ein echter Marktunterschied (alte Karten gehen in den USA
# regelmäßig zum Mehrfachen weg — Guardevoir LV.X steht bei 42,50 € und 207,91 $) oder eine
# falsche Produktzuordnung. Die Schwelle lag zuerst bei 3 und traf damit vor allem den
# ersten Fall; die Oberfläche nennt den Abstand deshalb nur noch als Zahl, statt den Preis
# in Frage zu stellen. Erst ab dem Zehnfachen ist ein Marktunterschied nicht mehr plausibel.
KURS_UNSICHER = 5.0          # Zahl zeigen, Abstand danebenschreiben
KURS_GESPERRT = 10.0         # gar keine Zahl mehr


# Ein einziger Wechselkurs für den ganzen Katalog trägt nicht. Gemessen an 7.743
# Kartenpaaren liegt der Median bei 1,32 USD/EUR — aber nach Jahrzehnt und Preisklasse
# aufgeschlüsselt reicht die Spanne von 0,97 bis 2,67: alte Karten im mittleren Preisband
# gehen in den USA regelmäßig zum Zweieinhalbfachen weg, moderne fast zum Gleichstand.
# Wer einen fehlenden Europreis aus dem US-Preis rechnet, muss den passenden Kurs nehmen.
USD_KLASSEN = ((3, "<3"), (15, "3-15"), (60, "15-60"), (250, "60-250"), (10 ** 9, ">250"))
KURS_MIN_PAARE = 25          # darunter ist der Median Zufall


def _usd_klasse(usd):
    for grenze, name in USD_KLASSEN:
        if (usd or 0) < grenze:
            return name
    return ">250"


def _jahrzehnt(datum):
    try:
        return (int(str(datum)[:4]) // 10) * 10
    except Exception:
        return 2020


def _kurstabelle(con):
    """→ {(Jahrzehnt, US-Klasse): Median-Kurs}. Wird bei jedem Preislauf neu gemessen."""
    eimer = {}
    for r in con.execute("SELECT c.release_date, p.eur, p.usd FROM card_prices p"
                         " JOIN cards c ON c.id = p.card_id"
                         " WHERE p.eur > 0.5 AND p.usd > 0.5"
                         " AND COALESCE(p.status,'') <> 'gesperrt'"):
        eimer.setdefault((_jahrzehnt(r["release_date"]), _usd_klasse(r["usd"])), []).append(
            r["usd"] / r["eur"])
    aus = {}
    for schluessel, werte in eimer.items():
        if len(werte) >= KURS_MIN_PAARE:
            werte.sort()
            aus[schluessel] = round(werte[len(werte) // 2], 3)
    return aus


def kurs_fuer(tabelle, datum, usd, standard):
    """Der passende Kurs, mit Rückfall auf das Jahrzehnt und zuletzt auf den Gesamtmedian."""
    if not tabelle:
        return standard
    jz, kl = _jahrzehnt(datum), _usd_klasse(usd)
    if (jz, kl) in tabelle:
        return tabelle[(jz, kl)]
    nachbarn = [v for (j, _k), v in tabelle.items() if j == jz]
    if nachbarn:
        nachbarn.sort()
        return nachbarn[len(nachbarn) // 2]
    return standard


def _kurs_pruefen(con):
    """Beide Börsen gegeneinander stellen und je Karte ein Urteil ablegen.

    Der Wechselkurs wird nicht gesetzt, sondern gemessen: Median über alle Karten, die
    in beiden Märkten einen Preis haben. So wandert er mit, ohne dass ihn jemand pflegt.
    Weicht eine einzelne Karte weit davon ab, ist nicht der Kurs falsch, sondern eine der
    beiden Zuordnungen — am 03.09.2026 betraf das 27,3 % aller Kartenpaare.

    → (Median-Kurs, {status: Anzahl})"""
    paare = [(r["card_id"], r["usd"] / r["eur"]) for r in con.execute(
        "SELECT card_id, eur, usd FROM card_prices WHERE eur > 0.5 AND usd > 0.5")]
    if len(paare) < 200:
        return None, {}
    werte = sorted(v for _, v in paare)
    median = werte[len(werte) // 2]

    # Nur die eigenen Urteile zurücknehmen. Der erste Entwurf löschte auch die Zahlen der
    # Zweitquelle mit — die stammen aus einem anderen Lauf und gehören dieser Prüfung nicht.
    con.execute("UPDATE card_prices SET status = NULL, kurs = NULL"
                " WHERE status IS NULL OR status <> 'zweitquelle'")
    con.execute("UPDATE card_prices SET eur_geschaetzt = NULL"
                " WHERE eur_geschaetzt IS NOT NULL AND COALESCE(status,'') <> 'zweitquelle'")
    zaehler = {}

    def setze(cid, status, kurs=None, geschaetzt=None):
        con.execute("UPDATE card_prices SET status=?, kurs=?, eur_geschaetzt=? WHERE card_id=?",
                    (status, kurs, geschaetzt, cid))
        zaehler[status] = zaehler.get(status, 0) + 1

    for cid, kurs in paare:
        abstand = max(kurs / median, median / kurs)
        if abstand > KURS_GESPERRT:
            setze(cid, "gesperrt", round(kurs, 2))
        elif abstand > KURS_UNSICHER:
            setze(cid, "unsicher", round(kurs, 2))

    # Karten ohne Europreis, aber mit amerikanischem: umgerechnet ist besser als leer,
    # solange danebensteht, dass es eine Umrechnung ist. Gerechnet wird mit dem Kurs, der
    # zu Alter und Preisklasse der Karte passt — der Gesamtmedian lag bei alten Karten um
    # den Faktor zwei daneben.
    tabelle = _kurstabelle(con)
    con.execute("INSERT OR REPLACE INTO kv (key,value) VALUES ('preis_kurstabelle', ?)",
                (json.dumps({f"{j}|{k}": v for (j, k), v in tabelle.items()}),))
    for r in con.execute("SELECT p.card_id, p.usd, c.release_date FROM card_prices p"
                         " JOIN cards c ON c.id = p.card_id"
                         " WHERE p.eur IS NULL AND p.usd > 0"
                         " AND COALESCE(p.status,'') <> 'zweitquelle'").fetchall():
        k = kurs_fuer(tabelle, r["release_date"], r["usd"], median)
        setze(r["card_id"], "geschaetzt", round(k, 2), round(r["usd"] / k, 2))
    return round(median, 4), zaehler


def _preishistorie_job():
    """Preise erfassen und auffrischen.

    Vorher lief das nur über Karten, die schon einmal jemand angesehen hatte — dadurch
    kannte die Datenbank 649 von 33.700 Karten und jede Marktaussage stand auf Sand.
    Jetzt holt jeder Lauf zuerst Karten ohne Preis dazu (der Katalog ist nach etwa zehn
    Läufen vollständig) und frischt danach die ältesten Einträge auf.

    Seit dem 03.09.2026 fällt im selben Durchgang zweierlei mit ab, ohne einen einzigen
    zusätzlichen Netzaufruf: die tatsächlich gehandelten Druckvarianten und der Vergleich
    beider Börsen."""
    con = get_db()
    # Auch japanische Karten: die Annahme, Cardmarket führe sie nicht, war falsch — es gibt
    # dort für praktisch jede japanische Karte ein Produkt mit Trendpreis (nur keine
    # TCGplayer-Preise, das ist ein rein amerikanischer Markt).
    neue = [(r["id"], r["region"] or "intl") for r in con.execute(
        "SELECT c.id, c.region FROM cards c LEFT JOIN card_prices p ON p.card_id = c.id"
        " WHERE p.card_id IS NULL"
        " ORDER BY c.release_date DESC LIMIT ?", (PREIS_STAPEL,))]
    # Solange noch erfasst wird, hat das Vorrang — sonst der ganze bepreiste Katalog.
    alt = [] if neue else [(r["card_id"], r["region"] or "intl") for r in con.execute(
        "SELECT p.card_id, c.region FROM card_prices p LEFT JOIN cards c ON c.id = p.card_id"
        " ORDER BY p.updated_at LIMIT ?", (PREIS_TAGESLAUF,))]
    offen = con.execute(
        "SELECT COUNT(*) c FROM cards c LEFT JOIN card_prices p ON p.card_id = c.id"
        " WHERE p.card_id IS NULL").fetchone()["c"]
    con.close()

    ids = neue + alt
    if not ids:
        return
    with httpx.Client(timeout=20, headers=UA) as client:
        with ThreadPoolExecutor(6) as pool:
            ergebnisse = list(pool.map(lambda x: _fetch_price_voll(client, x[0], x[1]), ids))

    # Ein paar Ausfälle sind normal (Zeitüberschreitung, einzelne 404). Fällt aber ein
    # Fünftel aus, ist die Quelle selbst gestört; dann wird gar nichts geschrieben,
    # sonst wandert der Ausfall als Datenstand in die Historie.
    fehl = sum(1 for e in ergebnisse if not e["ok"])
    if fehl > len(ergebnisse) * 0.2:
        _log(f"Preislauf abgebrochen: {fehl} von {len(ergebnisse)} Abrufen fehlgeschlagen")
        return

    # Erst die Ausprägungen berichtigen, dann entscheiden, ob es überhaupt eine zweite
    # Ausgabe gibt. Die frühere Reihenfolge las den alten Stand und schob den falschen
    # Holo-Preis noch einen Lauf weiter.
    con = get_db()
    var_neu = _varianten_schreiben(con, ergebnisse)
    for e in ergebnisse:
        if e.get("merkmale"):
            con.execute("UPDATE cards SET merkmale = ? WHERE id = ?", (e["merkmale"], e["id"]))
    con.commit()

    # Cardmarket führt neben dem Trend eine zweite Reihe für die Reverse-/Holo-Ausgabe.
    # Sie gehört zu einer *zweiten* Ausgabe derselben Karte. Gibt es die nicht — weil die
    # Karte nur als Holo existiert (Base-Set-Glurak, jede LV.X) oder nur als Normaldruck —,
    # gehört diese Zahl zu nichts.
    ohne_zweite = {r["id"] for r in con.execute(
        "SELECT id FROM cards WHERE NOT (COALESCE(has_normal,0) = 1"
        " AND (COALESCE(has_reverse,0) = 1 OR COALESCE(has_holo,0) = 1))")}
    for e in ergebnisse:
        if e["id"] in ohne_zweite:
            e["eur_holo"] = None

    # Preise verwerfen, die an einem mehrfach vergebenen Cardmarket-Produkt hängen.
    mehrdeutig = _mehrdeutige_produkte(ergebnisse)
    for e in ergebnisse:
        if e["id"] in mehrdeutig:
            e["eur"] = None
            e["eur_holo"] = None
            # Gehört das Produkt nicht dieser Karte, gehört auch sein Tiefstpreis nicht
            # dazu. Vorher stand bei solchen Karten „ab 0,50 €" ohne Trend darüber.
            sp = e.get("spanne") or {}
            sp["eur_low"] = None
            sp["eur_avg30"] = None

    geschrieben = _preis_schreiben(con, ergebnisse)
    con.commit()
    kurs, urteile = _kurs_pruefen(con)
    con.execute("INSERT OR REPLACE INTO kv (key,value) VALUES ('preishistorie_lauf', datetime('now'))")
    con.execute("INSERT OR REPLACE INTO kv (key,value) VALUES ('preise_offen', ?)",
                (str(max(0, offen - len(neue))),))
    if kurs:
        con.execute("INSERT OR REPLACE INTO kv (key,value) VALUES ('preis_kurs', ?)", (str(kurs),))
    con.commit()
    con.close()
    _log(f"Preislauf: {len(neue)} neu, {len(alt)} aufgefrischt, {geschrieben} geschrieben,"
          f" {fehl} fehlgeschlagen, {len(mehrdeutig)} mehrdeutig verworfen,"
          f" {var_neu} Varianten berichtigt, Kurs {kurs}, {urteile},"
          f" {max(0, offen - len(neue))} offen")


# --- Zweitquelle pokemontcg.io ----------------------------------------------
# Der Schlüssel liegt seit Langem in der .env und trug bisher nur fehlende Bilder und
# Set-Symbole. Er kostet nichts, erlaubt 20.000 Abrufe am Tag und liefert je Set eine
# Antwort mit allen Karten — der ganze westliche Katalog sind rund 200 Anfragen.
#
# Zwei Dinge holt er, die TCGdex nicht hergibt:
#   1. eine eigene, unabhängig gepflegte Cardmarket-Zuordnung. Wo TCGdex vier Karten an
#      dieselbe Produktnummer hängt und deshalb keine einen Preis bekommt, hat hier jede
#      Karte ihre eigene Zahl.
#   2. die gehandelten TCGplayer-Ausprägungen auch für Karten, für die TCGdex keine führt.
#
# Was er NICHT ist: eine Preisquelle erster Wahl. Am Prüftag waren seine Cardmarket-Zahlen
# zwei Monate alt. Sie landen deshalb in `eur_geschaetzt` mit dem Vermerk „zweitquelle“ und
# nie in `eur` oder in der Preishistorie.
PTC_SETS = "https://api.pokemontcg.io/v2/sets?pageSize=250&select=id,name,releaseDate,total"
PTC_KARTEN = "https://api.pokemontcg.io/v2/cards?q=set.id:{sid}&pageSize=250&select=id,number,cardmarket,tcgplayer"


def _ptc_set_schluessel(sid, name, datum):
    """Unsere Set-Kennung auf die von pokemontcg.io abbilden.

    Die beiden Kataloge schreiben dieselben Sets verschieden: `sv03.5` heißt dort `sv3pt5`.
    Erst die Normalform versuchen, dann Name und Erscheinungsdatum."""
    roh = (sid or "").lower()
    norm = roh.replace(".", "pt")
    # führende Nullen im Zahlenteil weg: sv03pt5 → sv3pt5
    import re as _re
    norm2 = _re.sub(r"(?<=[a-z])0+(?=\d)", "", norm)
    return [x for x in dict.fromkeys([roh, norm, norm2]) if x], (name or "").lower().strip(), (datum or "")[:10]


def _ptc_set_karte(client, con):
    """→ {unsere Set-ID: ptcgio-Set-ID}"""
    daten = _ptc_get(client, PTC_SETS).get("data") or []
    nach_id = {d["id"].lower(): d["id"] for d in daten}
    nach_name = {}
    for d in daten:
        nach_name[((d.get("name") or "").lower().strip(),
                   (d.get("releaseDate") or "").replace("/", "-")[:10])] = d["id"]
    karte = {}
    for r in con.execute("SELECT id, name_en, name, release_date FROM sets"
                         " WHERE COALESCE(region,'intl') = 'intl'"):
        kandidaten, name, datum = _ptc_set_schluessel(r["id"], r["name_en"] or r["name"], r["release_date"])
        treffer = next((nach_id[k] for k in kandidaten if k in nach_id), None)
        if not treffer:
            treffer = nach_name.get((name, datum))
        if treffer:
            karte[r["id"]] = treffer
    return karte


def _ptcgio_job(nur_set=None):
    """Karten ohne verlässlichen Europreis über die Zweitquelle auffüllen."""
    key = _env().get("PTCGIO_KEY")
    if not key:
        _log("Zweitquelle übersprungen: PTCGIO_KEY fehlt")
        return 0
    con = get_db()
    merker = con.execute("SELECT value FROM kv WHERE key='preis_kurs'").fetchone()
    try:
        kurs = float(merker["value"]) if merker else None
    except Exception:
        kurs = None
    tab = con.execute("SELECT value FROM kv WHERE key='preis_kurstabelle'").fetchone()
    try:
        kurstabelle = {tuple([int(x.split("|")[0]), x.split("|")[1]]): v
                       for x, v in json.loads(tab["value"]).items()} if tab else {}
    except Exception:
        kurstabelle = {}
    datum_je_karte = {r["id"]: r["release_date"] for r in con.execute(
        "SELECT id, release_date FROM cards WHERE COALESCE(region,'intl') = 'intl'")}
    offen = {}
    # Karten ohne Europreis brauchen die Zweitquelle; Karten ohne `ptc_id` brauchen sie für
    # den Cardmarket-Direktlink. Beides kommt aus derselben Antwort, also in einem Durchgang.
    for r in con.execute(
            "SELECT c.id, c.set_id FROM cards c LEFT JOIN card_prices p ON p.card_id = c.id"
            " WHERE COALESCE(c.region,'intl') = 'intl'"
            " AND (p.card_id IS NULL OR p.eur IS NULL OR p.ptc_id IS NULL)"):
        offen.setdefault(r["set_id"], set()).add(r["id"])
    if nur_set:
        offen = {k: v for k, v in offen.items() if k == nur_set}
    if not offen:
        con.close()
        return 0
    with httpx.Client(timeout=40, headers=UA) as client:
        karte = _ptc_set_karte(client, con)
        gefuellt = 0
        for sid, ids in sorted(offen.items(), key=lambda x: -len(x[1])):
            ziel = karte.get(sid)
            if not ziel:
                continue
            try:
                daten = _ptc_get(client, PTC_KARTEN.format(sid=ziel)).get("data") or []
            except Exception:
                continue
            # Die Kennungen weichen bei manchen Sets ab; die Karte wird über die Nummer
            # zugeordnet, nicht über die volle Kennung.
            nach_nr = {}
            for x in ids:
                nach_nr[x.rsplit("-", 1)[-1].lstrip("0").lower()] = x
            for c in daten:
                cid_ptc = c.get("id") or ""
                cid = cid_ptc if cid_ptc in ids else nach_nr.get(
                    cid_ptc.rsplit("-", 1)[-1].lstrip("0").lower())
                if not cid:
                    continue
                cm = ((c.get("cardmarket") or {}).get("prices") or {})
                tp = ((c.get("tcgplayer") or {}).get("prices") or {})
                wert = tief = None
                for k in ("trendPrice", "averageSellPrice", "avg30", "lowPrice"):
                    if cm.get(k):
                        wert = round(float(cm[k]), 2); break
                if cm.get("lowPrice"):
                    tief = round(float(cm["lowPrice"]), 2)
                quelle = "zweitquelle"
                # Manche Sets führt Cardmarket bei pokemontcg.io gar nicht — die
                # DP-Black-Star-Promos etwa. Dort gibt es wenigstens einen US-Marktpreis;
                # umgerechnet ist er besser als ein leeres Feld, solange es dransteht.
                if wert is None and kurs:
                    us = [v.get("market") for v in tp.values()
                          if isinstance(v, dict) and v.get("market")]
                    if us:
                        k = kurs_fuer(kurstabelle, datum_je_karte.get(cid), min(us), kurs)
                        wert = round(min(us) / k, 2)
                        quelle = "geschaetzt"
                keys = ",".join(sorted(tp.keys())) or None
                con.execute(
                    "INSERT INTO card_prices (card_id, eur_geschaetzt, eur_low, status, tp_keys,"
                    " ptc_id, updated_at) VALUES (?,?,?,?,?,?,datetime('now'))"
                    " ON CONFLICT(card_id) DO UPDATE SET"
                    " eur_geschaetzt=COALESCE(excluded.eur_geschaetzt, card_prices.eur_geschaetzt),"
                    " eur_low=CASE WHEN card_prices.eur IS NULL AND excluded.eur_low IS NOT NULL"
                    "              THEN excluded.eur_low ELSE card_prices.eur_low END,"
                    " status=CASE WHEN excluded.eur_geschaetzt IS NOT NULL"
                    "             AND card_prices.eur IS NULL THEN excluded.status"
                    "             ELSE card_prices.status END,"
                    " tp_keys=COALESCE(excluded.tp_keys, card_prices.tp_keys),"
                    " ptc_id=excluded.ptc_id",
                    (cid, wert, tief, quelle if wert is not None else None, keys, cid_ptc))
                if wert is not None:
                    gefuellt += 1
            con.commit()
    # Die Ausprägungen aus derselben Antwort mitnehmen — dieselbe Vereinigungsregel wie im
    # Preislauf, nur mit den Schlüsseln von pokemontcg.io.
    reihen = [{"id": r["card_id"], "ok": True, "tp_keys": (r["tp_keys"] or "").split(",")}
              for r in con.execute("SELECT card_id, tp_keys FROM card_prices WHERE tp_keys IS NOT NULL")]
    var = _varianten_schreiben(con, reihen)
    con.execute("INSERT OR REPLACE INTO kv (key,value) VALUES ('ptcgio_lauf', datetime('now'))")
    con.commit()
    con.close()
    _log(f"Zweitquelle: {gefuellt} Preise ergänzt, {var} Varianten berichtigt")
    return gefuellt


# --- Cardmarket: offizielles Preisverzeichnis und Produktkatalog -------------
#
# Cardmarket stellt beide Dateien seit einiger Zeit allen Nutzern zum Herunterladen
# bereit (cardmarket.com → Data → Download); vorher gab es sie nur über die API. Das
# Preisverzeichnis wird täglich erneuert, der Produktkatalog bei jeder Neuveröffentlichung.
#
# Damit braucht es keinen Datenkratzer: die Zahlen kommen aus der Quelle selbst, in der
# Fassung, die Cardmarket dafür vorgesehen hat. Der Weg ist ein Handgriff — Datei laden,
# hier hochladen — und ersetzt jede Zuordnung, die wir uns bisher zusammengesucht haben.
#
# Beide Dateien kommen je nach Auswahl als CSV oder JSON. Der Leser erkennt das Format
# selbst und ordnet die Spalten über ihre Namen zu, damit eine Umbenennung auf der
# Gegenseite nicht gleich alles anhält.

# Die Feldnamen der echten Dateien (geprüft an price_guide_6.json vom 03.09.2026):
# idProduct, idCategory, avg, low, trend, avg1, avg7, avg30 und dieselben mit „-holo".
# Die CSV-Fassung schreibt sie aus („Trend Price", „Low Price“) — beide Schreibweisen
# stehen hier, damit es egal ist, welche Fassung geladen wurde.
CM_PREIS_FELDER = {
    "eur":       ("trend", "trendprice", "avg", "avgsellprice"),
    "eur_low":   ("low", "lowprice"),
    "eur_avg7":  ("avg7", "avg7days"),
    "eur_avg30": ("avg30", "avg30days"),
    "eur_holo":  ("trendholo", "reverseholotrend", "foiltrend", "trendfoil",
                  "avgholo", "reverseholosell", "foilsell"),
}
# Nur Einzelkarten. Das Preisverzeichnis enthält auch Displays und Zubehör.
CM_KATEGORIE_SINGLE = 51


def _cm_zahl(wert):
    """Cardmarket schreibt fehlende Preise als 0 oder leer — beides heißt „unbekannt“."""
    if wert in (None, "", "0", "0.0", "0.00"):
        return None
    try:
        z = float(str(wert).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return round(z, 2) if z > 0 else None


def _cm_schluessel(name):
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def _cm_zeilen(roh: bytes):
    """→ Liste von Wörterbüchern, egal ob CSV (auch mit Semikolon) oder JSON."""
    text = roh.decode("utf-8-sig", errors="replace").lstrip()
    if text[:1] in "[{":
        daten = json.loads(text)
        if isinstance(daten, dict):
            for schluessel in ("priceGuides", "products", "data", "priceguide", "productlist"):
                if isinstance(daten.get(schluessel), list):
                    daten = daten[schluessel]; break
            else:
                daten = [daten]
        return [d for d in daten if isinstance(d, dict)]
    import csv as _csv
    kopf = text.split("\n", 1)[0]
    trenner = ";" if kopf.count(";") > kopf.count(",") else ","
    return list(_csv.DictReader(io.StringIO(text), delimiter=trenner))


def cm_preisverzeichnis_lesen(roh: bytes):
    """→ {idProduct: {eur, eur_low, eur_avg30, eur_holo}}"""
    aus = {}
    for zeile in _cm_zeilen(roh):
        felder = {_cm_schluessel(k): v for k, v in zeile.items()}
        pid = felder.get("idproduct") or felder.get("id") or felder.get("productid")
        try:
            pid = int(str(pid).strip())
        except (TypeError, ValueError):
            continue
        werte = {}
        for ziel, kandidaten in CM_PREIS_FELDER.items():
            for k in kandidaten:
                z = _cm_zahl(felder.get(_cm_schluessel(k)))
                if z is not None:
                    werte[ziel] = z
                    break
        if werte:
            aus[pid] = werte
    return aus


def cm_katalog_lesen(roh: bytes, nur_singles=True):
    """→ [{id, name, expansion, nummer}] aus dem Produktkatalog.

    Der Katalog führt keine Kartennummer: die Karten heißen dort „Weedle [Multiply]“ oder
    „Kakuna [Bug Bite | Primal Clash]“ — Basisname plus Attacken in eckigen Klammern. Die
    Zuordnung läuft deshalb über die Erweiterung und den Basisnamen, nicht über die Nummer.
    Eine Nummer wird nur mitgenommen, wenn sie ausnahmsweise in runden Klammern steht."""
    aus = []
    for zeile in _cm_zeilen(roh):
        felder = {_cm_schluessel(k): v for k, v in zeile.items()}
        pid = felder.get("idproduct") or felder.get("id") or felder.get("productid")
        try:
            pid = int(str(pid).strip())
        except (TypeError, ValueError):
            continue
        kategorie = felder.get("idcategory")
        if nur_singles and kategorie is not None and str(kategorie).strip().isdigit() \
                and int(str(kategorie).strip()) != CM_KATEGORIE_SINGLE:
            continue
        roh_name = str(felder.get("name") or felder.get("enname") or "").strip()
        nummer = str(felder.get("number") or felder.get("collectornumber") or "").strip()
        if not nummer:
            for teil in re.findall(r"\(([^)]*)\)", roh_name):
                if re.search(r"\d", teil) and len(teil) <= 12:
                    nummer = teil.strip()
        aus.append({
            "id": pid,
            "name": roh_name,
            "expansion": str(felder.get("idexpansion") or felder.get("expansion")
                             or felder.get("expansionname") or "").strip(),
            "nummer": nummer,
        })
    return aus


# Produktarten, die einer Erweiterung angehängt werden. „Base Set Booster" verrät, dass
# Erweiterung 1523 „Base Set" heißt — der Einzelkarten-Katalog nennt nur die Nummer.
CM_PRODUKTARTEN = ("Elite Trainer Box", "Ultra Premium Collection", "Premium Collection",
                   "Booster Box", "Booster Pack", "Booster Bundle", "Booster",
                   "Build and Battle", "Theme Deck", "Starter Deck", "Deck",
                   "Display", "Blister", "Tin", "Box Set", "Coin", "Bundle",
                   "Collection", "Premium", "Box")


def cm_erweiterungsnamen(roh: bytes):
    """→ {idExpansion: Name} aus dem Katalog der Nicht-Einzelkarten."""
    zaehler = {}
    for e in cm_katalog_lesen(roh, nur_singles=False):
        name = e["name"]
        for art in CM_PRODUKTARTEN:
            name = re.sub(r"\s*" + re.escape(art) + r"e?s?\b.*$", "", name, flags=re.I)
        name = name.strip()
        try:
            ex = int(e["expansion"])
        except (TypeError, ValueError):
            continue
        if len(name) >= 3:
            zaehler.setdefault(ex, {}).setdefault(name, 0)
            zaehler[ex][name] += 1
    return {ex: max(namen, key=namen.get) for ex, namen in zaehler.items()}


def _cm_merkmale(text, aus_klammer=False):
    """Fähigkeits- und Attackennamen als vergleichbare Menge.

    Aus dem Katalog kommt „Ampharos [Conductivity | Lightning Crush | Prime]", von
    TCGdex „Conductivity | Lightning Crush | Prime" — beide Male dieselben Namen, in
    derselben Reihenfolge, mit denselben Schreibweisen. Das ist kein Zufall: Cardmarket
    setzt die Klammer genau aus diesen Feldern zusammen."""
    text = str(text or "")
    if aus_klammer:
        # Die LETZTE Klammer. Cardmarket schreibt Zusätze davor — „Dialga [G] Lv.79
        # [Deafen | Second Strike]" —, und wer die erste nimmt, hält „G" für eine
        # Attacke und findet die Karte nie.
        treffer = re.findall(r"\[(.*?)\]", text)
        text = treffer[-1] if treffer else ""
    teile = (re.sub(r"[^a-z0-9]", "", t.lower()) for t in text.split("|"))
    return {t for t in teile if t}


def _cm_worte(text, ohne_klammer=False):
    """Namen als Wortmenge. `ohne_klammer` wirft die Attackenliste am Ende weg."""
    text = str(text or "")
    if ohne_klammer:
        text = re.sub(r"\s*\[[^\]]*\]\s*$", "", text)
    text = (text.replace("&female;", " f ").replace("&male;", " m ")
                .replace("&", " and ").replace("é", "e").replace("♀", " f ").replace("♂", " m "))
    teile = (re.sub(r"[^a-z0-9]", "", t.lower()) for t in re.split(r"[\s\[\]().]+", text))
    return {t for t in teile if t}


def _cm_produkt_waehlen(kandidaten, merkmale):
    """Aus mehreren Produkten desselben Namens das passende wählen.

    `kandidaten` ist eine Liste (idProduct, Katalogname), `merkmale` unsere Kette aus
    `cards.merkmale`. Gewinner ist, wer die meisten Namen teilt — und zwar allein: bei
    Gleichstand wird nichts zugeordnet. Ein einzelner Kandidat gewinnt kampflos, sonst
    hinge auch die heutige Katalog-Zuordnung plötzlich an den Merkmalen.

    Das ersetzt den Versuch, aus der Cardmarket-Adresse zu schließen. Der scheiterte
    daran, dass der Adressteil den Unterschied gar nicht trägt (`Ampharos-HS105` gegen
    `Ampharos-HS14`), und die Reihenfolge der Produktnummern taugt auch nicht: in
    HeartGold SoulSilver steht Ampharos Prime *hinter* der Normalkarte, Meganium Prime
    davor."""
    if len(kandidaten) == 1:
        return kandidaten[0][0]
    meine = _cm_merkmale(merkmale)
    if not meine or not kandidaten:
        return None
    punkte = sorted(((len(meine & _cm_merkmale(name, aus_klammer=True)), pid)
                     for pid, name in kandidaten), reverse=True)
    if punkte[0][0] >= 1 and (len(punkte) == 1 or punkte[0][0] > punkte[1][0]):
        return punkte[0][1]
    return None


def _cm_basisname(name):
    """„Kakuna [Bug Bite | Primal Clash]“ → „kakuna“. Die eckige Klammer trägt die
    Attacken, nicht die Ausgabe — sie unterscheidet Karten gleichen Namens innerhalb
    einer Erweiterung, taugt aber nicht zum Abgleich mit unserem Katalog."""
    n = re.sub(r"\[.*?\]", " ", str(name or ""))
    n = re.sub(r"\(.*?\)", " ", n)
    n = n.replace("&", "and").replace("é", "e").replace("♀", "f").replace("♂", "m")
    return re.sub(r"[^a-z0-9]", "", n.lower())


def _cm_suchname(name):
    """„Arceus LV.X [Multitype | Ominiscient]" → „Arceus LV.X".

    Der Name, unter dem Cardmarket die Karte selbst führt, ohne die Attackenklammer.
    Genau der gehört in die Suche: zusammen mit der Erweiterung bleibt meistens ein
    einziger Treffer übrig, und darauf springt Cardmarket direkt auf die Produktseite.
    Abgeschnitten wird nur die **letzte** Klammer — Zusätze stehen davor
    („Dialga [G] Lv.79 [Deafen | Second Strike]")."""
    n = re.sub(r"\s*\[[^\]]*\]\s*$", " ", str(name or ""))
    return re.sub(r"\s+", " ", n).strip()


def _cm_name_norm(name):
    """Namen vergleichbar machen.

    Klammerzusätze fliegen raus (sie tragen Nummer oder Ausgabe), Satzzeichen ebenfalls.
    Was bleibt, sind auch die Suffixe: „Gardevoir LV.X“ ist eine andere Karte als
    „Gardevoir“, und wer das wegkürzt, legt zwei Karten auf ein Produkt — genau der
    Fehler, den wir gerade erst aufgeräumt haben."""
    n = str(name or "").lower()
    n = re.sub(r"\(.*?\)", " ", n)
    n = n.replace("&", "and").replace("é", "e").replace("♀", "f").replace("♂", "m")
    return re.sub(r"[^a-z0-9]", "", n)


def cm_import(preise_roh=None, katalog_roh=None, nonsingles_roh=None):
    """Preisverzeichnis und Produktkatalog übernehmen.

    Reihenfolge ist wichtig: erst der Katalog (er stellt fehlende Zuordnungen her), dann
    das Preisverzeichnis (es trägt die Zahlen ein). Wer beides in einem Aufruf schickt,
    bekommt genau das."""
    bericht = {"katalog": 0, "zuordnungen": 0, "preise": 0, "karten": 0, "unbekannt": 0}
    con = get_db()

    # --- Produktkatalog: fehlende Zuordnungen herstellen ---------------------
    #
    # Der Katalog nennt keine Kartennummer, nur `idExpansion` als Zahl. Welche unserer
    # Sets zu welcher Erweiterung gehört, wird deshalb aus den Zuordnungen gelernt, die
    # schon stehen: für jedes Set gewinnt die Erweiterung, die bei seinen bereits
    # bekannten Produktnummern am häufigsten vorkommt. Innerhalb der Erweiterung wird über
    # den Basisnamen verglichen, und nur eindeutige Treffer werden übernommen.
    if katalog_roh:
        eintraege = cm_katalog_lesen(katalog_roh)
        bericht["katalog"] = len(eintraege)
        exp_je_produkt = {}
        for e in eintraege:
            try:
                exp_je_produkt[e["id"]] = int(e["expansion"])
            except (TypeError, ValueError):
                pass
        gelernt = {}
        for r in con.execute("SELECT c.set_id, p.cm_produkt FROM card_prices p"
                             " JOIN cards c ON c.id = p.card_id"
                             " WHERE p.cm_produkt IS NOT NULL"):
            e = exp_je_produkt.get(r["cm_produkt"])
            if e is not None:
                gelernt.setdefault(r["set_id"], {}).setdefault(e, 0)
                gelernt[r["set_id"]][e] += 1
        set_exp = {sid: max(zaehler, key=zaehler.get) for sid, zaehler in gelernt.items()}
        set_exp_alt = {sid: {e} for sid, e in set_exp.items()}
        # Zweite Brücke: Sets, für die keine einzige Zuordnung steht, lassen sich über den
        # Namen der Erweiterung finden — den verraten die Nicht-Einzelkarten („Base Set
        # Booster" gehört zu Erweiterung 1523, also heißt sie „Base Set").
        if nonsingles_roh:
            namen = cm_erweiterungsnamen(nonsingles_roh)
            bericht["erweiterungsnamen"] = len(namen)
            nach_name = {}
            for ex, nm in namen.items():
                nach_name.setdefault(_cm_basisname(nm), []).append(ex)
            for r in con.execute("SELECT id, name, name_en FROM sets"):
                for feld in (r["name_en"], r["name"]):
                    treffer = nach_name.get(_cm_basisname(feld or ""))
                    if treffer and len(treffer) == 1:
                        # Auch für Sets, die schon eine gelernte Erweiterung haben: die
                        # kann falsch sein. Base Set 2 hat bei Cardmarket eine eigene
                        # Erweiterung, TCGdex hängt seine Karten aber an die Jungle- und
                        # Promo-Nachdrucke — gelernt wird dann die falsche. Beide bleiben
                        # als Kandidat stehen, entschieden wird über die Attacken.
                        set_exp_alt.setdefault(r["id"], set()).add(treffer[0])
                        if r["id"] not in set_exp:
                            set_exp[r["id"]] = treffer[0]
                            bericht["sets_ueber_namen"] = bericht.get("sets_ueber_namen", 0) + 1
                        break
        # Dritte Brücke: die Karten selbst abstimmen lassen. Promo-Erweiterungen haben
        # keine Booster und stehen deshalb in keinem Erweiterungsnamen — die
        # DP-Black-Star-Promos etwa, aus denen fünf Karten der eigenen Sammlung ohne
        # Preis dastanden. Für jedes Set wird gezählt, in welcher Erweiterung seine
        # Karten (Name + Attackenkette) am häufigsten wiederzufinden sind.
        weltweit = {}
        for e in eintraege:
            m = _cm_merkmale(e["name"], aus_klammer=True)
            if not m:
                continue
            try:
                ex = int(e["expansion"])
            except (TypeError, ValueError):
                continue
            weltweit.setdefault(frozenset(m), []).append((ex, e["name"]))
        stimmen = {}
        for r in con.execute("SELECT set_id, name_en, name_de, name_ja, merkmale FROM cards"
                             " WHERE merkmale IS NOT NULL AND merkmale <> ''"):
            m = _cm_merkmale(r["merkmale"])
            treffer = weltweit.get(frozenset(m)) if m else None
            if not treffer:
                continue
            z = stimmen.setdefault(r["set_id"], [{}, 0])
            z[1] += 1
            namen = (r["name_en"], r["name_de"], r["name_ja"])
            for ex, pname in treffer:
                pw = _cm_worte(pname, ohne_klammer=True)
                if any(_cm_worte(n) and _cm_worte(n) <= pw for n in namen if n):
                    z[0][ex] = z[0].get(ex, 0) + 1
        for sid, (zaehler, karten) in stimmen.items():
            if not zaehler:
                continue
            rang = sorted(zaehler.items(), key=lambda x: -x[1])
            # Die Hälfte der Karten muss die Erweiterung tragen, und sie muss allein
            # vorn liegen. Als *zusätzlicher* Kandidat, nicht als Ersatz — entschieden
            # wird ohnehin über die Attackenkette.
            if rang[0][1] >= max(3, karten * 0.5) and (len(rang) == 1 or rang[0][1] > rang[1][1]):
                set_exp_alt.setdefault(sid, set()).add(rang[0][0])
                set_exp.setdefault(sid, rang[0][0])
                bericht["sets_ueber_karten"] = bericht.get("sets_ueber_karten", 0) + 1

        bericht["erweiterungen"] = len(set_exp)

        # Attackenkette → Produkt. Zwei verschiedene Karten einer Erweiterung mit exakt
        # derselben Kombination aus Fähigkeiten und Attacken gibt es praktisch nicht; wo
        # doch, wird nichts zugeordnet. Das trägt die Fälle, in denen schon der Name
        # auseinanderläuft: wir führen „Shaymin", Cardmarket „Shaymin LV.X".
        nach_exp_merk = {}
        name_je_produkt = {e["id"]: e["name"] for e in eintraege}
        for e in eintraege:
            try:
                ex = int(e["expansion"])
            except (TypeError, ValueError):
                continue
            m = _cm_merkmale(e["name"], aus_klammer=True)
            if m:
                nach_exp_merk.setdefault(ex, {}).setdefault(frozenset(m), []).append(e["id"])

        def _namensbezug(produktname, namen):
            """Trägt das Produkt einen unserer Kartennamen?

            Die Attackenkette allein reicht nicht: „Karrablast [Peck]" und
            „Prinplup [Peck]" haben dieselbe, und ohne diese Prüfung wanderte der Preis
            der einen Karte auf die andere.

            Verglichen wird wortweise: jedes Wort unseres Namens muss im Produktnamen
            vorkommen. Ein Präfixvergleich reichte nicht, weil Cardmarket Zusätze
            mitten hinein setzt — „Charizard G" steht dort als „Charizard [G] LV.X",
            und „charizardlvx" beginnt nicht mit „charizardg". Umgekehrt findet die
            Wortregel weiterhin „Shaymin" in „Shaymin LV.X"."""
            pw = _cm_worte(produktname, ohne_klammer=True)
            for n in namen:
                k = _cm_worte(n)
                if k and k <= pw:
                    return True
            return False

        def ueber_merkmale(exs, merkmale, namen, nur_freie=None):
            """→ idProduct, wenn genau ein Produkt der Erweiterungen diese Kette trägt
            und dabei einen unserer Kartennamen führt."""
            m = _cm_merkmale(merkmale)
            if not m:
                return None
            treffer = set()
            for ex in exs:
                treffer.update(nach_exp_merk.get(ex, {}).get(frozenset(m), ()))
            if nur_freie is not None:
                treffer = {t for t in treffer if t not in nur_freie}
            treffer = {t for t in treffer if _namensbezug(name_je_produkt.get(t), namen)}
            if len(treffer) == 1:
                return treffer.pop()
            # Cardmarket führt manche Promos doppelt: „Dialga LV.X [Time Skip | Metal
            # Flash]" steht in den DP-Black-Star-Promos zweimal, mit verschiedenen
            # Produktnummern. Tragen alle Kandidaten denselben Namen, ist es dieselbe
            # Karte und damit derselbe Preis — dann gewinnt die kleinere Nummer.
            if treffer and len({name_je_produkt.get(t) for t in treffer}) == 1:
                return min(treffer)
            return None

        vergeben = {r["cm_produkt"] for r in con.execute(
            "SELECT cm_produkt FROM card_prices WHERE cm_produkt IS NOT NULL")}
        frei = {}
        for e in eintraege:
            if e["id"] in vergeben:
                continue
            try:
                ex = int(e["expansion"])
            except (TypeError, ValueError):
                continue
            frei.setdefault(ex, {}).setdefault(_cm_basisname(e["name"]), []).append(
                (e["id"], e["name"]))

        for r in con.execute(
                "SELECT c.id, c.set_id, c.name_en, c.name_de, c.name_ja, c.merkmale FROM cards c"
                " LEFT JOIN card_prices p ON p.card_id = c.id WHERE p.cm_produkt IS NULL"):
            ex = set_exp.get(r["set_id"])
            if ex is None:
                continue
            kandidaten = {}
            for name in (r["name_en"], r["name_de"], r["name_ja"]):
                if name:
                    kandidaten.update(frei.get(ex, {}).get(_cm_basisname(name), []))
            # Mehrere Produkte gleichen Namens werden über die Attacken entschieden. Blind
            # eines zu nehmen wäre genau der Fehler, den wir TCGdex vorwerfen.
            pid = _cm_produkt_waehlen(list(kandidaten.items()), r["merkmale"])
            if pid is None:
                pid = ueber_merkmale(set_exp_alt.get(r["set_id"], {ex}), r["merkmale"],
                                     (r["name_en"], r["name_de"], r["name_ja"]), vergeben)
            if pid is not None:
                con.execute("INSERT INTO card_prices (card_id, cm_produkt, cm_quelle)"
                            " VALUES (?,?,'katalog')"
                            " ON CONFLICT(card_id) DO UPDATE SET cm_produkt = excluded.cm_produkt,"
                            " cm_quelle = 'katalog' WHERE card_prices.cm_produkt IS NULL",
                            (r["id"], pid))
                vergeben.add(pid)
                bericht["zuordnungen"] += 1
        con.commit()

    # --- Falsch zugeordnete Karten berichtigen ------------------------------
    #
    # 1.813 Karten hängen bei TCGdex an einer mehrfach vergebenen Produktnummer und
    # bekommen deshalb gar keinen Preis. In 509 der 856 Fälle sind es schlicht zwei
    # verschiedene Karten gleichen Namens in derselben Erweiterung — die Prime-Karten aus
    # HeartGold SoulSilver etwa, oder die beiden Shaymin LV.X aus Platin. Cardmarket führt
    # sie längst getrennt und schreibt den Unterschied in den Produktnamen; TCGdex hat
    # beiden dieselbe Nummer gegeben.
    if katalog_roh:
        nach_exp_name = {}
        for e in eintraege:
            try:
                ex = int(e["expansion"])
            except (TypeError, ValueError):
                continue
            nach_exp_name.setdefault(ex, {}).setdefault(
                _cm_basisname(e["name"]), []).append((e["id"], e["name"]))
        doppelt = {r["cm_produkt"] for r in con.execute(
            "SELECT cm_produkt FROM card_prices WHERE cm_produkt IS NOT NULL"
            " GROUP BY cm_produkt HAVING COUNT(*) > 1")}
        zu_pruefen = list(con.execute(
            "SELECT p.card_id, p.cm_produkt, c.set_id, c.name_en, c.name_de, c.name_ja,"
            " c.merkmale FROM card_prices p JOIN cards c ON c.id = p.card_id"
            " WHERE p.cm_produkt IS NOT NULL AND c.merkmale IS NOT NULL AND c.merkmale <> ''"))
        for r in zu_pruefen:
            # Wo die Attackenkette der Karte schon zum zugeordneten Produkt passt, ist
            # nichts zu tun — das sind 13.735 der Zuordnungen. Geprüft wird nur der Rest.
            if _cm_merkmale(r["merkmale"]) == _cm_merkmale(
                    name_je_produkt.get(r["cm_produkt"]), aus_klammer=True):
                continue
            # Die Erweiterung steht am zugeordneten Produkt selbst; sie ist auch dann
            # richtig, wenn die Karte innerhalb der Erweiterung falsch sitzt.
            ex = exp_je_produkt.get(r["cm_produkt"], set_exp.get(r["set_id"]))
            if ex is None:
                continue
            # Zwei Strengegrade, und der Unterschied ist der Ausgangszustand:
            #
            # Karten an einer mehrfach vergebenen Nummer haben nachweislich keine
            # brauchbare Zuordnung — sie bekommen ja gerade deshalb keinen Preis. Dort
            # genügt die beste Übereinstimmung unter den namensgleichen Produkten.
            #
            # Alle übrigen haben eine Zuordnung, die stimmen kann; sie umzuhängen, weil
            # die Klammer nicht wörtlich passt, würde mehr kaputtmachen als heilen. Hier
            # zählt nur die vollständige Übereinstimmung der Attackenkette.
            pid = None
            if r["cm_produkt"] in doppelt:
                kandidaten = {}
                for name in (r["name_en"], r["name_de"], r["name_ja"]):
                    if name:
                        kandidaten.update(nach_exp_name.get(ex, {}).get(_cm_basisname(name), []))
                pid = _cm_produkt_waehlen(list(kandidaten.items()), r["merkmale"])
            if pid is None:
                # Auch die Erweiterung kann falsch sein — dann zählt die des Set-Namens.
                pid = ueber_merkmale({ex} | set_exp_alt.get(r["set_id"], set()), r["merkmale"],
                                     (r["name_en"], r["name_de"], r["name_ja"]))
            if pid is not None and pid != r["cm_produkt"]:
                con.execute("UPDATE card_prices SET cm_produkt = ?, cm_quelle = 'merkmale'"
                            " WHERE card_id = ?", (pid, r["card_id"]))
                bericht["berichtigt"] = bericht.get("berichtigt", 0) + 1
        con.commit()

        # --- Ausschlussverfahren: was übrig bleibt, gehört zusammen -----------
        #
        # Arceus DP53 stand als einzige Karte der eigenen Sammlung ohne Preis da. TCGdex
        # führt für sie nur die Fähigkeit „Multitype" und keine Attacke; damit passte sie
        # gleich gut auf zwei Produkte („Multitype | Ominiscient" und „Multitype |
        # Meteor Blast"), und bei Gleichstand ordnet die Kette nichts zu — zu Recht.
        # Die Entscheidung liefert erst der Blick auf den Rest: die andere Karte (DP56)
        # hat ihr Produkt längst, übrig bleibt genau eines, dessen Kette unsere enthält.
        # Zugeordnet wird nur bei genau einem übrigen Kandidaten.
        vergeben = {r["cm_produkt"] for r in con.execute(
            "SELECT cm_produkt FROM card_prices WHERE cm_produkt IS NOT NULL")}
        # Verglichen wird über die Kette, nicht über den Basisnamen: Cardmarket schreibt
        # den Zusatz in den Namen („Arceus LV.X"), wir führen ihn in `stage` — die
        # Basisnamen laufen genau bei den Karten auseinander, um die es hier geht.
        uebrig = {}
        for e in eintraege:
            if e["id"] in vergeben:
                continue
            try:
                ex = int(e["expansion"])
            except (TypeError, ValueError):
                continue
            uebrig.setdefault(ex, []).append((e["id"], e["name"]))
        for r in con.execute(
                "SELECT c.id, c.set_id, c.name_en, c.name_de, c.name_ja, c.merkmale FROM cards c"
                " LEFT JOIN card_prices p ON p.card_id = c.id WHERE p.cm_produkt IS NULL"
                " AND c.merkmale IS NOT NULL AND c.merkmale <> ''"):
            ex = set_exp.get(r["set_id"])
            meine = _cm_merkmale(r["merkmale"])
            if ex is None or not meine:
                continue
            namen = (r["name_en"], r["name_de"], r["name_ja"])
            passend = [pid for pid, pname in uebrig.get(ex, ())
                       if pid not in vergeben
                       and meine < _cm_merkmale(pname, aus_klammer=True)
                       and _namensbezug(pname, namen)]
            if len(passend) == 1:
                con.execute("INSERT INTO card_prices (card_id, cm_produkt, cm_quelle)"
                            " VALUES (?,?,'ausschluss')"
                            " ON CONFLICT(card_id) DO UPDATE SET cm_produkt = excluded.cm_produkt,"
                            " cm_quelle = 'ausschluss' WHERE card_prices.cm_produkt IS NULL",
                            (r["id"], passend[0]))
                vergeben.add(passend[0])
                bericht["ausschluss"] = bericht.get("ausschluss", 0) + 1
        con.commit()

        # --- Der Name, unter dem Cardmarket die Karte führt -------------------
        #
        # Er ist der Rückfall, wenn die Produktseite unbekannt ist: „Arceus LV.X" in
        # Erweiterung 1609 gesucht schlägt jede Suche nach unserem eigenen Kartennamen,
        # und ob dabei genau ein Treffer übrig bleibt, lässt sich hier ausrechnen statt
        # raten — dann springt Cardmarket direkt auf die Karte. Gezählt wird über die
        # Namen *vor* der Attackenklammer: die Suche findet auch „Arceus LV.X [Multitype
        # | Meteor Blast]", wenn man nur „Arceus LV.X" eingibt.
        import bisect
        namen_je_exp = {}
        for e in eintraege:
            try:
                ex = int(e["expansion"])
            except (TypeError, ValueError):
                continue
            namen_je_exp.setdefault(ex, []).append(_cm_suchname(e["name"]).lower())
        for liste in namen_je_exp.values():
            liste.sort()
        aendern = []
        for r in con.execute("SELECT card_id, cm_produkt FROM card_prices"
                             " WHERE cm_produkt IS NOT NULL"):
            pname = name_je_produkt.get(r["cm_produkt"])
            if not pname:
                continue
            such = _cm_suchname(pname)
            ex = exp_je_produkt.get(r["cm_produkt"])
            liste = namen_je_exp.get(ex) or []
            k = such.lower()
            gleiche = (bisect.bisect_right(liste, k + "\uffff")
                       - bisect.bisect_left(liste, k)) if liste else 0
            aendern.append((such, 1 if gleiche == 1 else 0, r["card_id"]))
        con.executemany("UPDATE card_prices SET cm_name = ?, cm_eindeutig = ?"
                        " WHERE card_id = ?", aendern)
        bericht["namen"] = len(aendern)
        bericht["eindeutig"] = sum(1 for a in aendern if a[1])
        con.commit()

        # --- Lesbare Namen für japanische Karten und Sets ---------------------
        #
        # „アクア団のしたっぱ" erkennt hier niemand — Cardmarket führt dieselbe Karte als
        # „Team Aqua Grunt". Der Pokédex-Abgleich des Japan-Syncs greift nur bei Pokémon;
        # Trainer und Energien (2.467 Karten) blieben japanisch. Von denen tragen 1.994
        # eine Cardmarket-Zuordnung, und damit einen englischen Namen.
        n_karten = con.execute(
            "UPDATE cards SET name_en = (SELECT p.cm_name FROM card_prices p"
            "   WHERE p.card_id = cards.id)"
            " WHERE COALESCE(region,'intl') = 'jp' AND COALESCE(name_en,'') = ''"
            "   AND (SELECT p.cm_name FROM card_prices p WHERE p.card_id = cards.id)"
            "       IS NOT NULL").rowcount
        bericht["jp_namen"] = n_karten
        # Dasselbe für die Sets: Cardmarket führt japanische Erweiterungen unter
        # englischen Namen („VMAX Climax" für „VMAXクライマックス"). Welche Erweiterung ein
        # Set ist, sagt die Mehrheit seiner Karten.
        ex_namen = cm_erweiterungsnamen(nonsingles_roh) if nonsingles_roh else {}
        if ex_namen:
            n_sets = 0
            for r in con.execute(
                    "SELECT c.set_id, p.cm_expansion ex, COUNT(*) n FROM cards c"
                    " JOIN card_prices p ON p.card_id = c.id"
                    " WHERE COALESCE(c.region,'intl') = 'jp' AND p.cm_expansion IS NOT NULL"
                    " GROUP BY c.set_id, p.cm_expansion ORDER BY c.set_id, n DESC"):
                name = ex_namen.get(r["ex"])
                if not name:
                    continue
                # ORDER BY liefert je Set zuerst die häufigste Erweiterung; spätere
                # Zeilen desselben Sets prallen an der Bedingung ab.
                n_sets += con.execute(
                    "UPDATE sets SET name_en = ? WHERE id = ? AND region = 'jp'"
                    " AND (name_en IS NULL OR name_en = name)", (name, r["set_id"])).rowcount
            bericht["jp_sets"] = n_sets
        for ja, en in JP_KARTEN_NAMEN.items():
            con.execute("UPDATE cards SET name_en = ? WHERE COALESCE(region,'intl') = 'jp'"
                        " AND name_ja = ? AND COALESCE(name_en,'') = ''", (en, ja))
        for sid, name in JP_SET_NAMEN.items():
            con.execute("UPDATE sets SET name_en = ? WHERE id = ? AND region = 'jp'"
                        " AND COALESCE(name_en, name) <> ?", (name, sid, name))
        con.commit()

        # Die Erweiterung des zugeordneten Produkts festhalten. Ohne sie findet die
        # Suche 75 Palkia aus zwanzig Jahren; mit ihr bleibt die eine übrig.
        con.executemany("UPDATE card_prices SET cm_expansion = ? WHERE card_id = ?",
                        [(exp_je_produkt[r["cm_produkt"]], r["card_id"])
                         for r in con.execute("SELECT card_id, cm_produkt FROM card_prices"
                                              " WHERE cm_produkt IS NOT NULL")
                         if r["cm_produkt"] in exp_je_produkt])
        # Auch Karten ohne eigenes Produkt: die Erweiterung ihres Sets grenzt die Suche
        # trotzdem auf ein Set ein, und mehr braucht der Rückfall nicht.
        con.executemany(
            "INSERT INTO card_prices (card_id, cm_expansion) VALUES (?,?)"
            " ON CONFLICT(card_id) DO UPDATE SET cm_expansion = excluded.cm_expansion",
            [(r["id"], set_exp[r["set_id"]]) for r in con.execute(
                "SELECT c.id, c.set_id FROM cards c LEFT JOIN card_prices p ON p.card_id = c.id"
                " WHERE p.cm_expansion IS NULL") if r["set_id"] in set_exp])
        con.commit()

    # --- Preisverzeichnis: Zahlen eintragen ---------------------------------
    if preise_roh:
        preise = cm_preisverzeichnis_lesen(preise_roh)
        bericht["preise"] = len(preise)
        nach_produkt = {}
        for r in con.execute("SELECT card_id, cm_produkt FROM card_prices"
                             " WHERE cm_produkt IS NOT NULL"):
            nach_produkt.setdefault(r["cm_produkt"], []).append(r["card_id"])
        verlauf = []
        for pid, werte in preise.items():
            karten = nach_produkt.get(pid)
            if not karten:
                bericht["unbekannt"] += 1
                continue
            # Mehrere Karten an einem Produkt bleiben gesperrt — daran ändert die Datei
            # nichts, sie kennt die Zuordnung ja nicht besser als wir.
            if len(karten) > 1:
                continue
            cid = karten[0]
            # Nur überschreiben, wo die Datei etwas hergibt. Der erste Entwurf setzte
            # `eur` auch dann, wenn die Zeile nur einen Tiefstpreis trug, und löschte
            # dabei Status und Schätzung mit — 428 Karten standen danach ohne jede Zahl da,
            # die vorher eine hatten.
            hat_trend = werte.get("eur") is not None
            con.execute(
                "UPDATE card_prices SET eur = COALESCE(?, eur),"
                " eur_low = COALESCE(?, eur_low), eur_avg7 = COALESCE(?, eur_avg7),"
                " eur_avg30 = COALESCE(?, eur_avg30),"
                " eur_holo = CASE WHEN ? THEN ? ELSE eur_holo END,"
                " status = CASE WHEN ? THEN NULL ELSE status END,"
                " eur_geschaetzt = CASE WHEN ? THEN NULL ELSE eur_geschaetzt END,"
                " cm_import_am = CASE WHEN ? THEN datetime('now') ELSE cm_import_am END,"
                " updated_at = datetime('now') WHERE card_id = ?",
                (werte.get("eur"), werte.get("eur_low"), werte.get("eur_avg7"),
                 werte.get("eur_avg30"), hat_trend, werte.get("eur_holo"),
                 hat_trend, hat_trend, hat_trend, cid))
            if hat_trend:
                verlauf.append((cid, werte["eur"], None))
                bericht["karten"] += 1
        # Der Verlauf bekommt nur, was sich bewegt hat — siehe `_historie_eintragen`.
        bericht["verlauf"] = _historie_eintragen(con, verlauf, "cardmarket")
        con.commit()
        # Der Holo-Wert gehört wieder nur an Karten mit zweiter Ausgabe.
        con.execute("UPDATE card_prices SET eur_holo = NULL WHERE card_id IN"
                    " (SELECT id FROM cards WHERE NOT (COALESCE(has_normal,0) = 1"
                    " AND (COALESCE(has_reverse,0) = 1 OR COALESCE(has_holo,0) = 1)))"
                    " AND eur_holo IS NOT NULL")
        con.execute("INSERT OR REPLACE INTO kv (key,value)"
                    " VALUES ('cm_import_lauf', datetime('now'))")
        con.commit()
        # Die Urteile des Börsenvergleichs gehören zu den neuen Zahlen, nicht zu den alten.
        try:
            kurs, urteile = _kurs_pruefen(con)
            con.commit()
            bericht["kurs"] = kurs
            bericht["urteile"] = urteile
        except Exception as exc:
            _log("Börsenvergleich nach dem Import fehlgeschlagen:", exc)
    con.close()
    return bericht


def _cm_nummer(text):
    """„4/102“, „DP45“, „005“ → vergleichbare Form."""
    t = str(text or "").strip().upper()
    t = t.split("/")[0]
    t = re.sub(r"[^A-Z0-9]", "", t)
    ziffern = re.sub(r"^0+", "", re.sub(r"[^0-9]", "", t))
    buchstaben = re.sub(r"[0-9]", "", t)
    return buchstaben + ziffern


# Die Downloads liegen auf einem eigenen Ablageplatz, nicht hinter Cloudflare: die
# Seiten cardmarket.com/…/Data/Price-Guide und …/Data/Product-List verlinken direkt
# dorthin, und der Server erreicht sie ohne Anmeldung in unter einer Sekunde. Damit
# braucht es weder Browser noch Zugangsdaten — der Nachtlauf holt sie selbst.
# Die 6 ist Pokémon (1 = Magic, 18 = One Piece).
CM_ABLAGE = "https://downloads.s3.cardmarket.com/productCatalog"
CM_DOWNLOADS = {
    "price_guide_6.json": f"{CM_ABLAGE}/priceGuide/price_guide_6.json",
    "products_singles_6.json": f"{CM_ABLAGE}/productList/products_singles_6.json",
    "products_nonsingles_6.json": f"{CM_ABLAGE}/productList/products_nonsingles_6.json",
}


def _cm_download_job():
    """Die drei Cardmarket-Dateien holen. → {Datei: Bytes} der wirklich neuen.

    Geladen wird immer, geschrieben nur, wenn sich der Zeitstempel in der Datei geändert
    hat: das Preisverzeichnis wird täglich erneuert, die Kataloge nur bei einer
    Neuveröffentlichung. Ein unvollständiger Download darf die vorhandene Datei nicht
    ersetzen, deshalb erst prüfen, dann schreiben."""
    ordner = BASE / "cardmarket"
    ordner.mkdir(exist_ok=True)
    neu = {}
    with httpx.Client(timeout=180, headers=UA, follow_redirects=True) as client:
        for name, url in CM_DOWNLOADS.items():
            ziel = ordner / name
            try:
                r = client.get(url)
                if r.status_code != 200 or len(r.content) < 10000:
                    _log(f"Cardmarket-Download {name}: HTTP {r.status_code},"
                          f" {len(r.content)} Bytes — übersprungen")
                    continue
            except Exception as exc:
                _log(f"Cardmarket-Download {name} fehlgeschlagen:", exc)
                continue
            # Zeitstempel aus dem Kopf der Datei lesen, ohne die 15 MB zu zerlegen.
            treffer = re.search(rb'"createdAt"\s*:\s*"([^"]+)"', r.content[:500])
            stand = treffer.group(1).decode() if treffer else ""
            alt = ""
            if ziel.exists():
                mit = re.search(rb'"createdAt"\s*:\s*"([^"]+)"', ziel.read_bytes()[:500])
                alt = mit.group(1).decode() if mit else ""
            if stand and stand == alt:
                continue          # unverändert, nichts zu tun
            ziel.write_bytes(r.content)
            neu[name] = stand or "?"
            _log(f"Cardmarket-Download {name}: {len(r.content)} Bytes, Stand {stand}")
    if neu:
        con = get_db()
        con.execute("INSERT OR REPLACE INTO kv (key,value) VALUES ('cm_download_lauf', ?)",
                    (json.dumps({"am": _now_text(), "neu": neu}),))
        con.commit()
        con.close()
    return neu


def _now_text():
    from datetime import datetime as _dt
    return _dt.utcnow().strftime("%Y-%m-%d %H:%M:%S")


@app.post("/api/admin/cm_download")
def admin_cm_download(key: str = "", einlesen: int = 1):
    """Dateien holen und gleich einlesen."""
    if not admin_ok(key):
        raise HTTPException(403, "Kein Zugriff")

    def lauf():
        neu = _cm_download_job()
        if neu and einlesen:
            _cm_import_job()
    threading.Thread(target=lauf, daemon=True).start()
    return {"ok": True}


def _cm_dateien():
    """→ (Preisverzeichnis, Einzelkarten-Katalog, Nicht-Einzelkarten) als Bytes."""
    ordner = BASE / "cardmarket"
    preise = katalog = nonsingles = None
    if ordner.exists():
        for datei in sorted(ordner.glob("*")):
            if not datei.is_file():
                continue
            name = datei.name.lower()
            if "price" in name or "preis" in name:
                preise = datei.read_bytes()
            elif "nonsingle" in name or "non_single" in name:
                nonsingles = datei.read_bytes()
            elif "product" in name or "katalog" in name or "catalog" in name:
                katalog = datei.read_bytes()
    return preise, katalog, nonsingles


def _cm_import_job():
    """Nachts: die abgelegten Cardmarket-Dateien einlesen, falls es sie gibt.

    Der Server kann sie nicht selbst holen — Cardmarket beantwortet jeden Serverabruf mit
    403, auch den der Download-Seite. Steht in der .env ein `CM_PREISE_URL`, wird es
    trotzdem versucht (falls Cardmarket später eine direkte Dateiadresse anbietet);
    sonst zählt, was im Ordner liegt."""
    url = _env().get("CM_PREISE_URL")
    if url:
        try:
            with httpx.Client(timeout=120, headers=UA, follow_redirects=True) as client:
                r = client.get(url)
            if r.status_code == 200 and r.content:
                ziel = BASE / "cardmarket"
                ziel.mkdir(exist_ok=True)
                (ziel / "price_guide.json").write_bytes(r.content)
                _log("Cardmarket-Preisverzeichnis geladen:", len(r.content), "Bytes")
        except Exception as exc:
            _log("Cardmarket-Preisverzeichnis nicht ladbar:", exc)
    preise, katalog, nonsingles = _cm_dateien()
    if not preise and not katalog:
        return None
    bericht = cm_import(preise, katalog, nonsingles)
    _log("Cardmarket-Import:", bericht)
    return bericht


@app.post("/api/admin/cm_import")
async def admin_cm_import(request: Request, key: str = ""):
    """Preisverzeichnis und/oder Produktkatalog von Cardmarket übernehmen.

    Zwei Wege: als Datei-Upload (Felder `preise` und `katalog`) oder als Verweis auf
    Dateien im Ordner `cardmarket/` neben der Datenbank."""
    if not admin_ok(key):
        raise HTTPException(403, "Kein Zugriff")
    preise_roh = katalog_roh = nonsingles_roh = None
    art = (request.headers.get("content-type") or "")
    if art.startswith("multipart/form-data"):
        form = await request.form()
        if form.get("preise") is not None and hasattr(form["preise"], "read"):
            preise_roh = await form["preise"].read()
        if form.get("katalog") is not None and hasattr(form["katalog"], "read"):
            katalog_roh = await form["katalog"].read()
        if form.get("nonsingles") is not None and hasattr(form["nonsingles"], "read"):
            nonsingles_roh = await form["nonsingles"].read()
    else:
        preise_roh, katalog_roh, nonsingles_roh = _cm_dateien()
    if not preise_roh and not katalog_roh:
        raise HTTPException(400, "Weder Preisverzeichnis noch Produktkatalog gefunden. "
                                 "Dateien hochladen oder in den Ordner cardmarket/ legen.")
    bericht = await run_in_threadpool(cm_import, preise_roh, katalog_roh, nonsingles_roh)
    return {"ok": True, **bericht}


@app.get("/api/admin/cm_stand")
def admin_cm_stand(key: str = ""):
    """Wie weit trägt der Import? Für die Entscheidung, ob eine neue Datei nötig ist."""
    if not admin_ok(key):
        raise HTTPException(403, "Kein Zugriff")
    con = get_db()
    o = lambda sql: con.execute(sql).fetchone()[0]      # noqa: E731
    aus = {
        "karten": o("SELECT COUNT(*) FROM cards"),
        "mit_produkt": o("SELECT COUNT(*) FROM card_prices WHERE cm_produkt IS NOT NULL"),
        "produkt_aus_katalog": o("SELECT COUNT(*) FROM card_prices WHERE cm_quelle = 'katalog'"),
        "aus_datei": o("SELECT COUNT(*) FROM card_prices WHERE cm_import_am IS NOT NULL"),
        "letzter_import": (o("SELECT value FROM kv WHERE key='cm_import_lauf'")
                           if o("SELECT COUNT(*) FROM kv WHERE key='cm_import_lauf'") else None),
        "ohne_zahl": o("SELECT COUNT(*) FROM card_prices WHERE eur IS NULL AND eur_geschaetzt IS NULL"),
    }
    con.close()
    return aus


# --- Cardmarket-Produktseite ------------------------------------------------
# pokemontcg.io betreibt unter prices.pokemontcg.io/cardmarket/<id> eine Weiterleitung
# auf die echte Produktseite. Ein Aufruf je Karte genügt, das Ergebnis steht danach in
# `card_prices.cm_url`. Dasselbe Verfahren nutzt das Empire-Dashboard für den TCG-Bereich.
CM_REDIRECT = "https://prices.pokemontcg.io/cardmarket/{cid}"
CM_SPRACHE = {"de": 3, "en": 1, "fr": 2, "es": 4, "it": 5, "jp": 7, "ja": 7, "kr": 10}
# Cardmarkets Mindestzustand als Filter. Wer einen Posten in GD führt, will keine
# Near-Mint-Angebote sehen — und umgekehrt.
CM_ZUSTAND = {"M": 1, "NM": 2, "EX": 3, "GD": 4, "LP": 5, "PL": 6, "PO": 7}

# Von Hand hinterlegte Adressmuster. Absichtlich leer: Cardmarket schreibt die Nummer im
# Adressteil selbst uneinheitlich — im Set „XY Promokarten" steht bei XY35 die volle
# Nummer (`XYPRXY35`), bei XY98 nur die Ziffern (`XYPR98`). Ein geratener Direktlink
# führte damit auf „ungültiges Produkt", und das ist schlechter als die Suche. Ein
# Eintrag hier ist nur vertretbar, wenn die Adresse für dieses Set nachweislich einer
# Regel folgt.
CM_MUSTER_HAND = {}
# Ab dieser Trefferquote gegen die bereits aufgelösten Adressen darf ein Muster benutzt
# werden. Darunter ist Raten schlechter als die Suche: ein falscher Direktlink führt auf
# eine Fehlerseite, die Suche wenigstens in die Nähe.
# Ein gebautes Muster darf nur greifen, wo es im ganzen Set ausnahmslos stimmt. Bei
# 0.9 war jeder zehnte Link falsch — und ein falscher Direktlink („ungültiges Produkt")
# ist schlechter als die Suche, die immer trifft. Über alle 14.546 bekannten Adressen
# gemessen folgen nur 68,6 % der Bauregel; regelrein sind 10 von 145 Sets.
CM_MUSTER_SCHWELLE = 1.0
CM_MUSTER_BELEGE = 8


def _cm_nummer_slug(local_id):
    return re.sub(r"[^A-Za-z0-9]", "", str(local_id or "").split("/")[0]).upper()


def _cm_name_slug(name, stage, suffix, gold=False):
    """Den Namensteil der Cardmarket-Adresse bauen.

    Cardmarket schreibt Sonderzeichen aus („Mudkip ☆" wird mal `Mudkip`, mal
    `Mudkip-Star`), deshalb liefert `_cm_muster_treffer` beide Schreibweisen und je Set
    wird gemessen, welche stimmt."""
    n = str(name or "")
    for zeichen, ersatz in (("☆", " Star"), ("★", " Star"), ("δ", " Delta Species")):
        n = n.replace(zeichen, ersatz)
    if suffix and suffix.lower() not in n.lower():
        n += " " + suffix
    n = (n.replace("&", "and").replace("é", "e").replace("'", "")
          .replace(":", "").replace("LV.X", "LVX").replace("Lv.X", "LVX"))
    n = re.sub(r"[^A-Za-z0-9 -]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    if gold:
        n = re.sub(r"\bStar\b", "Gold Star", n)
    teile = [t for t in re.split(r"[ -]+", n) if t]
    if (stage or "") == "LEVEL-UP" and "LVX" not in teile:
        teile.append("LVX")
    return "-".join(teile)


def _cm_pfad_bauen(slug, code, name, stage, suffix, local_id, gold=False):
    return (f"/Pokemon/Products/Singles/{slug}/"
            f"{_cm_name_slug(name, stage, suffix, gold)}-{code}{_cm_nummer_slug(local_id)}")


def cm_muster_lernen():
    """Aus den bereits aufgelösten Adressen je Set Pfadname und Kürzel ableiten — und
    nur die Sets freigeben, bei denen das Ergebnis nachweislich stimmt.

    Gegen 15.057 bekannte Adressen getestet: über alle Sets hinweg trifft das Verfahren
    nur zwei Drittel, je Set aber entweder fast immer oder fast nie. Deshalb wird je Set
    gemessen und nur bei mindestens 90 Prozent gebaut."""
    con = get_db()
    reihen = list(con.execute(
        "SELECT c.set_id, c.name_en, c.local_id, c.stage, c.suffix, p.cm_url"
        " FROM card_prices p JOIN cards c ON c.id = p.card_id WHERE p.cm_url IS NOT NULL"))
    nach_set = {}
    for r in reihen:
        teile = r["cm_url"].strip("/").split("/")
        if len(teile) < 5:
            continue
        nach_set.setdefault(r["set_id"], []).append((teile[-2], teile[-1], r))
    muster = {}
    for sid, liste in nach_set.items():
        pfade = {}
        for slug, _letzter, _r in liste:
            pfade[slug] = pfade.get(slug, 0) + 1
        slug = max(pfade, key=pfade.get)
        # Kürzel aus dem Rest hinter dem Namen und vor der Nummer
        kuerzel = {}
        for _slug, letzter, r in liste:
            nr = _cm_nummer_slug(r["local_id"])
            if not nr or not letzter.upper().endswith(nr):
                continue
            rest = letzter[:len(letzter) - len(nr)]
            for gold in (False, True):
                ns = _cm_name_slug(r["name_en"], r["stage"], r["suffix"], gold)
                if rest.lower().startswith(ns.lower() + "-"):
                    k = (rest[len(ns) + 1:].rstrip("-"), gold)
                    kuerzel[k] = kuerzel.get(k, 0) + 1
                    break
        if not kuerzel:
            continue
        code, gold = max(kuerzel, key=kuerzel.get)
        treffer = sum(1 for _s, _l, r in liste
                      if _cm_pfad_bauen(slug, code, r["name_en"], r["stage"], r["suffix"],
                                        r["local_id"], gold).lower() == r["cm_url"].lower())
        if treffer / len(liste) >= CM_MUSTER_SCHWELLE and len(liste) >= CM_MUSTER_BELEGE:
            muster[sid] = (slug, code, gold, round(treffer / len(liste), 3), len(liste))
    con.execute("INSERT OR REPLACE INTO kv (key,value) VALUES ('cm_muster', ?)",
                (json.dumps({k: v[:3] for k, v in muster.items()}),))
    con.commit()
    con.close()
    return muster


def _cm_muster_laden(con):
    row = con.execute("SELECT value FROM kv WHERE key='cm_muster'").fetchone()
    aus = {sid: (v[0], v[1], v[2]) for sid, v in json.loads(row["value"]).items()} if row else {}
    for sid, (slug, code) in CM_MUSTER_HAND.items():
        aus.setdefault(sid, (slug, code, False))
    return aus


@app.post("/api/admin/cm_muster")
def admin_cm_muster(key: str = ""):
    if not admin_ok(key):
        raise HTTPException(403, "Kein Zugriff")
    m = cm_muster_lernen()
    return {"ok": True, "sets": len(m),
            "beispiele": {k: v for k, v in list(m.items())[:8]}}


def _cm_url_aufloesen(client, card_id, ptc_id=None):
    """→ die Adresse der Cardmarket-Produktseite oder None."""
    try:
        r = client.get(CM_REDIRECT.format(cid=ptc_id or card_id),
                       headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"},
                       follow_redirects=False)
        ziel = r.headers.get("location", "")
        if ziel.startswith("http") and "cardmarket.com" in ziel:
            # Die Weiterleitung landet auf der englischen Fassung ohne „www“. Gespeichert
            # wird der Pfad ab „/Pokemon/“, damit die Sprache der Oberfläche folgen kann.
            pfad = ziel.split("?")[0]
            schnitt = pfad.find("/Pokemon/")
            return pfad[schnitt:] if schnitt > 0 else pfad
    except Exception:
        pass
    return None


def cm_adresse(card_id, sprache="", ui="de", zustand="", con=None, aufloesen=True):
    """Die Cardmarket-Seite genau dieser Karte.

    Drei Stufen, in dieser Reihenfolge: die aufgelöste Adresse aus der Datenbank, dann
    das gemessene Adressmuster des Sets, zuletzt die Suche. Sprache und Mindestzustand
    hängen als Filter dran, wenn sie bekannt sind — wer einen Posten in GD führt, will
    keine Near-Mint-Angebote sehen."""
    eigene = con is None
    con = con or get_db()
    try:
        row = con.execute("SELECT cm_url, ptc_id, cm_expansion, cm_name, cm_eindeutig"
                          " FROM card_prices WHERE card_id = ?", (card_id,)).fetchone()
        karte = con.execute("SELECT name_en, name_de, local_id, set_id, stage, suffix"
                            " FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not karte:
            raise HTTPException(404, "Karte unbekannt")
        url = row["cm_url"] if row and "cm_url" in row.keys() else None
        genau = bool(url)
        if not url and aufloesen:
            ptc = row["ptc_id"] if row and "ptc_id" in row.keys() else None
            with httpx.Client(timeout=12, headers=UA) as client:
                url = _cm_url_aufloesen(client, card_id, ptc)
            if url:
                genau = True
                con.execute("INSERT INTO card_prices (card_id, cm_url) VALUES (?,?)"
                            " ON CONFLICT(card_id) DO UPDATE SET cm_url = excluded.cm_url",
                            (card_id, url))
                con.commit()
        if not url:
            # Zweite Stufe: das Muster des Sets, aber nur wenn es gemessen wurde.
            muster = _cm_muster_laden(con).get(karte["set_id"])
            if muster:
                slug, code, gold = muster
                url = _cm_pfad_bauen(slug, code, karte["name_en"] or karte["name_de"],
                                     karte["stage"], karte["suffix"], karte["local_id"], gold)
                genau = True
    finally:
        if eigene:
            con.close()

    loc = "en" if (ui or "").lower() == "en" else "de"
    filter_ = []
    nr = CM_SPRACHE.get((sprache or "").lower())
    if nr:
        filter_.append(f"language={nr}")
    zn = CM_ZUSTAND.get(str(zustand or "").strip().upper()[:2])
    if zn:
        filter_.append(f"minCondition={zn}")
    if not url:
        # Rückfall: die Suche — aber mit Cardmarkets eigenen Angaben, nicht mit unseren.
        #
        # Drei Dinge machen den Unterschied zwischen „führt zur Karte" und „führt in eine
        # Liste": der Name, unter dem Cardmarket die Karte selbst führt (`cm_name` aus dem
        # Produktkatalog, „Arceus LV.X" statt unserem „Arceus"), die Erweiterung
        # („Palkia" allein trifft 75 Produkte aus zwanzig Jahren, „Palkia" in den
        # DP-Black-Star-Promos genau eines) und die Kategorie. Bleibt genau ein Treffer,
        # springt Cardmarket direkt auf die Produktseite — und ob das so ist, steht als
        # `cm_eindeutig` schon fest, gezählt beim Import gegen den Katalog.
        # Die eckigen Klammern fallen für die Suche weg: Cardmarket *schreibt*
        # „Rayquaza [C] LV.X", gesucht wird aber nach Wörtern, und „[C]" als Wort findet
        # nichts. Der Rest des Namens bleibt, wie er dort steht.
        name = re.sub(r"\s+", " ", ((row["cm_name"] if row and "cm_name" in row.keys()
                                     else None) or "").replace("[", " ").replace("]", " ")).strip()
        if not name:
            name = karte["name_en"] or karte["name_de"] or ""
            if (karte["stage"] or "") == "LEVEL-UP" and "lv" not in name.lower():
                name += " LV.X"
            elif karte["suffix"] and karte["suffix"].lower() not in name.lower():
                name += " " + karte["suffix"]
        suche = ["searchString=" + quote(name.strip()), "idCategory=51"]
        exp = row["cm_expansion"] if row and "cm_expansion" in row.keys() else None
        if exp:
            suche.append(f"idExpansion={exp}")
        direkt = bool(row["cm_eindeutig"]) if row and "cm_eindeutig" in row.keys() else False
        return {"url": f"https://www.cardmarket.com/{loc}/Pokemon/Products/Search?"
                       + "&".join(suche), "genau": False, "direkt": direkt}
    voll = url if url.startswith("http") else f"https://www.cardmarket.com/{loc}{url}"
    return {"url": voll + ("?" + "&".join(filter_) if filter_ else ""), "genau": genau,
            "direkt": True}


@app.get("/api/cards/{card_id}/cardmarket")
def card_cardmarket(card_id: str, sprache: str = "", ui: str = "de", zustand: str = ""):
    return cm_adresse(card_id, sprache, ui, zustand)


@app.post("/api/admin/sets_nachladen")
def admin_sets_nachladen(key: str = "", ids: str = ""):
    """Set-Zeilen und fehlende Karten eines internationalen Sets nachholen."""
    if not admin_ok(key):
        raise HTTPException(403, "Kein Zugriff")
    liste = [x.strip() for x in ids.split(",") if x.strip()]
    if not liste:
        con = get_db()
        merker = con.execute("SELECT value FROM kv WHERE key='jp_praefix'").fetchone()
        con.close()
        liste = [x for x in (merker["value"].split(",") if merker else []) if x and x != "-"]
    threading.Thread(target=_intl_sets_nachladen, args=(liste,), daemon=True).start()
    return {"ok": True, "sets": liste}


def _cm_urls_job(grenze=40000):
    """Die Cardmarket-Produktseiten vorab auflösen.

    Ein Aufruf je Karte an die Weiterleitung von pokemontcg.io, danach steht die Adresse
    in der Datenbank und der Link im Kartendetail ist von Anfang an der richtige. Läuft
    nur über Karten, die noch keine haben — beim zweiten Mal ist er in Sekunden durch."""
    con = get_db()
    offen = [(r["card_id"], r["ptc_id"]) for r in con.execute(
        "SELECT p.card_id, p.ptc_id FROM card_prices p JOIN cards c ON c.id = p.card_id"
        " WHERE p.cm_url IS NULL AND COALESCE(c.region,'intl') = 'intl'"
        " LIMIT ?", (grenze,))]
    con.close()
    if not offen:
        return 0
    # In Blöcken schreiben, nicht erst am Ende: der Lauf dauert für den ganzen Katalog
    # rund zwanzig Minuten, und ein Neustart hätte sonst alles verworfen.
    n = 0
    with httpx.Client(timeout=15, headers=UA, follow_redirects=False) as client:
        for start in range(0, len(offen), 400):
            block = offen[start:start + 400]
            with ThreadPoolExecutor(6) as pool:
                paare = list(pool.map(lambda x: (x[0], _cm_url_aufloesen(client, x[0], x[1])), block))
            con = get_db()
            for cid, url in paare:
                if url:
                    con.execute("INSERT INTO card_prices (card_id, cm_url) VALUES (?,?)"
                                " ON CONFLICT(card_id) DO UPDATE SET cm_url = excluded.cm_url",
                                (cid, url))
                    n += 1
            con.execute("INSERT OR REPLACE INTO kv (key,value) VALUES ('cm_urls_lauf', datetime('now'))")
            con.commit()
            con.close()
    _log(f"Cardmarket-Adressen: {n} von {len(offen)} aufgelöst")
    return n


# --- Scans, die TCGdex nicht hat --------------------------------------------
#
# Für 8.899 japanische Karten führt TCGdex kein Bild (`image: null`) — darunter die
# Originalserie von 1996. Ein Scan liegt trotzdem im Netz: TCGplayer zeigt zu jedem
# Produkt ein Foto, und TCGdex nennt in `variants_detailed[].thirdParty.tcgplayer` die
# Produktnummer. Aus ihr wird die Bildadresse gebaut; ausgeliefert wird sie wie jedes
# andere Bild über den eigenen Zwischenspeicher, nicht als Fremdlink im Browser.
TP_BILD = "https://product-images.tcgplayer.com/{tp}.jpg"


def _tp_id_holen(client, card_id, region):
    """Die TCGplayer-Produktnummer einer Karte bei TCGdex."""
    pfad = "ja" if region == "jp" else "en"
    try:
        r = client.get(f"https://api.tcgdex.net/v2/{pfad}/cards/{card_id}", timeout=20)
        if r.status_code != 200:
            return None
        d = r.json()
    except Exception:
        return None
    for v in d.get("variants_detailed") or []:
        tp = (v.get("thirdParty") or {}).get("tcgplayer")
        if tp:
            return int(tp)
    return None


def _tp_bilder_job(grenze=20000):
    """Karten ohne Scan mit dem Bild von TCGplayer versorgen.

    Zwei Schritte: erst die Karten, deren Produktnummer schon in `card_prices` steht
    (die kosten keinen einzigen Netzaufruf), dann der Rest über TCGdex."""
    con = get_db()
    schon = [(r["id"], r["tcgplayer_id"]) for r in con.execute(
        "SELECT c.id, p.tcgplayer_id FROM cards c JOIN card_prices p ON p.card_id = c.id"
        " WHERE p.tcgplayer_id IS NOT NULL AND c.image_de IS NULL AND c.image_en IS NULL"
        " AND COALESCE(c.image_alt,'') = '' LIMIT ?", (grenze,))]
    for cid, tp in schon:
        con.execute("UPDATE cards SET image_alt = ? WHERE id = ?", (TP_BILD.format(tp=tp), cid))
    con.commit()
    offen = [(r["id"], r["region"] or "intl") for r in con.execute(
        "SELECT c.id, c.region FROM cards c LEFT JOIN card_prices p ON p.card_id = c.id"
        " WHERE c.image_de IS NULL AND c.image_en IS NULL AND COALESCE(c.image_alt,'') = ''"
        " AND p.tcgplayer_id IS NULL ORDER BY c.release_date LIMIT ?", (grenze,))]
    con.close()
    _log(f"TCGplayer-Bilder: {len(schon)} aus vorhandenen Nummern, {len(offen)} werden abgefragt")
    gefunden = len(schon)
    with httpx.Client(timeout=25, headers=UA) as client:
        for start in range(0, len(offen), 300):
            block = offen[start:start + 300]
            with ThreadPoolExecutor(6) as pool:
                paare = list(pool.map(lambda x: (x[0], _tp_id_holen(client, x[0], x[1])), block))
            con = get_db()
            for cid, tp in paare:
                if not tp:
                    continue
                con.execute("UPDATE cards SET image_alt = ? WHERE id = ?",
                            (TP_BILD.format(tp=tp), cid))
                con.execute("INSERT INTO card_prices (card_id, tcgplayer_id) VALUES (?,?)"
                            " ON CONFLICT(card_id) DO UPDATE SET tcgplayer_id = excluded.tcgplayer_id",
                            (cid, tp))
                gefunden += 1
            con.execute("INSERT OR REPLACE INTO kv (key,value)"
                        " VALUES ('tp_bilder_lauf', datetime('now'))")
            con.commit()
            con.close()
    _log(f"TCGplayer-Bilder: {gefunden} Karten haben jetzt einen Scan")
    return gefunden


# Für die japanischen Sets von 2000 bis 2006 (neo, VS, web, e-Card, PCG) führt TCGdex
# keine TCGplayer-Nummer — für diese 1.400 Karten gäbe es sonst weiter keinen Scan.
# TCGplayer selbst führt sie: sein Katalog kennt die japanischen Sets unter denselben
# englischen Namen, die auch hier stehen („The Town on No Map", 92 Karten — genau unsere
# Zahl), und nennt zu jedem Produkt die Kartennummer. Darüber wird zugeordnet: Set über
# den Namen, Karte über die Nummer. Ein Lauf sind rund sechzig Abfragen, danach liegen
# die Bilder im eigenen Zwischenspeicher.
TP_SUCHE = "https://mp-search-api.tcgplayer.com/v1/search/request?q=&isList=false"
TP_KOPF = {"Content-Type": "application/json",
           "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128 Safari/537.36"}


TP_SEITE = 48        # mehr als 50 lehnt die Suche mit „Bad Request" ab


def _tp_anfrage(client, setname=None, von=0, groesse=TP_SEITE):
    term = {"productLineName": ["pokemon-japan"]}
    if setname:
        term["setName"] = [setname]
    rumpf = {"algorithm": "sales_synonym_v2", "from": von, "size": groesse,
             "filters": {"term": term, "range": {}, "match": {}},
             "context": {"cart": {}, "shippingCountry": "US"},
             "settings": {"useFuzzySearch": True, "didYouMean": {}}, "sort": {}}
    r = client.post(TP_SUCHE, json=rumpf, headers=TP_KOPF, timeout=30)
    r.raise_for_status()
    return (r.json().get("results") or [{}])[0]


def _tp_norm(name):
    """Setnamen vergleichbar machen.

    TCGplayer stellt dem Namen das Kürzel der Reihe voran („S8b: VMAX Climax"), der
    Cardmarket-Katalog nicht („VMAX Climax"); dazu kommen Akzent und Zeichensetzung
    („Pokémon Card VS" gegen „Pokemon VS")."""
    n = str(name or "").lower().replace("é", "e")
    n = re.sub(r"^[a-z0-9.+-]{1,7}:\s*", "", n)      # führendes Reihenkürzel
    n = re.sub(r"\b(pokemon|card|jp)\b", " ", n)
    return re.sub(r"[^a-z0-9]", "", n)


def _tp_nummer(text):
    """„059/092" → „59". Führende Nullen fliegen, damit „059" und „59" dasselbe sind."""
    n = str(text or "").split("/")[0].strip().upper()
    return n.lstrip("0") or n


def _tp_katalog_job():
    con = get_db()
    offen = {}
    for r in con.execute(
            "SELECT c.id, c.set_id, c.local_id, s.name_en FROM cards c JOIN sets s ON s.id = c.set_id"
            " WHERE COALESCE(c.region,'intl') = 'jp' AND c.image_de IS NULL AND c.image_en IS NULL"
            " AND COALESCE(c.image_alt,'') = '' AND s.name_en IS NOT NULL"):
        offen.setdefault((r["set_id"], r["name_en"]), []).append((r["id"], r["local_id"]))
    con.close()
    if not offen:
        return 0
    gefunden = 0
    with httpx.Client(timeout=40) as client:
        try:
            kopf = _tp_anfrage(client, groesse=1)
            tp_sets = {}
            for a in (kopf.get("aggregations") or {}).get("setName", []):
                tp_sets.setdefault(a["value"].strip().lower(), a["urlValue"])
                tp_sets.setdefault(_tp_norm(a["value"]), a["urlValue"])
        except Exception as exc:
            _log("TCGplayer-Katalog: Setliste fehlgeschlagen:", exc)
            return 0
        _log(f"TCGplayer-Katalog: {len(offen)} Sets offen, {len(tp_sets)} Sets im Katalog")
        for (sid, name_en), karten in sorted(offen.items()):
            url = (tp_sets.get((name_en or "").strip().lower())
                   or tp_sets.get(_tp_norm(name_en)))
            if not url:
                continue

            produkte = {}
            for von in range(0, 1200, TP_SEITE):
                try:
                    d = _tp_anfrage(client, url, von, TP_SEITE)
                except Exception as exc:
                    _log(f"TCGplayer-Katalog: {sid} Seite {von} fehlgeschlagen: {exc}")
                    break
                treffer = d.get("results") or []
                for p in treffer:
                    nr = _tp_nummer((p.get("customAttributes") or {}).get("number"))
                    if nr and nr not in produkte:
                        produkte[nr] = (int(p["productId"]), p.get("productName"))
                if len(treffer) < TP_SEITE:
                    break
            if not produkte:
                continue
            con = get_db()
            n = 0
            for cid, local_id in karten:
                treffer = produkte.get(_tp_nummer(local_id))
                if not treffer:
                    continue
                pid, pname = treffer
                con.execute("UPDATE cards SET image_alt = ? WHERE id = ?", (TP_BILD.format(tp=pid), cid))
                con.execute("INSERT INTO card_prices (card_id, tcgplayer_id) VALUES (?,?)"
                            " ON CONFLICT(card_id) DO UPDATE SET tcgplayer_id = excluded.tcgplayer_id",
                            (cid, pid))
                # Der englische Produktname füllt nebenbei die letzten japanischen Namen.
                if pname:
                    con.execute("UPDATE cards SET name_en = ? WHERE id = ?"
                                " AND COALESCE(name_en,'') = ''", (pname, cid))
                n += 1
            con.commit()
            con.close()
            gefunden += n
            _log(f"TCGplayer-Katalog: {sid} ({name_en}) — {n} von {len(karten)} zugeordnet")
    con = get_db()
    con.execute("INSERT OR REPLACE INTO kv (key,value) VALUES ('tp_katalog_lauf', datetime('now'))")
    con.commit()
    con.close()
    _log(f"TCGplayer-Katalog: {gefunden} Karten haben jetzt einen Scan")
    return gefunden


@app.post("/api/admin/tp_katalog")
def admin_tp_katalog(key: str = ""):
    if not admin_ok(key):
        raise HTTPException(403, "Kein Zugriff")
    threading.Thread(target=_tp_katalog_job, daemon=True).start()
    return {"ok": True}


@app.post("/api/admin/tp_bilder")
def admin_tp_bilder(key: str = "", grenze: int = 20000):
    if not admin_ok(key):
        raise HTTPException(403, "Kein Zugriff")
    threading.Thread(target=lambda: _tp_bilder_job(grenze), daemon=True).start()
    return {"ok": True}


@app.post("/api/admin/cm_urls")
def admin_cm_urls(key: str = "", grenze: int = 40000):
    if not admin_ok(key):
        raise HTTPException(403, "Kein Zugriff")
    threading.Thread(target=lambda: _cm_urls_job(grenze), daemon=True).start()
    return {"ok": True}


@app.post("/api/admin/zweitquelle")
def admin_zweitquelle(key: str = "", set_id: str = ""):
    if not admin_ok(key):
        raise HTTPException(403, "Kein Zugriff")
    threading.Thread(target=lambda: _ptcgio_job(set_id or None), daemon=True).start()
    return {"ok": True}


def _aufraeumen_job():
    """Leere Gast-Binder (ohne Konto, ohne Karten, älter als 2 Tage) und abgelaufene
    Sitzungen entfernen."""
    con = get_db()
    con.execute("DELETE FROM binders WHERE user_id IS NULL AND (items IS NULL OR items = '[]')"
                " AND created_at < datetime('now', '-2 days')")
    # Sitzungen laufen nach einem Jahr ab; verwaiste (Konto gelöscht) fliegen sofort raus
    con.execute("DELETE FROM sessions WHERE created_at < datetime('now', '-365 days')"
                " OR user_id NOT IN (SELECT id FROM users)")
    con.execute("DELETE FROM stripe_events WHERE verarbeitet_am < datetime('now', '-90 days')")
    con.commit()
    con.close()


# Obergrenzen für den Bild-Cache (MB). Karten-/Sprite-Bilder sind jederzeit nachladbar,
# deshalb dürfen die ältesten weg, sobald es eng wird. Artwork-Bilder gehören Nutzern und
# werden hier nie angefasst — die verschwinden nur mit dem Artwork oder dem Konto.
# Seit 06.09.2026 so bemessen, dass der ganze Katalog in beiden Sprachen hineinpasst
# (33.746 Karten: low ≈ 25 KB, high ≈ 85 KB je Bild) — bei 144 GB freier Platte gab es
# keinen Grund, bei 400 MB die ältesten Bilder wieder wegzuwerfen und neu zu holen.
CACHE_GRENZEN = {"cards/low": 2500, "cards/high": 6500, "cards/print": 2000, "dex": 200, "sym": 50}


def _cache_job():
    """Bild-Cache auf die Obergrenzen stutzen (älteste Zugriffe zuerst). Ohne das wächst
    er unbegrenzt — der Katalog hat 33.700 Karten in zwei Sprachen."""
    for teil, grenze_mb in CACHE_GRENZEN.items():
        ordner = CACHE.joinpath(*teil.split("/"))
        if not ordner.is_dir():
            continue
        try:
            dateien = [(f, f.stat()) for f in ordner.iterdir() if f.is_file()]
        except OSError:
            continue
        gesamt = sum(st.st_size for _, st in dateien)
        grenze = grenze_mb * 1024 * 1024
        if gesamt <= grenze:
            continue
        # am längsten nicht gelesen zuerst löschen
        for f, st in sorted(dateien, key=lambda x: x[1].st_atime):
            try:
                f.unlink()
                gesamt -= st.st_size
            except OSError:
                pass
            if gesamt <= grenze * 0.9:
                break
        _log(f"Cache {teil}: auf {gesamt/1024/1024:.0f} MB gestutzt")


NACHTLAUF_STUNDE, NACHTLAUF_MINUTE = 4, 30     # Serverzeit (Europe/Berlin)


def _tageslauf_faellig(letzter_utc):
    """Ist der tägliche Preislauf fällig? Fällig ab 04:30 Uhr Serverzeit, sobald der letzte
    Lauf vor diesem Zeitpunkt lag. `letzter_utc` ist der kv-Wert (UTC, wie _now_text)."""
    from datetime import datetime
    jetzt = datetime.now()
    heute = jetzt.replace(hour=NACHTLAUF_STUNDE, minute=NACHTLAUF_MINUTE, second=0, microsecond=0)
    if jetzt < heute:
        return False
    if not letzter_utc:
        return True
    try:
        letzter = datetime.strptime(letzter_utc, "%Y-%m-%d %H:%M:%S") + (datetime.now() - datetime.utcnow())
    except ValueError:
        return True
    return letzter < heute


def _sekunden_bis_nachtlauf():
    """Wie lange bis zum nächsten 04:30 Uhr — damit der Takt nicht eine Stunde daneben liegt."""
    from datetime import datetime, timedelta
    jetzt = datetime.now()
    ziel = jetzt.replace(hour=NACHTLAUF_STUNDE, minute=NACHTLAUF_MINUTE, second=30, microsecond=0)
    if ziel <= jetzt:
        ziel += timedelta(days=1)
    return (ziel - jetzt).total_seconds()


def _markt_job():
    """Tagesstand des Markts rechnen und auf Plausibilität prüfen.

    Läuft direkt nach dem Preislauf. Fällt der Katalogwert gegenüber dem Vortag um mehr als
    5 %, geht eine Meldung an den Betreiber, bevor die Marktseite falsche Nachrichten zeigt."""
    if not _markt:
        return
    con = get_db()
    try:
        _markt.rueckwirkend_fuellen(con)
        ergebnis = _markt.markt_job(con)
        warnung = _markt.pruefen(con)
    finally:
        con.close()
    _log("Markt-Tagesstand:", ergebnis)
    if warnung:
        betreiber_melden(warnung)


_alarme = None   # wird unten beim Einhängen des Moduls gesetzt


def _alarme_job():
    """Preis-Alarme prüfen; sonntags dazu der Wochenrückblick. Läuft nach dem Markt-Job."""
    if not _alarme:
        return
    try:
        _log("Alarme:", _alarme.tagesjob(get_db, _mail_senden, _mail_konfiguriert))
    except Exception as exc:
        _log("Alarm-Job fehlgeschlagen:", exc)
    if datetime.datetime.now().weekday() == 6:
        try:
            _log("Digest:", _alarme.wochenjob(get_db, _mail_senden, _mail_konfiguriert))
        except Exception as exc:
            _log("Digest-Job fehlgeschlagen:", exc)


_JOB_POOL = ThreadPoolExecutor(4)


def _job(name, fn, limit=1800):
    """Ein Hintergrund-Job mit Zeitlimit und Stand in kv (job:<name>:letzter/dauer/fehler).
    Vorher liefen alle Jobs nacheinander im selben Thread: ein hängender Cardmarket-Download
    hielt den Preislauf und die Verdichtung auf. Nach dem Limit geht der Takt weiter; der Job
    selbst läuft im Pool zu Ende (ein Thread lässt sich nicht abbrechen)."""
    import concurrent.futures
    start = time.time()
    fehler = ""
    fut = _JOB_POOL.submit(fn)
    try:
        fut.result(timeout=limit)
    except concurrent.futures.TimeoutError:
        fehler = f"Zeitlimit {limit}s überschritten"
        log.warning("Job %s: %s", name, fehler)
    except Exception as exc:
        fehler = str(exc)[:300]
        log.warning("Job %s fehlgeschlagen: %s", name, exc)
    try:
        con = get_db()
        for k, v in (("letzter", datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")),
                     ("dauer", f"{time.time() - start:.0f}"), ("fehler", fehler)):
            con.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (f"job:{name}:{k}", v))
        con.commit()
        con.close()
    except Exception as exc:
        log.warning("Job-Stand %s: %s", name, exc)
    return not fehler


def _job_stand():
    """Die Jobs mit letztem Lauf, Dauer und Fehler – für die Betriebsseite."""
    con = get_db()
    zeilen = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM kv WHERE key LIKE 'job:%'")}
    con.close()
    namen = sorted({k.split(":")[1] for k in zeilen})
    return [{"job": n, "letzter": zeilen.get(f"job:{n}:letzter", ""), "dauer": zeilen.get(f"job:{n}:dauer", ""),
             "fehler": zeilen.get(f"job:{n}:fehler", "")} for n in namen]


def _hintergrund_takt():
    import time as _time
    while True:
        try:
            _aufraeumen_job()
            _cache_job()
            con = get_db()
            letzter = con.execute("SELECT value FROM kv WHERE key='preishistorie_lauf'").fetchone()
            offen_row = con.execute("SELECT value FROM kv WHERE key='preise_offen'").fetchone()
            con.close()
            offen = int((offen_row["value"] if offen_row else "0") or 0)
            # Solange der Katalog noch nicht erfasst ist, läuft es stündlich weiter. Danach
            # einmal am Tag zu einer FESTEN Uhrzeit: Cardmarket veröffentlicht das
            # Preisverzeichnis gegen 02:45 Uhr, der Lauf ist ab 04:30 Uhr fällig. Vorher galt
            # „23 Stunden nach dem letzten Lauf" — das wanderte täglich eine Stunde nach
            # hinten und hätte irgendwann einen Cardmarket-Tag übersprungen.
            if offen > 0:
                faellig = not letzter or letzter["value"] < datetime_str_vor(0.75)
            else:
                faellig = _tageslauf_faellig(letzter["value"] if letzter else "")
            if faellig:
                # Die Reihenfolge ist die Rangfolge der Quellen, von der schwächsten zur
                # stärksten: TCGdex legt die Grundlage und berichtigt die Ausprägungen,
                # das Cardmarket-Preisverzeichnis überschreibt sie mit den echten Zahlen,
                # und die Zweitquelle füllt nur noch, was danach leer geblieben ist.
                _job("preishistorie", _preishistorie_job, 3600)
                _job("cm_download", _cm_download_job, 900)
                _job("cm_import", _cm_import_job, 1800)
                _job("zweitquelle", _ptcgio_job, 1800)
                _job("cm_urls", lambda: _cm_urls_job(3000), 1200)      # neue Karten bekommen ihre Produktseite
                # Neue Karten ohne Scan: erst die günstige Quelle (TCGdex nennt die
                # TCGplayer-Nummer), dann der Katalogabgleich für ganze Sets.
                _job("tp_bilder", lambda: _tp_bilder_job(3000), 1200)
                _job("tp_katalog", _tp_katalog_job, 1200)

                def _verdichten():
                    con = get_db()
                    _historie_verdichten(con)
                    con.close()
                _job("verdichten", _verdichten, 900)
                try:
                    _markt_job()
                except Exception as exc:
                    _log("Markt-Tagesstand fehlgeschlagen:", exc)
                _alarme_job()
        except Exception:
            pass
        # Stündlich für Aufräumen und Cache; kurz vor 04:30 Uhr genau dorthin.
        _time.sleep(max(60, min(3600, _sekunden_bis_nachtlauf())))


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


def admin_ok(key: str, request: Request = None) -> bool:
    """Vergleich zeitkonstant (hmac.compare_digest); der Schlüssel darf auch als Kopfzeile
    X-Admin-Key kommen, damit er nicht in den nginx-Zugriffsprotokollen landet."""
    import hmac as _hmac
    echt = _admin_key()
    if not echt:
        return False
    kandidat = key or ""
    if request is not None and not kandidat:
        kandidat = request.headers.get("x-admin-key", "")
    return _hmac.compare_digest(kandidat, echt)


# --- Meta, Admin-Kennzahlen, Kartensuche, Seltenheiten, Import → katalog.py --------------------
_abschnitt("katalog")


# --- Konten, Sitzungen, Limits, E-Mail-Versand → auth.py ---------------------------------------
_abschnitt("auth")


# --- Stripe, Tarife & Credits ------------------------------------------------
#
# Vollständig in abo.py: Tarifdefinition, Credit-Konto mit Buchungsjournal, Checkout
# (Abos + Credit-Pakete), Kundenportal, Kündigung und der Stripe-Webhook mit
# Idempotenz-Sperre. Ein Fehler dort darf den Dienst nicht blockieren.

try:
    _abo_api = abo.register(
        app, get_db=get_db, current_user=_current_user, require_user=_require_user,
        env=_env, mail_senden=_mail_senden, mail_konfiguriert=_mail_konfiguriert, basis=BASE,
        melden=betreiber_melden,
    )
except Exception as _e:  # pragma: no cover
    _log("Abo-Modul nicht geladen:", _e)
    _abo_api = None


# --- Kartenpreise (Cardmarket-Trend via TCGdex, 24h-Cache) ------------------

def _fetch_price_voll(client, card_id, region="intl"):
    """Eine Kartenantwort von TCGdex auswerten.

    → dict(id, ok, eur, eur_holo, usd, usd_holo, tcgplayer_id, cm_produkt, spanne,
           varianten, tp_keys)

    Dieselbe Antwort trägt beide Märkte: Cardmarket in Euro und TCGplayer in Dollar.
    Seit dem 03.09.2026 wird sie zu Ende gelesen:

    * `variants_detailed` führt je Druckvariante eine eigene Cardmarket-Produktnummer
      und eigene Preise. Daraus entsteht `varianten` — der Preis, der wirklich zur
      gewählten Ausprägung gehört, statt eines Grundpreises für alles.
    * Die Schlüssel des TCGplayer-Blocks sagen, welche Ausprägungen überhaupt gehandelt
      werden. Das ist die verlässlichste Variantenquelle, die es umsonst gibt.

    `ok` unterscheidet „abgerufen, kein Preis vorhanden" von „Abruf fehlgeschlagen".
    Ohne diese Unterscheidung sahen beide Fälle gleich aus (überall None), und ein
    Ausfall der Quelle hätte beim nächsten Lauf sämtliche Preise auf NULL gesetzt."""
    leer = {"id": card_id, "ok": False, "eur": None, "eur_holo": None, "usd": None,
            "usd_holo": None, "tcgplayer_id": None, "cm_produkt": None, "spanne": {},
            "varianten": {}, "tp_keys": [], "merkmale": None}
    # Die Sprache kommt aus der Region, nicht aus der Schreibweise der Kennung: die alte
    # Heuristik („beginnt mit einem Großbuchstaben") hielt jedes japanische Set mit kleiner
    # Kennung (sv1a, neo1 …) für ein internationales und fragte den falschen Katalog.
    lang = "ja" if region == "jp" else "en"
    try:
        r = client.get(f"{TCGDEX}/{lang}/cards/{jp_quell_id(card_id)}")
        if r.status_code == 404:
            # Die Karte kennt die Quelle nicht — das ist ein Ergebnis, kein Ausfall. Als
            # Ausfall gezählt würden solche Karten jeden Lauf neu versucht und könnten
            # die Abbruchschwelle allein tragen.
            return dict(leer, ok=True)
        if r.status_code != 200:
            return leer
        d = r.json()
    except Exception:
        return leer

    preise = d.get("pricing") or {}
    cm = preise.get("cardmarket") or {}
    tp = preise.get("tcgplayer") or {}

    def cm_wert(block, *schluessel):
        # Eine 0 ist bei Cardmarket keine Preisangabe, sondern eine fehlende.
        for k in schluessel:
            if block.get(k):
                return round(float(block[k]), 2)
        return None

    eur = cm_wert(cm, "trend", "avg30", "avg", "low")
    holo = cm_wert(cm, "trend-holo", "avg30-holo", "avg-holo", "low-holo")

    tp_keys = [k for k in tp if k not in ("unit", "updated") and isinstance(tp.get(k), dict)]
    usd = usd_holo = None
    u_low = u_mid = u_high = None
    tid = None
    for name in ("normal", "1st-edition", "unlimited"):
        v = tp.get(name)
        if isinstance(v, dict) and v.get("marketPrice") is not None:
            usd = round(float(v["marketPrice"]), 2)
            u_low, u_mid, u_high = v.get("lowPrice"), v.get("midPrice"), v.get("highPrice")
            tid = tid or v.get("productId")
            break
    for name in ("holofoil", "reverse-holofoil", "1st-edition-holofoil"):
        v = tp.get(name)
        if isinstance(v, dict) and v.get("marketPrice") is not None:
            usd_holo = round(float(v["marketPrice"]), 2)
            if u_low is None:
                u_low, u_mid, u_high = v.get("lowPrice"), v.get("midPrice"), v.get("highPrice")
            tid = tid or v.get("productId")
            break
    if usd is None and usd_holo is not None:
        usd = usd_holo
    if tid is None:
        for v in tp.values():
            if isinstance(v, dict) and v.get("productId"):
                tid = v["productId"]; break

    # --- Preis je Druckvariante -------------------------------------------------
    # `variants_detailed` nennt je Ausprägung ihr eigenes Cardmarket-Produkt. Wo es das
    # gibt, ist der Preis eindeutig; wo nicht, bleibt es bei der alten Zuordnung
    # (Trend = Grundausgabe, Trend-Holo = zweite Ausgabe).
    varianten = {}
    for v in d.get("variants_detailed") or []:
        art = TCGDEX_VARIANTE.get(str(v.get("type") or "").lower())
        if not art:
            continue
        vcm = ((v.get("pricing") or {}).get("cardmarket") or {})
        wert = cm_wert(vcm, "trend-holo", "avg30-holo") if art in ("reverse", "holo") else None
        wert = wert or cm_wert(vcm, "trend", "avg30", "avg", "low")
        produkt = (v.get("thirdParty") or {}).get("cardmarket")
        if wert is not None:
            # Erster Treffer gewinnt: TCGdex listet dieselbe Ausprägung mehrfach, wenn eine
            # Karte in mehreren Cardmarket-Produkten steckt (Base Set: Unlimited und 1st Edition).
            varianten.setdefault(art, {"eur": wert, "produkt": produkt})
    if eur is None and varianten.get("normal"):
        eur = varianten["normal"]["eur"]
    if holo is None:
        for art in ("reverse", "holo"):
            if varianten.get(art):
                holo = varianten[art]["eur"]; break

    # Fähigkeiten und Attacken derselben Antwort: sie kosten keinen Abruf und sind das
    # einzige Merkmal, mit dem sich zwei gleichnamige Karten einer Erweiterung sicher
    # unterscheiden lassen (siehe `cm_zuordnung_merkmale`).
    merkmale = [a.get("name") for a in (d.get("abilities") or []) if a.get("name")]
    merkmale += [a.get("name") for a in (d.get("attacks") or []) if a.get("name")]
    if d.get("suffix"):
        merkmale.append(str(d["suffix"]))

    return {"id": card_id, "ok": True, "eur": eur, "eur_holo": holo, "usd": usd,
            "usd_holo": usd_holo, "tcgplayer_id": tid, "cm_produkt": cm.get("idProduct"),
            "merkmale": " | ".join(merkmale) or None,
            "spanne": {"eur_low": cm.get("low") or None, "eur_avg30": cm.get("avg30") or None,
                       "usd_low": u_low, "usd_mid": u_mid, "usd_high": u_high},
            "varianten": varianten, "tp_keys": tp_keys}


# TCGdex nennt die Ausprägung in `variants_detailed` anders als im TCGplayer-Block.
TCGDEX_VARIANTE = {"normal": "normal", "reverse": "reverse", "holo": "holo",
                   "firstedition": "first", "first-edition": "first"}


@app.post("/api/preise")
async def preise(request: Request):
    """EUR-Preise (Cardmarket-Trend) für Karten-IDs; fehlende werden nachgeladen.

    Preise sind der Kern-Nutzen und deshalb auch ohne Konto sichtbar: Gäste bekommen
    den Cache plus bis zu 120 frische Preise je Anfrage (globales Tagesbudget),
    Free-Konten aktualisieren 1x pro Tag, Pro sofort und unbegrenzt."""
    user = _current_user(request)
    data = await request.json()
    ids = list(dict.fromkeys(str(i) for i in (data.get("ids") or [])))[:1500]
    frei_gedrosselt = bool(user) and not abo.darf_preise_live(user) and user.get("preise_tag") == _heute()
    con = get_db()
    result, holo, fehlt = {}, {}, []
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        # Wo Cardmarket über TCGdex nichts hergibt, zählt die Zahl der Zweitquelle. Ohne
        # diesen Rückfall blieben in Sammlung und Binder Felder leer, obwohl ein Preis da war.
        rows = con.execute(
            "SELECT card_id, COALESCE(eur, eur_geschaetzt) eur, eur_holo, updated_at"
            " FROM card_prices WHERE card_id IN (%s)"
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
    nachgetragen = []
    if nachgeladen:
        # Die Abrufe laufen im Threadpool; im Event-Loop stand der ganze Dienst 5 bis 15 Sekunden.
        for e in await run_in_threadpool(_preise_holen, nachgeladen):
            if not e["ok"]:
                continue          # Ausfall der Quelle: den vorhandenen Stand nicht anfassen
            cid, eur, eur_holo, pid = e["id"], e["eur"], e["eur_holo"], e["cm_produkt"]
            # Gehört das Cardmarket-Produkt noch anderen Karten, ist der Preis nicht
            # dieser Karte zuzuordnen — dieselbe Regel wie im Nachtlauf.
            if pid is not None and eur is not None:
                andere = con.execute(
                    "SELECT COUNT(*) c FROM card_prices WHERE cm_produkt = ? AND card_id <> ?",
                    (pid, cid)).fetchone()["c"]
                if andere:
                    eur = eur_holo = None
            # Dieselbe Regel wie im Nachtlauf: die zweite Cardmarket-Reihe gehört zu einer
            # zweiten Ausgabe. Gibt es die nicht, gehört die Zahl zu nichts.
            zweite = con.execute(
                "SELECT COALESCE(has_normal,0) = 1 AND (COALESCE(has_reverse,0) = 1"
                " OR COALESCE(has_holo,0) = 1) AS ja FROM cards WHERE id = ?", (cid,)).fetchone()
            if not zweite or not zweite["ja"]:
                eur_holo = None
            result[cid] = eur; holo[cid] = eur_holo
            # INSERT OR REPLACE würde usd, tcgplayer_id und cm_produkt mitlöschen.
            # Wo das offizielle Preisverzeichnis diese Woche eine Zahl geliefert hat,
            # bleibt sie stehen — dieselbe Rangfolge wie im Nachtlauf.
            con.execute(
                "INSERT INTO card_prices (card_id, eur, eur_holo, cm_produkt, preise_json,"
                " tp_keys, updated_at)"
                " VALUES (?,?,?,?,?,?,datetime('now'))"
                " ON CONFLICT(card_id) DO UPDATE SET"
                " eur=CASE WHEN COALESCE(card_prices.cm_import_am,'') >= ? THEN card_prices.eur"
                "  ELSE excluded.eur END,"
                " eur_holo=CASE WHEN COALESCE(card_prices.cm_import_am,'') >= ?"
                "  THEN card_prices.eur_holo ELSE excluded.eur_holo END,"
                " cm_produkt=COALESCE(excluded.cm_produkt, card_prices.cm_produkt),"
                " preise_json=COALESCE(excluded.preise_json, card_prices.preise_json),"
                " tp_keys=COALESCE(excluded.tp_keys, card_prices.tp_keys),"
                " updated_at=excluded.updated_at",
                (cid, eur, eur_holo, pid,
                 json.dumps(e.get("varianten") or {}) if e.get("varianten") else None,
                 ",".join(e.get("tp_keys") or []) or None,
                 _tag_minus(7), _tag_minus(7)))
            if eur is not None:
                nachgetragen.append((cid, eur, None))
        # Ein einzeln nachgeladener Preis ist kein Messtag der Reihe — er trägt nur die
        # Karten, die gerade jemand ansieht.
        if nachgetragen:
            _historie_eintragen(con, nachgetragen, "nachladen", messtag=False,
                                ids=[c for c, _e, _u in nachgetragen])
        if user:
            con.execute("UPDATE users SET preise_tag = ? WHERE id = ?", (_heute(), user["id"]))
        else:
            tag = con.execute("SELECT value FROM kv WHERE key='gast_preise'").fetchone()
            heute, zaehler = (tag["value"].split(":") + ["0"])[:2] if tag and ":" in tag["value"] else (_heute(), "0")
            neu = (int(zaehler) if heute == _heute() else 0) + len(nachgeladen)
            con.execute("INSERT OR REPLACE INTO kv (key,value) VALUES ('gast_preise', ?)", (f"{_heute()}:{neu}",))
        con.commit()
    con.close()
    # Der Messtag der Preise. Ohne ihn stand im Binder eine Zahl ohne Alter — und die
    # Nachfrage „warum stimmt der Wert nicht" lässt sich nur mit dem Datum beantworten.
    stand = None
    if result:
        con2 = get_db()
        zeile = con2.execute("SELECT MAX(updated_at) s FROM card_prices WHERE card_id IN (%s)"
                             % ",".join("?" * len(list(result)[:500])), list(result)[:500]).fetchone()
        con2.close()
        stand = (zeile["s"] or "")[:10] or None
    return {"preise": result, "holo": holo, "offen": max(0, len(fehlt) - len(nachgeladen)),
            "gedrosselt": frei_gedrosselt, "stand": stand}


def _preise_holen(ids):
    """Netzabrufe gesammelt im Threadpool — der Aufrufer bleibt frei.

    → Liste der Ergebnisse von `_fetch_price_voll`. Der Status wird durchgereicht, damit
    ein Ausfall der Quelle nicht als „kein Preis" in der Datenbank landet."""
    con = get_db()
    regionen = {r["id"]: (r["region"] or "intl") for r in con.execute(
        "SELECT id, region FROM cards WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)}
    con.close()
    with httpx.Client(timeout=20, headers=UA) as client:
        with ThreadPoolExecutor(8) as pool:
            return list(pool.map(
                lambda c: _fetch_price_voll(client, c, regionen.get(c, "intl")), ids))


def datetime_str_vor(stunden):
    from datetime import datetime, timedelta
    return (datetime.utcnow() - timedelta(hours=stunden)).strftime("%Y-%m-%d %H:%M:%S")


# --- Bild-Cache und Bild-Endpunkte → bilder.py -------------------------------------------------
_abschnitt("bilder")


# --- Binder anlegen, laden, speichern, Wertverlauf → binder.py ---------------------------------
_abschnitt("binder")


# --- PDF-Export: Platzhalter, Checkliste, Kaufliste → pdf.py -----------------------------------
_abschnitt("pdf")


# --- Binder per Link teilen: Schalter, Pause, Ablauf, QR-Code → teilen.py ---------------
_abschnitt("teilen")


# --- KI-Artwork-Seiten (eigenes Modul artwork.py, eingehängt wie eine Integration) ----
#
# Bindet Tabelle `artworks`, Kontingent-Spalten in `users` und die Endpunkte /api/artwork/*
# ein. Ein Fehler dort darf den Start der App nicht blockieren.

try:
    import artwork as _artwork  # noqa: E402
    _artwork_kennzahlen = _artwork.register(
        app, get_db=get_db, current_user=_current_user, require_user=_require_user, ist_pro=_ist_pro,
        load_binder=_load_binder, card_image_path=_card_image_path, dex_image_path=_dex_image_path,
        pdf_wasserzeichen=_pdf_wasserzeichen,
        env=_env, CACHE=CACHE, abo=abo, bestaetigt=_bestaetigt,
    )
except Exception as _e:  # pragma: no cover
    _log("Artwork-Modul nicht geladen:", _e)
    _artwork_kennzahlen = None


# --- KI-Themenseiten (Modul themen.py) --------------------------------------
#
# Bindet `card_art_tags` samt Volltextindex und die Endpunkte /api/themen/* ein.

try:
    import themen as _themen  # noqa: E402
    _themen_kennzahlen = _themen.register(
        app, get_db=get_db, current_user=_current_user, require_user=_require_user, ist_pro=_ist_pro,
        card_image_path=_card_image_path, env=_env, CACHE=CACHE, abo=abo, admin_key=_admin_key,
    )
except Exception as _e:  # pragma: no cover
    _log("Themen-Modul nicht geladen:", _e)
    _themen_kennzahlen = None


# --- Binder aus Fotos (Modul fotoimport.py) ---------------------------------
#
# Bindet `card_hashes` und die Endpunkte /api/import/* ein.

try:
    import fotoimport as _fotoimport  # noqa: E402
    _foto_kennzahlen = _fotoimport.register(
        app, get_db=get_db, current_user=_current_user, require_user=_require_user,
        env=_env, CACHE=CACHE, admin_key=_admin_key,
    )
except Exception as _e:  # pragma: no cover
    _log("Fotoimport-Modul nicht geladen:", _e)
    _foto_kennzahlen = None


# --- Vitrine: Binder öffentlich zeigen (Modul vitrine.py) -------------------

try:
    import vitrine as _vitrine  # noqa: E402
    _vitrine_kennzahlen = _vitrine.register(
        app, get_db=get_db, current_user=_current_user, require_user=_require_user,
        env=_env, admin_key=_admin_key, load_binder=_load_binder, abo=abo, drossel=_drossel,
        items_wert=_items_wert,
    )
except Exception as _e:  # pragma: no cover
    _log("Vitrine-Modul nicht geladen:", _e)
    _vitrine_kennzahlen = None

# Die Vitrine wird nach dem Artwork-Modul geladen, ihre Freigabeprüfung wird dort aber
# gebraucht (Alter, Anzeigename, Textprüfung beim Veröffentlichen einer Kunstseite).
# Deshalb erst hier verbinden — eine Regel, zwei Aufrufer.
try:
    _artwork._dep["vitrine_pruefen"] = _vitrine._dep.get("vitrine_pruefen")
except Exception as _e:  # pragma: no cover
    _log("Kunstseiten-Prüfung nicht verbunden:", _e)


# --- Betreiber-Übersicht fürs Empire-Dashboard (Modul admin_uebersicht.py) ----

try:
    import admin_uebersicht as _admin_uebersicht  # noqa: E402
    _admin_uebersicht.register(app, get_db=get_db, env=_env, admin_key=_admin_key, abo=abo)
except Exception as _e:  # pragma: no cover
    _log("Betreiber-Übersicht nicht geladen:", _e)


# --- Sammlung: was wirklich besessen wird (Modul sammlung.py) ---------------

try:
    import sammlung as _sammlung  # noqa: E402
    _sammlung_kennzahlen = _sammlung.register(
        app, get_db=get_db, current_user=_current_user, require_user=_require_user, env=_env,
        card_query=_card_query, card_select=_CARD_SELECT, card_brief=_card_brief,
        preis_fuer_posten=preis_fuer_posten, ist_bezahlt=_ist_pro,
    )
except Exception as _e:  # pragma: no cover
    _log("Sammlung-Modul nicht geladen:", _e)
    _sammlung_kennzahlen = None


# --- Auswertungen: Sammlung & Markt (Modul analytics.py) -------------------

try:
    import analytics as _analytics  # noqa: E402
    _analytics_kennzahlen = _analytics.register(
        app, get_db=get_db, require_user=_require_user, ist_pro=_ist_pro,
        ist_pro_stufe=_ist_pro_stufe, preis_fuer_posten=preis_fuer_posten,
    )
except Exception as _e:  # pragma: no cover
    _log("Analytics-Modul nicht geladen:", _e)
    _analytics_kennzahlen = None


# --- Markt: Tagesstand je Set, Ära, Pokémon, Illustrator (Modul markt.py) ---

try:
    import markt as _markt  # noqa: E402
    _markt_kennzahlen = _markt.register(
        app, get_db=get_db, current_user=_current_user, require_user=_require_user,
        ist_pro=_ist_pro, ist_pro_stufe=_ist_pro_stufe, betreiber_melden=betreiber_melden,
    )
except Exception as _e:  # pragma: no cover
    _log("Markt-Modul nicht geladen:", _e)
    _markt = None
    _markt_kennzahlen = None


# --- Preis-Alarme und Wochen-Digest (Modul alarme.py) ---------------------------

try:
    import alarme as _alarme  # noqa: E402
    _alarme_kennzahlen = _alarme.register(app, get_db=get_db, require_user=_require_user, ist_bezahlt=_ist_pro,
                                          app_url=_env().get("APP_URL") or "https://binderplan.app")
except Exception as _e:  # pragma: no cover
    _log("Alarm-Modul nicht geladen:", _e)
    _alarme = None
    _alarme_kennzahlen = None


# --- Öffentliche Set-, Pokémon- und Kartenseiten samt Sitemap (Modul seiten.py) ---

try:
    import seiten as _seiten  # noqa: E402
    _seiten_kennzahlen = _seiten.register(app, get_db=get_db, app_url=_env().get("APP_URL") or "https://binderplan.app")
except Exception as _e:  # pragma: no cover
    _log("Seiten-Modul nicht geladen:", _e)
    _seiten_kennzahlen = None


# --- Frontend, Rechtsseite & PWA --------------------------------------------

def _landing_sprache(request: Request) -> str:
    """Welche Startseite bekommt der Besucher? Reihenfolge: ?lang= (ausdrückliche Wahl) →
    Cookie `bp_lang` (die App spiegelt ihre Sprachwahl dorthin) → Accept-Language des Browsers.
    Suchmaschinen schicken in der Regel keinen Accept-Language-Header und landen damit auf der
    deutschen Seite unter / – die englische ist per hreflang unter /en verlinkt. Bewusst keine
    JS-Weiche nach navigator.language: die würde den Crawler beim Rendern umleiten."""
    q = request.query_params.get("lang")
    if q in ("de", "en"):
        return q
    c = request.cookies.get("bp_lang")
    if c in ("de", "en"):
        return c
    erste = (request.headers.get("accept-language") or "").split(",")[0].strip().lower()
    if not erste:
        return ""          # kein Signal (Crawler): die aufgerufene Seite bleibt, wie sie ist
    return "en" if not erste.startswith("de") else "de"


def _landing_antwort(request: Request, datei: str, hierher: str, dorthin: str):
    sprache = _landing_sprache(request)
    qs = str(request.query_params)
    if sprache and sprache != hierher:
        antwort = RedirectResponse(dorthin + ("?" + qs if qs else ""), status_code=302)
    else:
        antwort = FileResponse(BASE / datei, media_type="text/html")
    antwort.headers["Vary"] = "Accept-Language, Cookie"
    if request.query_params.get("lang") in ("de", "en"):
        antwort.set_cookie("bp_lang", request.query_params["lang"], max_age=365 * 86400, samesite="lax")
    return antwort


@app.get("/")
def landing(request: Request):
    """Startseite: erklärt das Werkzeug (SEO, Teilen); Rückkehrer leitet sie per JS in die App.
    Englischsprachige Browser bekommen /en (siehe _landing_sprache)."""
    return _landing_antwort(request, "landing.html", "de", "/en")


@app.get("/en")
def landing_en(request: Request):
    """Englische Startseite (hreflang-Alternative zu /)."""
    return _landing_antwort(request, "landing_en.html", "en", "/")


# Jede Ansicht hat eine eigene Adresse: /app/vitrine, /app/planer, /app/binder/<id> …
# Alle liefern dieselbe Datei aus, die Aufteilung macht das Frontend. Vorher hing der
# Zustand am Hash und wurde nur für zwei Fälle gesetzt — wer die Seite in der Vitrine
# neu lud, landete wieder in der Suche.
APP_ROUTEN = {"", "start", "suche", "planer", "sammlung", "vitrine", "markt", "auswertung"}


@app.get("/app")
@app.get("/app/{rest:path}")
def index(rest: str = ""):
    erster = (rest or "").strip("/").split("/")[0]
    if erster and erster not in APP_ROUTEN and erster not in ("binder", "ansicht"):
        return RedirectResponse("/app", status_code=307)
    return FileResponse(BASE / "index.html", media_type="text/html")


@app.get("/assets/{name}")
def asset(name: str):
    """Statische Dateien der Startseite: Bilder, Schriftarten und deren CSS (assets/ im Repo).
    Die Schriften liegen bewusst lokal — ohne Google-Fonts-CDN gibt es keine Datenübertragung
    an Dritte und damit auch keinen Einwilligungsbedarf."""
    if not re.fullmatch(r"[a-z0-9_-]+\.(png|webp|jpg|svg|woff2|css|js)", name):
        raise HTTPException(404)
    f = BASE / "assets" / name
    if not f.exists():
        raise HTTPException(404)
    endung = name.rsplit(".", 1)[-1]
    typ = {"css": "text/css; charset=utf-8", "woff2": "font/woff2",
           "js": "text/javascript; charset=utf-8"}.get(endung)
    # Bilder und Schriften ändern sich nie — ein Jahr, unveränderlich. Skripte und
    # Stilblätter schon: sie tragen keinen Hash im Namen, und ein „immutable" darauf hieß,
    # dass eine ausgelieferte Fassung ein Jahr im Browser bleibt. Nach dem Umbau des Markts
    # rief eine solche Altfassung einen Endpunkt auf, den es nicht mehr gab. Sie werden
    # deshalb bei jedem Aufruf nachgefragt und mit 304 beantwortet, solange sie gleich sind
    # (FileResponse setzt ETag und Last-Modified selbst).
    kopf = {"Cache-Control": "no-cache"} if endung in ("js", "css") else IMG_HEADERS
    return FileResponse(f, media_type=typ, headers=kopf)


@app.on_event("startup")
async def _nach_dem_start():
    """Läuft, wenn das Modul vollständig geladen ist – der Autosync-Thread startet schon beim
    Import und kannte die Funktion noch nicht (NameError am 10.09.)."""
    threading.Thread(target=_stapel_alle_vorwaermen, daemon=True).start()


@app.get("/sw.js")
def service_worker():
    """Der Service Worker muss im Wurzelpfad liegen, sonst gilt er nur für /assets/."""
    return FileResponse(BASE / "assets" / "sw.js", media_type="text/javascript; charset=utf-8",
                        headers={"Cache-Control": "no-cache"})


@app.get("/api/binders/{binder_id}/vorschau.png")
def binder_vorschau(binder_id: str, request: Request):
    """Bild der ersten Binderseite, wie es beim Teilen in Chats und Netzwerken erscheint.
    Ohne so ein Bild zeigt ein geteilter Link nur den App-Namen — mit ihm wirbt jeder
    geteilte Binder für sich selbst. Wird im Cache abgelegt und bei Änderungen neu gebaut."""
    binder = _load_binder(binder_id)
    _binder_lesen_erlaubt(binder_id, _current_user(request))
    stand = re.sub(r"[^0-9]", "", str(binder.get("updated_at") or ""))[:14]
    ziel = CACHE / "vorschau" / f"{re.sub(r'[^A-Za-z0-9_-]', '_', binder_id)}.{stand}.png"
    if not ziel.exists():
        ziel.parent.mkdir(parents=True, exist_ok=True)
        for alt in ziel.parent.glob(f"{re.sub(r'[^A-Za-z0-9_-]', '_', binder_id)}.*.png"):
            try:
                alt.unlink()
            except OSError:
                pass
        _vorschau_bauen(binder, ziel)
    return FileResponse(ziel, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/binders/{binder_id}/stapel.webp")
def binder_stapel(binder_id: str, request: Request):
    """Vorschaubild der Binder-Kachel: die ersten drei Seiten als Stapel, ein Bild statt 27.
    Startseite, Vitrine und Landingpage luden vorher jedes Kartenbild einzeln – 193 Anfragen
    und 4,9 MB für zehn Binder (gemessen 09.09.2026). Der Stand steckt im Dateinamen und in
    der Adresse (?s=), deshalb darf die Antwort ein Jahr im Browser liegen."""
    binder = _load_binder(binder_id)
    _binder_lesen_erlaubt(binder_id, _current_user(request))
    stand = re.sub(r"[^0-9]", "", str(binder.get("updated_at") or ""))[:14]
    sicher = re.sub(r"[^A-Za-z0-9_-]", "_", binder_id)
    ziel = CACHE / "vorschau" / f"{sicher}.{stand}.stapel.webp"
    if not ziel.exists():
        ziel.parent.mkdir(parents=True, exist_ok=True)
        for alt in ziel.parent.glob(f"{sicher}.*.stapel.webp"):
            try:
                alt.unlink()
            except OSError:
                pass
        _stapel_bauen(binder, ziel)
    return FileResponse(ziel, media_type="image/webp",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


def _stapel_vorwaermen(binder_id):
    """Kachelbild im Hintergrund bauen, damit die nächste Startseite es fertig vorfindet."""
    def lauf():
        try:
            binder = _load_binder(binder_id)
            stand = re.sub(r"[^0-9]", "", str(binder.get("updated_at") or ""))[:14]
            sicher = re.sub(r"[^A-Za-z0-9_-]", "_", binder_id)
            ziel = CACHE / "vorschau" / f"{sicher}.{stand}.stapel.webp"
            if not ziel.exists():
                ziel.parent.mkdir(parents=True, exist_ok=True)
                _stapel_bauen(binder, ziel)
        except Exception as exc:
            log.warning("Stapelbild %s: %s", binder_id, exc)
    threading.Thread(target=lauf, daemon=True).start()


def _stapel_alle_vorwaermen():
    """Beim Start: fehlende Kachelbilder aller Konto- und Vitrine-Binder nachziehen, langsam,
    damit der Dienst nebenbei antwortet."""
    try:
        con = get_db()
        ids = [r["id"] for r in con.execute(
            "SELECT id FROM binders WHERE user_id IS NOT NULL OR COALESCE(sichtbar,0)=1 ORDER BY updated_at DESC")]
        con.close()
        for bid in ids:
            binder = _load_binder(bid)
            stand = re.sub(r"[^0-9]", "", str(binder.get("updated_at") or ""))[:14]
            sicher = re.sub(r"[^A-Za-z0-9_-]", "_", bid)
            ziel = CACHE / "vorschau" / f"{sicher}.{stand}.stapel.webp"
            if ziel.exists():
                continue
            ziel.parent.mkdir(parents=True, exist_ok=True)
            try:
                _stapel_bauen(binder, ziel)
            except Exception as exc:
                log.warning("Stapelbild %s: %s", bid, exc)
            time.sleep(0.3)
    except Exception as exc:
        log.warning("Stapelbilder vorwärmen: %s", exc)


def _stapel_bauen(binder, ziel: Path):
    """Drei Seiten leicht versetzt hintereinander, hinten dunkler – dieselbe Form, die
    vitrineBlatt() im Browser aus Einzelbildern baute. Transparenter Grund, damit die Kachel
    hell wie dunkel funktioniert."""
    from PIL import ImageEnhance
    items = binder["items"]
    optionen = binder.get("options") or {}
    lang = "en" if optionen.get("sprache") == "en" else "de"
    plan = _seiten_plan(binder)[:3]
    if not plan:
        plan = [{"nr": 0, "start": 0, "laenge": 9}]
    grund = RASTER.get(binder.get("layout") or "3x3", (3, 3))

    def raster(sp):
        return (sp.get("spalten") or grund[0], sp.get("zeilen") or grund[1])

    # Maßstab: die größte Seite muss in 440 × 400 passen (Einheiten: Karte 63 × 88, Fuge 4)
    s = min(440 / max(c * 67 + 4 for c, _ in map(raster, plan)),
            400 / max(r * 92 + 4 for _, r in map(raster, plan)))
    rand = int(6 * s)
    seiten = []
    for sp in plan:
        cols, rows = raster(sp)
        breite = int((cols * 67 - 4) * s) + 2 * rand
        hoehe = int((rows * 92 - 4) * s) + 2 * rand
        seite = Image.new("RGBA", (breite, hoehe), (0, 0, 0, 0))
        zeichnen = ImageDraw.Draw(seite)
        zeichnen.rounded_rectangle([0, 0, breite - 1, hoehe - 1], int(10 * s), fill="#14161a")
        fach_b, fach_h = int(63 * s), int(88 * s)
        for i in range(sp["laenge"]):
            c, r = i % cols, i // cols
            x = rand + int(c * 67 * s); y = rand + int(r * 92 * s)
            zeichnen.rounded_rectangle([x, y, x + fach_b, y + fach_h], int(4 * s), fill="#24272e")
            idx = sp["start"] + i
            item = items[idx] if idx < len(items) else None
            if not item:
                continue
            bild = None
            try:
                if item.get("type") == "card" and item.get("id"):
                    pfad = _card_image_path(item["id"], lang, "low")
                    if pfad:
                        bild = ImageOps.fit(Image.open(pfad).convert("RGB"), (fach_b, fach_h), Image.LANCZOS)
                elif item.get("type") == "dex" and item.get("dex"):
                    pfad = _dex_image_path(item["dex"])
                    if pfad:
                        sprite = Image.open(pfad).convert("RGBA")
                        sprite.thumbnail((fach_b - 6, fach_h - 6), Image.LANCZOS)
                        bild = Image.new("RGBA", (fach_b, fach_h), "#24272e")
                        bild.paste(sprite, ((fach_b - sprite.width) // 2, (fach_h - sprite.height) // 2), sprite)
                elif item.get("type") == "art" and item.get("artwork"):
                    tile = _artwork.kachel(item["artwork"], int(item.get("slot") or 0))
                    if tile is not None:
                        bild = ImageOps.fit(tile.convert("RGB"), (fach_b, fach_h), Image.LANCZOS)
            except Exception:
                bild = None
            if bild is not None:
                seite.paste(bild, (x, y))
        seiten.append(seite)

    # Stapel: vorn links oben, jede weitere Seite nach rechts unten versetzt und abgedunkelt
    vx, vy = int(seiten[0].width * 0.13), int(seiten[0].height * 0.07)
    B = max(sx.width for sx in seiten) + vx * (len(seiten) - 1)
    H = max(sx.height for sx in seiten) + vy * (len(seiten) - 1)
    bild = Image.new("RGBA", (B, H), (0, 0, 0, 0))
    for nr in reversed(range(len(seiten))):
        seite = seiten[nr]
        if nr:
            rgb = ImageEnhance.Brightness(seite.convert("RGB")).enhance(0.88 if nr == 1 else 0.76)
            seite = Image.merge("RGBA", (*rgb.split(), seite.split()[3]))
        bild.alpha_composite(seite, (nr * vx, nr * vy))
    tmp = ziel.with_name(ziel.name + f".{threading.get_ident()}.tmp")
    bild.save(tmp, "WEBP", quality=82, method=4)
    import os
    os.replace(tmp, ziel)


def _vorschau_bauen(binder, ziel: Path):
    """1200×630 (das Format, das Chats und soziale Netzwerke erwarten): dunkle Binderseite,
    die ersten neun Fächer, Name und Kartenzahl."""
    from PIL import ImageDraw, ImageFont
    B, H = 1200, 630
    bild = Image.new("RGB", (B, H), "#14161a")
    zeichnen = ImageDraw.Draw(bild)
    spalten, zeilen = 3, 3
    try:
        spalten, zeilen = (int(x) for x in str(binder.get("layout") or "3x3").split("x")[:2])
    except ValueError:
        pass
    spalten = max(1, min(5, spalten)); zeilen = max(1, min(5, zeilen))
    lang = "en" if (binder.get("options") or {}).get("sprache") == "en" else "de"

    rand, fuge = 40, 12
    hoehe = H - 2 * rand - 60
    fach_h = int((hoehe - (zeilen - 1) * fuge) / zeilen)
    fach_b = int(fach_h * 63 / 88)
    gitter_b = spalten * fach_b + (spalten - 1) * fuge
    x0 = (B - gitter_b) // 2
    y0 = rand
    zeichnen.rounded_rectangle([x0 - 18, y0 - 18, x0 + gitter_b + 18, y0 + zeilen * fach_h + (zeilen - 1) * fuge + 18],
                               22, fill="#1c1f25")
    items = [i for i in binder["items"][:spalten * zeilen]]
    for nr in range(spalten * zeilen):
        sx = x0 + (nr % spalten) * (fach_b + fuge)
        sy = y0 + (nr // spalten) * (fach_h + fuge)
        zeichnen.rounded_rectangle([sx, sy, sx + fach_b, sy + fach_h], 6, fill="#24272e")
        item = items[nr] if nr < len(items) else None
        if not item or item.get("type") != "card" or not item.get("id"):
            continue
        pfad = _card_image_path(item["id"], lang)
        if not pfad:
            continue
        try:
            karte = Image.open(pfad).convert("RGB")
            karte = ImageOps.fit(karte, (fach_b, fach_h), Image.LANCZOS)
            bild.paste(karte, (sx, sy))
        except Exception:
            pass

    name = (binder.get("name") or "Binderplan")[:48]
    karten = sum(1 for i in binder["items"] if i.get("type") == "card")
    schrift, klein = None, None
    for pfad in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            schrift = ImageFont.truetype(pfad, 34); klein = ImageFont.truetype(pfad, 22); break
        except OSError:
            continue
    unten = H - rand - 18
    zeichnen.text((rand, unten - 34), name, fill="#ECEEF2", font=schrift)
    zeichnen.text((rand, unten + 4), f"{karten} Karten · binderplan.app", fill="#9AA1AE", font=klein)
    bild.save(ziel, "PNG", optimize=True)


@app.get("/b/{binder_id}")
def binder_teilen_seite(binder_id: str, request: Request):
    """Adresse zum Teilen. Sie liefert Vorschaubild und Beschreibung für Chats und
    Netzwerke aus und schickt Menschen sofort in die Ansicht des Binders weiter."""
    try:
        binder = _load_binder(binder_id)
        _binder_lesen_erlaubt(binder_id, None)
    except HTTPException:
        # Stumm auf die Startseite zu leiten ließ den Besucher rätseln, ob der Link kaputt
        # ist. Die App zeigt zu diesem Parameter einen Hinweis.
        return RedirectResponse("/app?hinweis=privat", status_code=302)
    basis = (_env().get("APP_URL") or "https://binderplan.app").rstrip("/")
    name = html.escape((binder.get("name") or "Binder")[:80])
    karten = sum(1 for i in binder["items"] if i.get("type") == "card")
    seiten = max(1, -(-len(binder["items"]) // LAYOUTS.get(binder["layout"], 9)))
    beschreibung = html.escape(f"{karten} Karten auf {seiten} Seiten – geplant mit Binderplan.")
    # ?ref=b markiert den Weg „über einen geteilten Binder gekommen". Die App liest das
    # aus und zeigt Gästen den Einstieg; ohne die Markierung ließe sich nicht messen, ob
    # geteilte Binder überhaupt neue Nutzer bringen.
    ziel = f"{basis}/app?ref=b#ansicht/{html.escape(binder_id)}"
    return HTMLResponse(f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<title>{name} · Binderplan</title>
<meta name="description" content="{beschreibung}">
<meta property="og:type" content="website">
<meta property="og:title" content="{name}">
<meta property="og:description" content="{beschreibung}">
<meta property="og:image" content="{basis}/api/binders/{html.escape(binder_id)}/vorschau.png">
<meta property="og:url" content="{basis}/b/{html.escape(binder_id)}">
<meta property="og:site_name" content="Binderplan">
<meta name="twitter:card" content="summary_large_image">
<meta http-equiv="refresh" content="0; url={ziel}">
</head><body style="font-family:sans-serif;padding:40px;text-align:center">
<p><a href="{ziel}">{name} in Binderplan öffnen</a></p>
<script>location.replace({json.dumps(ziel)});</script>
</body></html>""")


@app.get("/recht")
def recht():
    return FileResponse(BASE / "recht.html", media_type="text/html")


@app.get("/favicon.ico")
def favicon():
    """Browser fragen /favicon.ico von sich aus an. Ohne Antwort zeigen manche
    weiter ein altes Symbol aus ihrem Zwischenspeicher."""
    return FileResponse(_app_icon(192), media_type="image/png", headers=IMG_HEADERS)


@app.get("/manifest.webmanifest")
def manifest():
    return Response(json.dumps({
        "name": "Binderplan", "short_name": "Binderplan",
        "description": "Pokémon-Binder planen und als Schwarz-Weiß-Checkliste drucken",
        "start_url": ".", "scope": ".", "display": "standalone",
        "background_color": "#ffffff", "theme_color": "#2a4b9b",
        "icons": [
            {"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }), media_type="application/manifest+json")


def _app_icon(groesse: int) -> Path:
    """Das App-Icon liegt als Datei im Repo (icon-192.png, icon-512.png).

    Frueher wurde es hier zur Laufzeit gezeichnet und im Cache abgelegt — mit der
    Folge, dass ein neues Icon im Repo wirkungslos blieb. Gezeichnet wird nur
    noch, wenn die Datei fehlt."""
    datei = BASE / f"icon-{groesse}.png"
    if datei.exists():
        return datei
    ziel = CACHE / f"icon-{groesse}.png"
    if ziel.exists():
        return ziel
    from PIL import ImageDraw
    img = Image.new("RGB", (groesse, groesse), "#f5c518")
    d = ImageDraw.Draw(img)
    g = groesse
    d.rounded_rectangle([g * 0.16, g * 0.16, g * 0.84, g * 0.84], radius=g * 0.05,
                        outline="#14161c", width=max(3, g // 22))
    for i in (1, 2):
        d.line([g * (0.16 + i * 0.227), g * 0.16, g * (0.16 + i * 0.227), g * 0.84], fill="#14161c", width=max(3, g // 26))
        d.line([g * 0.16, g * (0.16 + i * 0.227), g * 0.84, g * (0.16 + i * 0.227)], fill="#14161c", width=max(3, g // 26))
    img.save(ziel)
    return ziel


@app.get("/icon-{groesse}.png")
def icon(groesse: int):
    if groesse not in (192, 512):
        raise HTTPException(404)
    return FileResponse(_app_icon(groesse), media_type="image/png", headers=IMG_HEADERS)
