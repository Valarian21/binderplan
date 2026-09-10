"""Binderplan – PDF-Export: Platzhalter, Checkliste, Kaufliste.

Ein Abschnitt der main.py in eigener Datei (Phase 6, 10.09.2026). main.py führt ihn an der
Stelle aus, an der er vorher stand (`_abschnitt("pdf")`) – im selben Namensraum, mit
denselben Helfern, in derselben Reihenfolge. Es ist also kein importierbares Modul: neue
Funktionen hier sind in main.py sofort sichtbar, und umgekehrt. scripts/pruefen.py prüft alle
Dateien zusammen (pyflakes kennt die Namen der anderen Abschnitte)."""

# --- PDF-Export -------------------------------------------------------------

from PIL import Image, ImageDraw, ImageOps  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.lib.utils import ImageReader  # noqa: E402
from reportlab.pdfgen import canvas as pdfcanvas  # noqa: E402

SPRACHE_LABELS = {"de": "DE", "en": "EN", "jp": "JP"}
# Varianten je Fach (Kurzbezeichnung für Druck und Listen)
VARIANT_LABELS = {"reverse": "Reverse Holo", "holo": "Holo", "first": "1st Edition",
                  "pokeball": "Poké Ball", "masterball": "Master Ball"}

CARD_W = 63 * mm
CARD_H = 88 * mm
GUTTER = 4 * mm
COLS, ROWS = 3, 3


PRINT_W = 744   # 63 mm bei 300 dpi


def _print_image_path(card_id, lang="de", farbe=False):
    """JPEG in Druckauflösung, gecacht. Vorher wurde jedes Bild im PDF-Lauf in voller
    Auflösung konvertiert und verlustfrei eingebettet (26 MB, 34 s für 64 Karten).

    Graustufen bleibt die Vorgabe: ein Platzhalter im Binder soll als Platzhalter
    erkennbar sein und kostet so einen Bruchteil der Tinte. Farbe gibt es auf
    ausdrücklichen Wunsch, in einem eigenen Cache-Zweig."""
    safe = re.sub(r"[^A-Za-z0-9._%-]", "_", card_id)
    suffix = "" if lang != "en" else ".en"
    if farbe:
        suffix += ".farbe"
    target = CACHE / "cards" / "print" / f"{safe}{suffix}.jpg"
    if target.exists():
        return target
    quelle = _card_image_path(card_id, lang)
    if not quelle:
        return None
    try:
        img = Image.open(quelle)
        if img.mode in ("RGBA", "P", "LA"):
            bg = Image.new("RGB", img.size, "white")
            bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
            img = bg
        fertig = img.convert("RGB") if farbe else ImageOps.autocontrast(img.convert("L"), cutoff=1)
        if fertig.width > PRINT_W:
            fertig = fertig.resize((PRINT_W, int(fertig.height * PRINT_W / fertig.width)), Image.LANCZOS)
        fertig.save(target, "JPEG", quality=84, optimize=True)
        return target
    except Exception:
        return None


def _grayscale_reader(path: Path):
    img = Image.open(path)
    if img.mode in ("RGBA", "P", "LA"):
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
        img = bg
    gray = ImageOps.autocontrast(img.convert("L"), cutoff=1)
    return ImageReader(gray)


def _card_image_path(card_id, lang="de", groesse="high"):
    """Kartenbild besorgen (nutzt denselben Cache wie /api/img). „high" für Druck und Detail,
    „low" für Kacheln – die Stapelbilder zogen sonst je Binder bis zu 27 hochauflösende Scans
    aus dem Netz, 12 s je Kachel beim ersten Aufruf."""
    safe = re.sub(r"[^A-Za-z0-9._%-]", "_", card_id)
    suffix = "" if lang != "en" else ".en"
    target = CACHE / "cards" / groesse / f"{safe}{suffix}.webp"
    if target.exists():
        return target
    con = get_db()
    row = con.execute("SELECT image_de, image_en, image_alt FROM cards WHERE id = ?", (card_id,)).fetchone()
    con.close()
    if not row:
        return None
    urls = [
        f"{row['image_de']}/{groesse}.webp" if row["image_de"] else None,
        f"{row['image_en']}/{groesse}.webp" if row["image_en"] else None,
    ]
    if lang == "en":
        urls.reverse()
    urls += _alt_urls(row["image_alt"], groesse)
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


def _pdf_register(c, binder, lang, namen, plan):
    """Register: welche Seite enthält was. Wer einen Binder mit zwanzig Seiten füllt, sucht
    sonst blätternd — mit „Seite 3: Arkani bis Dragoran“ findet er die Stelle sofort."""
    seiten = len(plan)
    if seiten < 3:
        return                      # bei zwei Seiten ist ein Register nur Papierverschwendung
    page_w, page_h = A4
    zeilen = []
    for nr in range(seiten):
        teil = binder["items"][plan[nr]["start"]:plan[nr]["start"] + plan[nr]["laenge"]]
        karten = [namen.get(i.get("id")) for i in teil if i.get("type") == "card" and namen.get(i.get("id"))]
        if not karten:
            zeilen.append((nr + 1, "—"))
            continue
        von, bis = karten[0], karten[-1]
        zeilen.append((nr + 1, von if von == bis else f"{von} – {bis}"))

    c.setFillGray(0.1)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(22 * mm, page_h - 25 * mm, "Register" if lang == "de" else "Index")
    c.setFont("Helvetica", 9)
    c.setFillGray(0.45)
    c.drawString(22 * mm, page_h - 31 * mm,
                 "Welche Karten auf welcher Binderseite liegen" if lang == "de"
                 else "Which cards are on which binder page")

    spalten, pro_spalte = 2, 34
    breite = (page_w - 44 * mm) / spalten
    c.setFont("Helvetica", 9.5)
    for i, (nr, text) in enumerate(zeilen[:spalten * pro_spalte]):
        sp, ze = i // pro_spalte, i % pro_spalte
        x = 22 * mm + sp * breite
        y = page_h - 42 * mm - ze * 6.6 * mm
        c.setFillGray(0.35)
        c.drawString(x, y, ("Seite " if lang == "de" else "Page ") + str(nr))
        c.setFillGray(0.15)
        c.drawString(x + 17 * mm, y, text[:44])
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


def _zaehle_export(user, binder_id):
    """Export im Gratistarif abbuchen – aber nicht, wenn derselbe Binder in den letzten
    30 Minuten schon exportiert wurde (abgebrochener Download, zweiter Versuch,
    Drucker-Panne)."""
    if _limit_exporte(user) is None:
        return
    letzter = (user.get("letzter_export") or "").split(":", 1)
    if len(letzter) == 2 and letzter[0] == binder_id and letzter[1] >= datetime_str_vor(0.5):
        return
    con = get_db()
    con.execute("UPDATE users SET exports_monat = ?, letzter_export = ? WHERE id = ?",
                (f"{_monat_key()}:{_exporte_benutzt(user) + 1}",
                 f"{binder_id}:{datetime_str_vor(0)}", user["id"]))
    con.commit()
    con.close()


def _bilder_vorladen(card_ids, lang, farbe=False):
    """Hochauflösende Bilder parallel in den Cache holen (vorher lief das im PDF
    sequenziell: 64 Karten ≈ 40 s). → Anzahl fehlender Bilder."""
    ids = list(dict.fromkeys(i for i in card_ids if i))
    with ThreadPoolExecutor(8) as pool:
        pfade = list(pool.map(lambda c: _print_image_path(c, lang, farbe), ids))
    return sum(1 for p in pfade if p is None)


def _seiten_auswahl(text, seiten_gesamt):
    """„1-3,7,10-" → Menge der gemeinten Seitennummern (1-basiert).

    Leer oder unlesbar heißt: alle. Lieber zu viel drucken als schweigend zu wenig —
    ein PDF mit fehlenden Seiten fällt erst am Drucker auf."""
    if not text or not str(text).strip():
        return None
    treffer = set()
    for teil in str(text).replace(" ", "").split(","):
        if not teil:
            continue
        if "-" in teil:
            a, _, b = teil.partition("-")
            try:
                von = int(a) if a else 1
                bis = int(b) if b else seiten_gesamt
            except ValueError:
                continue
            for n in range(max(1, von), min(seiten_gesamt, bis) + 1):
                treffer.add(n)
        else:
            try:
                n = int(teil)
            except ValueError:
                continue
            if 1 <= n <= seiten_gesamt:
                treffer.add(n)
    return treffer or None


def _binder_lesen_erlaubt(binder_id: str, user):
    """Wer darf zusehen: der Besitzer, jeder bei einem anonymen Binder (die IDs sind
    unratbar und werden als Link geteilt), jeder bei einem Binder in der Vitrine — und
    seit dem 10.09.2026 jeder, für den der Besitzer den Link ausdrücklich freigegeben
    hat (`geteilt`, siehe teilen.py).

    Der Freigabe-Schalter schließt eine Lücke, die niemandem auffiel, weil der Besitzer
    selbst immer lesen darf: „Binder-Link teilen" erzeugte für einen angemeldeten Nutzer
    mit privatem Binder einen Link, der beim Empfänger auf 403 lief.

    Fremde private Binder bleiben gesperrt — ein Export lädt bis zu mehrere tausend
    hochauflösende Bilder nach."""
    con = get_db()
    spalten = {r[1] for r in con.execute("PRAGMA table_info(binders)")}
    extra = ", COALESCE(geteilt,0) geteilt, geteilt_bis" if "geteilt" in spalten else ""
    row = con.execute(f"SELECT user_id, COALESCE(sichtbar,0) sichtbar{extra}"
                      f" FROM binders WHERE id = ?", (binder_id,)).fetchone()
    con.close()
    if not row:
        return
    if not row["user_id"]:
        return
    if user and row["user_id"] == user["id"]:
        return
    if row["sichtbar"]:
        return
    # `teilen_offen` steht in teilen.py, das nach pdf.py ausgeführt wird — zur Laufzeit
    # ist es da, beim Import dieser Zeilen noch nicht. Deshalb der Blick in globals().
    offen = globals().get("teilen_offen")
    if offen and offen(row):
        return
    raise HTTPException(403, "Dieser Binder gehört jemand anderem.")


@app.post("/api/binders/{binder_id}/pdf_vorbereiten")  # noqa: E302
def binder_pdf_vorbereiten(binder_id: str, request: Request, farbe: int = 0):
    """Schritt 1 des Exports: Bilder laden (parallel), damit das PDF danach in Sekunden kommt.
    Nur für eigene Binder – sonst könnte ein beliebiges Konto über fremde Binder-IDs
    massenhaft Bild-Downloads auslösen."""
    _require_user(request)
    _binder_schreibrecht(binder_id, request)
    binder = _load_binder(binder_id)
    lang = "en" if (binder.get("options") or {}).get("sprache") == "en" else "de"
    ids = [i.get("id") for i in binder["items"] if i.get("type") == "card" and i.get("id")]
    fehlend = _bilder_vorladen(ids, lang, bool(farbe)) if ids else 0
    return {"karten": len(ids), "ohne_bild": fehlend}


# Fortschritt laufender Karten-PDFs je Binder – der Browser fragt ihn beim Warten ab.
_PDF_STAND = {}


@app.get("/api/binders/{binder_id}/pdf_stand")
def binder_pdf_stand(binder_id: str):
    return _PDF_STAND.get(binder_id, {})


@app.get("/api/binders/{binder_id}/pdf")
def binder_pdf(binder_id: str, request: Request, variante: str = "karten", nur_fehlende: int = 0,
               seiten: str = "", farbe: int = 0, nur_art: int = 0):
    user = _require_user(request)
    binder = _load_binder(binder_id)
    _binder_lesen_erlaubt(binder_id, user)
    plan = _seiten_plan(binder)
    lang = "en" if (binder.get("options") or {}).get("sprache") == "en" else "de"
    if variante == "checkliste":
        return _checkliste_pdf(binder, lang, bool(nur_fehlende), user)
    # Karten-PDF: zählt gegen das Monats-Limit von Free-Konten – ein zweiter Abruf desselben Binders
    # innerhalb von 30 Minuten (Download abgebrochen, nochmal drucken) bleibt frei.
    # Die Limit-Prüfung steht bewusst VOR der Credit-Abbuchung für fremde Artwork-Seiten:
    # sonst wurde bezahlt und danach mit 402 abgebrochen.
    letzter = (user.get("letzter_export") or "").split(":", 1)
    kulanz = len(letzter) == 2 and letzter[0] == binder_id and letzter[1] >= datetime_str_vor(0.5)
    grenze = _limit_exporte(user)
    if grenze is not None and not kulanz and _exporte_benutzt(user) >= grenze:
        raise HTTPException(402, detail={"code": "limit_export"})
    # Fremde, veröffentlichte Artwork-Seiten kosten einmalig Credits (siehe vitrine.py)
    if globals().get("_vitrine"):
        _vitrine.druckrecht_sichern(user, binder["items"])

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
    _bilder_vorladen(card_ids, lang, bool(farbe))

    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    page_w, page_h = A4
    grid_w = COLS * CARD_W + (COLS - 1) * GUTTER
    grid_h = ROWS * CARD_H + (ROWS - 1) * GUTTER
    ox = (page_w - grid_w) / 2
    oy = (page_h - grid_h) / 2

    # Seitenauswahl: „1-3,7" meint Binderseiten, nicht A4-Blätter — das ist die Zahl,
    # die im Planer und in der Blattansicht steht.
    seiten_gesamt = len(plan)
    gewaehlt = _seiten_auswahl(seiten, seiten_gesamt)
    besitz = _besitz_ids(user)
    printable = [
        (idx, item) for idx, item in enumerate(binder["items"])
        if item.get("type") != "empty"
        and not (nur_fehlende and _hat_karte(item, besitz, user))
        and not (nur_art and item.get("type") != "art")
        and (gewaehlt is None or (_seite_von(plan, idx) + 1) in gewaehlt)
    ]

    gesammelt = sum(1 for i in binder["items"] if _hat_karte(i, besitz, user))
    gesamt = sum(1 for i in binder["items"] if i.get("type") not in ("empty", "art"))
    if lang == "de":
        stats = [
            f"{gesamt} Karten geplant · {gesammelt} bereits gesammelt",
            f"{len(printable)} Proxys in diesem Druck · {max(1, -(-len(printable) // 9))} A4-Blätter",
            f"Raster {binder['layout'].replace('x', ' × ')} · {len(plan)} Binderseiten",
        ]
    else:
        stats = [
            f"{gesamt} cards planned · {gesammelt} already collected",
            f"{len(printable)} proxies in this print · {max(1, -(-len(printable) // 9))} A4 sheets",
            f"Grid {binder['layout'].replace('x', ' × ')} · {len(plan)} binder pages",
        ]
    _pdf_titelseite(c, binder, lang, stats)
    # Für das Register die Kartennamen in der Sprache des Drucks
    register_namen = {cid: ((r["name_en"] if lang == "en" else r["name_de"]) or r["name_de"] or r["name_en"] or "")
                      for cid, r in card_rows.items()}
    _pdf_register(c, binder, lang, register_namen, plan)

    _PDF_STAND[binder_id] = {"seiten": 1, "gesamt": max(1, -(-len(printable) // (COLS * ROWS)))}
    cell = 0
    for idx, item in printable:
        if cell == COLS * ROWS:
            c.showPage()
            _PDF_STAND[binder_id]["seiten"] += 1
            cell = 0
        col = cell % COLS
        row = cell // COLS
        x = ox + col * (CARD_W + GUTTER)
        y = oy + grid_h - (row + 1) * CARD_H - row * GUTTER

        seiten_nr = _seite_von(plan, idx)
        binder_page = seiten_nr + 1
        slot = idx - plan[seiten_nr]["start"] + 1
        variant = item.get("variant") or "normal"

        if item.get("type") == "art":
            # Artwork-Fach (KI-Seite): farbiger Ausschnitt, kein Wasserzeichen
            reader = (globals().get("_artwork").kachel_reader(item.get("artwork"), item.get("slot") or 0)
                      if globals().get("_artwork_kennzahlen") else None)
            if reader:
                c.drawImage(reader, x, y, CARD_W, CARD_H)
            else:
                _draw_placeholder(c, x, y, [("Artwork", 12, True)])
        elif item.get("type") == "dex":
            _draw_dex_cell(c, x, y, item, pokemon_names)
        else:
            card = card_rows.get(item.get("id"))
            path = _print_image_path(item.get("id"), lang, bool(farbe)) if card else None
            if path:
                try:
                    c.drawImage(str(path), x, y, CARD_W, CARD_H)   # JPEG-Pfad → DCT direkt eingebettet
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
        if item.get("type") == "art":
            label += " · Artwork"
        vl = VARIANT_LABELS.get(variant)
        if vl:
            label += " · " + vl
        if item.get("sprache") and item["sprache"] != lang:
            label += " · " + SPRACHE_LABELS.get(item["sprache"], str(item["sprache"]).upper())
        if item.get("zustand"):
            label += " · " + str(item["zustand"])[:12]
        c.drawCentredString(x + CARD_W / 2, y - 2.6 * mm, label)
        cell += 1

    if not printable:
        c.setFont("Helvetica", 14)
        c.drawCentredString(page_w / 2, page_h / 2,
                            "Dieser Binder ist noch leer." if lang == "de" else "This binder is still empty.")
    c.save()
    _PDF_STAND.pop(binder_id, None)

    con = get_db()
    con.execute(
        "INSERT INTO kv (key,value) VALUES ('pdf_exports','1')"
        " ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1"
    )
    con.commit()
    con.close()
    _zaehle_export(user, binder_id)

    fname = re.sub(r"[^A-Za-z0-9äöüÄÖÜß _-]", "", binder["name"]) or "binder"
    return Response(
        buf.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{fname}.pdf"'},
    )


def _binder_zeilen(binder, lang, user=None):
    """Alle Nicht-Leer-Fächer mit Anzeigedaten (für Checkliste und Kaufliste).

    Zwei Dinge kommen seit dem 03.09.2026 von woanders: Besitz aus der Sammlung statt aus
    `item.have`, und der Preis aus der geplanten Ausprägung statt aus dem Grundpreis. Ein
    Fach, das ausdrücklich als Reverse Holo geplant war, stand in der Kaufliste vorher mit
    dem Preis der Normalausgabe — während dasselbe Fach im Binderfenster den Holo-Preis
    zeigte."""
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
    preise = {r["card_id"]: r for r in con.execute(
        "SELECT card_id, COALESCE(eur, eur_geschaetzt) eur, eur_holo, eur_low FROM card_prices")}
    besitz = _besitz_ids(user, con)
    con.close()

    def preis_fuer(item):
        p = preise.get(item.get("id"))
        if not p:
            return None
        return preis_fuer_posten(p["eur"], p["eur_holo"], p["eur_low"],
                                 item.get("variant") or "normal", item.get("zustand") or "")
    plan = _seiten_plan(binder)
    zeilen = []
    for idx, item in enumerate(binder["items"]):
        if item.get("type") in ("empty", "art"):
            continue
        nr = _seite_von(plan, idx)
        pos = f"{nr + 1}·{idx - plan[nr]['start'] + 1}"
        if item.get("type") == "dex":
            zeilen.append({"pos": pos, "name": f"#{item.get('dex'):03d} {pokemon_names.get(item.get('dex'), '')}",
                           "set": "Pokédex", "nr": "", "eur": None,
                           "have": _hat_karte(item, besitz, user), "variant": ""})
        else:
            k = karten.get(item.get("id")) or {}
            name = (k.get("name_en") if lang == "en" else k.get("name")) or item.get("id", "?")
            setn = (k.get("set_name_en") if lang == "en" else k.get("set_name")) or ""
            zeilen.append({"pos": pos, "name": name, "set": setn, "nr": k.get("local_id") or "",
                           "eur": preis_fuer(item), "have": _hat_karte(item, besitz, user),
                           "variant": item.get("variant") or "", "zustand": item.get("zustand") or "",
                           "sprache": item.get("sprache") or ""})
    return zeilen


def _checkliste_pdf(binder, lang, nur_fehlende, user=None):
    """Karteiliste ohne Kartenbilder — zählt nicht als Export."""
    zeilen = _binder_zeilen(binder, lang, user)
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
    gesamt = len(zeilen); hab = sum(1 for z in zeilen if z["have"])
    c.setFont("Helvetica", 9)
    c.setFillGray(0.45)
    c.drawString(18 * mm, y, (f"{hab} von {gesamt} gesammelt · Preise: Cardmarket-Trend" if lang == "de"
                              else f"{hab} of {gesamt} collected · prices: Cardmarket trend"))
    y -= 8 * mm
    letzte_seite = None
    seiten_summe = 0.0
    c.setFont("Helvetica", 9.5)
    for z in zeilen:
        seite = z["pos"].split("·")[0]
        if seite != letzte_seite:
            # Kopfzeile je Binderseite – so hakt man Seite für Seite ab
            if y < 30 * mm:
                c.showPage(); neue_seite()
            y -= 2 * mm
            c.setFillGray(0.93); c.rect(18 * mm, y - 1.6 * mm, page_w - 36 * mm, 6 * mm, fill=1, stroke=0)
            c.setFillGray(0.2); c.setFont("Helvetica-Bold", 9.5)
            c.drawString(20 * mm, y, ("Seite " if lang == "de" else "Page ") + seite)
            c.setFont("Helvetica", 9.5)
            y -= 7 * mm
            letzte_seite = seite
        if y < 18 * mm:
            c.showPage()
            neue_seite()
            c.setFont("Helvetica", 9.5)
        c.setFillGray(0.15)
        c.setLineWidth(0.7)
        c.setStrokeGray(0.3)
        c.rect(20 * mm, y - 1, 3.4 * mm, 3.4 * mm)
        if z["have"]:
            c.setFont("Helvetica-Bold", 9)
            c.drawString(20.6 * mm, y - 0.4, "X")
            c.setFont("Helvetica", 9.5)
        c.drawString(27 * mm, y, z["pos"].split("·")[1])
        extra = VARIANT_LABELS.get(z["variant"], "")
        name = z["name"] + (f" ({extra})" if extra else "") + (f" · {SPRACHE_LABELS.get(z['sprache'], z['sprache'].upper())}" if z.get("sprache") else "") + (f" · {z['zustand']}" if z.get("zustand") else "")
        c.drawString(36 * mm, y, name[:46])
        c.setFillGray(0.45)
        c.drawString(122 * mm, y, ((z["set"] or "") + (" " + z["nr"] if z["nr"] else ""))[:30])
        if z["eur"] is not None:
            c.drawRightString(page_w - 18 * mm, y, f"{z['eur']:.2f} €")
            seiten_summe += z["eur"]
        y -= 6.2 * mm
    summe = sum(z["eur"] or 0 for z in zeilen); fehlend = sum((z["eur"] or 0) for z in zeilen if not z["have"])
    if y < 26 * mm:
        c.showPage(); neue_seite()
    y -= 4 * mm
    c.setFont("Helvetica-Bold", 9.5); c.setFillGray(0.2)
    c.drawRightString(page_w - 18 * mm, y, (f"Gesamt {summe:.2f} € · noch zu kaufen {fehlend:.2f} €" if lang == "de"
                                          else f"Total {summe:.2f} € · still to buy {fehlend:.2f} €"))
    c.save()
    fname = re.sub(r"[^A-Za-z0-9äöüÄÖÜß _-]", "", binder["name"]) or "binder"
    return Response(buf.getvalue(), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{fname}-checkliste.pdf"'})


@app.get("/api/binders/{binder_id}/kaufliste")
def binder_kaufliste(binder_id: str, request: Request, format: str = "csv"):
    """Fehlende Karten samt Preisen als Einkaufsliste (Pro-Funktion)."""
    user = _require_user(request)
    if not abo.darf_kaufliste(user):
        raise HTTPException(402, detail={"code": "limit_pro"})
    binder = _load_binder(binder_id)
    lang = "en" if (binder.get("options") or {}).get("sprache") == "en" else "de"
    zeilen = [z for z in _binder_zeilen(binder, lang, user) if not z["have"]]
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
        out = ["Name;Set;Nummer;Variante;Sprache;Zustand;Preis EUR"]
        for z in zeilen:
            preis = f"{z['eur']:.2f}".replace(".", ",") if z["eur"] is not None else ""
            out.append(f"{z['name']};{z['set']};{z['nr']};{z['variant']};{z['sprache']};{z['zustand']};{preis}")
        summe_txt = f"{summe:.2f}".replace(".", ",")
        out.append(f"Summe;;;;;;{summe_txt}")
        text = "\n".join(out)
        media, ext = "text/csv; charset=utf-8", "csv"
    fname = re.sub(r"[^A-Za-z0-9äöüÄÖÜß _-]", "", binder["name"]) or "binder"
    return Response(text.encode("utf-8-sig"), media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{fname}-kaufliste.{ext}"'})
