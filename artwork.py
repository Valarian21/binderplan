# Binderplan – KI-Artwork-Seiten
#
# Idee: Eine Binder-Seite (z. B. 3×3) trägt eine oder mehrere echte Karten in bestimmten
# Fächern. Die übrigen Fächer werden mit KI-Kunst gefüllt, die das Motiv der Karte(n)
# über die ganze Seite hinaus erweitert. Ausgedruckt (63×88 mm je Fach) und in die
# leeren Hüllen gesteckt ergibt das eine durchgehende Bildseite rund um die Originalkarte.
#
# Ablauf:
#   1. Analyse je Karte (Vision-Modell, gecacht): Was zeigt die Illustration, was ist an
#      welchem Rand abgeschnitten, Horizont, Licht, Palette – und wo genau sitzt das
#      Illustrationsfenster auf der Karte (Box).
#   2. Vorlage bauen: Seite im Modell-Seitenverhältnis. Von jeder Karte steht NUR das
#      Illustrationsfenster an seiner echten Position – der Kartenrahmen bleibt grau, also
#      „zu malen“. So setzt das Bildmodell die Szene direkt an den Bildkanten fort
#      (echtes Outpainting); die echte Karte deckt später den Rahmenbereich wieder ab.
#   3. Bildmodell (OpenRouter, Gemini „Nano Banana“) malt alles Graue: Beschreibung,
#      Illustrations-Ausschnitte und Referenzbilder gewünschter Pokémon als Kontext.
#   4. Ergebnis auf Vorlagengröße bringen, Seite ausschneiden, Kartenscans pixelgenau
#      zurücksetzen → Ganzseiten-PNG + Vorschau.
#   5. Druck-PDF: je Fach ein 63×88-mm-Ausschnitt (Kartenfächer werden ausgelassen, optional
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
MAX_POKEMON = 3

STANDARD_MODELL = "google/gemini-3.1-flash-image"     # Nano Banana 2 – Bild rein, Bild raus
STANDARD_GROESSE = "2K"
STUFE_A_FAKTOR = 2.3      # Stufe A: Illustration um diesen Faktor erweitern (kleiner Schritt = genaue Geometrie)
STUFE_A_MAX_ANTEIL = 0.8  # deckt Stufe A schon ≥ 80 % der Seite, entfällt Stufe B
ANALYSE_MODELL = "google/gemini-3.5-flash"            # Vision-Analyse der Karte (≈ 0,5 ct)

# Bildgröße je Fach/Fuge wie im Platzhalter-PDF (mm)
KARTE_W, KARTE_H, FUGE = 63.0, 88.0, 4.0

# Vom Modell unterstützte Seitenverhältnisse (Breite/Höhe) – die Vorlage wird ins nächstgelegene
# gelegt, das Ergebnis danach wieder auf die echte Seite zugeschnitten.
SEITENVERHAELTNISSE = {
    "1:1": 1.0, "4:5": 0.8, "3:4": 0.75, "2:3": 2 / 3, "9:16": 9 / 16,
    "5:4": 1.25, "4:3": 4 / 3, "3:2": 1.5, "16:9": 16 / 9,
}
LANGE_SEITE = {"1K": 1024, "2K": 2048, "4K": 4096}

# Stile: Schlüssel → Anweisung fürs Bildmodell. Der Stil ist NUR die Maltechnik – der Inhalt bleibt
# immer die Fortsetzung der Kartenszene (steht so im Prompt).
STILE = {
    "karte": "Exactly the technique, line quality, brushwork and color palette of the card illustration itself – "
             "the extension must be indistinguishable from the original artwork.",
    "comic": "Bold comic-book rendering: strong ink outlines, flat cel shading, halftone dots – painted as ONE "
             "full-page splash illustration, never divided into comic panels.",
    "foto": "Photorealistic rendering: cinematic lighting, realistic materials, atmosphere and depth of field, "
            "as if the card's scene were photographed in the real world.",
    "aquarell": "Soft watercolor rendering: wet washes, visible paper texture, gently bleeding edges, light colors.",
    "oel": "Classical oil-painting rendering: visible brush strokes, rich impasto texture, chiaroscuro lighting.",
    "anime": "Modern anime key-visual rendering: clean line art, vibrant cel shading, expressive dramatic lighting.",
    "retro": "1990s classic Pokémon card illustration rendering: airbrushed, soft gradients, nostalgic, slightly grainy.",
    "pixel": "Retro pixel-art rendering: 16-bit sprite aesthetic, limited palette, crisp visible pixels.",
    "neon": "Neon synthwave rendering: glowing magenta and cyan light, dark tones, retro-futuristic haze.",
    "skizze": "Pencil-sketch rendering: graphite hatching on white paper, loose confident sketch lines, monochrome.",
    "minimal": "Minimalist flat-vector rendering: few colors, clean geometric shapes, calm negative space.",
    "dunkel": "Dark-fantasy rendering: moody dramatic shadows, mystical atmosphere, deep saturated colors, epic scale.",
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


def _key():
    key = _dep["env"]().get("OPENROUTER_KEY", "")
    if not key:
        raise RuntimeError("Kein OPENROUTER_KEY in .env")
    return key


def _openrouter(body, timeout=240):
    r = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://binderplan.app", "X-Title": "Binderplan"},
        json=body, timeout=timeout,
    )
    d = r.json()
    if r.status_code != 200 or d.get("error"):
        raise RuntimeError(f"Bildmodell: {(d.get('error') or {}).get('message') or r.status_code}")
    return d


def _data_url(img: Image.Image, fmt="PNG"):
    buf = io.BytesIO()
    if fmt == "JPEG":
        img.convert("RGB").save(buf, "JPEG", quality=92)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    img.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


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
    """Kartenscans exakt in ihre Fächer setzen (auf dem Ergebnis, damit die Karte unangetastet bleibt)."""
    for slot, card_id in anker.items():
        bild = _kartenbild(card_id, lang)
        if not bild:
            continue
        x0, y0, x1, y1 = _fach_box(int(slot), cols, geo)
        x0, y0, x1, y1 = x0 - offset[0], y0 - offset[1], x1 - offset[0], y1 - offset[1]
        seite_img.paste(bild.resize((x1 - x0, y1 - y0), Image.LANCZOS), (x0, y0))


# --- Karten-Analyse (Vision-Modell, gecacht) ---------------------------------------

ANALYSE_PROMPT = (
    "You see a scan of a Pokémon trading card. Analyze ONLY the illustration inside the picture window "
    "(the artwork box); ignore the frame, name, HP, attacks and text. Answer with a single JSON object:\n"
    '{"box": [ymin, xmin, ymax, xmax] of the illustration window in 0-1000 normalized coordinates of the whole image '
    "(for full-art cards where the illustration covers the entire card use the card edges),\n"
    '"subject": the main subject – species, pose, size within the frame, facing/moving direction,\n'
    '"scene": setting and background elements with their placement (e.g. "volcano on the left, lava lake below"),\n'
    '"edges": {"left": which BACKGROUND/scenery elements (never the creature) touch or are cut off at the left edge '
    'and how they would continue beyond it, "right": ..., "top": ..., "bottom": ...},\n'
    '"horizon": horizon height as a fraction of the illustration height from the top, or "none",\n'
    '"perspective": camera angle (eye level / low angle / bird view) and depth cues,\n'
    '"light": light direction, time of day, weather,\n'
    '"palette": 3-5 dominant colors,\n'
    '"technique": painting medium and technique (e.g. airbrush, watercolor, digital cel shading, 3D render),\n'
    '"mood": mood in a few words}\n'
    "Be concrete and visual; write in English. JSON only."
)


def _analyse(card_id, lang):
    """Beschreibung + Box des Illustrationsfensters, je Karte einmal ermittelt und in der DB gecacht."""
    get_db = _dep["get_db"]
    con = get_db()
    row = con.execute("SELECT daten FROM card_art_analysis WHERE card_id=? AND lang=?", (card_id, lang)).fetchone()
    con.close()
    if row:
        try:
            return json.loads(row["daten"])
        except Exception:
            pass
    bild = _kartenbild(card_id, lang)
    if not bild:
        return None
    klein = bild.copy()
    klein.thumbnail((1024, 1024))
    modell = _dep["env"]().get("ARTWORK_ANALYSE_MODELL") or ANALYSE_MODELL
    try:
        d = _openrouter({
            "model": modell,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": ANALYSE_PROMPT},
                {"type": "image_url", "image_url": {"url": _data_url(klein, "JPEG")}},
            ]}],
            "response_format": {"type": "json_object"},
            "usage": {"include": True},
        }, timeout=90)
        text = (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        daten = json.loads(text)
        box = daten.get("box")
        if not (isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)):
            daten["box"] = None
        else:
            ymin, xmin, ymax, xmax = [max(0, min(1000, float(v))) for v in box]
            # Plausibilität: Fenster deckt mindestens ein Viertel der Karte und liegt nicht auf dem Kopf
            if xmax - xmin < 300 or ymax - ymin < 200:
                daten["box"] = None
            else:
                daten["box"] = [ymin, xmin, ymax, xmax]
        daten["_kosten"] = float((d.get("usage") or {}).get("cost") or 0)
    except Exception as e:
        return {"fehler": str(e)[:200], "box": None}
    con = get_db()
    con.execute("INSERT OR REPLACE INTO card_art_analysis (card_id, lang, daten, created_at) VALUES (?,?,?,?)",
                (card_id, lang, json.dumps(daten), _now()))
    con.commit()
    con.close()
    return daten


def _illustration(card_id, lang, analyse):
    """→ (Ausschnitt des Illustrationsfensters, Box in Kartenanteilen 0-1) oder (Kartenbild, None)."""
    bild = _kartenbild(card_id, lang)
    if not bild:
        return None, None
    box = (analyse or {}).get("box")
    if not box:
        return bild, None
    ymin, xmin, ymax, xmax = [v / 1000 for v in box]
    ymin, xmin, ymax, xmax = ymin + 0.012, xmin + 0.012, ymax - 0.012, xmax - 0.012
    w, h = bild.size
    crop = bild.crop((round(xmin * w), round(ymin * h), round(xmax * w), round(ymax * h)))
    return crop, (xmin, ymin, xmax, ymax)


def _vorlage(anker, cols, rows, geo, lang, analysen):
    """Seite: NUR die Illustrationsfenster an ihrer echten Position, alles andere grau (= malen).
    Der Kartenrahmen wird bewusst mitgemalt – die echte Karte deckt ihn später wieder ab, sodass die
    Szene an den Kanten des Fensters nahtlos weitergeht."""
    img = Image.new("RGB", (geo["cw"], geo["ch"]), (128, 128, 128))
    fenster = {}
    for slot, card_id in anker.items():
        crop, rel = _illustration(card_id, lang, analysen.get(card_id))
        if not crop:
            continue
        x0, y0, x1, y1 = _fach_box(int(slot), cols, geo)
        if rel:
            bx0 = x0 + round(rel[0] * (x1 - x0)); by0 = y0 + round(rel[1] * (y1 - y0))
            bx1 = x0 + round(rel[2] * (x1 - x0)); by1 = y0 + round(rel[3] * (y1 - y0))
        else:
            bx0, by0, bx1, by1 = x0, y0, x1, y1
        img.paste(crop.resize((max(1, bx1 - bx0), max(1, by1 - by0)), Image.LANCZOS), (bx0, by0))
        fenster[slot] = (bx0, by0, bx1, by1)
    return img, fenster


# --- Pokémon-Wünsche ---------------------------------------------------------------

def _pokemon_aufloesen(namen):
    """Eingegebene Namen (DE/EN/JP) → [{dex, name_en, name_de}] (max. MAX_POKEMON)."""
    out, gesehen = [], set()
    con = _dep["get_db"]()
    for n in namen or []:
        n = str(n).strip()
        if not n:
            continue
        r = con.execute(
            "SELECT dex_id, name_de, name_en FROM pokemon WHERE lower(name_de)=lower(?) OR lower(name_en)=lower(?)"
            " OR name_ja=? LIMIT 1", (n, n, n)).fetchone()
        if not r:
            r = con.execute(
                "SELECT dex_id, name_de, name_en FROM pokemon WHERE lower(name_de) LIKE lower(?) OR lower(name_en) LIKE lower(?)"
                " ORDER BY dex_id LIMIT 1", (n + "%", n + "%")).fetchone()
        if r and r["dex_id"] not in gesehen:
            gesehen.add(r["dex_id"])
            out.append({"dex": r["dex_id"], "name_en": r["name_en"] or r["name_de"], "name_de": r["name_de"]})
        if len(out) >= MAX_POKEMON:
            break
    con.close()
    return out


def _pokemon_bild(dex):
    pfad = _dep["dex_image_path"](dex)
    if not pfad:
        return None
    try:
        img = Image.open(pfad).convert("RGBA")
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img, mask=img.split()[-1])
        bg.thumbnail((512, 512))
        return bg
    except Exception:
        return None


# --- Prompt & Modellaufruf --------------------------------------------------------

def _analyse_text(a):
    if not a or a.get("fehler"):
        return "(no analysis available – study the illustration yourself)"
    teile = []
    for k in ("subject", "scene", "edges", "horizon", "perspective", "light", "palette", "technique", "mood"):
        v = a.get(k)
        if v:
            lbl = "subject (already fully inside the illustration – never repeat it)" if k == "subject" else k
            teile.append(f"{lbl}: {json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v}")
    return "\n".join(teile)


def _regionen(cols, rows, anker, anzahl):
    """Je Wunsch-Pokémon eine feste Region der Seite (in Worten) – eine Ortsangabe verhindert, dass das
    Modell dasselbe Pokémon mehrfach verteilt. Wahl: freies Fach mit größtem Abstand zu den Karten und zu
    schon vergebenen Regionen, bei Gleichstand weiter unten (Boden)."""
    belegt = [(int(s) % cols, int(s) // cols) for s in anker]
    frei = [(s % cols, s // cols) for s in range(cols * rows) if str(s) not in anker]
    gewaehlt, worte = [], []
    for _ in range(anzahl):
        if not frei:
            break
        def score(f):
            d = min([abs(f[0] - b[0]) + abs(f[1] - b[1]) for b in belegt + gewaehlt] or [0])
            return (d, f[1], -abs(f[0] - (cols - 1) / 2))
        best = max(frei, key=score)
        frei.remove(best); gewaehlt.append(best)
        zeile = "upper" if best[1] == 0 else ("lower" if best[1] == rows - 1 else "middle")
        spalte = "left" if best[0] == 0 else ("right" if best[0] == cols - 1 else "center")
        worte.append(f"{zeile} {spalte}" if not (zeile == "middle" and spalte == "center") else "center")
    return worte


def _prompt_teile(cols, rows, anker, stil, wunsch, namen, analysen, vorlage, bilder, pokemon, feedback=""):
    """Interleaved content für das Bildmodell: Outpainting-Auftrag, Vorlage, Illustrations-Ausschnitte,
    Pokémon-Referenzen. Bewusst KEIN Wort über Binder, Fächer oder Raster (→ Gitterlinien) und kein
    „erweitere das Artwork“ (→ Kreatur wird dupliziert). Kurz und konkret hat im Vergleich am besten
    abgeschnitten (Horizont, Licht und Wasserlinien laufen exakt weiter)."""
    mehrere = len(anker) > 1
    kreaturen = [namen[c] for c in dict.fromkeys(anker.values()) if namen.get(c)]
    intro = (
        "OUTPAINTING TASK. IMAGE 1 is a large painting of which only "
        + ("some rectangular parts are" if mehrere else "one rectangular part is")
        + " finished (the source illustration" + ("s" if mehrere else "") + "); every gray pixel is still unpainted. "
        "Paint all gray areas so that the finished part" + ("s extend" if mehrere else " extends")
        + " seamlessly in every direction: the same scene, same perspective and horizon height, same light, same "
        "colors and the same painting technique continue outward without any visible transition – as if the source "
        "were a crop from this bigger painting.\n"
    )
    teile = [{"type": "text", "text": intro}, {"type": "image_url", "image_url": {"url": _data_url(vorlage)}}]
    n = 2
    for slot, card_id in sorted(anker.items(), key=lambda kv: int(kv[0])):
        crop = bilder.get(card_id)
        if crop is None:
            continue
        col, row = int(slot) % cols, int(slot) // cols
        wo = f" (the one at row {row + 1}, column {col + 1} of IMAGE 1)" if mehrere else ""
        teile.append({"type": "text", "text": (
            f"IMAGE {n} – the source illustration{wo} in close-up, for reference of details, technique and colors. "
            f"Its creature is {namen.get(card_id) or 'the main creature'}.\n{_analyse_text(analysen.get(card_id))}")})
        teile.append({"type": "image_url", "image_url": {"url": _data_url(crop, 'JPEG')}})
        n += 1
    for p in pokemon:
        if p.get("_bild") is None:
            continue
        teile.append({"type": "text", "text": (
            f"IMAGE {n} – reference for the anatomy and colors of {p['name_en']} ONLY. Do not copy this picture: "
            "no white background, no cut-out look, not this pose.")})
        teile.append({"type": "image_url", "image_url": {"url": _data_url(p["_bild"], 'JPEG')}})
        n += 1
    erlaubt = ", ".join(p["name_en"] for p in pokemon) if pokemon else "none"
    regeln = (
        "\nRules:\n"
        f"- {' and '.join(kreaturen) if kreaturen else 'The creature of the source illustration'} appear"
        f"{'s' if len(kreaturen) <= 1 else ''} nowhere outside the finished part"
        + ("s" if mehrere else "") + " – not again, not partially, not as shadow, reflection or silhouette. "
        f"Creatures allowed in the new areas: {erlaubt}. Everything else outside is only the world the scene lives in.\n"
        "- Continue the surroundings: what is cut off at the edges of the finished part continues exactly there, at the "
        "same height and angle; then more of the same landscape / sky / water / ground, atmosphere and depth. Keep it "
        "calm – the source stays the most detailed and most important area.\n"
        + ("- Several finished parts: they belong to ONE world with a single horizon, light and perspective; paint "
           "believable transitions between them.\n" if mehrere else "")
        + "- One continuous painting: no frames, borders, lines, panels, tiles, text, letters, logos, watermarks. "
        "Not a single gray pixel may remain.\n"
        "- Do not change the finished part" + ("s" if mehrere else "") + ".\n"
        f"- Technique for the new areas: {STILE.get(stil, STILE['karte'])} This only changes how it is painted; what "
        "is painted stays the continuation of the source scene.\n"
    )
    if pokemon:
        regionen = _regionen(cols, rows, anker, len(pokemon))
        plaetze = "; ".join(f"ONE single {p['name_en']} in the {regionen[i] if i < len(regionen) else 'free'} "
                            "region of the painting" for i, p in enumerate(pokemon))
        regeln += ("- Add " + plaetze + " – and nowhere else; the painting contains exactly one of each. Paint it "
                   "as a real inhabitant of this scene: in exactly the same technique, lit by the scene's light "
                   "with cast shadows and reflections, standing / sitting / swimming / flying ON or IN the "
                   "environment (feet on the ground, splashing water, wind in fur), partly overlapped by foreground "
                   "elements where natural, seen from the scene's perspective, doing something that fits the moment "
                   "(watching the source creature, playing, resting). Never a floating cut-out.\n")
    if wunsch:
        regeln += f"- Wishes from the collector: {wunsch.strip()[:400]}\n"
    if feedback:
        regeln += ("\nA previous attempt was rejected by a reviewer for these problems – avoid them this time: "
                   + feedback + "\n")
    regeln += "Output exactly the same dimensions as IMAGE 1."
    teile.append({"type": "text", "text": regeln})
    return teile


# --- Kontrolle (Vision-Modell prüft die Fortsetzung an den Kanten) ---------------------------------

PRUEF_PROMPT = (
    "You are a strict art director checking an 'extended art' painting. IMAGE 1 is the source: the illustration of "
    "a trading card (it may include the card's frame, name, text boxes and symbols – that is expected and NOT a "
    "problem; a real card will cover that area later). IMAGE 2 is a larger painting into which the source was "
    "embedded (at its center); everything around it was painted to continue the source's SCENE. Judge only the "
    "painted surroundings, never the card itself, its frame, its text or the straight edges of the card rectangle.\n"
    "Check strictly:\n"
    "1. At every edge of the embedded source, do the background elements continue consistently – same lines, angles "
    "and heights (e.g. a pool edge, horizon, wall, railing, shoreline, beam of light continuing exactly where it "
    "leaves the source)? Name every element that breaks, bends, jumps or ends abruptly.\n"
    "2. Is the source's creature painted again anywhere outside the source (any size, part, shadow, reflection)?\n"
    "3. Are there frames, borders, straight seams, tiles, panels, text, or gray areas?\n"
    "4. Does the extension keep perspective, light direction, palette and painting technique of the source?\n"
    'Answer with JSON only: {"ok": true|false, "probleme": ["concrete problem with location", ...]}. '
    "ok is true unless the surroundings clearly fail to continue the scene (minor softness, the card's own frame, "
    "text or rectangular edge are never problems)."
)


def _pruefen(quelle: Image.Image, ergebnis: Image.Image):
    """→ (ok, probleme) – bei Modellfehlern gilt das Bild als ok (kein Retry auf Verdacht)."""
    try:
        q = quelle.copy(); q.thumbnail((768, 768))
        e = ergebnis.copy(); e.thumbnail((1024, 1024))
        d = _openrouter({
            "model": _dep["env"]().get("ARTWORK_ANALYSE_MODELL") or ANALYSE_MODELL,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": PRUEF_PROMPT},
                {"type": "text", "text": "IMAGE 1 – source:"},
                {"type": "image_url", "image_url": {"url": _data_url(q, "JPEG")}},
                {"type": "text", "text": "IMAGE 2 – extended painting:"},
                {"type": "image_url", "image_url": {"url": _data_url(e, "JPEG")}},
            ]}],
            "response_format": {"type": "json_object"},
            "usage": {"include": True},
        }, timeout=90)
        text = (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        j = json.loads(text)
        probleme = [str(x)[:200] for x in (j.get("probleme") or [])][:6]
        return bool(j.get("ok")) or not probleme, probleme, float((d.get("usage") or {}).get("cost") or 0)
    except Exception:
        return True, [], 0.0


# --- Stufe C: Wunsch-Pokémon als Bearbeitung des fertigen Bilds ------------------------------------

def _pokemon_teile(seite_canvas: Image.Image, pokemon, regionen, stil, namen_kreaturen):
    intro = (
        "EDIT TASK on a finished painting (IMAGE 1). Keep the painting exactly as it is – same composition, colors, "
        "light and every existing element – and add only what is listed below. Gray margins, if any, are outside "
        "the painting: simply extend the painting into them.\n"
    )
    teile = [{"type": "text", "text": intro}, {"type": "image_url", "image_url": {"url": _data_url(seite_canvas)}}]
    n = 2
    for p in pokemon:
        if p.get("_bild") is None:
            continue
        teile.append({"type": "text", "text": (
            f"IMAGE {n} – reference for the anatomy and colors of {p['name_en']} ONLY. Do not copy this picture: "
            "no white background, no cut-out look, not this pose.")})
        teile.append({"type": "image_url", "image_url": {"url": _data_url(p["_bild"], 'JPEG')}})
        n += 1
    plaetze = "; ".join(f"exactly ONE {p['name_en']} in the {regionen[i] if i < len(regionen) else 'free'} region"
                        for i, p in enumerate(pokemon))
    regeln = (
        f"\nAdd: {plaetze} – nowhere else; the painting must contain exactly one of each and no other new creature. "
        + (f"Do not add or alter {' / '.join(namen_kreaturen)}. " if namen_kreaturen else "")
        + "Paint each added Pokémon as a real inhabitant of this scene: in exactly the same painting technique "
        f"({STILE.get(stil, STILE['karte'])}), at a plausible size for its distance, lit by the scene's light with "
        "cast shadow and reflection, standing / sitting / swimming / flying ON or IN the environment (feet on the "
        "ground, splashing water, wind in fur), partly overlapped by foreground elements where natural, seen from "
        "the scene's perspective, doing something that fits the moment. Never a floating cut-out.\n"
        "Do not add text, frames, borders or lines. Output exactly the same dimensions as IMAGE 1."
    )
    teile.append({"type": "text", "text": regeln})
    return teile


# --- Geometrie-Helfer für die Stufen ---------------------------------------------------------------

def _canvas_fuer(w, h, groesse):
    """Leinwand im nächstgelegenen Modell-Seitenverhältnis für eine Region w×h → (cw, ch, skala, box)."""
    ar = _verhaeltnis_waehlen(w, h)
    r = SEITENVERHAELTNISSE[ar]
    lang = LANGE_SEITE.get(groesse, 2048)
    cw, ch = (lang, round(lang / r)) if r >= 1 else (round(lang * r), lang)
    skala = min((cw - 16) / w, (ch - 16) / h)
    sw, sh = round(w * skala), round(h * skala)
    ox, oy = (cw - sw) // 2, (ch - sh) // 2
    return {"ar": ar, "cw": cw, "ch": ch, "skala": skala, "seite": (ox, oy, ox + sw, oy + sh)}


def _farben_angleichen(teil: Image.Image, referenz: Image.Image):
    """Mittelwert/Streuung je Kanal von teil an referenz angleichen (Reinhard-Transfer in RGB) – Stufe B malt
    dieselbe Region oft etwas heller/kühler; ohne Angleich bleibt beim Zurücksetzen von Stufe A ein Rechteck."""
    from PIL import ImageStat
    teil = teil.convert("RGB"); referenz = referenz.convert("RGB").resize(teil.size)
    st, sr = ImageStat.Stat(teil), ImageStat.Stat(referenz)
    kanaele = []
    for i, band in enumerate(teil.split()):
        m_t, s_t = st.mean[i], max(st.stddev[i], 1e-3)
        m_r, s_r = sr.mean[i], sr.stddev[i]
        faktor = max(0.6, min(1.6, s_r / s_t))
        # nur zur Hälfte angleichen – A behält Charakter, B-Ton bestimmt die Richtung
        f = 1 + (faktor - 1) * 0.5
        off = (m_r - m_t) * 0.5
        kanaele.append(band.point(lambda v, f=f, m=m_t, o=off: max(0, min(255, round((v - m) * f + m + o)))))
    return Image.merge("RGB", kanaele)


def _weich_einsetzen(ziel: Image.Image, teil: Image.Image, box, rand):
    """Teilbild mit weich auslaufendem Rand in ziel einsetzen (verdeckt die Naht zwischen den Stufen)."""
    from PIL import ImageFilter
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return
    teil = teil.resize((w, h), Image.LANCZOS)
    maske = Image.new("L", (w, h), 0)
    innen = max(1, rand)
    maske.paste(255, (innen, innen, w - innen, h - innen))
    maske = maske.filter(ImageFilter.GaussianBlur(max(1, rand / 2)))
    ziel.paste(teil, (x0, y0), maske)


def kachel(artwork_id, slot):
    """Ausschnitt eines Fachs (RGB) aus der fertigen Seite – für Binder-Ansicht und Platzhalter-PDF."""
    con = _dep["get_db"]()
    row = con.execute("SELECT layout, status FROM artworks WHERE id = ?", (artwork_id,)).fetchone()
    con.close()
    if not row or row["status"] != "fertig":
        return None
    pfad = _artwork_dir() / f"{artwork_id}.png"
    if not pfad.exists():
        return None
    cols, rows = [int(v) for v in row["layout"].split("x")]
    if slot < 0 or slot >= cols * rows:
        return None
    seite = Image.open(pfad).convert("RGB")
    pw, ph = _seite_mm(cols, rows)
    sx, sy = seite.width / pw, seite.height / ph
    fx, fy = _fach_mm(slot, cols)
    return seite.crop((round(fx * sx), round(fy * sy), round((fx + KARTE_W) * sx), round((fy + KARTE_H) * sy)))


def kachel_reader(artwork_id, slot):
    """ReportLab-ImageReader für ein Artwork-Fach (JPEG), None wenn nicht verfügbar."""
    try:
        tile = kachel(artwork_id, int(slot))
    except Exception:
        return None
    if tile is None:
        return None
    buf = io.BytesIO()
    tile.save(buf, "JPEG", quality=90)
    buf.seek(0)
    return ImageReader(buf)


def _modell_aufruf(teile, modell, ar, groesse):
    d = _openrouter({
        "model": modell,
        "messages": [{"role": "user", "content": teile}],
        "modalities": ["image", "text"],
        "image_config": {"aspect_ratio": ar, "image_size": groesse},
        "usage": {"include": True},
    })
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
        groesse = row["groesse"]
        modell = row["modell"]
        geo = _geometrie(cols, rows, groesse)
        lang = row["sprache"] or "de"
        stil, wunsch = row["stil"], row["wunsch"] or ""
        schritte = []
        # 1. Analyse je Karte (gecacht) + Illustrations-Ausschnitte
        analysen, bilder, namen, kosten = {}, {}, {}, 0.0
        con = get_db()
        for cid in dict.fromkeys(anker.values()):
            r = con.execute("SELECT name_de, name_en FROM cards WHERE id = ?", (cid,)).fetchone()
            if r:
                namen[cid] = r["name_en"] or r["name_de"]
            a = _analyse(cid, lang)
            analysen[cid] = a
            kosten += float((a or {}).get("_kosten") or 0)
            crop, _ = _illustration(cid, lang, a)
            if crop is not None:
                crop = crop.copy(); crop.thumbnail((1024, 1024))
                bilder[cid] = crop
        con.close()
        # Vorlage der ganzen Seite (nur Illustrationsfenster) + Fensterpositionen in Seitenkoordinaten
        vorlage, fenster = _vorlage(anker, cols, rows, geo, lang, analysen)
        px0, py0, px1, py1 = geo["seite"]
        sw, sh = px1 - px0, py1 - py0
        fenster_seite = {k: (v[0] - px0, v[1] - py0, v[2] - px0, v[3] - py0) for k, v in fenster.items()}

        # 2. Stufe A: kleiner Ring um die Illustration(en) – genaue Geometrie an den Kanten
        stufe_a = None
        if fenster_seite:
            ux0 = min(v[0] for v in fenster_seite.values()); uy0 = min(v[1] for v in fenster_seite.values())
            ux1 = max(v[2] for v in fenster_seite.values()); uy1 = max(v[3] for v in fenster_seite.values())
            cx, cy = (ux0 + ux1) / 2, (uy0 + uy1) / 2
            hw, hh = (ux1 - ux0) / 2 * STUFE_A_FAKTOR, (uy1 - uy0) / 2 * STUFE_A_FAKTOR
            R = (max(0, round(cx - hw)), max(0, round(cy - hh)), min(sw, round(cx + hw)), min(sh, round(cy + hh)))
            rw, rh = R[2] - R[0], R[3] - R[1]
            if rw > 0 and rh > 0 and (rw * rh) / (sw * sh) < STUFE_A_MAX_ANTEIL:
                geo_a = _canvas_fuer(rw, rh, groesse)
                ax0, ay0 = geo_a["seite"][0], geo_a["seite"][1]
                k = geo_a["skala"]
                canvas_a = Image.new("RGB", (geo_a["cw"], geo_a["ch"]), (128, 128, 128))
                for slot, box in fenster_seite.items():
                    crop = bilder.get(anker[slot])
                    if crop is None:
                        continue
                    bx0 = ax0 + round((box[0] - R[0]) * k); by0 = ay0 + round((box[1] - R[1]) * k)
                    bx1 = ax0 + round((box[2] - R[0]) * k); by1 = ay0 + round((box[3] - R[1]) * k)
                    canvas_a.paste(crop.resize((max(1, bx1 - bx0), max(1, by1 - by0)), Image.LANCZOS), (bx0, by0))
                feedback = ""
                quelle = next(iter(bilder.values()))
                for versuch in range(2):
                    teile = _prompt_teile(cols, rows, anker, stil, wunsch, namen, analysen, canvas_a, bilder, [], feedback)
                    erg, kk, modell_a = _modell_aufruf(teile, modell, geo_a["ar"], groesse)
                    kosten += kk
                    if erg.size != (geo_a["cw"], geo_a["ch"]):
                        erg = erg.resize((geo_a["cw"], geo_a["ch"]), Image.LANCZOS)
                    stufe_a = erg.crop(geo_a["seite"]).resize((rw, rh), Image.LANCZOS)
                    ok, probleme, kp = _pruefen(quelle, stufe_a)
                    kosten += kp
                    schritte.append({"stufe": "A", "versuch": versuch + 1, "ok": ok, "probleme": probleme})
                    if ok or versuch == 1:
                        break
                    feedback = "; ".join(probleme)
                stufe_a_box = R

        # 3. Stufe B: ganze Seite – Vorlage enthält das Ergebnis von Stufe A (oder nur die Fenster)
        canvas_b = vorlage
        if stufe_a is not None:
            canvas_b = vorlage.copy()
            canvas_b.paste(stufe_a, (px0 + stufe_a_box[0], py0 + stufe_a_box[1]))
            for slot, box in fenster.items():   # Fenster pixelgenau zurück
                crop = bilder.get(anker[slot])
                if crop is not None:
                    canvas_b.paste(crop.resize((box[2] - box[0], box[3] - box[1]), Image.LANCZOS), (box[0], box[1]))
        teile = _prompt_teile(cols, rows, anker, stil, wunsch, namen, analysen, canvas_b, bilder, [])
        erg, kk, modell_b = _modell_aufruf(teile, modell, geo["ar"], groesse)
        kosten += kk
        schritte.append({"stufe": "B"})
        if erg.size != (geo["cw"], geo["ch"]):
            erg = erg.resize((geo["cw"], geo["ch"]), Image.LANCZOS)
        seite = erg.crop(geo["seite"])
        if stufe_a is not None:   # Stufe A weich zurücksetzen – sie ist die geometrisch genauere Fassung
            referenz = seite.crop(stufe_a_box)
            angeglichen = _farben_angleichen(stufe_a, referenz)
            _weich_einsetzen(seite, angeglichen, stufe_a_box,
                             rand=max(24, round(min(stufe_a_box[2] - stufe_a_box[0], stufe_a_box[3] - stufe_a_box[1]) * 0.14)))

        # 4. Stufe C: Wunsch-Pokémon als Bearbeitung des fertigen Bilds (integriert sich besser als beim Malen ins Leere)
        pokemon = json.loads(row["pokemon"] or "[]")
        if pokemon:
            for p_ in pokemon:
                p_["_bild"] = _pokemon_bild(p_["dex"])
            _karten_einsetzen(seite, anker, cols, geo, lang, offset=(px0, py0))
            canvas_c = Image.new("RGB", (geo["cw"], geo["ch"]), (128, 128, 128))
            canvas_c.paste(seite, (px0, py0))
            regionen = _regionen(cols, rows, anker, len(pokemon))
            teile = _pokemon_teile(canvas_c, pokemon, regionen, stil, list(namen.values()))
            erg, kk, _ = _modell_aufruf(teile, modell, geo["ar"], groesse)
            kosten += kk
            schritte.append({"stufe": "C", "pokemon": [p_["name_en"] for p_ in pokemon], "regionen": regionen})
            if erg.size != (geo["cw"], geo["ch"]):
                erg = erg.resize((geo["cw"], geo["ch"]), Image.LANCZOS)
            seite = erg.crop(geo["seite"])

        # 5. Echte Kartenscans pixelgenau darüber, speichern
        _karten_einsetzen(seite, anker, cols, geo, lang, offset=(px0, py0))
        d = _artwork_dir()
        seite.save(d / f"{artwork_id}.png", "PNG", optimize=True)
        vorschau = seite.copy()
        vorschau.thumbnail((900, 900), Image.LANCZOS)
        vorschau.save(d / f"{artwork_id}.vorschau.webp", "WEBP", quality=82)
        con = get_db()
        con.execute("UPDATE artworks SET status='fertig', modell=?, kosten_usd=?, breite=?, hoehe=?, fertig_at=?, schritte=?"
                    " WHERE id=?", (modell_b, kosten, seite.width, seite.height, _now(), json.dumps(schritte), artwork_id))
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
    try:
        pokemon = [{"dex": p["dex"], "name_de": p.get("name_de"), "name_en": p.get("name_en")}
                   for p in json.loads(row.get("pokemon") or "[]")]
    except Exception:
        pokemon = []
    return {
        "id": row["id"], "binder_id": row["binder_id"], "seite": row["seite"], "layout": row["layout"],
        "anker": json.loads(row["anker"] or "{}"), "stil": row["stil"], "wunsch": row["wunsch"] or "",
        "pokemon": pokemon,
        "status": row["status"], "fehler": row["fehler"], "breite": row["breite"], "hoehe": row["hoehe"],
        "created_at": row["created_at"], "modell": row["modell"],
        "schritte": json.loads(row["schritte"]) if row.get("schritte") else [],
        "vorschau": f"api/artwork/{row['id']}/bild?v=vorschau" if row["status"] == "fertig" else None,
    }


def register(app, *, get_db, current_user, require_user, ist_pro, load_binder, card_image_path,
             dex_image_path, pdf_wasserzeichen, env, CACHE):
    _dep.update(get_db=get_db, current_user=current_user, require_user=require_user, ist_pro=ist_pro,
                load_binder=load_binder, card_image_path=card_image_path, dex_image_path=dex_image_path,
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
        CREATE TABLE IF NOT EXISTS card_art_analysis (
            card_id TEXT, lang TEXT, daten TEXT, created_at TEXT, PRIMARY KEY (card_id, lang)
        );
        """
    )
    for alter in ("ALTER TABLE users ADD COLUMN artwork_monat TEXT",
                  "ALTER TABLE users ADD COLUMN artwork_gesamt INTEGER DEFAULT 0",
                  "ALTER TABLE artworks ADD COLUMN pokemon TEXT",
                  "ALTER TABLE artworks ADD COLUMN schritte TEXT"):
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
                "aktiv": bool(env().get("OPENROUTER_KEY")), "max_pokemon": MAX_POKEMON}

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
        pokemon = _pokemon_aufloesen(data.get("pokemon") if isinstance(data.get("pokemon"), list) else [])
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
                "INSERT INTO artworks (id,user_id,binder_id,seite,layout,anker,stil,wunsch,pokemon,sprache,modell,groesse,status)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'laeuft')",
                (artwork_id, user["id"], binder["id"], seite, layout, json.dumps(anker), stil, wunsch,
                 json.dumps(pokemon), sprache,
                 e.get("ARTWORK_MODELL") or STANDARD_MODELL, e.get("ARTWORK_GROESSE") or STANDARD_GROESSE),
            )
            con.commit()
            con.close()
            _kontingent_buchen(user, +1)
        threading.Thread(target=_job, args=(artwork_id,), daemon=True).start()
        return {"id": artwork_id, "status": "laeuft", "pokemon": pokemon}

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
        # Vorschau ist ohne Konto abrufbar (unratbare ID) – sie steckt als Fach-Kachel in geteilten Binder-Ansichten
        if v == "vorschau":
            con = get_db()
            r = con.execute("SELECT * FROM artworks WHERE id = ?", (artwork_id,)).fetchone()
            con.close()
            if not r:
                raise HTTPException(404, "Artwork nicht gefunden")
            row = dict(r)
        else:
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
