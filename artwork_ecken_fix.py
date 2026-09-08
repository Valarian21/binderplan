"""Weiße Kartenecken aus fertigen Kunstseiten entfernen.

Bis zum 08.09.2026 legte `_kartenbild` jeden Kartenscan auf weißen Grund, bevor
er ins Fach kam. Der Scan ist rechteckig, die Karte abgerundet — also blieben an
jeder Karte vier weiße Zipfel über dem gemalten Bild stehen. Das Einsetzen
benutzt inzwischen die Alphamaske; bestehende Seiten tragen die Zipfel aber in
den Pixeln, und die Malerei darunter ist überschrieben.

Dieses Skript holt sie zurück, ohne neu zu malen: die Ecken werden aus der
**unmittelbar angrenzenden Malerei gespiegelt**. Zwischen zwei Fächern liegt
eine Fuge von 4 mm (rund 30 px), die durchgehend gemalt ist — die Spiegelung
holt sich also echte Bildinhalte von direkt nebenan, keine Erfindung. Danach
wird der Kartenscan mit seiner Alphamaske wieder aufgesetzt, damit die Kante
sauber ist.

Aufruf:
    venv/bin/python artwork_ecken_fix.py            # zeigt nur, was zu tun wäre
    venv/bin/python artwork_ecken_fix.py --schreiben
    venv/bin/python artwork_ecken_fix.py --schreiben --nur CREzGGcCnL_7
"""
import json
import shutil
import sqlite3
import sys
from pathlib import Path

from PIL import Image, ImageDraw

BASIS = Path(__file__).resolve().parent
CACHE = BASIS / "cache" / "artwork"
SICHERUNG = BASIS / "cache" / "artwork-vor-eckenfix"
KARTE_W, KARTE_H, FUGE = 63.0, 88.0, 4.0
SEITENVERHAELTNISSE = {"1:1": 1.0, "4:5": 0.8, "3:4": 0.75, "2:3": 2 / 3, "9:16": 9 / 16,
                       "5:4": 1.25, "4:3": 4 / 3, "3:2": 1.5, "16:9": 16 / 9}
LANGE_SEITE = {"1K": 1024, "2K": 2048, "4K": 4096}
#: Ab hier gilt ein Pixel als „nicht Karte". Der weiche Rand des Scans liegt
#: darunter und wird mitrepariert, sonst bliebe ein heller Saum stehen.
UNDURCHSICHTIG = 200


def geometrie(cols, rows, groesse):
    pw, ph = cols * KARTE_W + (cols - 1) * FUGE, rows * KARTE_H + (rows - 1) * FUGE
    ar = min(SEITENVERHAELTNISSE.items(), key=lambda kv: abs(kv[1] - pw / ph))[0]
    r = SEITENVERHAELTNISSE[ar]
    lang = LANGE_SEITE.get(groesse, 2048)
    cw, ch = (lang, round(lang / r)) if r >= 1 else (round(lang * r), lang)
    return min((cw - 16) / pw, (ch - 16) / ph)


#: Eckenradius einer Karte als Anteil der Breite – dieselbe Zahl wie in artwork.py.
KARTEN_RADIUS = 0.048


def mit_alpha(img):
    """Scans ohne Transparenz (JPEG von TCGplayer) bekommen ihre Ecken geschnitten."""
    if img.split()[-1].getextrema()[0] < 250:
        return img
    w, h = img.size
    maske = Image.new("L", (w, h), 0)
    ImageDraw.Draw(maske).rounded_rectangle((0, 0, w - 1, h - 1), radius=max(1, round(w * KARTEN_RADIUS)), fill=255)
    out = img.copy()
    out.putalpha(maske)
    return out


def fach_box(slot, cols, skala):
    """Fach im **zugeschnittenen** Seitenbild — der Versatz der Leinwand ist da schon ab."""
    col, row = slot % cols, slot // cols
    x, y = col * (KARTE_W + FUGE), row * (KARTE_H + FUGE)
    return (round(x * skala), round(y * skala), round((x + KARTE_W) * skala), round((y + KARTE_H) * skala))


def ecken_fuellen(seite, box, alpha):
    """Durchsichtige Stellen des Scans aus der Malerei daneben spiegeln.

    Gewählt wird die **nächstgelegene** Fachkante, hinter der noch Bild liegt.
    Ein Fach am Seitenrand hat dort keine Nachbarschaft; dann greift die
    übernächste Kante. Liegt gar keine, bleibt der Pixel, wie er ist — das
    passiert nur in den vier äußersten Ecken der ganzen Seite.
    """
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    sw, sh = seite.size
    px = seite.load()
    apx = alpha.load()
    gefuellt = 0
    for yy in range(bh):
        for xx in range(bw):
            if apx[xx, yy] >= UNDURCHSICHTIG:
                continue
            # Kanten nach Nähe, dann die erste, deren Spiegelquelle im Bild liegt.
            kandidaten = sorted((
                (yy, (x0 + xx, y0 - 1 - yy)),
                (bh - 1 - yy, (x0 + xx, y1 + (bh - 1 - yy))),
                (xx, (x0 - 1 - xx, y0 + yy)),
                (bw - 1 - xx, (x1 + (bw - 1 - xx), y0 + yy)),
            ), key=lambda k: k[0])
            for _, (qx, qy) in kandidaten:
                if 0 <= qx < sw and 0 <= qy < sh:
                    px[x0 + xx, y0 + yy] = px[qx, qy]
                    gefuellt += 1
                    break
    return gefuellt


def seite_reparieren(pfad, cols, rows, groesse, anker, lang, kartenpfad):
    seite = Image.open(pfad).convert("RGB")
    skala = geometrie(cols, rows, groesse)
    gefuellt = 0
    for slot, card_id in anker.items():
        quelle = kartenpfad(card_id, lang)
        if not quelle:
            continue
        karte = mit_alpha(Image.open(quelle).convert("RGBA"))
        box = fach_box(int(slot), cols, skala)
        karte = karte.resize((box[2] - box[0], box[3] - box[1]), Image.LANCZOS)
        gefuellt += ecken_fuellen(seite, box, karte.split()[-1])
        seite.paste(karte, (box[0], box[1]), karte)
    return seite, gefuellt


def main():
    schreiben = "--schreiben" in sys.argv
    nur = sys.argv[sys.argv.index("--nur") + 1] if "--nur" in sys.argv else None

    sys.path.insert(0, str(BASIS))
    from main import _card_image_path  # noqa: E402  – braucht den Pfad oben

    con = sqlite3.connect(f"file:{BASIS / 'app.db'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    zeilen = con.execute("SELECT id, layout, groesse, sprache, anker FROM artworks WHERE status = 'fertig'").fetchall()
    con.close()

    if schreiben:
        SICHERUNG.mkdir(parents=True, exist_ok=True)
    for r in zeilen:
        if nur and r["id"] != nur:
            continue
        anker = json.loads(r["anker"] or "{}")
        if not anker:
            continue
        pfad = CACHE / f"{r['id']}.png"
        if not pfad.exists():
            print(f"{r['id']}: keine Datei"); continue
        cols, rows = (int(v) for v in (r["layout"] or "3x3").split("x"))
        seite, gefuellt = seite_reparieren(pfad, cols, rows, r["groesse"], anker, r["sprache"] or "de", _card_image_path)
        print(f"{r['id']}: {len(anker)} Karten, {gefuellt} Eckpixel {'ersetzt' if schreiben else 'wären zu ersetzen'}")
        if not schreiben:
            continue
        if not (SICHERUNG / pfad.name).exists():
            shutil.copy2(pfad, SICHERUNG / pfad.name)
        seite.save(pfad, "PNG")
        vorschau = seite.copy()
        vorschau.thumbnail((900, 900), Image.LANCZOS)
        vorschau.save(CACHE / f"{r['id']}.vorschau.webp", "WEBP", quality=82)


if __name__ == "__main__":
    main()
