"""Binder aus Fotos: Seite fotografieren, Karten erkennen, Binder anlegen.

Das Modell sagt, WO die Seite liegt und WAS auf den Karten steht; WELCHE Karte es ist,
entscheidet der Bildabgleich. Diese Arbeitsteilung ist gemessen: Vision-Modelle raten
Sammelnummern, ein Bildfingerabdruck trifft sie nicht — zusammen sind sie stark.

Ablauf je Foto (ein einziger Modellaufruf, ≈ 0,15 ct):
  1. `_seite_lesen` — Raster, die vier Ecken der Kartenflaeche, Name und Nummer je Fach.
  2. `entzerren` — die vier Ecken auf ein gerades Rechteck; jedes Binderfoto ist schraeg,
     und ohne diesen Schritt sind die aeusseren Faecher unbrauchbar verzogen.
  3. `_linien` — die Faecher dort schneiden, wo die Karten wirklich sitzen (gemessen am
     Helligkeitsprofil), statt die Seite stur in gleiche Teile zu zerlegen.
  4. `karte_zuschneiden` — in jedem Fach die Karte vom Huellenhintergrund freistellen.
  5. `_erkennen` — dHash (Form) plus Farbsignatur (Palette) gegen 31.590 Karten; der
     gelesene Name, die Sammelnummer und die Setgroesse hinter dem „/" geben Abzuege,
     die Region der Seite entscheidet zwischen westlichem und japanischem Druck.

Die erste Fassung fragte das Modell nach neun einzelnen Kaestchen und verglich nur den
dHash. Gemessen an einem Testfoto: 0 von 9 Karten landeten im richtigen Fach (die Boxen
kamen verschoben zurueck), beim Nutzer 1 von 9. Mit dem Verfahren oben sind es 36 von 45
ueber fuenf Testseiten — 8 von 9 bei normal fotografierten Seiten, weniger nur bei stark
schraegen Aufnahmen und bei japanischen Karten, deren Drucke sich das Motiv teilen.
"""

import base64
import io
import json
import re
import threading
import time

import httpx
from fastapi import HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool
from PIL import Image, ImageFilter, ImageOps

_dep = {}

BOX_MODELL = "google/gemini-2.5-flash"     # Geometrie + Namen, ein Aufruf je Foto
KANTE = 16                                  # dHash-Raster → 256 Bit
FARB_X, FARB_Y = 8, 11                      # Farbsignatur: 88 Bloecke im Kartenformat
MAX_FOTO = 1500                             # Kantenlaenge, mit der das Modell arbeitet
# „Sicher" entscheidet nicht die Punktzahl, sondern der Abstand zum Zweiten: gemessen an
# einem Testfoto liegen richtige Treffer bei 82–134 Punkten, falsche bei 91–131 — die Zahl
# allein trennt nichts. Der Vorsprung trennt sauber (richtig 8–57, falsch 0–4).
GUT = 150                                   # Obergrenze, ab der auch ein Vorsprung nichts mehr rettet
VORSPRUNG = 12                              # Mindestabstand zum zweitbesten Treffer
NAME_BONUS = 28                             # Abzug, wenn der gelesene Kartenname passt
NR_BONUS = 30                               # Abzug, wenn die gelesene Sammelnummer passt
NENNER_BONUS = 20                           # Abzug, wenn die Setgröße hinter dem „/" passt
REGION_MALUS = 25                           # Aufschlag für die Region, die auf der Seite in der Minderheit ist
ZELL_B, ZELL_H = 320, 447                   # entzerrte Zelle (Kartenformat 63:88)
FARB_GEWICHT = 0.8                          # so stark zaehlt die Farbsignatur neben dem dHash
VORAUSWAHL = 60                             # so viele dHash-Kandidaten gehen in die Farbrunde

# Ein einziger Modellaufruf je Foto, und er fragt nicht nach neun Kaestchen, sondern nach
# der Geometrie der ganzen Seite plus dem, was auf den Karten steht. Neun Boxen einzeln
# zu erfragen war der Fehler der ersten Fassung: die Kaestchen kamen verschoben zurueck,
# die Ausschnitte enthielten Nachbarkarten, und die Zuordnung landete bei 0 von 9 (die
# Karten wurden erkannt, nur im falschen Fach). Vier Eckpunkte trifft dasselbe Modell
# zuverlaessig, und aus ihnen wird die Seite entzerrt und sauber in Faecher geteilt.
SEITE_PROMPT = (
    "This is a photo of ONE page of a trading card binder holding a regular grid of cards.\n"
    "Return JSON only:\n"
    '{"spalten":3,"reihen":3,"ecken":[[x,y],[x,y],[x,y],[x,y]],"karten":[{"name":"","nummer":""}]}\n'
    "- spalten/reihen: the grid you actually see.\n"
    "- ecken: the four corners of the RECTANGLE that tightly encloses ALL cards, in the order "
    "top-left, top-right, bottom-right, bottom-left, each as [x,y] in 0-1000 coordinates of the image "
    "(x = horizontal). Follow the card block even if the photo is taken at an angle.\n"
    "- karten: one entry per pocket in reading order (left to right, top to bottom). "
    "name = the printed card name; nummer = the collector number printed at the bottom, copied exactly "
    "as shown, including any letters and the total after the slash. Empty strings for an empty pocket."
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


def farb_signatur(img, bx=FARB_X, by=FARB_Y):
    """Grobes Farbraster der Karte als Bytes.

    Der dHash sieht nur Helligkeitskanten und verwechselt deshalb zwei Drucke desselben
    Pokémon (gemessen: Meloetta xy8-85 gegen bw11-78 — dHash 130 zu 117 fuer den
    falschen, Farbabstand 18 zu 37 fuer den richtigen). Die Farbe entscheidet solche
    Faelle; zusammen sind beide deutlich staerker als jedes allein."""
    return bytes(v for px in img.convert("RGB").resize((bx, by), Image.LANCZOS).getdata() for v in px)


def _farb_abstand(a, b):
    """Mittlere Abweichung je Kanal (0-255). Ohne numpy, aber nur ueber ~260 Werte."""
    if not a or not b or len(a) != len(b):
        return 255.0
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def _loesen(A, b):
    """Gauss mit Spaltenpivot fuer kleine Systeme — numpy gibt es im Dienst nicht."""
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for s in range(n):
        p = max(range(s, n), key=lambda r: abs(M[r][s]))
        if abs(M[p][s]) < 1e-12:
            return None
        M[s], M[p] = M[p], M[s]
        for r in range(n):
            if r == s:
                continue
            f = M[r][s] / M[s][s]
            if f:
                for c in range(s, n + 1):
                    M[r][c] -= f * M[s][c]
    return [M[i][n] / M[i][i] for i in range(n)]


def entzerren(img, ecken, breite, hoehe):
    """Die vier Eckpunkte der Kartenflaeche → gerades Rechteck.

    Ein Foto vom Binder ist immer leicht schraeg; ohne Entzerrung sind die aeusseren
    Faecher am staerksten verzogen, und genau dort riss der Abgleich ab."""
    ziel = [(0, 0), (breite, 0), (breite, hoehe), (0, hoehe)]
    A, b = [], []
    for (X, Y), (x, y) in zip(ziel, ecken):        # Ziel → Quelle: PIL braucht die Rueckabbildung
        A.append([X, Y, 1, 0, 0, 0, -x * X, -x * Y]); b.append(x)
        A.append([0, 0, 0, X, Y, 1, -y * X, -y * Y]); b.append(y)
    k = _loesen(A, b)
    if not k:
        return None
    try:
        return img.transform((breite, hoehe), Image.PERSPECTIVE, k, Image.BICUBIC)
    except Exception:
        return None


def karte_zuschneiden(zelle):
    """In einem Fach die Karte selbst finden: alles, was sich vom Huellen-Hintergrund
    abhebt. Die Farbe am Zellrand ist der Hintergrund — das gilt fuer schwarze Huellen
    genauso wie fuer weisse Seiten."""
    b, h = zelle.size
    g = zelle.convert("L").filter(ImageFilter.GaussianBlur(1))
    px = g.load()
    rb, rh = max(2, int(b * 0.03)), max(2, int(h * 0.03))
    rand = ([px[x, y] for y in range(rh) for x in range(0, b, 3)]
            + [px[x, h - 1 - y] for y in range(rh) for x in range(0, b, 3)]
            + [px[x, y] for x in range(rb) for y in range(0, h, 3)]
            + [px[b - 1 - x, y] for x in range(rb) for y in range(0, h, 3)])
    if not rand:
        return zelle
    rand.sort()
    hintergrund = rand[len(rand) // 2]
    spalten = [sum(1 for y in range(0, h, 4) if abs(px[x, y] - hintergrund) > 28) / max(1, h / 4)
               for x in range(0, b, 2)]
    zeilen = [sum(1 for x in range(0, b, 4) if abs(px[x, y] - hintergrund) > 28) / max(1, b / 4)
              for y in range(0, h, 2)]

    def spanne(werte, schritt, laenge):
        an = [i for i, v in enumerate(werte) if v > 0.45]
        if len(an) < 3:
            return 0, laenge
        return max(0, an[0] * schritt), min(laenge, (an[-1] + 1) * schritt)

    x0, x1 = spanne(spalten, 2, b)
    y0, y1 = spanne(zeilen, 2, h)
    if x1 - x0 < b * 0.5 or y1 - y0 < h * 0.5:
        return zelle
    return zelle.crop((x0, y0, x1, y1))


def _profil(bild, achse):
    """Wie „kartig" jede Spalte (achse=0) bzw. Zeile ist: Anteil der Punkte, die sich vom
    Rand-Hintergrund abheben."""
    g = bild.convert("L")
    b, h = g.size
    px = g.load()
    ecke = [px[x, y] for x in (1, b - 2) for y in range(0, h, max(1, h // 40))]
    ecke.sort()
    hg = ecke[len(ecke) // 2] if ecke else 0
    aus = []
    if achse == 0:
        for x in range(0, b, 2):
            aus.append(sum(1 for y in range(0, h, 6) if abs(px[x, y] - hg) > 26) / max(1, h / 6))
    else:
        for y in range(0, h, 2):
            aus.append(sum(1 for x in range(0, b, 6) if abs(px[x, y] - hg) > 26) / max(1, b / 6))
    return aus


def _linien(profil, anzahl, laenge, schritt=2):
    """Die `anzahl` breitesten zusammenhängenden Bereiche im Profil → Grenzen in Pixeln.

    Damit werden die Fächer dort geschnitten, wo die Karten wirklich sitzen, statt die
    Seite stur in gleiche Teile zu zerlegen — das fängt Eckpunkte ab, die ein paar Prozent
    danebenliegen."""
    laeufe, start = [], None
    for i, v in enumerate(profil):
        if v > 0.5 and start is None:
            start = i
        elif v <= 0.5 and start is not None:
            laeufe.append((start, i))
            start = None
    if start is not None:
        laeufe.append((start, len(profil)))
    laeufe = [l for l in laeufe if (l[1] - l[0]) * schritt > laenge / (anzahl * 4)]
    if len(laeufe) != anzahl:
        return None
    return [(a * schritt, min(laenge, b * schritt)) for a, b in laeufe]


def _abstand(a, b):
    return bin(a ^ b).count("1")


_vorrat = {"stand": 0.0, "ids": [], "werte": [], "farben": [], "platz": {}}
_vorrat_sperre = threading.Lock()


def _vorrat_laden(erzwingen=False):
    """Alle Fingerabdruecke im Speicher halten — 23.000 Zahlen sind nichts, und
    jeder Vergleich muss gegen den ganzen Katalog laufen."""
    with _vorrat_sperre:
        if not erzwingen and _vorrat["ids"] and time.time() - _vorrat["stand"] < 600:
            return
        con = _dep["get_db"]()
        reihen = con.execute("SELECT card_id, hash, farbe FROM card_hashes WHERE hash <> ''").fetchall()
        con.close()
        _vorrat["ids"] = [r["card_id"] for r in reihen]
        _vorrat["werte"] = [int(r["hash"], 16) for r in reihen]
        _vorrat["farben"] = [bytes.fromhex(r["farbe"]) if r["farbe"] else None for r in reihen]
        _vorrat["platz"] = {c: i for i, c in enumerate(_vorrat["ids"])}
        _vorrat["stand"] = time.time()


def _suchen(hash_wert, anzahl=25, farbe=None, nur=None):
    """→ [(punkte, card_id), ...] aufsteigend.

    Zwei Stufen: der dHash laeuft als ganzzahliger XOR ueber den ganzen Katalog und
    liefert eine Vorauswahl, die Farbsignatur entscheidet darin. Ist eine Kandidatenmenge
    bekannt (gelesener Kartenname), wird nur in ihr gesucht — dann traegt die Farbe von
    Anfang an mit."""
    _vorrat_laden()
    ids, werte, farben = _vorrat["ids"], _vorrat["werte"], _vorrat["farben"]
    if nur:
        stellen = [_vorrat["platz"][c] for c in nur if c in _vorrat["platz"]]
    else:
        roh = sorted(zip((_abstand(hash_wert, w) for w in werte), range(len(ids))))[:VORAUSWAHL]
        stellen = [i for _d, i in roh]
    if not stellen:
        return []
    aus = []
    for i in stellen:
        d = _abstand(hash_wert, werte[i])
        if farbe is not None and farben[i]:
            d += _farb_abstand(farbe, farben[i]) * FARB_GEWICHT
        aus.append((round(d, 1), ids[i]))
    return sorted(aus)[:anzahl]


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
    # Karten werden vor der Arbeit mit leerem Hash vorgemerkt, damit zwei Spuren nicht
    # dieselben ziehen. Ein Neustart mitten im Lauf laesst solche Zeilen zurueck — sie
    # gelten sonst als erledigt und blieben fuer immer leer.
    con = _dep["get_db"]()
    con.execute("DELETE FROM card_hashes WHERE hash = ''")
    con.commit()
    con.close()
    from concurrent.futures import ThreadPoolExecutor
    entnahme = threading.Lock()
    schreiben = threading.Lock()

    def eine(row):
        # Kleines Bild reicht: der Hash rastert ohnehin auf 17x16 herunter, und
        # low.webp laedt ein Vielfaches schneller als high.webp. Karten ohne TCGdex-Scan
        # (die japanischen Altbestaende) haben ihr Bild in `image_alt` — dieselbe
        # Zweitquelle, aus der auch die Kartenansicht es holt.
        urls = [f"{row[feld]}/low.webp" for feld in ("image_de", "image_en") if row[feld]]
        alt = row["image_alt"] if "image_alt" in row.keys() else None
        if alt:
            urls.append(alt.replace("product-images.tcgplayer.com/",
                                    "product-images.tcgplayer.com/fit-in/437x437/")
                        if "product-images.tcgplayer.com" in alt else alt)
        for u in urls:
            img = _bild_holen(u)
            if img:
                return f"{dhash(img):064x}|{farb_signatur(img).hex()}"
        return ""

    def spur():
        while not _hashlauf["stop"] and _hashlauf["marke"] == marke:
            with entnahme:
                con = _dep["get_db"]()
                rows = con.execute(
                    "SELECT c.id, c.image_de, c.image_en, c.image_alt FROM cards c"
                    " LEFT JOIN card_hashes h ON h.card_id = c.id"
                    " WHERE h.card_id IS NULL"
                    " AND COALESCE(c.image_de, c.image_en, c.image_alt) IS NOT NULL"
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
                con.executemany("UPDATE card_hashes SET hash=?, farbe=?, created_at=datetime('now')"
                                " WHERE card_id=?",
                                [(w.split("|")[0] if w else "", (w.split("|")[1] if "|" in w else None), cid)
                                 for w, cid in werte])
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


def _seite_lesen(img):
    """Ein Aufruf: Raster, die vier Ecken der Kartenflaeche, Name und Nummer je Fach."""
    klein = img.copy()
    klein.thumbnail((MAX_FOTO, MAX_FOTO))
    r = httpx.post("https://openrouter.ai/api/v1/chat/completions",
                   headers={"Authorization": f"Bearer {_key()}", "HTTP-Referer": "https://binderplan.app",
                            "X-Title": "Binderplan"},
                   json={"model": _dep["env"]().get("FOTO_MODELL") or BOX_MODELL,
                         "messages": [{"role": "user", "content": [
                             {"type": "text", "text": SEITE_PROMPT},
                             {"type": "image_url", "image_url": {"url": _data_url(klein)}}]}],
                         "response_format": {"type": "json_object"}, "max_tokens": 2000,
                         "usage": {"include": True}}, timeout=120)
    d = r.json()
    if r.status_code != 200 or d.get("error"):
        raise HTTPException(502, f"Bilderkennung: {(d.get('error') or {}).get('message') or r.status_code}")
    txt = re.sub(r"^```(?:json)?|```$", "",
                 ((d.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip(),
                 flags=re.M).strip()
    try:
        erg = json.loads(txt)
    except Exception:
        raise HTTPException(502, "Die Bilderkennung hat unlesbar geantwortet")
    ecken = []
    for p in (erg.get("ecken") or []):
        if isinstance(p, (list, tuple)) and len(p) == 2:
            try:
                ecken.append((max(0.0, min(1000.0, float(p[0]))), max(0.0, min(1000.0, float(p[1])))))
            except Exception:
                pass
    karten = []
    for e in (erg.get("karten") or []):
        if isinstance(e, dict):
            karten.append({"name": str(e.get("name") or "")[:60], "nummer": str(e.get("nummer") or "")[:20]})
        else:
            karten.append({"name": str(e or "")[:60], "nummer": ""})
    def zahl(x, klein_, gross):
        try:
            return max(klein_, min(gross, int(x)))
        except Exception:
            return 0
    return ({"ecken": ecken if len(ecken) == 4 else [],
             "spalten": zahl(erg.get("spalten"), 1, 8), "reihen": zahl(erg.get("reihen"), 1, 8),
             "karten": karten},
            float((d.get("usage") or {}).get("cost") or 0))


# Namen und Nummern, die das Modell liest, engen die Suche ein: „Meloetta" hat zwoelf
# Drucke, aber der Katalog hat 33.000 Karten. Der Name kommt aus dem Register, das beim
# ersten Aufruf gebaut und danach im Speicher gehalten wird.
_namen = {"stand": 0.0, "reg": {}, "karte": {}}
_namen_sperre = threading.Lock()


def _namen_laden(erzwingen=False):
    with _namen_sperre:
        if not erzwingen and _namen["reg"] and time.time() - _namen["stand"] < 3600:
            return
        con = _dep["get_db"]()
        reihen = con.execute(
            "SELECT c.id, c.name_de, c.name_en, c.name_ja, c.local_id, c.region,"
            " COALESCE(s.official, s.total) AS gesamt FROM cards c"
            " LEFT JOIN sets s ON s.id = c.set_id").fetchall()
        con.close()
        reg, karte = {}, {}
        for r in reihen:
            for n in (r["name_de"], r["name_en"], r["name_ja"]):
                if n:
                    reg.setdefault(_schluessel(n), []).append(r["id"])
            karte[r["id"]] = (_nummer_schluessel(r["local_id"]), r["gesamt"] or 0,
                              (r["region"] or "intl"))
        _namen.update(reg=reg, karte=karte, stand=time.time())


def _schluessel(text):
    """Namen vergleichbar machen — auch japanische.

    Die erste Fassung warf alles außer a-z0-9 weg; bei „エンテイGX" blieb davon nichts
    übrig, und die Namenssuche lief auf einer japanischen Seite komplett ins Leere."""
    return re.sub(r"[\W_]", "", str(text or "").lower(), flags=re.UNICODE)


def _nummer_schluessel(text):
    """„097/193" → „97"; führende Nullen und der Nenner fliegen raus."""
    n = str(text or "").split("/")[0].strip().upper()
    n = re.sub(r"[^A-Z0-9]", "", n)
    return (n.lstrip("0") or n).lower()


def _kandidaten(name):
    """Karten, die so heissen. → set oder None (dann wird der ganze Katalog gesucht)."""
    if not name:
        return None
    _namen_laden()
    k = _schluessel(name)
    if not k:
        return None
    ids = set(_namen["reg"].get(k, []))
    if not ids:
        # Gelesen wird oft nur ein Teil („Glurak ex" gegen „Glurak-ex", „Mimigma V")
        for nk, liste in _namen["reg"].items():
            if (k in nk or nk in k) and abs(len(nk) - len(k)) <= 6:
                ids.update(liste)
    return ids or None


def _erkennen(zelle, name="", nummer=""):
    """Ein entzerrtes Fach → bester Kartentreffer.

    Reihenfolge: Karte im Fach freistellen, dann Name als Filter, dann dHash + Farbe,
    zuletzt die gelesene Sammelnummer als Stichentscheid unter gleichnamigen Karten."""
    aus = karte_zuschneiden(zelle)
    if aus.size[0] < 20 or aus.size[1] < 20:
        return None
    # Der gelesene Name schließt nichts aus, er belohnt nur: liest das Modell „Bianca's
    # Devotion" statt „Glalie", darf das die richtige Karte nicht aus dem Rennen werfen.
    # Deshalb laufen beide Suchen — der ganze Katalog und die Namensgleichen — und werden
    # zusammengelegt.
    kand = _kandidaten(name)
    bestes = None
    for dreh in (0, 180):
        probe = aus if dreh == 0 else aus.rotate(180)
        w, f = dhash(probe), farb_signatur(probe)
        treffer = _suchen(w, 25, f)
        if kand:
            gefunden = {c for _p, c in treffer}
            treffer += [t for t in _suchen(w, 25, f, kand) if t[1] not in gefunden]
            treffer.sort()
        if treffer and (bestes is None or treffer[0][0] < bestes[0][0][0]):
            bestes = (treffer, dreh, probe)
    if not bestes:
        return None
    treffer, dreh, probe = bestes
    _namen_laden()
    # Die gelesene Sammelnummer trägt zweimal: der Zähler nennt die Karte, der Nenner die
    # Größe des Sets. Der Nenner ist die größere, ruhigere Zahl auf der Karte und wird
    # zuverlässiger gelesen — er entscheidet zwischen zwei Drucken derselben Karte auch
    # dann noch, wenn der Zähler verlesen wurde („127/191" statt „137/191").
    zaehler = _nummer_schluessel(nummer)
    nenner = 0
    m = re.search(r"/\s*(\d{1,4})", nummer or "")
    if m:
        nenner = int(m.group(1))
    bewertet = []
    for punkte, cid in treffer:
        nr, gesamt, region = _namen["karte"].get(cid, ("", 0, "intl"))
        passt_name = bool(kand and cid in kand)
        if passt_name:
            punkte -= NAME_BONUS
        # Nummer und Setgröße zählen nur, wenn auch der Name passt — oder wenn gar kein
        # Name gelesen wurde. Sonst gewinnt eine verlesene Nummer gegen den richtigen
        # Namen: „Lickitung, 78/108" holte die japanische Flunkifer-Karte Nummer 78 aus
        # einem Set mit 108 Karten nach vorn, obwohl der Name nicht einmal ähnlich war.
        zaehlt = passt_name or not kand
        if zaehlt and zaehler and nr == zaehler:
            punkte -= NR_BONUS if passt_name else NR_BONUS // 2
        if zaehlt and nenner and gesamt and abs(gesamt - nenner) <= 1:
            punkte -= NENNER_BONUS if passt_name else NENNER_BONUS // 2
        bewertet.append((punkte, cid, region))
    bewertet.sort()
    return {"treffer": [(p, c) for p, c, _r in bewertet[:6]],
            "regionen": {c: r for _p, c, r in bewertet[:6]},
            "abstand": round(bewertet[0][0]),
            "vorsprung": round(bewertet[1][0] - bewertet[0][0]) if len(bewertet) > 1 else 99,
            "dreh": dreh, "aus": probe}


def register(app, *, get_db, current_user, require_user, env, CACHE, admin_key):
    _dep.update(get_db=get_db, current_user=current_user, require_user=require_user, env=env,
                CACHE=CACHE, admin_key=admin_key)

    con = get_db()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS card_hashes (
            card_id TEXT PRIMARY KEY, hash TEXT, created_at TEXT
        );
    """)
    try:
        con.execute("ALTER TABLE card_hashes ADD COLUMN farbe TEXT")
    except Exception:
        pass
    con.commit()
    con.close()

    def _kontingent(request, kosten=1):
        """Der Import kostet fast nichts, ist aber ein offener Endpunkt — deshalb
        eine Tagesgrenze je Konto bzw. je Adresse."""
        user = current_user(request)
        kennung = str(user["id"]) if user else (request.headers.get("x-real-ip", "").strip() or (request.client.host if request.client else "?"))
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
        gesamt = con.execute("SELECT COUNT(*) n FROM cards"
                             " WHERE COALESCE(image_de,image_en,image_alt) IS NOT NULL").fetchone()["n"]
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
        return await run_in_threadpool(_foto_auswerten, img, layout, raster)

    def _foto_auswerten(img, layout, raster):
        """Ein Modellaufruf (Geometrie + gelesene Namen), dann rein lokal: Seite
        entzerren, in Fächer teilen, je Fach die Karte freistellen und über dHash +
        Farbsignatur zuordnen. Läuft im Threadpool, damit der Dienst antwortbereit bleibt."""
        kosten = 0.0
        cols, rows = ((int(x) for x in (layout.split("x") + ["3"])[:2])
                      if re.fullmatch(r"\d+x\d+", layout) else (3, 3))
        cols, rows = int(cols), int(rows)
        plan = {"ecken": [], "karten": []}
        if not raster:
            try:
                plan, kosten = _seite_lesen(img)
            except HTTPException:
                plan = {"ecken": [], "karten": []}
            # Was das Modell im Foto sieht, sticht die Voreinstellung des Binders: wer eine
            # 4×3-Seite fotografiert, hat sie fotografiert.
            if plan.get("spalten") and plan.get("reihen"):
                cols, rows = plan["spalten"], plan["reihen"]
        b, h = img.size
        seite = None
        if len(plan.get("ecken") or []) == 4:
            ecken = [(x / 1000 * b, y / 1000 * h) for x, y in plan["ecken"]]
            seite = entzerren(img, ecken, cols * ZELL_B, rows * ZELL_H)
        if seite is None:
            # Rückfall: das ganze Foto als Raster — so lief es früher immer.
            seite = img.resize((cols * ZELL_B, rows * ZELL_H), Image.LANCZOS)
        gelesen = plan.get("karten") or []

        zb, zh = seite.size[0] // cols, seite.size[1] // rows
        # Wo die Fächer wirklich sitzen: gemessen statt geteilt.
        sx = _linien(_profil(seite, 0), cols, seite.size[0]) or [
            (c * zb, (c + 1) * zb) for c in range(cols)]
        sy = _linien(_profil(seite, 1), rows, seite.size[1]) or [
            (r * zh, (r + 1) * zh) for r in range(rows)]
        roh = []
        for i in range(cols * rows):
            c, r = i % cols, i // cols
            zelle = seite.crop((sx[c][0], sy[r][0], sx[c][1], sy[r][1]))
            g = gelesen[i] if i < len(gelesen) else {}
            e = _erkennen(zelle, (g.get("name") or ""), (g.get("nummer") or ""))
            if e:
                e["gelesen"] = g.get("name") or ""
                e["gelesen_nr"] = g.get("nummer") or ""
                roh.append((i, e))
        # Eine Seite ist entweder westlich oder japanisch — gemischte Binder sind die
        # Ausnahme. Seit auch die japanischen Karten Fingerabdrücke haben, gewinnt sonst
        # gelegentlich der japanische Druck derselben Illustration. Entschieden wird das
        # an der Schrift der gelesenen Namen: „マーレイン" ist eine japanische Seite,
        # „Bronzong" eine westliche. Erst wenn nichts lesbar war, zählt die Mehrheit der
        # Treffer — die ist bei einem schlechten Foto selbst unsicher.
        cjk = sum(1 for g in gelesen if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", g.get("name") or ""))
        latein = sum(1 for g in gelesen if re.search(r"[A-Za-zÄÖÜäöü]", g.get("name") or ""))
        if cjk or latein:
            mehrheit = "jp" if cjk > latein else "intl"
        else:
            stimmen = {}
            for _i, e in roh:
                reg = e["regionen"].get(e["treffer"][0][1], "intl")
                stimmen[reg] = stimmen.get(reg, 0) + 1
            mehrheit = max(stimmen, key=stimmen.get) if stimmen else "intl"
        for _i, e in roh:
            neu = sorted((p + (REGION_MALUS if e["regionen"].get(c) != mehrheit else 0), c)
                         for p, c in e["treffer"])
            e["treffer"] = neu
            e["abstand"] = round(neu[0][0])
            e["vorsprung"] = round(neu[1][0] - neu[0][0]) if len(neu) > 1 else 99
            e["sicher"] = e["abstand"] <= GUT and e["vorsprung"] >= VORSPRUNG

        con = get_db()
        karten = []
        for i, e in roh:
            alternativen = []
            for punkte, cid in e["treffer"]:
                row = con.execute(
                    "SELECT c.id, c.name_de, c.name_en, c.name_ja, c.local_id,"
                    " COALESCE(s.name_en, s.name) AS setn FROM cards c"
                    " LEFT JOIN sets s ON s.id=c.set_id WHERE c.id=?", (cid,)).fetchone()
                if row:
                    alternativen.append({"id": row["id"],
                                         "name": row["name_de"] or row["name_en"] or row["name_ja"] or row["id"],
                                         "set": row["setn"] or "", "nr": row["local_id"],
                                         "abstand": round(punkte)})
            if not alternativen:
                continue
            karten.append({"platz": i, "id": alternativen[0]["id"], "name": alternativen[0]["name"],
                           "set": alternativen[0]["set"], "nr": alternativen[0]["nr"],
                           "gelesen": e.get("gelesen", ""), "gelesen_nr": e.get("gelesen_nr", ""),
                           "abstand": e["abstand"], "vorsprung": e["vorsprung"], "sicher": bool(e["sicher"]),
                           "ausschnitt": _vorschau(e["aus"]), "alternativen": alternativen[1:]})
        con.close()
        return {"karten": karten, "faecher": cols * rows, "layout": f"{cols}x{rows}",
                "kosten_usd": round(kosten, 5)}

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
