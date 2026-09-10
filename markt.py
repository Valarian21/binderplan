"""Markt: was sich im Katalog bewegt — Sets, Ären, Pokémon, Illustratoren.

Warum ein eigenes Modul neben analytics.py: die Marktzahlen sind nicht mehr eine Auswertung
je Anfrage, sondern ein Tagesstand. Einmal am Tag, direkt nach dem Preislauf, wird jede Ebene
ausgerechnet und in `markt_tag` abgelegt; jede Anfrage liest daraus. Vorher summierte jeder
Aufruf über 30.000 Zeilen, und trotzdem gab es keine Zeitachse.

**Woher die Bewegung kommt.** Die eigene Preishistorie führt den Vollkatalog erst seit dem
02.09.2026 — für „30 Tage" reicht das nicht. Cardmarket liefert aber je Karte selbst einen
7- und einen 30-Tage-Schnitt (`eur_avg7`, `eur_avg30`, 30.384 Karten). Die Bewegung ist
deshalb der heutige Trend gegen diesen Schnitt, nicht die eigene Reihe. Sobald die Historie
lang genug ist, kann `_bewegung_aus_historie` die Quelle ersetzen; die Definition bleibt.

**Median, nicht Durchschnitt.** Ein Set ist nicht gestiegen, weil eine 6.000-€-Karte gestiegen
ist. Gezählt wird der Median der relativen Bewegung über die Karten des Sets.

**Ausreißer fliegen raus.** Springt der Trend einer Karte über das Dreifache oder unter ein
Drittel ihres Schnitts, ist das fast nie der Markt, sondern eine falsche Cardmarket-Zuordnung
in der Quelle (gemessen: 62 von 10.067 Karten über 2 €). Solche Karten zählen nirgends mit
und werden als `ausreisser` protokolliert.
"""

import datetime
import json
import statistics

import wert as _wert

# Ab hier zählt eine Karte für die Bewegung: darunter sind die Sprünge Rundung.
MIN_PREIS = 1.0
# Ausreißergrenze (Trend geteilt durch Schnitt).
AUS_UNTEN, AUS_OBEN = 1 / 3, 3.0
# So viele Karten braucht eine Gruppe, um in einer Rangliste zu erscheinen.
MIN_KARTEN_SET = 25
MIN_KARTEN_GRUPPE = 20
# Tage, über die eine Sparkline läuft.
SPARK_TAGE = 30

# Ären als Datumsfenster. Bewusst gröber als die elf Sammler-Ären in main.py: sechs Kurven
# sind noch lesbar, elf nicht mehr.
AEREN = [
    ("wotc", "WotC 1999–2003", "1996-01-01", "2003-06-14"),
    ("ex", "EX bis Platin", "2003-06-15", "2010-04-30"),
    ("hgss", "HGSS bis XY", "2010-05-01", "2016-12-31"),
    ("sm", "Sonne & Mond", "2017-01-01", "2019-12-31"),
    ("swsh", "Schwert & Schild", "2020-01-01", "2022-12-31"),
    ("sv", "Karmesin & Purpur", "2023-01-01", "2099-12-31"),
]

EBENEN = ("gesamt", "set", "aera", "pokemon", "illustrator")


def _heute():
    return datetime.date.today().isoformat()


def _aera_von(datum):
    """Zu welcher Ära gehört ein Erscheinungsdatum?"""
    if not datum:
        return None
    for kuerzel, _name, von, bis in AEREN:
        if von <= datum[:10] <= bis:
            return kuerzel
    return None


def aera_name(kuerzel):
    for k, name, _v, _b in AEREN:
        if k == kuerzel:
            return name
    return kuerzel


def ist_ausreisser(eur, schnitt):
    """Preissprung, den kein Markt macht — fast immer ein Zuordnungsfehler der Quelle."""
    if not eur or not schnitt or schnitt <= 0:
        return False
    q = eur / schnitt
    return q > AUS_OBEN or q < AUS_UNTEN


def _median_bewegung(paare):
    """Median der relativen Bewegung über eine Kartenmenge → (median, gezählt, ausreisser)."""
    werte, aus = [], 0
    for eur, schnitt in paare:
        if not eur or not schnitt or schnitt <= 0 or eur < MIN_PREIS or schnitt < MIN_PREIS:
            continue
        if ist_ausreisser(eur, schnitt):
            aus += 1
            continue
        werte.append(eur / schnitt - 1)
    if not werte:
        return None, 0, aus
    return round(statistics.median(werte) * 100, 2), len(werte), aus


# --------------------------------------------------------------- Tabelle & Job

def tabelle_anlegen(con):
    con.execute("""CREATE TABLE IF NOT EXISTS markt_tag (
        datum TEXT NOT NULL,
        ebene TEXT NOT NULL,
        schluessel TEXT NOT NULL,
        name TEXT,
        n INTEGER,
        summe REAL,
        median REAL,
        hoechst REAL,
        bew7 REAL,
        bew7_n INTEGER,
        bew30 REAL,
        bew30_n INTEGER,
        ausreisser INTEGER,
        geplant INTEGER,
        PRIMARY KEY (datum, ebene, schluessel))""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_markt_ebene ON markt_tag(ebene, datum)")
    con.commit()


def _karten_laden(con):
    """Alle bepreisten westlichen Karten mit den Merkmalen, nach denen gruppiert wird.

    `eur_avg7` wird nach dem Laden durch den eigenen Preis von vor sieben Tagen ersetzt:
    Cardmarkets `avg7` ist der Schnitt *verkaufter* Exemplare, `eur` der Trend der aktuellen
    Angebote — ihr Quotient ist keine Veränderung über die Zeit (Audit 11.09.2026, B1).
    `eur_avg30` bleibt vorerst der Verkaufsschnitt und heißt in der Oberfläche seitdem auch
    so („gg. Schnitt"); sobald price_history dreißig Tage weit zurückreicht, kommt auch dieses
    Fenster aus `wert.historie_basis`."""
    karten = [dict(r) for r in con.execute(
        "SELECT p.card_id, p.eur, p.eur_avg7, p.eur_avg30, p.usd,"
        " c.set_id, c.release_date, c.first_dex, c.illustrator,"
        " (SELECT name FROM sets WHERE sets.id = c.set_id) AS set_name"
        " FROM card_prices p JOIN cards c ON c.id = p.card_id"
        " WHERE p.eur IS NOT NULL AND COALESCE(c.region,'intl') = 'intl'")]
    basis7 = _wert.historie_basis(con, [k["card_id"] for k in karten], 7)
    for k in karten:
        k["eur_avg7"] = (basis7.get(k["card_id"]) or (None,))[0]
    return karten


def _geplant_zaehlen(con):
    """Wie oft ein Set in echten Bindern vorkommt — das Signal, das kein Konkurrent hat."""
    zaehler = {}
    for (roh,) in con.execute("SELECT items FROM binders"):
        try:
            posten = json.loads(roh or "[]")
        except Exception:
            continue
        for p in posten:
            if isinstance(p, dict) and p.get("type") == "card" and p.get("id"):
                sid = str(p["id"]).rsplit("-", 1)[0]
                zaehler[sid] = zaehler.get(sid, 0) + 1
    return zaehler


def _pokemon_namen(con):
    return {r["dex_id"]: r["name_de"] or r["name_en"]
            for r in con.execute("SELECT dex_id, name_de, name_en FROM pokemon")}


def _gruppen_rechnen(karten, schluessel_fn, name_fn=None):
    """Kennzahlen je Gruppe: Summe, Median-Preis, Höchstpreis, Bewegung 7 und 30 Tage."""
    gruppen = {}
    for k in karten:
        s = schluessel_fn(k)
        if s is None or s == "":
            continue
        gruppen.setdefault(s, []).append(k)
    aus = []
    for s, liste in gruppen.items():
        preise = [k["eur"] for k in liste if k["eur"]]
        if not preise:
            continue
        b7, b7n, a7 = _median_bewegung([(k["eur"], k["eur_avg7"]) for k in liste])
        b30, b30n, a30 = _median_bewegung([(k["eur"], k["eur_avg30"]) for k in liste])
        aus.append({
            "schluessel": str(s),
            "name": name_fn(s, liste) if name_fn else str(s),
            "n": len(preise),
            "summe": round(sum(preise), 2),
            "median": round(statistics.median(preise), 2),
            "hoechst": round(max(preise), 2),
            "bew7": b7, "bew7_n": b7n,
            "bew30": b30, "bew30_n": b30n,
            "ausreisser": max(a7, a30),
        })
    return aus


def markt_job(con, datum=None):
    """Den Tagesstand aller Ebenen rechnen und ablegen. Läuft nach dem Preislauf."""
    tabelle_anlegen(con)
    datum = datum or _heute()
    karten = _karten_laden(con)
    if not karten:
        return {"zeilen": 0}
    geplant = _geplant_zaehlen(con)
    namen = _pokemon_namen(con)

    zeilen = []
    # Gesamt
    b7, b7n, a7 = _median_bewegung([(k["eur"], k["eur_avg7"]) for k in karten])
    b30, b30n, a30 = _median_bewegung([(k["eur"], k["eur_avg30"]) for k in karten])
    preise = [k["eur"] for k in karten if k["eur"]]
    zeilen.append({"ebene": "gesamt", "schluessel": "alle", "name": "Westlicher Katalog",
                   "n": len(preise), "summe": round(sum(preise), 2),
                   "median": round(statistics.median(preise), 2), "hoechst": round(max(preise), 2),
                   "bew7": b7, "bew7_n": b7n, "bew30": b30, "bew30_n": b30n,
                   "ausreisser": max(a7, a30), "geplant": sum(geplant.values())})

    for z in _gruppen_rechnen(karten, lambda k: k["set_id"],
                              lambda s, li: li[0]["set_name"] or s):
        z["ebene"] = "set"
        z["geplant"] = geplant.get(z["schluessel"], 0)
        zeilen.append(z)
    for z in _gruppen_rechnen(karten, lambda k: _aera_von(k["release_date"]),
                              lambda s, li: aera_name(s)):
        z["ebene"] = "aera"
        zeilen.append(z)
    for z in _gruppen_rechnen(karten, lambda k: k["first_dex"],
                              lambda s, li: namen.get(int(s), str(s))):
        z["ebene"] = "pokemon"
        zeilen.append(z)
    for z in _gruppen_rechnen(karten, lambda k: k["illustrator"]):
        z["ebene"] = "illustrator"
        zeilen.append(z)

    con.executemany(
        "INSERT OR REPLACE INTO markt_tag (datum, ebene, schluessel, name, n, summe, median,"
        " hoechst, bew7, bew7_n, bew30, bew30_n, ausreisser, geplant)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(datum, z["ebene"], z["schluessel"], z["name"], z["n"], z["summe"], z["median"],
          z["hoechst"], z.get("bew7"), z.get("bew7_n"), z.get("bew30"), z.get("bew30_n"),
          z.get("ausreisser"), z.get("geplant")) for z in zeilen])
    con.commit()
    return {"zeilen": len(zeilen), "datum": datum}


def rueckwirkend_fuellen(con, max_tage=45):
    """Die Summen vergangener Messtage aus der Preishistorie nachtragen.

    Damit hat die Sparkline vom ersten Tag an eine Linie und nicht einen Punkt. Bewegungen
    lassen sich rückwirkend nicht rekonstruieren — die Cardmarket-Schnitte gibt es nur für
    heute —, deshalb bleiben `bew7`/`bew30` an diesen Tagen leer."""
    tabelle_anlegen(con)
    tage = [r["datum"] for r in con.execute(
        "SELECT datum FROM price_tage GROUP BY datum HAVING MAX(karten) >= 20000"
        " ORDER BY datum DESC LIMIT ?", (max_tage,))]
    schon = {r["datum"] for r in con.execute(
        "SELECT DISTINCT datum FROM markt_tag WHERE ebene='gesamt'")}
    offen = [t for t in tage if t not in schon]
    if not offen:
        return {"tage": 0}
    merkmale = {r["id"]: r for r in con.execute(
        "SELECT c.id, c.set_id, c.release_date, c.first_dex, c.illustrator,"
        " (SELECT name FROM sets WHERE sets.id = c.set_id) AS set_name FROM cards c"
        " WHERE COALESCE(c.region,'intl') = 'intl'")}
    geschrieben = 0
    for tag in offen:
        stand = {r["card_id"]: r["eur"] for r in con.execute(
            "SELECT card_id, eur FROM (SELECT card_id, eur, ROW_NUMBER() OVER"
            " (PARTITION BY card_id ORDER BY datum DESC) rn FROM price_history"
            " WHERE datum <= ? AND eur IS NOT NULL) WHERE rn = 1", (tag,))}
        karten = [{"card_id": cid, "eur": eur, "eur_avg7": None, "eur_avg30": None,
                   "set_id": merkmale[cid]["set_id"], "release_date": merkmale[cid]["release_date"],
                   "first_dex": merkmale[cid]["first_dex"], "illustrator": merkmale[cid]["illustrator"],
                   "set_name": merkmale[cid]["set_name"]}
                  for cid, eur in stand.items() if cid in merkmale]
        if len(karten) < 5000:      # ein halber Katalog ist kein Tagesstand
            continue
        zeilen = []
        preise = [k["eur"] for k in karten]
        zeilen.append(("gesamt", "alle", "Westlicher Katalog", len(preise), round(sum(preise), 2),
                       round(statistics.median(preise), 2), round(max(preise), 2)))
        for ebene, fn, nfn in (
                ("set", lambda k: k["set_id"], lambda s, li: li[0]["set_name"] or s),
                ("aera", lambda k: _aera_von(k["release_date"]), lambda s, li: aera_name(s)),
                ("pokemon", lambda k: k["first_dex"], lambda s, li: str(s)),
                ("illustrator", lambda k: k["illustrator"], None)):
            for z in _gruppen_rechnen(karten, fn, nfn):
                zeilen.append((ebene, z["schluessel"], z["name"], z["n"], z["summe"],
                               z["median"], z["hoechst"]))
        con.executemany(
            "INSERT OR IGNORE INTO markt_tag (datum, ebene, schluessel, name, n, summe,"
            " median, hoechst) VALUES (?,?,?,?,?,?,?,?)",
            [(tag, *z) for z in zeilen])
        geschrieben += 1
    con.commit()
    return {"tage": geschrieben}


def pruefen(con):
    """Springt der Katalogwert gegenüber dem Vortag, stimmt etwas mit den Preisen nicht.

    Der Markt ist eine bezahlte Ansicht; er darf keine falschen Nachrichten zeigen. Gibt
    einen Meldungstext zurück, wenn etwas auffällt, sonst None."""
    zeilen = [dict(r) for r in con.execute(
        "SELECT datum, summe, n, ausreisser FROM markt_tag WHERE ebene='gesamt'"
        " ORDER BY datum DESC LIMIT 2")]
    if len(zeilen) < 2 or not zeilen[1]["summe"]:
        return None
    heute, gestern = zeilen[0], zeilen[1]
    ab = (heute["summe"] - gestern["summe"]) / gestern["summe"] * 100
    if abs(ab) < 5:
        return None
    return (f"Binderplan-Markt: Katalogwert {ab:+.1f} % gegenüber dem Vortag "
            f"({gestern['summe']:,.0f} € → {heute['summe']:,.0f} €, {heute['n']} Karten, "
            f"{heute['ausreisser'] or 0} Ausreißer). Preislauf prüfen.").replace(",", ".")


# ------------------------------------------------------------------ Endpunkte

import functools

# Tagesstand-Cache: Die Markt-Antworten ändern sich nur mit dem Preislauf um 04:30. Vorher
# rechnete jede Anfrage die Aggregation neu (4,8 s für /api/markt/heute, gemessen 09.09.2026,
# das SQL selbst brauchte 4 ms). Schlüssel: Endpunkt, Argumente, Pro-Stufe, Tag.
_TAGESCACHE = {}
_cache_helfer = {}


def _tageskache(fn):
    """Antworten je (Funktion, Argumente, Pro, Tag) im Prozess halten. Der Tag wird bei
    jeder Anfrage aus markt_tag gelesen (ein MAX über einen Index) – wechselt er, verfällt
    alles Alte von selbst."""
    @functools.wraps(fn)
    def wrapper(request, *args, **kwargs):
        con = _cache_helfer["get_db"]()
        try:
            r = con.execute("SELECT MAX(datum) d FROM markt_tag").fetchone()
            tag = r["d"] if r else None
        finally:
            con.close()
        user = _cache_helfer["current_user"](request)
        voll = bool(user) and _cache_helfer["ist_pro_stufe"](user)
        schluessel = (fn.__name__, tag, voll, tuple(args), tuple(sorted(kwargs.items())))
        treffer = _TAGESCACHE.get(schluessel)
        if treffer is not None:
            return treffer
        wert = fn(request, *args, **kwargs)
        if len(_TAGESCACHE) > 400:
            _TAGESCACHE.clear()
        _TAGESCACHE[schluessel] = wert
        return wert
    return wrapper


def register(app, *, get_db, current_user, require_user, ist_pro, ist_pro_stufe,
             betreiber_melden=None):
    from fastapi import HTTPException, Request
    _cache_helfer.update(get_db=get_db, current_user=current_user, ist_pro_stufe=ist_pro_stufe)

    def _verlauf(con, ebene, schluessel, tage=SPARK_TAGE):
        """Die Summen der letzten Tage als Sparkline-Punkte."""
        von = (datetime.date.today() - datetime.timedelta(days=tage)).isoformat()
        return [{"datum": r["datum"], "wert": r["summe"]} for r in con.execute(
            "SELECT datum, summe FROM markt_tag WHERE ebene=? AND schluessel=? AND datum >= ?"
            " ORDER BY datum", (ebene, str(schluessel), von))]

    def _letzter_tag(con):
        r = con.execute("SELECT MAX(datum) d FROM markt_tag").fetchone()
        return r["d"] if r else None

    def _zeilen(con, ebene, tag, min_n=0):
        return [dict(r) for r in con.execute(
            "SELECT * FROM markt_tag WHERE ebene=? AND datum=? AND n >= ?",
            (ebene, tag, min_n))]

    def _spitzen(zeilen, feld, min_n, anzahl=5, richtung="hoch"):
        brauchbar = [z for z in zeilen if z.get(feld) is not None and (z["bew30_n"] or 0) >= min_n]
        brauchbar.sort(key=lambda z: z[feld], reverse=(richtung == "hoch"))
        gefiltert = [z for z in brauchbar
                     if (z[feld] > 0 if richtung == "hoch" else z[feld] < 0)]
        return gefiltert[:anzahl]

    def _mit_verlauf(con, ebene, zeilen, tage=SPARK_TAGE):
        for z in zeilen:
            z["verlauf"] = _verlauf(con, ebene, z["schluessel"], tage)
        return zeilen

    @app.get("/api/markt/heute")
    @_tageskache
    def markt_heute(request: Request, fenster: int = 30):
        """Die Startseite des Markts. Der Kopf ist für alle sichtbar, die Ranglisten für Pro.

        Eine gesperrte Seite, die nur einen unscharfen Platzhalter zeigt, verkauft nichts —
        die erste Zeile echter Zahlen schon."""
        user = current_user(request)
        voll = bool(user) and ist_pro_stufe(user)
        feld = "bew7" if fenster == 7 else "bew30"
        con = get_db()
        tabelle_anlegen(con)
        tag = _letzter_tag(con)
        if not tag:
            con.close()
            return {"pro": voll, "leer": True}

        gesamt = con.execute(
            "SELECT * FROM markt_tag WHERE ebene='gesamt' AND datum=?", (tag,)).fetchone()
        gesamt = dict(gesamt) if gesamt else {}
        gesamt["verlauf"] = _verlauf(con, "gesamt", "alle")

        sets = _zeilen(con, "set", tag)
        monat = _spitzen(sets, "bew30", MIN_KARTEN_SET, 1)
        # Zwei Kacheln, die dasselbe Set nennen, sind eine Kachel. Die Woche nimmt dann
        # das nächstbeste Set — die beiden Fenster sollen zwei Nachrichten tragen.
        woche_liste = _spitzen(sets, "bew7", MIN_KARTEN_SET, 2)
        if monat and woche_liste and woche_liste[0]["schluessel"] == monat[0]["schluessel"]:
            woche_liste = woche_liste[1:]
        woche = woche_liste[:1]
        geplant = sorted([s for s in sets if s.get("geplant")],
                         key=lambda s: -s["geplant"])[:1]
        kopf = {
            "pro": voll, "stand": tag,
            "gesamt": gesamt,
            "monat": _mit_verlauf(con, "set", monat)[0] if monat else None,
            "woche": _mit_verlauf(con, "set", woche)[0] if woche else None,
            "geplant": geplant[0] if geplant else None,
        }
        if not voll:
            con.close()
            return kopf

        kopf["aufwind"] = _mit_verlauf(con, "set", _spitzen(sets, feld, MIN_KARTEN_SET, 5, "hoch"))
        kopf["druck"] = _mit_verlauf(con, "set", _spitzen(sets, feld, MIN_KARTEN_SET, 5, "runter"))
        aeren = _zeilen(con, "aera", tag)
        reihenfolge = {k: i for i, (k, *_r) in enumerate(AEREN)}
        aeren.sort(key=lambda z: reihenfolge.get(z["schluessel"], 99))
        kopf["aeren"] = _mit_verlauf(con, "aera", aeren)
        kopf["karten"] = _karten_bewegung(con, feld)
        kopf["fenster"] = fenster
        con.close()
        return kopf

    def _karten_bewegung(con, feld, anzahl=10):
        """Die größten Bewegungen einzelner Karten — Ausreißer ausdrücklich ausgelassen.

        Vorher stand hier, was zwischen zwei Tagen am stärksten gesprungen war. Das waren
        Karten, deren Cardmarket-Zuordnung sich geändert hatte (−99,8 % an einem Tag), also
        Datenfehler in der Rolle einer Marktnachricht."""
        spalte = "eur_avg7" if feld == "bew7" else "eur_avg30"
        roh = [dict(r) for r in con.execute(
            f"SELECT p.card_id id, p.eur, p.{spalte} schnitt, c.name_de, c.name_en, c.local_id,"
            f" (SELECT name FROM sets WHERE sets.id = c.set_id) AS set_name"
            f" FROM card_prices p JOIN cards c ON c.id = p.card_id"
            f" WHERE p.eur >= 2 AND p.{spalte} >= 2 AND COALESCE(c.region,'intl')='intl'")]
        werte = []
        for z in roh:
            if ist_ausreisser(z["eur"], z["schnitt"]):
                continue
            werte.append({"id": z["id"], "name": z["name_de"] or z["name_en"],
                          "set": z["set_name"], "nr": z["local_id"],
                          "alt": round(z["schnitt"], 2), "neu": round(z["eur"], 2),
                          "prozent": round((z["eur"] / z["schnitt"] - 1) * 100, 1)})
        werte.sort(key=lambda z: -z["prozent"])
        return {"basis": len(werte),
                "hoch": [z for z in werte[:anzahl] if z["prozent"] > 0],
                "runter": [z for z in reversed(werte[-anzahl:]) if z["prozent"] < 0]}

    SORTEN = {"bewegung30": ("bew30", True), "bewegung7": ("bew7", True),
              "summe": ("summe", True), "median": ("median", True),
              "hoechst": ("hoechst", True), "karten": ("n", True),
              "geplant": ("geplant", True), "name": ("name", False)}

    @app.get("/api/markt/rangliste")
    @_tageskache
    def markt_rangliste(request: Request, ebene: str = "set", sortier: str = "bewegung30",
                        limit: int = 40, richtung: str = "ab"):
        """Eine Ebene als Tabelle: Sets, Ären, Pokémon oder Illustratoren."""
        user = require_user(request)
        if not ist_pro_stufe(user):
            return {"pro": False}
        if ebene not in EBENEN:
            raise HTTPException(400, "Unbekannte Ebene.")
        feld, absteigend = SORTEN.get(sortier, SORTEN["bewegung30"])
        if richtung == "auf":
            absteigend = not absteigend
        min_n = MIN_KARTEN_SET if ebene == "set" else MIN_KARTEN_GRUPPE
        con = get_db()
        tag = _letzter_tag(con)
        zeilen = _zeilen(con, ebene, tag, min_n) if tag else []
        # Nach Bewegung sortieren heißt: nur Zeilen, die überhaupt eine haben.
        if feld in ("bew7", "bew30"):
            zeilen = [z for z in zeilen if z.get(feld) is not None]
        zeilen.sort(key=lambda z: (z.get(feld) is None, z.get(feld) if not isinstance(z.get(feld), str) else z.get(feld).lower()),
                    reverse=absteigend)
        zeilen = zeilen[:max(5, min(200, limit))]
        if ebene == "set":
            gesamt = {r["id"]: r for r in con.execute(
                "SELECT id, total, official, release_date, serie_name FROM sets")}
            for z in zeilen:
                s = gesamt.get(z["schluessel"])
                if s:
                    z["release_date"] = s["release_date"]
                    z["serie_name"] = s["serie_name"]
                    ges = s["official"] or s["total"]
                    if ges:
                        # Über 100 % gibt es nicht: manche Sets führen mehr bepreiste Karten
                        # als die offizielle Zahl (Sonderdrucke, Promos im selben Set).
                        z["abdeckung"] = min(100, round(z["n"] / ges * 100))
        _mit_verlauf(con, ebene, zeilen)
        con.close()
        return {"pro": True, "stand": tag, "ebene": ebene, "sortier": sortier, "zeilen": zeilen}

    @app.get("/api/markt/set/{set_id}")
    @_tageskache
    def markt_set(request: Request, set_id: str):
        """Eine Set-Seite: Index, Verteilung, Bewegung je Karte, teuerste Karten."""
        user = require_user(request)
        if not ist_pro_stufe(user):
            return {"pro": False}
        con = get_db()
        tag = _letzter_tag(con)
        kopf = con.execute("SELECT * FROM markt_tag WHERE ebene='set' AND schluessel=? AND datum=?",
                           (set_id, tag)).fetchone()
        stamm = con.execute("SELECT id, name, serie_name, release_date, total, official, symbol"
                            " FROM sets WHERE id = ?", (set_id,)).fetchone()
        if not stamm:
            con.close()
            raise HTTPException(404, "Set nicht gefunden.")
        karten = [dict(r) for r in con.execute(
            "SELECT p.card_id id, p.eur, p.eur_avg7, p.eur_avg30, c.name_de, c.name_en,"
            " c.local_id, c.rarity FROM card_prices p JOIN cards c ON c.id = p.card_id"
            " WHERE c.set_id = ? AND p.eur IS NOT NULL", (set_id,))]
        for k in karten:
            k["name"] = k.pop("name_de", None) or k.pop("name_en", None) or k["id"]
            # Unter einem Euro sind die Sprünge Rundung, keine Bewegung.
            k["bewegung"] = (None if not k["eur_avg30"] or k["eur"] < MIN_PREIS
                             or k["eur_avg30"] < MIN_PREIS or ist_ausreisser(k["eur"], k["eur_avg30"])
                             else round((k["eur"] / k["eur_avg30"] - 1) * 100, 1))
        klassen = [(0, 1, "unter 1 €"), (1, 5, "1–5 €"), (5, 20, "5–20 €"), (20, 50, "20–50 €"),
                   (50, 150, "50–150 €"), (150, 500, "150–500 €"), (500, None, "über 500 €")]
        verteilung = []
        for unten, oben, name in klassen:
            teil = [k for k in karten if k["eur"] >= unten and (oben is None or k["eur"] < oben)]
            verteilung.append({"name": name, "anzahl": len(teil),
                               "summe": round(sum(k["eur"] for k in teil), 2)})
        bewegt = [k for k in karten if k["bewegung"] is not None]
        bewegt.sort(key=lambda k: -abs(k["bewegung"]))
        teuerste = sorted(karten, key=lambda k: -k["eur"])[:12]
        geplant = con.execute("SELECT geplant FROM markt_tag WHERE ebene='set' AND schluessel=?"
                              " AND datum=?", (set_id, tag)).fetchone()
        aus = {"pro": True, "stand": tag, "set": dict(stamm),
               "kopf": dict(kopf) if kopf else None,
               "verlauf": _verlauf(con, "set", set_id, 90),
               "verteilung": verteilung,
               "bewegung": bewegt[:14],
               "teuerste": teuerste,
               "geplant": geplant["geplant"] if geplant else 0,
               "karten_gesamt": stamm["official"] or stamm["total"]}
        con.close()
        return aus

    @app.get("/api/markt/pokemon/{dex}")
    @_tageskache
    def markt_pokemon(request: Request, dex: int):
        """Alle Karten eines Pokémon über alle Sets — die Frage „was ist Glurak wert"."""
        user = require_user(request)
        if not ist_pro_stufe(user):
            return {"pro": False}
        con = get_db()
        tag = _letzter_tag(con)
        kopf = con.execute("SELECT * FROM markt_tag WHERE ebene='pokemon' AND schluessel=?"
                           " AND datum=?", (str(dex), tag)).fetchone()
        name = con.execute("SELECT name_de, name_en FROM pokemon WHERE dex_id = ?", (dex,)).fetchone()
        karten = [dict(r) for r in con.execute(
            "SELECT p.card_id id, p.eur, p.eur_avg30, c.name_de, c.name_en, c.local_id,"
            " c.release_date, (SELECT name FROM sets WHERE sets.id = c.set_id) AS set_name"
            " FROM card_prices p JOIN cards c ON c.id = p.card_id"
            " WHERE c.first_dex = ? AND p.eur IS NOT NULL AND COALESCE(c.region,'intl')='intl'"
            " ORDER BY p.eur DESC", (dex,))]
        for k in karten:
            k["name"] = k.pop("name_de", None) or k.pop("name_en", None) or k["id"]
            k["bewegung"] = (None if not k["eur_avg30"] or ist_ausreisser(k["eur"], k["eur_avg30"])
                             else round((k["eur"] / k["eur_avg30"] - 1) * 100, 1))
        jahre = {}
        for k in karten:
            j = (k["release_date"] or "")[:4]
            if j:
                jahre.setdefault(j, []).append(k["eur"])
        aus = {"pro": True, "stand": tag, "dex": dex,
               "name": (name["name_de"] or name["name_en"]) if name else str(dex),
               "kopf": dict(kopf) if kopf else None,
               "verlauf": _verlauf(con, "pokemon", dex, 90),
               "karten": karten[:30],
               "jahre": [{"name": j, "anzahl": len(v), "summe": round(sum(v), 2)}
                         for j, v in sorted(jahre.items())]}
        con.close()
        return aus

    def kennzahlen():
        con = get_db()
        tabelle_anlegen(con)
        r = con.execute("SELECT MAX(datum) d, COUNT(*) c FROM markt_tag").fetchone()
        con.close()
        return {"markt_stand": r["d"], "markt_zeilen": r["c"]}

    return kennzahlen
