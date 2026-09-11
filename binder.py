"""Binderplan – Binder anlegen, laden, speichern, Wertverlauf.

Ein Abschnitt der main.py in eigener Datei (Phase 6, 10.09.2026). main.py führt ihn an der
Stelle aus, an der er vorher stand (`_abschnitt("binder")`) – im selben Namensraum, mit
denselben Helfern, in derselben Reihenfolge. Es ist also kein importierbares Modul: neue
Funktionen hier sind in main.py sofort sichtbar, und umgekehrt. scripts/pruefen.py prüft alle
Dateien zusammen (pyflakes kennt die Namen der anderen Abschnitte)."""

# --- Binder -----------------------------------------------------------------

# Gängige Binder-Raster: 4er (2×2), 9er (3×3), 12er hoch (3×4) und quer (4×3),
# 16er (4×4), 20er (4×5 bzw. 5×4) und 25er-Jumbo (5×5)
LAYOUTS = {"2x2": 4, "3x3": 9, "3x4": 12, "4x3": 12, "4x4": 16, "4x5": 20, "5x4": 20, "5x5": 25}


def _seiten_plan(binder, mindestens=0):
    """Wo jede Binderseite anfängt und wie groß sie ist.

    Einzelne Seiten dürfen vom Standardraster abweichen; die Abweichungen stehen in
    `options.seitenLayouts` als {Seitennummer: Raster}. Ohne diese Funktion rechnet
    jede Stelle mit einer festen Seitenlänge und liegt bei gemischten Bindern daneben —
    im PDF stünde dann die falsche Seitenzahl am Fach."""
    items = binder.get("items") or []
    je = ((binder.get("options") or {}).get("seitenLayouts")) or {}
    standard = binder.get("layout") or "3x3"
    plan, i, nr = [], 0, 0
    while True:
        roh = je.get(str(nr), je.get(nr))
        layout = roh if roh in LAYOUTS else standard
        laenge = LAYOUTS.get(layout, 9)
        spalten, zeilen = RASTER.get(layout, (3, 3))
        plan.append({"nr": nr, "start": i, "laenge": laenge,
                     "spalten": spalten, "zeilen": zeilen, "layout": layout})
        i += laenge
        nr += 1
        if i >= len(items) and nr > mindestens:
            break
    return plan


def _seite_von(plan, idx):
    """Seitennummer (0-basiert) eines Fachs."""
    for p in plan:
        if idx < p["start"] + p["laenge"]:
            return p["nr"]
    return plan[-1]["nr"]


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
        "items": json.dumps(_items_saeubern(items)),
    }


@app.post("/api/binders")
async def binder_create(request: Request):
    data = await request.json()
    p = _binder_payload(data)
    user = _current_user(request)
    grenze = _limit_binder(user) if user else None
    if user and grenze is not None:
        con = get_db()
        anzahl = con.execute("SELECT COUNT(*) c FROM binders WHERE user_id = ?", (user["id"],)).fetchone()["c"]
        con.close()
        if anzahl >= grenze:
            raise HTTPException(402, detail={"code": "limit_binder"})
    binder_id = secrets.token_urlsafe(8)
    con = get_db()
    con.execute(
        "INSERT INTO binders (id,name,mode,layout,options,items,user_id) VALUES (?,?,?,?,?,?,?)",
        (binder_id, p["name"], p["mode"], p["layout"], p["options"], p["items"],
         user["id"] if user else None),
    )
    con.commit()
    con.close()
    return {"id": binder_id}


def _binder_schreibrecht(binder_id: str, request: Request):
    """Konto-Binder darf nur der Besitzer ändern; anonyme Binder bleiben offen."""
    con = get_db()
    row = con.execute("SELECT user_id FROM binders WHERE id = ?", (binder_id,)).fetchone()
    con.close()
    if not row:
        raise HTTPException(404, "Binder nicht gefunden")
    if row["user_id"] is not None:
        user = _current_user(request)
        if not user or user["id"] != row["user_id"]:
            raise HTTPException(403, detail={"code": "fremder_binder"})


@app.put("/api/binders/{binder_id}")
async def binder_update(binder_id: str, request: Request):
    _binder_schreibrecht(binder_id, request)
    data = await request.json()
    p = _binder_payload(data)
    con = get_db()
    # Ein Binder in der Vitrine trägt seinen Namen öffentlich. Die Prüfung lief bisher nur
    # beim Veröffentlichen — danach ließ sich beliebiger Text nachschieben.
    alt = con.execute("SELECT name, COALESCE(sichtbar,0) sichtbar, updated_at FROM binders WHERE id=?",
                      (binder_id,)).fetchone()
    # Zwei Geräte überschrieben sich stumm – der letzte gewann. Der Client schickt den Stand mit,
    # den er geladen hat; weicht er ab, gibt es 409 und der Browser holt sich den neuen Stand.
    stand = str(data.get("updated_at") or "")
    if alt and stand and alt["updated_at"] and stand != alt["updated_at"]:
        con.close()
        raise HTTPException(409, detail={"code": "konflikt", "updated_at": alt["updated_at"]})
    if alt and alt["sichtbar"] and (alt["name"] or "") != p["name"] and globals().get("_vitrine"):
        ok, grund = await run_in_threadpool(_vitrine._text_ok, p["name"])
        if not ok:
            con.close()
            raise HTTPException(400, detail={"code": "text", "text": grund})
    cur = con.execute(
        "UPDATE binders SET name=?, mode=?, layout=?, options=?, items=?,"
        " updated_at=datetime('now') WHERE id=?",
        (p["name"], p["mode"], p["layout"], p["options"], p["items"], binder_id),
    )
    con.commit()
    neu = con.execute("SELECT updated_at FROM binders WHERE id=?", (binder_id,)).fetchone()
    con.close()
    if cur.rowcount == 0:
        raise HTTPException(404, "Binder nicht gefunden")
    _stapel_vorwaermen(binder_id)
    return {"ok": True, "updated_at": neu["updated_at"] if neu else None}


ITEM_FELDER = {"type", "id", "dex", "variant", "zustand", "sprache", "have", "artwork", "slot", "layout"}
ITEM_TYPEN = {"card", "dex", "empty", "art"}


def _items_saeubern(items):
    """Nur bekannte Felder und Typen speichern. Vorher ließ sich jedes beliebige Objekt
    ablegen, das später in fremden Vitrine-Ansichten und im PDF wieder auftauchte."""
    sauber = []
    for i in items[:5000]:
        if not isinstance(i, dict) or i.get("type") not in ITEM_TYPEN:
            continue
        e = {k: v for k, v in i.items() if k in ITEM_FELDER}
        for k in ("id", "variant", "zustand", "sprache", "artwork"):
            if k in e and e[k] is not None:
                e[k] = str(e[k])[:80]
        sauber.append(e)
    return sauber


# --- Wertverlauf ------------------------------------------------------------
# Die Tabelle price_history sammelt seit Wochen täglich Preise, sichtbar war davon nichts.
# Der Endpunkt summiert je Tag die Karten eines Binders — daraus wird die Linie „was ist
# meine Sammlung heute wert“ und die Zahl „+12 € seit letzter Woche“.

@app.get("/api/binders/{binder_id}/wert")
def binder_wert(binder_id: str, request: Request, tage: int = 30):
    # Wer den Binder lesen kann (die IDs sind unratbar und werden als Link geteilt), darf
    # auch seinen Wert sehen: die geteilte Ansicht fragte ihn ab und bekam 403, obwohl
    # dieselben Karten samt Preisen bereits auf dem Bildschirm standen. Der Wert kommt aus
    # der Datenbank, nicht aus dem Bild-Cache – die Export-Sperre ist hier nicht nötig.
    binder = _load_binder(binder_id)
    ids = list({i.get("id") for i in binder["items"] if i.get("type") == "card" and i.get("id")})
    if not ids:
        return {"punkte": [], "karten": 0, "aktuell": None, "veraenderung": None}
    tage = max(7, min(365, tage))
    von = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=tage)).strftime("%Y-%m-%d")
    con = get_db()
    reihen = []
    # Der Stand am Fenstergrund: die Historie führt nur noch Bewegungen, ein seit Wochen
    # unveränderter Preis hat am ersten Tag des Fensters keine Zeile. Ohne diesen
    # Startwert fielen genau die ruhigen Karten aus der Basis — und übrig blieben die
    # schwankenden, die Kurve wäre nervöser als der Binder.
    start_stand = {}
    messtage = {r["datum"] for r in con.execute(
        "SELECT datum FROM price_tage WHERE datum >= ? AND karten >= 50", (von,))}
    for start in range(0, len(ids), 400):
        teil = ids[start:start + 400]
        marken = ",".join("?" * len(teil))
        reihen += [dict(r) for r in con.execute(
            f"SELECT datum, card_id, eur FROM price_history"
            f" WHERE card_id IN ({marken}) AND datum >= ? AND eur IS NOT NULL", (*teil, von))]
        start_stand.update({c: w[0] for c, w in _historie_stand(con, von, teil).items()
                            if w[0] is not None})
    con.close()

    # Vergleichbar wird die Linie nur mit einer festen Basis: den Karten, die am ersten
    # *und* am letzten Tag einen Preis haben. Sonst stiege der Wert allein dadurch, dass
    # mit der Zeit mehr Karten einen Preis bekommen — das sähe wie Wertzuwachs aus.
    nach_tag = {}
    for r in reihen:
        nach_tag.setdefault(r["datum"], {})[r["card_id"]] = r["eur"]
    tage = sorted(messtage | set(nach_tag))
    if len(tage) < 2:
        return {"punkte": [], "karten": len(ids), "aktuell": None, "veraenderung": None, "basis": 0}
    letzte = dict(start_stand)
    letzte.update(nach_tag.get(tage[0], {}))
    ende = dict(letzte)
    for datum in tage:
        ende.update(nach_tag.get(datum, {}))
    basis = set(letzte) & set(ende)
    if len(basis) < 3:
        return {"punkte": [], "karten": len(ids), "aktuell": None, "veraenderung": None, "basis": len(basis)}
    letzte = {k: v for k, v in letzte.items() if k in basis}
    punkte = []
    for datum in tage:
        # Preise fortschreiben: ein Tag ohne frischen Wert ist kein Wertverlust
        letzte.update({k: v for k, v in nach_tag.get(datum, {}).items() if k in basis})
        if len(letzte) < len(basis):
            continue
        punkte.append({"datum": datum, "eur": round(sum(letzte.values()), 2)})
    if not punkte:
        return {"punkte": [], "karten": len(ids), "aktuell": None, "veraenderung": None, "basis": len(basis)}
    aktuell = punkte[-1]["eur"]
    grenze = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    vergleich = next((p["eur"] for p in punkte if p["datum"] >= grenze), punkte[0]["eur"])
    return {"punkte": punkte[-90:], "karten": len(ids), "basis": len(basis), "aktuell": aktuell,
            "veraenderung": round(aktuell - vergleich, 2)}


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
        # Steht der Binder in der Vitrine? (Spalte kommt aus vitrine.py, kann fehlen)
        "sichtbar": (row["sichtbar"] if "sichtbar" in row.keys() else 0) or 0,
    }


@app.get("/api/binders/{binder_id}")
def binder_get(binder_id: str, request: Request):
    """Einen Binder laden — mit derselben Rechteprüfung wie Druck und Vorschaubild.

    Bis zum 10.09.2026 stand hier `return _load_binder(binder_id)` ohne jede Prüfung: die
    Binder-IDs sind unratbar (`secrets.token_urlsafe(8)`), und das galt als Schutz genug.
    Mit dem Freigabe-Schalter stimmt das nicht mehr — „Link abschalten" hätte nur die
    Seite `/b/<id>` gesperrt, während `/api/binders/<id>` weiter geantwortet hätte. Der
    Schalter wäre eine Beschriftung ohne Wirkung gewesen.

    Erlaubt bleibt alles, was vorher erlaubt war: eigener Binder, anonymer Binder (kein
    Konto dran), Binder in der Vitrine — und neu der ausdrücklich freigegebene."""
    _binder_lesen_erlaubt(binder_id, _current_user(request))
    binder = _load_binder(binder_id)
    binder["drucke"] = _drucke_fuer(binder.get("items") or [])
    return binder


def _drucke_fuer(items):
    """Welche Ausprägungen es zu den Karten dieses Binders überhaupt gibt.

    Das Abzeichen am Fach soll unterscheiden: „REV" heißt „dieses Exemplar ist das Reverse,
    nicht das Normale". Bei einer Karte, die es nur als Holo gibt — Illustration Rare,
    Special Illustration Rare, Secret Rare, Full Art — unterscheidet „HOLO" nichts und legt
    sich nur in Rot über das Bild. Das betrifft 6.360 von 21.149 westlichen Karten (30 %),
    und ausgerechnet die, bei denen das Bild der Grund für die Karte ist."""
    ids = sorted({i.get("id") for i in items
                  if isinstance(i, dict) and i.get("type") == "card" and i.get("id")})
    if not ids:
        return {}
    aus, con = {}, get_db()
    for start in range(0, len(ids), 800):
        teil = ids[start:start + 800]
        for r in con.execute(
                "SELECT id, has_normal, has_reverse, has_holo, has_first FROM cards"
                " WHERE id IN (%s)" % ",".join("?" * len(teil)), teil):
            aus[r["id"]] = [name for name, an in (("normal", r["has_normal"]),
                                                  ("reverse", r["has_reverse"]),
                                                  ("holo", r["has_holo"]),
                                                  ("first", r["has_first"])) if an]
    con.close()
    return aus


@app.delete("/api/binders/{binder_id}")
def binder_delete(binder_id: str, request: Request):
    _binder_schreibrecht(binder_id, request)
    con = get_db()
    con.execute("DELETE FROM binders WHERE id = ?", (binder_id,))
    con.execute("DELETE FROM stimmen WHERE binder_id = ?", (binder_id,))      # Herzen ohne Binder zählen nirgends mehr
    con.execute("DELETE FROM meldungen WHERE ziel_typ = 'binder' AND ziel_id = ?", (binder_id,))
    con.commit()
    con.close()
    return {"ok": True}


RASTER = {"2x2": (2, 2), "3x3": (3, 3), "3x4": (3, 4), "4x3": (4, 3), "4x4": (4, 4),
          "4x5": (4, 5), "5x4": (5, 4), "5x5": (5, 5)}


def _besitz_ids(user, con=None):
    """Welche Karten dem Konto wirklich gehören — die einzige Quelle dafür.

    Seit dem Sammlungs-Umbau schreibt der Haken im Binder in die Tabelle `sammlung`;
    `item.have` im Fach wird bei angemeldeten Nutzern nicht mehr gesetzt. Serverseitig
    lasen PDF, Checkliste und Kaufliste aber weiterhin nur das Häkchen — „nur Fehlende"
    druckte deshalb alles und die Checkliste meldete „0 von 30 gesammelt". Wer Besitz
    braucht, fragt ab jetzt hier."""
    if not user:
        return set()
    eigene = con is None
    con = con or get_db()
    try:
        return {x["card_id"] for x in con.execute(
            "SELECT card_id FROM sammlung WHERE user_id = ? AND anzahl > 0", (user["id"],))}
    except Exception:
        return set()
    finally:
        if eigene:
            con.close()


def _hat_karte(item, besitz, user):
    """Ohne Konto zählt das Häkchen im Fach — dort gibt es keine Sammlung."""
    if user:
        return item.get("type") == "card" and item.get("id") in besitz
    return bool(item.get("have"))


def _blatt_vorschau(items, layout, seiten=3, seiten_layouts=None):
    """Die ersten Seiten eines Binders als Raster aus Fächern, leere Plätze inklusive.
    Dieselbe Form wie in der Vitrine, damit beide Vorschauen gleich aussehen.

    Jede Seite trägt ihr eigenes Raster: seit einzelne Seiten davon abweichen dürfen,
    wäre eine gemeinsame Spaltenzahl für alle schlicht falsch."""
    plan = _seiten_plan({"items": items, "layout": layout,
                         "options": {"seitenLayouts": seiten_layouts or {}}})
    spalten, zeilen = RASTER.get(layout or "3x3", (3, 3))
    aus = []
    for nr in range(max(1, min(4, seiten))):
        if nr >= len(plan):
            break
        sp = plan[nr]
        pro_seite = sp["laenge"]
        teil = items[sp["start"]:sp["start"] + pro_seite]
        if not teil and nr:
            break
        faecher = []
        for i in range(pro_seite):
            it = teil[i] if i < len(teil) else None
            if not it:
                faecher.append({"art": "leer"})
            elif it.get("type") == "card" and it.get("id"):
                faecher.append({"art": "card", "id": it["id"]})
            elif it.get("type") == "art" and it.get("artwork"):
                faecher.append({"art": "artwork", "id": it["artwork"],
                                "slot": it.get("slot") or 0, "layout": it.get("layout") or ""})
            elif it.get("type") == "dex" and it.get("dex"):
                faecher.append({"art": "dex", "dex": it["dex"]})
            else:
                faecher.append({"art": "leer"})
        if nr and all(f["art"] == "leer" for f in faecher):
            break
        aus.append({"spalten": sp["spalten"], "zeilen": sp["zeilen"], "faecher": faecher})
    return {"spalten": spalten, "zeilen": zeilen, "seiten": aus}


def _items_wert(con, items):
    """Was alle Karten eines Plans heute zusammen kosten, und wie sich das in 30 Tagen bewegt hat.

    Auf den Kacheln von Startseite und Vitrine: „Komplett heute 1.234 € · +2,1 %". Verbindet
    Planen und Markt an der Stelle, wo Nutzer täglich hinsehen. Bewegung nach der Regel aus
    markt.py (Ausreißer und Cent-Karten zählen nicht)."""
    karten = [i for i in items if i.get("type") == "card" and i.get("id")]
    if not karten:
        return None, None
    ids = list({i["id"] for i in karten})
    preise = {}
    for start in range(0, len(ids), 600):
        teil = ids[start:start + 600]
        marken = ",".join("?" * len(teil))
        for r in con.execute(f"SELECT card_id, {_wert.sql_eur('card_prices')} eur, eur_holo, eur_low,"
                             f" eur_avg30 FROM card_prices WHERE card_id IN ({marken})", teil):
            preise[r["card_id"]] = r
    # Jedes Fach ist ein Exemplar: liegt dieselbe Karte zweimal im Binder, zählt sie zweimal.
    # Und ein Fach kann einen Zustand tragen (Kartendialog „Zustand" beim Einlegen) — der zählt
    # hier genauso wie in Kaufliste und Checkliste, sonst hat derselbe Binder zwei Werte.
    zeilen = []
    basis = diff = 0.0
    for i in karten:
        pr = preise.get(i["id"])
        if not pr or not pr["eur"]:
            continue
        zeilen.append({"eur": pr["eur"], "eur_holo": pr["eur_holo"], "eur_low": pr["eur_low"],
                       "variante": i.get("variant") or "normal", "zustand": i.get("zustand") or ""})
        s = pr["eur_avg30"]
        if _wert.bewegung_prozent(pr["eur"], s) is not None:
            basis += s
            diff += pr["eur"] - s
    wert, _n, _ohne = _wert.zeilen_wert(zeilen, anzahl_feld=None)
    return wert, (round(diff / basis * 100, 1) if basis else None)


@app.get("/api/binders")
def binder_list(request: Request, ids: str = ""):
    """Konto-Binder (falls angemeldet) plus lokal gemerkte anonyme Binder."""
    wanted = [i for i in ids.split(",") if i][:50]
    user = _current_user(request)
    con = get_db()
    rows = []
    if user:
        rows += con.execute(
            "SELECT id,name,mode,layout,options,items,updated_at FROM binders WHERE user_id = ?"
            " ORDER BY updated_at DESC", (user["id"],)).fetchall()
    if wanted:
        rows += con.execute(
            "SELECT id,name,mode,layout,options,items,updated_at FROM binders WHERE user_id IS NULL"
            " AND id IN (%s)" % ",".join("?" * len(wanted)),
            wanted,
        ).fetchall()
    besitz = _besitz_ids(user, con)
    gesehen = set()
    result = []
    reihenfolge = [r["id"] for r in rows if user] + wanted
    by_id = {r["id"]: r for r in rows}
    for bid in reihenfolge:
        r = by_id.get(bid)
        if not r or bid in gesehen:
            continue
        gesehen.add(bid)
        items = json.loads(r["items"] or "[]")
        try:
            optionen = json.loads(r["options"] or "{}")
        except Exception:
            optionen = {}
        wert, bew30 = _items_wert(con, items)
        result.append({
            "id": r["id"], "name": r["name"], "mode": r["mode"], "layout": r["layout"],
            # „anzahl" sind Fächer (Innensicht des Planers), „karten" echte Karten. Die
            # Vitrine zählte immer Karten, die Startseite Fächer — derselbe Binder hatte
            # zwei Größen: „117 Fächer" gegen „20 Karten".
            "anzahl": len(items), "wert": wert, "bew30": bew30,
            "karten": sum(1 for i in items if i.get("type") == "card" and i.get("id")),
            "seiten": len(_seiten_plan({"items": items, "layout": r["layout"],
                                        "options": optionen})),
            # Weicht mindestens eine Seite vom Standardraster ab? Sonst behauptet die
            # Kachel „3×3" für einen Binder, in dem auch 4×4-Seiten stecken.
            "gemischt": bool(optionen.get("seitenLayouts")),
            # Zählt dieser Binder zur Kaufliste? Ohne Angabe ja — so war es immer.
            "wants": optionen.get("wants") is not False,
            "gesammelt": (sum(1 for i in items if i.get("id") in besitz) if user
                          else sum(1 for i in items if i.get("have"))),
            "updated_at": r["updated_at"],
            "vorschau": [i.get("id") for i in items if i.get("type") == "card" and i.get("id")][:3],
            "dex_vorschau": [i.get("dex") for i in items if i.get("type") == "dex"][:3],
            # Die ersten drei Seiten als Raster — die Startseite zeigt daraus einen Stapel,
            # der aussieht wie ein Binder, in dem man geblättert hat.
            "blatt": _blatt_vorschau(items, r["layout"], 3, optionen.get("seitenLayouts")),
        })
    con.close()
    return {"binder": result}
