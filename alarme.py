"""Preis-Alarme und Wochen-Digest: der Grund, zurückzukommen, ohne nachzusehen.

„Sag mir, wenn Glurak unter 100 € fällt" oder „wenn mein Rest zum Grundset unter 200 €
liegt". Die Alarme werden einmal am Tag nach dem Preislauf geprüft (der Preis ändert
sich nur dort). Ein ausgelöster Alarm steht auf der Startseite, bis er gesehen wurde,
und geht als E-Mail hinaus, sobald der Mailversand eingerichtet ist (SMTP_PASS in der
.env). Fällt die Bedingung später wieder weg, wird der Alarm scharf gestellt und kann
erneut auslösen — ein Alarm ist eine Schwelle, kein einmaliger Wecker.

Telegram gibt es nur für den Betreiber: der Bot wird vom Hermes-Gateway gepollt, und
ein zweiter Empfänger von /start-Nachrichten wäre ein zweiter Poller. Für Nutzer gilt
deshalb App + E-Mail.

Der Digest fasst sonntags zusammen, was der Markt und die eigene Sammlung in der Woche
gemacht haben: Sammlungsbewegung, Set des Monats, drei Karten der Wunschliste, die
günstiger wurden. Er liegt in `digest` und erscheint auf der Startseite; als E-Mail
sobald SMTP da ist. Der Digest ist der Markt in einer Mail.
"""

import datetime
import json
import re
import time

import wert as _wert

from fastapi import HTTPException, Request

ALARME_FREI = 3          # ohne Plus: drei Alarme, damit man sieht, was es bringt
ALARME_MAX = 100
APP_URL = "https://binderplan.app"


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def _heute():
    return datetime.date.today().isoformat()


def _woche():
    """Kalenderwoche als Schlüssel, z. B. 2026-W37."""
    j, w, _t = datetime.date.today().isocalendar()
    return f"{j}-W{w:02d}"


def _eur(n, stellen=2):
    if n is None:
        return "–"
    return f"{n:,.{stellen}f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"


def tabellen_anlegen(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS preis_alarme (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, art TEXT NOT NULL, ziel TEXT NOT NULL,
            richtung TEXT NOT NULL DEFAULT 'unter', schwelle REAL NOT NULL,
            created_at TEXT, ausgeloest_am TEXT, letzter_wert REAL, gesehen INTEGER DEFAULT 0,
            versandt_am TEXT);
        CREATE INDEX IF NOT EXISTS idx_alarme_user ON preis_alarme(user_id);
        CREATE TABLE IF NOT EXISTS digest (
            user_id INTEGER NOT NULL, woche TEXT NOT NULL, inhalt TEXT,
            erstellt_am TEXT, versandt_am TEXT, gesehen INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, woche));
    """)
    con.commit()


# ------------------------------------------------------------------ Rechnen

def _karten_preis(con, card_id):
    r = con.execute("SELECT COALESCE(eur, eur_geschaetzt) eur FROM card_prices WHERE card_id = ?",
                    (card_id,)).fetchone()
    return r["eur"] if r else None


def _set_rest(con, user_id, set_id):
    """Summe der Trendpreise aller Karten des Sets, die nicht in der Sammlung liegen."""
    besitz = {r["card_id"] for r in con.execute(
        "SELECT DISTINCT card_id FROM sammlung WHERE user_id = ? AND anzahl > 0", (user_id,))}
    rest = 0.0
    fehlt = 0
    for r in con.execute("SELECT c.id, COALESCE(p.eur, p.eur_geschaetzt) eur FROM cards c"
                         " LEFT JOIN card_prices p ON p.card_id = c.id WHERE c.set_id = ?", (set_id,)):
        if r["id"] in besitz:
            continue
        fehlt += 1
        rest += r["eur"] or 0
    return round(rest, 2), fehlt


def _wert_von(con, alarm):
    if alarm["art"] == "set":
        return _set_rest(con, alarm["user_id"], alarm["ziel"])[0]
    return _karten_preis(con, alarm["ziel"])


def _bedingung(alarm, wert):
    if wert is None:
        return False
    return wert <= alarm["schwelle"] if alarm["richtung"] == "unter" else wert >= alarm["schwelle"]


def _name_von(con, alarm):
    if alarm["art"] == "set":
        r = con.execute("SELECT name, name_en FROM sets WHERE id = ?", (alarm["ziel"],)).fetchone()
        return (r["name"] or r["name_en"]) if r else alarm["ziel"]
    r = con.execute("SELECT c.name_de, c.name_en, c.local_id, s.name set_name FROM cards c"
                    " LEFT JOIN sets s ON s.id = c.set_id WHERE c.id = ?", (alarm["ziel"],)).fetchone()
    if not r:
        return alarm["ziel"]
    return f"{r['name_de'] or r['name_en']} · {r['set_name'] or ''} {r['local_id'] or ''}".strip()


def _alarm_text(name, alarm, wert):
    if alarm["art"] == "set":
        return (f"Rest zum Set {name}: {_eur(wert, 0)} — "
                f"{'unter' if alarm['richtung'] == 'unter' else 'über'} deiner Schwelle von {_eur(alarm['schwelle'], 0)}.")
    return (f"{name}: Cardmarket-Trend {_eur(wert)} — "
            f"{'unter' if alarm['richtung'] == 'unter' else 'über'} deiner Schwelle von {_eur(alarm['schwelle'])}.")


def tagesjob(get_db, mail_senden=None, mail_konfiguriert=None):
    """Alle Alarme gegen die frischen Preise prüfen. Läuft nach dem Preislauf."""
    con = get_db()
    tabellen_anlegen(con)
    alarme = [dict(r) for r in con.execute("SELECT * FROM preis_alarme")]
    mails = {}
    ausgeloest = zurueck = 0
    for a in alarme:
        wert = _wert_von(con, a)
        trifft = _bedingung(a, wert)
        if trifft and not a["ausgeloest_am"]:
            con.execute("UPDATE preis_alarme SET ausgeloest_am = ?, letzter_wert = ?, gesehen = 0 WHERE id = ?",
                        (_now(), wert, a["id"]))
            mails.setdefault(a["user_id"], []).append((a, wert))
            ausgeloest += 1
        elif not trifft and a["ausgeloest_am"]:
            # Wieder scharf: die Karte ist über die Schwelle zurück.
            con.execute("UPDATE preis_alarme SET ausgeloest_am = NULL, letzter_wert = ? WHERE id = ?", (wert, a["id"]))
            zurueck += 1
        else:
            con.execute("UPDATE preis_alarme SET letzter_wert = ? WHERE id = ?", (wert, a["id"]))
    con.commit()
    versandt = 0
    if mails and mail_senden and mail_konfiguriert and mail_konfiguriert():
        for user_id, liste in mails.items():
            u = con.execute("SELECT email, name FROM users WHERE id = ?", (user_id,)).fetchone()
            if not u:
                continue
            zeilen = [_alarm_text(_name_von(con, a), a, w) for a, w in liste]
            text = (f"Hallo{(' ' + u['name']) if u['name'] else ''},\n\n"
                    + "\n".join("• " + z for z in zeilen)
                    + f"\n\nAlle Alarme: {APP_URL}/app#sammlung\n\nBinderplan")
            if mail_senden(u["email"], f"Preis-Alarm: {len(zeilen)} {'Karte' if len(zeilen) == 1 else 'Treffer'}", text):
                con.execute("UPDATE preis_alarme SET versandt_am = ? WHERE id IN (%s)" % ",".join("?" * len(liste)),
                            (_now(), *[a["id"] for a, _w in liste]))
                versandt += 1
        con.commit()
    con.close()
    return {"geprueft": len(alarme), "ausgeloest": ausgeloest, "zurueck": zurueck, "mails": versandt}


def _digest_fuer(con, user_id):
    """Der Inhalt des Wochenrückblicks für ein Konto — oder None, wenn es nichts zu sagen gibt."""
    posten = [dict(r) for r in con.execute(
        "SELECT s.card_id, s.variante, s.anzahl, s.zustand, c.name_de, c.name_en, c.local_id,"
        " (SELECT name FROM sets WHERE sets.id = c.set_id) set_name,"
        f" {_wert.sql_eur()} eur, p.eur_holo, p.eur_low, p.eur_avg7"
        " FROM sammlung s JOIN cards c ON c.id = s.card_id LEFT JOIN card_prices p ON p.card_id = s.card_id"
        " WHERE s.user_id = ? AND s.anzahl > 0", (user_id,))]
    wants = [dict(r) for r in con.execute(
        "SELECT w.card_id, c.name_de, c.name_en, c.local_id,"
        " (SELECT name FROM sets WHERE sets.id = c.set_id) set_name,"
        " COALESCE(p.eur, p.eur_geschaetzt) eur, p.eur_avg30"
        " FROM wants w JOIN cards c ON c.id = w.card_id LEFT JOIN card_prices p ON p.card_id = w.card_id"
        " WHERE w.user_id = ?", (user_id,))]
    if not posten and not wants:
        return None
    # Der Vergleichswert ist der eigene Preis von vor sieben Tagen, nicht Cardmarkets
    # Verkaufsschnitt — sonst meldete der Rückblick Gewinne, die es nie gab (Audit B1).
    basis7 = _wert.historie_basis(con, [p["card_id"] for p in posten], 7)
    for p in posten:
        p["eur_avg7"] = (basis7.get(p["card_id"]) or (None,))[0]
    # Der Zustand wurde hier geladen und nie benutzt: der Rückblick nannte einen anderen Wert
    # als die Kachel darüber. Jetzt rechnet er wie die Sammlung.
    wert, _n, _ohne = _wert.zeilen_wert(posten)
    diff = basis = 0.0
    bewegung = []
    for p in posten:
        d = _wert.bewegung_euro(p, "eur_avg7")
        if d is None:
            continue
        diff += d
        # Zähler und Nenner mit demselben Faktor (Holo, Zustand) – sonst stimmt der
        # Prozentsatz nicht zum Eurobetrag daneben (dieselbe Falle wie in Audit B3).
        _w = _wert.posten_wert(p.get("eur"), p.get("eur_holo"), p.get("eur_low"),
                               p.get("variante") or "normal", p.get("zustand") or "")
        _f = (_w / p["eur"]) if (_w and p.get("eur")) else 1
        basis += (p["eur_avg7"] or 0) * _f * (p["anzahl"] or 0)
        if abs(d) >= 0.5:
            bewegung.append({"id": p["card_id"], "name": p["name_de"] or p["name_en"], "set": p["set_name"],
                             "nr": p["local_id"], "diff": d,
                             "prozent": _wert.bewegung_prozent(p["eur"], p["eur_avg7"])})
    bewegung.sort(key=lambda b: -abs(b["diff"]))
    besitz = {p["card_id"] for p in posten}
    guenstiger = []
    for w in wants:
        if w["card_id"] in besitz or not w["eur"] or not w["eur_avg30"] or w["eur"] < 1 or w["eur_avg30"] < 1:
            continue
        q = w["eur"] / w["eur_avg30"]
        if q > 3 or q < 1 / 3 or q > 0.97:
            continue
        guenstiger.append({"id": w["card_id"], "name": w["name_de"] or w["name_en"], "set": w["set_name"],
                           "nr": w["local_id"], "eur": round(w["eur"], 2), "avg30": round(w["eur_avg30"], 2),
                           "prozent": round((q - 1) * 100, 1)})
    guenstiger.sort(key=lambda g: g["prozent"])
    set_monat = None
    try:
        tag = con.execute("SELECT MAX(datum) d FROM markt_tag").fetchone()["d"]
        r = con.execute("SELECT name, schluessel, bew30, bew30_n FROM markt_tag WHERE ebene='set' AND datum=?"
                        " AND bew30 IS NOT NULL AND bew30_n >= 25 ORDER BY bew30 DESC LIMIT 1", (tag,)).fetchone()
        if r:
            set_monat = {"name": r["name"], "set_id": r["schluessel"], "bew30": r["bew30"]}
    except Exception:
        pass
    return {
        "woche": _woche(), "stand": _heute(),
        "wert": wert, "bew7_eur": round(diff, 2),
        "bew7": round(diff / basis * 100, 1) if basis else None,
        "karten": sum(p["anzahl"] or 0 for p in posten),
        "bewegung": bewegung[:3], "guenstiger": guenstiger[:3], "set_monat": set_monat,
    }


def _digest_text(u, d):
    zeilen = [f"Hallo{(' ' + u['name']) if u['name'] else ''},", "",
              f"Deine Sammlung ist heute {_eur(d['wert'], 0)} wert"
              + (f" ({'+' if d['bew7_eur'] >= 0 else ''}{_eur(d['bew7_eur'], 0)} in sieben Tagen)." if d.get("bew7") is not None else ".")]
    if d["bewegung"]:
        zeilen += ["", "Größte Bewegungen:"]
        zeilen += [f"• {b['name']} ({b['set']} {b['nr']}): {'+' if b['diff'] >= 0 else ''}{_eur(b['diff'])} · {b['prozent']:+.1f} %".replace(".", ",")
                   for b in d["bewegung"]]
    if d["set_monat"]:
        zeilen += ["", f"Set des Monats: {d['set_monat']['name']} ({d['set_monat']['bew30']:+.1f} % in 30 Tagen)".replace(".", ",")]
    if d["guenstiger"]:
        zeilen += ["", "Günstiger geworden auf deiner Wunschliste:"]
        zeilen += [f"• {g['name']} ({g['set']} {g['nr']}): {_eur(g['eur'])} statt {_eur(g['avg30'])} im Schnitt"
                   for g in d["guenstiger"]]
    zeilen += ["", f"Alles im Detail: {APP_URL}/app#sammlung", "", "Binderplan"]
    return "\n".join(zeilen)


def wochenjob(get_db, mail_senden=None, mail_konfiguriert=None):
    """Sonntags: ein Rückblick je Konto mit Sammlung oder Wunschliste."""
    con = get_db()
    tabellen_anlegen(con)
    woche = _woche()
    nutzer = [dict(r) for r in con.execute(
        "SELECT id, email, name FROM users WHERE id IN (SELECT user_id FROM sammlung WHERE anzahl > 0"
        " UNION SELECT user_id FROM wants)")]
    erstellt = versandt = 0
    kann_mail = bool(mail_senden and mail_konfiguriert and mail_konfiguriert())
    for u in nutzer:
        if con.execute("SELECT 1 FROM digest WHERE user_id = ? AND woche = ?", (u["id"], woche)).fetchone():
            continue
        d = _digest_fuer(con, u["id"])
        if not d:
            continue
        con.execute("INSERT INTO digest (user_id, woche, inhalt, erstellt_am) VALUES (?,?,?,?)",
                    (u["id"], woche, json.dumps(d, ensure_ascii=False), _now()))
        erstellt += 1
        if kann_mail and mail_senden(u["email"], f"Dein Wochenrückblick: Sammlung {_eur(d['wert'], 0)}", _digest_text(u, d)):
            con.execute("UPDATE digest SET versandt_am = ? WHERE user_id = ? AND woche = ?", (_now(), u["id"], woche))
            versandt += 1
    con.commit()
    con.close()
    return {"erstellt": erstellt, "mails": versandt, "woche": woche}


# ------------------------------------------------------------------ Endpunkte

def register(app, *, get_db, require_user, ist_bezahlt=None, app_url=None):
    global APP_URL
    if app_url:
        APP_URL = app_url.rstrip("/")
    ist_bezahlt = ist_bezahlt or (lambda u: True)
    con = get_db()
    tabellen_anlegen(con)
    con.close()

    def _mit_namen(con, alarme):
        for a in alarme:
            a["name"] = _name_von(con, a)
            a["trifft"] = bool(a["ausgeloest_am"])
        return alarme

    @app.get("/api/alarme")
    def alarme_liste(request: Request):
        user = require_user(request)
        con = get_db()
        alarme = [dict(r) for r in con.execute(
            "SELECT * FROM preis_alarme WHERE user_id = ? ORDER BY ausgeloest_am DESC, id DESC", (user["id"],))]
        _mit_namen(con, alarme)
        con.close()
        return {"alarme": alarme, "frei": ALARME_FREI, "plus": ist_bezahlt(user),
                "neu": sum(1 for a in alarme if a["ausgeloest_am"] and not a["gesehen"])}

    @app.post("/api/alarme")
    async def alarm_setzen(request: Request):
        user = require_user(request)
        data = await request.json()
        art = "set" if data.get("art") == "set" else "karte"
        ziel = str(data.get("ziel") or "").strip()
        richtung = "ueber" if data.get("richtung") == "ueber" else "unter"
        try:
            schwelle = round(float(str(data.get("schwelle")).replace(",", ".")), 2)
        except Exception:
            raise HTTPException(400, "Keine Schwelle")
        if not ziel or schwelle <= 0 or schwelle > 1_000_000:
            raise HTTPException(400, "Ziel oder Schwelle fehlt")
        con = get_db()
        tabelle = "sets" if art == "set" else "cards"
        if not con.execute(f"SELECT 1 FROM {tabelle} WHERE id = ?", (ziel,)).fetchone():
            con.close()
            raise HTTPException(404, "Nicht gefunden")
        n = con.execute("SELECT COUNT(*) c FROM preis_alarme WHERE user_id = ?", (user["id"],)).fetchone()["c"]
        schon = con.execute("SELECT id FROM preis_alarme WHERE user_id = ? AND art = ? AND ziel = ? AND richtung = ?",
                            (user["id"], art, ziel, richtung)).fetchone()
        if not schon and n >= (ALARME_MAX if ist_bezahlt(user) else ALARME_FREI):
            con.close()
            raise HTTPException(402, detail={"code": "limit_pro"})
        if schon:
            con.execute("UPDATE preis_alarme SET schwelle = ?, ausgeloest_am = NULL, gesehen = 0 WHERE id = ?",
                        (schwelle, schon["id"]))
            alarm_id = schon["id"]
        else:
            alarm_id = con.execute(
                "INSERT INTO preis_alarme (user_id, art, ziel, richtung, schwelle, created_at) VALUES (?,?,?,?,?,?)",
                (user["id"], art, ziel, richtung, schwelle, _now())).lastrowid
        # Sofort prüfen: trifft die Schwelle heute schon, soll das nicht bis morgen früh warten.
        a = dict(con.execute("SELECT * FROM preis_alarme WHERE id = ?", (alarm_id,)).fetchone())
        wert = _wert_von(con, a)
        if _bedingung(a, wert):
            con.execute("UPDATE preis_alarme SET ausgeloest_am = ?, letzter_wert = ? WHERE id = ?", (_now(), wert, alarm_id))
        else:
            con.execute("UPDATE preis_alarme SET letzter_wert = ? WHERE id = ?", (wert, alarm_id))
        con.commit()
        a = dict(con.execute("SELECT * FROM preis_alarme WHERE id = ?", (alarm_id,)).fetchone())
        _mit_namen(con, [a])
        con.close()
        return {"ok": True, "alarm": a}

    @app.post("/api/alarme/loeschen")
    async def alarm_loeschen(request: Request):
        user = require_user(request)
        data = await request.json()
        con = get_db()
        con.execute("DELETE FROM preis_alarme WHERE user_id = ? AND id = ?", (user["id"], int(data.get("id") or 0)))
        con.commit()
        con.close()
        return {"ok": True}

    @app.post("/api/alarme/gesehen")
    async def alarme_gesehen(request: Request):
        user = require_user(request)
        con = get_db()
        con.execute("UPDATE preis_alarme SET gesehen = 1 WHERE user_id = ? AND ausgeloest_am IS NOT NULL", (user["id"],))
        con.commit()
        con.close()
        return {"ok": True}

    @app.get("/api/digest")
    def digest_aktuell(request: Request):
        """Der jüngste Wochenrückblick — auf der Startseite, solange keine Mail hinausgeht."""
        user = require_user(request)
        con = get_db()
        r = con.execute("SELECT woche, inhalt, erstellt_am, gesehen FROM digest WHERE user_id = ?"
                        " ORDER BY woche DESC LIMIT 1", (user["id"],)).fetchone()
        con.close()
        if not r:
            return {"digest": None}
        try:
            inhalt = json.loads(r["inhalt"] or "{}")
        except Exception:
            inhalt = {}
        inhalt.update({"erstellt_am": r["erstellt_am"], "gesehen": bool(r["gesehen"])})
        return {"digest": inhalt}

    @app.post("/api/digest/gesehen")
    async def digest_gesehen(request: Request):
        user = require_user(request)
        con = get_db()
        con.execute("UPDATE digest SET gesehen = 1 WHERE user_id = ?", (user["id"],))
        con.commit()
        con.close()
        return {"ok": True}

    def kennzahlen():
        con = get_db()
        r = con.execute("SELECT COUNT(*) c, SUM(ausgeloest_am IS NOT NULL) a FROM preis_alarme").fetchone()
        con.close()
        return {"alarme": r["c"], "ausgeloest": r["a"] or 0}

    return kennzahlen
