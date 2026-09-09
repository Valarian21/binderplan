"""Binderplan – Meta, Admin-Kennzahlen, Kartensuche, Seltenheiten, Import.

Ein Abschnitt der main.py in eigener Datei (Phase 6, 10.09.2026). main.py führt ihn an der
Stelle aus, an der er vorher stand (`_abschnitt("katalog")`) – im selben Namensraum, mit
denselben Helfern, in derselben Reihenfolge. Es ist also kein importierbares Modul: neue
Funktionen hier sind in main.py sofort sichtbar, und umgekehrt. scripts/pruefen.py prüft alle
Dateien zusammen (pyflakes kennt die Namen der anderen Abschnitte)."""

# --- Basis-Endpunkte --------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/admin/mailtest")
def admin_mailtest(request: Request, key: str = "", an: str = ""):
    """Prüft die SMTP-Zugangsdaten aus der .env mit einer echten Testmail.
    Aufruf: curl -X POST "http://127.0.0.1:8103/api/admin/mailtest?key=…&an=du@example.com" """
    if not admin_ok(key, request):
        raise HTTPException(403, "Falscher Schlüssel")
    env = _env()
    stand = {"host": env.get("SMTP_HOST", ""), "port": env.get("SMTP_PORT", ""),
             "user": env.get("SMTP_USER", ""), "passwort_gesetzt": bool(env.get("SMTP_PASS")),
             "absender": env.get("SMTP_FROM", ""), "konfiguriert": _mail_konfiguriert()}
    if not an:
        return {"stand": stand, "hinweis": "Zum Senden ?an=<adresse> angeben."}
    if not _mail_konfiguriert():
        return {"stand": stand, "gesendet": False,
                "grund": "SMTP_HOST, SMTP_USER und SMTP_PASS müssen in der .env stehen."}
    ok = _mail_senden(an, "Binderplan – Testmail",
                      "Diese Nachricht bestätigt, dass der Mailversand von binderplan.app "
                      "funktioniert.\n\nViele Grüße\nBinderplan")
    return {"stand": stand, "gesendet": ok, "fehler": "" if ok else _mail_letzter_fehler,
            "hinweis": "" if ok else "Der Server hat die Nachricht nicht angenommen. Bei "
                                     "„authentication failed“ stimmen Benutzername oder Passwort "
                                     "nicht — eine reine Weiterleitung hat kein Passwort und kann "
                                     "nicht senden; dafür braucht es ein echtes Postfach."}


@app.post("/api/admin/sync")
def admin_sync(key: str = ""):
    if not _admin_key() or key != _admin_key():
        raise HTTPException(403, "Falscher Schlüssel")
    threading.Thread(target=run_sync, daemon=True).start()
    return {"gestartet": True}


def _ptc_get(client, url, params=None):
    """pokemontcg.io antwortet sporadisch mit leeren 500ern — mit Backoff wiederholen."""
    import time as _time
    for versuch in range(5):
        try:
            r = client.get(url, params=params)
            if r.status_code == 200 and r.content:
                return r.json()
        except Exception:
            pass
        _time.sleep(2 * (versuch + 1))
    return {}


def _bilder_fallback_job():
    """Bildlücken (TCGdex ohne Scan) über pokemontcg.io füllen.
    Set-Zuordnung über den englischen Set-Namen, Karten über die Setnummer."""
    import time as _time
    def norm(s):
        return re.sub(r"[^a-z0-9]", "", (s or "").lower())

    con = get_db()
    luecken = con.execute(
        "SELECT set_id, COUNT(*) n FROM cards WHERE image_de IS NULL AND image_en IS NULL"
        " AND image_alt IS NULL GROUP BY set_id"
    ).fetchall()
    unsere = {r["id"]: norm(r["name_en"]) for r in con.execute("SELECT id, name_en FROM sets")}
    con.close()
    if not luecken:
        return
    headers = dict(UA)
    key = _env().get("PTCGIO_KEY")
    if key:
        headers["X-Api-Key"] = key
    try:
        with httpx.Client(timeout=30, headers=headers) as client:
            ptc_sets = _ptc_get(client, "https://api.pokemontcg.io/v2/sets?pageSize=250").get("data", [])
            nach_name = {norm(s.get("name")): s["id"] for s in ptc_sets}
            gefunden = 0
            for lk in luecken:
                sid = lk["set_id"]
                ptc_id = nach_name.get(unsere.get(sid, ""))
                if not ptc_id:
                    continue
                seite = 1
                nummern = {}
                namen = {}
                try:
                    while True:
                        # Achtung: "select" MUSS "id" enthalten, sonst antwortet die API mit 500
                        r = _ptc_get(
                            client, "https://api.pokemontcg.io/v2/cards",
                            params={"q": f"set.id:{ptc_id}", "pageSize": 250, "page": seite,
                                    "select": "id,number,images,name"},
                        )
                        daten = r.get("data", [])
                        for karte in daten:
                            bild = (karte.get("images") or {}).get("small")
                            if bild:
                                nummern[norm(str(karte.get("number", "")))] = bild
                                namen.setdefault(norm(karte.get("name")), bild)
                        if len(daten) < 250:
                            break
                        seite += 1
                        _time.sleep(1)
                except Exception:
                    continue  # einzelnes Set überspringen, Job läuft weiter
                if not nummern:
                    continue
                con = get_db()
                for r2 in con.execute(
                    "SELECT id, local_id, name_en FROM cards WHERE set_id = ? AND image_de IS NULL"
                    " AND image_en IS NULL AND image_alt IS NULL", (sid,)
                ).fetchall():
                    # erst Setnummer, sonst Kartenname (z. B. Classic Collection: CC001 vs. Originalnummern)
                    bild = nummern.get(norm(str(r2["local_id"] or ""))) or namen.get(norm(r2["name_en"]))
                    if bild:
                        con.execute("UPDATE cards SET image_alt = ? WHERE id = ?", (bild, r2["id"]))
                        gefunden += 1
                con.commit()
                con.close()
                _time.sleep(2)
        con = get_db()
        con.execute("INSERT OR REPLACE INTO kv (key,value) VALUES ('bilder_fallback', ?)", (str(gefunden),))
        con.commit()
        con.close()
    except Exception:
        pass


@app.post("/api/admin/bilder_fallback")
def admin_bilder_fallback(key: str = ""):
    if not _admin_key() or key != _admin_key():
        raise HTTPException(403, "Falscher Schlüssel")
    threading.Thread(target=_bilder_fallback_job, daemon=True).start()
    return {"gestartet": True}


@app.get("/api/admin/stats")
def admin_stats(key: str = ""):
    """Interne Kennzahlen fürs Empire-Dashboard (Venture Lab), nur mit ADMIN_KEY."""
    if not _admin_key() or key != _admin_key():
        raise HTTPException(403, "Falscher Schlüssel")
    con = get_db()
    binder = con.execute("SELECT COUNT(*) c FROM binders").fetchone()["c"]
    neu7 = con.execute(
        "SELECT COUNT(*) c FROM binders WHERE created_at >= datetime('now','-7 days')"
    ).fetchone()["c"]
    karten = con.execute("SELECT COUNT(*) c FROM cards").fetchone()["c"]
    pdfs = con.execute("SELECT value FROM kv WHERE key='pdf_exports'").fetchone()
    con.close()
    return {"kpis": [
        {"label": "Binder", "value": binder, "color": "blue"},
        {"label": "Neu (7T)", "value": neu7, "color": "green" if neu7 else None},
        {"label": "PDF-Exporte", "value": int(pdfs["value"]) if pdfs else 0},
        {"label": "Karten im Katalog", "value": karten},
    ] + _artwork_kpis() + _job_kpis(), "jobs": _job_stand()}


def _job_kpis():
    """Preislauf-Stand als Kennzahl: wann zuletzt, wie lange, ob mit Fehler."""
    try:
        for j in _job_stand():
            if j["job"] == "preishistorie":
                return [{"label": "Preislauf", "value": f"{(j['letzter'] or '–')[5:16]} · {j['dauer'] or '?'} s",
                         "color": "red" if j["fehler"] else "green"}]
    except Exception:
        pass
    return []


def _artwork_kpis():
    if not globals().get("_artwork_kennzahlen"):
        return []
    try:
        n, kosten = _artwork_kennzahlen()
        return [{"label": "Artwork-Seiten", "value": n, "color": "purple" if n else None},
                {"label": "Artwork-Kosten", "value": f"{kosten:.2f} $"}]
    except Exception:
        return []


_META_CACHE = {"bis": 0.0, "etag": "", "body": b""}


@app.get("/api/meta")
def meta(request: Request):
    """163 KB bei jedem Start der App, 0,7 s Rechenzeit – dabei ändert sich der Katalog nur
    beim Sync. Zehn Minuten im Prozess gehalten und per ETag als 304 beantwortet."""
    jetzt = time.time()
    if jetzt > _META_CACHE["bis"]:
        import hashlib
        body = json.dumps(_meta_bauen(), ensure_ascii=False).encode("utf-8")
        _META_CACHE.update(bis=jetzt + 600, body=body, etag='"' + hashlib.md5(body).hexdigest()[:16] + '"')
    if request.headers.get("if-none-match") == _META_CACHE["etag"]:
        return Response(status_code=304, headers={"ETag": _META_CACHE["etag"]})
    return Response(content=_META_CACHE["body"], media_type="application/json",
                    headers={"ETag": _META_CACHE["etag"], "Cache-Control": "private, max-age=0, must-revalidate"})


def _meta_bauen():
    con = get_db()
    counts = {
        "cards": con.execute("SELECT COUNT(*) c FROM cards").fetchone()["c"],
        "sets": con.execute("SELECT COUNT(*) c FROM sets").fetchone()["c"],
        "pokemon": con.execute("SELECT COUNT(*) c FROM pokemon").fetchone()["c"],
    }
    sets = []
    vorhandene_aeren = set()
    for r in con.execute(
        "SELECT id,name,name_en,serie_id,serie_name,serie_name_en,release_date,total,official,symbol,region,symbol_alt"
        " FROM sets ORDER BY release_date IS NULL, release_date"
    ):
        d = dict(r)
        d["region"] = d.get("region") or "intl"
        # Promos, Jumbo-Karten, McDonald's & Co. sind keine Sammel-Sets – sie wandern
        # in der Set-Liste ans Ende ihrer Ära (vorher stand „Miscellaneous Promos“ ganz oben)
        d["promo"] = bool(re.search(r"promo|jumbo|misc|mcdonald|trainer kit|pop series|deck|starter", (d.get("name_en") or d.get("name") or ""), re.I)
                          or (d["serie_id"] in ("pop", "tk", "mc", "misc")))
        if d["region"] == "jp":
            # Der englische Name kommt aus dem Cardmarket-Katalog (siehe cm_import); erst
            # wenn er fehlt, bleibt der japanische stehen.
            d["name"] = d["name_en"] or d["name"]
            jn = JP_AEREN.get(d["serie_id"] or "", (d["serie_name"] or d["serie_id"] or "?",) * 2)
            d["aera"] = "jp:" + (d["serie_id"] or "?"); d["aera_name"] = jn[0]; d["aera_name_en"] = jn[1]
            d["symbol"] = d["symbol"] or None
            sets.append(d)
            continue
        d["symbol"] = d["symbol"] or (r["symbol_alt"] if "symbol_alt" in r.keys() and r["symbol_alt"] else None)
        d["name"] = SET_NAME_FIX_DE.get(d["id"], d["name"]) or d["name_en"]
        d["serie_name"] = SERIE_NAME_FIX_DE.get(d["serie_id"], d["serie_name"]) or d["serie_name_en"]
        aera = _aera_fuer_set(d["serie_id"], d["release_date"])
        info = next(a for a in AEREN if a["id"] == aera)
        d["aera"] = aera
        d["aera_name"] = info["name"]
        d["aera_name_en"] = info["name_en"]
        vorhandene_aeren.add(aera)
        sets.append(d)
    # Ären-Reihenfolge, innerhalb einer Ära erst Sammel-Sets chronologisch, dann Promos; Japan als eigener Baum
    jp_start = {}
    for x in sets:
        if x["region"] == "jp":
            jp_start[x["aera"]] = min(jp_start.get(x["aera"], "9999"), x["release_date"] or "9999")
    sets.sort(key=lambda s: (AERA_ORDNUNG.get(s["aera"], 99) if s["region"] != "jp" else 100, jp_start.get(s["aera"], ""), s["promo"], s["release_date"] or "9999"))
    series = [
        {"id": a["id"], "name": a["name"], "name_en": a["name_en"],
         "von": a["von"], "bis": a["bis"], "region": "intl"}
        for a in AEREN if a["id"] in vorhandene_aeren
    ]
    for aera, start in sorted(jp_start.items(), key=lambda x: x[1]):
        bsp = next(x for x in sets if x["aera"] == aera)
        jahre = [x["release_date"][:4] for x in sets if x["aera"] == aera and x["release_date"]]
        series.append({"id": aera, "name": bsp["aera_name"], "name_en": bsp["aera_name_en"], "von": min(jahre) if jahre else "", "bis": max(jahre) if jahre else "", "region": "jp"})
    rarities = [
        {"rarity": r["rarity"], "anzahl": r["c"]}
        for r in con.execute(
            "SELECT rarity, COUNT(*) c FROM cards WHERE rarity IS NOT NULL AND rarity != 'None'"
            " GROUP BY rarity ORDER BY c DESC"
        )
    ]
    last_sync = con.execute("SELECT value FROM kv WHERE key='last_sync'").fetchone()
    alle_illu = {r["illustrator"]: r["c"] for r in con.execute(
        "SELECT illustrator, COUNT(*) c FROM cards WHERE illustrator IS NOT NULL GROUP BY illustrator HAVING c >= 3")}
    nach_klein = {n.lower(): n for n in alle_illu}
    top_namen = [nach_klein[t.lower()] for t in TOP_ARTISTS if t.lower() in nach_klein]
    illustrators = [{"name": n, "anzahl": alle_illu[n], "top": True} for n in top_namen]
    illustrators += [{"name": n, "anzahl": c, "top": False} for n, c in sorted(alle_illu.items(), key=lambda x: x[0].lower()) if n not in top_namen]
    regmarks = [r["regulation_mark"] for r in con.execute(
        "SELECT DISTINCT regulation_mark FROM cards WHERE regulation_mark IS NOT NULL AND regulation_mark != 'None' ORDER BY regulation_mark")]
    trainer_types = [r["trainer_type"] for r in con.execute(
        "SELECT trainer_type FROM cards WHERE trainer_type IS NOT NULL GROUP BY trainer_type ORDER BY COUNT(*) DESC")]
    jahre = con.execute("SELECT MIN(substr(release_date,1,4)) a, MAX(substr(release_date,1,4)) b FROM cards WHERE release_date IS NOT NULL AND region='intl'").fetchone()
    familien = con.execute("SELECT COUNT(*) c FROM pokemon WHERE familie IS NOT NULL").fetchone()["c"]
    jp_details = con.execute("SELECT COUNT(*) c FROM cards WHERE region='jp' AND rarity IS NOT NULL").fetchone()["c"]
    con.close()
    return {
        "sync": {**SYNC, "last": last_sync["value"] if last_sync else None},
        "illustrators": illustrators,
        "rarity_groups": [{"id": k, **{x: v[x] for x in ("name", "name_en")}} for k, v in RARITY_GROUPS.items()],
        "presets": [{"id": k, "name": v["name"], "name_en": v["name_en"]} for k, v in PRESETS.items()],
        "regmarks": regmarks, "trainer_types": trainer_types,
        "jahre": [int(jahre["a"] or 1999), int(jahre["b"] or 2026)], "familien": familien,
        "jp_details": jp_details,
        "counts": counts,
        "sets": sets,
        "series": series,
        "rarities": rarities,
        "types": TYPES_DE,
        "gens": [{"gen": g, "von": lo, "bis": hi} for g, lo, hi in GEN_RANGES],
        # Die Zustandsfaktoren standen dreimal im Browser-Code und wichen dort voneinander ab.
        # Jetzt kommen sie von hier; im Frontend wird nur noch nachgeschlagen.
        "zustand_faktor": _wert.ZUSTAND_FAKTOR,
    }


# --- Seltenheits-Gruppen & Sammel-Schnellauswahlen ---------------------------
# 39 Roh-Seltenheiten (inkl. TCG Pocket „One Diamond“ …) sind kein Filter, den ein Sammler
# versteht – die Gruppen entsprechen dem Sprachgebrauch: Illustration Rares, Full Arts, Secret/Gold …
# Seltenheitsgruppen. Jeder Rohwert gehört zu genau einer Gruppe — eine Auswahl darf nie etwas
# mitbringen, das nicht dazugehört.
#
# Der Haken dabei: der Rohwert allein reicht nicht. Bis zur Schwarz-Weiß-Ära schreibt die Quelle
# für eine Holo-Rare dasselbe „Rare" wie für eine gewöhnliche Rare — gemessen tragen 4.036 Karten
# den Wert „Rare", und nur 47 % davon gibt es überhaupt als Holo. Deshalb entscheidet zusätzlich
# `has_holo`, das aus den Ausprägungen der Börsen stammt und verlässlich ist:
#   `werte`            gelten unabhängig davon,
#   `werte_holo`       nur mit has_holo = 1,
#   `werte_ohne_holo`  nur mit has_holo = 0.
# Anlass: „Ära Diamant & Perl + Rare/Holo" brachte Bannoss (dp3-23) mit, das es nie als Holo gab.
RARITY_GROUPS = {
    "common":       {"name": "Common", "name_en": "Common", "werte": ["Common"]},
    "uncommon":     {"name": "Uncommon", "name_en": "Uncommon", "werte": ["Uncommon"]},
    "rare":         {"name": "Rare (ohne Holo)", "name_en": "Rare (non-holo)",
                     "werte": [], "werte_ohne_holo": ["Rare"]},
    "holo":         {"name": "Rare Holo", "name_en": "Rare Holo",
                     "werte": ["Rare Holo", "Holo Rare"], "werte_holo": ["Rare"]},
    "double":       {"name": "Double Rare (ex)", "name_en": "Double Rare (ex)",
                     "werte": ["Double rare", "Triple Rare"]},
    "v":            {"name": "V / VMAX / VSTAR", "name_en": "V / VMAX / VSTAR",
                     "werte": ["Holo Rare V", "Holo Rare VMAX", "Holo Rare VSTAR"]},
    "lvx":          {"name": "LV.X / Prime / LEGEND", "name_en": "LV.X / Prime / LEGEND",
                     "werte": ["Rare Holo LV.X", "Rare PRIME", "LEGEND", "Classic Collection"]},
    "ultra":        {"name": "Ultra Rare / Full Art", "name_en": "Ultra Rare / Full Art", "werte": ["Ultra Rare", "Full Art Trainer"]},
    "illustration": {"name": "Illustration Rare / Alt Art", "name_en": "Illustration Rare / Alt Art", "werte": ["Illustration rare", "Special illustration rare", "Character Rare", "Character Super Rare"]},
    "secret":       {"name": "Secret / Gold / Rainbow", "name_en": "Secret / Gold / Rainbow", "werte": ["Secret Rare", "Hyper rare", "Mega Hyper Rare", "Black White Rare"]},
    "shiny":        {"name": "Shiny", "name_en": "Shiny", "werte": ["Shiny rare", "Shiny rare V", "Shiny rare VMAX", "Shiny Ultra Rare"]},
    "special":      {"name": "Radiant / Amazing / ACE SPEC", "name_en": "Radiant / Amazing / ACE SPEC", "werte": ["Radiant Rare", "Amazing Rare", "ACE SPEC Rare"]},
    "promo":        {"name": "Promo", "name_en": "Promo", "werte": ["Promo"]},
}
# Meistgesammelte Illustratoren (Recherche 2026-08-27, siehe TOP_ARTISTS_QUELLEN) – stehen im Künstler-
# Dropdown ganz oben; danach alle übrigen alphabetisch.
TOP_ARTISTS = [
    "Mitsuhiro Arita", "Shinji Kanda", "Akira Egawa", "Yuka Morii", "Tomokazu Komiya", "Sowsow",
    "Kagemaru Himeno", "Atsuko Nishida", "Ken Sugimori", "Kouki Saitou", "Masakazu Fukuda", "Ryo Ueda",
    "Naoyo Kimura", "Tokiya", "Asako Ito", "Naoki Saito", "Shin Nagasawa", "Oswaldo Kato", "Narumi Sato", "Mugi Hamada",
]
TOP_ARTISTS_QUELLEN = "snkrdunk.com, woahpoke.com, thegamer.com, crispycards.de"

# Beliebte Sammelthemen als Pokédex-Listen (Grundformen; Entwicklungen kommen über die Familie dazu)
PRESETS = {
    "starter":   {"name": "Starter", "name_en": "Starters", "dex": [1, 4, 7, 152, 155, 158, 252, 255, 258, 387, 390, 393, 495, 498, 501, 650, 653, 656, 722, 725, 728, 810, 813, 816, 906, 909, 912], "familie": True},
    "legendary": {"name": "Legendäre & Mysteriöse", "name_en": "Legendary & Mythical", "dex": [144, 145, 146, 150, 151, 243, 244, 245, 249, 250, 251, 377, 378, 379, 380, 381, 382, 383, 384, 385, 386, 480, 481, 482, 483, 484, 485, 486, 487, 488, 489, 490, 491, 492, 493, 494, 638, 639, 640, 641, 642, 643, 644, 645, 646, 647, 648, 649, 716, 717, 718, 719, 720, 721, 772, 773, 785, 786, 787, 788, 789, 790, 791, 792, 793, 794, 795, 796, 797, 798, 799, 800, 801, 802, 803, 804, 805, 806, 807, 808, 809, 888, 889, 890, 891, 892, 893, 894, 895, 896, 897, 898, 905, 1001, 1002, 1003, 1004, 1007, 1008, 1014, 1015, 1016, 1017, 1020, 1021, 1022, 1023, 1024, 1025], "familie": False},
    "eevee":     {"name": "Evoli & Entwicklungen", "name_en": "Eeveelutions", "dex": [133, 134, 135, 136, 196, 197, 470, 471, 700], "familie": False},
    "baby":      {"name": "Baby-Pokémon", "name_en": "Baby Pokémon", "dex": [172, 173, 174, 175, 236, 238, 239, 240, 298, 360, 406, 433, 438, 439, 440, 446, 447, 458, 848], "familie": False},
    "pikachu":   {"name": "Pikachu-Familie", "name_en": "Pikachu family", "dex": [25, 26, 172], "familie": False},
}


def _familie_dex(con, dex_ids):
    """Alle Pokédex-Nummern der Entwicklungsfamilien der gegebenen Pokémon."""
    if not dex_ids:
        return []
    fams = [r["familie"] for r in con.execute(
        "SELECT DISTINCT familie FROM pokemon WHERE dex_id IN (%s) AND familie IS NOT NULL" % ",".join("?" * len(dex_ids)), list(dex_ids))]
    if not fams:
        return list(dex_ids)
    return [r["dex_id"] for r in con.execute(
        "SELECT dex_id FROM pokemon WHERE familie IN (%s) ORDER BY familie, evo_stufe, dex_id" % ",".join("?" * len(fams)), fams)]


def _dex_frag(dexe):
    """WHERE-Fragment: Karte gehört zu einem der Pokémon (first_dex reicht – Mehrfach-Dex sind selten)."""
    dexe = [int(d) for d in dexe]
    if not dexe:
        return "0", []
    return "first_dex IN (%s)" % ",".join("?" * len(dexe)), dexe


# --- Kartensuche ------------------------------------------------------------

SORTS = {
    "datum": "release_date IS NULL, release_date, set_id, local_num",
    "dex": "first_dex IS NULL, first_dex, release_date",
    "name": "COALESCE(name_de, name_en) COLLATE NOCASE",
    "nummer": "set_id, local_num",
    "typ": "types, COALESCE(name_de, name_en) COLLATE NOCASE",
}


_ART_TABELLE = None


def _art_tabelle_da():
    """Gibt es den Artwork-Index? (themen.py legt ihn an — ohne Modul keine Bildfilter.)"""
    global _ART_TABELLE
    if _ART_TABELLE is None:
        con = get_db()
        _ART_TABELLE = bool(con.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'card_art_tags'").fetchone())
        con.close()
    return _ART_TABELLE


# Die Szenenbeschreibungen im Bildindex sind englisch (das Modell benennt Motive dort
# treffsicherer). Damit „Mond" trotzdem Treffer liefert, übersetzt diese Liste die
# gängigen Motivwörter; alles Unbekannte geht unverändert in die Suche.
_ART_WORT = {
    "mond": "moon", "sterne": "star", "stern": "star", "sonne": "sun", "sonnenuntergang": "sunset",
    "sonnenaufgang": "sunrise", "regenbogen": "rainbow", "wolke": "cloud", "wolken": "cloud",
    "regen": "rain", "schnee": "snow", "gewitter": "storm", "sturm": "storm", "blitz": "lightning",
    "nebel": "mist", "wind": "wind", "feuer": "fire", "flamme": "flame", "flammen": "flame",
    "eis": "ice", "rauch": "smoke", "blume": "flower", "blumen": "flower", "baum": "tree",
    "bäume": "tree", "baeume": "tree", "gras": "grass", "fels": "rock", "felsen": "rock",
    "wasserfall": "waterfall", "see": "lake", "meer": "ocean", "ozean": "ocean", "fluss": "river",
    "bach": "stream", "teich": "pond", "strand": "beach", "welle": "wave", "wellen": "wave",
    "koralle": "coral", "korallen": "coral", "blase": "bubble", "blasen": "bubble", "insel": "island",
    "berg": "mountain", "berge": "mountain", "vulkan": "volcano", "lava": "lava", "höhle": "cave",
    "hoehle": "cave", "wald": "forest", "dschungel": "jungle", "wüste": "desert", "wueste": "desert",
    "stadt": "city", "gebäude": "building", "gebaeude": "building", "haus": "house", "turm": "tower",
    "brücke": "bridge", "bruecke": "bridge", "straße": "street", "strasse": "street",
    "fenster": "window", "tür": "door", "zug": "train", "boot": "boat", "schiff": "ship",
    "auto": "car", "mensch": "person", "menschen": "person", "trainer": "trainer", "kind": "child",
    "nacht": "night", "tag": "day", "dämmerung": "dusk", "abend": "evening", "morgen": "morning",
    "himmel": "sky", "weltraum": "space", "planet": "planet", "galaxie": "galaxy", "ruine": "ruins",
    "ruinen": "ruins", "tempel": "temple", "schloss": "castle", "burg": "castle", "garten": "garden",
    "park": "park", "bahnhof": "station", "markt": "market", "laterne": "lantern", "licht": "light",
    "schatten": "shadow", "spiegelung": "reflection", "silhouette": "silhouette",
    "schlafen": "sleeping", "schläft": "sleeping", "fliegen": "flying", "fliegt": "flying",
    "springen": "jumping", "springt": "jumping", "rennen": "running", "rennt": "running",
    "schwimmen": "swimming", "schwimmt": "swimming", "tauchen": "diving", "taucht": "diving",
    "kampf": "battle", "kämpft": "fighting", "essen": "food", "musik": "music", "buch": "book",
    "bücher": "book", "kirschblüten": "cherry blossom", "kirschblüte": "cherry blossom",
    "herbst": "autumn", "winter": "winter", "frühling": "spring", "sommer": "summer",
    "laub": "leaves", "blätter": "leaves", "pilz": "mushroom", "pilze": "mushroom",
    "kristall": "crystal", "kristalle": "crystal", "regenschirm": "umbrella", "schnee​flocke": "snowflake",
}


def _art_frag(art_ort, art_zeit, art_wasser, art_merkmal, art_text):
    """Bedingungen für die Bildsuche: Ort, Tageszeit, Wasseranteil, Merkmale, Freitext.

    Mehrere Orte oder Merkmale werden UND-verknüpft — „Wald" und „Mond" heißt: beides
    im selben Bild. Der Freitext geht gegen die englische Szenenbeschreibung."""
    if not _art_tabelle_da():
        return [], []
    bed, bp = [], []
    for o in [x for x in (art_ort or "").split(",") if x.strip()]:
        bed.append("orte LIKE ?"); bp.append(f"%{o.strip()}%")
    for m in [x for x in (art_merkmal or "").split(",") if x.strip()]:
        bed.append("merkmale LIKE ?"); bp.append(f"%{m.strip()}%")
    if art_zeit in ("tag", "nacht", "daemmerung"):
        bed.append("zeit = ?"); bp.append(art_zeit)
    if art_wasser:
        bed.append("wasser >= ?"); bp.append(max(1, min(3, int(art_wasser))))
    where, params = [], []
    if bed:
        where.append("cards.id IN (SELECT card_id FROM card_art_tags WHERE %s)" % " AND ".join(bed))
        params += bp
    text = re.sub(r'[^\w äöüßÄÖÜ-]', " ", art_text or "").strip()
    if text:
        woerter = []
        for w in text.split()[:8]:
            woerter += _ART_WORT.get(w.lower(), w).split()
        if woerter:
            where.append("cards.id IN (SELECT card_id FROM card_art_fts WHERE card_art_fts MATCH ?)")
            params.append(" AND ".join(f'"{w}"' for w in woerter[:12]))
    return where, params


def _card_query(q, set_id, serie, typ, kind, sort, richtung, rarity="", dex=0, region="intl",
                illustrator="", rgroup="", trainer_type="", regmark="", first=0, jahr_von=0, jahr_bis=0,
                preset="", familie=0, art_ort="", art_zeit="", art_wasser=0, art_merkmal="", art_text=""):
    where, params = [], []
    aw, ap = _art_frag(art_ort, art_zeit, art_wasser, art_merkmal, art_text)
    where += aw; params += ap
    if illustrator:
        where.append("illustrator = ?"); params.append(illustrator)
    if rgroup:
        teile, gp = [], []
        for g in rgroup.split(","):
            gd = RARITY_GROUPS.get(g)
            if not gd:
                continue
            for schluessel, holo in (("werte", None), ("werte_holo", 1), ("werte_ohne_holo", 0)):
                w = gd.get(schluessel) or []
                if not w:
                    continue
                frag = "rarity IN (%s)" % ",".join("?" * len(w))
                gp += w
                if holo is not None:
                    frag = f"({frag} AND COALESCE(has_holo, 0) = ?)"
                    gp.append(holo)
                teile.append(frag)
        if teile:
            where.append("(" + " OR ".join(teile) + ")"); params += gp
    if trainer_type:
        where.append("trainer_type = ?"); params.append(trainer_type)
    if regmark:
        marks = [m for m in regmark.split(",") if m]
        where.append("regulation_mark IN (%s)" % ",".join("?" * len(marks))); params += marks
    if first:
        where.append("has_first = 1")
    if jahr_von:
        where.append("release_date >= ?"); params.append(f"{int(jahr_von)}-01-01")
    if jahr_bis:
        where.append("release_date <= ?"); params.append(f"{int(jahr_bis)}-12-31")
    if preset in PRESETS or familie:
        con = get_db()
        if familie:
            dexe = _familie_dex(con, [int(familie)])
        else:
            pr = PRESETS[preset]
            dexe = _familie_dex(con, pr["dex"]) if pr["familie"] else pr["dex"]
        con.close()
        frag, dp = _dex_frag(dexe)
        where.append(frag); params += dp
    if q:
        where.append("(name_de LIKE ? OR name_en LIKE ? OR name_ja LIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if serie == "jp":
        region = "jp"; serie = ""
    if serie.startswith("jp:"):
        region = "jp"
        where.append("set_id IN (SELECT id FROM sets WHERE serie_id = ? AND region = 'jp')"); params.append(serie[3:])
        serie = ""
    if region in ("intl", "jp"):
        where.append("COALESCE(cards.region,'intl') = ?")
        params.append(region)
    if set_id:
        where.append("set_id = ?")
        params.append(set_id)
    if serie:
        if serie in AERA_ORDNUNG:
            frag, aera_params = _aera_sql(serie)
            where.append(frag)
            params += aera_params
        else:  # Rückfall: alte Links mit TCGdex-Serien-ID
            where.append("set_id IN (SELECT id FROM sets WHERE serie_id = ?)")
            params.append(serie)
    if typ:
        where.append("types LIKE ?")
        params.append(f'%"{typ}"%')
    if rarity:
        where.append("rarity = ?")
        params.append(rarity)
    if dex:
        where.append("dex_ids LIKE ?")
        params.append(f"%{int(dex)}%")
        # LIKE %25% träfe auch 251 — exakt nachprüfen über JSON-Ränder
        where[-1] = "(dex_ids LIKE ? OR dex_ids LIKE ? OR dex_ids LIKE ? OR dex_ids = ?)"
        params[-1:] = [f"[{int(dex)},%", f"%, {int(dex)},%", f"%, {int(dex)}]", f"[{int(dex)}]"]
    if kind:
        kinds = [k for k in kind.split(",") if k]
        if kinds:
            where.append("(" + " OR ".join("kinds LIKE ?" for _ in kinds) + ")")
            params += [f'%"{k}"%' for k in kinds]
    sql_where = (" WHERE " + " AND ".join(where)) if where else ""
    order = SORTS.get(sort, SORTS["datum"])
    # Japan lief hier auf „neueste zuerst", weil die alten Sets kaum Scans hatten. Seit
    # die Bilder von TCGplayer kommen (1996–2006 nahezu lückenlos), gibt es dafür keinen
    # Grund mehr — und zwei Regionen mit gegenläufiger Reihenfolge verwirren mehr, als
    # die Ausnahme je gebracht hat.
    if richtung == "desc":
        order = ", ".join(
            part.strip() + " DESC" if "IS NULL" not in part else part.strip()
            for part in order.split(",")
        )
    # Karten ohne Bild immer ans Ende – ein Raster voller Text-Platzhalter wirkt kaputt
    order = "(image_de IS NULL AND image_en IS NULL AND image_alt IS NULL), " + order
    return sql_where, params, order


def _card_brief(row):
    keys = row.keys()
    name_ja = row["name_ja"] if "name_ja" in keys else None
    region = (row["region"] if "region" in keys else None) or "intl"
    # Japanische Karten tragen den lateinischen Namen, nicht den japanischen: „Pikachu"
    # statt „ピカチュウ · Pikachu". Der japanische Markt ist nicht die Zielgruppe, und wer
    # hier sucht, erkennt die Zeichen nicht. Der Originalname bleibt als eigenes Feld
    # erhalten — die Kartenansicht zeigt ihn klein darunter, die Suche findet ihn weiter.
    return {
        "id": row["id"],
        "name": row["name_de"] or row["name_en"] or name_ja,
        "name_en": row["name_en"] or row["name_de"] or name_ja,
        "name_ja": name_ja if region == "jp" else None,
        "region": region,
        # Welche Sprache das Bild hat: alte WotC-Sets haben bei TCGdex keine deutschen Scans
        "img_lang": "de" if row["image_de"] else ("en" if row["image_en"] else ("alt" if ("image_alt" in keys and row["image_alt"]) else None)),
        "holo": bool(row["has_holo"]), "first": bool(row["has_first"]) if "has_first" in keys else False,
        "normal": bool(row["has_normal"]) if "has_normal" in keys else True,
        "illustrator": row["illustrator"] if "illustrator" in keys else None,
        "regmark": row["regulation_mark"] if "regulation_mark" in keys else None,
        "set_id": row["set_id"],
        # Bei japanischen Sets steht der englische Name vorn (aus dem Cardmarket-Katalog);
        # „拡張パック" sagt hier niemandem etwas.
        "set_name": ((row["set_name_en"] or row["set_name"]) if region == "jp"
                     else (SET_NAME_FIX_DE.get(row["set_id"], row["set_name"]) or row["set_name_en"])),
        "set_name_en": row["set_name_en"] or row["set_name"],
        "local_id": row["local_id"],
        "rarity": row["rarity"],
        "kinds": json.loads(row["kinds"] or "[]"),
        "types": json.loads(row["types"] or "[]"),
        "dex": row["first_dex"],
        "datum": row["release_date"],
        "reverse": bool(row["has_reverse"]),
        # Poké-Ball- und Master-Ball-Muster gibt es nur in zwei Sets. Vorher bot die
        # Oberfläche sie bei jeder Karte an, auch beim Grundset-Glurak von 1999.
        "muster": muster_fuer_set(row["set_id"]),
        "img": bool(row["image_de"] or row["image_en"] or ("image_alt" in keys and row["image_alt"])),
    }


_CARD_SELECT = (
    "SELECT cards.*, (SELECT name FROM sets WHERE sets.id = cards.set_id) set_name,"
    " (SELECT name_en FROM sets WHERE sets.id = cards.set_id) set_name_en FROM cards"
)


@app.get("/api/cards")
def cards(q: str = "", set_id: str = "", serie: str = "", typ: str = "",
          kind: str = "", rarity: str = "", dex: int = 0,
          sort: str = "datum", richtung: str = "asc",
          limit: int = 60, offset: int = 0, region: str = "intl",
          illustrator: str = "", rgroup: str = "", trainer_type: str = "", regmark: str = "", first: int = 0,
          jahr_von: int = 0, jahr_bis: int = 0, preset: str = "", familie: int = 0,
          art_ort: str = "", art_zeit: str = "", art_wasser: int = 0, art_merkmal: str = "", art_text: str = ""):
    limit = max(1, min(limit, 300))
    sql_where, params, order = _card_query(q, set_id, serie, typ, kind, sort, richtung, rarity, dex, region,
                                           illustrator, rgroup, trainer_type, regmark, first, jahr_von, jahr_bis,
                                           preset, familie, art_ort, art_zeit, art_wasser, art_merkmal, art_text)
    con = get_db()
    total = con.execute(f"SELECT COUNT(*) c FROM cards{sql_where}", params).fetchone()["c"]
    rows = con.execute(
        f"{_CARD_SELECT}{sql_where} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    con.close()
    return {"total": total, "karten": [_card_brief(r) for r in rows]}


@app.get("/api/cards/ids")
def card_ids(q: str = "", set_id: str = "", serie: str = "", typ: str = "",
             kind: str = "", rarity: str = "", dex: int = 0,
             sort: str = "datum", richtung: str = "asc",
             limit: int = 1000, region: str = "intl",
             illustrator: str = "", rgroup: str = "", trainer_type: str = "", regmark: str = "", first: int = 0,
             jahr_von: int = 0, jahr_bis: int = 0, preset: str = "", familie: int = 0,
             art_ort: str = "", art_zeit: str = "", art_wasser: int = 0, art_merkmal: str = "", art_text: str = ""):
    limit = max(1, min(limit, 2000))
    sql_where, params, order = _card_query(q, set_id, serie, typ, kind, sort, richtung, rarity, dex, region,
                                           illustrator, rgroup, trainer_type, regmark, first, jahr_von, jahr_bis,
                                           preset, familie, art_ort, art_zeit, art_wasser, art_merkmal, art_text)
    con = get_db()
    rows = con.execute(
        f"SELECT id FROM cards{sql_where} ORDER BY {order} LIMIT ?",
        params + [limit],
    ).fetchall()
    con.close()
    return {"ids": [r["id"] for r in rows]}


@app.post("/api/cards/nach_ids")
async def cards_nach_ids(request: Request):
    """Kartendaten in gegebener Reihenfolge — für Planer-Sortierung und Preise."""
    data = await request.json()
    ids = [str(i) for i in (data.get("ids") or [])][:5000]
    con = get_db()
    by_id = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        for r in con.execute(
            f"{_CARD_SELECT} WHERE cards.id IN ({','.join('?' * len(chunk))})", chunk
        ):
            by_id[r["id"]] = _card_brief(r)
    con.close()
    return {"karten": by_id}


@app.get("/api/sets/{set_id}/cards")
def set_cards(set_id: str):
    con = get_db()
    rows = con.execute(
        f"{_CARD_SELECT} WHERE set_id = ? ORDER BY local_num, local_id",
        (set_id,),
    ).fetchall()
    con.close()
    if not rows:
        raise HTTPException(404, "Set unbekannt oder noch nicht synchronisiert")
    return {"karten": [_card_brief(r) for r in rows]}


@app.get("/api/cards/{card_id}/detail")
def card_detail(card_id: str):
    """Alles für das Detail-Panel: Karte, Set, Preise (normal/holo), Verlauf, andere Drucke desselben Pokémon."""
    con = get_db()
    r = con.execute(f"{_CARD_SELECT} WHERE cards.id = ?", (card_id,)).fetchone()
    if not r:
        con.close()
        raise HTTPException(404, "Karte unbekannt")
    k = _card_brief(r)
    k["category"] = r["category"]; k["stage"] = r["stage"]; k["suffix"] = r["suffix"]
    k["hp"] = r["hp"] if "hp" in r.keys() else None
    k["evolve_from"] = r["evolve_from"] if "evolve_from" in r.keys() else None
    k["trainer_type"] = r["trainer_type"] if "trainer_type" in r.keys() else None
    if r["first_dex"]:
        fam = _familie_dex(con, [r["first_dex"]])
        k["familie"] = [{"dex": d, "name": n} for d, n in con.execute(
            "SELECT dex_id, name_de FROM pokemon WHERE dex_id IN (%s) ORDER BY evo_stufe, dex_id" % ",".join("?" * len(fam)), fam)] if len(fam) > 1 else []
    k["reverse"] = bool(r["has_reverse"]); k["normal"] = bool(r["has_normal"])
    sr = con.execute("SELECT id, name, name_en, release_date, total, official, serie_id, region FROM sets WHERE id = ?", (r["set_id"],)).fetchone()
    k["set"] = dict(sr) if sr else None
    if k["set"]:
        k["set"]["name"] = SET_NAME_FIX_DE.get(k["set"]["id"], k["set"]["name"]) or k["set"]["name_en"]
    pr = con.execute("SELECT eur, eur_holo, updated_at, cm_produkt, eur_low, eur_avg7,"
                     " eur_avg30, usd, usd_low, usd_mid, usd_high, preise_json, status,"
                     " kurs, eur_geschaetzt, cm_url, ptc_id, cm_import_am, cm_name,"
                     " cm_eindeutig, cm_expansion FROM card_prices"
                     " WHERE card_id = ?", (card_id,)).fetchone()
    # Im Detail gilt dieselbe Regel wie überall sonst: fehlt der Cardmarket-Trend, tritt die
    # Zahl der Zweitquelle an seine Stelle — mit dem Vermerk, woher sie kommt.
    if pr and pr["eur"] is None and pr["eur_geschaetzt"] is not None:
        pr = dict(pr); pr["eur"] = pr["eur_geschaetzt"]
    # Fehlt der Preis, weil das Cardmarket-Produkt noch anderen Karten gehört? Dann soll
    # die Karte das sagen können, statt kommentarlos einen Strich zu zeigen.
    # Zwei verschiedene Gründe für einen fehlenden Preis, und der Unterschied gehört
    # in die Oberfläche: entweder gehört das Cardmarket-Produkt mehreren Karten, oder
    # die Quelle führt für dieses Set gar keine Preisverknüpfung (ganze Sets wie Gym
    # Heroes oder die meisten japanischen Reihen).
    geteilt = 0
    ohne_quelle = bool(pr and pr["eur"] is None and not pr["cm_produkt"])
    if pr and pr["eur"] is None and pr["cm_produkt"]:
        geteilt = con.execute("SELECT COUNT(*) c FROM card_prices WHERE cm_produkt = ?",
                              (pr["cm_produkt"],)).fetchone()["c"]
    # Preis je Druckvariante: was TCGdex in `variants_detailed` mitliefert, plus die
    # beiden alten Reihen als Rückfall. Die Oberfläche schaltet damit den Preis um, wenn
    # jemand die Ausprägung wechselt — vorher stand bei Reverse Holo der Normalpreis.
    var_preise = {}
    if pr:
        try:
            var_preise = {a: v.get("eur") for a, v in json.loads(pr["preise_json"] or "{}").items()
                          if isinstance(v, dict) and v.get("eur") is not None}
        except Exception:
            var_preise = {}
        # Der Grundpreis gehört der Ausprägung, die es wirklich gibt. Bei einer Karte, die
        # nur als Holo existiert (jede LV.X), stand er sonst unter „normal" — einer
        # Ausprägung, die gar nicht zur Wahl steht.
        grund = ("normal" if k.get("normal") is not False
                 else "holo" if k.get("holo") else "reverse" if k.get("reverse")
                 else "first" if k.get("first") else "normal")
        var_preise.setdefault(grund, pr["eur"])
        if pr["eur_holo"] is not None:
            if k.get("reverse"):
                var_preise.setdefault("reverse", pr["eur_holo"])
            if k.get("holo") and grund != "holo":
                var_preise.setdefault("holo", pr["eur_holo"])
        var_preise = {a: w for a, w in var_preise.items() if w is not None}
    # Die obere US-Zahl ist das teuerste Einzelangebot, nicht der Marktrand: 132 Karten
    # führen dort 9.999 $. Gezeigt wird deshalb nur noch Tief bis Mitte.
    k["preis"] = {"eur": pr["eur"], "eur_holo": pr["eur_holo"], "stand": pr["updated_at"],
                  "geteilt": geteilt if geteilt > 1 else 0, "ohne_quelle": ohne_quelle,
                  "eur_low": pr["eur_low"], "eur_avg7": pr["eur_avg7"],
                  "eur_avg30": pr["eur_avg30"], "direkt": bool(pr["cm_import_am"]),
                  "usd": pr["usd"], "usd_low": pr["usd_low"], "usd_mid": pr["usd_mid"],
                  "varianten": var_preise, "status": pr["status"], "kurs": pr["kurs"],
                  "eur_geschaetzt": pr["eur_geschaetzt"]} if pr else None
    # Die fertige Cardmarket-Adresse gleich mitgeben, damit der Link im Dialog ein echter
    # Link sein kann. Wird er erst beim Klick aufgelöst, blockt der Browser das neue Fenster —
    # es entsteht nach einem await und gilt dann nicht mehr als Klick des Nutzers.
    # Die fertige Adresse gleich mitgeben — auch die aus dem Muster gebaute, damit der
    # Link im Dialog von Anfang an der richtige ist und nicht erst nachgetragen wird.
    # Geliefert wird der Pfad ab „/Pokemon/", nicht die volle Adresse: die Oberfläche
    # setzt Sprachraum, Kartensprache und Mindestzustand selbst davor und dahinter.
    k["cm_url"] = None
    try:
        muster = _cm_muster_laden(con).get(r["set_id"])
        pfad = pr["cm_url"] if pr and "cm_url" in pr.keys() else None
        if not pfad and muster:
            pfad = _cm_pfad_bauen(muster[0], muster[1], r["name_en"] or r["name_de"],
                                  r["stage"], r["suffix"], r["local_id"], muster[2])
        k["cm_url"] = pfad
        # Für den Rückfall: Cardmarkets eigener Kartenname, seine Erweiterung und ob die
        # Suche damit auf genau einen Treffer läuft. Ohne diese drei landete der Link auf
        # einer Namenssuche über zwanzig Jahrgänge — der häufigste Grund, warum ein
        # Cardmarket-Link „nicht klappt".
        if pr:
            k["cm_such"] = pr["cm_name"] if "cm_name" in pr.keys() else None
            k["cm_exp"] = pr["cm_expansion"] if "cm_expansion" in pr.keys() else None
            k["cm_direkt"] = bool(pfad) or bool(
                pr["cm_eindeutig"] if "cm_eindeutig" in pr.keys() else 0)
    except Exception:
        pass
    k["verlauf"] = [{"datum": h["datum"], "eur": h["eur"]} for h in con.execute(
        "SELECT datum, eur FROM price_history WHERE card_id = ? ORDER BY datum", (card_id,))]
    andere = []
    if r["first_dex"]:
        for a in con.execute(
            f"{_CARD_SELECT} WHERE first_dex = ? AND cards.id != ? AND COALESCE(cards.region,'intl') = ?"
            " AND (image_de IS NOT NULL OR image_en IS NOT NULL OR image_alt IS NOT NULL)"
            " ORDER BY release_date DESC LIMIT 24", (r["first_dex"], card_id, k["region"])):
            andere.append(_card_brief(a))
        k["andere_gesamt"] = con.execute("SELECT COUNT(*) c FROM cards WHERE first_dex = ?", (r["first_dex"],)).fetchone()["c"]
    k["andere"] = andere
    con.close()
    return k


# --- Import (Listen aus anderen Tools) ---------------------------------------
# Versteht je Zeile: „sv1 25“, „SV1-025“, „4/102 Charizard“, „Charizard 4/102“,
# „1x Glurak (Base Set) 4“, TCG-Collector/Collectr-CSV (Name;Set;Nummer …) und
# Cardmarket-Wants („1 Charizard (Base Set)“). Ergebnis: Treffer + unklare Zeilen.

_SET_CODES = None


def _set_codes():
    """Set-ID ↔ gängige Kürzel/Namen (klein, ohne Sonderzeichen)."""
    global _SET_CODES
    if _SET_CODES is not None:
        return _SET_CODES
    con = get_db()
    codes = {}
    # japanische Sets zuerst, internationale überschreiben: „sv1“ meint das internationale Set, nicht SV1 (JP)
    for r in con.execute("SELECT id, name, name_en, region FROM sets ORDER BY CASE WHEN region='jp' THEN 0 ELSE 1 END"):
        for key in (r["id"], r["name"], r["name_en"], SET_NAME_FIX_DE.get(r["id"])):
            if key:
                codes[re.sub(r"[^a-z0-9]", "", key.lower())] = r["id"]
    con.close()
    _SET_CODES = codes
    return codes


# Spaltennamen der großen Sammel-Apps (Collectr, TCG Collector, Cardmarket, eigene Tabellen).
# Klein geschrieben, ohne Sonderzeichen — so passt „Card Number" genauso wie „card_number".
_IMPORT_SPALTEN = {
    "name": ("name", "cardname", "productname", "card", "karte", "kartenname", "produkt", "title"),
    "set": ("set", "setname", "expansion", "edition", "series", "serie", "erweiterung", "setcode"),
    "nummer": ("number", "cardnumber", "collectornumber", "no", "nr", "nummer", "kartennummer", "#", "num"),
    "anzahl": ("quantity", "qty", "count", "amount", "anzahl", "menge", "owned", "stueck", "stück", "copies"),
    "zustand": ("condition", "zustand", "cond"),
    "sprache": ("language", "sprache", "lang"),
    "kaufpreis": ("pricepaid", "purchaseprice", "kaufpreis", "paid", "buyprice", "cost", "einkaufspreis"),
    "variante": ("variant", "variante", "finish", "printing", "foil", "holo"),
}
_ZUSTAND_WORTE = [("mint", "M"), ("nearmint", "NM"), ("nm", "NM"), ("excellent", "EX"), ("ex", "EX"), ("good", "GD"),
                  ("gd", "GD"), ("lightlyplayed", "LP"), ("lightplayed", "LP"), ("lp", "LP"),
                  ("moderatelyplayed", "PL"), ("played", "PL"), ("mp", "PL"), ("pl", "PL"),
                  ("heavilyplayed", "PO"), ("hp", "PO"), ("damaged", "PO"), ("poor", "PO"), ("po", "PO"), ("dmg", "PO")]
_SPRACHE_WORTE = {"german": "de", "deutsch": "de", "de": "de", "english": "en", "englisch": "en", "en": "en",
                  "japanese": "jp", "japanisch": "jp", "jp": "jp", "ja": "jp", "french": "fr", "fr": "fr",
                  "italian": "it", "it": "it", "spanish": "es", "es": "es", "portuguese": "pt", "pt": "pt",
                  "korean": "kr", "kr": "kr", "chinese": "cn", "cn": "cn", "russian": "ru", "ru": "ru"}


def _import_zustand(text):
    t = re.sub(r"[^a-z]", "", str(text or "").lower())
    if not t:
        return ""
    for wort, kuerzel in sorted(_ZUSTAND_WORTE, key=lambda x: -len(x[0])):
        if t.startswith(wort) or t == wort:
            return kuerzel
    return ""


def _import_kopf(zeile):
    """Erkennt eine CSV-Kopfzeile und liefert (Trenner, {Feld: Spaltenindex}) — oder None."""
    for sep in (";", ",", "\t"):
        if zeile.count(sep) < 1:
            continue
        teile = [re.sub(r"[^a-z0-9#]", "", t.strip().strip('"').lower()) for t in zeile.split(sep)]
        karte = {}
        for i, t in enumerate(teile):
            for feld, namen in _IMPORT_SPALTEN.items():
                if t in namen and feld not in karte:
                    karte[feld] = i
        if "name" in karte and len(karte) >= 2:
            return sep, karte
    return None


def _import_csv_zeile(zeile, sep, karte, con):
    """Eine Datenzeile einer erkannten CSV: Name, Set und Nummer über die Spalten, dazu
    Anzahl, Zustand, Sprache und Kaufpreis — damit landet ein Collectr-Export mit einem
    Klick in der Sammlung, nicht nur als Kartenliste im Binder."""
    import csv as _csv
    try:
        teile = next(_csv.reader([zeile], delimiter=sep))
    except Exception:
        teile = zeile.split(sep)
    def spalte(feld):
        i = karte.get(feld)
        return teile[i].strip() if i is not None and i < len(teile) else ""
    name, setname, nummer = spalte("name"), spalte("set"), spalte("nummer")
    if not name:
        return None
    nummer = nummer.split("/")[0].strip()
    synth = name + (f" ({setname})" if setname else "") + (f" {nummer}" if nummer else "")
    e = _import_zeile(synth, con, roh=zeile)
    if not e:
        return None
    try:
        e["anzahl"] = max(1, min(999, int(float(spalte("anzahl") or 1))))
    except Exception:
        e["anzahl"] = 1
    e["zustand"] = _import_zustand(spalte("zustand"))
    e["sprache"] = _SPRACHE_WORTE.get(re.sub(r"[^a-z]", "", spalte("sprache").lower()), "")
    v = spalte("variante").lower()
    e["variante"] = "reverse" if "reverse" in v else ("holo" if "holo" in v or "foil" in v else "normal")
    try:
        kp = spalte("kaufpreis").replace("€", "").replace("$", "").replace(",", ".").strip()
        e["kaufpreis"] = round(float(kp), 2) if kp else None
    except Exception:
        e["kaufpreis"] = None
    return e


def _import_zeile(zeile, con, roh=None):
    z = zeile.strip()
    if not z or z.lower().startswith(("name;", "name,", "card name", "quantity", "menge")):
        return None
    anzahl = 1
    ma = re.match(r"^\s*(\d{1,3})\s*[x×]\s*", z)
    if ma:
        anzahl = max(1, int(ma.group(1)))
    z = re.sub(r"^\s*\d+\s*[x×]\s*", "", z)          # „2x “ vorne weg
    z = re.sub(r"^\s*\d+\s+(?=[A-Za-zÄÖÜäöü])", "", z)  # „1 Charizard …“
    codes = _set_codes()
    sep = ";" if z.count(";") >= 2 else ("," if z.count(",") >= 2 else ("\t" if "\t" in z else None))
    name = setname = nummer = None
    total = None   # „4/102“: die Set-Größe grenzt das Set ein, wenn kein Setname dabeisteht
    mt = re.search(r"\b(\d{1,3})\s*/\s*(\d{2,3})\b", z)
    if mt:
        total = int(mt.group(2))
    if sep:
        teile = [p.strip().strip('"') for p in z.split(sep)]
        name = teile[0]
        for p in teile[1:]:
            if re.fullmatch(r"[A-Za-z]{0,4}\d{1,3}[a-z]?(/\d+)?", p) and nummer is None:
                nummer = p.split("/")[0]
            elif p and setname is None and not re.fullmatch(r"[\d.,€$ ]+", p):
                setname = p
    else:
        m = re.match(r"^([a-z0-9]{2,8})[-\s](\d{1,3}[a-z]?)$", z, re.I)     # sv1-025 / sv1 25
        if m:
            setname, nummer = m.group(1), m.group(2)
        else:
            m = re.match(r"^(.*?)\s*\((.+?)\)\s*(\d{1,3})?(?:/\d+)?\s*$", z)  # Name (Set) 4
            if m:
                name, setname, nummer = m.group(1).strip(), m.group(2).strip(), m.group(3)
            else:
                m = re.match(r"^(?:(\d{1,3})/\d+\s+)?(.*?)(?:\s+(\d{1,3})/\d+)?$", z)  # 4/102 Name | Name 4/102
                if m:
                    nummer = m.group(1) or m.group(3)
                    name = m.group(2).strip()
    set_id = codes.get(re.sub(r"[^a-z0-9]", "", (setname or "").lower())) if setname else None
    where, params = [], []
    if set_id:
        where.append("set_id = ?"); params.append(set_id)
    else:
        where.append("COALESCE(cards.region,'intl') = 'intl'")
        if total:
            where.append("set_id IN (SELECT id FROM sets WHERE official = ? OR total = ?)"); params += [total, total]
    if nummer:
        where.append("local_num = ?"); params.append(_local_num(nummer))
    if name:
        where.append("(name_de LIKE ? OR name_en LIKE ?)"); params += [f"%{name}%", f"%{name}%"]
    if not where:
        return {"zeile": roh or zeile, "id": None}
    rows = con.execute(f"{_CARD_SELECT} WHERE {' AND '.join(where)} ORDER BY release_date DESC LIMIT 3", params).fetchall()
    if not rows and name and set_id:   # Name passt nicht zur Nummer → Nummer + Set reicht
        rows = con.execute(f"{_CARD_SELECT} WHERE set_id = ? AND local_num = ? LIMIT 1", (set_id, _local_num(nummer or ""))).fetchall()
    if not rows:
        return {"zeile": roh or zeile, "id": None}
    k = _card_brief(rows[0])
    return {"zeile": roh or zeile, "id": k["id"], "name": k["name"], "set_name": k["set_name"], "local_id": k["local_id"],
            "sicher": bool(set_id and nummer) or len(rows) == 1, "anzahl": anzahl}


@app.post("/api/import/parse")
async def import_parse(request: Request):
    _drossel(request, "import")
    data = await request.json()
    text = str(data.get("text") or "")[:60000]
    return await run_in_threadpool(_import_parse_sync, text)


def _import_parse_sync(text: str):
    """Läuft im Threadpool: jede Zeile kostet eine Suche über den ganzen Katalog.
    300 Zeilen sind rund 10 Sekunden Rechenzeit — mehr nimmt eine Anfrage nicht."""
    con = get_db()
    treffer, unklar = [], []
    zeilen = [z for z in text.splitlines() if z.strip()]
    # Eine Kopfzeile mit bekannten Spaltennamen schaltet auf den Tabellenmodus um: dann
    # zählen Spalten, nicht Muster — und Anzahl, Zustand, Sprache kommen mit.
    kopf = _import_kopf(zeilen[0]) if zeilen else None
    if kopf:
        sep, karte = kopf
        for zeile in zeilen[1:301]:
            e = _import_csv_zeile(zeile, sep, karte, con)
            if e is None:
                continue
            (treffer if e["id"] else unklar).append(e)
    else:
        for zeile in zeilen[:300]:
            e = _import_zeile(zeile, con)
            if e is None:
                continue
            (treffer if e["id"] else unklar).append(e)
    con.close()
    return {"treffer": treffer, "unklar": [u["zeile"] for u in unklar],
            "abgeschnitten": max(0, len(zeilen) - (301 if kopf else 300)),
            "tabelle": bool(kopf), "spalten": sorted(kopf[1]) if kopf else []}


@app.get("/api/pokedex")
def pokedex(gens: str = ""):
    wanted = {int(g) for g in gens.split(",") if g.strip().isdigit()} if gens else None
    con = get_db()
    rows = con.execute("SELECT * FROM pokemon ORDER BY dex_id").fetchall()
    con.close()
    result = [
        {"dex": r["dex_id"], "name": r["name_de"], "name_en": r["name_en"], "gen": r["gen"]}
        for r in rows
        if wanted is None or r["gen"] in wanted
    ]
    return {"pokemon": result}
