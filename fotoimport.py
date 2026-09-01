"""Binder aus Fotos: Seite fotografieren, Karten erkennen, Binder anlegen.

Der Kern ist bewusst kein KI-Aufruf. Gemessen (2026-09-01) raten Vision-Modelle die
Sammelnummer einer Karte — gemini-2.5-flash traf 6 von 8, die guenstige Variante 1 von 8.
Ein Bildfingerabdruck (dHash, 16x16, 256 Bit) traf dagegen 8 von 8 gegen 9.208 Karten,
mit deutlichem Abstand (richtig 31-54, zweitbester 70-94) und ohne ein einziges Token.

Also: das Modell sagt nur, WO auf dem Foto Karten liegen; WELCHE Karte es ist,
entscheidet der Abgleich gegen `card_hashes`. Das ist genauer und kostet fast nichts.
"""

import base64
import io
import json
import re
import threading
import time

import httpx
from fastapi import HTTPException, Request, UploadFile
from PIL import Image, ImageOps

_dep = {}

BOX_MODELL = "google/gemini-2.5-flash"     # nur die Kaestchen, kein Lesen
KANTE = 16                                  # dHash-Raster → 256 Bit
MAX_FOTO = 1500                             # Kantenlaenge, mit der das Modell arbeitet
GUT = 68                                    # Abstand, bis zu dem ein Treffer als sicher gilt
VORSPRUNG = 10                              # Mindestabstand zum zweitbesten Treffer

BOX_PROMPT = (
    "This is a photo of one page of a trading card binder. Find every trading card in the photo. "
    "Return the cards ordered like reading text: left to right, top to bottom.\n"
    'JSON only: {"karten":[{"platz":1,"box":[ymin,xmin,ymax,xmax]}]} — box in 0-1000 coordinates of the '
    "whole image, tight around the card including its border. Include a card even if it is rotated or "
    "partly covered. Do not return empty pockets."
)


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


# --- Fingerabdruck -----------------------------------------------------------

def dhash(img, k=KANTE):
    """Differenz-Hash: je Zeile vergleicht jedes Pixel mit seinem rechten Nachbarn.
    Unempfindlich gegen Helligkeit, Kontrast und JPEG-Artefakte."""
    g = img.convert("L").resize((k + 1, k), Image.LANCZOS)
    px = list(g.getdata())
    bits = 0
    for r in range(k):
        z = r * (k + 1)
        for c in range(k):
            bits = (bits << 1) | (1 if px[z + c] < px[z + c + 1] else 0)
    return bits


def _abstand(a, b):
    return bin(a ^ b).count("1")


_vorrat = {"stand": 0.0, "ids": [], "werte": []}
_vorrat_sperre = threading.Lock()


def _vorrat_laden(erzwingen=False):
    """Alle Fingerabdruecke im Speicher halten — 23.000 Zahlen sind nichts, und
    jeder Vergleich muss gegen den ganzen Katalog laufen."""
    with _vorrat_sperre:
        if not erzwingen and _vorrat["ids"] and time.time() - _vorrat["stand"] < 600:
            return
        con = _dep["get_db"]()
        reihen = con.execute("SELECT card_id, hash FROM card_hashes WHERE hash <> ''").fetchall()
        con.close()
        _vorrat["ids"] = [r["card_id"] for r in reihen]
        _vorrat["werte"] = [int(r["hash"], 16) for r in reihen]
        _vorrat["stand"] = time.time()


def _suchen(hash_wert, anzahl=5):
    """→ [(abstand, card_id), ...] aufsteigend."""
    _vorrat_laden()
    treffer = sorted(zip((_abstand(hash_wert, w) for w in _vorrat["werte"]), _vorrat["ids"]))[:anzahl]
    return treffer


# --- Fingerabdruecke aufbauen (Hintergrundlauf) ------------------------------

_hashlauf = {"aktiv": False, "fertig": 0, "fehler": "", "start": "", "stop": False, "marke": 0}


def _bild_holen(url):
    for versuch in range(2):
        try:
            r = httpx.get(url, timeout=25)
            if r.status_code == 200:
                return Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception:
            time.sleep(0.5)
    return None


def _hash_schleife(marke, grenze=None):
    _hashlauf.update(aktiv=True, fertig=0, fehler="", start=_now(), stop=False, marke=marke)
    from concurrent.futures import ThreadPoolExecutor
    entnahme = threading.Lock()
    schreiben = threading.Lock()

    def eine(row):
        # Kleines Bild reicht: der Hash rastert ohnehin auf 17x16 herunter, und
        # low.webp laedt ein Vielfaches schneller als high.webp.
        for feld in ("image_de", "image_en"):
            if not row[feld]:
                continue
            img = _bild_holen(f"{row[feld]}/low.webp")
            if img:
                return f"{dhash(img):064x}"
        return ""

    def spur():
        while not _hashlauf["stop"] and _hashlauf["marke"] == marke:
            with entnahme:
                con = _dep["get_db"]()
                rows = con.execute(
                    "SELECT c.id, c.image_de, c.image_en FROM cards c"
                    " LEFT JOIN card_hashes h ON h.card_id = c.id"
                    " WHERE h.card_id IS NULL AND COALESCE(c.image_de, c.image_en) IS NOT NULL"
                    " LIMIT 40").fetchall()
                con.close()
                if not rows:
                    return
                # sofort vormerken, damit keine zweite Spur dieselben zieht
                with schreiben:
                    con = _dep["get_db"]()
                    con.executemany("INSERT OR IGNORE INTO card_hashes (card_id, hash, created_at) VALUES (?,'',?)",
                                    [(r["id"], _now()) for r in rows])
                    con.commit(); con.close()
            werte = []
            for row in rows:
                if _hashlauf["stop"]:
                    break
                try:
                    werte.append((eine(row), row["id"]))
                except Exception as e:
                    _hashlauf["fehler"] = str(e)[:200]
            with schreiben:
                con = _dep["get_db"]()
                con.executemany("UPDATE card_hashes SET hash=?, created_at=datetime('now') WHERE card_id=?", werte)
                con.commit(); con.close()
            _hashlauf["fertig"] += sum(1 for w, _ in werte if w)
            if grenze and _hashlauf["fertig"] >= grenze:
                return

    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            for f in [pool.submit(spur) for _ in range(6)]:
                f.result()
    except Exception as e:
        _hashlauf["fehler"] = str(e)[:300]
    finally:
        if _hashlauf["marke"] == marke:
            _hashlauf["aktiv"] = False
        _vorrat_laden(True)


# --- Foto auswerten ----------------------------------------------------------

def _key():
    k = _dep["env"]().get("OPENROUTER_KEY", "")
    if not k:
        raise HTTPException(503, "Bilderkennung ist gerade nicht verfügbar")
    return k


def _data_url(img):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _boxen(img):
    """Kartenkästchen im Foto finden. → [[ymin,xmin,ymax,xmax], ...] in 0-1000."""
    klein = img.copy()
    klein.thumbnail((MAX_FOTO, MAX_FOTO))
    r = httpx.post("https://openrouter.ai/api/v1/chat/completions",
                   headers={"Authorization": f"Bearer {_key()}", "HTTP-Referer": "https://binderplan.app",
                            "X-Title": "Binderplan"},
                   json={"model": _dep["env"]().get("FOTO_MODELL") or BOX_MODELL,
                         "messages": [{"role": "user", "content": [
                             {"type": "text", "text": BOX_PROMPT},
                             {"type": "image_url", "image_url": {"url": _data_url(klein)}}]}],
                         "response_format": {"type": "json_object"}, "max_tokens": 2000,
                         "usage": {"include": True}}, timeout=120)
    d = r.json()
    if r.status_code != 200 or d.get("error"):
        raise HTTPException(502, f"Bilderkennung: {(d.get('error') or {}).get('message') or r.status_code}")
    txt = re.sub(r"^```(?:json)?|```$", "", ((d.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip(), flags=re.M).strip()
    try:
        erg = json.loads(txt)
    except Exception:
        raise HTTPException(502, "Die Bilderkennung hat unlesbar geantwortet")
    boxen = []
    for e in (erg.get("karten") or []):
        b = e.get("box")
        if isinstance(b, list) and len(b) == 4:
            try:
                ymin, xmin, ymax, xmax = [max(0.0, min(1000.0, float(v))) for v in b]
            except Exception:
                continue
            if ymax - ymin > 60 and xmax - xmin > 40:
                boxen.append((ymin, xmin, ymax, xmax))
    return boxen, float((d.get("usage") or {}).get("cost") or 0)


def _raster_boxen(cols, rows):
    """Rückfall ohne Modell: gleichmäßiges Raster über das ganze Foto."""
    boxen = []
    for r in range(rows):
        for c in range(cols):
            boxen.append((r * 1000 / rows, c * 1000 / cols, (r + 1) * 1000 / rows, (c + 1) * 1000 / cols))
    return boxen


def _erkennen(img, box):
    """Ausschnitt → bester Kartentreffer. Karten liegen auch mal quer, deshalb
    werden alle vier Drehungen probiert."""
    b, h = img.size
    ymin, xmin, ymax, xmax = box
    x0, y0 = int(xmin / 1000 * b), int(ymin / 1000 * h)
    x1, y1 = int(xmax / 1000 * b), int(ymax / 1000 * h)
    if x1 - x0 < 20 or y1 - y0 < 20:
        return None
    aus = img.crop((x0, y0, x1, y1))
    bestes = None
    for dreh in (0, 90, 180, 270):
        probe = aus if dreh == 0 else aus.rotate(dreh, expand=True)
        treffer = _suchen(dhash(probe), 5)
        if not treffer:
            continue
        if bestes is None or treffer[0][0] < bestes[0][0][0]:
            bestes = (treffer, dreh, probe)
    if not bestes:
        return None
    treffer, dreh, probe = bestes
    abstand = treffer[0][0]
    vorsprung = (treffer[1][0] - abstand) if len(treffer) > 1 else 99
    return {"treffer": treffer, "abstand": abstand, "vorsprung": vorsprung, "dreh": dreh, "aus": probe,
            "sicher": abstand <= GUT and vorsprung >= VORSPRUNG}


def register(app, *, get_db, current_user, require_user, env, CACHE, admin_key):
    _dep.update(get_db=get_db, current_user=current_user, require_user=require_user, env=env,
                CACHE=CACHE, admin_key=admin_key)

    con = get_db()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS card_hashes (
            card_id TEXT PRIMARY KEY, hash TEXT, created_at TEXT
        );
    """)
    con.commit()
    con.close()

    def _kontingent(request, kosten=1):
        """Der Import kostet fast nichts, ist aber ein offener Endpunkt — deshalb
        eine Tagesgrenze je Konto bzw. je Adresse."""
        user = current_user(request)
        kennung = str(user["id"]) if user else (request.client.host if request.client else "?")
        schluessel = f"fotoimport:{time.strftime('%Y-%m-%d')}:{kennung}"
        con = get_db()
        row = con.execute("SELECT value FROM kv WHERE key=?", (schluessel,)).fetchone()
        n = int(row["value"]) if row else 0
        grenze = 120 if user else 20
        if n + kosten > grenze:
            con.close()
            raise HTTPException(429, detail={"code": "foto_limit", "grenze": grenze})
        con.execute("INSERT OR REPLACE INTO kv (key,value) VALUES (?,?)", (schluessel, str(n + kosten)))
        con.commit()
        con.close()

    @app.get("/api/import/status")
    def import_status():
        con = get_db()
        gesamt = con.execute("SELECT COUNT(*) n FROM cards WHERE COALESCE(image_de,image_en) IS NOT NULL").fetchone()["n"]
        fertig = con.execute("SELECT COUNT(*) n FROM card_hashes WHERE hash <> ''").fetchone()["n"]
        con.close()
        return {"aktiv": bool(env().get("OPENROUTER_KEY")), "fingerabdruecke": fertig, "gesamt": gesamt,
                "lauf": {k: v for k, v in _hashlauf.items() if k not in ("stop", "marke")}}

    @app.post("/api/import/fingerabdruecke")
    def import_hashes(key: str = "", stop: bool = False, grenze: int = 0):
        if not admin_key() or key != admin_key():
            raise HTTPException(403)
        if stop:
            _hashlauf["stop"] = True
            return {"ok": True, "gestoppt": True}
        if _hashlauf["aktiv"]:
            raise HTTPException(409, "Läuft bereits")
        marke = _hashlauf["marke"] + 1
        threading.Thread(target=_hash_schleife, args=(marke, grenze or None), daemon=True).start()
        time.sleep(0.3)
        return {"ok": True, "lauf": {k: v for k, v in _hashlauf.items() if k not in ("stop", "marke")}}

    @app.post("/api/import/foto")
    async def import_foto(request: Request, datei: UploadFile, layout: str = "3x3", raster: int = 0):
        """Ein Foto einer Binderseite → erkannte Karten in Lesereihenfolge."""
        _vorrat_laden()
        if len(_vorrat["ids"]) < 500:
            raise HTTPException(503, detail={"code": "index_unfertig",
                                             "text": "Die Kartenbilder werden gerade eingelesen. Bitte später erneut."})
        roh = await datei.read()
        if len(roh) > 12 * 1024 * 1024:
            raise HTTPException(413, "Das Foto ist zu groß (max. 12 MB)")
        try:
            img = ImageOps.exif_transpose(Image.open(io.BytesIO(roh))).convert("RGB")
        except Exception:
            raise HTTPException(400, "Das ist kein lesbares Bild")
        img.thumbnail((2000, 2000))
        _kontingent(request)

        kosten = 0.0
        cols, rows = (int(x) for x in (layout.split("x") + ["3"])[:2]) if re.fullmatch(r"\d+x\d+", layout) else (3, 3)
        if raster:
            boxen = _raster_boxen(cols, rows)
        else:
            try:
                boxen, kosten = _boxen(img)
            except HTTPException:
                boxen = _raster_boxen(cols, rows)
        if not boxen:
            boxen = _raster_boxen(cols, rows)

        con = get_db()
        karten = []
        for i, box in enumerate(boxen[: cols * rows * 2]):
            e = _erkennen(img, box)
            if not e:
                continue
            alternativen = []
            for abstand, cid in e["treffer"]:
                r = con.execute(
                    "SELECT c.id, c.name_de, c.name_en, c.local_id, s.name AS setn FROM cards c"
                    " LEFT JOIN sets s ON s.id=c.set_id WHERE c.id=?", (cid,)).fetchone()
                if r:
                    alternativen.append({"id": r["id"], "name": r["name_de"] or r["name_en"],
                                         "set": r["setn"] or "", "nr": r["local_id"], "abstand": abstand})
            if not alternativen:
                continue
            karten.append({"platz": i, "id": alternativen[0]["id"], "name": alternativen[0]["name"],
                           "set": alternativen[0]["set"], "nr": alternativen[0]["nr"],
                           "abstand": e["abstand"], "vorsprung": e["vorsprung"], "sicher": bool(e["sicher"]),
                           "ausschnitt": _vorschau(e["aus"]), "alternativen": alternativen[1:]})
        con.close()
        return {"karten": karten, "faecher": cols * rows, "kosten_usd": round(kosten, 5)}

    def _vorschau(img):
        klein = img.copy()
        klein.thumbnail((150, 210))
        buf = io.BytesIO()
        klein.save(buf, "JPEG", quality=70)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

    def kennzahlen():
        con = get_db()
        n = con.execute("SELECT COUNT(*) n FROM card_hashes WHERE hash <> ''").fetchone()["n"]
        con.close()
        return n

    return kennzahlen
