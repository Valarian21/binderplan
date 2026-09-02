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


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def _var(v):
    v = str(v or "normal").strip().lower()
    return v if v in VARIANTEN else "normal"


def register(app, *, get_db, current_user, require_user, env, card_query, card_select, card_brief):
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

    def _geplant(user_id):
        """→ {card_id: wie oft in Bindern geplant}. Grundlage für „fehlt mir noch"."""
        con = get_db()
        reihen = con.execute("SELECT items FROM binders WHERE user_id = ?", (user_id,)).fetchall()
        con.close()
        aus = {}
        for r in reihen:
            try:
                items = json.loads(r["items"] or "[]")
            except Exception:
                continue
            for i in items:
                if i.get("type") == "card" and i.get("id"):
                    aus[i["id"]] = aus.get(i["id"], 0) + 1
        return aus

    def _preise(card_ids):
        if not card_ids:
            return {}
        con = get_db()
        aus = {}
        ids = list(card_ids)
        for teil in [ids[i:i + 800] for i in range(0, len(ids), 800)]:
            marken = ",".join("?" * len(teil))
            for r in con.execute(f"SELECT card_id, eur FROM card_prices WHERE card_id IN ({marken})", teil):
                if r["eur"]:
                    aus[r["card_id"]] = r["eur"]
        con.close()
        return aus

    # --- Endpunkte ---------------------------------------------------------

    @app.get("/api/sammlung")
    def sammlung(request: Request, q: str = "", set_id: str = "", serie: str = "", typ: str = "",
                 rarity: str = "", illustrator: str = "", art_ort: str = "", art_merkmal: str = "",
                 art_zeit: str = "", art_wasser: int = 0, art_text: str = "",
                 nur: str = "", sortierung: str = "neu", limit: int = 60, offset: int = 0):
        """Die eigene Sammlung, mit denselben Filtern wie die Kartensuche — inklusive
        Bildmotiv. „Zeig mir alle Unterwasser-Karten, die ich besitze" geht nur so."""
        user = require_user(request)
        limit = max(1, min(200, limit))
        besitz = _besitz(user["id"])
        if not besitz:
            return {"karten": [], "gesamt": 0}

        geplant = _geplant(user["id"]) if nur in ("ohne_binder", "") else {}
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
        eintraege = {(r["card_id"], r["variante"]): dict(r) for r in con.execute(
            "SELECT * FROM sammlung WHERE user_id = ?", (user["id"],))}
        con.close()

        preise = _preise([r["id"] for r in treffer])
        karten = []
        for r in treffer:
            kurz = card_brief(r)
            eigene = [e for (cid, _), e in eintraege.items() if cid == r["id"]]
            kurz["anzahl"] = sum(e["anzahl"] for e in eigene)
            kurz["varianten"] = [{"variante": e["variante"], "anzahl": e["anzahl"], "zustand": e["zustand"],
                                  "kaufpreis": e["kaufpreis"], "notiz": e["notiz"]} for e in eigene]
            kurz["eur"] = preise.get(r["id"])
            kurz["wert"] = round((preise.get(r["id"]) or 0) * kurz["anzahl"], 2)
            kurz["geplant"] = geplant.get(r["id"], 0)
            karten.append(kurz)

        if sortierung == "wert":
            karten.sort(key=lambda k: -(k["wert"] or 0))
        elif sortierung == "name":
            karten.sort(key=lambda k: (k.get("name") or "").lower())
        elif sortierung == "anzahl":
            karten.sort(key=lambda k: -k["anzahl"])
        else:
            reihenfolge = {c: i for i, c in enumerate(
                [k for k in eintraege and sorted(eintraege, key=lambda x: eintraege[x]["updated_at"] or "", reverse=True)] )}
            karten.sort(key=lambda k: reihenfolge.get((k["id"], "normal"), 9999))
        return {"karten": karten[offset:offset + limit], "gesamt": len(karten)}

    @app.get("/api/sammlung/uebersicht")
    def uebersicht(request: Request):
        user = require_user(request)
        besitz = _besitz(user["id"])
        preise = _preise(list(besitz))
        wert = sum((preise.get(c) or 0) * n for c, n in besitz.items())
        con = get_db()
        gezahlt = con.execute("SELECT SUM(kaufpreis * anzahl) s FROM sammlung WHERE user_id = ?"
                              " AND kaufpreis IS NOT NULL", (user["id"],)).fetchone()["s"] or 0
        mit_preis = con.execute("SELECT COUNT(*) c FROM sammlung WHERE user_id = ? AND kaufpreis IS NOT NULL",
                                (user["id"],)).fetchone()["c"]
        con.close()
        geplant = _geplant(user["id"])
        fehlt = sum(1 for c in geplant if c not in besitz)
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
        row = con.execute("SELECT anzahl FROM sammlung WHERE user_id=? AND card_id=? AND variante=?",
                          (user["id"], card_id, variante)).fetchone()
        if row:
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
        zustand = str(data.get("zustand") or "").upper()[:2]
        if zustand not in ZUSTAENDE:
            zustand = ""
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
            con.execute("DELETE FROM sammlung WHERE user_id=? AND card_id=? AND variante=?",
                        (user["id"], card_id, variante))
        else:
            con.execute(
                "INSERT INTO sammlung (user_id, card_id, variante, anzahl, zustand, kaufpreis, gekauft_am,"
                " notiz, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(user_id, card_id, variante) DO UPDATE SET anzahl=excluded.anzahl,"
                " zustand=excluded.zustand, kaufpreis=excluded.kaufpreis, gekauft_am=excluded.gekauft_am,"
                " notiz=excluded.notiz, updated_at=excluded.updated_at",
                (user["id"], card_id, variante, anzahl, zustand, kaufpreis, gekauft, notiz, _now(), _now()))
        con.commit()
        con.close()
        return {"ok": True}

    @app.get("/api/sammlung/fehlt")
    def fehlt(request: Request, limit: int = 300):
        """In Bindern geplant, aber nicht in der Sammlung — die Kaufliste über alle Binder."""
        user = require_user(request)
        besitz = _besitz(user["id"])
        geplant = _geplant(user["id"])
        offen = [c for c in geplant if c not in besitz]
        if not offen:
            return {"karten": [], "gesamt": 0, "summe": 0}
        preise = _preise(offen)
        con = get_db()
        aus = []
        for teil in [offen[i:i + 600] for i in range(0, len(offen), 600)]:
            marken = ",".join("?" * len(teil))
            for r in con.execute(f"{card_select} WHERE cards.id IN ({marken})", teil):
                k = card_brief(r)
                k["eur"] = preise.get(r["id"])
                aus.append(k)
        con.close()
        aus.sort(key=lambda k: -(k["eur"] or 0))
        return {"karten": aus[:limit], "gesamt": len(aus),
                "summe": round(sum(k["eur"] or 0 for k in aus), 2)}

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

    @app.get("/api/sammlung/besitz")
    def besitz_liste(request: Request):
        """Kompakte Liste für den Binder: welche Karten besitze ich, wie oft."""
        user = current_user(request)
        if not user:
            return {"besitz": {}}
        return {"besitz": _besitz(user["id"])}

    def kennzahlen():
        con = get_db()
        n = con.execute("SELECT COUNT(*) c FROM sammlung").fetchone()["c"]
        con.close()
        return n

    return kennzahlen
