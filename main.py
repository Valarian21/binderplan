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
    ):
        try:
            con.execute(alter)
        except sqlite3.OperationalError:
            pass
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
            return dex_id, name_de, name_en or (d.get("name") or "").capitalize()
        except Exception:
            return dex_id, None, None

    with ThreadPoolExecutor(8) as pool:
        for dex_id, name_de, name_en in pool.map(fetch, ids):
            SYNC["done"] += 1
            gen = next((g for g, lo, hi in GEN_RANGES if lo <= dex_id <= hi), None)
            con.execute(
                "INSERT OR REPLACE INTO pokemon (dex_id,name_de,name_en,gen) VALUES (?,?,?,?)",
                (dex_id, name_de or (name_en or "").capitalize(), name_en, gen),
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


def _maybe_autosync():
    con = get_db()
    n = con.execute("SELECT COUNT(*) c FROM cards").fetchone()["c"]
    con.close()
    if n == 0:
        threading.Thread(target=run_sync, daemon=True).start()
    else:
        _maybe_backfill()


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
    series = {}
    for r in con.execute(
        "SELECT id,name,name_en,serie_id,serie_name,serie_name_en,release_date,total,official,symbol"
        " FROM sets ORDER BY release_date IS NULL, release_date"
    ):
        d = dict(r)
        d["name"] = SET_NAME_FIX_DE.get(d["id"], d["name"]) or d["name_en"]
        d["serie_name"] = SERIE_NAME_FIX_DE.get(d["serie_id"], d["serie_name"]) or d["serie_name_en"]
        sets.append(d)
        sid = d["serie_id"] or "misc"
        if sid not in series:
            series[sid] = {"id": sid, "name": d["serie_name"] or sid,
                           "name_en": d["serie_name_en"] or d["serie_name"] or sid,
                           "von": d["release_date"] or "9999"}
    rarities = [
        {"rarity": r["rarity"], "anzahl": r["c"]}
        for r in con.execute(
            "SELECT rarity, COUNT(*) c FROM cards WHERE rarity IS NOT NULL AND rarity != 'None'"
            " GROUP BY rarity ORDER BY c DESC"
        )
    ]
    last_sync = con.execute("SELECT value FROM kv WHERE key='last_sync'").fetchone()
    con.close()
    return {
        "sync": {**SYNC, "last": last_sync["value"] if last_sync else None},
        "counts": counts,
        "sets": sets,
        "series": sorted(series.values(), key=lambda s: s["von"]),
        "rarities": rarities,
        "types": TYPES_DE,
        "gens": [{"gen": g, "von": lo, "bis": hi} for g, lo, hi in GEN_RANGES],
    }


# --- Kartensuche ------------------------------------------------------------

SORTS = {
    "datum": "release_date IS NULL, release_date, set_id, local_num",
    "dex": "first_dex IS NULL, first_dex, release_date",
    "name": "COALESCE(name_de, name_en) COLLATE NOCASE",
    "nummer": "set_id, local_num",
    "typ": "types, COALESCE(name_de, name_en) COLLATE NOCASE",
}


def _card_query(q, set_id, serie, typ, kind, sort, richtung, rarity="", dex=0):
    where, params = [], []
    if q:
        where.append("(name_de LIKE ? OR name_en LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if set_id:
        where.append("set_id = ?")
        params.append(set_id)
    if serie:
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
    if richtung == "desc":
        order = ", ".join(
            part.strip() + " DESC" if "IS NULL" not in part else part.strip()
            for part in order.split(",")
        )
    return sql_where, params, order


def _card_brief(row):
    return {
        "id": row["id"],
        "name": row["name_de"] or row["name_en"],
        "name_en": row["name_en"] or row["name_de"],
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
        "img": bool(row["image_de"] or row["image_en"]),
    }


_CARD_SELECT = (
    "SELECT cards.*, (SELECT name FROM sets WHERE sets.id = cards.set_id) set_name,"
    " (SELECT name_en FROM sets WHERE sets.id = cards.set_id) set_name_en FROM cards"
)


@app.get("/api/cards")
def cards(q: str = "", set_id: str = "", serie: str = "", typ: str = "",
          kind: str = "", rarity: str = "", dex: int = 0,
          sort: str = "datum", richtung: str = "asc",
          limit: int = 60, offset: int = 0):
    limit = max(1, min(limit, 300))
    sql_where, params, order = _card_query(q, set_id, serie, typ, kind, sort, richtung, rarity, dex)
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
             limit: int = 1000):
    limit = max(1, min(limit, 2000))
    sql_where, params, order = _card_query(q, set_id, serie, typ, kind, sort, richtung, rarity, dex)
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
        "email": user["email"], "plan": user["plan"],
        "binder_anzahl": anzahl, "binder_limit": None if pro else FREE_BINDER_LIMIT,
        "exporte_benutzt": _exporte_benutzt(user),
        "exporte_limit": None if pro else FREE_EXPORT_LIMIT,
        "stripe": bool(_env().get("STRIPE_SECRET_KEY")),
    }


def _neue_session(con, user_id) -> str:
    token = secrets.token_urlsafe(32)
    con.execute("INSERT INTO sessions (token, user_id) VALUES (?,?)", (token, user_id))
    return token


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
    try:
        d = client.get(f"{TCGDEX}/en/cards/{card_id}").json()
        cm = (d.get("pricing") or {}).get("cardmarket") or {}
        for key in ("trend", "avg30", "avg", "low"):
            if cm.get(key) is not None:
                return card_id, round(float(cm[key]), 2)
    except Exception:
        pass
    return card_id, None


@app.post("/api/preise")
async def preise(request: Request):
    """EUR-Preise (Cardmarket-Trend) für Karten-IDs; fehlende werden nachgeladen.
    Nur mit Konto; Free-Konten aktualisieren 1x pro Tag (Cache wird immer geliefert)."""
    user = _require_user(request)
    data = await request.json()
    ids = list(dict.fromkeys(str(i) for i in (data.get("ids") or [])))[:1500]
    frei_gedrosselt = not _ist_pro(user) and user.get("preise_tag") == _heute()
    con = get_db()
    result, fehlt = {}, []
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        rows = con.execute(
            "SELECT card_id, eur, updated_at FROM card_prices WHERE card_id IN (%s)"
            % ",".join("?" * len(chunk)), chunk).fetchall()
        frisch = {r["card_id"]: r for r in rows
                  if (r["updated_at"] or "") >= (datetime_str_vor(24))}
        for cid in chunk:
            if cid in frisch:
                result[cid] = frisch[cid]["eur"]
            else:
                fehlt.append(cid)
    nachgeladen = [] if frei_gedrosselt else fehlt[:400]
    if nachgeladen:
        with httpx.Client(timeout=20, headers=UA) as client:
            with ThreadPoolExecutor(8) as pool:
                for cid, eur in pool.map(lambda c: _fetch_price(client, c), nachgeladen):
                    result[cid] = eur
                    con.execute(
                        "INSERT OR REPLACE INTO card_prices (card_id, eur, updated_at)"
                        " VALUES (?,?,datetime('now'))", (cid, eur))
                    if eur is not None:
                        con.execute(
                            "INSERT OR REPLACE INTO price_history (card_id, datum, eur) VALUES (?,?,?)",
                            (cid, _heute(), eur))
        con.execute("UPDATE users SET preise_tag = ? WHERE id = ?", (_heute(), user["id"]))
        con.commit()
    con.close()
    return {"preise": result, "offen": max(0, len(fehlt) - len(nachgeladen)),
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
        })
    return {"binder": result}


# --- PDF-Export -------------------------------------------------------------

from PIL import Image, ImageOps  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.lib.utils import ImageReader  # noqa: E402
from reportlab.pdfgen import canvas as pdfcanvas  # noqa: E402

CARD_W = 63 * mm
CARD_H = 88 * mm
GUTTER = 4 * mm
COLS, ROWS = 3, 3


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


def _zaehle_export(user):
    if _ist_pro(user):
        return
    con = get_db()
    con.execute("UPDATE users SET exports_monat = ? WHERE id = ?",
                (f"{_monat_key()}:{_exporte_benutzt(user) + 1}", user["id"]))
    con.commit()
    con.close()


@app.get("/api/binders/{binder_id}/pdf")
def binder_pdf(binder_id: str, request: Request, variante: str = "karten", nur_fehlende: int = 0):
    user = _require_user(request)
    binder = _load_binder(binder_id)
    per_page = LAYOUTS.get(binder["layout"], 9)
    lang = "en" if (binder.get("options") or {}).get("sprache") == "en" else "de"
    if variante == "checkliste":
        return _checkliste_pdf(binder, lang, bool(nur_fehlende))
    # Karten-PDF: zählt gegen das Monats-Limit von Free-Konten
    if not _ist_pro(user) and _exporte_benutzt(user) >= FREE_EXPORT_LIMIT:
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
            path = _card_image_path(item.get("id"), lang) if card else None
            if path:
                try:
                    c.drawImage(_grayscale_reader(path), x, y, CARD_W, CARD_H)
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
        if variant == "reverse":
            label += " · Reverse Holo"
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
    _zaehle_export(user)

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
                           "variant": item.get("variant") or ""})
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
    c.setFont("Helvetica", 9.5)
    for z in zeilen:
        if y < 18 * mm:
            c.showPage()
            neue_seite()
            c.setFont("Helvetica", 9.5)
        c.setFillGray(0.15)
        c.setLineWidth(0.7)
        c.setStrokeGray(0.3)
        c.rect(18 * mm, y - 1, 3.4 * mm, 3.4 * mm)
        if z["have"]:
            c.setFont("Helvetica-Bold", 9)
            c.drawString(18.6 * mm, y - 0.4, "X")
            c.setFont("Helvetica", 9.5)
        c.drawString(25 * mm, y, z["pos"])
        name = z["name"] + (" (Reverse)" if z["variant"] == "reverse" else "")
        c.drawString(38 * mm, y, name[:42])
        c.setFillGray(0.45)
        c.drawString(118 * mm, y, (z["set"] or "")[:28])
        c.drawRightString(page_w - 30 * mm, y, z["nr"])
        if z["eur"] is not None:
            c.drawRightString(page_w - 18 * mm, y, f"{z['eur']:.2f}€")
        y -= 6.2 * mm
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
def index():
    return FileResponse(BASE / "index.html", media_type="text/html")


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
