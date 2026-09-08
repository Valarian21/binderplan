# Binderplan – Artwork-Seiten per Maske („echtes Outpainting“)
#
# Unterschied zu artwork.py: Dort bekommt das Bildmodell eine Vorlage mit grauen Flächen und
# malt die GANZE Leinwand neu – auch die Kartenfenster; das Original überlebt nur, weil die
# Scans hinterher wieder daraufgelegt werden. Alles zwischen den Karten ist deshalb frei
# interpretiert, nicht fortgesetzt.
#
# Hier bekommt ein Fill-Modell (FLUX.1 Fill [pro] bzw. Stability Inpaint) Bild UND Maske.
# Die schwarzen (unmaskierten) Pixel – die Illustrationsfenster – kommen unverändert zurück,
# gemalt wird nur die weiße Fläche, und zwar so, dass sie an jeder Kante anschließt.
# Ist ein Pokémon am Fensterrand abgeschnitten, wird sein Körper fortgesetzt (gewollt).
#
# Wiederverwendet aus artwork.py: Analyse je Karte (gecacht), Illustrationsfenster,
# Vorlage, Kartenscans einsetzen. Eigene Geometrie: die Seite selbst ist die Leinwand –
# ein Fill-Modell liefert exakt die Eingabegröße, das Seitenverhältnis des Modells spielt
# keine Rolle mehr, nichts wird abgeschnitten oder skaliert.
#
# Anbieter in der .env: ARTWORK_FILL_ANBIETER=bfl|stability, dazu BFL_KEY bzw. STABILITY_KEY.

import base64
import io
import json
import re
import time

import httpx
from PIL import Image, ImageFilter

import artwork as A

LANGE_SEITE = {"1K": 1024, "2K": 2048, "4K": 4096}
BFL_URL = "https://api.bfl.ai/v1/flux-pro-1.0-fill"
STABILITY_URL = "https://api.stability.ai/v2beta/stable-image/edit/inpaint"

# Innenring des Fensters, der mitgemalt wird (Anteil der Fensterkante). Bei Vollbildkarten
# liegen Namensleiste, HP und Attackentext IM Bild bis an den Rand – ohne Innenring zieht
# das Modell die Textleiste nach außen weiter. Der Ring wird später vom Scan bedeckt.
# 06.09.: von 4,5 % auf 0,6 % – der Kartenrand ist im Fensterausschnitt schon abgezogen, und
# die äußersten Artwork-Pixel sind genau die, an denen die Fortsetzung ansetzen muss.
INNENRING_VOLLBILD = 0.006
INNENRING_NORMAL = 0.006

SZENE_PROMPT = (
    "You write the prompt for an inpainting model that will paint the surroundings of finished card "
    "illustrations placed on one page, so that each illustration continues seamlessly outward and the whole "
    "page reads as ONE habitat. Write ONE paragraph, English, at most 110 words, present tense, purely visual: "
    "what the complete picture shows (terrain, water, plants, sky, light direction, time of day, depth), which "
    "single connective structure runs across the page and touches every illustration, and the painting "
    "technique in a few words. No card, frame, text, pocket or grid words. No negations. No creature names "
    "except those given as present."
)


def _geometrie(cols, rows, groesse):
    """Leinwand = Seite. Lange Seite laut Größe, Fächer an ihren echten Positionen."""
    pw, ph = A._seite_mm(cols, rows)
    lang = LANGE_SEITE.get(groesse, 2048)
    skala = lang / max(pw, ph)
    cw, ch = round(pw * skala), round(ph * skala)
    cw -= cw % 16; ch -= ch % 16          # Fill-Modelle wollen Vielfache von 16
    skala = min(cw / pw, ch / ph)
    return {"ar": "seite", "cw": cw, "ch": ch, "skala": skala, "seite": (0, 0, cw, ch)}


def _hat_aufdrucke(analyse):
    """Vollbildkarte: Fenster ≥ 88 % der Karte oder ausdrücklich `aufdrucke`."""
    if not analyse:
        return False
    if analyse.get("aufdrucke") is True:
        return True
    box = analyse.get("box")
    if box:
        ymin, xmin, ymax, xmax = box
        return (xmax - xmin) >= 880 and (ymax - ymin) >= 880
    return False


AUFDRUCK_PROMPT = (
    "You see a scan of a Pokémon trading card whose illustration covers the whole card. List every PRINTED "
    "element lying on top of the artwork as rectangles: the name plate with stage and HP at the top, ability "
    "and attack boxes with their text, the footer line (weakness, resistance, retreat cost), rule boxes "
    "(ex / V / VSTAR rule), set number, rarity mark, illustrator credit, energy symbols. Merge touching "
    "elements into as few rectangles as possible. Make each rectangle TIGHT: exactly the printed plate or "
    "text band, not a pixel of artwork above or below it – when unsure, smaller. The name plate ends where "
    "its background band ends. Do NOT include the creature or the scenery. Answer with JSON only: "
    '{"boxen": [[ymin, xmin, ymax, xmax], ...]} in 0-1000 normalized coordinates of the whole card image.'
)
# Rückfall, wenn das Modell keine brauchbaren Kästen liefert: Namensleiste oben, Textblock unten –
# so sind Vollbildkarten seit Schwert & Schild gebaut.
AUFDRUCK_RUECKFALL = [[0, 0, 110, 1000], [580, 0, 1000, 1000]]


def aufdruck_boxen(card_id, lang):
    """Kästen der Aufdrucke auf einer Vollbildkarte (0-1000, Kartenkoordinaten), einmal je Karte gecacht.
    Diese Flächen werden mitgemalt: das Modell rekonstruiert dort Artwork statt eine Textleiste nach
    außen fortzusetzen. Am Ende deckt der echte Scan sie sowieso wieder ab."""
    get_db = A._dep["get_db"]
    con = get_db()
    con.execute("CREATE TABLE IF NOT EXISTS card_art_aufdrucke (card_id TEXT, lang TEXT, boxen TEXT, created_at TEXT, "
                "PRIMARY KEY (card_id, lang))")
    con.commit()
    row = con.execute("SELECT boxen FROM card_art_aufdrucke WHERE card_id=? AND lang=?", (card_id, lang)).fetchone()
    con.close()
    if row:
        return json.loads(row["boxen"]), 0.0
    bild = A._kartenbild(card_id, lang)
    if bild is None:
        return AUFDRUCK_RUECKFALL, 0.0
    klein = bild.copy(); klein.thumbnail((1024, 1024))
    boxen, kosten = [], 0.0
    try:
        d = A._openrouter({
            "model": A._dep["env"]().get("ARTWORK_ANALYSE_MODELL") or A.ANALYSE_MODELL,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": AUFDRUCK_PROMPT},
                {"type": "image_url", "image_url": {"url": A._data_url(klein, "JPEG")}}]}],
            "response_format": {"type": "json_object"}, "usage": {"include": True},
        }, timeout=90)
        text = (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        for b in (json.loads(text).get("boxen") or []):
            if isinstance(b, list) and len(b) == 4:
                ymin, xmin, ymax, xmax = [max(0, min(1000, float(v))) for v in b]
                if ymax - ymin >= 15 and xmax - xmin >= 15:
                    boxen.append([ymin, xmin, ymax, xmax])
        kosten = float((d.get("usage") or {}).get("cost") or 0)
    except Exception:
        boxen = []
    if not boxen:
        boxen = AUFDRUCK_RUECKFALL
    con = get_db()
    con.execute("INSERT OR REPLACE INTO card_art_aufdrucke (card_id, lang, boxen, created_at) VALUES (?,?,?,?)",
                (card_id, lang, json.dumps(boxen), A._now()))
    con.commit(); con.close()
    return boxen, kosten


def maske_bauen(geo, fenster, analysen, anker, cols, lang, aufdrucke_mit=True):
    """Weiß = malen, Schwarz = behalten. Behalten wird nur die reine Illustration: jedes Fenster um den
    Innenring geschrumpft, und bei Vollbildkarten zusätzlich die Aufdruck-Kästen freigegeben."""
    from PIL import ImageDraw
    m = Image.new("L", (geo["cw"], geo["ch"]), 255)
    d = ImageDraw.Draw(m)
    kosten, boxen_je_karte = 0.0, {}
    for slot, (x0, y0, x1, y1) in fenster.items():
        voll = _hat_aufdrucke(analysen.get(anker[slot]))
        r = INNENRING_VOLLBILD if voll else INNENRING_NORMAL
        dx, dy = round((x1 - x0) * r), round((y1 - y0) * r)
        d.rectangle((x0 + dx, y0 + dy, x1 - dx - 1, y1 - dy - 1), fill=0)
        if voll and aufdrucke_mit:
            boxen, k = aufdruck_boxen(anker[slot], lang)
            kosten += k
            boxen_je_karte[slot] = boxen
            fx0, fy0, fx1, fy1 = A._fach_box(int(slot), cols, geo)     # Kästen sind Kartenkoordinaten
            fw, fh = fx1 - fx0, fy1 - fy0
            for ymin, xmin, ymax, xmax in boxen:
                rand = 0.004
                d.rectangle((fx0 + round((xmin / 1000 - rand) * fw), fy0 + round((ymin / 1000 - rand) * fh),
                             fx0 + round((xmax / 1000 + rand) * fw), fy0 + round((ymax / 1000 + rand) * fh)), fill=255)
    return m, kosten, boxen_je_karte


def _fuellung(vorlage, maske):
    """Was unter der Maske steht, ist dem Modell egal – ein weicher Farbverlauf aus den
    Fensterfarben ist trotzdem ein besserer Start als Grau (weniger Neigung zu grauem Dunst)."""
    klein = vorlage.resize((max(8, vorlage.width // 32), max(8, vorlage.height // 32)), Image.BOX)
    # Grau (128) durch den Mittelwert der Fenster ersetzen, dann glätten und hochziehen
    px = [p for p in klein.getdata() if p != (128, 128, 128)]
    if not px:
        return vorlage
    mittel = tuple(sum(c[i] for c in px) // len(px) for i in range(3))
    basis = Image.new("RGB", vorlage.size, mittel)
    grob = vorlage.resize(klein.size, Image.BOX).resize(vorlage.size, Image.BILINEAR).filter(ImageFilter.GaussianBlur(24))
    basis = Image.blend(basis, grob, 0.5)
    out = basis.copy()
    out.paste(vorlage, (0, 0), mask=maske.point(lambda v: 255 - v))
    return out


def szene_prompt(anker, cols, rows, namen, analysen, wunsch, stil):
    """Ein kurzer Szenenabsatz für das Fill-Modell (Textmodell, ≈ 0,5 ct)."""
    quellen = []
    for slot, cid in sorted(anker.items(), key=lambda kv: int(kv[0])):
        a = analysen.get(cid) or {}
        col, row = int(slot) % cols, int(slot) // cols
        quellen.append(f"- {namen.get(cid, cid)} at row {row + 1}/column {col + 1}: scene {a.get('scene')}; edges "
                       f"{json.dumps(a.get('edges'), ensure_ascii=False)}; horizon {a.get('horizon')}; light {a.get('light')}; "
                       f"palette {a.get('palette')}; technique {a.get('technique')}")
    text = SZENE_PROMPT + f"\n\nPage grid {cols} columns × {rows} rows. Present creatures (already painted, may continue " \
           f"where cut off): {', '.join(dict.fromkeys(namen.values()))}.\nSources:\n" + "\n".join(quellen)
    if wunsch:
        text += f"\nCollector's wish: {wunsch[:300]}"
    if stil and stil != "karte":
        text += f"\nRendering technique requested: {A.STILE.get(stil, '')}"
    try:
        d = A._openrouter({
            "model": A._dep["env"]().get("ARTWORK_REGIE_MODELL") or A.ANALYSE_MODELL,
            "messages": [{"role": "user", "content": text}],
            "usage": {"include": True},
        }, timeout=90)
        absatz = ((d.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()
        return absatz[:900], float((d.get("usage") or {}).get("cost") or 0)
    except Exception:
        return "", 0.0


def _b64(img, fmt="PNG"):
    buf = io.BytesIO()
    img.save(buf, fmt)
    return base64.b64encode(buf.getvalue()).decode()


def _bfl(bild, maske, prompt, key, log):
    """FLUX.1 Fill [pro]: Auftrag anlegen, Ergebnis abholen. Weiß in der Maske = malen."""
    r = httpx.post(BFL_URL, headers={"x-key": key, "Content-Type": "application/json"}, timeout=60, json={
        "image": _b64(bild, "JPEG" if False else "PNG"), "mask": _b64(maske), "prompt": prompt,
        "steps": 50, "guidance": 60, "output_format": "png", "safety_tolerance": 2, "prompt_upsampling": False,
    })
    if r.status_code != 200:
        raise RuntimeError(f"BFL {r.status_code}: {r.text[:300]}")
    d = r.json()
    poll = d.get("polling_url") or f"https://api.bfl.ai/v1/get_result?id={d['id']}"
    for _ in range(120):
        time.sleep(2)
        s = httpx.get(poll, headers={"x-key": key}, timeout=30).json()
        st = s.get("status")
        if st == "Ready":
            url = (s.get("result") or {}).get("sample")
            roh = httpx.get(url, timeout=120).content
            return Image.open(io.BytesIO(roh)).convert("RGB"), 0.05
        if st in ("Error", "Failed", "Content Moderated", "Request Moderated"):
            raise RuntimeError(f"BFL: {st} {json.dumps(s)[:300]}")
        log and log(f"BFL: {st}")
    raise RuntimeError("BFL: keine Antwort in 4 Minuten")


def _stability(bild, maske, prompt, key, log):
    """Stability Inpaint: multipart, Weiß in der Maske = ersetzen, 3 Credits."""
    ib = io.BytesIO(); bild.save(ib, "PNG")
    mb = io.BytesIO(); maske.save(mb, "PNG")
    r = httpx.post(STABILITY_URL, headers={"Authorization": f"Bearer {key}", "Accept": "image/*"}, timeout=240,
                   files={"image": ("bild.png", ib.getvalue(), "image/png"), "mask": ("maske.png", mb.getvalue(), "image/png")},
                   data={"prompt": prompt[:2000], "output_format": "png", "grow_mask": "8"})
    if r.status_code != 200:
        raise RuntimeError(f"Stability {r.status_code}: {r.text[:300]}")
    return Image.open(io.BytesIO(r.content)).convert("RGB"), 0.03


def fuellen(bild, maske, prompt, log=None):
    env = A._dep["env"]()
    anbieter = (env.get("ARTWORK_FILL_ANBIETER") or ("bfl" if env.get("BFL_KEY") else "stability")).lower()
    if anbieter == "bfl":
        if not env.get("BFL_KEY"):
            raise RuntimeError("BFL_KEY fehlt in der .env")
        return _bfl(bild, maske, prompt, env["BFL_KEY"], log) + ("bfl/flux-pro-1.0-fill",)
    if not env.get("STABILITY_KEY"):
        raise RuntimeError("STABILITY_KEY fehlt in der .env")
    return _stability(bild, maske, prompt, env["STABILITY_KEY"], log) + ("stability/inpaint",)


def seite_malen(anker, cols, rows, lang="de", stil="karte", wunsch="", groesse="2K", log=None, nur_vorbereiten=False):
    """→ dict mit seite (PIL), vorlage, maske, prompt, kosten, schritte. Ohne DB-Eintrag, ohne Credits –
    die Abrechnung bleibt beim Aufrufer (artwork.py-Job oder Testskript)."""
    get_db = A._dep["get_db"]
    geo = _geometrie(cols, rows, groesse)
    analysen, namen, kosten, schritte = {}, {}, 0.0, [{"modus": "maske", "leinwand": [geo["cw"], geo["ch"]]}]
    con = get_db()
    for cid in dict.fromkeys(anker.values()):
        r = con.execute("SELECT name_de, name_en FROM cards WHERE id = ?", (cid,)).fetchone()
        if r:
            namen[cid] = r["name_en"] or r["name_de"]
        a = A._analyse(cid, lang)
        analysen[cid] = a
        kosten += float((a or {}).get("_kosten") or 0)
    con.close()
    vorlage, fenster = A._vorlage(anker, cols, rows, geo, lang, analysen)
    maske, km, aufdrucke = maske_bauen(geo, fenster, analysen, anker, cols, lang)
    kosten += km
    schritte.append({"aufdrucke": aufdrucke, "kosten": round(km, 4)})
    eingabe = _fuellung(vorlage, maske)
    prompt, kp = szene_prompt(anker, cols, rows, namen, analysen, wunsch, stil)
    kosten += kp
    schritte.append({"szene": prompt, "kosten": round(kp, 4)})
    log and log(f"Szene: {prompt[:200]}…")
    out = {"vorlage": vorlage, "eingabe": eingabe, "maske": maske, "prompt": prompt, "fenster": fenster,
           "geo": geo, "kosten": kosten, "schritte": schritte, "seite": None}
    if nur_vorbereiten:
        return out
    seite, kb, modell = fuellen(eingabe, maske, prompt, log)
    kosten += kb
    schritte.append({"stufe": "fill", "modell": modell, "kosten": kb})
    if seite.size != (geo["cw"], geo["ch"]):
        seite = seite.resize((geo["cw"], geo["ch"]), Image.LANCZOS)
    # Fenster pixelgenau zurück (der Innenring wurde mitgemalt), dann die echten Scans darüber
    seite.paste(vorlage, (0, 0), mask=maske.point(lambda v: 255 - v))
    ohne_karten = seite.copy()
    A._karten_einsetzen(seite, anker, cols, geo, lang)
    out["ohne_karten"] = ohne_karten
    out.update(seite=seite, kosten=kosten, schritte=schritte, modell=modell)
    return out
