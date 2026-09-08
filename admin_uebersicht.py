# Binderplan – Betreiber-Übersicht fürs Empire-Dashboard
#
# Drei Endpunkte, alle nur mit ADMIN_KEY (wie /api/admin/stats), gelesen vom Dashboard über
# einen Proxy in dessen main.py. Nichts davon ist für Nutzer sichtbar.
#
#   GET /api/admin/uebersicht   Kennzahlen: Nutzer, Umsatz (Bestellungen + Stripe-Netto),
#                               OpenRouter-Verbrauch (Schlüssel-Auskunft + Artwork-Kosten),
#                               Credits, Kunstseiten, Vitrine, Katalog
#   GET /api/admin/nutzer       Liste aller Konten mit Tarif, Guthaben, Umsatz, letzter Aktivität
#   GET /api/admin/nutzer/{id}  Ein Konto mit Zeitleiste: Käufe, Buchungen, Kunstseiten, Binder
#
# Externe Auskünfte (Stripe, OpenRouter) werden 10 Minuten zwischengespeichert, damit das
# Dashboard sie beliebig oft laden darf, ohne die Anbieter zu belasten.

import json
import time
from datetime import datetime, timedelta

import httpx
from fastapi import HTTPException

_dep = {}
_cache = {}
CACHE_SEK = 600


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _pruefen(key):
    ak = _dep["admin_key"]()
    if not ak or key != ak:
        raise HTTPException(403, "Falscher Schlüssel")


def _gecacht(name, laden):
    t = _cache.get(name)
    if t and time.time() - t[0] < CACHE_SEK:
        return t[1]
    try:
        wert = laden()
    except Exception as e:
        wert = {"fehler": str(e)[:200]}
    _cache[name] = (time.time(), wert)
    return wert


# --- Externe Quellen ---------------------------------------------------------------

def _openrouter_key():
    key = _dep["env"]().get("OPENROUTER_KEY", "")
    if not key:
        return {"fehler": "kein Schlüssel"}
    r = httpx.get("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"}, timeout=15)
    d = (r.json() or {}).get("data") or {}
    return {"gesamt": round(float(d.get("usage") or 0), 2), "heute": round(float(d.get("usage_daily") or 0), 2),
            "woche": round(float(d.get("usage_weekly") or 0), 2), "monat": round(float(d.get("usage_monthly") or 0), 2),
            "limit": d.get("limit"), "limit_rest": d.get("limit_remaining")}


def _stripe():
    key = _dep["env"]().get("STRIPE_SECRET_KEY", "")
    if not key:
        return {"fehler": "kein Schlüssel", "buchungen": []}
    con = _dep["get_db"]()
    kunden = {r["stripe_customer"]: r["email"] for r in
              con.execute("SELECT stripe_customer, email FROM users WHERE stripe_customer IS NOT NULL")}
    con.close()
    # Ladungen mit Kunde + Gebühr, dazu Rückerstattungen und Auszahlungen aus den Kontobewegungen
    r = httpx.get("https://api.stripe.com/v1/charges", auth=(key, ""), params={"limit": 100}, timeout=20)
    charges = (r.json() or {}).get("data") or []
    r2 = httpx.get("https://api.stripe.com/v1/balance_transactions", auth=(key, ""), params={"limit": 100}, timeout=20)
    bt = {t["id"]: t for t in ((r2.json() or {}).get("data") or [])}
    buchungen, brutto, gebuehr, netto, fremd = [], 0.0, 0.0, 0.0, 0.0
    for c in charges:
        t = bt.get(c.get("balance_transaction")) or {}
        betrag = (c.get("amount") or 0) / 100
        geb = (t.get("fee") or 0) / 100
        net = (t.get("net") or 0) / 100 if t else betrag
        kunde = c.get("customer")
        eigener = kunde in kunden
        eintrag = {
            "am": datetime.utcfromtimestamp(c["created"]).strftime("%Y-%m-%d %H:%M"),
            "betrag": betrag, "gebuehr": geb, "netto": net, "waehrung": (c.get("currency") or "").upper(),
            "status": c.get("status"), "erstattet": (c.get("amount_refunded") or 0) / 100,
            "beschreibung": c.get("description") or "", "email": kunden.get(kunde) or (c.get("billing_details") or {}).get("email") or "",
            "binderplan": eigener, "id": c.get("id"),
        }
        buchungen.append(eintrag)
        if c.get("status") == "succeeded":
            if eigener:
                brutto += betrag; gebuehr += geb; netto += net
            else:
                fremd += betrag
    sonstige = [{"am": datetime.utcfromtimestamp(t["created"]).strftime("%Y-%m-%d %H:%M"), "typ": t["type"],
                 "betrag": (t.get("amount") or 0) / 100, "beschreibung": t.get("description") or ""}
                for t in bt.values() if t["type"] not in ("charge", "payment")]
    gebuehr_sonst = -sum(x["betrag"] for x in sonstige if x["typ"] == "stripe_fee")
    return {"brutto": round(brutto, 2), "gebuehren": round(gebuehr + gebuehr_sonst, 2), "netto": round(netto - gebuehr_sonst, 2),
            "fremd": round(fremd, 2), "buchungen": buchungen, "sonstige": sonstige, "stand": _now()}


# --- Kennzahlen ---------------------------------------------------------------------

def _aktivitaet_sql():
    """Letzte Aktivität je Nutzer: das Jüngste aus Binder-Änderung, Kunstseite, Buchung, Bestellung, Anmeldung."""
    return """
        SELECT u.id, MAX(t) AS zuletzt FROM users u LEFT JOIN (
            SELECT user_id, MAX(updated_at) t FROM binders GROUP BY user_id
            UNION ALL SELECT user_id, MAX(created_at) FROM artworks GROUP BY user_id
            UNION ALL SELECT user_id, MAX(created_at) FROM credit_buchungen GROUP BY user_id
            UNION ALL SELECT user_id, MAX(created_at) FROM bestellungen GROUP BY user_id
            UNION ALL SELECT user_id, MAX(created_at) FROM sessions GROUP BY user_id
        ) a ON a.user_id = u.id GROUP BY u.id"""


def uebersicht():
    con = _dep["get_db"]()
    q = lambda sql, *p: con.execute(sql, p).fetchone()
    heute = datetime.utcnow().strftime("%Y-%m-%d")
    monat = datetime.utcnow().strftime("%Y-%m")
    d7 = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    d30 = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    akt = {r["id"]: r["zuletzt"] for r in con.execute(_aktivitaet_sql())}
    nutzer = {
        "gesamt": q("SELECT COUNT(*) c FROM users")["c"],
        "bestaetigt": q("SELECT COUNT(*) c FROM users WHERE email_bestaetigt IS NOT NULL")["c"],
        "neu_7": q("SELECT COUNT(*) c FROM users WHERE created_at >= ?", d7)["c"],
        "neu_30": q("SELECT COUNT(*) c FROM users WHERE created_at >= ?", d30)["c"],
        "aktiv_7": sum(1 for t in akt.values() if t and t >= d7),
        "aktiv_30": sum(1 for t in akt.values() if t and t >= d30),
        "zahlend": q("SELECT COUNT(*) c FROM users WHERE plan NOT IN ('free','lifetime')")["c"],
        "plaene": {r["plan"]: r["c"] for r in con.execute("SELECT plan, COUNT(*) c FROM users GROUP BY plan")},
        "abos_aktiv": q("SELECT COUNT(*) c FROM users WHERE stripe_sub IS NOT NULL AND abo_status='active'")["c"],
        "abos_gekuendigt": q("SELECT COUNT(*) c FROM users WHERE abo_kuendigt=1")["c"],
    }
    # Umsatz aus dem eigenen Bestellprotokoll (bezahlt), nach Art und Monat
    umsatz = {
        "gesamt": q("SELECT COALESCE(SUM(betrag),0) s FROM bestellungen WHERE status='bezahlt'")["s"],
        "monat": q("SELECT COALESCE(SUM(betrag),0) s FROM bestellungen WHERE status='bezahlt' AND created_at LIKE ?", monat + "%")["s"],
        "abo": q("SELECT COALESCE(SUM(betrag),0) s, COUNT(*) n FROM bestellungen WHERE status='bezahlt' AND art IN ('plus','pro')"),
        "paket": q("SELECT COALESCE(SUM(betrag),0) s, COUNT(*) n FROM bestellungen WHERE status='bezahlt' AND art='paket'"),
        "monate": [dict(r) for r in con.execute(
            "SELECT substr(created_at,1,7) monat, SUM(betrag) summe, COUNT(*) n,"
            " SUM(CASE WHEN art='paket' THEN betrag ELSE 0 END) paket, SUM(CASE WHEN art IN ('plus','pro') THEN betrag ELSE 0 END) abo"
            " FROM bestellungen WHERE status='bezahlt' GROUP BY 1 ORDER BY 1 DESC LIMIT 12")],
        "letzte": [dict(r) for r in con.execute(
            "SELECT b.id, b.art, b.variante, b.betrag, b.waehrung, b.status, b.created_at, u.email"
            " FROM bestellungen b LEFT JOIN users u ON u.id=b.user_id WHERE b.art != 'kuendigung_angefragt'"
            " ORDER BY b.id DESC LIMIT 20")],
    }
    umsatz["abo"] = {"summe": umsatz["abo"]["s"], "anzahl": umsatz["abo"]["n"]}
    umsatz["paket"] = {"summe": umsatz["paket"]["s"], "anzahl": umsatz["paket"]["n"]}
    # MRR aus laufenden Abos (Monatspreis, Jahresabo auf Monat umgelegt)
    tarife = _dep["abo"].TARIFE
    mrr = 0.0
    for r in con.execute("SELECT plan, abo_intervall FROM users WHERE stripe_sub IS NOT NULL AND abo_status='active'"):
        t = tarife.get(r["plan"]) or {}
        mrr += (t.get("preis_jahr") or 0) / 12 if r["abo_intervall"] == "jahr" else (t.get("preis_monat") or 0)
    umsatz["mrr"] = round(mrr, 2)
    # KI-Kosten: Kunstseiten aus der eigenen Tabelle (je Tag/Monat/Modell), Gesamtverbrauch vom Schlüssel
    ki = {
        "artwork_gesamt": q("SELECT COALESCE(SUM(kosten_usd),0) s, COUNT(*) n FROM artworks WHERE status='fertig'"),
        "artwork_heute": q("SELECT COALESCE(SUM(kosten_usd),0) s, COUNT(*) n FROM artworks WHERE created_at LIKE ?", heute + "%"),
        "artwork_monat": q("SELECT COALESCE(SUM(kosten_usd),0) s, COUNT(*) n FROM artworks WHERE created_at LIKE ?", monat + "%"),
        "artwork_tage": [dict(r) for r in con.execute(
            "SELECT substr(created_at,1,10) tag, ROUND(SUM(kosten_usd),3) kosten, COUNT(*) n,"
            " SUM(CASE WHEN status='fehler' THEN 1 ELSE 0 END) fehler FROM artworks WHERE created_at >= ? GROUP BY 1 ORDER BY 1", (d30,))],
        "artwork_modelle": [dict(r) for r in con.execute(
            "SELECT modell, COUNT(*) n, ROUND(SUM(kosten_usd),2) kosten, ROUND(AVG(kosten_usd),3) schnitt FROM artworks"
            " WHERE status='fertig' GROUP BY modell ORDER BY kosten DESC")],
        "artwork_fehler": q("SELECT COUNT(*) c FROM artworks WHERE status='fehler'")["c"],
        "openrouter": _gecacht("openrouter", _openrouter_key),
        "tageslimit_usd": getattr(__import__("artwork"), "TAGESLIMIT_USD", None),
    }
    for k in ("artwork_gesamt", "artwork_heute", "artwork_monat"):
        ki[k] = {"kosten": round(ki[k]["s"], 3), "anzahl": ki[k]["n"]}
    credits = {
        "im_umlauf": q("SELECT COALESCE(SUM(credits),0) g, COALESCE(SUM(credits_abo),0) a FROM users"),
        "gekauft": q("SELECT COALESCE(SUM(delta),0) s FROM credit_buchungen WHERE grund='kauf'")["s"],
        "abo_gutschrift": q("SELECT COALESCE(SUM(delta),0) s FROM credit_buchungen WHERE grund='abo_guthaben'")["s"],
        "start": q("SELECT COALESCE(SUM(delta),0) s FROM credit_buchungen WHERE grund='start'")["s"],
        "verbraucht": -q("SELECT COALESCE(SUM(delta),0) s FROM credit_buchungen WHERE delta < 0")["s"],
        "artwork_anteil": q("SELECT COALESCE(SUM(delta),0) s FROM credit_buchungen WHERE grund='artwork_anteil'")["s"],
        "erstattet": q("SELECT COALESCE(SUM(delta),0) s FROM credit_buchungen WHERE grund LIKE 'erstattung%'")["s"],
    }
    credits["im_umlauf"] = {"gekauft": credits["im_umlauf"]["g"], "abo": credits["im_umlauf"]["a"]}
    vitrine = {
        "binder_gesamt": q("SELECT COUNT(*) c FROM binders")["c"],
        "binder_oeffentlich": q("SELECT COUNT(*) c FROM binders WHERE sichtbar=1")["c"],
        "kunstseiten_oeffentlich": q("SELECT COUNT(*) c FROM artworks WHERE oeffentlich=1 AND status='fertig'")["c"],
        "uebernahmen": q("SELECT COUNT(*) c FROM artwork_freigaben")["c"],
        "herzen_binder": q("SELECT COUNT(*) c FROM stimmen")["c"],
        "herzen_kunst": q("SELECT COUNT(*) c FROM artwork_stimmen")["c"],
        "meldungen_offen": q("SELECT COUNT(*) c FROM meldungen WHERE status='offen'")["c"],
        "pdf_exporte": int((q("SELECT value FROM kv WHERE key='pdf_exports'") or {"value": 0})["value"]),
    }
    katalog = {
        "karten": q("SELECT COUNT(*) c FROM cards")["c"], "sets": q("SELECT COUNT(*) c FROM sets")["c"],
        "neuestes_set": dict(q("SELECT id, name, release_date FROM sets ORDER BY release_date DESC LIMIT 1") or {}),
        "preislauf": (q("SELECT value FROM kv WHERE key='preishistorie_lauf'") or {"value": None})["value"],
        "katalog_sync": (q("SELECT value FROM kv WHERE key='last_sync'") or {"value": None})["value"],
    }
    con.close()
    return {"stand": _now(), "nutzer": nutzer, "umsatz": umsatz, "stripe": _gecacht("stripe", _stripe),
            "ki": ki, "credits": credits, "vitrine": vitrine, "katalog": katalog}


def nutzer_liste():
    con = _dep["get_db"]()
    akt = {r["id"]: r["zuletzt"] for r in con.execute(_aktivitaet_sql())}
    rows = con.execute("""
        SELECT u.id, u.email, u.name, u.plan, u.credits, u.credits_abo, u.abo_status, u.abo_bis, u.abo_kuendigt,
               u.abo_intervall, u.created_at, u.email_bestaetigt, u.stripe_customer, p.name AS oeff_name,
               (SELECT COUNT(*) FROM binders b WHERE b.user_id=u.id) binder,
               (SELECT COUNT(*) FROM artworks a WHERE a.user_id=u.id AND a.status='fertig') kunstseiten,
               (SELECT COALESCE(SUM(kosten_usd),0) FROM artworks a WHERE a.user_id=u.id) ki_kosten,
               (SELECT COALESCE(SUM(betrag),0) FROM bestellungen o WHERE o.user_id=u.id AND o.status='bezahlt') umsatz,
               (SELECT COUNT(*) FROM bestellungen o WHERE o.user_id=u.id AND o.status='bezahlt') kaeufe
        FROM users u LEFT JOIN profile p ON p.user_id=u.id ORDER BY u.id DESC""").fetchall()
    con.close()
    out = []
    for r in rows:
        d = dict(r)
        d["zuletzt"] = akt.get(d["id"])
        d["ki_kosten"] = round(d["ki_kosten"], 3)
        out.append(d)
    return {"nutzer": out, "stand": _now()}


GRUND_TEXT = {"start": "Startguthaben", "kauf": "Credits gekauft", "artwork": "Kunstseite erzeugt",
              "abo_guthaben": "Monatsguthaben", "artwork_uebernahme": "Fremde Kunstseite übernommen",
              "artwork_anteil": "Anteil aus Übernahme", "erstattung_fehler": "Rückbuchung (Fehler)",
              "erstattung_neustart": "Rückbuchung (Neustart)", "test": "Korrektur"}


def nutzer_detail(uid: int):
    con = _dep["get_db"]()
    u = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        con.close(); raise HTTPException(404, "Nutzer nicht gefunden")
    u = dict(u)
    for k in ("pw_hash", "salt", "reset_token", "bestaetigung_token", "kuend_token"):
        u.pop(k, None)
    profil = con.execute("SELECT name, kurztext, tauschliste, gesperrt, created_at FROM profile WHERE user_id=?", (uid,)).fetchone()
    titel = {r["id"]: r["titel"] for r in con.execute("SELECT id, titel FROM artworks WHERE user_id=?", (uid,))}
    buchungen = [dict(r) for r in con.execute(
        "SELECT id, delta, grund, ref, saldo_danach, created_at FROM credit_buchungen WHERE user_id=? ORDER BY id DESC", (uid,))]
    for b in buchungen:
        b["text"] = GRUND_TEXT.get(b["grund"], b["grund"])
        b["titel"] = titel.get(b["ref"] or "", "") if (b["grund"] or "").startswith("artwork") else ""
    bestellungen = [dict(r) for r in con.execute(
        "SELECT id, art, variante, betrag, waehrung, status, created_at FROM bestellungen WHERE user_id=? ORDER BY id DESC", (uid,))]
    kunst = [dict(r) for r in con.execute(
        "SELECT a.id, a.titel, a.seite, a.layout, a.stil, a.status, a.kosten_usd, a.credits, a.oeffentlich, a.downloads, a.verdient,"
        " a.created_at, a.modell, a.anker, a.pokemon, b.name AS binder,"
        " (SELECT COUNT(*) FROM artwork_stimmen s WHERE s.artwork_id=a.id) herzen"
        " FROM artworks a LEFT JOIN binders b ON b.id=a.binder_id WHERE a.user_id=? ORDER BY a.created_at DESC", (uid,))]
    for k in kunst:
        k["anker_n"] = len(json.loads(k.pop("anker") or "{}"))
        try:
            k["pokemon"] = [p.get("name_de") or p.get("name_en") for p in json.loads(k["pokemon"] or "[]")]
        except Exception:
            k["pokemon"] = []
        k["vorschau"] = f"/api/artwork/{k['id']}/bild?v=vorschau" if k["status"] == "fertig" else None
    binder = []
    for r in con.execute("SELECT id, name, mode, layout, items, created_at, updated_at, sichtbar, veroeffentlicht_at FROM binders WHERE user_id=? ORDER BY updated_at DESC", (uid,)):
        d = dict(r)
        try:
            items = json.loads(d.pop("items") or "[]")
            d["karten"] = sum(1 for i in items if i and i.get("type") == "card")
            d["kunstfaecher"] = sum(1 for i in items if i and i.get("type") == "art")
        except Exception:
            d["karten"] = d["kunstfaecher"] = 0
        binder.append(d)
    sessions = [r["created_at"] for r in con.execute("SELECT created_at FROM sessions WHERE user_id=? ORDER BY created_at DESC LIMIT 20", (uid,))]
    uebernommen = [dict(r) for r in con.execute(
        "SELECT f.created_at, a.titel, a.id, p.name AS von FROM artwork_freigaben f JOIN artworks a ON a.id=f.artwork_id"
        " LEFT JOIN profile p ON p.user_id=a.user_id WHERE f.user_id=? ORDER BY f.created_at DESC", (uid,))]
    con.close()
    # Zeitleiste: alles mit Zeitstempel in einer Liste, neueste zuerst
    zeit = [{"am": u["created_at"], "art": "konto", "text": "Konto angelegt"}]
    if u.get("email_bestaetigt"):
        zeit.append({"am": u["email_bestaetigt"], "art": "konto", "text": "E-Mail bestätigt"})
    for b in bestellungen:
        if b["art"] == "kuendigung_angefragt":
            zeit.append({"am": b["created_at"], "art": "abo", "text": "Kündigung angefragt"})
        else:
            zeit.append({"am": b["created_at"], "art": "kauf", "text": f"{'Abo ' + b['art'].capitalize() if b['art'] != 'paket' else 'Paket ' + b['variante']} · {b['betrag']:.2f} {b['waehrung']} · {b['status']}"})
    for b in buchungen:
        zeit.append({"am": b["created_at"], "art": "credits", "text": f"{b['text']}{' · ' + b['titel'] if b['titel'] else ''}", "delta": b["delta"], "saldo": b["saldo_danach"]})
    for k in kunst:
        zeit.append({"am": k["created_at"], "art": "kunst", "text": f"Kunstseite {k['titel'] or 'Seite ' + str((k['seite'] or 0) + 1)} · {k['anker_n']} Karten · {k['status']} · {k['kosten_usd']:.3f} $",
                     "ref": k["id"]})
    for b in binder:
        zeit.append({"am": b["created_at"], "art": "binder", "text": f"Binder „{b['name']}“ angelegt ({b['layout']})"})
        if b.get("veroeffentlicht_at"):
            zeit.append({"am": b["veroeffentlicht_at"], "art": "vitrine", "text": f"Binder „{b['name']}“ veröffentlicht"})
    for s in sessions:
        zeit.append({"am": s, "art": "login", "text": "Anmeldung"})
    for f in uebernommen:
        zeit.append({"am": f["created_at"], "art": "kunst", "text": f"Fremde Kunstseite übernommen: {f['titel'] or f['id']} (von {f['von'] or '?'})"})
    zeit.sort(key=lambda z: z["am"] or "", reverse=True)
    return {"nutzer": u, "profil": dict(profil) if profil else None, "buchungen": buchungen, "bestellungen": bestellungen,
            "kunstseiten": kunst, "binder": binder, "uebernommen": uebernommen, "zeitleiste": zeit,
            "summen": {"umsatz": sum(b["betrag"] for b in bestellungen if b["status"] == "bezahlt"),
                       "ki_kosten": round(sum(k["kosten_usd"] or 0 for k in kunst), 3),
                       "credits_ausgegeben": -sum(b["delta"] for b in buchungen if b["delta"] < 0),
                       "credits_verdient": sum(b["delta"] for b in buchungen if b["grund"] == "artwork_anteil")}}


def register(app, *, get_db, env, admin_key, abo):
    _dep.update(get_db=get_db, env=env, admin_key=admin_key, abo=abo)

    @app.get("/api/admin/uebersicht")
    def admin_uebersicht(key: str = ""):
        _pruefen(key)
        return uebersicht()

    @app.get("/api/admin/nutzer")
    def admin_nutzer(key: str = ""):
        _pruefen(key)
        return nutzer_liste()

    @app.get("/api/admin/nutzer/{uid}")
    def admin_nutzer_detail(uid: int, key: str = ""):
        _pruefen(key)
        return nutzer_detail(uid)
