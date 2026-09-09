"""Die Sammlung: was du wirklich besitzt — getrennt vom Binder, der nur ein Plan ist.

Bis hierher war Besitz ein Häkchen *im Fach* (`item.have`). Das hatte drei Fehler:
dieselbe Karte in zwei Bindern hatte zwei unabhängige Häkchen, Karten ohne Binder
existierten gar nicht, und es gab weder Anzahl noch Zustand noch Kaufpreis.

Jetzt gilt: **eine Wahrheit, zwei Blicke.** Die Sammlung ist die Liste dessen, was
dir gehört. Ein Binder ist ein Plan, wohin es soll. Ein Fach gilt als belegt, weil
die Karte in der Sammlung liegt — nicht wegen eines zweiten Häkchens. Der Haken im
Binder schreibt in die Sammlung, der Binder liest aus ihr.

Ohne Konto gibt es keine Sammlung; dort bleibt `item.have` im Binder wie bisher und
wandert beim ersten Anmelden mit (siehe `uebernehmen_aus_bindern`).
"""

import json
import re
import time

from fastapi import HTTPException, Request

_dep = {}

ZUSTAENDE = ["", "M", "NM", "EX", "GD", "LP", "PL", "PO"]
VARIANTEN = ["normal", "reverse", "holo", "first", "pokeball", "masterball"]
# Sprachen, die Cardmarket für Pokémon führt. Leer heißt „nicht festgelegt“ — das ist der
# Zustand nach einem Haken im Binder, wo niemand nach der Sprache gefragt wurde.
SPRACHEN = ["", "de", "en", "fr", "it", "es", "pt", "jp", "kr", "cn", "ru"]


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def _var(v):
    v = str(v or "normal").strip().lower()
    return v if v in VARIANTEN else "normal"


def _zus(v):
    v = str(v or "").strip().upper()[:2]
    return v if v in ZUSTAENDE else ""


def _spr(v):
    v = str(v or "").strip().lower()[:2]
    return v if v in SPRACHEN else ""


GRADER = ["PSA", "BGS", "CGC", "SGC", "ACE", "TAG", "GMA"]


def _grd(v):
    """„psa 10" → „PSA 10"; unbekannte Anbieter oder Noten außerhalb 1–10 werden verworfen."""
    t = re.sub(r"\s+", " ", str(v or "").strip().upper())
    m = re.match(r"^([A-Z]{2,4})\s*(10|[1-9](?:[.,]5)?)$", t)
    if not m or m.group(1) not in GRADER:
        return ""
    return f"{m.group(1)} {m.group(2).replace(',', '.')}"


def register(app, *, get_db, current_user, require_user, env, card_query, card_select, card_brief,
             preis_fuer_posten=None, ist_bezahlt=None):
    _dep.update(get_db=get_db, current_user=current_user, require_user=require_user, env=env,
                card_query=card_query, card_select=card_select, card_brief=card_brief)
    # Export und Ziele gehören zum Sammeln, nicht zum Händlerwerkzeug: Plus reicht. Ohne
    # Schranke (ältere Einbindung) darf jeder — besser offen als kaputt.
    ist_bezahlt = ist_bezahlt or (lambda user: True)

    con = get_db()
    # Grading (PSA 10, BGS 9.5 …) gehört zum Exemplar wie Zustand und Sprache. Es wird Teil
    # des Posten-Schlüssels, damit ein PSA-10-Glurak und ein loses NM-Exemplar zwei Posten
    # sind. Kein Preis dafür (keine freie Quelle), aber Sortier- und Filterwert.
    try:
        spalten = {r[1] for r in con.execute("PRAGMA table_info(sammlung)")}
        if "grading" not in spalten:
            con.execute("ALTER TABLE sammlung ADD COLUMN grading TEXT NOT NULL DEFAULT ''")
            con.execute("ALTER TABLE sammlung ADD COLUMN zertifikat TEXT DEFAULT ''")
            con.execute("DROP INDEX IF EXISTS idx_sammlung_pos")
            con.execute("CREATE UNIQUE INDEX idx_sammlung_pos"
                        " ON sammlung(user_id, card_id, variante, zustand, sprache, grading)")
            con.commit()
    except Exception as _e:
        print("Grading-Migration übersprungen:", _e)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS sammlung (
            user_id INTEGER, card_id TEXT, variante TEXT DEFAULT 'normal',
            anzahl INTEGER DEFAULT 1, zustand TEXT DEFAULT '',
            kaufpreis REAL, gekauft_am TEXT, notiz TEXT,
            created_at TEXT, updated_at TEXT,
            PRIMARY KEY (user_id, card_id, variante)
        );
        CREATE INDEX IF NOT EXISTS idx_sammlung_user ON sammlung(user_id);
        CREATE TABLE IF NOT EXISTS wants (
            user_id INTEGER, card_id TEXT, created_at TEXT,
            PRIMARY KEY (user_id, card_id)
        );
    """)
    con.commit()
    con.close()

    # --- Hilfen ------------------------------------------------------------

    def _besitz(user_id):
        """→ {card_id: gesamtanzahl} über alle Varianten."""
        con = get_db()
        reihen = con.execute("SELECT card_id, SUM(anzahl) n FROM sammlung WHERE user_id = ?"
                             " GROUP BY card_id", (user_id,)).fetchall()
        con.close()
        return {r["card_id"]: r["n"] for r in reihen if r["n"] > 0}

    def _geplant(user_id, nur_wants=False):
        """→ {card_id: wie oft in Bindern geplant}.

        `nur_wants` zählt nur die Binder, die **ausdrücklich** auf der Wunschliste stehen
        (`options.wants is True`). Vorher war es umgekehrt: jeder Binder zählte mit, wenn
        niemand widersprochen hatte — und dann stand über einem Binder, den man zum
        Ausprobieren angelegt hat, „dir fehlen 340 Karten für 1.200 €". Ein Plan ist keine
        Einkaufsliste; was gekauft werden soll, sagt man ausdrücklich."""
        con = get_db()
        reihen = con.execute("SELECT items, options FROM binders WHERE user_id = ?",
                             (user_id,)).fetchall()
        con.close()
        aus = {}
        for r in reihen:
            if nur_wants:
                try:
                    if json.loads(r["options"] or "{}").get("wants") is not True:
                        continue
                except Exception:
                    continue
            try:
                items = json.loads(r["items"] or "[]")
            except Exception:
                continue
            for i in items:
                if i.get("type") == "card" and i.get("id"):
                    aus[i["id"]] = aus.get(i["id"], 0) + 1
        return aus

    def _wants(user_id):
        """→ Menge der gewünschten Karten: einzeln gemerkte plus die Karten der Binder,
        die auf der Wunschliste stehen."""
        con = get_db()
        aus = {r["card_id"] for r in con.execute(
            "SELECT card_id FROM wants WHERE user_id = ?", (user_id,))}
        con.close()
        aus |= set(_geplant(user_id, nur_wants=True))
        return aus

    def _wunsch_binder(user_id):
        """Alle Binder mit der Angabe, ob sie auf der Wunschliste stehen."""
        con = get_db()
        aus = []
        for r in con.execute("SELECT id, name, items, options FROM binders WHERE user_id = ?"
                             " ORDER BY updated_at DESC", (user_id,)):
            try:
                an = json.loads(r["options"] or "{}").get("wants") is True
            except Exception:
                an = False
            try:
                n = sum(1 for i in json.loads(r["items"] or "[]")
                        if i.get("type") == "card" and i.get("id"))
            except Exception:
                n = 0
            aus.append({"id": r["id"], "name": r["name"], "karten": n, "an": an})
        con.close()
        return aus

    def _preise(card_ids):
        """→ {card_id: {eur, eur_holo, eur_low}} — alles, was die Bewertung braucht."""
        if not card_ids:
            return {}
        con = get_db()
        aus = {}
        ids = list(card_ids)
        for teil in [ids[i:i + 800] for i in range(0, len(ids), 800)]:
            marken = ",".join("?" * len(teil))
            for r in con.execute("SELECT card_id, COALESCE(eur, eur_geschaetzt) eur,"
                                 " eur_holo, eur_low, status, eur_avg7, eur_avg30 FROM card_prices"
                                 f" WHERE card_id IN ({marken})", teil):
                if r["eur"]:
                    aus[r["card_id"]] = {"eur": r["eur"], "eur_holo": r["eur_holo"],
                                         "eur_low": r["eur_low"], "quelle": r["status"],
                                         "eur_avg7": r["eur_avg7"], "eur_avg30": r["eur_avg30"]}
        con.close()
        return aus

    def _posten_wert(preis, posten):
        """Wert eines einzelnen Postens — Ausprägung und Zustand eingerechnet."""
        if not preis or not preis_fuer_posten:
            return None
        return preis_fuer_posten(preis.get("eur"), preis.get("eur_holo"), preis.get("eur_low"),
                                 posten.get("variante") or "normal", posten.get("zustand") or "")

    # --- Endpunkte ---------------------------------------------------------

    @app.get("/api/sammlung")
    def sammlung(request: Request, q: str = "", set_id: str = "", serie: str = "", typ: str = "",
                 rarity: str = "", illustrator: str = "", art_ort: str = "", art_merkmal: str = "",
                 art_zeit: str = "", art_wasser: int = 0, art_text: str = "",
                 nur: str = "", sortierung: str = "neu", umgekehrt: int = 0,
                 limit: int = 60, offset: int = 0):
        """Die eigene Sammlung, mit denselben Filtern wie die Kartensuche — inklusive
        Bildmotiv. „Zeig mir alle Unterwasser-Karten, die ich besitze" geht nur so."""
        user = require_user(request)
        limit = max(1, min(200, limit))
        besitz = _besitz(user["id"])
        if not besitz:
            return {"karten": [], "gesamt": 0}

        # „In keinem Binder" fragt nach dem Plan überhaupt, nicht nach der Kaufliste.
        geplant = _geplant(user["id"]) if nur == "ohne_binder" else {}
        ids = set(besitz)
        if nur == "doppelt":
            ids = {c for c in ids if besitz[c] >= 2}
        elif nur == "ohne_binder":
            ids = {c for c in ids if c not in geplant}
        elif nur == "graded":
            con0 = get_db()
            graded = {r["card_id"] for r in con0.execute(
                "SELECT DISTINCT card_id FROM sammlung WHERE user_id = ? AND grading <> ''", (user["id"],))}
            con0.close()
            ids = {c for c in ids if c in graded}
        if not ids:
            return {"karten": [], "gesamt": 0}

        # Filter über die vorhandene Kartenabfrage, danach auf den Besitz eingeschränkt
        sql_where, params, order = card_query(q, set_id, serie, typ, "", "datum", "asc", rarity, 0, "",   # alle Regionen
                                              illustrator, "", "", "", 0, 0, 0, "", 0,
                                              art_ort, art_zeit, art_wasser, art_merkmal, art_text)
        con = get_db()
        liste = list(ids)
        treffer = []
        for teil in [liste[i:i + 600] for i in range(0, len(liste), 600)]:
            marken = ",".join("?" * len(teil))
            wo = (sql_where + f" AND cards.id IN ({marken})") if sql_where else f" WHERE cards.id IN ({marken})"
            treffer += con.execute(f"{card_select}{wo}", params + teil).fetchall()
        eintraege = {}
        for r in con.execute("SELECT * FROM sammlung WHERE user_id = ? ORDER BY zustand, sprache",
                             (user["id"],)):
            eintraege.setdefault(r["card_id"], []).append(dict(r))
        con.close()

        preise = _preise([r["id"] for r in treffer])
        karten = []
        for r in treffer:
            kurz = card_brief(r)
            eigene = eintraege.get(r["id"], [])
            kurz["anzahl"] = sum(e["anzahl"] for e in eigene)
            # Ein Posten je Kombination aus Variante, Zustand und Sprache — die Oberfläche
            # zeigt sie einzeln, damit man jeden für sich ändern kann.
            kurz["posten"] = [{"variante": e["variante"], "anzahl": e["anzahl"], "zustand": e["zustand"] or "",
                               "sprache": e.get("sprache") or "", "kaufpreis": e["kaufpreis"],
                               "gekauft_am": e.get("gekauft_am"), "notiz": e["notiz"],
                               "grading": e.get("grading") or "", "zertifikat": e.get("zertifikat") or ""} for e in eigene]
            kurz["varianten"] = kurz["posten"]      # alter Name, solange die Oberfläche ihn nutzt
            pr = preise.get(r["id"])
            kurz["eur"] = pr["eur"] if pr else None
            # Woher die Zahl kommt, gehört an die Zahl. „geschaetzt" heißt aus dem
            # US-Preis umgerechnet, „zweitquelle" heißt von pokemontcg.io statt TCGdex.
            kurz["preis_quelle"] = pr.get("quelle") if pr else None
            # Jeder Posten wird mit seinem eigenen Zustand bewertet. Vorher galt für alle
            # derselbe Trend — eine Poor-Karte zählte so viel wie eine Near-Mint-Karte.
            summe = 0
            for pkt in kurz["posten"]:
                w = _posten_wert(pr, pkt)
                pkt["stueckwert"] = w
                summe += (w or 0) * (pkt["anzahl"] or 0)
            kurz["wert"] = round(summe, 2)
            kurz["geplant"] = geplant.get(r["id"], 0)
            # Bewegung gegen den Cardmarket-Schnitt: dieselbe Regel wie im Markt (markt.py),
            # damit eine Karte hier nicht anders steigt als dort.
            kurz["bew7"] = _bewegung(pr, "eur_avg7") if pr else None
            kurz["bew30"] = _bewegung(pr, "eur_avg30") if pr else None
            karten.append(kurz)

        if sortierung == "wert":
            karten.sort(key=lambda k: -(k["wert"] or 0))
        elif sortierung == "name":
            karten.sort(key=lambda k: (k.get("name") or "").lower())
        elif sortierung == "anzahl":
            karten.sort(key=lambda k: -k["anzahl"])
        else:
            # Zuletzt angefasst zuerst. Je Karte zählt der jüngste ihrer Posten — seit
            # Zustand und Sprache eigene Zeilen tragen, sind es mehrere.
            juengste = {cid: max((e["updated_at"] or "") for e in liste_e)
                        for cid, liste_e in eintraege.items()}
            karten.sort(key=lambda k: juengste.get(k["id"], ""), reverse=True)
        if umgekehrt:
            # Ein Knopf dreht die Reihenfolge um — „älteste zuerst", „günstigste zuerst".
            karten.reverse()
        return {"karten": karten[offset:offset + limit], "gesamt": len(karten)}

    @app.get("/api/sammlung/uebersicht")
    def uebersicht(request: Request):
        user = require_user(request)
        besitz = _besitz(user["id"])
        preise = _preise(list(besitz))
        con = get_db()
        # Der Gesamtwert summiert die Posten einzeln, damit der Zustand zählt.
        wert = 0.0
        for r in con.execute("SELECT card_id, variante, zustand, anzahl FROM sammlung"
                             " WHERE user_id = ?", (user["id"],)):
            w = _posten_wert(preise.get(r["card_id"]), dict(r))
            wert += (w or 0) * (r["anzahl"] or 0)
        gezahlt = con.execute("SELECT SUM(kaufpreis * anzahl) s FROM sammlung WHERE user_id = ?"
                              " AND kaufpreis IS NOT NULL", (user["id"],)).fetchone()["s"] or 0
        mit_preis = con.execute("SELECT COUNT(*) c FROM sammlung WHERE user_id = ? AND kaufpreis IS NOT NULL",
                                (user["id"],)).fetchone()["c"]
        graded = con.execute("SELECT COUNT(DISTINCT card_id) c FROM sammlung WHERE user_id = ? AND grading <> ''",
                             (user["id"],)).fetchone()["c"]
        con.close()
        geplant = _geplant(user["id"])                    # alle Binder: „in keinem Binder"
        fehlt = sum(1 for c in _wants(user["id"]) if c not in besitz)
        return {
            "karten": sum(besitz.values()), "verschiedene": len(besitz),
            "wert": round(wert, 2), "mit_preis": len([c for c in besitz if preise.get(c)]),
            "gezahlt": round(gezahlt, 2), "eintraege_mit_preis": mit_preis,
            "doppelte": sum(1 for n in besitz.values() if n >= 2),
            "ohne_binder": sum(1 for c in besitz if c not in geplant),
            "fehlt": fehlt, "graded": graded,
        }

    @app.post("/api/sammlung/toggle")
    async def toggle(request: Request):
        """Ein Klick im Binder: Karte gehört mir / gehört mir nicht."""
        user = require_user(request)
        data = await request.json()
        card_id = str(data.get("card_id") or "").strip()
        variante = _var(data.get("variante"))
        if not card_id:
            raise HTTPException(400, "Keine Karte angegeben")
        con = get_db()
        # Der Haken kennt weder Zustand noch Sprache und arbeitet deshalb auf dem
        # unbestimmten Posten. Wer die Karte schon mit Angaben erfasst hat, verliert sie
        # durch das Abhaken nicht — es wird nur dieser eine Posten entfernt.
        row = con.execute("SELECT anzahl FROM sammlung WHERE user_id=? AND card_id=? AND variante=?"
                          " AND zustand='' AND sprache='' AND grading=''",
                          (user["id"], card_id, variante)).fetchone()
        andere = con.execute("SELECT COUNT(*) c FROM sammlung WHERE user_id=? AND card_id=? AND variante=?"
                             " AND (zustand<>'' OR sprache<>'' OR grading<>'')",
                             (user["id"], card_id, variante)).fetchone()["c"]
        if row:
            con.execute("DELETE FROM sammlung WHERE user_id=? AND card_id=? AND variante=?"
                        " AND zustand='' AND sprache='' AND grading=''", (user["id"], card_id, variante))
            drin = bool(andere)
        elif andere:
            # Schon als bestimmter Posten vorhanden: der Haken nimmt ihn heraus, statt einen
            # zweiten anzulegen — sonst stünde die Karte doppelt in der Sammlung.
            con.execute("DELETE FROM sammlung WHERE user_id=? AND card_id=? AND variante=?",
                        (user["id"], card_id, variante))
            drin = False
        else:
            con.execute("INSERT INTO sammlung (user_id, card_id, variante, anzahl, created_at, updated_at)"
                        " VALUES (?,?,?,1,?,?)", (user["id"], card_id, variante, _now(), _now()))
            drin = True
        con.commit()
        con.close()
        return {"ok": True, "besitze": drin}

    @app.post("/api/sammlung/eintrag")
    async def eintrag(request: Request):
        """Anzahl, Zustand, Kaufpreis und Notiz zu einer Karte setzen. anzahl 0 löscht."""
        user = require_user(request)
        data = await request.json()
        card_id = str(data.get("card_id") or "").strip()
        variante = _var(data.get("variante"))
        if not card_id:
            raise HTTPException(400, "Keine Karte angegeben")
        try:
            anzahl = max(0, min(999, int(data.get("anzahl", 1))))
        except Exception:
            anzahl = 1
        zustand = _zus(data.get("zustand"))
        sprache = _spr(data.get("sprache"))
        # Beim Ändern eines bestehenden Postens kann sich sein Schlüssel verschieben (aus
        # „NM/de" wird „EX/en"). Der alte Stand muss deshalb mitkommen, sonst entsteht ein
        # zweiter Posten statt einer Änderung.
        alt_zustand = _zus(data.get("alt_zustand", zustand))
        alt_sprache = _spr(data.get("alt_sprache", sprache))
        grading = _grd(data.get("grading"))
        alt_grading = _grd(data.get("alt_grading", grading))
        zertifikat = re.sub(r"[^A-Za-z0-9-]", "", str(data.get("zertifikat") or ""))[:30]
        kaufpreis = data.get("kaufpreis")
        try:
            kaufpreis = round(float(kaufpreis), 2) if kaufpreis not in (None, "") else None
        except Exception:
            kaufpreis = None
        gekauft = str(data.get("gekauft_am") or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", gekauft):
            gekauft = None
        notiz = re.sub(r"\s+", " ", str(data.get("notiz") or "")).strip()[:200]

        con = get_db()
        if anzahl == 0:
            con.execute("DELETE FROM sammlung WHERE user_id=? AND card_id=? AND variante=?"
                        " AND zustand=? AND sprache=? AND grading=?",
                        (user["id"], card_id, variante, alt_zustand, alt_sprache, alt_grading))
        else:
            if (alt_zustand, alt_sprache, alt_grading) != (zustand, sprache, grading):
                con.execute("DELETE FROM sammlung WHERE user_id=? AND card_id=? AND variante=?"
                            " AND zustand=? AND sprache=? AND grading=?",
                            (user["id"], card_id, variante, alt_zustand, alt_sprache, alt_grading))
            con.execute(
                "INSERT INTO sammlung (user_id, card_id, variante, zustand, sprache, grading, zertifikat, anzahl,"
                " kaufpreis, gekauft_am, notiz, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(user_id, card_id, variante, zustand, sprache, grading) DO UPDATE SET"
                " anzahl=excluded.anzahl, kaufpreis=excluded.kaufpreis, gekauft_am=excluded.gekauft_am,"
                " notiz=excluded.notiz, zertifikat=excluded.zertifikat, updated_at=excluded.updated_at",
                (user["id"], card_id, variante, zustand, sprache, grading, zertifikat, anzahl, kaufpreis, gekauft,
                 notiz, _now(), _now()))
        con.commit()
        con.close()
        return {"ok": True}

    @app.post("/api/sammlung/aufnehmen")
    async def aufnehmen(request: Request):
        """Eine Karte in die Sammlung legen, ohne Umweg über einen Binder.

        Der Weg über den Haken im Binder setzt voraus, dass die Karte dort geplant ist —
        wer einfach besitzt, was er besitzt, hatte bisher keinen Weg. Mehrfaches Aufnehmen
        derselben Karte in derselben Ausprägung erhöht die Anzahl."""
        user = require_user(request)
        data = await request.json()
        card_id = str(data.get("card_id") or "").strip()
        if not card_id:
            raise HTTPException(400, "Keine Karte angegeben")
        variante, zustand, sprache = _var(data.get("variante")), _zus(data.get("zustand")), _spr(data.get("sprache"))
        try:
            dazu = max(1, min(99, int(data.get("anzahl", 1))))
        except Exception:
            dazu = 1
        con = get_db()
        con.execute(
            "INSERT INTO sammlung (user_id, card_id, variante, zustand, sprache, anzahl, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(user_id, card_id, variante, zustand, sprache, grading) DO UPDATE SET"
            " anzahl = MIN(999, sammlung.anzahl + excluded.anzahl), updated_at = excluded.updated_at",
            (user["id"], card_id, variante, zustand, sprache, dazu, _now(), _now()))
        gesamt = con.execute("SELECT SUM(anzahl) n FROM sammlung WHERE user_id=? AND card_id=?",
                             (user["id"], card_id)).fetchone()["n"] or 0
        con.commit()
        con.close()
        return {"ok": True, "anzahl": gesamt}

    def _wunschliste(user, limit=300):
        """Die Wunschliste ohne das, was längst im Regal steht."""
        besitz = _besitz(user["id"])
        offen = [c for c in _wants(user["id"]) if c not in besitz]
        if not offen:
            return {"karten": [], "gesamt": 0, "summe": 0}
        preise = _preise(offen)
        con = get_db()
        aus = []
        for teil in [offen[i:i + 600] for i in range(0, len(offen), 600)]:
            marken = ",".join("?" * len(teil))
            for r in con.execute(f"{card_select} WHERE cards.id IN ({marken})", teil):
                k = card_brief(r)
                # _preise liefert seit der Zustandsbewertung ein Objekt je Karte.
                pr = preise.get(r["id"])
                k["eur"] = pr["eur"] if pr else None
                k["eur_low"] = pr.get("eur_low") if pr else None
                aus.append(k)
        con.close()
        aus.sort(key=lambda k: -(k["eur"] or 0))
        return {"karten": aus[:limit], "gesamt": len(aus),
                "summe": round(sum(k["eur"] or 0 for k in aus), 2)}

    @app.get("/api/sammlung/fehlt")
    def fehlt(request: Request, limit: int = 300):
        """Alter Name der Wunschliste — bleibt, damit ältere Oberflächen weiterlaufen."""
        return _wunschliste(require_user(request), limit)

    # --- Wunschliste --------------------------------------------------------
    #
    # Vorher war „fehlt mir noch" eine Rechnung über *alle* Binder: alles, was irgendwo
    # geplant und nicht im Regal war, galt als Kaufwunsch. Das stimmt fast nie — Binder
    # sind Pläne, Entwürfe, Geschenke, Kunstseiten. Die Wunschliste ist jetzt eine eigene
    # Liste: einzelne Karten kommen per Klick hinein, und ein ganzer Binder lässt sich
    # dazustellen (dann zählen alle seine Karten). Nichts davon passiert von allein.

    @app.get("/api/wants")
    def wants_liste(request: Request, limit: int = 300):
        user = require_user(request)
        aus = _wunschliste(user, limit)
        con = get_db()
        einzeln = [r["card_id"] for r in con.execute(
            "SELECT card_id FROM wants WHERE user_id = ?", (user["id"],))]
        con.close()
        aus["einzeln"] = einzeln
        aus["binder"] = _wunsch_binder(user["id"])
        return aus

    @app.post("/api/wants")
    async def wants_setzen(request: Request):
        """Eine Karte auf die Wunschliste oder herunter (`an`)."""
        user = require_user(request)
        data = await request.json()
        card_id = str(data.get("card_id") or "").strip()
        if not card_id:
            raise HTTPException(400, "Keine Karte angegeben")
        an = data.get("an")
        con = get_db()
        da = con.execute("SELECT 1 FROM wants WHERE user_id = ? AND card_id = ?",
                         (user["id"], card_id)).fetchone()
        an = (not da) if an is None else bool(an)      # ohne Angabe: umschalten
        if an:
            con.execute("INSERT OR IGNORE INTO wants (user_id, card_id, created_at)"
                        " VALUES (?,?,?)", (user["id"], card_id, _now()))
        else:
            con.execute("DELETE FROM wants WHERE user_id = ? AND card_id = ?",
                        (user["id"], card_id))
        con.commit()
        n = con.execute("SELECT COUNT(*) c FROM wants WHERE user_id = ?",
                        (user["id"],)).fetchone()["c"]
        con.close()
        return {"ok": True, "an": an, "einzeln": n}

    @app.post("/api/wants/binder")
    async def wants_binder(request: Request):
        """Einen ganzen Binder auf die Wunschliste stellen oder herunternehmen."""
        user = require_user(request)
        data = await request.json()
        binder_id = str(data.get("binder_id") or "")
        an = bool(data.get("an"))
        con = get_db()
        row = con.execute("SELECT options FROM binders WHERE id = ? AND user_id = ?",
                          (binder_id, user["id"])).fetchone()
        if not row:
            con.close()
            raise HTTPException(404, "Binder nicht gefunden")
        try:
            opt = json.loads(row["options"] or "{}")
        except Exception:
            opt = {}
        opt["wants"] = an
        con.execute("UPDATE binders SET options = ? WHERE id = ? AND user_id = ?",
                    (json.dumps(opt), binder_id, user["id"]))
        con.commit()
        con.close()
        return {"ok": True, "an": an}

    @app.post("/api/sammlung/aus_binder")
    async def aus_binder(request: Request):
        """Alle abgehakten Karten eines Binders in die Sammlung übernehmen — der Weg,
        auf dem die alten `have`-Häkchen einmalig umziehen."""
        user = require_user(request)
        data = await request.json()
        binder_id = str(data.get("binder_id") or "")
        alle = bool(data.get("alle"))          # true = auch nicht abgehakte Karten
        con = get_db()
        row = con.execute("SELECT items, user_id FROM binders WHERE id = ?", (binder_id,)).fetchone()
        if not row or (row["user_id"] not in (None, user["id"])):
            con.close()
            raise HTTPException(404, "Binder nicht gefunden")
        try:
            items = json.loads(row["items"] or "[]")
        except Exception:
            items = []
        neu = 0
        for i in items:
            if i.get("type") != "card" or not i.get("id"):
                continue
            if not alle and not i.get("have"):
                continue
            da = con.execute("SELECT 1 FROM sammlung WHERE user_id=? AND card_id=? AND variante=?",
                             (user["id"], i["id"], _var(i.get("variant")))).fetchone()
            if da:
                continue
            con.execute("INSERT INTO sammlung (user_id, card_id, variante, anzahl, zustand, created_at, updated_at)"
                        " VALUES (?,?,?,1,?,?,?)",
                        (user["id"], i["id"], _var(i.get("variant")), str(i.get("zustand") or "")[:2],
                         _now(), _now()))
            neu += 1
        con.commit()
        con.close()
        return {"ok": True, "uebernommen": neu}

    @app.get("/api/sammlung/restkosten")
    def restkosten(request: Request, binder: str = ""):
        """Was noch fehlt und was es kosten würde — je Binder oder über alle.

        Die Frage, die jeder mit einem halbvollen Binder hat. Alle Zahlen liegen längst
        vor, sie wurden nur nie zusammengerechnet."""
        user = require_user(request)
        besitz = _besitz(user["id"])
        con = get_db()
        ids = [i.strip() for i in binder.split(",") if i.strip()][:20]
        wo = "user_id = ?"
        args = [user["id"]]
        if ids:
            wo += " AND id IN (%s)" % ",".join("?" * len(ids))
            args += ids
        geplant = {}
        for r in con.execute(f"SELECT id, name, items FROM binders WHERE {wo}", args):
            try:
                items = json.loads(r["items"] or "[]")
            except Exception:
                continue
            offen = [i["id"] for i in items
                     if i.get("type") == "card" and i.get("id") and i["id"] not in besitz]
            if offen:
                geplant[r["id"]] = {"name": r["name"], "offen": offen}
        con.close()
        alle = sorted({c for g in geplant.values() for c in g["offen"]})
        preise = _preise(alle)
        aus = []
        for bid, g in geplant.items():
            teuer = sorted(((c, (preise.get(c) or {}).get("eur") or 0) for c in set(g["offen"])),
                           key=lambda x: -x[1])
            aus.append({
                "binder": bid, "name": g["name"],
                "fehlt": len(set(g["offen"])),
                "summe": round(sum((preise.get(c) or {}).get("eur") or 0 for c in set(g["offen"])), 2),
                "ohne_preis": sum(1 for c in set(g["offen"]) if not preise.get(c)),
                "teuerste": [{"id": c, "eur": e} for c, e in teuer[:5] if e],
            })
        aus.sort(key=lambda x: -x["summe"])
        return {"binder": aus, "summe": round(sum(b["summe"] for b in aus), 2),
                "fehlt": sum(b["fehlt"] for b in aus)}

    @app.get("/api/sammlung/besitz")
    def besitz_liste(request: Request):
        """Kompakte Liste für den Binder: welche Karten besitze ich, wie oft."""
        user = current_user(request)
        if not user:
            return {"besitz": {}, "wants": []}
        con = get_db()
        wl = [r["card_id"] for r in con.execute("SELECT card_id FROM wants WHERE user_id = ?",
                                                (user["id"],))]
        con.close()
        return {"besitz": _besitz(user["id"]), "wants": wl}

    # --- Sammlung als Werkzeug ----------------------------------------------
    #
    # Sammler denken in Sets („Base Set 34 von 102"), nicht in Einzelkarten. Und sie kommen
    # wieder, wenn sich etwas bewegt hat. Beides fehlte: die Sammlung war eine Kartenliste
    # mit einer Zahl darüber. Die Bewegung kommt — wie im Markt — aus den 7- und 30-Tage-
    # Schnitten von Cardmarket, weil die eigene Preishistorie noch zu jung ist.

    AUS_UNTEN, AUS_OBEN = 1 / 3, 3.0

    def _bewegung(pr, feld):
        """Prozent gegen den Schnitt; Ausreißer (Zuordnungsfehler der Quelle) bleiben leer."""
        if not pr or not pr.get("eur") or not pr.get(feld) or pr[feld] <= 0:
            return None
        q = pr["eur"] / pr[feld]
        if q > AUS_OBEN or q < AUS_UNTEN or pr["eur"] < 1 or pr[feld] < 1:
            return None
        return round((q - 1) * 100, 1)

    def _heute():
        return time.strftime("%Y-%m-%d")

    def _ziele_tabelle():
        con = get_db()
        con.execute("""CREATE TABLE IF NOT EXISTS sammlung_ziele (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, set_id TEXT,
            ziel_datum TEXT, created_at TEXT, UNIQUE (user_id, set_id))""")
        con.commit()
        con.close()

    _ziele_tabelle()

    def _posten_alle(user_id):
        con = get_db()
        aus = [dict(r) for r in con.execute(
            "SELECT card_id, variante, zustand, sprache, anzahl, kaufpreis FROM sammlung"
            " WHERE user_id = ? AND anzahl > 0", (user_id,))]
        con.close()
        return aus

    def _markt_sets(con):
        """Bewegung je Set aus dem Tagesstand des Markts (markt_tag), falls vorhanden."""
        try:
            tag = con.execute("SELECT MAX(datum) d FROM markt_tag").fetchone()["d"]
            if not tag:
                return {}
            return {r["schluessel"]: r["bew30"] for r in con.execute(
                "SELECT schluessel, bew30 FROM markt_tag WHERE ebene='set' AND datum=?", (tag,))}
        except Exception:
            return {}

    def _set_stand(user_id, nur_sets=None):
        """Fortschritt, Rest und Wert je Set, in dem mindestens eine Karte liegt.

        „Rest zum Set" ist die Summe der Trendpreise aller Karten des Sets, die nicht in der
        Sammlung liegen — zu heutigen Preisen, denn morgen sind es andere. Karten ohne Preis
        zählen als 0 und werden gezählt, damit die Zahl ehrlich bleibt."""
        posten = _posten_alle(user_id)
        if not posten:
            return []
        besitz = {}
        for p in posten:
            besitz[p["card_id"]] = besitz.get(p["card_id"], 0) + (p["anzahl"] or 0)
        con = get_db()
        set_von = {}
        ids = list(besitz)
        for teil in [ids[i:i + 800] for i in range(0, len(ids), 800)]:
            marken = ",".join("?" * len(teil))
            for r in con.execute(f"SELECT id, set_id FROM cards WHERE id IN ({marken})", teil):
                set_von[r["id"]] = r["set_id"]
        sets = sorted({s for s in set_von.values() if s})
        if nur_sets is not None:
            sets = [s for s in sets if s in nur_sets]
        if not sets:
            con.close()
            return []
        marken = ",".join("?" * len(sets))
        stamm = {r["id"]: dict(r) for r in con.execute(
            f"SELECT id, name, name_en, serie_name, release_date, total, official FROM sets"
            f" WHERE id IN ({marken})", sets)}
        # Alle Karten der Sets mit Preis — daraus Gesamtzahl und Rest.
        karten = {}
        for r in con.execute(
                f"SELECT c.id, c.set_id, COALESCE(p.eur, p.eur_geschaetzt) eur FROM cards c"
                f" LEFT JOIN card_prices p ON p.card_id = c.id WHERE c.set_id IN ({marken})", sets):
            karten.setdefault(r["set_id"], []).append((r["id"], r["eur"]))
        bewegung = _markt_sets(con)
        ziele = {r["set_id"]: r["ziel_datum"] for r in con.execute(
            "SELECT set_id, ziel_datum FROM sammlung_ziele WHERE user_id = ?", (user_id,))}
        con.close()

        preise = _preise(ids)
        wert_je_set, einsatz_je_set, wert_gekauft_je_set = {}, {}, {}
        for p in posten:
            sid = set_von.get(p["card_id"])
            if not sid:
                continue
            w = (_posten_wert(preise.get(p["card_id"]), p) or 0) * (p["anzahl"] or 0)
            wert_je_set[sid] = wert_je_set.get(sid, 0) + w
            if p["kaufpreis"] is not None:
                einsatz_je_set[sid] = einsatz_je_set.get(sid, 0) + p["kaufpreis"] * (p["anzahl"] or 0)
                wert_gekauft_je_set[sid] = wert_gekauft_je_set.get(sid, 0) + w
        aus = []
        for sid in sets:
            st = stamm.get(sid) or {}
            alle = karten.get(sid, [])
            gesamt = len(alle) or st.get("total") or 0
            besessen = sum(1 for cid, _e in alle if cid in besitz)
            fehlend = [(cid, e) for cid, e in alle if cid not in besitz]
            rest = sum(e or 0 for _c, e in fehlend)
            einsatz = einsatz_je_set.get(sid)
            rendite = None
            if einsatz:
                rendite = round((wert_gekauft_je_set.get(sid, 0) - einsatz) / einsatz * 100, 1)
            aus.append({
                "set_id": sid, "name": st.get("name") or st.get("name_en") or sid,
                "serie_name": st.get("serie_name"), "jahr": (st.get("release_date") or "")[:4],
                "gesamt": gesamt, "besessen": besessen,
                "prozent": round(besessen / gesamt * 100) if gesamt else 0,
                "fehlt": len(fehlend), "rest": round(rest, 2),
                "rest_ohne_preis": sum(1 for _c, e in fehlend if not e),
                "wert": round(wert_je_set.get(sid, 0), 2),
                "einsatz": round(einsatz, 2) if einsatz else None, "rendite": rendite,
                "bew30": bewegung.get(sid), "ziel": ziele.get(sid),
            })
        return aus

    @app.get("/api/sammlung/kopf")
    def kopf(request: Request):
        """Die vier Zahlen über der Sammlung: Wert und Bewegung, Einsatz und Gewinn,
        Sets, Rest zu den Zielen."""
        user = require_user(request)
        posten = _posten_alle(user["id"])
        if not posten:
            return {"leer": True}
        preise = _preise({p["card_id"] for p in posten})
        wert = basis7 = diff7 = basis30 = diff30 = 0.0
        einsatz = wert_gekauft = 0.0
        for p in posten:
            pr = preise.get(p["card_id"])
            w = (_posten_wert(pr, p) or 0)
            n = p["anzahl"] or 0
            wert += w * n
            if p["kaufpreis"] is not None:
                einsatz += p["kaufpreis"] * n
                wert_gekauft += w * n
            if pr and pr.get("eur"):
                # Die Bewegung rechnet mit dem Grundpreis der Karte — Zustand und Ausprägung
                # skalieren beide Seiten gleich, der Prozentwert bleibt derselbe.
                for feld, (b, d) in (("eur_avg7", ("basis7", "diff7")), ("eur_avg30", ("basis30", "diff30"))):
                    if _bewegung(pr, feld) is None:
                        continue
                    faktor = w / pr["eur"] if pr["eur"] else 1
                    if feld == "eur_avg7":
                        basis7 += pr[feld] * faktor * n; diff7 += (pr["eur"] - pr[feld]) * faktor * n
                    else:
                        basis30 += pr[feld] * faktor * n; diff30 += (pr["eur"] - pr[feld]) * faktor * n
        sets = _set_stand(user["id"])
        ziele = [s for s in sets if s.get("ziel")]
        return {
            "wert": round(wert, 2), "karten": sum(p["anzahl"] or 0 for p in posten),
            "bew7_eur": round(diff7, 2), "bew7": round(diff7 / basis7 * 100, 1) if basis7 else None,
            "bew30_eur": round(diff30, 2), "bew30": round(diff30 / basis30 * 100, 1) if basis30 else None,
            "einsatz": round(einsatz, 2) if einsatz else None,
            "gewinn": round(wert_gekauft - einsatz, 2) if einsatz else None,
            "gewinn_proz": round((wert_gekauft - einsatz) / einsatz * 100, 1) if einsatz else None,
            "sets": len(sets), "sets_fast": sum(1 for s in sets if 90 <= s["prozent"] < 100),
            "sets_komplett": sum(1 for s in sets if s["prozent"] >= 100),
            "ziele": len(ziele), "ziel_rest": round(sum(s["rest"] for s in ziele), 2),
            "ziel_fehlt": sum(s["fehlt"] for s in ziele),
        }

    @app.get("/api/sammlung/sets")
    def sets_liste(request: Request, sortier: str = "fortschritt"):
        """Der Set-Reiter: Fortschritt nach besessenen Karten, Rest zu heutigen Preisen."""
        user = require_user(request)
        sets = _set_stand(user["id"])
        schluessel = {
            "fortschritt": lambda s: (-s["prozent"], -s["besessen"]),
            "rest": lambda s: (-s["rest"],),
            "wert": lambda s: (-s["wert"],),
            "bewegung": lambda s: (s["bew30"] is None, -(s["bew30"] or 0)),
            "name": lambda s: ((s["name"] or "").lower(),),
            "jahr": lambda s: (s["jahr"] or "",),
        }.get(sortier) or (lambda s: (-s["prozent"], -s["besessen"]))
        sets.sort(key=schluessel)
        return {"sets": sets, "sortier": sortier}

    @app.get("/api/sammlung/set/{set_id}")
    def set_seite(request: Request, set_id: str):
        """Häkchenraster eines Sets: jede Karte mit Preis, besessen oder nicht."""
        user = require_user(request)
        con = get_db()
        stamm = con.execute("SELECT id, name, name_en, serie_name, release_date, total, official"
                            " FROM sets WHERE id = ?", (set_id,)).fetchone()
        if not stamm:
            con.close()
            raise HTTPException(404, "Set nicht gefunden")
        reihen = con.execute(
            f"{card_select} WHERE cards.set_id = ? ORDER BY cards.local_num, cards.local_id", (set_id,)).fetchall()
        ziel = con.execute("SELECT id, ziel_datum FROM sammlung_ziele WHERE user_id = ? AND set_id = ?",
                           (user["id"], set_id)).fetchone()
        con.close()
        besitz = _besitz(user["id"])
        preise = _preise([r["id"] for r in reihen])
        karten = []
        for r in reihen:
            k = card_brief(r)
            pr = preise.get(r["id"])
            k["eur"] = pr["eur"] if pr else None
            k["bew30"] = _bewegung(pr, "eur_avg30") if pr else None
            k["anzahl"] = besitz.get(r["id"], 0)
            karten.append(k)
        fehlend = [k for k in karten if not k["anzahl"]]
        stand = (_set_stand(user["id"], {set_id}) or [None])[0]
        return {
            "set": dict(stamm), "karten": karten,
            "gesamt": len(karten), "besessen": len(karten) - len(fehlend),
            "rest": round(sum(k["eur"] or 0 for k in fehlend), 2),
            "rest_ohne_preis": sum(1 for k in fehlend if not k["eur"]),
            "teuerste_fehlend": sorted([k for k in fehlend if k["eur"]], key=lambda k: -k["eur"])[:6],
            "stand": stand,
            "ziel": {"id": ziel["id"], "datum": ziel["ziel_datum"]} if ziel else None,
        }

    @app.get("/api/sammlung/guenstig")
    def guenstig(request: Request, limit: int = 30):
        """Wunschlisten-Karten unter ihrem 30-Tage-Schnitt — der Kaufmoment."""
        user = require_user(request)
        besitz = _besitz(user["id"])
        offen = [c for c in _wants(user["id"]) if c not in besitz]
        if not offen:
            return {"karten": [], "gesamt": 0}
        preise = _preise(offen)
        treffer = []
        for cid in offen:
            pr = preise.get(cid)
            b = _bewegung(pr, "eur_avg30") if pr else None
            if b is None or b > -3:
                continue
            treffer.append((cid, pr, b))
        treffer.sort(key=lambda x: x[2])
        treffer = treffer[:max(1, min(100, limit))]
        if not treffer:
            return {"karten": [], "gesamt": 0}
        con = get_db()
        marken = ",".join("?" * len(treffer))
        kurz = {r["id"]: card_brief(r) for r in con.execute(
            f"{card_select} WHERE cards.id IN ({marken})", [x[0] for x in treffer])}
        con.close()
        aus = []
        for cid, pr, b in treffer:
            k = kurz.get(cid)
            if not k:
                continue
            k.update({"eur": pr["eur"], "avg30": round(pr["eur_avg30"], 2), "prozent": b})
            aus.append(k)
        return {"karten": aus, "gesamt": len(aus)}

    @app.post("/api/sammlung/kaufpreis")
    async def kaufpreis_setzen(request: Request):
        """Kaufpreis nachtragen, direkt nach „Hab ich" — ohne den Weg über den Dialog."""
        user = require_user(request)
        data = await request.json()
        card_id = str(data.get("card_id") or "").strip()
        try:
            preis = float(str(data.get("kaufpreis") or "").replace(",", "."))
        except Exception:
            raise HTTPException(400, "Kein Preis")
        if not card_id or preis < 0 or preis > 1_000_000:
            raise HTTPException(400, "Kein Preis")
        variante, zustand, sprache = _var(data.get("variante")), _zus(data.get("zustand")), _spr(data.get("sprache"))
        con = get_db()
        n = con.execute(
            "UPDATE sammlung SET kaufpreis = ?, gekauft_am = COALESCE(gekauft_am, ?), updated_at = ?"
            " WHERE user_id = ? AND card_id = ? AND variante = ? AND zustand = ? AND sprache = ?",
            (round(preis, 2), _heute(), _now(), user["id"], card_id, variante, zustand, sprache)).rowcount
        con.commit()
        con.close()
        return {"ok": bool(n)}

    @app.post("/api/sammlung/import")
    async def sammlung_import(request: Request):
        """Eine geprüfte Importliste direkt in die Sammlung — mit Anzahl, Zustand, Sprache
        und Kaufpreis aus der Tabelle. Vorher landete jeder Import nur als Plan im Binder."""
        user = require_user(request)
        data = await request.json()
        karten = data.get("karten") or []
        if not isinstance(karten, list):
            raise HTTPException(400, "Keine Liste")
        con = get_db()
        n = 0
        for k in karten[:300]:
            cid = str(k.get("id") or "").strip()
            if not cid or not con.execute("SELECT 1 FROM cards WHERE id = ?", (cid,)).fetchone():
                continue
            try:
                anzahl = max(1, min(999, int(k.get("anzahl") or 1)))
            except Exception:
                anzahl = 1
            kp = k.get("kaufpreis")
            try:
                kp = round(float(kp), 2) if kp not in (None, "") else None
            except Exception:
                kp = None
            con.execute(
                "INSERT INTO sammlung (user_id, card_id, variante, zustand, sprache, anzahl, kaufpreis, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(user_id, card_id, variante, zustand, sprache, grading) DO UPDATE SET"
                " anzahl = MIN(999, sammlung.anzahl + excluded.anzahl),"
                " kaufpreis = COALESCE(excluded.kaufpreis, sammlung.kaufpreis), updated_at = excluded.updated_at",
                (user["id"], cid, _var(k.get("variante")), _zus(k.get("zustand")), _spr(k.get("sprache")),
                 anzahl, kp, _now(), _now()))
            n += 1
        con.commit()
        con.close()
        return {"ok": True, "aufgenommen": n}

    @app.post("/api/sammlung/mehrfach")
    async def mehrfach(request: Request):
        """Mehrere Karten auf einmal: Zustand oder Sprache setzen, oder entfernen.

        Posten, die dadurch denselben Schlüssel bekämen, werden zusammengelegt (Anzahl
        addiert, Kaufpreis des ersten behalten) — sonst liefe die Änderung in den
        Unique-Index."""
        user = require_user(request)
        data = await request.json()
        ids = [str(x) for x in (data.get("card_ids") or []) if x][:500]
        if not ids:
            raise HTTPException(400, "Keine Karten")
        con = get_db()
        marken = ",".join("?" * len(ids))
        if data.get("loeschen"):
            n = con.execute(f"DELETE FROM sammlung WHERE user_id = ? AND card_id IN ({marken})",
                            (user["id"], *ids)).rowcount
            con.commit()
            con.close()
            return {"ok": True, "geaendert": n}
        neu_z = _zus(data.get("zustand")) if data.get("zustand") is not None else None
        neu_s = _spr(data.get("sprache")) if data.get("sprache") is not None else None
        if neu_z is None and neu_s is None:
            con.close()
            raise HTTPException(400, "Nichts zu ändern")
        posten = [dict(r) for r in con.execute(
            f"SELECT * FROM sammlung WHERE user_id = ? AND card_id IN ({marken})", (user["id"], *ids))]
        zusammen = {}
        for p in posten:
            z = neu_z if neu_z is not None else (p["zustand"] or "")
            sp = neu_s if neu_s is not None else (p.get("sprache") or "")
            key = (p["card_id"], p["variante"], z, sp, p.get("grading") or "")
            if key in zusammen:
                zusammen[key]["anzahl"] = min(999, zusammen[key]["anzahl"] + (p["anzahl"] or 0))
                if zusammen[key]["kaufpreis"] is None:
                    zusammen[key]["kaufpreis"] = p["kaufpreis"]
            else:
                zusammen[key] = dict(p, zustand=z, sprache=sp)
        con.execute(f"DELETE FROM sammlung WHERE user_id = ? AND card_id IN ({marken})", (user["id"], *ids))
        for (cid, var, z, sp, g), p in zusammen.items():
            con.execute(
                "INSERT INTO sammlung (user_id, card_id, variante, zustand, sprache, grading, zertifikat, anzahl,"
                " kaufpreis, gekauft_am, notiz, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (user["id"], cid, var, z, sp, g, p.get("zertifikat") or "", p["anzahl"], p["kaufpreis"],
                 p.get("gekauft_am"), p.get("notiz"), p.get("created_at") or _now(), _now()))
        con.commit()
        con.close()
        return {"ok": True, "geaendert": len(zusammen)}

    # --- Ziele ----------------------------------------------------------------
    #
    # „Base Set komplett bis Dezember." Ein Ziel ist ein Set mit Datum; der Fortschritt
    # steht auf der Startseite. Das ist der Grund, jede Woche wiederzukommen.

    @app.get("/api/sammlung/ziele")
    def ziele_liste(request: Request):
        user = require_user(request)
        con = get_db()
        ziele = [dict(r) for r in con.execute(
            "SELECT id, set_id, ziel_datum FROM sammlung_ziele WHERE user_id = ? ORDER BY ziel_datum, id",
            (user["id"],))]
        con.close()
        if not ziele:
            return {"ziele": []}
        stand = {s["set_id"]: s for s in _set_stand(user["id"], {z["set_id"] for z in ziele})}
        # Ein Ziel-Set ohne einzige Karte hat noch keinen Stand — dann aus dem Stamm.
        con = get_db()
        heute = _heute()
        for z in ziele:
            s = stand.get(z["set_id"])
            if not s:
                st = con.execute("SELECT name, name_en, total FROM sets WHERE id = ?", (z["set_id"],)).fetchone()
                gesamt = con.execute("SELECT COUNT(*) c FROM cards WHERE set_id = ?", (z["set_id"],)).fetchone()["c"]
                rest = con.execute("SELECT SUM(COALESCE(p.eur, p.eur_geschaetzt)) s FROM cards c"
                                   " LEFT JOIN card_prices p ON p.card_id = c.id WHERE c.set_id = ?",
                                   (z["set_id"],)).fetchone()["s"] or 0
                s = {"name": (st["name"] or st["name_en"]) if st else z["set_id"], "gesamt": gesamt,
                     "besessen": 0, "prozent": 0, "fehlt": gesamt, "rest": round(rest, 2), "bew30": None}
            z.update({k: s.get(k) for k in ("name", "gesamt", "besessen", "prozent", "fehlt", "rest", "bew30")})
            if z["ziel_datum"]:
                try:
                    import datetime as _dt
                    z["tage"] = (_dt.date.fromisoformat(z["ziel_datum"]) - _dt.date.fromisoformat(heute)).days
                except Exception:
                    z["tage"] = None
        con.close()
        return {"ziele": ziele}

    @app.post("/api/sammlung/ziele")
    async def ziel_setzen(request: Request):
        user = require_user(request)
        data = await request.json()
        set_id = str(data.get("set_id") or "").strip()
        if data.get("loeschen"):
            con = get_db()
            con.execute("DELETE FROM sammlung_ziele WHERE user_id = ? AND set_id = ?", (user["id"], set_id))
            con.commit()
            con.close()
            return {"ok": True}
        if not ist_bezahlt(user):
            raise HTTPException(402, detail={"code": "limit_pro"})
        datum = str(data.get("ziel_datum") or "").strip()[:10] or None
        if datum and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", datum):
            raise HTTPException(400, "Datum als JJJJ-MM-TT")
        con = get_db()
        if not con.execute("SELECT 1 FROM sets WHERE id = ?", (set_id,)).fetchone():
            con.close()
            raise HTTPException(404, "Set nicht gefunden")
        n = con.execute("SELECT COUNT(*) c FROM sammlung_ziele WHERE user_id = ?", (user["id"],)).fetchone()["c"]
        schon = con.execute("SELECT 1 FROM sammlung_ziele WHERE user_id = ? AND set_id = ?",
                            (user["id"], set_id)).fetchone()
        if n >= 6 and not schon:
            con.close()
            raise HTTPException(400, "Höchstens sechs Ziele — sonst ist keins mehr eins.")
        con.execute("INSERT INTO sammlung_ziele (user_id, set_id, ziel_datum, created_at) VALUES (?,?,?,?)"
                    " ON CONFLICT(user_id, set_id) DO UPDATE SET ziel_datum = excluded.ziel_datum",
                    (user["id"], set_id, datum, _now()))
        con.commit()
        con.close()
        return {"ok": True}

    # --- Export ------------------------------------------------------------------
    #
    # Eine Tabelle mit Wert je Karte — für Versicherung, Verkauf oder den Wechsel in ein
    # anderes Werkzeug. CSV für Tabellen, PDF für den Ordner.

    def _export_zeilen(user_id):
        con = get_db()
        reihen = [dict(r) for r in con.execute(
            "SELECT s.card_id, s.variante, s.zustand, s.sprache, s.anzahl, s.kaufpreis, s.gekauft_am,"
            " s.notiz, s.grading, s.zertifikat, c.name_de, c.name_en, c.local_id, c.rarity, c.set_id,"
            " (SELECT name FROM sets WHERE sets.id = c.set_id) AS set_name"
            " FROM sammlung s JOIN cards c ON c.id = s.card_id WHERE s.user_id = ? AND s.anzahl > 0"
            " ORDER BY set_name, c.local_num, c.local_id, s.variante", (user_id,))]
        con.close()
        preise = _preise({r["card_id"] for r in reihen})
        for r in reihen:
            r["name"] = r["name_de"] or r["name_en"] or r["card_id"]
            r["stueck"] = _posten_wert(preise.get(r["card_id"]), r)
            r["wert"] = round((r["stueck"] or 0) * (r["anzahl"] or 0), 2)
        return reihen

    def _d(n, stellen=2):
        return "" if n is None else f"{n:.{stellen}f}".replace(".", ",")

    @app.get("/api/sammlung/export")
    def sammlung_export(request: Request, format: str = "csv"):
        user = require_user(request)
        if not ist_bezahlt(user):
            raise HTTPException(402, detail={"code": "limit_pro"})
        zeilen = _export_zeilen(user["id"])
        summe = sum(z["wert"] for z in zeilen)
        stueck = sum(z["anzahl"] or 0 for z in zeilen)
        from fastapi import Response
        datum = _heute()
        if format == "pdf":
            return Response(_export_pdf(zeilen, summe, stueck, datum), media_type="application/pdf",
                            headers={"Content-Disposition": f'attachment; filename="sammlung-{datum}.pdf"'})
        out = ["Name;Set;Nummer;Seltenheit;Variante;Zustand;Sprache;Grading;Zertifikat;Anzahl;Stückwert EUR;Wert EUR;Kaufpreis EUR;Gekauft am;Notiz;Karten-ID"]
        for z in zeilen:
            notiz = (z["notiz"] or "").replace(";", ",").replace("\n", " ")
            out.append(";".join([z["name"], z["set_name"] or z["set_id"], z["local_id"] or "", z["rarity"] or "",
                                 z["variante"] or "normal", z["zustand"] or "", (z["sprache"] or "").upper(),
                                 z.get("grading") or "", z.get("zertifikat") or "",
                                 str(z["anzahl"] or 0), _d(z["stueck"]), _d(z["wert"]), _d(z["kaufpreis"]),
                                 z["gekauft_am"] or "", notiz, z["card_id"]]))
        out.append(f"Summe;;;;;;;;;{stueck};;{_d(summe)};;;;")
        return Response("\n".join(out).encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="sammlung-{datum}.csv"'})

    def _export_pdf(zeilen, summe, stueck, datum):
        """Sammlungsübersicht als Tabelle: eine Zeile je Posten, Summen am Ende."""
        import io
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas as pdfcanvas
        buf = io.BytesIO()
        c = pdfcanvas.Canvas(buf, pagesize=A4)
        breite, hoehe = A4
        links, oben = 15 * mm, hoehe - 18 * mm
        spalten = [("Karte", 0), ("Set · Nr.", 62 * mm), ("Posten", 118 * mm), ("Anz.", 143 * mm),
                   ("Stück €", 156 * mm), ("Wert €", 172 * mm)]

        def kopf(seite):
            c.setFont("Helvetica-Bold", 14)
            c.drawString(links, oben, "Sammlungsübersicht")
            c.setFont("Helvetica", 9)
            c.drawRightString(breite - links, oben, f"Stand {datum} · Seite {seite}")
            c.drawString(links, oben - 5 * mm,
                         f"{stueck} Karten · Wert nach Cardmarket-Trend, Zustand eingerechnet: {summe:,.2f} €".replace(",", "X").replace(".", ",").replace("X", "."))
            y = oben - 13 * mm
            c.setFont("Helvetica-Bold", 8)
            for name, x in spalten:
                if name in ("Anz.", "Stück €", "Wert €"):
                    c.drawRightString(links + x + 12 * mm, y, name)
                else:
                    c.drawString(links + x, y, name)
            c.line(links, y - 1.5 * mm, breite - links, y - 1.5 * mm)
            return y - 6 * mm

        seite = 1
        y = kopf(seite)
        c.setFont("Helvetica", 8)
        for z in zeilen:
            if y < 18 * mm:
                c.showPage()
                seite += 1
                y = kopf(seite)
                c.setFont("Helvetica", 8)
            posten = " · ".join(x for x in (z["variante"] if z["variante"] != "normal" else "",
                                            z["zustand"] or "", (z["sprache"] or "").upper(),
                                            z.get("grading") or "") if x) or "–"
            c.drawString(links, y, (z["name"] or "")[:34])
            c.drawString(links + spalten[1][1], y, f"{(z['set_name'] or z['set_id'])[:26]} · {z['local_id'] or ''}")
            c.drawString(links + spalten[2][1], y, posten[:18])
            c.drawRightString(links + spalten[3][1] + 12 * mm, y, str(z["anzahl"] or 0))
            c.drawRightString(links + spalten[4][1] + 12 * mm, y, _d(z["stueck"]) or "–")
            c.drawRightString(links + spalten[5][1] + 12 * mm, y, _d(z["wert"]))
            y -= 4.6 * mm
        y -= 2 * mm
        c.line(links, y + 3 * mm, breite - links, y + 3 * mm)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(links, y - 1 * mm, "Summe")
        c.drawRightString(links + spalten[3][1] + 12 * mm, y - 1 * mm, str(stueck))
        c.drawRightString(links + spalten[5][1] + 12 * mm, y - 1 * mm, _d(summe))
        c.setFont("Helvetica", 7)
        c.drawString(links, 10 * mm, "Erstellt mit binderplan.app · Preise: Cardmarket-Trend je Karte, Zustandsabschläge wie in der App.")
        c.showPage()
        c.save()
        return buf.getvalue()

    def kennzahlen():
        con = get_db()
        n = con.execute("SELECT COUNT(*) c FROM sammlung").fetchone()["c"]
        con.close()
        return n

    return kennzahlen
