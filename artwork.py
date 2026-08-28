# Binderplan – KI-Artwork-Seiten
#
# Idee: Eine Binder-Seite (z. B. 3×3) trägt eine oder mehrere echte Karten in bestimmten
# Fächern. Die übrigen Fächer werden mit KI-Kunst gefüllt, die das Motiv der Karte(n)
# über die ganze Seite hinaus erweitert. Ausgedruckt (63×88 mm je Fach) und in die
# leeren Hüllen gesteckt ergibt das eine durchgehende Bildseite rund um die Originalkarte.
#
# Ablauf:
#   1. Vorlage bauen: Seite in Modell-Seitenverhältnis, Kartenscans exakt an ihren Fächern,
#      alles andere grau („hier malen“).
#   2. Bildmodell (OpenRouter, Gemini „Nano Banana“) erweitert das Motiv im gewünschten Stil.
#   3. Ergebnis auf Vorlagengröße bringen, Seite ausschneiden, Kartenscans pixelgenau
#      zurücksetzen → Ganzseiten-PNG + Vorschau.
#   4. Druck-PDF: je Fach ein 63×88-mm-Ausschnitt (Kartenfächer werden ausgelassen, optional
#      als Proxy mitgedruckt), 9 Ausschnitte pro A4, Beschriftung „Seite X · Fach Y“ in der Fuge.
#
# Läuft als Hintergrund-Job (30–90 s); das Frontend fragt den Status ab.
# Eingehängt über register(app, …) aus main.py, damit main.py nicht weiter wächst.

import base64
import io
import json
import re
import secrets
import threading
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import HTTPException, Request, Response
from fastapi.responses import FileResponse
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdfcanvas

# Kontingente: Free-Konten dürfen eine Probeseite erzeugen, Pro/Lifetime 20 je Monat
# (eine Generierung kostet je nach Modell 0,05–0,15 € – unbegrenzt wäre nicht tragbar).
FREE_ARTWORK_GESAMT = 1
PRO_ARTWORK_MONAT = 20

STANDARD_MODELL = "google/gemini-3.1-flash-image"     # Nano Banana 2 – Bild rein, Bild raus
STANDARD_GROESSE = "2K"

# Bildgröße je Fach/Fuge wie im Platzhalter-PDF (mm)
KARTE_W, KARTE_H, FUGE = 63.0, 88.0, 4.0

# Vom Modell unterstützte Seitenverhältnisse (Breite/Höhe) – die Vorlage wird ins nächstgelegene
# gelegt, das Ergebnis danach wieder auf die echte Seite zugeschnitten.
SEITENVERHAELTNISSE = {
    "1:1": 1.0, "4:5": 0.8, "3:4": 0.75, "2:3": 2 / 3, "9:16": 9 / 16,
    "5:4": 1.25, "4:3": 4 / 3, "3:2": 1.5, "16:9": 16 / 9,
}
LANGE_SEITE = {"1K": 1024, "2K": 2048, "4K": 4096}

# Stile: Schlüssel → Anweisung fürs Bildmodell (englisch – die Modelle folgen so am zuverlässigsten)
STILE = {
    "karte": "Continue in exactly the visual style, technique, line quality and color palette of the card "
             "illustration itself, so the extension is indistinguishable from the original artwork.",
    "comic": "Bold comic-book style: strong ink outlines, flat cel shading, halftone dots, dynamic composition – "
             "painted as ONE full-page splash illustration, never divided into comic panels.",
    "foto": "Photorealistic: cinematic lighting, realistic materials, atmosphere and depth of field, "
            "as if the scene were photographed in the real world.",
    "aquarell": "Soft watercolor painting: wet washes, visible paper texture, gently bleeding edges, light colors.",
    "oel": "Classical oil painting: visible brush strokes, rich impasto texture, dramatic chiaroscuro lighting.",
    "anime": "Modern anime key-visual style: clean line art, vibrant cel shading, expressive dramatic lighting.",
    "retro": "1990s classic Pokémon card illustration style: airbrushed, soft gradients, nostalgic, slightly grainy.",
    "pixel": "Retro pixel art: 16-bit sprite aesthetic, limited palette, crisp visible pixels.",
    "neon": "Neon synthwave: glowing magenta and cyan light, dark background, retro-futuristic grid and haze.",
    "skizze": "Pencil sketch: graphite hatching on white paper, loose confident sketch lines, monochrome.",
    "minimal": "Minimalist flat vector illustration: few colors, clean geometric shapes, calm negative space.",
    "dunkel": "Dark fantasy: moody dramatic shadows, mystical atmosphere, deep saturated colors, epic scale.",
}

# Wird von register() befüllt (Helfer aus main.py)
_dep = {}
_jobs_lock = threading.Lock()


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _monat():
    return datetime.utcnow().strftime("%Y-%m")


def _artwork_dir() -> Path:
    d = _dep["CACHE"] / "artwork"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --- Kontingent ---------------------------------------------------------------

def _kontingent(user):
    """→ (benutzt, limit, zeitraum) – zeitraum 'monat' (Pro) oder 'gesamt' (Free-Probeseite)."""
    if _dep["ist_pro"](user):
        teil = (user.get("artwork_monat") or "").split(":")
        benutzt = int(teil[1]) if len(teil) == 2 and teil[0] == _monat() else 0
        return benutzt, PRO_ARTWORK_MONAT, "monat"
    return int(user.get("artwork_gesamt") or 0), FREE_ARTWORK_GESAMT, "gesamt"


def _kontingent_buchen(user, delta):
    con = _dep["get_db"]()
    if _dep["ist_pro"](user):
        benutzt, _, _ = _kontingent(user)
        con.execute("UPDATE users SET artwork_monat = ? WHERE id = ?",
                    (f"{_monat()}:{max(0, benutzt + delta)}", user["id"]))
    else:
        con.execute("UPDATE users SET artwork_gesamt = MAX(0, COALESCE(artwork_gesamt, 0) + ?) WHERE id = ?",
                    (delta, user["id"]))
    con.commit()
    con.close()


def _kontingent_info(user):
    benutzt, limit, zeitraum = _kontingent(user)
    return {"benutzt": benutzt, "limit": limit, "zeitraum": zeitraum, "frei": max(0, limit - benutzt)}


# --- Geometrie ----------------------------------------------------------------

def _seite_mm(cols, rows):
    return cols * KARTE_W + (cols - 1) * FUGE, rows * KARTE_H + (rows - 1) * FUGE


def _fach_mm(slot, cols):
    """Position (x, y von oben) eines Fachs in mm auf der Seite."""
    col, row = slot % cols, slot // cols
    return col * (KARTE_W + FUGE), row * (KARTE_H + FUGE)


def _verhaeltnis_waehlen(w, h):
    ziel = w / h
    return min(SEITENVERHAELTNISSE.items(), key=lambda kv: abs(kv[1] - ziel))[0]


def _geometrie(cols, rows, groesse):
    """Leinwandmaße im Modell-Seitenverhältnis und der Pixelrahmen der Binder-Seite darin."""
    pw, ph = _seite_mm(cols, rows)
    ar = _verhaeltnis_waehlen(pw, ph)
    r = SEITENVERHAELTNISSE[ar]
    lang = LANGE_SEITE.get(groesse, 2048)
    cw, ch = (lang, round(lang / r)) if r >= 1 else (round(lang * r), lang)
    # Seite so groß wie möglich zentriert einpassen (schmaler Rand wird mitgemalt, dann abgeschnitten)
    skala = min((cw - 16) / pw, (ch - 16) / ph)
    sw, sh = round(pw * skala), round(ph * skala)
    ox, oy = (cw - sw) // 2, (ch - sh) // 2
    return {"ar": ar, "cw": cw, "ch": ch, "skala": skala, "seite": (ox, oy, ox + sw, oy + sh)}


def _fach_box(slot, cols, geo):
    x, y = _fach_mm(slot, cols)
    ox, oy = geo["seite"][0], geo["seite"][1]
    s = geo["skala"]
    return (ox + round(x * s), oy + round(y * s), ox + round((x + KARTE_W) * s), oy + round((y + KARTE_H) * s))


def _kartenbild(card_id, lang):
    pfad = _dep["card_image_path"](card_id, lang)
    if not pfad:
        return None
    try:
        img = Image.open(pfad).convert("RGBA")
    except Exception:
        return None
    bg = Image.new("RGB", img.size, "white")
    bg.paste(img, mask=img.split()[-1])
    return bg


def _karten_einsetzen(seite_img, anker, cols, geo, lang, offset=(0, 0)):
    """Kartenscans exakt in ihre Fächer setzen (auf Vorlage und Ergebnis identisch)."""
    for slot, card_id in anker.items():
        bild = _kartenbild(card_id, lang)
        if not bild:
            continue
        x0, y0, x1, y1 = _fach_box(int(slot), cols, geo)
        x0, y0, x1, y1 = x0 - offset[0], y0 - offset[1], x1 - offset[0], y1 - offset[1]
        seite_img.paste(bild.resize((x1 - x0, y1 - y0), Image.LANCZOS), (x0, y0))


def _vorlage(anker, cols, rows, geo, lang):
    img = Image.new("RGB", (geo["cw"], geo["ch"]), (128, 128, 128))
    _karten_einsetzen(img, anker, cols, geo, lang)
    return img


# --- Prompt & Modellaufruf --------------------------------------------------------

def _prompt(cols, rows, anker, stil, wunsch, namen):
    plaetze = []
    for slot, card_id in sorted(anker.items(), key=lambda kv: int(kv[0])):
        col, row = int(slot) % cols, int(slot) // cols
        plaetze.append(f"row {row + 1}, column {col + 1} ({namen.get(card_id) or 'trading card'})")
    mehrere = len(anker) > 1
    text = (
        f"The attached image is a trading-card binder page with a grid of {cols} columns × {rows} rows of card pockets. "
        f"{'Real cards are' if mehrere else 'A real card is'} already placed on it at: {'; '.join(plaetze)}. "
        "Every gray area is empty and must be painted.\n\n"
        "Task: paint all gray areas so that the illustration of the card"
        + ("s continues seamlessly beyond their borders and all card scenes merge into ONE coherent, "
           "continuous world that fills the whole page" if mehrere else
           " continues seamlessly beyond its borders and fills the whole page as one coherent, continuous scene")
        + " – extended background, environment, landscape, lighting, weather and effects, in the same mood and "
        "perspective as the card art. The card must feel like a window into the larger painting.\n\n"
        "Strict rules:\n"
        "- Keep every card exactly where it is: identical position, size and content, including its frame and text.\n"
        "- Do NOT add new cards, card frames, borders, panels, text, letters, numbers, logos or watermarks anywhere.\n"
        "- The new areas contain only artwork, no grid lines and no gray left.\n"
        "- The page is ONE single uninterrupted painting: do not divide it into panels, tiles, pockets or "
        "framed sections – the pocket grid does not exist in the picture.\n"
        "- Output exactly the same dimensions and layout as the input image.\n\n"
        f"Style of the new artwork: {STILE.get(stil, STILE['karte'])}"
    )
    if wunsch:
        text += f"\n\nAdditional wishes from the collector: {wunsch.strip()[:400]}"
    return text


def _modell_aufruf(vorlage: Image.Image, prompt, modell, ar, groesse):
    key = _dep["env"]().get("OPENROUTER_KEY", "")
    if not key:
        raise RuntimeError("Kein OPENROUTER_KEY in .env")
    buf = io.BytesIO()
    vorlage.save(buf, "PNG", optimize=True)
    data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    body = {
        "model": modell,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}],
        "modalities": ["image", "text"],
        "image_config": {"aspect_ratio": ar, "image_size": groesse},
        "usage": {"include": True},
    }
    r = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://binderplan.app", "X-Title": "Binderplan"},
        json=body, timeout=240,
    )
    d = r.json()
    if r.status_code != 200 or d.get("error"):
        raise RuntimeError(f"Bildmodell: {(d.get('error') or {}).get('message') or r.status_code}")
    msg = (d.get("choices") or [{}])[0].get("message") or {}
    bilder = msg.get("images") or []
    url = (bilder[0].get("image_url") or {}).get("url") if bilder else None
    if not url or not url.startswith("data:image"):
        raise RuntimeError("Das Bildmodell hat kein Bild geliefert" + (f": {str(msg.get('content'))[:160]}" if msg.get("content") else "."))
    roh = base64.b64decode(url.split(",", 1)[1])
    kosten = float((d.get("usage") or {}).get("cost") or 0)
    return Image.open(io.BytesIO(roh)).convert("RGB"), kosten, d.get("model") or modell


# --- Job ------------------------------------------------------------------------

def _job(artwork_id):
    get_db = _dep["get_db"]
    con = get_db()
    row = con.execute("SELECT * FROM artworks WHERE id = ?", (artwork_id,)).fetchone()
    con.close()
    if not row:
        return
    try:
        cols, rows = [int(v) for v in row["layout"].split("x")]
        anker = json.loads(row["anker"] or "{}")
        geo = _geometrie(cols, rows, row["groesse"])
        lang = row["sprache"] or "de"
        vorlage = _vorlage(anker, cols, rows, geo, lang)
        con = get_db()
        namen = {}
        for cid in anker.values():
            r = con.execute("SELECT name_de, name_en FROM cards WHERE id = ?", (cid,)).fetchone()
            if r:
                namen[cid] = f"Pokémon card «{r['name_en'] or r['name_de']}»"
        con.close()
        prompt = _prompt(cols, rows, anker, row["stil"], row["wunsch"] or "", namen)
        ergebnis, kosten, modell = _modell_aufruf(vorlage, prompt, row["modell"], geo["ar"], row["groesse"])
        # Ergebnis auf Vorlagenmaß bringen, Seite ausschneiden, Kartenscans pixelgenau zurücksetzen
        if ergebnis.size != (geo["cw"], geo["ch"]):
            ergebnis = ergebnis.resize((geo["cw"], geo["ch"]), Image.LANCZOS)
        x0, y0, x1, y1 = geo["seite"]
        seite = ergebnis.crop((x0, y0, x1, y1))
        _karten_einsetzen(seite, anker, cols, geo, lang, offset=(x0, y0))
        d = _artwork_dir()
        seite.save(d / f"{artwork_id}.png", "PNG", optimize=True)
        vorschau = seite.copy()
        vorschau.thumbnail((900, 900), Image.LANCZOS)
        vorschau.save(d / f"{artwork_id}.vorschau.webp", "WEBP", quality=82)
        con = get_db()
        con.execute("UPDATE artworks SET status='fertig', modell=?, kosten_usd=?, breite=?, hoehe=?, fertig_at=?"
                    " WHERE id=?", (modell, kosten, seite.width, seite.height, _now(), artwork_id))
        con.commit()
        con.close()
    except Exception as e:  # Fehler festhalten, Kontingent zurückbuchen
        con = get_db()
        con.execute("UPDATE artworks SET status='fehler', fehler=? WHERE id=?", (str(e)[:300], artwork_id))
        user = con.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
        con.commit()
        con.close()
        if user:
            _kontingent_buchen(dict(user), -1)


# --- PDF ------------------------------------------------------------------------

def _pdf(artwork, mit_karten, lang):
    """Druckbogen: je Fach ein Ausschnitt in 63×88 mm, 9 je A4, Beschriftung in der Fuge."""
    cols, rows = [int(v) for v in artwork["layout"].split("x")]
    anker = json.loads(artwork["anker"] or "{}")
    seite = Image.open(_artwork_dir() / f"{artwork['id']}.png").convert("RGB")
    pw, ph = _seite_mm(cols, rows)
    sx, sy = seite.width / pw, seite.height / ph

    CARD_W, CARD_H, GUTTER = KARTE_W * mm, KARTE_H * mm, FUGE * mm
    COLS, ROWS = 3, 3
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    page_w, page_h = A4
    grid_w = COLS * CARD_W + (COLS - 1) * GUTTER
    grid_h = ROWS * CARD_H + (ROWS - 1) * GUTTER
    x_start = (page_w - grid_w) / 2
    y_top = page_h - (page_h - grid_h) / 2

    faecher = [s for s in range(cols * rows) if mit_karten or str(s) not in anker]
    cell = 0
    for slot in faecher:
        if cell and cell % (COLS * ROWS) == 0:
            c.showPage()
        i = cell % (COLS * ROWS)
        x = x_start + (i % COLS) * (CARD_W + GUTTER)
        y = y_top - (i // COLS + 1) * CARD_H - (i // COLS) * GUTTER
        fx, fy = _fach_mm(slot, cols)
        box = (round(fx * sx), round(fy * sy), round((fx + KARTE_W) * sx), round((fy + KARTE_H) * sy))
        tile = seite.crop(box)
        tb = io.BytesIO()
        tile.save(tb, "JPEG", quality=90)
        tb.seek(0)
        c.drawImage(ImageReader(tb), x, y, CARD_W, CARD_H)
        if str(slot) in anker:
            _dep["pdf_wasserzeichen"](c, x, y, lang)
        c.setLineWidth(0.4)
        c.setStrokeGray(0.75)
        c.rect(x, y, CARD_W, CARD_H)
        c.setFillGray(0.45)
        c.setFont("Helvetica", 6.5)
        label = (f"Page {artwork['seite'] + 1} · Slot {slot + 1}" if lang == "en"
                 else f"Seite {artwork['seite'] + 1} · Fach {slot + 1}")
        label += " · Artwork" if str(slot) not in anker else (" · Card" if lang == "en" else " · Karte")
        c.drawCentredString(x + CARD_W / 2, y - 2.6 * mm, label)
        cell += 1
    if not faecher:
        c.setFont("Helvetica", 14)
        c.drawCentredString(page_w / 2, page_h / 2, "Nichts zu drucken." if lang == "de" else "Nothing to print.")
    c.save()
    return buf.getvalue()


# --- Endpunkte ----------------------------------------------------------------

def _artwork_row(artwork_id, user):
    con = _dep["get_db"]()
    row = con.execute("SELECT * FROM artworks WHERE id = ?", (artwork_id,)).fetchone()
    con.close()
    if not row:
        raise HTTPException(404, "Artwork nicht gefunden")
    if not user or row["user_id"] != user["id"]:
        raise HTTPException(403, detail={"code": "fremder_binder"})
    return dict(row)


def _payload(row):
    return {
        "id": row["id"], "binder_id": row["binder_id"], "seite": row["seite"], "layout": row["layout"],
        "anker": json.loads(row["anker"] or "{}"), "stil": row["stil"], "wunsch": row["wunsch"] or "",
        "status": row["status"], "fehler": row["fehler"], "breite": row["breite"], "hoehe": row["hoehe"],
        "created_at": row["created_at"], "modell": row["modell"],
        "vorschau": f"api/artwork/{row['id']}/bild?v=vorschau" if row["status"] == "fertig" else None,
    }


def register(app, *, get_db, current_user, require_user, ist_pro, load_binder, card_image_path,
             pdf_wasserzeichen, env, CACHE):
    _dep.update(get_db=get_db, current_user=current_user, require_user=require_user, ist_pro=ist_pro,
                load_binder=load_binder, card_image_path=card_image_path,
                pdf_wasserzeichen=pdf_wasserzeichen, env=env, CACHE=CACHE)

    con = get_db()
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS artworks (
            id TEXT PRIMARY KEY, user_id INTEGER, binder_id TEXT, seite INTEGER, layout TEXT,
            anker TEXT, stil TEXT, wunsch TEXT, sprache TEXT, modell TEXT, groesse TEXT,
            status TEXT, fehler TEXT, kosten_usd REAL DEFAULT 0, breite INTEGER, hoehe INTEGER,
            created_at TEXT DEFAULT (datetime('now')), fertig_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_artworks_binder ON artworks(binder_id);
        """
    )
    for alter in ("ALTER TABLE users ADD COLUMN artwork_monat TEXT",
                  "ALTER TABLE users ADD COLUMN artwork_gesamt INTEGER DEFAULT 0"):
        try:
            con.execute(alter)
        except Exception:
            pass
    # Jobs, die einen Neustart nicht überlebt haben
    con.execute("UPDATE artworks SET status='fehler', fehler='Abgebrochen (Neustart)' WHERE status='laeuft'")
    con.commit()
    con.close()

    @app.get("/api/artwork/stile")
    def artwork_stile(request: Request):
        user = current_user(request)
        return {"stile": list(STILE.keys()), "kontingent": _kontingent_info(user) if user else None,
                "aktiv": bool(env().get("OPENROUTER_KEY"))}

    @app.post("/api/artwork")
    async def artwork_start(request: Request):
        user = require_user(request)
        data = await request.json()
        binder = load_binder(str(data.get("binder_id") or ""))
        if binder.get("id") is None:
            raise HTTPException(404, "Binder nicht gefunden")
        con = get_db()
        besitzer = con.execute("SELECT user_id FROM binders WHERE id = ?", (binder["id"],)).fetchone()
        con.close()
        if besitzer and besitzer["user_id"] not in (None, user["id"]):
            raise HTTPException(403, detail={"code": "fremder_binder"})
        layout = binder["layout"]
        cols, rows = [int(v) for v in layout.split("x")]
        pp = cols * rows
        seite = max(0, int(data.get("seite") or 0))
        items = binder["items"][seite * pp:(seite + 1) * pp]
        # Anker = Kartenfächer der Seite, optional vom Nutzer eingeschränkt („KI füllt“ auch über Karten)
        erlaubt = data.get("anker")
        anker = {}
        for s, item in enumerate(items):
            if item and item.get("type") == "card" and item.get("id"):
                if erlaubt is None or s in erlaubt or str(s) in erlaubt:
                    anker[str(s)] = item["id"]
        if not anker:
            raise HTTPException(400, detail={"code": "kein_anker"})
        if len(anker) >= pp:
            raise HTTPException(400, detail={"code": "kein_platz"})
        stil = data.get("stil") if data.get("stil") in STILE else "karte"
        wunsch = str(data.get("wunsch") or "")[:400]
        benutzt, limit, _ = _kontingent(user)
        if benutzt >= limit:
            raise HTTPException(402, detail={"code": "limit_artwork"})
        if not env().get("OPENROUTER_KEY"):
            raise HTTPException(503, "Artwork-Funktion ist nicht eingerichtet")
        with _jobs_lock:
            con = get_db()
            laufend = con.execute("SELECT COUNT(*) c FROM artworks WHERE user_id=? AND status='laeuft'",
                                  (user["id"],)).fetchone()["c"]
            con.close()
            if laufend:
                raise HTTPException(409, detail={"code": "artwork_laeuft"})
            artwork_id = secrets.token_urlsafe(9)
            sprache = "en" if (binder.get("options") or {}).get("sprache") == "en" else "de"
            e = env()
            con = get_db()
            con.execute(
                "INSERT INTO artworks (id,user_id,binder_id,seite,layout,anker,stil,wunsch,sprache,modell,groesse,status)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,'laeuft')",
                (artwork_id, user["id"], binder["id"], seite, layout, json.dumps(anker), stil, wunsch, sprache,
                 e.get("ARTWORK_MODELL") or STANDARD_MODELL, e.get("ARTWORK_GROESSE") or STANDARD_GROESSE),
            )
            con.commit()
            con.close()
            _kontingent_buchen(user, +1)
        threading.Thread(target=_job, args=(artwork_id,), daemon=True).start()
        return {"id": artwork_id, "status": "laeuft"}

    @app.get("/api/artwork")
    def artwork_liste(request: Request, binder_id: str = ""):
        user = require_user(request)
        con = get_db()
        rows = con.execute(
            "SELECT * FROM artworks WHERE user_id = ? AND (? = '' OR binder_id = ?) AND status != 'fehler'"
            " ORDER BY created_at DESC LIMIT 60", (user["id"], binder_id, binder_id)).fetchall()
        con.close()
        return {"artworks": [_payload(dict(r)) for r in rows], "kontingent": _kontingent_info(user)}

    @app.get("/api/artwork/{artwork_id}")
    def artwork_status(artwork_id: str, request: Request):
        user = require_user(request)
        row = _artwork_row(artwork_id, user)
        out = _payload(row)
        # Nutzer nach dem Job aktuell nachladen (Kontingent kann zurückgebucht worden sein)
        out["kontingent"] = _kontingent_info(current_user(request) or user)
        return out

    @app.get("/api/artwork/{artwork_id}/bild")
    def artwork_bild(artwork_id: str, request: Request, v: str = "vorschau"):
        row = _artwork_row(artwork_id, current_user(request))
        if row["status"] != "fertig":
            raise HTTPException(404, "Noch nicht fertig")
        d = _artwork_dir()
        if v == "voll":
            name = re.sub(r"[^A-Za-z0-9_-]", "", f"artwork-seite-{row['seite'] + 1}-{row['stil']}") + ".png"
            return FileResponse(d / f"{artwork_id}.png", media_type="image/png",
                                headers={"Content-Disposition": f'attachment; filename="{name}"'})
        return FileResponse(d / f"{artwork_id}.vorschau.webp", media_type="image/webp",
                            headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/api/artwork/{artwork_id}/pdf")
    def artwork_pdf(artwork_id: str, request: Request, mit_karten: int = 0):
        user = require_user(request)
        row = _artwork_row(artwork_id, user)
        if row["status"] != "fertig":
            raise HTTPException(404, "Noch nicht fertig")
        lang = row["sprache"] or "de"
        pdf = _pdf(row, bool(mit_karten), lang)
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="artwork-seite-{row["seite"] + 1}.pdf"'})

    @app.delete("/api/artwork/{artwork_id}")
    def artwork_loeschen(artwork_id: str, request: Request):
        user = require_user(request)
        _artwork_row(artwork_id, user)
        con = get_db()
        con.execute("DELETE FROM artworks WHERE id = ?", (artwork_id,))
        con.commit()
        con.close()
        d = _artwork_dir()
        for f in (d / f"{artwork_id}.png", d / f"{artwork_id}.vorschau.webp"):
            try:
                f.unlink()
            except FileNotFoundError:
                pass
        return {"ok": True}

    def kennzahlen():
        """Fürs Admin-Dashboard: Anzahl + Kosten der Artwork-Seiten."""
        con = get_db()
        r = con.execute("SELECT COUNT(*) c, COALESCE(SUM(kosten_usd),0) k FROM artworks WHERE status='fertig'").fetchone()
        con.close()
        return r["c"], r["k"]

    return kennzahlen
