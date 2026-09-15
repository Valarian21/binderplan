"""Binderplan – woher die Besucher und Konten kommen.

Ein Abschnitt der main.py in eigener Datei (wie katalog.py, passend.py): wird in deren
Namensraum ausgeführt, kein importierbares Modul.

**Ohne Cookie und ohne localStorage.** In der Datenschutzerklärung steht wörtlich, dass
Binderplan keine Werbe-Cookies setzt und deshalb keinen Cookie-Banner zeigt. Eine
Reichweitenmessung ist nach § 25 TDDDG nicht „unbedingt erforderlich" — ein Cookie dafür wäre
einwilligungspflichtig und würde den Banner nach sich ziehen. Der Weg hier kommt ohne
Speicher im Browser aus:

1. Der Kurzlink aus der Bio (`/tt`, `/ig`, …) leitet auf `/?utm_source=…&utm_medium=bio`.
2. Die Seite zählt den Besuch **aggregiert** je Tag und Kanal (`besuche_tag`) — keine
   IP, keine Kennung, keine Person.
3. Die Oberfläche liest die UTM-Werte einmal beim Laden in eine JavaScript-Variable (die mit
   der Seite stirbt) und schickt sie mit, wenn ein Gastbinder entsteht oder ein Konto
   angelegt wird. Gespeichert wird sie dann an `binders.herkunft` bzw. `users.herkunft`.
4. Bei der Registrierung übernimmt `/api/auth/claim` die Herkunft des Gastbinders ins Konto.

Der Preis: wer den Tab schließt und Tage später wiederkommt, zählt als „direkt". Das ist
bewusst so — die Alternative wäre ein Einwilligungsbanner.

Die Zahlen gehen zusätzlich an den Marketing-Piloten (`POST /api/mp/events`), wo die
Kanalzahlen der Beiträge liegen — erst dort steht der ganze Trichter nebeneinander.
"""

# --- Kurzlinks für die Profile ------------------------------------------------
# Ein Link je Plattform, nicht mehrere in einer Bio: mehrere Links verteilen die Klicks und
# machen die Zahlen unbrauchbar. Die Adresse muss lesbar und dauerhaft sein — „binderplan.app/tt"
# steht im Profil, die Parameter hängt der Server an. 302, nicht 301: ein dauerhaft
# weitergeleiteter Pfad bleibt im Browser hängen und lässt sich später nicht mehr umhängen.
HERKUNFT_KURZLINKS = {
    "ig":  ("instagram", "bio"),
    "tt":  ("tiktok", "bio"),
    "yt":  ("youtube", "bio"),
    "fb":  ("facebook", "bio"),
    "th":  ("threads", "bio"),
    "pin": ("pinterest", "bio"),
    "rd":  ("reddit", "bio"),
    "dc":  ("discord", "bio"),
}

# Diese Felder werden gelesen; mehr speichern wir nicht.
HERKUNFT_FELDER = ("utm_source", "utm_medium", "utm_campaign", "utm_content")
HERKUNFT_MAX = 200


def _herkunft_saeubern(wert: str) -> str:
    """Nur harmlose Zeichen und begrenzte Länge — der Wert kommt aus einer fremden URL."""
    wert = re.sub(r"[^A-Za-z0-9 _.:/+-]", "", (wert or "").strip())
    return wert[:HERKUNFT_MAX]


def herkunft_aus_query(query) -> str:
    """→ „quelle/medium/kampagne/inhalt" (hintere leere Teile fallen weg) oder ""."""
    teile = [_herkunft_saeubern(str(query.get(f) or "")) for f in HERKUNFT_FELDER]
    if not teile[0]:
        return ""
    while teile and not teile[-1]:
        teile.pop()
    return "/".join(teile)[:HERKUNFT_MAX]


def _herkunft_vom_referrer(request) -> str:
    """Fremde Verweise ohne UTM: nur der Hostname, nichts weiter."""
    ref = request.headers.get("referer") or ""
    if not ref:
        return ""
    try:
        host = ref.split("/")[2].lower()
    except IndexError:
        return ""
    if not host or host.endswith("binderplan.app") or host.startswith("127.0.0.1") or host.startswith("localhost"):
        return ""
    return _herkunft_saeubern("referrer/" + host)


def herkunft_tabellen(con):
    """Eigenes Schema. Bewusst hier und nicht in `init_db()`: die läuft beim Import der
    main.py, lange bevor dieser Abschnitt geladen ist."""
    con.execute("""CREATE TABLE IF NOT EXISTS besuche_tag (
        tag TEXT NOT NULL, quelle TEXT NOT NULL, medium TEXT NOT NULL DEFAULT '',
        kampagne TEXT NOT NULL DEFAULT '', inhalt TEXT NOT NULL DEFAULT '',
        seite TEXT NOT NULL DEFAULT '', anzahl INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (tag, quelle, medium, kampagne, inhalt, seite))""")
    for alter in ("ALTER TABLE binders ADD COLUMN herkunft TEXT",
                  "ALTER TABLE users ADD COLUMN herkunft TEXT"):
        try:
            con.execute(alter)
        except sqlite3.OperationalError:
            pass


def herkunft_zaehlen(request, antwort_status: int):
    """Einen Seitenaufruf aggregiert mitschreiben. Läuft in der Zugriffs-Middleware.

    Gezählt werden nur echte Seitenaufrufe mit erkennbarer Herkunft — keine API-Aufrufe,
    keine Bilder, keine Kurzlink-Weiterleitungen (die zählen erst auf der Zielseite, sonst
    stünde jeder Klick doppelt drin). Ein Fehler hier darf nie eine Auslieferung kippen."""
    try:
        pfad = request.url.path
        if antwort_status >= 400 or request.method != "GET":
            return
        if pfad.startswith("/api/") or pfad.startswith("/assets/") or "." in pfad.rsplit("/", 1)[-1]:
            return
        if pfad.strip("/") in HERKUNFT_KURZLINKS:
            return
        wert = herkunft_aus_query(request.query_params) or _herkunft_vom_referrer(request)
        if not wert:
            return
        teile = (wert.split("/") + ["", "", ""])[:4]
        seite = "app" if pfad.startswith("/app") else ("landing" if pfad in ("/", "/en") else "sonst")
        con = get_db()
        con.execute("""INSERT INTO besuche_tag (tag, quelle, medium, kampagne, inhalt, seite, anzahl)
                       VALUES (date('now'),?,?,?,?,?,1)
                       ON CONFLICT(tag, quelle, medium, kampagne, inhalt, seite)
                       DO UPDATE SET anzahl = anzahl + 1""",
                    (teile[0], teile[1], teile[2], teile[3], seite))
        con.commit()
        con.close()
    except Exception:
        log.debug("Besuchszähler übersprungen", exc_info=True)


def herkunft_an_binder(con, binder_id: str, wert: str):
    """Die Herkunft einmalig an einen Binder schreiben — die erste Berührung zählt."""
    if not wert or not binder_id:
        return
    con.execute("UPDATE binders SET herkunft = ? WHERE id = ? AND COALESCE(herkunft,'') = ''",
                (wert, binder_id))


def herkunft_ins_konto(con, user_id: int, wert: str, melden=True) -> bool:
    """Die Herkunft einmalig an ein Konto schreiben → True, wenn sie gesetzt wurde.

    Zwei Sperren, damit die Zahlen nicht verfälscht werden können (dieselben wie bei
    Lehreule): nur wenn das Feld noch leer ist, und nur bei Konten, die jünger als zehn
    Minuten sind — sonst ließe sich ein Bestandskonto nachträglich einer laufenden Kampagne
    zurechnen."""
    if not wert or not user_id:
        return False
    row = con.execute("SELECT COALESCE(herkunft,'') h, created_at FROM users WHERE id = ?",
                      (user_id,)).fetchone()
    if not row or row["h"]:
        return False
    alt = con.execute("SELECT (julianday('now') - julianday(?)) * 86400 s",
                      (row["created_at"],)).fetchone()["s"]
    if alt is None or alt > 600:
        return False
    con.execute("UPDATE users SET herkunft = ? WHERE id = ?", (wert, user_id))
    if melden:
        herkunft_melden("signup", str(user_id), wert)
    return True


def herkunft_ereignis(con, user_id, event: str):
    """Einen späteren Schritt desselben Kontos melden („activated", „paid").

    Die Herkunft steht schon am Konto; hier geht es nur darum, den Trichter weiterzuzählen.
    Konten ohne Herkunft (Direktbesuch, oder vor dem 15.09.2026 angelegt) werden übersprungen —
    eine Zeile ohne Kanal beantwortet keine Frage."""
    try:
        row = con.execute("SELECT COALESCE(herkunft,'') h FROM users WHERE id = ?", (user_id,)).fetchone()
        if row and row["h"]:
            herkunft_melden(event, str(user_id), row["h"])
    except Exception:
        log.debug("Ereignis %s nicht gemeldet", event, exc_info=True)


# --- Meldung an den Marketing-Piloten -----------------------------------------
# Dort liegen die Kanalzahlen der Beiträge (Aufrufe, Klicks je Kurzlink). Erst wenn die
# Anmeldungen daneben stehen, ergibt sich ein Trichter statt zweier halber Bilder.
# Der Aufruf läuft in einem eigenen Thread und darf niemals eine Antwort aufhalten oder
# scheitern lassen: er ist Buchhaltung, nicht Produkt.

def herkunft_melden(event: str, user_ref: str, wert: str, meta=None):
    umgebung = _env()
    ziel = (umgebung.get("MP_EVENTS_URL") or "").strip()
    token = (umgebung.get("MP_EVENTS_TOKEN") or "").strip()
    projekt = (umgebung.get("MP_PROJEKT") or "").strip()
    if not (ziel and token and projekt):
        return
    teile = ((wert or "").split("/") + ["", "", ""])[:4]
    nutzlast = {
        "project": projekt,
        "event": event,
        "userRef": str(user_ref or "")[:64],
        "utm": {"source": teile[0], "medium": teile[1], "campaign": teile[2], "content": teile[3]},
        "meta": meta or {},
    }

    def senden():
        try:
            httpx.post(ziel, json=nutzlast, timeout=8,
                       headers={"Authorization": "Bearer " + token})
        except Exception:
            log.debug("Ereignis %s nicht an den Piloten gemeldet", event, exc_info=True)

    threading.Thread(target=senden, daemon=True).start()


# --- Endpunkte ----------------------------------------------------------------

def _herkunft_weiterleitung(quelle: str, medium: str):
    def gehe_zu():
        return RedirectResponse(f"/?utm_source={quelle}&utm_medium={medium}", status_code=302)
    return gehe_zu


for _kuerzel, (_quelle, _medium) in HERKUNFT_KURZLINKS.items():
    app.add_api_route("/" + _kuerzel, _herkunft_weiterleitung(_quelle, _medium),
                      methods=["GET"], include_in_schema=False)


@app.post("/api/herkunft")
async def herkunft_melden_endpunkt(request: Request):
    """Die Oberfläche meldet, woher dieser Besuch kam — einmal je Gastbinder bzw. Konto.

    Absichtlich ein eigener, winziger Endpunkt statt eines neuen Feldes an „Binder speichern":
    er darf ohne Konto aufgerufen werden, er darf fehlschlagen, ohne dass etwas verloren geht,
    und er lässt sich später abschalten, ohne das Speichern anzufassen."""
    data = await request.json()
    wert = _herkunft_saeubern(str(data.get("herkunft") or ""))
    if not wert:
        return {"ok": False, "grund": "leer"}
    binder_id = str(data.get("binder_id") or "")[:64]
    con = get_db()
    try:
        if binder_id:
            herkunft_an_binder(con, binder_id, wert)
        user = _current_user(request)
        gesetzt = herkunft_ins_konto(con, user["id"], wert) if user else False
        con.commit()
    finally:
        con.close()
    return {"ok": True, "konto": gesetzt}


_hk_con = get_db()
try:
    herkunft_tabellen(_hk_con)
    _hk_con.commit()
finally:
    _hk_con.close()
