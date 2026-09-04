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

# Abgerechnet wird in Credits (siehe abo.artwork_preis) – gestaffelt nach Ankerkarten,
# weil jede weitere Karte eine Analyse und ein weiteres Bild im Prompt bedeutet.
MAX_POKEMON = 3

# Notbremse: Tagesdeckel für Modellkosten über alle Nutzer. Schützt vor Ausreißern und
# davor, dass ein Fehler in einer Schleife das OpenRouter-Guthaben leerräumt.
TAGESLIMIT_USD = 25.0

STANDARD_MODELL = "google/gemini-3.1-flash-image"     # Nano Banana 2 – Bild rein, Bild raus
STANDARD_GROESSE = "2K"
# Modus: "schnell" = Analyse + ein Malschritt (~0,12 $), "stufen" = Ring → Kontrolle → Seite → Pokémon-Edit
# (~0,33–0,46 $). Die Stufen brachten im Vergleich keinen sichtbaren Mehrwert (28.08.) – Standard ist "schnell";
# per ARTWORK_MODUS=stufen in .env umschaltbar.
STANDARD_MODUS = "schnell"
STUFE_A_FAKTOR = 2.3      # Stufe A: Illustration um diesen Faktor erweitern (kleiner Schritt = genaue Geometrie)
STUFE_A_MAX_ANTEIL = 0.8  # deckt Stufe A schon ≥ 80 % der Seite, entfällt Stufe B
ANALYSE_MODELL = "google/gemini-3.5-flash"            # Vision-Analyse der Karte (≈ 0,5 ct)
# Der Wunschtext ist reines Umschreiben — dafür genügt das kleine Modell. Gemessen an vier
# echten Wünschen: 2.5-flash 0,009 ct und behält die Fortsetzungs-Formulierung („extend the
# forest …"), 3.5-flash kostet 0,7 ct für dasselbe, flash-lite verliert den Bezug zur Karte.
WUNSCH_MODELL = "google/gemini-2.5-flash"

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


# --- Credits & Kostendeckel ---------------------------------------------------

def _preis(anker_anzahl, groesse):
    return _dep["abo"].artwork_preis(anker_anzahl, groesse)


def _tageskosten():
    con = _dep["get_db"]()
    wert = con.execute("SELECT COALESCE(SUM(kosten_usd),0) s FROM artworks"
                       " WHERE created_at >= date('now')").fetchone()["s"]
    con.close()
    return float(wert or 0)


class ModellBezahlt(Exception):
    """Das Bildmodell hat geantwortet (und damit Kosten verursacht), aber kein Bild
    geliefert – etwa wegen eines Inhaltsfilters. Solche Läufe werden nicht erstattet,
    sonst ließen sich über den Wunsch-Text beliebig Kosten erzeugen."""


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
    '"aufdrucke": true if the artwork area carries printed card elements on top of it – name plate, HP, attack '
    "text box, energy or set symbols, rarity mark, illustrator credit, holo pattern overlay; false if the "
    "artwork box is clean illustration only,\n"
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


# Der Maßstab ist die häufigste Ursache dafür, dass eine Seite auseinanderfällt: die Karte zeigt
# zwei Meter Dschungelboden, das Bildmodell malt drumherum dreißig Meter Landschaft, weil ihm
# niemand sagt, wie groß die abgebildete Welt ist. Gemessen an den 151er-Karten: Bisasam 2,2 m,
# Bisaknosp 2,5 m, Bisaflor 4,5 m — die gemalten Blätter waren fünf- bis zehnmal zu groß.
# Eigener kleiner Aufruf (≈ 0,8 ct) statt einer neuen Vollanalyse, damit die 23.000 schon
# analysierten Karten ihren Eintrag behalten; das Ergebnis wird in dieselbe Zeile gemischt.
MASS_PROMPT = (
    "Look at the illustration of this Pokémon trading card. Estimate the REAL-WORLD scale of the "
    "depicted scene. Answer JSON only:\n"
    '{"span_m": how many metres of the world the illustration spans horizontally (a number),\n'
    '"creature_m": the real height of the depicted creature in metres (a number),\n'
    '"camera_m": how far the viewer stands from the subject, in metres (a number),\n'
    '"anchor": ONE sentence naming two ordinary objects in the picture with their real size, so that '
    'a painter can match the scale (e.g. "the round leaves in the foreground are about 25 cm across, '
    'the tree trunk on the left is about 40 cm thick")}'
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


def _massstab(card_id, lang, analyse):
    """Maßstabsfelder nachtragen, falls die gecachte Analyse sie noch nicht hat. → (analyse, kosten)

    Läuft je Karte genau einmal; danach steht der Maßstab in derselben Zeile wie die übrige
    Analyse. Schlägt der Aufruf fehl, bleibt die Analyse ohne Maßstab und der Auftrag lässt den
    Block einfach weg — lieber keine Angabe als eine erfundene."""
    if not analyse or analyse.get("fehler") or analyse.get("span_m") is not None:
        return analyse, 0.0
    bild = _kartenbild(card_id, lang)
    if not bild:
        return analyse, 0.0
    klein = bild.copy()
    klein.thumbnail((1024, 1024))
    try:
        d = _openrouter({
            "model": _dep["env"]().get("ARTWORK_ANALYSE_MODELL") or ANALYSE_MODELL,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": MASS_PROMPT},
                {"type": "image_url", "image_url": {"url": _data_url(klein, "JPEG")}},
            ]}],
            "response_format": {"type": "json_object"},
            "usage": {"include": True},
        }, timeout=90)
        text = (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        mass = json.loads(text)
    except Exception:
        return analyse, 0.0
    for k in ("span_m", "creature_m", "camera_m"):
        try:
            v = float(mass.get(k))
        except (TypeError, ValueError):
            v = None
        analyse[k] = v if (v and 0 < v < 100000) else None
    analyse["anchor"] = str(mass.get("anchor") or "")[:300]
    kosten = float((d.get("usage") or {}).get("cost") or 0)
    con = _dep["get_db"]()
    con.execute("INSERT OR REPLACE INTO card_art_analysis (card_id, lang, daten, created_at) VALUES (?,?,?,?)",
                (card_id, lang, json.dumps(analyse), _now()))
    con.commit()
    con.close()
    return analyse, kosten


def _tiefen(anker, cols, namen, analysen):
    """Die Karten nach Bildausschnitt in Tiefenebenen ordnen. → Textblock oder ""

    Ein unterschiedlicher Ausschnitt ist kein Widerspruch, sondern Tiefe: Wer 5 m Dschungel
    zeigt, steht weiter hinten als wer 1,8 m zeigt. Statt einen einheitlichen Maßstab zu
    erzwingen — was die Seite flach macht — bekommt jede Karte ihre Ebene im Raum, und die
    gemalten Flächen dazwischen sind der Weg von vorne nach hinten. Liegt die weiteste Karte
    ohnehin oben, stimmt die Staffelung mit der Anordnung überein und das Bild trägt sich
    von selbst."""
    mit = [(s, c) for s, c in anker.items() if (analysen.get(c) or {}).get("span_m")]
    if len(mit) < 2:
        return ""
    nach_weite = sorted(mit, key=lambda sc: analysen[sc[1]]["span_m"])
    stufen = ["the FOREGROUND", "the MIDDLE distance", "the BACKGROUND"]
    zeilen = []
    for i, (slot, cid) in enumerate(nach_weite):
        a = analysen[cid]
        if len(nach_weite) == 2:
            wo = stufen[0] if i == 0 else stufen[2]
        else:
            wo = stufen[min(i * len(stufen) // len(nach_weite), 2)]
        zeile = int(slot) // cols + 1
        zeilen.append(
            f"- The finished rectangle at row {zeile} (the one showing {namen.get(cid) or cid}) is a "
            f"WINDOW into {wo} of the scene: it spans about {a['span_m']:g} m of the world, seen from "
            f"about {a.get('camera_m') or '?'} m away. Paint the areas around that window at that depth."
            + (f" {a['anchor']}" if a.get("anchor") else ""))
    eng = analysen[nach_weite[0][1]]["span_m"]
    weit = analysen[nach_weite[-1][1]]["span_m"]
    return (
        "\nONE SPACE, STAGED IN DEPTH – this is what makes the page work:\n"
        "The finished rectangles are windows into the same place, seen from different distances. That "
        "is not a problem: it is the depth of one single scene. A window showing more of the world "
        "looks further back; a window showing less looks closer to us. The creatures inside the "
        "windows stay exactly where they are — only the areas AROUND each window take its depth.\n"
        + "\n".join(zeilen) + "\n"
        f"Build ONE continuous space from front to back around them: near the foreground source paint "
        f"large, close objects at its scale (it shows about {eng:g} m across); as the eye travels towards "
        f"the background source, the same kinds of objects get smaller, softer and hazier until they match "
        f"its scale (about {weit:g} m across). The ground, water, canopy and light run through all of it "
        "without a break, so one can walk from the nearest source to the furthest.\n"
        "Do NOT paint every area at the same size, and do NOT zoom the camera out beyond the furthest "
        "source: match each area to the depth it sits in. Compare every object you paint with the objects "
        "in the nearest source and place it correctly in front of or behind them.\n")


def _mass_text(namen, analysen):
    """Der Maßstabsblock für den Bildauftrag – oder \"\", wenn keine Karte eine Angabe hat."""
    zeilen = []
    for cid, a in analysen.items():
        if not a or not a.get("span_m"):
            continue
        name = namen.get(cid) or "the source"
        t = f"- {name}: its illustration spans about {a['span_m']:g} m of the world"
        if a.get("creature_m"):
            t += f"; {name} is about {a['creature_m']:g} m tall"
        if a.get("camera_m"):
            t += f"; the viewer stands about {a['camera_m']:g} m away"
        if a.get("anchor"):
            t += f". {a['anchor']}"
        zeilen.append(t)
    if not zeilen:
        return ""
    return (
        "\nSCALE – the most common failure, read this carefully:\n" + "\n".join(zeilen) + "\n"
        "The areas you paint show the SAME world seen from the SAME distance. Every leaf, flower, stone, "
        "root, branch or trunk you paint must have the same real-world size as objects of that kind inside "
        "the finished part"
        + ("s" if len(analysen) > 1 else "") + ", and must therefore be drawn at the same size on the page. "
        "Do NOT zoom out: no giant tree trunks, no wide valley vista, no distant canopy, no aerial view. The "
        "camera does not move; we simply see more of the same close view. Before painting an object, compare "
        "it with the objects at the nearest edge of a finished part and match their size.\n")


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
            lbl = ("subject (this creature stays INSIDE the illustration; outside it does not exist – "
                   "paint only the scene it stands in)") if k == "subject" else k
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


RICHTUNG = {(0, -1): ("above", "top"), (0, 1): ("below", "bottom"),
            (-1, 0): ("left of", "left"), (1, 0): ("right of", "right")}


def _freiflaechen(cols, rows, anker):
    """Die freien Fächer in zusammenhängende Stücke zerlegen. → [[slot, …], …], größtes zuerst."""
    belegt = {int(s) for s in anker}
    frei = [s for s in range(cols * rows) if s not in belegt]
    gesehen, stuecke = set(), []
    for start in frei:
        if start in gesehen:
            continue
        stapel, teil = [start], []
        while stapel:
            k = stapel.pop()
            if k in gesehen:
                continue
            gesehen.add(k); teil.append(k)
            x, y = k % cols, k // cols
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                n = ny * cols + nx
                if 0 <= nx < cols and 0 <= ny < rows and n in frei and n not in gesehen:
                    stapel.append(n)
        stuecke.append(sorted(teil))
    return sorted(stuecke, key=len, reverse=True)


def _layout_text(cols, rows, anker, namen):
    """Die Seite als Text: welches freie Fach grenzt an welche Karte, und an welche ihrer Kanten.

    Diese Auswertung fehlte bisher ganz. Jede Karte wurde einzeln sehr genau analysiert, die Seite
    als Ganzes gar nicht — der Planer musste raten, wo etwas hingehört. Gemessen an den gelungenen
    Seiten ist genau das der Unterschied: dort benannte der Plan eine Struktur und einen Inhalt je
    Fläche, bei den misslungenen stand „mehr Dschungel"."""
    zeilen = []
    for stueck in _freiflaechen(cols, rows, anker):
        for slot in stueck:
            x, y = slot % cols, slot // cols
            nachbarn = []
            for (dx, dy), (wort, kante) in RICHTUNG.items():
                nx, ny = x + dx, y + dy
                n = ny * cols + nx
                if 0 <= nx < cols and 0 <= ny < rows and str(n) in anker:
                    cid = anker[str(n)]
                    # Liegt die Karte rechts vom freien Fach, setzt das Fach ihre LINKE Kante fort.
                    gegen = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}[kante]
                    nachbarn.append(f"{namen.get(cid) or cid} is {wort} it, so this area continues "
                                    f"that source's {gegen} edge")
            zeilen.append(f"- row {y + 1} / column {x + 1}: "
                          + ("; ".join(nachbarn) if nachbarn else "touches no source directly"))
    return "\n".join(zeilen)


def _zonen(cols, rows, anker):
    """Jedes freie Fach der nächstgelegenen Karte zuordnen → {slot: card_id} + Nachbarpaare der Zonen.
    Bei mehreren Karten scheitert „eine gemeinsame Szene“; jede Karte bekommt stattdessen ihre eigene
    Umgebung, und nur an den Zonengrenzen wird überblendet."""
    karten = [(int(sl), cid) for sl, cid in anker.items()]
    zone = {}
    for slot in range(cols * rows):
        if str(slot) in anker:
            zone[slot] = anker[str(slot)]
            continue
        x, y = slot % cols, slot // cols
        zone[slot] = min(karten, key=lambda k: ((x - k[0] % cols) ** 2 + (y - k[0] // cols) ** 2, k[0]))[1]
    paare = set()
    for slot in range(cols * rows):
        x, y = slot % cols, slot // cols
        for nx, ny in ((x + 1, y), (x, y + 1)):
            if nx < cols and ny < rows:
                a, b = zone[slot], zone[ny * cols + nx]
                if a != b:
                    paare.add(tuple(sorted((a, b))))
    return zone, sorted(paare)


# --- Passen die Karten überhaupt zusammen? ----------------------------------
#
# Eine Seite aus einer Unterwasserkarte und einer Vulkankarte kann das beste Bildmodell
# nicht zu einer Landschaft verbinden — es wird ein Bruch oder ein Brei. Das lässt sich
# vorher sagen: `card_art_tags` hält für 23.461 Karten Ort, Tageszeit und Wasseranteil
# (aus dem Bildmotiv-Index, siehe themen.py). Der Nutzer erfährt es, bevor er Credits
# ausgibt, und der Regie-Plan bekommt den gemeinsamen Lebensraum als Vorgabe.
ORT_GRUPPEN = {
    "wasser": {"unterwasser", "gewaesser", "strand", "fluss"},
    "gruen": {"wald", "dschungel", "wiese"},
    "karg": {"berge", "wueste", "vulkan", "hoehle", "unterirdisch", "ruinen"},
    "kalt": {"schnee"},
    "gebaut": {"stadt", "gebaeude", "innenraum", "technik"},
    "himmel": {"himmel", "weltraum"},
}
ORT_TEXT = {
    "unterwasser": "unter Wasser", "gewaesser": "am Wasser", "strand": "am Strand", "fluss": "am Fluss",
    "wald": "im Wald", "dschungel": "im Dschungel", "wiese": "auf der Wiese", "berge": "in den Bergen",
    "wueste": "in der Wüste", "vulkan": "am Vulkan", "hoehle": "in einer Höhle",
    "unterirdisch": "unterirdisch", "ruinen": "in Ruinen", "schnee": "im Schnee", "stadt": "in der Stadt",
    "gebaeude": "in einem Gebäude", "innenraum": "im Innenraum", "technik": "in technischer Umgebung",
    "himmel": "am Himmel", "weltraum": "im Weltraum", "dunkelheit": "im Dunkeln", "kampf": "im Kampf",
    "abstrakt": "ohne erkennbaren Ort", "portraet": "als Porträt",
}
ZEIT_TEXT = {"tag": "am Tag", "nacht": "nachts", "daemmerung": "in der Dämmerung",
             "dunkelheit": "im Dunkeln", "sonnenunterg": "im Sonnenuntergang", "unklar": ""}


def _tags(card_ids):
    con = _dep["get_db"]()
    marken = ",".join("?" * len(card_ids)) or "''"
    reihen = con.execute(
        f"SELECT card_id, orte, zeit, wasser, merkmale FROM card_art_tags WHERE card_id IN ({marken})",
        list(card_ids)).fetchall()
    con.close()
    aus = {}
    for r in reihen:
        aus[r["card_id"]] = {
            "orte": [o for o in re.split(r"[,\s]+", r["orte"] or "") if o],
            "zeit": (r["zeit"] or "").strip(),
            "wasser": r["wasser"] or 0,
            "merkmale": [m for m in re.split(r"[,\s]+", r["merkmale"] or "") if m],
        }
    return aus


def _massstaebe_gecacht(card_ids, lang="de"):
    """Gespeicherte Maßstäbe je Karte, ohne einen einzigen Modellaufruf. → {card_id: span_m}

    Die Passungsprüfung läuft, bevor der Sammler Credits ausgibt; sie darf deshalb nichts
    kosten. Was schon gemessen wurde, wird genutzt, der Rest bleibt einfach unbeantwortet."""
    if not card_ids:
        return {}
    con = _dep["get_db"]()
    marken = ",".join("?" * len(card_ids))
    aus = {}
    for r in con.execute(f"SELECT card_id, daten FROM card_art_analysis WHERE card_id IN ({marken})",
                         list(card_ids)):
        try:
            d = json.loads(r["daten"])
        except Exception:
            continue
        if d.get("span_m"):
            aus[r["card_id"]] = float(d["span_m"])
    con.close()
    return aus


def _geometrie_hinweis(slots, cols, rows, anzahl_karten):
    """Wie weit liegen die Ankerkarten auseinander? → (abzug, hinweis) oder (0, "")

    Gemessen am Fall, der die Analyse ausgelöst hat: drei Karten über Eck (Fächer 0, 5, 6) mit
    sechs freien Fächern dazwischen. Das Bildmodell muss zwei Drittel der Seite erfinden und
    zwei lange Strecken überbrücken. Die guten Seiten der Vitrine haben ihre Karten nebeneinander."""
    if not slots or len(slots) < 2:
        return 0, ""
    pos = [(int(s) % cols, int(s) // cols) for s in slots]
    # Gemessen wird nicht die Spanne über die ganze Seite, sondern ob jede Karte eine Nachbarin
    # hat: drei Karten in einer Reihe spannen ebenfalls über zwei Felder, sind aber der gute Fall.
    # Entscheidend ist die größte Lücke zur jeweils nächsten Karte.
    luecke = max(min(max(abs(a[0] - b[0]), abs(a[1] - b[1])) for b in pos if b != a) for a in pos)
    frei = cols * rows - len(set(int(s) for s in slots))
    if luecke >= 3:
        return 20, (f"Die Karten liegen sehr weit auseinander ({frei} freie Fächer dazwischen). Die KI "
                    "muss lange Strecken erfinden — nebeneinander oder in einer Reihe gelingt die "
                    "Verbindung deutlich zuverlässiger.")
    if luecke == 2:
        return 12, ("Zwischen den Karten liegt jeweils ein freies Fach. Direkt nebeneinander gelingt "
                    "der Übergang zuverlässiger.")
    return 0, ""


# Woraus die Umgebung besteht, entscheidet mit, wie frei die KI erfinden darf. Fels, Wasser,
# Himmel und Feuer haben keine feste Größe — dort sieht jede Erfindung richtig aus. Blätter,
# Blüten und Gras haben eine: ein Monstera-Blatt ist 40 cm lang, und daneben liegt die Karte,
# auf der es in der richtigen Größe zu sehen ist. Gemessen an den Seiten der Vitrine sind alle
# gelungenen Mehrkarten-Seiten aus maßstabsfreiem Material (Schlucht, Strand, Vulkan, Sturmsee).
MATERIAL_FREI = {"berge", "vulkan", "hoehle", "unterirdisch", "gewaesser", "unterwasser", "strand",
                 "fluss", "himmel", "weltraum", "schnee", "wueste", "ruinen", "dunkelheit", "abstrakt"}
MATERIAL_GEBUNDEN = {"wald", "dschungel", "wiese", "stadt", "gebaeude", "innenraum", "technik"}


def _material(card_ids):
    """→ "frei" | "gebunden" | "" — woraus die Umgebung dieser Karten besteht."""
    tags = _tags(list(dict.fromkeys(card_ids)))
    if not tags:
        return ""
    f = g = 0
    for t in tags.values():
        orte = set(t["orte"])
        f += len(orte & MATERIAL_FREI)
        g += len(orte & MATERIAL_GEBUNDEN)
        if "nahaufnahme" in (t.get("merkmale") or ""):
            g += 1
    if g > f:
        return "gebunden"
    return "frei" if f else ""


def _material_regel(art):
    """Wie ausführlich die Umgebung ausgemalt werden soll."""
    if art != "gebunden":
        return ""
    return ("- This kind of scenery is made of things with a known size (leaves, flowers, grass, "
            "windows, bricks). Away from the sources, keep it simple and let it recede: fewer and "
            "larger shapes, deeper shade, haze and soft focus towards the edges of the page. Detail "
            "belongs close to the sources; further out the scene falls into shadow and mist. That "
            "reads as depth and is safer than inventing many new plants at guessed sizes.\n")


def _passung(card_ids, namen=None, slots=None, cols=3, rows=3):
    """→ {wert 0-100, gemeinsam, konflikte, karten}. Ohne Bildmotiv-Daten: neutral."""
    namen = namen or {}
    tags = _tags(list(dict.fromkeys(card_ids)))
    bekannt = [c for c in dict.fromkeys(card_ids) if c in tags]
    if len(bekannt) < 2:
        return {"wert": None, "gemeinsam": "", "konflikte": [], "karten": [], "gruppen": []}
    gruppen, zeiten, wasser = {}, {}, {}
    for c in bekannt:
        t = tags[c]
        gs = {g for g, menge in ORT_GRUPPEN.items() if menge & set(t["orte"])}
        gruppen[c] = gs
        z = t["zeit"]
        zeiten[c] = "nacht" if z in ("nacht", "dunkelheit") else ("tag" if z in ("tag", "sonnenunterg") else "")
        wasser[c] = t["wasser"]
    wert = 100
    konflikte, hinweise, gruppen_konflikt = [], [], False
    alle_gruppen = set().union(*gruppen.values()) if gruppen else set()
    geteilt = set.intersection(*[g for g in gruppen.values() if g]) if all(gruppen.values()) else set()
    if len(alle_gruppen) > 1 and not geteilt:
        gruppen_konflikt = True
        wert -= min(50, 25 * (len(alle_gruppen) - 1))
        namen_von = lambda g: ", ".join(namen.get(c, c) for c in bekannt if g in gruppen[c])
        konflikte.append("Verschiedene Landschaften: "
                         + " · ".join(f"{g} ({namen_von(g)})" for g in sorted(alle_gruppen)))
    if "tag" in zeiten.values() and "nacht" in zeiten.values():
        wert -= 35
        konflikte.append("Tag trifft Nacht: "
                         + ", ".join(namen.get(c, c) for c in bekannt if zeiten[c] == "nacht") + " spielt nachts")
    if any(w >= 3 for w in wasser.values()) and any(w == 0 for w in wasser.values()):
        wert -= 30
        konflikte.append("Unter Wasser trifft Land: "
                         + ", ".join(namen.get(c, c) for c in bekannt if wasser[c] >= 3) + " spielt unter Wasser")
    # Der Zoom entscheidet mit, ob eine Seite gelingt: eine Nahaufnahme neben einer Weitsicht
    # lässt sich nicht in eine Landschaft bringen, ohne dass eine der beiden falsch wirkt.
    # Gemessen nur aus schon gespeicherten Werten — die Prüfung kostet nichts.
    spannen = _massstaebe_gecacht(bekannt)
    if len(spannen) >= 2:
        klein = min(spannen.values()); gross = max(spannen.values())
        faktor = gross / max(0.01, klein)
        # Ein weiterer Ausschnitt ist kein Widerspruch, sondern Tiefe: diese Karte steht in der
        # Szene weiter hinten. Das ist ein Hinweis, kein Mangel — der Auftrag staffelt danach.
        if faktor >= 1.8:
            eng = [namen.get(c, c) for c in bekannt if spannen.get(c) == klein]
            weit = [namen.get(c, c) for c in bekannt if spannen.get(c) == gross]
            hinweise.append(
                f"{', '.join(weit)} zeigt einen weiteren Ausschnitt ({gross:g} m) als "
                f"{', '.join(eng)} ({klein:g} m) — die KI stellt es entsprechend weiter hinten "
                "in die Szene.")
    abzug, hinweis = _geometrie_hinweis(slots, cols, rows, len(bekannt))
    if hinweis:
        wert -= abzug
        konflikte.append(hinweis)
    gemeinsam = ""
    if geteilt:
        orte = [o for c in bekannt for o in tags[c]["orte"]]
        haeufig = max(set(orte), key=orte.count) if orte else ""
        gemeinsam = ORT_TEXT.get(haeufig, haeufig)
    z = [x for x in zeiten.values() if x]
    # „nachts" nur, wenn es mindestens zwei Karten sagen — eine allein ist keine Tageszeit
    # der Seite, sondern der Ausreißer, den der Plan überbrücken muss.
    if len(z) >= 2 and len(set(z)) == 1:
        gemeinsam = (gemeinsam + " " + ZEIT_TEXT.get(z[0], "")).strip()
    return {"wert": max(0, wert), "gemeinsam": gemeinsam, "konflikte": konflikte,
            "hinweise": hinweise, "gruppen_konflikt": gruppen_konflikt,
            "gruppen": sorted(alle_gruppen),
            "karten": [{"id": c, "name": namen.get(c, c),
                        "ort": ", ".join(ORT_TEXT.get(o, o) for o in tags[c]["orte"][:2]),
                        "zeit": ZEIT_TEXT.get(tags[c]["zeit"], "")} for c in bekannt]}


REGIE_PROMPT = (
    "You are the art director for ONE 'extended art' page. IMAGE 1 shows the page: the finished "
    "illustrations sit at their real positions, every gray area still has to be painted. The images "
    "after it are those illustrations in close-up.\n"
    "LOOK AT THE IMAGES and decide what goes into each gray area, so the whole page reads as one place.\n\n"
    "Two things decide whether this works:\n"
    "A) ONE structure that runs across the whole page and touches every source – a stream, a ridge, "
    "a path, a fallen trunk, a shaft of light, a shoreline. Not a mood, not 'more jungle': a thing "
    "with a course. Say where it starts, which source edges it touches and where it ends.\n"
    "B) Every gray area gets its own content. An area with nothing assigned is where the painter "
    "invents a creature.\n\n"
    "Answer with JSON only:\n"
    '{"habitat": "the place, time of day and light direction in one sentence",\n'
    '"struktur": "the one structure from A, one or two sentences, concrete and visual",\n'
    '"faecher": {"row/column": "one sentence naming what is seen in exactly this area – name plants, '
    'water, rock, ground, sky, and say which source edge it continues", ...}}\n'
    "Use exactly the row/column keys listed under FREE AREAS. Be visual and specific. Never mention "
    "cards, frames, grids, pockets or Pokémon by name in the faecher sentences – describe scenery only."
)


def _drehbuch_text(plan, cols, rows, anker):
    """Den Plan als Auftrag rendern. Fächer ohne Satz bekommen einen Rückfall, damit keine Fläche
    ohne Inhalt bleibt — genau dort entstand bisher das zweite Pokémon."""
    if not isinstance(plan, dict):
        return ""
    teile = []
    if plan.get("habitat"):
        teile.append("THE PLACE: " + str(plan["habitat"])[:300])
    if plan.get("struktur"):
        teile.append("ONE STRUCTURE RUNS THROUGH THE WHOLE PAGE, touching every finished part: "
                     + str(plan["struktur"])[:400])
    faecher = plan.get("faecher") if isinstance(plan.get("faecher"), dict) else {}
    zeilen = []
    for stueck in _freiflaechen(cols, rows, anker):
        for slot in stueck:
            x, y = slot % cols, slot // cols
            key = f"{y + 1}/{x + 1}"
            satz = faecher.get(key) or faecher.get(f"row {y + 1} / column {x + 1}") or faecher.get(str(slot))
            zeilen.append(f"- row {y + 1} / column {x + 1}: "
                          + (str(satz)[:220] if satz else "the same scenery as the area next to it, "
                             "continuing the nearest source; nothing new is introduced here"))
    if zeilen:
        teile.append("WHAT IS IN EACH AREA THAT IS STILL GRAY – paint exactly this, nothing else:\n"
                     + "\n".join(zeilen))
    return "\n".join(teile) + "\n" if teile else ""


WUNSCH_PROMPT = (
    "You turn a collector's wish into ONE instruction for a painter who extends a trading-card "
    "illustration into a full page around the card.\n"
    "Rules: keep the intent and every concrete thing the collector asked for; make it visual and "
    "specific; English; imperative; at most 35 words; one sentence or two. Never mention cards, "
    "frames, borders, grids, pockets, text or logos. Do not invent a second main creature. "
    "If the wish is already fine, just tighten it. Answer with the instruction only."
)


def _wunsch_scharf(wunsch, kontext=""):
    """Den Wunschtext des Sammlers vor dem Bildmodell schärfen (≈ 0,2 ct).

    Gemessen an der Praxis: „mehr wasser bitte und schön hell" landet als deutscher
    Halbsatz mitten in einem englischen Prompt und wird überlesen. Ein kurzer Textschritt
    macht daraus eine Anweisung, die das Bildmodell wörtlich ausführen kann. Fällt der
    Schritt aus, geht der Originaltext weiter wie bisher."""
    wunsch = (wunsch or "").strip()
    if len(wunsch) < 4:
        return wunsch, 0.0
    try:
        d = _openrouter({
            "model": _dep["env"]().get("ARTWORK_WUNSCH_MODELL") or WUNSCH_MODELL,
            "messages": [{"role": "user", "content":
                          WUNSCH_PROMPT + (f"\n\nPAGE CONTEXT: {kontext}" if kontext else "")
                          + f"\n\nWISH (any language): {wunsch[:400]}"}],
            "usage": {"include": True},
        }, timeout=60)
        text = ((d.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()
        text = re.sub(r"^[\"\u201c\u201e]|[\"\u201d]$", "", text).strip()
        return (text[:400] or wunsch), float((d.get("usage") or {}).get("cost") or 0)
    except Exception:
        return wunsch, 0.0


def _regie(cols, rows, anker, namen, analysen, passung=None, vorlage=None, bilder=None):
    """Drehbuch für Seiten mit mehreren Karten (Vision-Aufruf, ≈ 1,5 ct). → (plan, kosten)

    Der Plan war bis zum 04.09. ein reiner Textschritt ohne Blick auf die Seite und lieferte Prosa.
    Gemessen an den Seiten der Vitrine ist er die entscheidende Stelle: die gelungenen Seiten haben
    eine benannte Struktur quer über die Seite und einen Inhalt je Fläche, die misslungenen hatten
    „mehr Dschungel"."""
    beschreibung = []
    for slot, cid in sorted(anker.items(), key=lambda kv: int(kv[0])):
        col, row = int(slot) % cols, int(slot) // cols
        a = analysen.get(cid) or {}
        mass = (f" It spans about {a['span_m']:g} m of the world." if a.get("span_m") else "")
        beschreibung.append(
            f"SOURCE {namen.get(cid) or cid} at row {row + 1}, column {col + 1}.{mass}\n"
            + _analyse_text(a))
    hinweis = ""
    if passung and passung.get("konflikte"):
        hinweis = "\n\nKNOWN CLASHES to bridge, do not average them away: " + " | ".join(passung["konflikte"])
    text = (REGIE_PROMPT + "\n\nSOURCES:\n" + "\n\n".join(beschreibung)
            + "\n\nFREE AREAS (these are the keys for \"faecher\"):\n"
            + _layout_text(cols, rows, anker, namen) + hinweis)
    inhalt = [{"type": "text", "text": text}]
    if vorlage is not None:
        v = vorlage.copy(); v.thumbnail((1024, 1024))
        inhalt.append({"type": "text", "text": "IMAGE 1 – the page (gray = to be painted):"})
        inhalt.append({"type": "image_url", "image_url": {"url": _data_url(v, "JPEG")}})
        for i, (slot, cid) in enumerate(sorted(anker.items(), key=lambda kv: int(kv[0]))):
            crop = (bilder or {}).get(cid)
            if crop is None:
                continue
            c = crop.copy(); c.thumbnail((640, 640))
            inhalt.append({"type": "text", "text": f"IMAGE {i + 2} – {namen.get(cid) or cid} in close-up:"})
            inhalt.append({"type": "image_url", "image_url": {"url": _data_url(c, "JPEG")}})
    try:
        d = _openrouter({
            "model": _dep["env"]().get("ARTWORK_REGIE_MODELL") or ANALYSE_MODELL,
            "messages": [{"role": "user", "content": inhalt}],
            "response_format": {"type": "json_object"},
            "usage": {"include": True},
        }, timeout=120)
        roh = ((d.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()
        roh = re.sub(r"^```(?:json)?|```$", "", roh, flags=re.M).strip()
        return json.loads(roh), float((d.get("usage") or {}).get("cost") or 0)
    except Exception:
        return {}, 0.0


def _prompt_teile(cols, rows, anker, stil, wunsch, namen, analysen, vorlage, bilder, pokemon, feedback="",
                  regie=None, eine_szene=False, material=""):
    """Interleaved content für das Bildmodell: Auftrag, Vorlage, Ausschnitte, Drehbuch, fünf Regeln.

    Der Auftrag war am 04.09. auf 11.900 Zeichen und vierzehn Regelzeilen angewachsen, mit
    19 Verneinungen — die guten Seiten der Vitrine entstanden mit 8.800 Zeichen und elf Regeln.
    Ein Bildmodell gewichtet den Anfang stark; eine lange Verbotsliste arbeitet gegen sich selbst.
    Belegt: die Anweisung gegen ein zweites Kartenbild stand an drei Stellen und wurde trotzdem
    übergangen — gewirkt hat erst eine ZAHL. Deshalb: sagen, was zu sehen ist (Drehbuch), und nur
    das Nötigste verbieten."""
    mehrere = len(anker) > 1
    kreaturen = [namen[c] for c in dict.fromkeys(anker.values()) if namen.get(c)]
    plaetze = ", ".join(f"row {int(s_) // cols + 1}/column {int(s_) % cols + 1}"
                        for s_ in sorted(anker, key=lambda x: int(x)))
    intro = (
        "OUTPAINTING TASK. IMAGE 1 is one large painting of which only "
        + (f"{len(anker)} rectangular parts are" if mehrere else "one rectangular part is")
        + f" finished (at {plaetze}); every gray pixel is still unpainted. Paint all gray areas so "
        "that the finished part" + ("s become windows into one and the same picture: the scene, "
                                    "perspective, light, colours and painting technique of each one "
                                    "continue outward from its edges without any visible transition."
                                    if mehrere else
                                    " extends seamlessly in every direction: the same scene, the same "
                                    "horizon height, light, colours and painting technique continue "
                                    "outward without any visible transition.") + "\n")
    teile = [{"type": "text", "text": intro}, {"type": "image_url", "image_url": {"url": _data_url(vorlage)}}]
    n = 2
    for slot, card_id in sorted(anker.items(), key=lambda kv: int(kv[0])):
        crop = bilder.get(card_id)
        if crop is None:
            continue
        col, row = int(slot) % cols, int(slot) // cols
        wo = f" at row {row + 1}, column {col + 1}" if mehrere else ""
        a = analysen.get(card_id) or {}
        # Kurzfassung der Analyse: die Kanten sind das, was fortgesetzt werden muss; alles Übrige
        # sieht das Modell auf dem Bild selbst.
        kanten = a.get("edges") if isinstance(a.get("edges"), dict) else {}
        kantentext = "; ".join(f"{k}: {str(v)[:150]}" for k, v in kanten.items() if v)
        mass = (f" The illustration spans about {a['span_m']:g} m of the world"
                + (f"; {a['anchor']}" if a.get("anchor") else "") + "." if a.get("span_m") else "")
        aufdruck = (" Card print (name, HP, attack text, symbols) lies on this artwork – it is print, "
                    "not scenery.") if _hat_aufdrucke(a) else ""
        teile.append({"type": "text", "text": (
            f"IMAGE {n} – the finished part{wo} in close-up. Its creature is "
            f"{namen.get(card_id) or 'the main creature'}.{mass}{aufdruck}\n"
            + (f"What is cut off at its edges and has to continue – {kantentext}" if kantentext else ""))})
        teile.append({"type": "image_url", "image_url": {"url": _data_url(crop, 'JPEG')}})
        n += 1
    for p in pokemon:
        if p.get("_bild") is None:
            continue
        teile.append({"type": "text", "text": (
            f"IMAGE {n} – reference for the anatomy and colors of {p['name_en']} ONLY. Do not copy this "
            "picture: no white background, no cut-out look, not this pose.")})
        teile.append({"type": "image_url", "image_url": {"url": _data_url(p["_bild"], 'JPEG')}})
        n += 1

    wer = " and ".join(kreaturen) if kreaturen else "the creature of the finished part"
    erlaubt = ", ".join(p["name_en"] for p in pokemon) if pokemon else ""
    text = ""
    if mehrere and regie:
        text += "\n" + _drehbuch_text(regie, cols, rows, anker)
    # Fünf Regeln. Mehr wurden nachweislich nicht gelesen.
    text += (
        "\nRules:\n"
        f"1. {wer} exist"
        f"{'s' if len(kreaturen) <= 1 else ''} only inside the finished part"
        + ("s, and stop at their edges." if mehrere else ", and stops at its edge.") + " Where a body is cut off there, hide the "
        "cut behind scenery – mist, foliage, rock, water, light. Number of creatures you paint into "
        f"the gray areas: {'exactly one ' + erlaubt if erlaubt else '0'}.\n"
        "2. At every edge of a finished part, continue what is cut off there at the same height and "
        "angle, then more of the same landscape, atmosphere and depth. Objects keep the real-world "
        "size they have inside the finished part.\n"
        "3. " + _technik_regel(stil, analysen).strip().lstrip("- ").replace("\n- ", " ") + "\n"
        f"4. The page keeps exactly {len(anker)} rectangular finished area"
        + ("s" if mehrere else "") + f", at {plaetze}. Everything printed on them – border, name plate, "
        "HP, text boxes, symbols – is print, not scenery: none of it appears in the painted areas, and "
        "no second card, frame, panel, text or number is painted anywhere.\n"
        "5. One continuous painting, no gray pixel left, and the finished parts stay untouched.\n")
    text += _material_regel(material)
    if wunsch:
        text += f"Wish from the collector: {wunsch.strip()[:400]}\n"
    if feedback:
        text += "A previous attempt was rejected for: " + feedback + " – avoid that.\n"
    text += "Output exactly the same dimensions as IMAGE 1."
    teile.append({"type": "text", "text": text})
    return teile


def _hat_aufdrucke(analyse):
    """Liegt der Kartentext direkt auf der Illustration? (Vollbildkarten)

    Das Feld `aufdrucke` kam erst später dazu; die vor ihm angelegten Analysen haben es nicht.
    Deshalb der Rückfall über die Fenstergröße: deckt das Illustrationsfenster fast die ganze
    Karte, ist es eine Vollbildkarte, und dann liegen Name, HP und Attackentext im Bild."""
    a = analyse or {}
    if a.get("aufdrucke") is not None:
        return bool(a["aufdrucke"])
    box = a.get("box")
    if not (isinstance(box, list) and len(box) == 4):
        return False
    ymin, xmin, ymax, xmax = box
    return (xmax - xmin) >= 880 and (ymax - ymin) >= 880


def _technik_regel(stil, analysen):
    """Wie die neuen Flächen gemalt werden. Die gemessene Technik der Quellen steht vor dem
    Stil-Chip, nicht dahinter.

    Vorher war der Chip die letzte und deutlichste Anweisung: „Ölgemälde" malte Impasto direkt
    neben eine flache Gouache-Karte, und der Chip „Wie die Karte" verwies bei drei Karten auf
    „die Kartenillustration", ohne zu sagen, welche. Jetzt wird die Technik ausgeschrieben."""
    techniken = [str((a or {}).get("technique") or "").strip() for a in analysen.values()]
    techniken = list(dict.fromkeys([t for t in techniken if t]))
    quelle = " / ".join(techniken[:3])
    if stil == "karte" and quelle:
        text = ("- Technique for the new areas – copy the sources exactly: " + quelle
                + " Match their flatness or depth, their brush texture, their level of detail and their "
                "colour saturation, so that the painted areas are indistinguishable from the sources.\n")
    else:
        text = f"- Technique for the new areas: {STILE.get(stil, STILE['karte'])}\n"
        if quelle:
            text += ("  Even in this technique, keep the sources' own look: " + quelle
                     + " Match their level of detail, their flatness or depth and their colour saturation.\n")
    # Der Fehler, der im Vergleich am haeufigsten auftrat: die Umgebung wird tiefer, dunkler und
    # fotografischer als die Karte, und dadurch bricht jede Kartenkante sichtbar.
    text += ("- Do not make the painted areas more photorealistic, deeper, softer, darker or more "
             "atmospheric than the finished part(s). What is painted stays the continuation of the "
             "source scene; only how it is painted follows the technique above.\n")
    return text


def _eine_szene(passung):
    """Gehören die Karten in EINEN Raum oder braucht jede ihre eigene Zone?

    **Vorerst abgeschaltet** (`ARTWORK_SZENE=eins` schaltet ihn an). Gemessen an vier Läufen der
    151er-Seite am 04.09.: der gemeinsame Raum verbindet die Karten sichtbar besser — eine
    durchgehende Lichtung mit Bach, Tiefe von vorn nach hinten. Er verleitet das Bildmodell aber
    zuverlässig dazu, die Pokémon der Karten ein zweites Mal mitten in die Szene zu malen; drei
    von vier Läufen endeten so. Weder die Zählangabe („0 Kreaturen") noch das Herausretuschieren
    haben das gehalten. Ein doppeltes Pokémon ist ein harter Fehler, drei getrennte Vignetten sind
    nur mittelmäßig — deshalb bleibt bis zur Lösung der zurückhaltende Weg der Standard.

    Die Zonen-Regel („jede Karte behält ihre eigene Umgebung") stammt vom 28.08. und war
    richtig gegen den Matsch, der beim Verschmelzen dreier Szenen entstand. Sie ist aber der
    Grund, warum drei Dschungelkarten als drei getrennte Vignetten enden statt als ein Bild:
    sie verbietet die gemeinsame Szene ausdrücklich. Seit Maßstab und Maltechnik im Auftrag
    stehen, entsteht der Matsch nicht mehr aus dem Verschmelzen, sondern kam aus den fehlenden
    Ankern. Also: gleicher Lebensraum → ein Raum mit Tiefe. Sturmsee neben Vulkan → Zonen."""
    if (_dep["env"]().get("ARTWORK_SZENE") or "zonen") != "eins":
        return False
    if not passung or passung.get("wert") is None:
        return False
    return passung["wert"] >= 70 and not passung.get("gruppen_konflikt")


def _zonen_regeln(cols, rows, anker, namen, regie, eine_szene=False):
    """Regeln für Seiten mit mehreren Karten.

    `eine_szene=True`: ein durchgehender Raum, gestaffelt in die Tiefe (siehe `_tiefen`).
    Sonst: jede Karte behält ihre eigene Umgebung, überblendet wird nur an den Grenzen —
    das ist der Fall für Karten aus unvereinbaren Lebensräumen."""
    if eine_szene:
        text = (
            "- SEVERAL SOURCES, ONE SCENE: these illustrations show the same kind of place. Do not give "
            "each of them its own separate little world with a border between them. Paint ONE single "
            "continuous location that all of them are part of, seen from one viewpoint, with one light "
            "direction and one weather. The ground, the water, the canopy and the light run without "
            "interruption from one source to the next.\n"
            "- Where the space between two sources is wide, fill it with the scene itself – a path, a "
            "bank, a slope, a stream, a fallen trunk, open ground – so the eye travels from one source "
            "to the other through the place, not across a gap.\n"
            "- Keep one consistent perspective for the whole page. The sources sit at different depths "
            "in it (see the staging below); everything you paint has to agree with that depth.\n"
            "- What you paint is the LOCATION only: plants, water, ground, rock, wood, sky, light and "
            "weather. One scene does not mean the creatures step out into it – everything alive on this "
            "page is already inside the rectangles, and the gray areas receive 0 of them.\n")
        if regie:
            text += "- Follow this composition plan for the new areas:\n" + regie.strip() + "\n"
        return text
    zone, _ = _zonen(cols, rows, anker)
    orte = {}
    for slot, cid in zone.items():
        if str(slot) in anker:
            continue
        orte.setdefault(cid, []).append(f"row {slot // cols + 1}/column {slot % cols + 1}")
    zeilen = "; ".join(f"the areas at {', '.join(v)} belong to {namen.get(k) or k}" for k, v in orte.items())
    text = (
        "- IMPORTANT – several sources: do NOT merge them into one averaged scene. Each source keeps its own "
        f"landscape, colours, depth and light in the areas around it: {zeilen}. Continue each source's own scene "
        "outward from its own edges, exactly as it looks there.\n"
        "- Where two such areas meet, blend them with ONE believable connective element – dense vegetation, a slope, "
        "mist, a stream, rocks, a fallen trunk – so the change of scenery reads as walking through one habitat, "
        "never as a hard cut or a straight seam.\n"
        "- Do not force one straight horizon across the whole painting: the sources are painted at different heights "
        "and distances. Hide those differences behind foreground plants, terrain, haze or water instead of drawing a "
        "single line through everything.\n"
    )
    if regie:
        text += "- Follow this composition plan for the new areas:\n" + regie.strip() + "\n"
    return text


# --- Kontrolle (Vision-Modell prüft die Fortsetzung an den Kanten) ---------------------------------

# Die Nachkontrolle sucht bisher nach Gitterlinien und doppelten Kreaturen; der zweite
# Kartenrahmen gehoert in dieselbe Liste.
PRUEF_PROMPT = (
    "You are a strict art director checking an 'extended art' page for a card collector.\n"
    "IMAGE 1 is the TEMPLATE: it shows the finished source illustrations at exactly the positions they "
    "occupy on the page; every gray area was still unpainted. IMAGE 2 is the finished page: the gray areas "
    "were painted so that each source continues seamlessly into its own surroundings.\n"
    "The sources are scans of printed trading cards. Their frames, name plates, HP numbers, attack text, "
    "symbols, holo overlay and straight rectangular edges are EXPECTED and are never a problem. Judge ONLY "
    "the areas that are gray in IMAGE 1.\n"
    "Check strictly, in the painted areas only:\n"
    "1. EDGES: at every edge of every source, do the background elements continue consistently – same lines, "
    "angles and heights (a shoreline, horizon, branch, wall, beam of light continuing exactly where it leaves "
    "the source)? Name every element that breaks, bends, jumps or ends abruptly, and say at which source.\n"
    "2. SCALE: are objects painted at the same real-world size as objects of the same kind inside the sources? "
    "A leaf, stone, flower or trunk outside must not be several times larger or smaller than inside.\n"
    "3. CREATURE – the worst failure, look hardest here: take each source's creature and follow its outline "
    "to the edge of its rectangle. Does any part of its body carry on beyond that edge into the painted "
    "area – ears, horns, wings, tail, limbs, claws, fur, spikes, ribbons, energy aura or glow – as if the "
    "creature were bigger than its rectangle? Is it painted a second time anywhere, at any size, whole or "
    "partial, as shadow, reflection, silhouette or abstract shape in its colours? Any of this is always "
    "ok=false and always schwer=true, even when it looks beautiful.\n"
    "4. CARD ELEMENTS – count them: IMAGE 1 shows a fixed number of source rectangles. Count the "
    "card-like rectangles in IMAGE 2 (anything with a border and a name, a number or a text box). If "
    "IMAGE 2 has MORE of them than IMAGE 1, a second card was painted into a gray area – say how many "
    "and where. Also report a single new name plate, HP number, attack text box, energy/set/rarity "
    "symbol or illustrator credit in a formerly gray area. The sources themselves never count here.\n"
    "5. LOOK: do technique, palette, level of detail and light direction of the painted areas match the "
    "sources, or did they become more photorealistic, deeper, darker or softer?\n"
    "6. Are there frames, straight seams, tiles, panels, text, or remaining gray areas?\n"
    'Answer with JSON only: {"ok": true|false, "schwer": true|false, "probleme": ["concrete problem with '
    'location", ...]}.\n'
    '"ok" is false as soon as you find anything from 1-6. "schwer" is true ONLY for: a duplicated or '
    "continued creature, an extra card-like rectangle or any newly painted card element, remaining gray "
    "areas, or a hard seam / broken edge – "
    "those are worth painting the page again. Mismatched technique, palette or softness alone are NOT "
    "schwer: repainting does not reliably fix them and only costs money."
)


def _pruefen(vorlage: Image.Image, ergebnis: Image.Image):
    """→ (ok, schwer, probleme, kosten) – bei Modellfehlern gilt das Bild als ok (kein Retry auf Verdacht).

    `vorlage` ist die Vorlage der ganzen Seite: alle Illustrationen an ihrer echten Position,
    alles Übrige grau. Bis zum 04.09.2026 bekam der Prüfer stattdessen EINE einzelne
    Kartenillustration und den Satz, sie sitze in der Mitte — die übrigen echten Karten der Seite
    hielt er folglich für hineingemalte Kartenrahmen. Gemessen über alle Läufe: 25 von 29
    Beanstandungen bei Mehrkarten-Seiten waren genau dieser Fehlalarm, und jede davon hat die
    Seite ein zweites Mal malen lassen (0,27 $ statt 0,13 $)."""
    try:
        v = vorlage.copy(); v.thumbnail((1024, 1024))
        e = ergebnis.copy(); e.thumbnail((1024, 1024))
        d = _openrouter({
            "model": _dep["env"]().get("ARTWORK_ANALYSE_MODELL") or ANALYSE_MODELL,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": PRUEF_PROMPT},
                {"type": "text", "text": "IMAGE 1 – template (gray = was unpainted):"},
                {"type": "image_url", "image_url": {"url": _data_url(v, "JPEG")}},
                {"type": "text", "text": "IMAGE 2 – finished page:"},
                {"type": "image_url", "image_url": {"url": _data_url(e, "JPEG")}},
            ]}],
            "response_format": {"type": "json_object"},
            "usage": {"include": True},
        }, timeout=90)
        text = (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        j = json.loads(text)
        probleme = [str(x)[:200] for x in (j.get("probleme") or [])][:6]
        ok = bool(j.get("ok")) or not probleme
        return ok, (not ok and bool(j.get("schwer"))), probleme, float((d.get("usage") or {}).get("cost") or 0)
    except Exception:
        return True, False, [], 0.0


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

REPARATUR_PROMPT = (
    "Retouch this finished painting. Keep EVERYTHING exactly as it is – same composition, same "
    "colours, same brushwork, same light, same level of detail, the same rectangular card scans "
    "untouched and in the same place. Change only the spots listed below, and change nothing else. "
    "At each of those spots, paint over the offending object with the surroundings it stands in: "
    "continue the plants, water, ground, rock or sky that are already around it, in the same "
    "technique and the same scale, so that afterwards nothing is there but the location itself and "
    "nobody can tell that something was removed.\n"
    "Spots to fix:\n{probleme}\n"
    "Output the same image at the same dimensions, with only those spots changed."
)


def _reparatur_teile(seite: Image.Image, probleme):
    """Einen benannten Fehler aus dem fertigen Bild herausretuschieren, statt neu zu würfeln.

    Ein zweiter vollständiger Lauf kostet dasselbe wie diese Bearbeitung, wirft aber die gelungene
    Komposition weg und beginnt von vorn — gemessen an drei Läufen der 151er-Seite: der Aufbau war
    jedes Mal gut, verdorben hat ihn ein zweites Exemplar einer Kreatur. Das lässt sich gezielt
    übermalen."""
    liste = "\n".join(f"- {p}" for p in probleme[:4])
    return [
        {"type": "text", "text": REPARATUR_PROMPT.replace("{probleme}", liste)},
        {"type": "image_url", "image_url": {"url": _data_url(seite)}},
    ]


def _reparierbar(probleme):
    """Lässt sich der Mangel wegretuschieren? Ein doppeltes Wesen oder ein gemalter Kartenrahmen ja;
    ein falscher Maßstab über die halbe Seite nicht — dafür braucht es einen neuen Lauf."""
    text = " ".join(probleme).lower()
    treffer = ("painted a second time", "second copy", "duplicat", "again in the", "added in the",
               "extra card", "another card", "second card", "card frame", "name plate", "text box",
               "a painted", "is painted", "continues into", "continues directly", "continued",
               "extends into", "extending", "spans across", "sweeping across", "giant ")
    return any(t in text for t in treffer)


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
        raise ModellBezahlt("Das Bildmodell hat kein Bild geliefert" + (f": {str(msg.get('content'))[:160]}" if msg.get("content") else "."))
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
        stufen = (_dep["env"]().get("ARTWORK_MODUS") or STANDARD_MODUS) == "stufen"
        pokemon = json.loads(row["pokemon"] or "[]")
        for p_ in pokemon:
            p_["_bild"] = _pokemon_bild(p_["dex"])
        schritte = [{"modus": "stufen" if stufen else "schnell"}]
        # 1. Analyse je Karte (gecacht) + Illustrations-Ausschnitte
        analysen, bilder, namen, kosten = {}, {}, {}, 0.0
        con = get_db()
        for cid in dict.fromkeys(anker.values()):
            r = con.execute("SELECT name_de, name_en FROM cards WHERE id = ?", (cid,)).fetchone()
            if r:
                namen[cid] = r["name_en"] or r["name_de"]
            a = _analyse(cid, lang)
            # Maßstab: je Karte einmal gemessen (≈ 0,8 ct) und dauerhaft gespeichert. Ohne ihn
            # malt das Modell die Umgebung in einem beliebigen Zoom — der häufigste Grund dafür,
            # dass eine Seite auseinanderfällt.
            a, km = _massstab(cid, lang, a)
            kosten += km
            analysen[cid] = a
            kosten += float((a or {}).get("_kosten") or 0)
            crop, _ = _illustration(cid, lang, a)
            if crop is not None:
                crop = crop.copy(); crop.thumbnail((1024, 1024))
                bilder[cid] = crop
        con.close()
        # Passt die Auswahl zusammen? Die Antwort steht in den Bildmotiv-Daten und geht in
        # den Regie-Plan; der Nutzer sieht sie schon vor dem Start (/api/artwork/passung).
        passung = _passung(list(anker.values()), namen, slots=list(anker.keys()), cols=cols, rows=rows)
        if passung.get("wert") is not None:
            schritte.append({"passung": passung["wert"], "gemeinsam": passung["gemeinsam"],
                             "konflikte": passung["konflikte"], "hinweise": passung.get("hinweise") or []})
        # Den Wunsch des Sammlers vor dem Bildmodell schärfen
        if wunsch:
            wunsch_roh = wunsch
            wunsch, kw = _wunsch_scharf(wunsch, passung.get("gemeinsam") or "")
            kosten += kw
            if wunsch != wunsch_roh:
                schritte.append({"wunsch_roh": wunsch_roh, "wunsch": wunsch})
        # Vorlage der ganzen Seite (nur Illustrationsfenster) + Fensterpositionen in Seitenkoordinaten.
        # Sie entsteht vor dem Regie-Plan, weil der Plan sie sehen soll.
        vorlage, fenster = _vorlage(anker, cols, rows, geo, lang, analysen)
        # Gehören die Karten in EINEN Raum (gleicher Lebensraum) oder braucht jede ihre eigene
        # Zone? Davon hängt ab, ob die Seite als eine Szene mit Tiefe geplant wird.
        eine_szene = _eine_szene(passung)
        material = _material(list(anker.values()))
        if material:
            schritte.append({"material": material})
        # Regie-Plan bei mehreren Karten (billiger Schritt, jetzt mit Blick auf die Seite)
        regie = None
        if len(dict.fromkeys(anker.values())) > 1:
            regie, kr = _regie(cols, rows, anker, namen, analysen, passung,
                               vorlage=vorlage.crop(geo["seite"]), bilder=bilder)
            kosten += kr
            if regie:
                schritte.append({"drehbuch": regie})
        px0, py0, px1, py1 = geo["seite"]
        sw, sh = px1 - px0, py1 - py0
        fenster_seite = {k: (v[0] - px0, v[1] - py0, v[2] - px0, v[3] - py0) for k, v in fenster.items()}

        # 2. Stufe A: kleiner Ring um die Illustration(en) – genaue Geometrie an den Kanten
        stufe_a = None
        if stufen and fenster_seite:
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
                vorlage_a = canvas_a.crop(geo_a["seite"]).resize((rw, rh), Image.LANCZOS)
                for versuch in range(2):
                    teile = _prompt_teile(cols, rows, anker, stil, wunsch, namen, analysen, canvas_a, bilder, [],
                                          feedback, regie, eine_szene, material)
                    erg, kk, modell_a = _modell_aufruf(teile, modell, geo_a["ar"], groesse)
                    kosten += kk
                    if erg.size != (geo_a["cw"], geo_a["ch"]):
                        erg = erg.resize((geo_a["cw"], geo_a["ch"]), Image.LANCZOS)
                    stufe_a = erg.crop(geo_a["seite"]).resize((rw, rh), Image.LANCZOS)
                    ok, schwer, probleme, kp = _pruefen(vorlage_a, stufe_a)
                    kosten += kp
                    schritte.append({"stufe": "A", "versuch": versuch + 1, "ok": ok, "schwer": schwer,
                                     "probleme": probleme})
                    if ok or not schwer or versuch == 1:
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
        # Nach dem Malen prüfen — im Schnellmodus war das bisher nicht der Fall, und genau
        # dort entstand der Fehler, den eine gespeicherte Seite zeigte: die Ankerkarte
        # noch einmal, leicht versetzt, hinter der echten Karte. `_pruefen` sucht diesen
        # Fall ausdrücklich (Regel 2 im Prüf-Prompt) und kostet rund 0,5 ct. Eine einzige
        # Wiederholung, und nur wenn die Prüfung wirklich etwas findet — sonst würde ein
        # Fehlurteil jede Seite verdoppeln.
        # Geprüft wird gegen die Vorlage der ganzen Seite, und wiederholt nur bei schweren Mängeln
        # (verdoppelte Kreatur, neu gemalte Kartenelemente, graue Reste, harte Naht). Stil und
        # Weichzeichnung allein lösen keinen zweiten Lauf mehr aus: er behebt sie nicht, er würfelt
        # neu — und kostet 13 Cent. Der zweite Versuch wird selbst geprüft; ist er nicht besser,
        # bleibt der erste.
        vorlage_seite = canvas_b.crop(geo["seite"])
        feedback_b, seite, bester = "", None, None
        for versuch in range(2):
            teile = _prompt_teile(cols, rows, anker, stil, wunsch, namen, analysen, canvas_b, bilder,
                                  [] if stufen else pokemon, feedback_b, regie, eine_szene, material)
            erg, kk, modell_b = _modell_aufruf(teile, modell, geo["ar"], groesse)
            kosten += kk
            if erg.size != (geo["cw"], geo["ch"]):
                erg = erg.resize((geo["cw"], geo["ch"]), Image.LANCZOS)
            seite = erg.crop(geo["seite"])
            if stufen or not bilder:
                schritte.append({"stufe": "B", "versuch": versuch + 1})
                break
            ok, schwer, probleme, kp = _pruefen(vorlage_seite, seite)
            kosten += kp
            schritte.append({"stufe": "B", "versuch": versuch + 1, "ok": ok, "schwer": schwer,
                             "probleme": probleme})
            if bester is None or ok or len(probleme) < len(bester[1]):
                bester = (seite, probleme, ok)
            if ok or not schwer or versuch == 1:
                break
            # Ein benanntes Objekt zu viel — ein zweites Exemplar, ein über die Kante fortgesetztes
            # Körperteil, ein gemalter Kartenrahmen — wird herausretuschiert statt die ganze Seite
            # neu zu würfeln. Der Aufbau war in den Vergleichsläufen jedes Mal gut; verdorben hat
            # ihn genau dieses eine Objekt. Kostet denselben einen Bildaufruf, behält aber die
            # gelungene Komposition. Danach ist Schluss: höchstens zwei Bildaufrufe je Seite,
            # entweder Reparatur oder ein zweiter Lauf, nie beides.
            if _reparierbar(probleme):
                erg, kk, _ = _modell_aufruf(_reparatur_teile(seite, probleme), modell, geo["ar"], groesse)
                kosten += kk
                if erg.size != seite.size:
                    erg = erg.resize(seite.size, Image.LANCZOS)
                ok2, schwer2, probleme2, kp2 = _pruefen(vorlage_seite, erg)
                kosten += kp2
                schritte.append({"stufe": "R", "ok": ok2, "schwer": schwer2, "probleme": probleme2})
                if ok2 or len(probleme2) < len(probleme):
                    bester = (erg, probleme2, ok2)
                break
            feedback_b = "; ".join(probleme)
        if bester is not None:
            seite = bester[0]
        if stufe_a is not None:   # Stufe A weich zurücksetzen – sie ist die geometrisch genauere Fassung
            referenz = seite.crop(stufe_a_box)
            angeglichen = _farben_angleichen(stufe_a, referenz)
            _weich_einsetzen(seite, angeglichen, stufe_a_box,
                             rand=max(24, round(min(stufe_a_box[2] - stufe_a_box[0], stufe_a_box[3] - stufe_a_box[1]) * 0.14)))

        # 4. Stufe C: Wunsch-Pokémon als Bearbeitung des fertigen Bilds (integriert sich besser als beim Malen ins Leere)
        if pokemon and stufen:
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
    except Exception as e:
        # Credits zurückgeben, wenn der Fehler vor bzw. beim Bezahlvorgang auftrat.
        # Hat das Modell dagegen geantwortet und nur kein Bild geliefert (ModellBezahlt),
        # sind die Kosten bereits entstanden – dann wird nicht erstattet.
        erstatten = not isinstance(e, ModellBezahlt)
        con = get_db()
        con.execute("UPDATE artworks SET status='fehler', fehler=? WHERE id=?",
                    (("Kein Bild erzeugt: " if not erstatten else "") + str(e)[:280], artwork_id))
        con.commit()
        con.close()
        if erstatten and row["credits"]:
            _dep["abo"].gutschrift(row["user_id"], row["credits"], "erstattung_fehler", artwork_id)


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

def _artwork_row(artwork_id, user, mit_freigabe=False):
    """Eine Artwork-Zeile holen.

    `mit_freigabe=True` lässt auch durch, wer die Seite übernommen hat — seit es die
    Kunstseiten-Vitrine gibt, gehört eine bezahlte Seite dem Käufer genauso wie dem
    Ersteller. Ohne das könnte er sie zwar im Binder sehen, aber nicht drucken."""
    con = _dep["get_db"]()
    row = con.execute("SELECT * FROM artworks WHERE id = ?", (artwork_id,)).fetchone()
    frei = False
    if row and user and mit_freigabe and row["user_id"] != user["id"]:
        frei = bool(con.execute("SELECT 1 FROM artwork_freigaben WHERE user_id = ? AND artwork_id = ?",
                                (user["id"], artwork_id)).fetchone())
    con.close()
    if not row:
        raise HTTPException(404, "Artwork nicht gefunden")
    if not user or (row["user_id"] != user["id"] and not frei):
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
        "created_at": row["created_at"], "modell": row["modell"], "credits": row.get("credits") or 0,
        "oeffentlich": bool(row.get("oeffentlich")), "titel": row.get("titel") or "",
        "downloads": row.get("downloads") or 0, "verdient": row.get("verdient") or 0,
        "schritte": json.loads(row["schritte"]) if row.get("schritte") else [],
        "vorschau": f"api/artwork/{row['id']}/bild?v=vorschau" if row["status"] == "fertig" else None,
    }


def register(app, *, get_db, current_user, require_user, ist_pro, load_binder, card_image_path,
             dex_image_path, pdf_wasserzeichen, env, CACHE, abo, bestaetigt=None,
             vitrine_pruefen=None):
    _dep.update(bestaetigt=bestaetigt, vitrine_pruefen=vitrine_pruefen,
                get_db=get_db, current_user=current_user, require_user=require_user, ist_pro=ist_pro,
                load_binder=load_binder, card_image_path=card_image_path, dex_image_path=dex_image_path,
                pdf_wasserzeichen=pdf_wasserzeichen, env=env, CACHE=CACHE, abo=abo)

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
                  "ALTER TABLE artworks ADD COLUMN schritte TEXT",
                  "ALTER TABLE artworks ADD COLUMN credits INTEGER DEFAULT 0",
                  # 2026-09-03: Kunstseiten haben einen eigenen Bereich in der Vitrine.
                  # Eine Seite steht dort erst, wenn ihr Ersteller sie ausdrücklich
                  # freigibt — unabhängig davon, ob sein Binder öffentlich ist.
                  "ALTER TABLE artworks ADD COLUMN oeffentlich INTEGER DEFAULT 0",
                  "ALTER TABLE artworks ADD COLUMN titel TEXT",
                  "ALTER TABLE artworks ADD COLUMN downloads INTEGER DEFAULT 0",
                  "ALTER TABLE artworks ADD COLUMN verdient INTEGER DEFAULT 0",
                  "ALTER TABLE artworks ADD COLUMN veroeffentlicht_at TEXT"):
        try:
            con.execute(alter)
        except Exception:
            pass
    # Jobs, die einen Neustart nicht überlebt haben
    # Beim Neustart abgebrochene Jobs: die Credits gehören zurück. Vorher blieb der Nutzer
    # auf 12 bis 32 Credits sitzen, ohne je ein Bild gesehen zu haben.
    offen = con.execute("SELECT id, user_id, COALESCE(credits,0) c FROM artworks"
                        " WHERE status='laeuft'").fetchall()
    con.execute("UPDATE artworks SET status='fehler', fehler='Abgebrochen (Neustart)' WHERE status='laeuft'")
    con.commit()
    con.close()
    for zeile in offen:
        if zeile["c"] and zeile["user_id"]:
            try:
                _dep["abo"].gutschrift(zeile["user_id"], zeile["c"], "erstattung_neustart", zeile["id"])
            except Exception as e:
                print("Rückbuchung nach Neustart fehlgeschlagen:", zeile["id"], e)

    @app.get("/api/artwork/stile")
    def artwork_stile(request: Request):
        user = current_user(request)
        return {"stile": list(STILE.keys()),
                "konto": _dep["abo"].konto_info(_dep["abo"].auffrischen(user)) if user else None,
                "preis_basis": _dep["abo"].ARTWORK_BASIS, "preis_je_karte": _dep["abo"].ARTWORK_JE_KARTE,
                "preis_max": _dep["abo"].ARTWORK_MAX,
                "aktiv": bool(env().get("OPENROUTER_KEY")), "max_pokemon": MAX_POKEMON}

    @app.get("/api/artwork/passung")
    def artwork_passung(request: Request, ids: str = "", slots: str = "", layout: str = "3x3"):
        """Passen diese Karten auf eine Seite? Antwort vor dem Ausgeben der Credits.

        Grundlage sind die Bildmotiv-Daten (Ort, Tageszeit, Wasser) aus `card_art_tags`, dazu der
        gespeicherte Maßstab je Karte und die Lage der Fächer. Kostet nichts: es wird nur gelesen."""
        liste = [x.strip() for x in ids.split(",") if x.strip()][:12]
        faecher = [x.strip() for x in slots.split(",") if x.strip().isdigit()][:12]
        try:
            sp, ze = [int(v) for v in str(layout).split("x")]
        except Exception:
            sp, ze = 3, 3
        if len(liste) < 2:
            return {"wert": None, "gemeinsam": "", "konflikte": [], "karten": []}
        con = get_db()
        marken = ",".join("?" * len(liste))
        namen = {r["id"]: (r["name_de"] or r["name_en"] or r["id"]) for r in con.execute(
            f"SELECT id, name_de, name_en FROM cards WHERE id IN ({marken})", liste)}
        con.close()
        return _passung(liste, namen, slots=faecher, cols=sp, rows=ze)

    @app.post("/api/artwork")
    async def artwork_start(request: Request):
        user = require_user(request)
        # Ohne bestätigte E-Mail keine KI-Seite: das Startguthaben wäre sonst eine
        # kostenlose Seite je erfundener Adresse.
        pruef = _dep.get("bestaetigt")
        if pruef and not pruef(user):
            raise HTTPException(403, detail={"code": "email_offen",
                                             "text": "Bitte bestätige zuerst deine E-Mail-Adresse."})
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
        if not env().get("OPENROUTER_KEY"):
            raise HTTPException(503, "Artwork-Funktion ist nicht eingerichtet")
        if _tageskosten() > TAGESLIMIT_USD:
            raise HTTPException(503, detail={"code": "tageslimit"})
        e = env()
        groesse = e.get("ARTWORK_GROESSE") or STANDARD_GROESSE
        kosten_credits = _preis(len(anker), groesse)
        with _jobs_lock:
            con = get_db()
            laufend = con.execute("SELECT COUNT(*) c FROM artworks WHERE user_id=? AND status='laeuft'",
                                  (user["id"],)).fetchone()["c"]
            con.close()
            if laufend:
                raise HTTPException(409, detail={"code": "artwork_laeuft"})
            artwork_id = secrets.token_urlsafe(9)
            sprache = "en" if (binder.get("options") or {}).get("sprache") == "en" else "de"
            # Erst abbuchen, dann starten – schlägt die Buchung fehl (402), läuft nichts an
            _dep["abo"].abbuchen(user, kosten_credits, "artwork", artwork_id)
            con = get_db()
            con.execute(
                "INSERT INTO artworks (id,user_id,binder_id,seite,layout,anker,stil,wunsch,pokemon,"
                "sprache,modell,groesse,status,credits)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'laeuft',?)",
                (artwork_id, user["id"], binder["id"], seite, layout, json.dumps(anker), stil, wunsch,
                 json.dumps(pokemon), sprache, e.get("ARTWORK_MODELL") or STANDARD_MODELL,
                 groesse, kosten_credits),
            )
            con.commit()
            con.close()
        threading.Thread(target=_job, args=(artwork_id,), daemon=True).start()
        return {"id": artwork_id, "status": "laeuft", "pokemon": pokemon, "credits": kosten_credits}

    @app.get("/api/artwork")
    def artwork_liste(request: Request, binder_id: str = ""):
        user = require_user(request)
        con = get_db()
        rows = con.execute(
            "SELECT * FROM artworks WHERE user_id = ? AND (? = '' OR binder_id = ?) AND status != 'fehler'"
            " ORDER BY created_at DESC LIMIT 60", (user["id"], binder_id, binder_id)).fetchall()
        con.close()
        return {"artworks": [_payload(dict(r)) for r in rows],
                "konto": _dep["abo"].konto_info(user)}

    @app.get("/api/artwork/{artwork_id}")
    def artwork_status(artwork_id: str, request: Request):
        user = require_user(request)
        row = _artwork_row(artwork_id, user)
        out = _payload(row)
        # Nutzer frisch laden – bei einem Fehlschlag wurden die Credits zurückgebucht
        out["konto"] = _dep["abo"].konto_info(current_user(request) or user)
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
            row = _artwork_row(artwork_id, current_user(request), mit_freigabe=True)
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
        row = _artwork_row(artwork_id, user, mit_freigabe=True)
        if row["status"] != "fertig":
            raise HTTPException(404, "Noch nicht fertig")
        lang = row["sprache"] or "de"
        pdf = _pdf(row, bool(mit_karten), lang)
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="artwork-seite-{row["seite"] + 1}.pdf"'})

    @app.post("/api/artwork/{artwork_id}/veroeffentlichen")
    async def artwork_veroeffentlichen(artwork_id: str, request: Request):
        """Eine eigene Kunstseite in die Vitrine stellen — oder wieder herausnehmen.

        Gefragt wird beim Übernehmen ins Fach: „Sollen andere diese Seite laden dürfen?"
        Wer ja sagt, bekommt für jede Übernahme Credits zurück (siehe abo.ARTWORK_ANTEIL).
        Dieselben Regeln wie beim Veröffentlichen eines Binders: Anzeigename, Mindestalter,
        Textprüfung des Titels."""
        user = require_user(request)
        row = _artwork_row(artwork_id, user)
        data = await request.json()
        an = bool(data.get("oeffentlich"))
        titel = str(data.get("titel") or "").strip()[:60]
        pruef = _dep.get("vitrine_pruefen")
        if an:
            if row["status"] != "fertig":
                raise HTTPException(400, "Die Seite ist noch nicht fertig.")
            if pruef:
                await pruef(user, titel)
        con = get_db()
        # Der Titel bleibt, wenn keiner mitkommt: Zurückziehen schickt nur `oeffentlich:
        # false` — vorher löschte das den Titel, und beim nächsten Freigeben stand das Feld
        # wieder leer da.
        con.execute("UPDATE artworks SET oeffentlich = ?, titel = COALESCE(?, titel),"
                    " veroeffentlicht_at = COALESCE(veroeffentlicht_at, datetime('now'))"
                    " WHERE id = ?", (1 if an else 0, titel or None, artwork_id))
        con.commit()
        con.close()
        return {"ok": True, "oeffentlich": an, "titel": titel,
                "preis": _dep["abo"].ARTWORK_FREMD, "anteil": _dep["abo"].ARTWORK_ANTEIL}

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
