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


def register(app, *, get_db, current_user, require_user, env, card_query, card_select, card_brief,
             preis_fuer_posten=None):
    _dep.update(get_db=get_db, current_user=current_user, require_user=require_user, env=env,
                card_query=card_query, card_select=card_select, card_brief=card_brief)

    con = get_db()
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
                                 " eur_holo, eur_low, status FROM card_prices"
                                 f" WHERE card_id IN ({marken})", teil):
                if r["eur"]:
                    aus[r["card_id"]] = {"eur": r["eur"], "eur_holo": r["eur_holo"],
                                         "eur_low": r["eur_low"], "quelle": r["status"]}
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
                               "gekauft_am": e.get("gekauft_am"), "notiz": e["notiz"]} for e in eigene]
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
        con.close()
        geplant = _geplant(user["id"])                    # alle Binder: „in keinem Binder"
        fehlt = sum(1 for c in _wants(user["id"]) if c not in besitz)
        return {
            "karten": sum(besitz.values()), "verschiedene": len(besitz),
            "wert": round(wert, 2), "mit_preis": len([c for c in besitz if preise.get(c)]),
            "gezahlt": round(gezahlt, 2), "eintraege_mit_preis": mit_preis,
            "doppelte": sum(1 for n in besitz.values() if n >= 2),
            "ohne_binder": sum(1 for c in besitz if c not in geplant),
            "fehlt": fehlt,
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
                          " AND zustand='' AND sprache=''",
                          (user["id"], card_id, variante)).fetchone()
        andere = con.execute("SELECT COUNT(*) c FROM sammlung WHERE user_id=? AND card_id=? AND variante=?"
                             " AND (zustand<>'' OR sprache<>'')",
                             (user["id"], card_id, variante)).fetchone()["c"]
        if row:
            con.execute("DELETE FROM sammlung WHERE user_id=? AND card_id=? AND variante=?"
                        " AND zustand='' AND sprache=''", (user["id"], card_id, variante))
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
                        " AND zustand=? AND sprache=?",
                        (user["id"], card_id, variante, alt_zustand, alt_sprache))
        else:
            if (alt_zustand, alt_sprache) != (zustand, sprache):
                con.execute("DELETE FROM sammlung WHERE user_id=? AND card_id=? AND variante=?"
                            " AND zustand=? AND sprache=?",
                            (user["id"], card_id, variante, alt_zustand, alt_sprache))
            con.execute(
                "INSERT INTO sammlung (user_id, card_id, variante, zustand, sprache, anzahl, kaufpreis,"
                " gekauft_am, notiz, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(user_id, card_id, variante, zustand, sprache) DO UPDATE SET"
                " anzahl=excluded.anzahl, kaufpreis=excluded.kaufpreis, gekauft_am=excluded.gekauft_am,"
                " notiz=excluded.notiz, updated_at=excluded.updated_at",
                (user["id"], card_id, variante, zustand, sprache, anzahl, kaufpreis, gekauft, notiz,
                 _now(), _now()))
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
            " ON CONFLICT(user_id, card_id, variante, zustand, sprache) DO UPDATE SET"
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

    def kennzahlen():
        con = get_db()
        n = con.execute("SELECT COUNT(*) c FROM sammlung").fetchone()["c"]
        con.close()
        return n

    return kennzahlen
