"""Binderplan – Bild-Cache und Bild-Endpunkte.

Ein Abschnitt der main.py in eigener Datei (Phase 6, 10.09.2026). main.py führt ihn an der
Stelle aus, an der er vorher stand (`_abschnitt("bilder")`) – im selben Namensraum, mit
denselben Helfern, in derselben Reihenfolge. Es ist also kein importierbares Modul: neue
Funktionen hier sind in main.py sofort sichtbar, und umgekehrt. scripts/pruefen.py prüft alle
Dateien zusammen (pyflakes kennt die Namen der anderen Abschnitte)."""

# --- Bild-Cache -------------------------------------------------------------

# Ein gemeinsamer Client für alle Bildabrufe: hält die TLS-Verbindung offen (eine Seite
# lädt bis zu 60 Bilder) und erzwingt IPv4. Grund: assets.tcgdex.net hat einen
# AAAA-Eintrag, der auf diesem Server keine TLS-Verbindung annimmt. Der synchrone
# httpx-Client probiert die Adressen der Reihe nach und wartet die volle Zeitgrenze ab —
# jedes noch nicht zwischengespeicherte Kartenbild kostete dadurch 30 s, und ab 40
# gleichzeitigen Abrufen stand die ganze App (Threadpool voll). Zusätzlich steht die
# Reihenfolge systemweit in /etc/gai.conf; hier noch einmal, damit es einen Serverumzug
# überlebt.
_BILD_CLIENT = httpx.Client(
    transport=httpx.HTTPTransport(local_address="0.0.0.0", retries=1),
    timeout=12, headers=UA, follow_redirects=True,
    limits=httpx.Limits(max_connections=24, max_keepalive_connections=12),
)


def _fetch_asset(urls, target: Path):
    for url in urls:
        if not url:
            continue
        try:
            r = _BILD_CLIENT.get(url)
            if r.status_code == 200 and r.content:
                # Erst daneben schreiben, dann umbenennen: ein Abbruch mittendrin hinterließ
                # sonst eine kaputte Datei, die wegen des immutable-Headers ewig ausgeliefert wird.
                tmp = target.with_suffix(target.suffix + ".tmp")
                tmp.write_bytes(r.content)
                tmp.replace(target)
                return True
        except Exception:
            continue
    return False


IMG_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}


def _alt_urls(image_alt, variante):
    """Zweitquellen für den Scan: pokemontcg.io (small-URL …/2.png) und TCGplayer.

    Bei TCGplayer steht die volle Auflösung unter der nackten Nummer; für die Kachel
    genügt die zurechtgerechnete Fassung, die ein Viertel wiegt."""
    if not image_alt:
        return []
    if "product-images.tcgplayer.com" in image_alt:
        klein = image_alt.replace("product-images.tcgplayer.com/",
                                  "product-images.tcgplayer.com/fit-in/437x437/")
        return [image_alt] if variante == "high" else [klein, image_alt]
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
    # Der Dateikopf entscheidet, nicht die Endung: die Zweitquellen liefern PNG (pokemontcg.io)
    # und JPEG (TCGplayer), und ein JPEG als „image/webp" ausgeliefert zeigt kein Browser an.
    kopf = target.read_bytes()[:4]
    media = ("image/png" if kopf == b"\x89PNG"
             else "image/jpeg" if kopf[:3] == b"\xff\xd8\xff" else "image/webp")
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


@app.get("/api/img/set/{set_id}")
def set_symbol_image(set_id: str):
    """Set-Symbol (TCGdex) für die Set-Filteransicht – gecacht wie die übrigen Bilder."""
    target = CACHE / "sym" / f"{set_id}.png"
    if not target.exists():
        con = get_db()
        row = con.execute("SELECT symbol, symbol_alt FROM sets WHERE id = ?", (set_id,)).fetchone()
        con.close()
        sym = ((row["symbol"] if row else "") or "").strip()
        alt = ((row["symbol_alt"] if row else "") or "").strip()
        if not sym and not alt:
            raise HTTPException(404, "Kein Symbol")
        target.parent.mkdir(parents=True, exist_ok=True)
        # TCGdex-Symbol-URL braucht eine Endung (png bevorzugt, webp als Fallback); sonst pokemontcg.io
        urls = ([sym + ".png", sym + ".webp"] if sym else []) + ([alt] if alt else [])
        if not _fetch_asset(urls, target):
            raise HTTPException(404, "Kein Symbol verfügbar")
    return FileResponse(target, media_type="image/png", headers=IMG_HEADERS)
