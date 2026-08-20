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
        """
    )
    con.commit()
    con.close()


init_db()


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
                "INSERT OR REPLACE INTO sets (id,name,serie_id,serie_name,release_date,total,official,symbol)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (detail["id"], detail.get("name"), serie.get("id"), serie.get("name"),
                 detail.get("releaseDate"), cc.get("total"), cc.get("official"),
                 detail.get("symbol")),
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
            return dex_id, name_de, d.get("name")
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
    sets = [
        dict(r)
        for r in con.execute(
            "SELECT id,name,serie_id,serie_name,release_date,total,official,symbol"
            " FROM sets ORDER BY release_date IS NULL, release_date"
        )
    ]
    last_sync = con.execute("SELECT value FROM kv WHERE key='last_sync'").fetchone()
    con.close()
    return {
        "sync": {**SYNC, "last": last_sync["value"] if last_sync else None},
        "counts": counts,
        "sets": sets,
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


def _card_query(q, set_id, serie, typ, kind, sort, richtung):
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
    if kind:
        kinds = [k for k in kind.split(",") if k]
        where.append("kind IN (%s)" % ",".join("?" * len(kinds)))
        params += kinds
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
        "set_id": row["set_id"],
        "set_name": row["set_name"],
        "local_id": row["local_id"],
        "rarity": row["rarity"],
        "kind": row["kind"],
        "types": json.loads(row["types"] or "[]"),
        "dex": row["first_dex"],
        "datum": row["release_date"],
        "reverse": bool(row["has_reverse"]),
        "img": bool(row["image_de"] or row["image_en"]),
    }


@app.get("/api/cards")
def cards(q: str = "", set_id: str = "", serie: str = "", typ: str = "",
          kind: str = "", sort: str = "datum", richtung: str = "asc",
          limit: int = 60, offset: int = 0):
    limit = max(1, min(limit, 300))
    sql_where, params, order = _card_query(q, set_id, serie, typ, kind, sort, richtung)
    con = get_db()
    total = con.execute(f"SELECT COUNT(*) c FROM cards{sql_where}", params).fetchone()["c"]
    rows = con.execute(
        f"SELECT cards.*, (SELECT name FROM sets WHERE sets.id = cards.set_id) set_name"
        f" FROM cards{sql_where} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    con.close()
    return {"total": total, "karten": [_card_brief(r) for r in rows]}


@app.get("/api/cards/ids")
def card_ids(q: str = "", set_id: str = "", serie: str = "", typ: str = "",
             kind: str = "", sort: str = "datum", richtung: str = "asc",
             limit: int = 1000):
    limit = max(1, min(limit, 2000))
    sql_where, params, order = _card_query(q, set_id, serie, typ, kind, sort, richtung)
    con = get_db()
    rows = con.execute(
        f"SELECT id FROM cards{sql_where} ORDER BY {order} LIMIT ?",
        params + [limit],
    ).fetchall()
    con.close()
    return {"ids": [r["id"] for r in rows]}


@app.get("/api/sets/{set_id}/cards")
def set_cards(set_id: str):
    con = get_db()
    rows = con.execute(
        "SELECT cards.*, (SELECT name FROM sets WHERE sets.id = cards.set_id) set_name"
        " FROM cards WHERE set_id = ? ORDER BY local_num, local_id",
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
        {"dex": r["dex_id"], "name": r["name_de"], "gen": r["gen"]}
        for r in rows
        if wanted is None or r["gen"] in wanted
    ]
    return {"pokemon": result}


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


@app.get("/api/img/card/{card_id}")
def card_image(card_id: str, variante: str = "low"):
    variante = "high" if variante == "high" else "low"
    safe = re.sub(r"[^A-Za-z0-9._%-]", "_", card_id)
    target = CACHE / "cards" / variante / f"{safe}.webp"
    if not target.exists():
        con = get_db()
        row = con.execute("SELECT image_de, image_en FROM cards WHERE id = ?", (card_id,)).fetchone()
        con.close()
        if not row:
            raise HTTPException(404, "Karte unbekannt")
        urls = [
            f"{row['image_de']}/{variante}.webp" if row["image_de"] else None,
            f"{row['image_en']}/{variante}.webp" if row["image_en"] else None,
        ]
        if not _fetch_asset(urls, target):
            raise HTTPException(404, "Kein Bild verfügbar")
    return FileResponse(target, media_type="image/webp", headers=IMG_HEADERS)


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

LAYOUTS = {"2x2": 4, "3x3": 9, "3x4": 12, "4x4": 16}


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
    binder_id = secrets.token_urlsafe(8)
    con = get_db()
    con.execute(
        "INSERT INTO binders (id,name,mode,layout,options,items) VALUES (?,?,?,?,?,?)",
        (binder_id, p["name"], p["mode"], p["layout"], p["options"], p["items"]),
    )
    con.commit()
    con.close()
    return {"id": binder_id}


@app.put("/api/binders/{binder_id}")
async def binder_update(binder_id: str, request: Request):
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
def binder_delete(binder_id: str):
    con = get_db()
    con.execute("DELETE FROM binders WHERE id = ?", (binder_id,))
    con.commit()
    con.close()
    return {"ok": True}


@app.get("/api/binders")
def binder_list(ids: str = ""):
    wanted = [i for i in ids.split(",") if i][:50]
    if not wanted:
        return {"binder": []}
    con = get_db()
    rows = con.execute(
        "SELECT id,name,mode,layout,items,updated_at FROM binders WHERE id IN (%s)"
        % ",".join("?" * len(wanted)),
        wanted,
    ).fetchall()
    con.close()
    by_id = {
        r["id"]: {
            "id": r["id"], "name": r["name"], "mode": r["mode"], "layout": r["layout"],
            "anzahl": len(json.loads(r["items"] or "[]")), "updated_at": r["updated_at"],
        }
        for r in rows
    }
    return {"binder": [by_id[i] for i in wanted if i in by_id]}


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


def _card_image_path(card_id):
    """Hochauflösendes Kartenbild besorgen (nutzt denselben Cache wie /api/img)."""
    safe = re.sub(r"[^A-Za-z0-9._%-]", "_", card_id)
    target = CACHE / "cards" / "high" / f"{safe}.webp"
    if target.exists():
        return target
    con = get_db()
    row = con.execute("SELECT image_de, image_en FROM cards WHERE id = ?", (card_id,)).fetchone()
    con.close()
    if not row:
        return None
    urls = [
        f"{row['image_de']}/high.webp" if row["image_de"] else None,
        f"{row['image_en']}/high.webp" if row["image_en"] else None,
    ]
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
    name = pokemon_names.get(dex, f"#{dex}")
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


@app.get("/api/binders/{binder_id}/pdf")
def binder_pdf(binder_id: str):
    binder = _load_binder(binder_id)
    per_page = LAYOUTS.get(binder["layout"], 9)

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
    pokemon_names = {r["dex_id"]: r["name_de"] for r in con.execute("SELECT dex_id, name_de FROM pokemon")}
    con.close()

    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    page_w, page_h = A4
    grid_w = COLS * CARD_W + (COLS - 1) * GUTTER
    grid_h = ROWS * CARD_H + (ROWS - 1) * GUTTER
    ox = (page_w - grid_w) / 2
    oy = (page_h - grid_h) / 2

    printable = [
        (idx, item) for idx, item in enumerate(binder["items"]) if item.get("type") != "empty"
    ]

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
            path = _card_image_path(item.get("id")) if card else None
            if path:
                try:
                    c.drawImage(_grayscale_reader(path), x, y, CARD_W, CARD_H)
                except Exception:
                    path = None
            if not path:
                name = (card["name_de"] or card["name_en"]) if card else item.get("id", "?")
                setline = f"{card['set_name'] or card['set_id']} · {card['local_id']}" if card else ""
                _draw_placeholder(c, x, y, [(str(name), 12, True), (setline, 9, False)])

        # Schnittkante + Fach-Beschriftung in der Fuge (wird mit abgeschnitten)
        c.setLineWidth(0.4)
        c.setStrokeGray(0.75)
        c.rect(x, y, CARD_W, CARD_H)
        c.setFillGray(0.45)
        c.setFont("Helvetica", 6.5)
        label = f"Seite {binder_page} · Fach {slot}"
        if variant == "reverse":
            label += " · Reverse Holo"
        c.drawCentredString(x + CARD_W / 2, y - 2.6 * mm, label)
        cell += 1

    if not printable:
        c.setFont("Helvetica", 14)
        c.drawCentredString(page_w / 2, page_h / 2, "Dieser Binder ist noch leer.")
    c.save()

    con = get_db()
    con.execute(
        "INSERT INTO kv (key,value) VALUES ('pdf_exports','1')"
        " ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1"
    )
    con.commit()
    con.close()

    fname = re.sub(r"[^A-Za-z0-9äöüÄÖÜß _-]", "", binder["name"]) or "binder"
    return Response(
        buf.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{fname}.pdf"'},
    )


# --- Frontend ---------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(BASE / "index.html", media_type="text/html")
