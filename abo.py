# Binderplan – Tarife, Credits und Zahlungen
#
# Ein Ort für alles Kommerzielle: Tarifdefinition, Credit-Konto, Stripe (Checkout für Abos
# und Credit-Pakete, Kundenportal, Webhook), Bestellprotokoll und die Endpunkte
# /api/tarife, /api/credits, /api/stripe/*. main.py hängt das Modul über register(app, …)
# ein, damit main.py nicht weiter wächst.
#
# Modell (seit 2026-08-29):
#   Gratis  – 3 Binder, 2 Platzhalter-PDFs/Monat, Preise 1×/Tag, 20 Start-Credits
#   Plus    – 3,99 €/Monat · 39,99 €/Jahr: alles unbegrenzt + 80 Credits/Monat
#   Pro     – 8,99 €/Monat · 89,99 €/Jahr: alles unbegrenzt + 250 Credits/Monat
#   Pakete  – 100 / 250 / 600 Credits einmalig, ohne Verfall
#
# Credits bilden ausschließlich die echten KI-Kosten ab. Alles, was nur Rechenzeit kostet
# (Planen, Checkliste, Drucken), gehört zum Tarif und ist nicht creditpflichtig.
# Die Kosten einer Artwork-Seite hängen an der Zahl der Ankerkarten (je Karte eine weitere
# Analyse und ein weiteres Bild im Prompt), deshalb ist der Creditpreis gestaffelt.

import hashlib
import hmac
import json
import time
from datetime import datetime

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

STRIPE_API = "https://api.stripe.com/v1"

# --- Creditpreise ---------------------------------------------------------------
# 1 Credit ≈ 4–5 ct Verkaufswert. Eine Artwork-Seite kostet den Betrieb 0,11 € (eine Karte)
# bis 0,28 € (24 Karten auf 5×5). Der Deckel hält große Raster für Sammler bezahlbar.
ARTWORK_BASIS = 10          # bis zwei Ankerkarten
ARTWORK_JE_KARTE = 2        # je weiterer Ankerkarte
ARTWORK_MAX = 30            # Obergrenze je Seite
ARTWORK_4K_FAKTOR = 1.6     # Druckauflösung kostet das Modell entsprechend mehr

ARTWORK_FREMD = 6           # fremde, veröffentlichte Artwork-Seite drucken

START_CREDITS = 20          # Willkommensguthaben je neuem Konto (= 2 Artwork-Seiten)

# Eine fremde Seite zu drucken kostet uns nichts — sie ist schon erzeugt. Der Preis
# liegt trotzdem nicht bei null: sonst lohnt es sich, eine Seite einmal erzeugen zu
# lassen und sie über die Vitrine an alle zu verteilen. Sechs statt zehn Credits.


def artwork_preis(anker_anzahl: int, groesse: str = "2K") -> int:
    """Credits für eine Artwork-Seite – gestaffelt nach Ankerkarten, weil die Modellkosten
    mit jeder Karte steigen (zusätzliche Analyse + Bild-Upload im Prompt)."""
    credits = ARTWORK_BASIS + max(0, int(anker_anzahl) - 2) * ARTWORK_JE_KARTE
    credits = min(credits, ARTWORK_MAX)
    if groesse == "4K":
        credits = int(round(credits * ARTWORK_4K_FAKTOR))
    return credits


# --- Tarife ---------------------------------------------------------------------
TARIFE = {
    "free": {
        "name": "Gratis", "credits": 0, "binder": 3, "exporte": 2,
        "preise_live": False, "kaufliste": False,
    },
    "plus": {
        "name": "Plus", "credits": 80, "binder": None, "exporte": None,
        "preise_live": True, "kaufliste": True,
        "preis_monat": 3.99, "preis_jahr": 39.99,
    },
    "pro": {
        "name": "Pro", "credits": 200, "binder": None, "exporte": None,
        "preise_live": True, "kaufliste": True,
        "preis_monat": 7.99, "preis_jahr": 79.99,
    },
    # Altbestand: Betreiberkonto und eventuelle Lifetime-Käufe der Vorversion. Wird nicht
    # mehr verkauft – unbegrenzte KI-Nutzung bei laufenden Modellkosten trägt sich nicht.
    "lifetime": {
        "name": "Lifetime", "credits": 250, "binder": None, "exporte": None,
        "preise_live": True, "kaufliste": True, "versteckt": True,
    },
}
STAPEL_FAKTOR = 2           # ungenutzte Abo-Credits sammeln sich bis max. 2 Monatsmengen

PAKETE = {
    "p100": {"credits": 100, "preis": 4.99, "name": "100 Credits"},
    "p250": {"credits": 250, "preis": 10.99, "name": "250 Credits"},
    "p600": {"credits": 600, "preis": 23.99, "name": "600 Credits"},
}

# Stripe-Preis-IDs stehen in der .env (angelegt über stripe_setup.py)
PREIS_ENV = {
    ("plus", "monat"): "STRIPE_PLUS_MONAT", ("plus", "jahr"): "STRIPE_PLUS_JAHR",
    ("pro", "monat"): "STRIPE_PRO_MONAT", ("pro", "jahr"): "STRIPE_PRO_JAHR",
    ("paket", "p100"): "STRIPE_PAKET_100", ("paket", "p250"): "STRIPE_PAKET_250",
    ("paket", "p600"): "STRIPE_PAKET_600",
}

# Wortlaut der Widerrufs-Zustimmung – wird je Bestellung mitprotokolliert (Nachweispflicht
# nach § 356 Abs. 5 BGB; ohne diese Erklärung erlischt das Widerrufsrecht nicht).
WIDERRUF_TEXT = (
    "Ich verlange ausdrücklich, dass Binderplan vor Ablauf der Widerrufsfrist mit der "
    "Leistung beginnt. Mir ist bekannt, dass ich mein Widerrufsrecht mit vollständiger "
    "Vertragserfüllung verliere."
)

_dep = {}


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _monat():
    return datetime.utcnow().strftime("%Y-%m")


# --- Tarif-Auskunft ---------------------------------------------------------------

def tarif(user):
    return TARIFE.get((user or {}).get("plan") or "free", TARIFE["free"])


def ist_bezahlt(user) -> bool:
    return (user or {}).get("plan") in ("plus", "pro", "lifetime")


def limit_binder(user):
    return tarif(user)["binder"]


def limit_exporte(user):
    return tarif(user)["exporte"]


def darf_kaufliste(user) -> bool:
    return tarif(user)["kaufliste"]


def darf_preise_live(user) -> bool:
    return tarif(user)["preise_live"]


# --- Credit-Konto -----------------------------------------------------------------
#
# Zwei Töpfe: `credits_abo` wird monatlich ersetzt (mit Stapel-Obergrenze), `credits` sind
# gekaufte bzw. geschenkte Credits ohne Verfall. Verbraucht wird zuerst das Abo-Guthaben,
# damit gekaufte Credits möglichst lange erhalten bleiben.

def saldo(user) -> int:
    return int((user or {}).get("credits") or 0) + int((user or {}).get("credits_abo") or 0)


def _user_frisch(con, user_id):
    row = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def _buchen(con, user_id, delta_abo, delta_kauf, grund, ref=""):
    con.execute("UPDATE users SET credits_abo = MAX(0, COALESCE(credits_abo,0) + ?),"
                " credits = MAX(0, COALESCE(credits,0) + ?) WHERE id = ?",
                (delta_abo, delta_kauf, user_id))
    u = _user_frisch(con, user_id)
    con.execute("INSERT INTO credit_buchungen (user_id, delta, grund, ref, saldo_danach, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (user_id, delta_abo + delta_kauf, grund, ref, saldo(u), _now()))
    return u


def gutschrift(user_id, menge, grund, ref="", auf_abo=False):
    con = _dep["get_db"]()
    u = _buchen(con, user_id, menge if auf_abo else 0, 0 if auf_abo else menge, grund, ref)
    con.commit()
    con.close()
    return saldo(u)


def abbuchen(user, menge, grund, ref=""):
    """Credits abbuchen – erst Abo-Guthaben, dann gekaufte. 402, wenn es nicht reicht.
    Liest den Kontostand frisch aus der DB (der Request-Snapshot kann veraltet sein)."""
    con = _dep["get_db"]()
    u = _user_frisch(con, user["id"])
    if not u or saldo(u) < menge:
        stand = saldo(u) if u else 0
        con.close()
        raise HTTPException(402, detail={"code": "keine_credits", "benoetigt": menge, "saldo": stand})
    aus_abo = min(int(u.get("credits_abo") or 0), menge)
    u = _buchen(con, user["id"], -aus_abo, -(menge - aus_abo), grund, ref)
    con.commit()
    con.close()
    return saldo(u)


def auffrischen(user, con=None):
    """Monatliches Abo-Guthaben nachlegen, wenn eine neue Periode begonnen hat. Läuft über den
    Stripe-Webhook (invoice.paid) und zusätzlich faul beim Abruf, damit ein verpasstes Event
    niemanden ohne Guthaben zurücklässt."""
    if not user or not ist_bezahlt(user):
        return user
    menge = tarif(user)["credits"]
    if not menge or (user.get("credits_periode") or "") == _monat():
        return user
    eigen = con is None
    con = con or _dep["get_db"]()
    frisch = _user_frisch(con, user["id"])
    if frisch and (frisch.get("credits_periode") or "") != _monat():
        # Reste bleiben stehen, aber gedeckelt – sonst sammeln Karteileichen unbegrenzt an
        rest = min(int(frisch.get("credits_abo") or 0), menge * (STAPEL_FAKTOR - 1))
        con.execute("UPDATE users SET credits_abo = ?, credits_periode = ? WHERE id = ?",
                    (rest + menge, _monat(), user["id"]))
        con.execute("INSERT INTO credit_buchungen (user_id, delta, grund, ref, saldo_danach, created_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (user["id"], menge, "abo_guthaben", _monat(),
                     rest + menge + int(frisch.get("credits") or 0), _now()))
        con.commit()
        frisch = _user_frisch(con, user["id"])
    if eigen:
        con.close()
    return frisch or user


def konto_info(user):
    """Tarif- und Guthabenangaben für das Frontend."""
    t = tarif(user)
    return {
        "plan": user.get("plan") or "free",
        "plan_name": t["name"],
        "credits": saldo(user),
        "credits_abo": int(user.get("credits_abo") or 0),
        "credits_gekauft": int(user.get("credits") or 0),
        "credits_monat": t["credits"],
        "abo_bis": (user.get("abo_bis") or "")[:10],
        "abo_kuendigt": bool(user.get("abo_kuendigt")),
        "abo_intervall": user.get("abo_intervall") or "",
        "abo_status": user.get("abo_status") or "",
    }


# --- Stripe -------------------------------------------------------------------------

def _key():
    key = _dep["env"]().get("STRIPE_SECRET_KEY", "")
    if not key:
        raise HTTPException(503, "Zahlungen sind gerade nicht eingerichtet")
    return key


def _flach(prefix, wert, out):
    """Stripe erwartet Formulardaten mit verschachtelten Schlüsseln: a[b][0][c]=…"""
    if isinstance(wert, dict):
        for k, v in wert.items():
            _flach(f"{prefix}[{k}]" if prefix else k, v, out)
    elif isinstance(wert, (list, tuple)):
        for i, v in enumerate(wert):
            _flach(f"{prefix}[{i}]", v, out)
    elif wert is not None:
        out[prefix] = "true" if wert is True else ("false" if wert is False else str(wert))
    return out


def _stripe(pfad, daten=None, methode="POST"):
    r = httpx.request(methode, f"{STRIPE_API}/{pfad}", data=_flach("", daten or {}, {}),
                      auth=(_key(), ""), timeout=30)
    d = r.json()
    if r.status_code >= 400:
        raise HTTPException(502, f"Stripe: {(d.get('error') or {}).get('message') or r.status_code}")
    return d


def _preis_id(art, variante):
    pid = _dep["env"]().get(PREIS_ENV.get((art, variante), ""), "")
    if not pid:
        raise HTTPException(503, f"Für {art}/{variante} ist kein Stripe-Preis hinterlegt")
    return pid


def _app_url():
    return (_dep["env"]().get("APP_URL") or "https://binderplan.app").rstrip("/")


def _kunde(con, user):
    if user.get("stripe_customer"):
        return user["stripe_customer"]
    d = _stripe("customers", {"email": user["email"], "metadata": {"binderplan_user": user["id"]}})
    con.execute("UPDATE users SET stripe_customer = ? WHERE id = ?", (d["id"], user["id"]))
    con.commit()
    return d["id"]


def _plan_aus_preis(preis_id):
    env = _dep["env"]()
    for (art, variante), key in PREIS_ENV.items():
        if art != "paket" and env.get(key) == preis_id:
            return art, variante
    return None, None


def _ts(wert):
    try:
        return datetime.utcfromtimestamp(int(wert)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def _abo_aus_stripe(con, user_id, sub):
    """Abo-Zustand aus einem Stripe-Subscription-Objekt in die DB übernehmen."""
    status = sub.get("status")
    preis = (((sub.get("items") or {}).get("data") or [{}])[0].get("price") or {})
    plan, intervall = _plan_aus_preis(preis.get("id"))
    aktiv = status in ("active", "trialing")
    con.execute(
        "UPDATE users SET plan=?, stripe_sub=?, abo_status=?, abo_bis=?, abo_kuendigt=?, abo_intervall=?"
        " WHERE id=?",
        (plan if (aktiv and plan) else "free", sub.get("id"), status or "",
         _ts(sub.get("current_period_end")), 1 if sub.get("cancel_at_period_end") else 0,
         intervall or "", user_id))
    con.commit()
    return plan if aktiv else "free"


def _user_zu_sub(con, sub):
    """Nutzer zu einer Subscription finden – über gespeicherte Sub-ID oder Stripe-Customer."""
    row = con.execute("SELECT * FROM users WHERE stripe_sub = ?", (sub.get("id"),)).fetchone()
    if not row and sub.get("customer"):
        row = con.execute("SELECT * FROM users WHERE stripe_customer = ?", (sub["customer"],)).fetchone()
    return dict(row) if row else None


def _signatur_ok(roh: bytes, sig_header: str, geheim: str) -> bool:
    """Stripe-Signatur prüfen (t=…,v1=…) – ohne die stripe-Bibliothek."""
    teile = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
    t, v1 = teile.get("t"), teile.get("v1")
    if not t or not v1:
        return False
    erwartet = hmac.new(geheim.encode(), f"{t}.".encode() + roh, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(erwartet, v1):
        return False
    try:   # Replay-Schutz: Events älter als 5 Minuten ablehnen.
        # time.time() statt utcnow().timestamp() — letzteres deutet die naive UTC-Zeit als
        # Ortszeit und liegt auf diesem Server (CEST) um zwei Stunden daneben.
        return abs(time.time() - int(t)) < 300
    except Exception:
        return False


def register(app, *, get_db, current_user, require_user, env, mail_senden, mail_konfiguriert, basis):
    _dep.update(get_db=get_db, current_user=current_user, require_user=require_user, env=env,
                mail_senden=mail_senden, mail_konfiguriert=mail_konfiguriert, basis=basis)

    con = get_db()
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS credit_buchungen (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            delta INTEGER NOT NULL, grund TEXT, ref TEXT, saldo_danach INTEGER,
            created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_credit_user ON credit_buchungen(user_id, id DESC);
        CREATE TABLE IF NOT EXISTS bestellungen (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            art TEXT, variante TEXT, betrag REAL, waehrung TEXT, session_id TEXT,
            widerruf_text TEXT, zustimmung_am TEXT, status TEXT, created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_bestellung_user ON bestellungen(user_id, id DESC);
        CREATE TABLE IF NOT EXISTS stripe_events (
            id TEXT PRIMARY KEY, typ TEXT, verarbeitet_am TEXT
        );
        """
    )
    for alter in ("ALTER TABLE users ADD COLUMN credits INTEGER DEFAULT 0",
                  "ALTER TABLE users ADD COLUMN credits_abo INTEGER DEFAULT 0",
                  "ALTER TABLE users ADD COLUMN credits_periode TEXT",
                  "ALTER TABLE users ADD COLUMN abo_status TEXT",
                  "ALTER TABLE users ADD COLUMN abo_bis TEXT",
                  "ALTER TABLE users ADD COLUMN abo_kuendigt INTEGER DEFAULT 0",
                  "ALTER TABLE users ADD COLUMN abo_intervall TEXT"):
        try:
            con.execute(alter)
        except Exception:
            pass
    con.commit()
    con.close()

    # --- Tarif-Übersicht: eine Quelle für Frontend, Landingpage und Rechtstexte ---
    @app.get("/api/tarife")
    def tarife(request: Request):
        user = current_user(request)
        if user:
            user = auffrischen(user)
        return {
            "tarife": [
                {"id": k, "name": v["name"], "credits": v["credits"], "binder": v["binder"],
                 "exporte": v["exporte"], "kaufliste": v["kaufliste"], "preise_live": v["preise_live"],
                 "preis_monat": v.get("preis_monat"), "preis_jahr": v.get("preis_jahr")}
                for k, v in TARIFE.items() if not v.get("versteckt")
            ],
            "pakete": [{"id": k, **v} for k, v in PAKETE.items()],
            "artwork": {"basis": ARTWORK_BASIS, "je_karte": ARTWORK_JE_KARTE, "max": ARTWORK_MAX,
                        "faktor_4k": ARTWORK_4K_FAKTOR},
            "start_credits": START_CREDITS,
            "widerruf_text": WIDERRUF_TEXT,
            "konto": konto_info(user) if user else None,
            "zahlbar": bool(env().get("STRIPE_SECRET_KEY")),
        }

    @app.get("/api/credits")
    def credits_stand(request: Request):
        user = auffrischen(require_user(request))
        con = get_db()
        rows = con.execute(
            "SELECT delta, grund, ref, saldo_danach, created_at FROM credit_buchungen"
            " WHERE user_id = ? ORDER BY id DESC LIMIT 50", (user["id"],)).fetchall()
        con.close()
        return {"konto": konto_info(user), "buchungen": [dict(r) for r in rows]}

    # --- Checkout ---------------------------------------------------------------
    @app.post("/api/stripe/checkout")
    async def checkout(request: Request):
        """Erzeugt die Stripe-Sitzung. Die Zustimmung zum sofortigen Leistungsbeginn ist
        Pflicht (§ 356 Abs. 5 BGB) und wird mit Zeitstempel und Wortlaut protokolliert."""
        user = require_user(request)
        data = await request.json()
        art = data.get("art")            # 'plus' | 'pro' | 'paket'
        variante = data.get("variante")  # 'monat' | 'jahr' | 'p100' | 'p250' | 'p600'
        if not data.get("agb") or not data.get("widerruf"):
            raise HTTPException(400, detail={"code": "zustimmung_fehlt"})
        if art == "paket":
            if variante not in PAKETE:
                raise HTTPException(400, "Unbekanntes Paket")
            betrag, modus = PAKETE[variante]["preis"], "payment"
        elif art in ("plus", "pro"):
            if variante not in ("monat", "jahr"):
                raise HTTPException(400, "Unbekannte Laufzeit")
            betrag, modus = TARIFE[art][f"preis_{variante}"], "subscription"
        else:
            raise HTTPException(400, "Unbekannter Tarif")

        con = get_db()
        kunde = _kunde(con, user)
        basis_url = _app_url()
        daten = {
            "mode": modus,
            "customer": kunde,
            "client_reference_id": str(user["id"]),
            "line_items": [{"price": _preis_id("paket" if art == "paket" else art, variante),
                            "quantity": 1}],
            "success_url": f"{basis_url}/app?zahlung=ok&sitzung={{CHECKOUT_SESSION_ID}}",
            "cancel_url": f"{basis_url}/app?zahlung=abbruch",
            "allow_promotion_codes": True,
            "locale": "de",
            "metadata": {"art": art, "variante": variante, "user": str(user["id"])},
        }
        if modus == "payment":
            # Beleg für Einmalkäufe – ohne das erzeugt Stripe keine Rechnung
            daten["invoice_creation"] = {
                "enabled": True,
                "invoice_data": {"footer": "Gemäß § 19 UStG wird keine Umsatzsteuer erhoben."},
            }
            daten["payment_intent_data"] = {"metadata": {"art": art, "variante": variante,
                                                         "user": str(user["id"])}}
        else:
            daten["subscription_data"] = {"metadata": {"art": art, "variante": variante,
                                                       "user": str(user["id"])}}
        sitzung = _stripe("checkout/sessions", daten)
        con.execute(
            "INSERT INTO bestellungen (user_id, art, variante, betrag, waehrung, session_id,"
            " widerruf_text, zustimmung_am, status, created_at) VALUES (?,?,?,?,'EUR',?,?,?,?,?)",
            (user["id"], art, variante, betrag, sitzung["id"], WIDERRUF_TEXT, _now(), "offen", _now()))
        con.commit()
        con.close()
        return {"url": sitzung["url"]}

    @app.post("/api/stripe/portal")
    def portal(request: Request):
        user = require_user(request)
        if not user.get("stripe_customer"):
            raise HTTPException(400, "Für dieses Konto gibt es noch keine Zahlung.")
        d = _stripe("billing_portal/sessions",
                    {"customer": user["stripe_customer"], "return_url": f"{_app_url()}/app"})
        return {"url": d["url"]}

    @app.post("/api/abo/kuendigen")
    def kuendigen(request: Request):
        """Kündigung zum Laufzeitende, direkt aus dem Konto heraus."""
        user = require_user(request)
        if not user.get("stripe_sub"):
            raise HTTPException(400, detail={"code": "kein_abo"})
        sub = _stripe(f"subscriptions/{user['stripe_sub']}", {"cancel_at_period_end": True})
        bis = _ts(sub.get("current_period_end"))
        con = get_db()
        con.execute("UPDATE users SET abo_kuendigt = 1, abo_bis = ? WHERE id = ?", (bis, user["id"]))
        con.commit()
        con.close()
        _kuendigung_mail(user["email"], bis[:10])
        return {"ok": True, "bis": bis[:10], "mail": mail_konfiguriert()}

    @app.post("/api/abo/reaktivieren")
    def reaktivieren(request: Request):
        user = require_user(request)
        if not user.get("stripe_sub"):
            raise HTTPException(400, detail={"code": "kein_abo"})
        _stripe(f"subscriptions/{user['stripe_sub']}", {"cancel_at_period_end": False})
        con = get_db()
        con.execute("UPDATE users SET abo_kuendigt = 0 WHERE id = ?", (user["id"],))
        con.commit()
        con.close()
        return {"ok": True}

    def _kuendigung_mail(email, bis):
        if not mail_konfiguriert():
            return
        try:
            mail_senden(email, "Deine Kündigung bei Binderplan",
                        f"Hallo,\n\nwir haben deine Kündigung erhalten und zum {bis} vorgemerkt. "
                        f"Bis dahin kannst du alles weiter nutzen, danach läuft dein Konto kostenlos "
                        f"weiter. Es wird nichts mehr abgebucht.\n\nGekaufte Credits bleiben "
                        f"erhalten.\n\nViele Grüße\nBinderplan")
        except Exception:
            pass

    # --- Webhook -----------------------------------------------------------------
    def _kaufbestaetigung(user, art, variante, obj):
        """Bestätigung in Textform – Pflicht nach § 312f BGB, mit Widerrufsbelehrung."""
        if not user or not mail_konfiguriert():
            return
        basis_url = _app_url()
        if art == "paket":
            was = f"{PAKETE.get(variante, {}).get('name', variante)} (einmalig)"
        else:
            was = (f"Binderplan {TARIFE.get(art, {}).get('name', art)} – "
                   f"{'monatlich' if variante == 'monat' else 'jährlich'}")
        betrag = (obj.get("amount_total") or 0) / 100
        try:
            mail_senden(
                user["email"], "Deine Bestellung bei Binderplan",
                f"Hallo,\n\nvielen Dank für deine Bestellung.\n\n"
                f"Leistung: {was}\nBetrag: {betrag:.2f} €\n"
                f"Gemäß § 19 UStG wird keine Umsatzsteuer erhoben.\n\n"
                f"Du hast beim Kauf bestätigt:\n„{WIDERRUF_TEXT}“\n\n"
                f"Widerrufsbelehrung, AGB und Datenschutzerklärung: {basis_url}/recht\n"
                f"Kündigen kannst du jederzeit hier: {basis_url}/kuendigen\n\n"
                f"Dein Konto: {basis_url}/app\n\nViele Grüße\nBinderplan")
        except Exception:
            pass

    def _checkout_fertig(con, obj):
        """Bezahlte Sitzung verbuchen: Abo freischalten oder Credits gutschreiben.
        Erst bei tatsächlich bezahltem Status – verzögerte Zahlarten melden `unpaid`
        bzw. `processing` und dürfen noch nichts freischalten."""
        if obj.get("payment_status") not in ("paid", "no_payment_required"):
            return
        user_id = obj.get("client_reference_id")
        if not user_id:
            return
        user = _user_frisch(con, int(user_id))
        if not user:
            return
        con.execute("UPDATE bestellungen SET status='bezahlt' WHERE session_id=?", (obj.get("id"),))
        con.commit()
        meta = obj.get("metadata") or {}
        art, variante = meta.get("art"), meta.get("variante")
        if obj.get("mode") == "subscription" and obj.get("subscription"):
            sub = _stripe(f"subscriptions/{obj['subscription']}", methode="GET")
            plan = _abo_aus_stripe(con, user["id"], sub)
            if plan and plan != "free":
                con.execute("UPDATE users SET credits_periode = '' WHERE id = ?", (user["id"],))
                con.commit()
                auffrischen(_user_frisch(con, user["id"]), con)
        elif art == "paket" and variante in PAKETE:
            _buchen(con, user["id"], 0, PAKETE[variante]["credits"], "kauf", variante)
            con.commit()
        _kaufbestaetigung(_user_frisch(con, user["id"]), art, variante, obj)

    def _erstattung(con, obj):
        """Rückerstattung: gekaufte Credits wieder abziehen, soweit noch vorhanden."""
        meta = obj.get("metadata") or {}
        art, variante, user_id = meta.get("art"), meta.get("variante"), meta.get("user")
        if art == "paket" and user_id and variante in PAKETE:
            menge = PAKETE[variante]["credits"]
            con.execute("UPDATE users SET credits = MAX(0, COALESCE(credits,0) - ?) WHERE id = ?",
                        (menge, int(user_id)))
            u = _user_frisch(con, int(user_id))
            con.execute("INSERT INTO credit_buchungen (user_id, delta, grund, ref, saldo_danach,"
                        " created_at) VALUES (?,?,?,?,?,?)",
                        (int(user_id), -menge, "erstattung", variante, saldo(u), _now()))
            con.commit()

    @app.post("/api/stripe/webhook")
    async def webhook(request: Request):
        roh = await request.body()
        geheim = env().get("STRIPE_WEBHOOK_SECRET", "")
        if geheim and not _signatur_ok(roh, request.headers.get("stripe-signature", ""), geheim):
            raise HTTPException(400, "Signatur ungültig")
        ereignis = json.loads(roh.decode())
        typ, obj = ereignis.get("type"), (ereignis.get("data") or {}).get("object") or {}

        con = get_db()
        # Idempotenz: Stripe stellt Events mehrfach zu – ohne Sperre gäbe es Doppelbuchungen
        try:
            con.execute("INSERT INTO stripe_events (id, typ, verarbeitet_am) VALUES (?,?,?)",
                        (ereignis.get("id"), typ, _now()))
            con.commit()
        except Exception:
            con.close()
            return {"ok": True, "doppelt": True}

        try:
            if typ in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
                _checkout_fertig(con, obj)
            elif typ == "checkout.session.async_payment_failed":
                con.execute("UPDATE bestellungen SET status='fehlgeschlagen' WHERE session_id=?",
                            (obj.get("id"),))
                con.commit()
            elif typ in ("customer.subscription.updated", "customer.subscription.created"):
                user = _user_zu_sub(con, obj)
                if user and user.get("plan") != "lifetime":
                    _abo_aus_stripe(con, user["id"], obj)
            elif typ == "customer.subscription.deleted":
                user = _user_zu_sub(con, obj)
                if user and user.get("plan") != "lifetime":
                    con.execute("UPDATE users SET plan='free', stripe_sub=NULL, abo_status='beendet',"
                                " abo_kuendigt=0 WHERE id=?", (user["id"],))
                    con.commit()
            elif typ == "invoice.paid":
                # Verlängerung: neues Monatsguthaben, sobald die Rechnung bezahlt ist
                if obj.get("subscription"):
                    row = con.execute("SELECT * FROM users WHERE stripe_sub = ?",
                                      (obj["subscription"],)).fetchone()
                    user = dict(row) if row else None
                    if user and ist_bezahlt(user):
                        con.execute("UPDATE users SET credits_periode = '', abo_status='active'"
                                    " WHERE id = ?", (user["id"],))
                        con.commit()
                        auffrischen(_user_frisch(con, user["id"]), con)
            elif typ == "invoice.payment_failed":
                if obj.get("subscription"):
                    con.execute("UPDATE users SET abo_status='zahlung_offen' WHERE stripe_sub=?",
                                (obj["subscription"],))
                    con.commit()
            elif typ == "charge.refunded":
                _erstattung(con, obj)
        finally:
            con.close()
        return {"ok": True}

    # --- Öffentliche Kündigungsseite (§ 312k BGB: ohne Login erreichbar) ----------
    @app.get("/kuendigen")
    def kuendigen_seite():
        pfad = basis / "kuendigen.html"
        if not pfad.exists():
            raise HTTPException(404, "Seite nicht gefunden")
        return HTMLResponse(pfad.read_text(encoding="utf-8"))

    @app.post("/api/kuendigung")
    async def kuendigung_formular(request: Request):
        """Kündigungsformular ohne Login: identifiziert über die E-Mail, kündigt zum
        Laufzeitende und bestätigt in Textform. Ohne passendes Abo wird trotzdem freundlich
        geantwortet, damit das Formular nicht verrät, welche Konten es gibt."""
        data = await request.json()
        email = str(data.get("email") or "").strip().lower()[:200]
        notiz = str(data.get("notiz") or "")[:500]
        con = get_db()
        row = con.execute("SELECT * FROM users WHERE lower(email) = ?", (email,)).fetchone()
        user = dict(row) if row else None
        gekuendigt, bis = False, ""
        if user and user.get("stripe_sub"):
            try:
                sub = _stripe(f"subscriptions/{user['stripe_sub']}", {"cancel_at_period_end": True})
                bis = _ts(sub.get("current_period_end"))
                con.execute("UPDATE users SET abo_kuendigt = 1, abo_bis = ? WHERE id = ?",
                            (bis, user["id"]))
                con.commit()
                gekuendigt = True
            except Exception:
                pass
        con.execute("INSERT INTO bestellungen (user_id, art, variante, betrag, waehrung, session_id,"
                    " widerruf_text, zustimmung_am, status, created_at)"
                    " VALUES (?,?,?,?,'EUR',?,?,?,?,?)",
                    (user["id"] if user else 0, "kuendigung", email, 0, "", notiz, _now(),
                     "bestaetigt" if gekuendigt else "kein_abo", _now()))
        con.commit()
        con.close()
        if gekuendigt:
            _kuendigung_mail(email, bis[:10])
        return {"ok": True, "gekuendigt": gekuendigt, "bis": bis[:10], "mail": mail_konfiguriert()}

    return {"tarif": tarif, "konto_info": konto_info}
