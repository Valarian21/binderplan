"""Binderplan – Konten, Sitzungen, Limits, E-Mail-Versand.

Ein Abschnitt der main.py in eigener Datei (Phase 6, 10.09.2026). main.py führt ihn an der
Stelle aus, an der er vorher stand (`_abschnitt("auth")`) – im selben Namensraum, mit
denselben Helfern, in derselben Reihenfolge. Es ist also kein importierbares Modul: neue
Funktionen hier sind in main.py sofort sichtbar, und umgekehrt. scripts/pruefen.py prüft alle
Dateien zusammen (pyflakes kennt die Namen der anderen Abschnitte)."""

# --- Konten, Limits & Abos --------------------------------------------------
#
# Die Tarife stehen in abo.py (Gratis / Plus / Pro) und werden von dort gelesen —
# hier gibt es bewusst keine zweite Wahrheit mehr. Gratis: 3 Binder, 2 Karten-PDFs
# im Monat, Preis-Abruf 1x/Tag. Plus/Pro: alles unbegrenzt + Kaufliste + monatliche
# Credits fürs KI-Artwork. Checklisten-PDF (ohne Kartenbilder) zählt bewusst nicht
# als Export. Anonyme Binder (user_id NULL) bleiben frei planbar — das Gate sitzt
# am PDF-Export.

import hashlib  # noqa: E402

import abo  # noqa: E402

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Einfache Drossel je IP: Registrierung und Anmeldung sind teuer (PBKDF2 mit 120.000 Runden)
# und jedes neue Konto bringt Start-Credits — ohne Bremse ließe sich beides ausnutzen.
_versuche = {}
LIMITS = {"register": (5, 3600), "login": (12, 900), "reset": (5, 3600),
          "import": (20, 600), "melden": (10, 3600), "kuendigung": (5, 3600)}


def client_ip(request: Request) -> str:
    """Adresse des Besuchers hinter nginx. nginx setzt X-Real-IP; X-Forwarded-For wird bewusst
    NICHT gelesen – den Header kann der Client selbst mitschicken und damit jede Drossel umgehen."""
    return (request.headers.get("x-real-ip", "").strip()
            or (request.client.host if request.client else "?"))


def _drossel(request: Request, was: str):
    grenze, fenster = LIMITS[was]
    ip = client_ip(request)
    jetzt = time.time()
    schluessel = f"{was}:{ip}"
    treffer = [t for t in _versuche.get(schluessel, []) if jetzt - t < fenster]
    if len(treffer) >= grenze:
        raise HTTPException(429, "Zu viele Versuche. Bitte warte einen Moment.")
    treffer.append(jetzt)
    _versuche[schluessel] = treffer
    if len(_versuche) > 5000:      # Speicher begrenzen
        for k in [k for k, v in _versuche.items() if not [t for t in v if jetzt - t < 3600]][:2000]:
            _versuche.pop(k, None)


def _env():
    text = (BASE / ".env").read_text() if (BASE / ".env").exists() else ""
    out = {}
    for line in text.splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _spalten_nachruesten():
    """E-Mail-Bestätigung: Zeitpunkt, Einmal-Token und dessen Ablauf. Additiv, damit
    bestehende Konten unberührt bleiben."""
    con = get_db()
    for befehl in ("ALTER TABLE users ADD COLUMN email_bestaetigt TEXT",
                   "ALTER TABLE users ADD COLUMN bestaetigung_token TEXT",
                   "ALTER TABLE users ADD COLUMN bestaetigung_bis TEXT",
                   "ALTER TABLE users ADD COLUMN start_credits_am TEXT",
                   "ALTER TABLE users ADD COLUMN pw_geaendert_am TEXT"):
        try:
            con.execute(befehl)
        except Exception:
            pass
    con.commit()
    con.close()


_spalten_nachruesten()


def _bestaetigt(user) -> bool:
    """Ohne eingerichteten Mailversand kann niemand bestätigen — dann gilt jedes Konto
    als bestätigt. Sobald SMTP in der .env steht, greift die Pflicht von selbst."""
    if not _mail_konfiguriert():
        return True
    return bool((user or {}).get("email_bestaetigt"))


def _bestaetigungsmail(con, user_id, email) -> bool:
    token = secrets.token_urlsafe(24)
    con.execute("UPDATE users SET bestaetigung_token = ?, bestaetigung_bis = datetime('now', '+7 days')"
                " WHERE id = ?", (token, user_id))
    con.commit()
    app_url = _env().get("APP_URL", "https://binderplan.app")
    # Nebenläufig: ein langsamer oder toter Mailserver darf die Registrierung nicht
    # um sein 20-Sekunden-Zeitlimit verzögern.
    return _mail_nebenbei(
        email, "Binderplan – bitte bestätige deine E-Mail",
        "Hallo,\n\n"
        "willkommen bei Binderplan! Bitte bestätige einmal kurz deine E-Mail-Adresse — "
        "danach schreiben wir dir dein Startguthaben gut:\n\n"
        f"{app_url}/app?bestaetigen={token}\n\n"
        "Der Link ist sieben Tage gültig. Wenn du dich nicht angemeldet hast, "
        "kannst du diese E-Mail einfach ignorieren.\n\n"
        "Viele Grüße\nBinderplan",
    )


def _hash_pw(pw: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 120_000).hex()


# Der Cookie existiert nur, damit <img src="…">-Tags Bilder laden können — dort lässt sich
# kein Authorization-Kopf setzen. Für alles andere zählt allein der Bearer-Token: sonst genügt
# ein Link auf /api/binders/<id>/pdf, um beim Opfer einen Monats-Export und Credits zu verbrauchen.
# Bild-Adressen, die der Browser ohne Bearer-Header lädt (<img>): dort zählt der Cookie.
_COOKIE_PFADE = ("/api/artwork/", "/api/binders/")


def _current_user(request: Request):
    token = ""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token and any(request.url.path.startswith(p) for p in _COOKIE_PFADE):
        token = request.cookies.get("bp_token", "")
    if not token:
        return None
    con = get_db()
    row = con.execute(
        "SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id WHERE sessions.token = ?",
        (token,),
    ).fetchone()
    con.close()
    return dict(row) if row else None


def _require_user(request: Request):
    user = _current_user(request)
    if not user:
        raise HTTPException(401, detail={"code": "login"})
    return user


def _ist_pro(user) -> bool:
    """Bezahlter Tarif (Plus, Pro oder Lifetime-Altbestand)."""
    return abo.ist_bezahlt(user)


def _ist_pro_stufe(user) -> bool:
    """Nur Pro und der Lifetime-Altbestand — siehe abo.ist_pro."""
    return abo.ist_pro(user)


def _limit_binder(user):
    return abo.limit_binder(user)


def _limit_exporte(user):
    return abo.limit_exporte(user)


def _monat_key():
    from datetime import datetime as _dt
    return _dt.utcnow().strftime("%Y-%m")


def _heute():
    from datetime import datetime as _dt
    return _dt.utcnow().strftime("%Y-%m-%d")


def _exporte_benutzt(user) -> int:
    teil = (user.get("exports_monat") or "").split(":")
    return int(teil[1]) if len(teil) == 2 and teil[0] == _monat_key() else 0


def _user_info(user):
    """Kontostand fürs Frontend. Frischt nebenbei das monatliche Abo-Guthaben auf,
    damit ein verpasstes Stripe-Event niemanden ohne Credits zurücklässt."""
    user = abo.auffrischen(user) or user
    con = get_db()
    anzahl = con.execute("SELECT COUNT(*) c FROM binders WHERE user_id = ?", (user["id"],)).fetchone()["c"]
    con.close()
    return {
        "email": user["email"], "plan": user["plan"], "name": user.get("name") or "",
        "binder_anzahl": anzahl, "binder_limit": _limit_binder(user),
        "exporte_benutzt": _exporte_benutzt(user),
        "exporte_limit": _limit_exporte(user),
        "kaufliste": abo.darf_kaufliste(user),
        "stripe": bool(_env().get("STRIPE_SECRET_KEY")),
        "email_bestaetigt": _bestaetigt(user),
        "pw_geaendert_am": (user.get("pw_geaendert_am") or "")[:10],
        "mail_moeglich": _mail_konfiguriert(),
        **abo.konto_info(user),
    }


def _neue_session(con, user_id) -> str:
    token = secrets.token_urlsafe(32)
    con.execute("INSERT INTO sessions (token, user_id) VALUES (?,?)", (token, user_id))
    return token


# --- E-Mail-Versand (SMTP aus .env: SMTP_HOST/PORT/USER/PASS/FROM) ----------

_mail_letzter_fehler = ""


def _mail_senden(an: str, betreff: str, text: str) -> bool:
    import smtplib
    from email.message import EmailMessage
    env = _env()
    host = env.get("SMTP_HOST")
    user = env.get("SMTP_USER")
    if not host or not user:
        return False
    msg = EmailMessage()
    msg["Subject"] = betreff
    msg["From"] = f'{env.get("SMTP_FROM_NAME", "Binderplan")} <{env.get("SMTP_FROM", user)}>'
    msg["To"] = an
    # Gesendet wird über das Postfach, das die Zugangsdaten hat; antworten sollen die Kunden
    # aber dorthin, wo die Weiterleitung hängt. Deshalb sind Absender und Antwortadresse getrennt.
    antwort = env.get("SMTP_REPLY_TO") or ""
    if antwort:
        msg["Reply-To"] = antwort
    msg.set_content(text)
    try:
        port = int(env.get("SMTP_PORT", "587") or 587)
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20) as s:
                s.login(user, env.get("SMTP_PASS", ""))
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.ehlo()
                s.starttls()
                s.login(user, env.get("SMTP_PASS", ""))
                s.send_message(msg)
        return True
    except Exception as e:
        global _mail_letzter_fehler
        _mail_letzter_fehler = f"{type(e).__name__}: {e}"[:300]
        _log("Mailversand fehlgeschlagen:", _mail_letzter_fehler)
        return False


def _mail_nebenbei(an, betreff, text) -> bool:
    """Mail im Hintergrund verschicken. Gibt zurück, ob es überhaupt versucht wird."""
    if not _mail_konfiguriert():
        return False
    threading.Thread(target=_mail_senden, args=(an, betreff, text), daemon=True).start()
    return True


def betreiber_melden(text: str) -> bool:
    """Kurze Nachricht an den Betreiber über denselben Telegram-Bot wie Jarvis.

    Zweiter Kanal neben der E-Mail: Solange kein Postfach zum Senden eingerichtet ist, wäre ein
    Kündigungswunsch sonst nur eine Zeile in der Datenbank, die niemand sieht. Läuft im
    Hintergrund und schlägt still fehl — eine Meldung darf nie einen Kundenvorgang aufhalten."""
    env = _env()
    token, chat = env.get("TELEGRAM_TOKEN"), env.get("TELEGRAM_CHAT")
    if not token or not chat:
        return False

    def senden():
        try:
            httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                       json={"chat_id": chat, "text": text[:3500],
                             "disable_web_page_preview": True}, timeout=15)
        except Exception as e:
            _log("Telegram-Meldung fehlgeschlagen:", e)

    threading.Thread(target=senden, daemon=True).start()
    return True


def _mail_konfiguriert() -> bool:
    """Erst wenn alle drei Angaben stehen, gilt der Versand als eingerichtet. Das Passwort
    gehört ausdrücklich dazu: mit Host und Benutzer allein würde die App bestätigte E-Mails
    verlangen, aber keine Bestätigungsmail zustellen können — niemand käme mehr ins Konto."""
    env = _env()
    return bool(env.get("SMTP_HOST") and env.get("SMTP_USER") and env.get("SMTP_PASS"))


@app.post("/api/auth/passwort_vergessen")
async def passwort_vergessen(request: Request):
    """Reset-Link mailen. Antwortet immer gleich, verrät also nicht, ob die E-Mail existiert."""
    _drossel(request, "reset")
    if not _mail_konfiguriert():
        raise HTTPException(503, detail={"code": "mail"})
    data = await request.json()
    email = str(data.get("email") or "").strip().lower()
    con = get_db()
    row = con.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if row:
        token = secrets.token_urlsafe(24)
        con.execute(
            "UPDATE users SET reset_token = ?, reset_bis = datetime('now', '+2 hours') WHERE id = ?",
            (token, row["id"]),
        )
        con.commit()
        app_url = _env().get("APP_URL", "https://binderplan.app")
        _mail_nebenbei(
            email, "Binderplan – Passwort zurücksetzen",
            "Hallo,\n\n"
            "für dein Binderplan-Konto wurde ein neues Passwort angefordert. "
            "Klicke auf diesen Link (2 Stunden gültig):\n\n"
            f"{app_url}/?reset={token}\n\n"
            "Wenn du das nicht warst, kannst du diese E-Mail einfach ignorieren.\n\n"
            "Viele Grüße\nBinderplan",
        )
    con.close()
    return {"ok": True}


@app.post("/api/auth/passwort_neu")
async def passwort_neu(request: Request):
    data = await request.json()
    token = str(data.get("token") or "")
    pw = str(data.get("passwort") or "")
    if len(pw) < 8:
        raise HTTPException(400, "Das Passwort braucht mindestens 8 Zeichen.")
    con = get_db()
    row = con.execute(
        "SELECT id FROM users WHERE reset_token = ? AND reset_token != ''"
        " AND reset_bis > datetime('now')", (token,),
    ).fetchone()
    if not row:
        con.close()
        raise HTTPException(400, "Der Link ist ungültig oder abgelaufen — bitte neu anfordern.")
    salt = secrets.token_hex(16)
    con.execute(
        "UPDATE users SET pw_hash = ?, salt = ?, reset_token = '', reset_bis = '',"
        " pw_geaendert_am = datetime('now') WHERE id = ?",
        (_hash_pw(pw, salt), salt, row["id"]),
    )
    con.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
    session = _neue_session(con, row["id"])
    con.commit()
    user = dict(con.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone())
    con.close()
    return {"token": session, "user": _user_info(user)}


@app.post("/api/auth/register")
async def auth_register(request: Request):
    _drossel(request, "register")
    data = await request.json()
    email = str(data.get("email") or "").strip().lower()
    pw = str(data.get("passwort") or "")
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, "Bitte eine gültige E-Mail-Adresse angeben.")
    if len(pw) < 8:
        raise HTTPException(400, "Das Passwort braucht mindestens 8 Zeichen.")
    # Geburtsdatum: gebraucht wird es erst beim Veröffentlichen in der Vitrine (ab 16,
    # Art. 8 DSGVO). Es hier zu fragen ist ehrlicher, als es später nachzufordern.
    geb = str(data.get("geburtsdatum") or "").strip()
    # date.fromisoformat statt Regex: „2000-99-99“ passt auf das Muster, ist aber kein Datum
    try:
        from datetime import date as _date
        geb_datum = _date.fromisoformat(geb)
    except ValueError:
        raise HTTPException(400, "Bitte gib dein Geburtsdatum an.")
    if geb_datum.year < 1900 or geb_datum > _date.today():
        raise HTTPException(400, "Dieses Geburtsdatum stimmt nicht.")
    salt = secrets.token_hex(16)
    pw_hash = await run_in_threadpool(_hash_pw, pw, salt)
    con = get_db()
    try:
        cur = con.execute(
            "INSERT INTO users (email, pw_hash, salt, geburtsdatum) VALUES (?,?,?,?)",
            (email, pw_hash, salt, geb),
        )
    except sqlite3.IntegrityError:
        con.close()
        raise HTTPException(400, "Für diese E-Mail gibt es schon ein Konto — bitte anmelden.")
    token = _neue_session(con, cur.lastrowid)
    # Das Willkommensguthaben gibt es erst nach bestätigter E-Mail. Ohne diesen Schritt
    # ist jede erfundene Adresse eine kostenlose Artwork-Seite.
    if _mail_konfiguriert():
        _bestaetigungsmail(con, cur.lastrowid, email)
    else:
        _start_credits_geben(con, cur.lastrowid)
    con.commit()
    user = dict(con.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone())
    con.close()
    return {"token": token, "user": _user_info(user)}


def _start_credits_geben(con, user_id):
    """Einmalig, egal wie oft der Bestätigungslink angeklickt wird."""
    row = con.execute("SELECT start_credits_am FROM users WHERE id = ?", (user_id,)).fetchone()
    if row and row["start_credits_am"]:
        return False
    con.execute("UPDATE users SET credits = COALESCE(credits,0) + ?, start_credits_am = datetime('now')"
                " WHERE id = ?", (abo.START_CREDITS, user_id))
    saldo = con.execute("SELECT COALESCE(credits,0) + COALESCE(credits_abo,0) s FROM users WHERE id = ?",
                        (user_id,)).fetchone()["s"]
    con.execute("INSERT INTO credit_buchungen (user_id, delta, grund, ref, saldo_danach, created_at)"
                " VALUES (?,?,?,?,?,datetime('now'))",
                (user_id, abo.START_CREDITS, "start", "", saldo))
    return True


@app.post("/api/auth/bestaetigen")
async def auth_bestaetigen(request: Request):
    """Bestätigungslink einlösen: E-Mail als geprüft markieren und Startguthaben gutschreiben."""
    data = await request.json()
    token = str(data.get("token") or "").strip()
    if not token:
        raise HTTPException(400, "Kein Bestätigungscode.")
    con = get_db()
    row = con.execute("SELECT id, email, email_bestaetigt FROM users"
                      " WHERE bestaetigung_token = ? AND bestaetigung_bis > datetime('now')",
                      (token,)).fetchone()
    if not row:
        con.close()
        raise HTTPException(400, detail={"code": "token", "text": "Dieser Bestätigungslink ist abgelaufen "
                                                                  "oder wurde schon benutzt."})
    con.execute("UPDATE users SET email_bestaetigt = COALESCE(email_bestaetigt, datetime('now')),"
                " bestaetigung_token = NULL WHERE id = ?", (row["id"],))
    neu = _start_credits_geben(con, row["id"])
    con.commit()
    user = dict(con.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone())
    sitzung = _neue_session(con, row["id"])
    con.commit()
    con.close()
    return {"ok": True, "credits_neu": neu, "token": sitzung, "user": _user_info(user)}


@app.post("/api/auth/bestaetigung_neu")
def auth_bestaetigung_neu(request: Request):
    """Bestätigungsmail noch einmal schicken."""
    user = _require_user(request)
    if _bestaetigt(user):
        return {"ok": True, "schon": True}
    _drossel(request, "reset")
    con = get_db()
    ok = _bestaetigungsmail(con, user["id"], user["email"])
    con.close()
    if not ok:
        raise HTTPException(503, detail={"code": "mail", "text": "Der Mailversand ist nicht eingerichtet."})
    return {"ok": True}


@app.post("/api/auth/login")
async def auth_login(request: Request):
    _drossel(request, "login")
    data = await request.json()
    email = str(data.get("email") or "").strip().lower()
    pw = str(data.get("passwort") or "")
    con = get_db()
    row = con.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    # PBKDF2 mit 120.000 Runden braucht ~75 ms CPU. Im Event-Loop stünde währenddessen der
    # ganze Dienst; im Threadpool läuft er weiter. Auch ohne Treffer wird gerechnet, sonst
    # verrät die Antwortzeit, welche Adressen es gibt.
    salt = row["salt"] if row else "00" * 16
    hash_neu = await run_in_threadpool(_hash_pw, pw, salt)
    if not row or hash_neu != row["pw_hash"]:
        con.close()
        raise HTTPException(401, "E-Mail oder Passwort stimmt nicht.")
    token = _neue_session(con, row["id"])
    con.commit()
    con.close()
    return {"token": token, "user": _user_info(dict(row))}


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    token = request.headers.get("authorization", "")[7:].strip() or request.cookies.get("bp_token", "")
    if token:
        con = get_db()
        con.execute("DELETE FROM sessions WHERE token = ?", (token,))
        con.commit()
        con.close()
    return {"ok": True}


@app.post("/api/auth/passwort_aendern")
async def passwort_aendern(request: Request):
    """Passwort im Profil ändern; meldet alle anderen Sitzungen ab."""
    user = _require_user(request)
    data = await request.json()
    alt = str(data.get("alt") or "")
    neu = str(data.get("neu") or "")
    if len(neu) < 8:
        raise HTTPException(400, "Das neue Passwort braucht mindestens 8 Zeichen.")
    if await run_in_threadpool(_hash_pw, alt, user["salt"]) != user["pw_hash"]:
        raise HTTPException(400, "Das aktuelle Passwort stimmt nicht.")
    salt = secrets.token_hex(16)
    hash_neu = await run_in_threadpool(_hash_pw, neu, salt)
    con = get_db()
    con.execute(
        "UPDATE users SET pw_hash = ?, salt = ?, pw_geaendert_am = datetime('now') WHERE id = ?",
        (hash_neu, salt, user["id"]),
    )
    con.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
    token = _neue_session(con, user["id"])
    con.commit()
    con.close()
    return {"ok": True, "token": token}


@app.post("/api/auth/konto_loeschen")
async def konto_loeschen(request: Request):
    """Konto endgültig löschen (Art. 17 DSGVO): Binder, Artworks samt Bilddateien, Sitzungen
    und Credit-Buchungen. Ein laufendes Abo wird dabei automatisch gekündigt — die Löschung
    darf nicht davon abhängen, dass der Nutzer vorher selbst kündigt. Bestellungen bleiben
    anonymisiert erhalten, weil für Zahlungsbelege gesetzliche Aufbewahrungsfristen gelten."""
    user = _require_user(request)
    data = await request.json()
    pw = str(data.get("passwort") or "")
    if _hash_pw(pw, user["salt"]) != user["pw_hash"]:
        raise HTTPException(400, "Das Passwort stimmt nicht.")
    if user.get("stripe_sub"):
        try:
            abo._stripe(f"subscriptions/{user['stripe_sub']}", {"cancel_at_period_end": True})
        except Exception as e:
            _log("Abo-Kündigung bei Kontolöschung fehlgeschlagen:", e)
    con = get_db()
    artworks = [r["id"] for r in con.execute("SELECT id FROM artworks WHERE user_id = ?", (user["id"],))]
    con.execute("DELETE FROM artworks WHERE user_id = ?", (user["id"],))
    con.execute("DELETE FROM binders WHERE user_id = ?", (user["id"],))
    con.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
    con.execute("DELETE FROM credit_buchungen WHERE user_id = ?", (user["id"],))
    # Vitrine und Sammlung gehören auch zum Konto: der öffentliche Name wäre sonst weiter
    # abrufbar, Besitzdaten mit Kaufpreisen blieben liegen, Herzen und Freigaben würden weiterzählen.
    con.execute("DELETE FROM profile WHERE user_id = ?", (user["id"],))
    con.execute("DELETE FROM sammlung WHERE user_id = ?", (user["id"],))
    con.execute("DELETE FROM stimmen WHERE user_id = ?", (user["id"],))
    con.execute("DELETE FROM artwork_freigaben WHERE user_id = ?", (user["id"],))
    con.execute("UPDATE meldungen SET melder_id = NULL WHERE melder_id = ?", (user["id"],))
    con.execute("UPDATE bestellungen SET user_id = 0, variante = CASE WHEN art = 'kuendigung' THEN '' ELSE variante END"
                " WHERE user_id = ?", (user["id"],))
    con.execute("DELETE FROM users WHERE id = ?", (user["id"],))
    con.commit()
    con.close()
    for aid in artworks:
        for datei in (CACHE / "artwork" / f"{aid}.png", CACHE / "artwork" / f"{aid}.vorschau.webp"):
            try:
                datei.unlink()
            except FileNotFoundError:
                pass
    return {"ok": True, "artworks_geloescht": len(artworks)}


@app.get("/api/auth/export")
def konto_export(request: Request):
    """Datenauskunft nach Art. 15/20 DSGVO: alles, was zu diesem Konto gespeichert ist,
    als JSON-Datei zum Herunterladen."""
    user = _require_user(request)
    con = get_db()
    def liste(sql, *args):
        return [dict(r) for r in con.execute(sql, args)]
    daten = {
        "konto": {k: v for k, v in user.items() if k not in ("pw_hash", "salt", "reset_token")},
        "binder": liste("SELECT * FROM binders WHERE user_id = ?", user["id"]),
        "profil": liste("SELECT name, kurztext, avatar_card, created_at FROM profile WHERE user_id = ?", user["id"]),
        "sammlung": liste("SELECT card_id, variante, anzahl, zustand, sprache, kaufpreis, gekauft_am, notiz, created_at FROM sammlung WHERE user_id = ?", user["id"]),
        "herzen": liste("SELECT binder_id, created_at FROM stimmen WHERE user_id = ?", user["id"]),
        "artworks": liste("SELECT id, binder_id, seite, layout, anker, stil, wunsch, pokemon,"
                          " status, credits, created_at FROM artworks WHERE user_id = ?", user["id"]),
        "credit_buchungen": liste("SELECT delta, grund, ref, saldo_danach, created_at"
                                  " FROM credit_buchungen WHERE user_id = ?", user["id"]),
        "bestellungen": liste("SELECT art, variante, betrag, waehrung, status, widerruf_text,"
                              " zustimmung_am, created_at FROM bestellungen WHERE user_id = ?", user["id"]),
        "hinweis": "Vollständige Auskunft nach Art. 15 DSGVO. Zahlungsdaten liegen bei Stripe;"
                   " wir speichern davon nur Kunden- und Abo-Kennungen.",
        "erstellt_am": datetime_str_vor(0),
    }
    con.close()
    inhalt = json.dumps(daten, ensure_ascii=False, indent=2)
    return Response(inhalt, media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="binderplan-daten.json"'})


@app.post("/api/auth/profil")
async def profil_aendern(request: Request):
    """Anzeigename (statt E-Mail-Präfix in Begrüßung und Kopfzeile)."""
    user = _require_user(request)
    data = await request.json()
    name = str(data.get("name") or "").strip()[:40]
    con = get_db()
    con.execute("UPDATE users SET name = ? WHERE id = ?", (name, user["id"]))
    con.commit()
    user = dict(con.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone())
    con.close()
    return {"user": _user_info(user)}


@app.get("/api/auth/me")
def auth_me(request: Request):
    user = _current_user(request)
    return {"user": _user_info(user) if user else None}


@app.post("/api/auth/claim")
async def auth_claim(request: Request):
    """Anonyme Binder aus dem Browser dem frisch angemeldeten Konto zuordnen."""
    user = _require_user(request)
    data = await request.json()
    ids = [str(i) for i in (data.get("ids") or [])][:50]
    if not ids:
        return {"uebernommen": 0}
    con = get_db()
    cur = con.execute(
        "UPDATE binders SET user_id = ? WHERE user_id IS NULL AND id IN (%s)"
        % ",".join("?" * len(ids)),
        [user["id"]] + ids,
    )
    con.commit()
    con.close()
    return {"uebernommen": cur.rowcount}
