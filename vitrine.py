"""Die Vitrine: Binder öffentlich zeigen, abstimmen, Bestenliste.

Bewusst eine Vitrine und kein Forum. Es gibt keine Kommentare und keine Nachrichten —
damit fällt der teuerste Teil eines sozialen Netzes weg, das Moderieren von Gesprächen.
Übrig bleiben drei Textfelder (Anzeigename, Bindername, ein Satz zur Person), und die
werden beim Speichern geprüft.

Zwei Grenzen sind fest eingebaut, weil sie sich nachträglich schlecht einziehen lassen:
Veröffentlichen erst ab 16 (Art. 8 DSGVO, deutsche Grenze) und Abstimmen nur mit Konto.
"""

import json
import re
import calendar
import time

import httpx
from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

_dep = {}

MIND_ALTER = 16
NAME_MIN, NAME_MAX = 3, 24
TEXT_MAX = 140
PRUEF_MODELL = "google/gemini-2.5-flash-lite"

# Grobfilter für den Sofort-Fall. Die feine Prüfung macht das Modell; diese Liste
# fängt das Offensichtliche, ohne dass ein Aufruf nötig wird.
SPERRWORTE = [
    "hitler", "nazi", "hakenkreuz", "88er", "heil ", "nigger", "neger", "fotze", "schlampe",
    "hure", "wichser", "arschloch", "fick", "fuck", "shit", "bitch", "cunt", "rape", "vergewalt",
    "kinderporno", "cp ", "pedo", "suizid", "kys", "admin", "binderplan", "support", "moderator",
]

NAME_PROMPT = (
    "Prüfe, ob dieser öffentlich sichtbare Text auf einer Pokémon-Sammelseite unproblematisch ist. "
    "Beanstande nur: Beleidigungen, Hass, Sexuelles, Gewaltaufrufe, Drogenhandel, Kontaktdaten "
    "(Telefon, E-Mail, Adresse, Social-Media-Namen), Werbung/Links, Nachahmung des Betreibers. "
    "Fantasienamen, Pokémon-Namen, Anspielungen und schräger Humor sind erlaubt.\n"
    'Antwort als JSON: {"ok": true|false, "grund": "kurz, deutsch"}\n\nText: '
)


def _admin_ok(key, request=None):
    """Zeitkonstanter Vergleich; der Schlüssel darf auch als Kopfzeile X-Admin-Key kommen,
    damit er nicht in den Zugriffsprotokollen des Webservers landet."""
    import hmac
    echt = (_dep.get("admin_key") or (lambda: None))()
    if not echt:
        return False
    kandidat = key or ""
    if request is not None and not kandidat:
        kandidat = request.headers.get("x-admin-key", "")
    return hmac.compare_digest(kandidat, echt)


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def _alter(geburtsdatum):
    """Alter in Jahren aus 'JJJJ-MM-TT'. → None, wenn nichts Brauchbares dasteht."""
    if not geburtsdatum:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", str(geburtsdatum).strip())
    if not m:
        return None
    j, mo, tg = (int(x) for x in m.groups())
    heute = time.gmtime()
    jahre = heute.tm_year - j - ((heute.tm_mon, heute.tm_mday) < (mo, tg))
    return jahre if 0 <= jahre < 130 else None


def _text_ok(text):
    """→ (ok, grund). Erst die Wortliste, dann ein Modell — zusammen ~0,002 ct."""
    schmal = " " + re.sub(r"[^a-zäöüß0-9 ]", " ", (text or "").lower()) + " "
    for w in SPERRWORTE:
        if w.strip() and w in schmal:
            return False, "Dieser Name geht so nicht."
    key = _dep["env"]().get("OPENROUTER_KEY", "")
    if not key or not text.strip():
        return True, ""
    try:
        r = httpx.post("https://openrouter.ai/api/v1/chat/completions",
                       headers={"Authorization": f"Bearer {key}", "HTTP-Referer": "https://binderplan.app",
                                "X-Title": "Binderplan"},
                       json={"model": _dep["env"]().get("VITRINE_MODELL") or PRUEF_MODELL,
                             "messages": [{"role": "user", "content": NAME_PROMPT + text[:200]}],
                             "response_format": {"type": "json_object"}, "max_tokens": 200}, timeout=25)
        d = r.json()
        roh = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or "{}"
        p = json.loads(re.sub(r"^```(?:json)?|```$", "", roh.strip(), flags=re.M).strip())
        if p.get("ok") is False:
            return False, str(p.get("grund") or "Das geht so nicht.")[:120]
    except Exception:
        pass          # Prüfung nicht erreichbar → durchlassen, die Meldefunktion fängt den Rest
    return True, ""


def _punkte(stimmen, veroeffentlicht_at):
    """Zeitgewichtet, damit die ersten drei Binder nicht für immer oben stehen."""
    try:
        t0 = calendar.timegm(time.strptime(veroeffentlicht_at, "%Y-%m-%d %H:%M:%S"))   # Zeitstempel sind UTC
    except Exception:
        return 0.0
    stunden = max(0.0, (time.time() - t0) / 3600)
    return (max(0, stimmen - 1)) / ((stunden + 2) ** 1.5)


def _fenster_seit(fenster):
    """Ab wann Herzen zählen: 'woche' = 7 Tage, 'monat' = 30 Tage, sonst alle.
    Liefert einen UTC-Zeitstempel im Format der Tabellen; '' passt auf alles."""
    tage = {"heute": 1, "woche": 7, "monat": 30}.get(fenster or "")
    if not tage:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - tage * 86400))


def druckrecht_sichern(user, items):
    """Von main.py aufgerufen, bevor ein PDF entsteht."""
    f = _dep.get("druckrecht_sichern")
    return f(user, items) if f else []


def register(app, *, get_db, current_user, require_user, env, admin_key, load_binder, abo, drossel=None):
    _dep.update(get_db=get_db, current_user=current_user, require_user=require_user, env=env,
                admin_key=admin_key, load_binder=load_binder, abo=abo, drossel=drossel)

    con = get_db()
    for befehl in ("ALTER TABLE profile ADD COLUMN tauschliste INTEGER DEFAULT 0",):
        try:
            con.execute(befehl)
        except Exception:
            pass
    con.executescript("""
        CREATE TABLE IF NOT EXISTS profile (
            user_id INTEGER PRIMARY KEY, name TEXT UNIQUE, kurztext TEXT, avatar_card TEXT,
            created_at TEXT, updated_at TEXT, gesperrt INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS stimmen (
            binder_id TEXT, user_id INTEGER, created_at TEXT,
            PRIMARY KEY (binder_id, user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_stimmen_binder ON stimmen(binder_id);
        CREATE TABLE IF NOT EXISTS artwork_stimmen (
            artwork_id TEXT, user_id INTEGER, created_at TEXT,
            PRIMARY KEY (artwork_id, user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_astimmen ON artwork_stimmen(artwork_id);
        CREATE TABLE IF NOT EXISTS artwork_freigaben (
            user_id INTEGER, artwork_id TEXT, created_at TEXT,
            PRIMARY KEY (user_id, artwork_id)
        );
        CREATE TABLE IF NOT EXISTS meldungen (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ziel_typ TEXT, ziel_id TEXT, grund TEXT,
            melder_id INTEGER, status TEXT DEFAULT 'offen', created_at TEXT, erledigt_at TEXT
        );
    """)
    for alter in ("ALTER TABLE binders ADD COLUMN sichtbar INTEGER DEFAULT 0",
                  "ALTER TABLE binders ADD COLUMN veroeffentlicht_at TEXT",
                  "ALTER TABLE binders ADD COLUMN gesperrt INTEGER DEFAULT 0",
                  "ALTER TABLE users ADD COLUMN geburtsdatum TEXT"):
        try:
            con.execute(alter)
        except Exception:
            pass
    con.commit()
    con.close()

    # --- Profil ------------------------------------------------------------

    def _profil_von(con, user_id):
        r = con.execute("SELECT * FROM profile WHERE user_id = ?", (user_id,)).fetchone()
        return dict(r) if r else None

    @app.get("/api/profil")
    def profil_eigen(request: Request):
        user = require_user(request)
        con = get_db()
        p = _profil_von(con, user["id"])
        con.close()
        return {"profil": p, "alter": _alter(user["geburtsdatum"] if "geburtsdatum" in user.keys() else None),
                "mindestalter": MIND_ALTER}

    @app.put("/api/profil")
    async def profil_setzen(request: Request):
        user = require_user(request)
        data = await request.json()
        name = re.sub(r"\s+", " ", str(data.get("name") or "")).strip()
        kurztext = re.sub(r"\s+", " ", str(data.get("kurztext") or "")).strip()[:TEXT_MAX]
        tausch = 1 if data.get("tauschliste") else 0
        avatar = str(data.get("avatar_card") or "").strip()[:40]
        if not re.fullmatch(r"[A-Za-zÄÖÜäöüß0-9 ._-]{%d,%d}" % (NAME_MIN, NAME_MAX), name):
            raise HTTPException(400, f"Der Anzeigename braucht {NAME_MIN}–{NAME_MAX} Zeichen "
                                     "(Buchstaben, Ziffern, Punkt, Unterstrich, Bindestrich).")
        ok, grund = await run_in_threadpool(_text_ok, name + " " + kurztext)
        if not ok:
            raise HTTPException(400, grund)
        con = get_db()
        schon = con.execute("SELECT user_id FROM profile WHERE lower(name) = lower(?) AND user_id <> ?",
                            (name, user["id"])).fetchone()
        if schon:
            con.close()
            raise HTTPException(400, "Diesen Anzeigenamen gibt es schon.")
        con.execute("INSERT INTO profile (user_id, name, kurztext, avatar_card, tauschliste,"
                    " created_at, updated_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET"
                    " name=excluded.name, kurztext=excluded.kurztext, avatar_card=excluded.avatar_card,"
                    " tauschliste=excluded.tauschliste, updated_at=excluded.updated_at",
                    (user["id"], name, kurztext, avatar, tausch, _now(), _now()))
        con.commit()
        p = _profil_von(con, user["id"])
        con.close()
        return {"profil": p}

    @app.post("/api/profil/geburtsdatum")
    async def geburtsdatum_setzen(request: Request):
        """Konten von vor der Vitrine haben noch kein Geburtsdatum. Einmal setzbar,
        danach nur noch über den Support — sonst wäre die Altersgrenze wertlos."""
        user = require_user(request)
        if _alter(user["geburtsdatum"] if "geburtsdatum" in user.keys() else None) is not None:
            raise HTTPException(400, "Dein Geburtsdatum steht schon fest.")
        data = await request.json()
        geb = str(data.get("geburtsdatum") or "").strip()
        if _alter(geb) is None:
            raise HTTPException(400, "Bitte ein gültiges Geburtsdatum angeben.")
        con = get_db()
        con.execute("UPDATE users SET geburtsdatum = ? WHERE id = ?", (geb, user["id"]))
        con.commit()
        con.close()
        return {"ok": True, "alter": _alter(geb)}

    # --- Veröffentlichen ---------------------------------------------------

    @app.post("/api/vitrine/veroeffentlichen")
    async def veroeffentlichen(request: Request):
        user = require_user(request)
        data = await request.json()
        binder_id = str(data.get("binder_id") or "")
        oeffentlich = bool(data.get("oeffentlich"))
        con = get_db()
        row = con.execute("SELECT user_id, name FROM binders WHERE id = ?", (binder_id,)).fetchone()
        if not row:
            con.close(); raise HTTPException(404, "Binder nicht gefunden")
        if row["user_id"] != user["id"]:
            con.close(); raise HTTPException(403, "Das ist nicht dein Binder")
        if oeffentlich:
            jahre = _alter(user["geburtsdatum"] if "geburtsdatum" in user.keys() else None)
            if jahre is None:
                con.close(); raise HTTPException(400, detail={"code": "geburtsdatum",
                                                              "text": "Bitte trage zuerst dein Geburtsdatum ein."})
            if jahre < MIND_ALTER:
                con.close(); raise HTTPException(403, detail={"code": "zu_jung",
                                                              "text": f"Veröffentlichen geht erst ab {MIND_ALTER} Jahren."})
            p = _profil_von(con, user["id"])
            if not p or not p.get("name"):
                con.close(); raise HTTPException(400, detail={"code": "kein_profil",
                                                              "text": "Bitte lege zuerst einen Anzeigenamen fest."})
            if p.get("gesperrt"):
                con.close(); raise HTTPException(403, "Dein Profil ist gesperrt.")
            ok, grund = await run_in_threadpool(_text_ok, row["name"] or "")
            if not ok:
                con.close(); raise HTTPException(400, f"Der Bindername geht so nicht: {grund}")
            con.execute("UPDATE binders SET sichtbar = 1, veroeffentlicht_at = COALESCE(veroeffentlicht_at, ?)"
                        " WHERE id = ?", (_now(), binder_id))
        else:
            con.execute("UPDATE binders SET sichtbar = 0 WHERE id = ?", (binder_id,))
        con.commit()
        con.close()
        return {"ok": True, "oeffentlich": oeffentlich}

    # --- Abstimmen ---------------------------------------------------------

    @app.post("/api/vitrine/stimme")
    async def stimme(request: Request):
        user = require_user(request)          # ohne Konto sind die Zahlen wertlos
        data = await request.json()
        binder_id = str(data.get("binder_id") or "")
        con = get_db()
        row = con.execute("SELECT sichtbar, gesperrt, user_id FROM binders WHERE id = ?", (binder_id,)).fetchone()
        if not row or not row["sichtbar"] or row["gesperrt"]:
            con.close(); raise HTTPException(404, "Dieser Binder steht nicht in der Vitrine")
        # Atomar: erst versuchen einzufügen; kam nichts an, stand das Herz schon da und geht weg
        neu = con.execute("INSERT OR IGNORE INTO stimmen (binder_id, user_id, created_at) VALUES (?,?,?)",
                          (binder_id, user["id"], _now())).rowcount
        da = not neu
        if da:
            con.execute("DELETE FROM stimmen WHERE binder_id=? AND user_id=?", (binder_id, user["id"]))
        con.commit()
        n = con.execute("SELECT COUNT(*) c FROM stimmen WHERE binder_id=?", (binder_id,)).fetchone()["c"]
        con.close()
        return {"ok": True, "gestimmt": not da, "stimmen": n}

    @app.post("/api/vitrine/artwork/stimme")
    async def artwork_stimme(request: Request):
        """Herz an einer Kunstseite — dasselbe Verfahren wie beim Binder. Vorher zählte dort
        nur „geladen", und das misst Käufe: es stand überall auf null."""
        user = require_user(request)
        data = await request.json()
        artwork_id = str(data.get("artwork_id") or "")
        con = get_db()
        row = con.execute("SELECT oeffentlich, status FROM artworks WHERE id = ?", (artwork_id,)).fetchone()
        if not row or not row["oeffentlich"] or row["status"] != "fertig":
            con.close(); raise HTTPException(404, "Diese Kunstseite steht nicht in der Vitrine")
        neu = con.execute("INSERT OR IGNORE INTO artwork_stimmen (artwork_id, user_id, created_at) VALUES (?,?,?)",
                          (artwork_id, user["id"], _now())).rowcount
        da = not neu
        if da:
            con.execute("DELETE FROM artwork_stimmen WHERE artwork_id=? AND user_id=?", (artwork_id, user["id"]))
        con.commit()
        n = con.execute("SELECT COUNT(*) c FROM artwork_stimmen WHERE artwork_id=?", (artwork_id,)).fetchone()["c"]
        con.close()
        return {"ok": True, "gestimmt": not da, "stimmen": n}

    # --- Listen ------------------------------------------------------------

    RASTER = {"2x2": (2, 2), "3x3": (3, 3), "3x4": (3, 4), "4x3": (4, 3), "4x4": (4, 4),
              "4x5": (4, 5), "5x4": (5, 4), "5x5": (5, 5)}

    def _fach(it):
        """Ein Fach so beschreiben, wie es die Vorschau zeichnen kann — leere Plätze inklusive."""
        if not it:
            return {"art": "leer"}
        if it.get("type") == "card" and it.get("id"):
            return {"art": "card", "id": it["id"], "sprache": it.get("sprache") or ""}
        if it.get("type") == "art" and it.get("artwork"):
            # layout mitgeben: die Vorschau muss den richtigen Ausschnitt zeigen, sonst steht
            # dasselbe Bild neunmal nebeneinander statt einer über die Seite laufenden Malerei
            return {"art": "artwork", "id": it["artwork"], "slot": it.get("slot") or 0,
                    "layout": it.get("layout") or ""}
        if it.get("type") == "dex" and it.get("dex"):
            return {"art": "dex", "dex": it["dex"]}
        return {"art": "leer"}

    def _seitenzahl(items, layout, seiten_layouts=None):
        """Wie viele Binderseiten die Fächer ergeben — mit dem Raster jeder einzelnen
        Seite, nicht mit einer festen Länge für alle."""
        je = seiten_layouts or {}
        n = i = 0
        while i < len(items) or n == 0:
            roh = je.get(str(n), je.get(n))
            l = roh if roh in RASTER else (layout or "3x3")
            sp, ze = RASTER.get(l, (3, 3))
            i += sp * ze
            n += 1
        return max(1, n)

    def _seiten_vorschau(items, layout, seiten=1, seiten_layouts=None, hoechstens=3):
        """Die ersten Seiten des Binders als Raster. Eine Binderseite ist das, was den Binder
        ausmacht — vier Karten nebeneinander sahen aus wie eine beliebige Trefferliste.

        Jede Seite bringt ihr eigenes Raster mit, weil einzelne Seiten vom Standard
        abweichen dürfen."""
        spalten, zeilen = RASTER.get(layout or "3x3", (3, 3))
        je = seiten_layouts or {}
        aus = []
        start = 0
        for nr in range(max(1, min(hoechstens, seiten))):
            roh = je.get(str(nr), je.get(nr))
            l = roh if roh in RASTER else (layout or "3x3")
            sp, ze = RASTER.get(l, (3, 3))
            pro_seite = sp * ze
            teil = items[start:start + pro_seite]
            start += pro_seite
            if not teil and nr:
                break
            faecher = [_fach(teil[i] if i < len(teil) else None) for i in range(pro_seite)]
            if nr and all(f["art"] == "leer" for f in faecher):
                break
            aus.append({"spalten": sp, "zeilen": ze, "faecher": faecher})
        return {"spalten": spalten, "zeilen": zeilen, "seiten": aus}

    def _kunst_blatt(artwork_id, layout, anker_json):
        """Eine Kunstseite so beschreiben, wie die Vitrine eine Binderseite zeichnet:
        Ankerkarten als echte Scans in ihren Fächern, die übrigen Fächer zeigen ihren
        Ausschnitt der Malerei. Vorher stand das Bild als Poster in der Kachel — das
        sah nach Wandbild aus, geliefert wird aber ein Druckbogen für die leeren Hüllen."""
        sp, ze = RASTER.get(layout or "3x3", (3, 3))
        try:
            anker = json.loads(anker_json or "{}") or {}
        except Exception:
            anker = {}
        faecher = []
        for i in range(sp * ze):
            cid = anker.get(str(i))
            faecher.append({"art": "card", "id": cid, "sprache": ""} if cid
                           else {"art": "artwork", "id": artwork_id, "slot": i, "layout": layout or "3x3"})
        return {"spalten": sp, "zeilen": ze, "seiten": [{"spalten": sp, "zeilen": ze, "faecher": faecher}]}

    def _vorschau(items, anzahl=6):
        """Flache Kartenliste — wird noch für die Bildauswahl im Profil gebraucht."""
        aus = []
        for it in items:
            if it.get("type") == "card" and it.get("id"):
                aus.append({"art": "card", "id": it["id"]})
            elif it.get("type") == "art" and it.get("artwork") and it.get("slot") == 0:
                aus.append({"art": "artwork", "id": it["artwork"]})
            if len(aus) >= anzahl:
                break
        return aus

    def _binder_art(mode, options, items):
        """Wofür steht dieser Binder? Wird als Merkmal angezeigt und ist filterbar."""
        opt = options or {}
        if mode == "dex" or any(i.get("type") == "dex" for i in items[:40]):
            return "dex"
        if opt.get("illustrator"):
            return "kuenstler"
        if opt.get("dex"):
            return "pokemon"
        if any(i.get("type") == "art" for i in items[:60]):
            return "artwork"
        if mode == "master" or opt.get("master_set") or opt.get("reverse"):
            return "master"
        return "frei"

    @app.get("/api/vitrine")
    def vitrine(request: Request, sortierung: str = "trend", limit: int = 24, offset: int = 0,
                set_id: str = "", art_ort: str = "", art_merkmal: str = "", q: str = "",
                art: str = "", groesse: str = "", fenster: str = ""):
        """`fenster` (woche/monat/leer) zählt für die Bestenliste nur Herzen aus diesem
        Zeitraum — „beliebt diese Woche" statt einer Liste, die für immer dieselbe bleibt."""
        limit = max(1, min(48, limit))
        user = current_user(request)
        seit = _fenster_seit(fenster)
        con = get_db()
        reihen = con.execute(
            "SELECT b.id, b.name, b.layout, b.mode, b.options, b.items, b.veroeffentlicht_at, b.user_id,"
            " p.name AS besitzer, p.avatar_card,"
            " (SELECT COUNT(*) FROM stimmen s WHERE s.binder_id = b.id) AS stimmen,"
            " (SELECT COUNT(*) FROM stimmen s WHERE s.binder_id = b.id AND s.created_at >= ?) AS stimmen_fenster"
            " FROM binders b LEFT JOIN profile p ON p.user_id = b.user_id"
            " WHERE b.sichtbar = 1 AND COALESCE(b.gesperrt,0) = 0"
            " AND COALESCE(p.gesperrt,0) = 0", (seit,)).fetchall()

        meine = set()
        if user:
            meine = {r["binder_id"] for r in con.execute("SELECT binder_id FROM stimmen WHERE user_id=?",
                                                         (user["id"],))}
        aus = []
        for r in reihen:
            try:
                items = json.loads(r["items"] or "[]")
            except Exception:
                items = []
            karten = [i for i in items if i.get("type") == "card"]
            if q and q.lower() not in ((r["name"] or "") + " " + (r["besitzer"] or "")).lower():
                continue
            if set_id and not any(str(i.get("id", "")).startswith(set_id + "-") for i in karten):
                continue
            try:
                optionen = json.loads(r["options"] or "{}")
            except Exception:
                optionen = {}
            binder_art = _binder_art(r["mode"], optionen, items)
            if art and binder_art != art:
                continue
            # Größe als grobe Stufe: wer eine kleine Themenseite sucht, will keine 500er-Sammlung
            if groesse == "klein" and len(karten) > 40:
                continue
            if groesse == "mittel" and not (40 < len(karten) <= 150):
                continue
            if groesse == "gross" and len(karten) <= 150:
                continue
            spalten, zeilen = RASTER.get(r["layout"] or "3x3", (3, 3))
            aus.append({
                "id": r["id"], "name": r["name"], "besitzer": r["besitzer"] or "—",
                "avatar_card": r["avatar_card"], "stimmen": r["stimmen"],
                "stimmen_fenster": r["stimmen_fenster"],
                "gestimmt": r["id"] in meine, "karten": len(karten),
                "seiten": _seitenzahl(items, r["layout"], optionen.get("seitenLayouts")),
                "layout": r["layout"],
                "art": binder_art,
                "veroeffentlicht_at": r["veroeffentlicht_at"],
                "vorschau": _vorschau(items),
                "blatt": _seiten_vorschau(items, r["layout"], 3, optionen.get("seitenLayouts")),
                "_punkte": _punkte(r["stimmen"], r["veroeffentlicht_at"] or ""),
            })
        con.close()

        if sortierung == "top":
            aus.sort(key=lambda b: (-b["stimmen_fenster"], -b["stimmen"], b["veroeffentlicht_at"] or ""))
        elif sortierung == "neu":
            aus.sort(key=lambda b: (b["veroeffentlicht_at"] or ""), reverse=True)
        else:
            aus.sort(key=lambda b: -b["_punkte"])
        gesamt = len(aus)
        for b in aus:
            b.pop("_punkte", None)
        return {"binder": aus[offset:offset + limit], "gesamt": gesamt, "sortierung": sortierung,
                "fenster": fenster}

    @app.get("/api/vitrine/binder/{binder_id}")
    def vitrine_binder(binder_id: str, request: Request):
        """Begleitdaten zu einem öffentlichen Binder — Besitzer, Herzen, ob man selbst
        schon abgestimmt hat. Der Binder selbst kommt wie bisher über /api/binders."""
        user = current_user(request)
        con = get_db()
        r = con.execute(
            "SELECT b.id, b.name, b.sichtbar, COALESCE(b.gesperrt,0) AS gesperrt, b.veroeffentlicht_at,"
            " p.name AS besitzer, p.kurztext,"
            " (SELECT COUNT(*) FROM stimmen s WHERE s.binder_id = b.id) AS stimmen"
            " FROM binders b LEFT JOIN profile p ON p.user_id = b.user_id WHERE b.id = ?",
            (binder_id,)).fetchone()
        if not r:
            con.close(); raise HTTPException(404, "Binder nicht gefunden")
        gestimmt = False
        if user:
            gestimmt = bool(con.execute("SELECT 1 FROM stimmen WHERE binder_id=? AND user_id=?",
                                        (binder_id, user["id"])).fetchone())
        con.close()
        return {"id": r["id"], "name": r["name"], "oeffentlich": bool(r["sichtbar"]) and not r["gesperrt"],
                "besitzer": r["besitzer"], "kurztext": r["kurztext"], "stimmen": r["stimmen"],
                "gestimmt": gestimmt, "veroeffentlicht_at": r["veroeffentlicht_at"]}

    @app.get("/api/vitrine/profil/{name}")
    def vitrine_profil(name: str, request: Request):
        con = get_db()
        p = con.execute("SELECT * FROM profile WHERE lower(name) = lower(?)", (name,)).fetchone()
        if not p or p["gesperrt"]:
            con.close(); raise HTTPException(404, "Profil nicht gefunden")
        reihen = con.execute(
            "SELECT b.id, b.name, b.layout, b.mode, b.options, b.items, b.veroeffentlicht_at,"
            " (SELECT COUNT(*) FROM stimmen s WHERE s.binder_id=b.id) AS stimmen"
            " FROM binders b WHERE b.user_id = ? AND b.sichtbar = 1 AND COALESCE(b.gesperrt,0)=0"
            " ORDER BY b.veroeffentlicht_at DESC", (p["user_id"],)).fetchall()
        binder = []
        karten_gesamt = herzen_gesamt = 0
        for r in reihen:
            try:
                items = json.loads(r["items"] or "[]")
            except Exception:
                items = []
            try:
                optionen = json.loads(r["options"] or "{}")
            except Exception:
                optionen = {}
            anzahl = sum(1 for i in items if i.get("type") == "card")
            karten_gesamt += anzahl
            herzen_gesamt += r["stimmen"]
            spalten, zeilen = RASTER.get(r["layout"] or "3x3", (3, 3))
            binder.append({"id": r["id"], "name": r["name"], "stimmen": r["stimmen"],
                           "karten": anzahl, "layout": r["layout"],
                           "seiten": _seitenzahl(items, r["layout"], optionen.get("seitenLayouts")),
                           "art": _binder_art(r["mode"], optionen, items),
                           "veroeffentlicht_at": r["veroeffentlicht_at"],
                           "vorschau": _vorschau(items, 4),
                           "blatt": _seiten_vorschau(items, r["layout"], 1,
                                                     optionen.get("seitenLayouts"))})
        # Tauschliste: nur wenn der Sammler sie ausdrücklich freigegeben hat. Sie zeigt Karten,
        # keine Nachrichten — die Vitrine bleibt damit frei von Text zwischen Nutzern.
        tausch = []
        spalten = {r[1] for r in con.execute("PRAGMA table_info(profile)")}
        zeigt_tausch = bool(p["tauschliste"]) if "tauschliste" in spalten else False
        if zeigt_tausch:
            reihen = con.execute(
                "SELECT s.card_id, SUM(s.anzahl) - 1 AS ueber, c.name_de, c.name_en, c.local_id,"
                " (SELECT name FROM sets WHERE sets.id = c.set_id) AS setn"
                " FROM sammlung s JOIN cards c ON c.id = s.card_id"
                " WHERE s.user_id = ? GROUP BY s.card_id HAVING SUM(s.anzahl) > 1"
                " ORDER BY ueber DESC, c.name_de LIMIT 60", (p["user_id"],)).fetchall()
            tausch = [{"id": r["card_id"], "name": r["name_de"] or r["name_en"],
                       "set": r["setn"] or "", "nr": r["local_id"], "ueber": r["ueber"]}
                      for r in reihen]
        con.close()
        return {"profil": {"name": p["name"], "kurztext": p["kurztext"], "avatar_card": p["avatar_card"],
                           "seit": (p["created_at"] or "")[:10],
                           "binder_anzahl": len(binder), "karten": karten_gesamt,
                           "herzen": herzen_gesamt, "tauschliste": zeigt_tausch},
                "binder": binder, "tausch": tausch}

    # --- Melden & Moderation ----------------------------------------------

    # --- Kunstseiten ------------------------------------------------------
    # Bis hierher waren Artwork-Seiten nur zu finden, wenn jemand den ganzen Binder
    # veröffentlicht hatte. Sie sind aber das Einzige in Binderplan, das echte Arbeit
    # und echtes Geld enthält — sie verdienen einen eigenen Bereich.
    #
    # Preismodell (die Zahlen stehen in abo.py, die Begründung auch): eine fremde Seite
    # kostet ARTWORK_FREMD Credits, davon gehen ARTWORK_ANTEIL an den Ersteller. Weil die
    # Ausschüttung kleiner ist als der Preis, vernichtet jede Übernahme Credits — zwei
    # Konten, die sich gegenseitig Seiten abkaufen, verlieren beide.

    async def vitrine_pruefen(user, text):
        """Dieselbe Schwelle wie beim Veröffentlichen eines Binders: Alter, Anzeigename,
        Textprüfung. Wird von artwork.py mitbenutzt, damit es nur eine Regel gibt."""
        jahre = _alter(user["geburtsdatum"] if "geburtsdatum" in user.keys() else None)
        if jahre is None:
            raise HTTPException(400, detail={"code": "geburtsdatum",
                                             "text": "Bitte trage zuerst dein Geburtsdatum ein."})
        if jahre < MIND_ALTER:
            raise HTTPException(403, detail={"code": "zu_jung",
                                             "text": f"Veröffentlichen geht erst ab {MIND_ALTER} Jahren."})
        con = get_db()
        pr = _profil_von(con, user["id"])
        con.close()
        if not pr or not pr.get("name"):
            raise HTTPException(400, detail={"code": "kein_profil",
                                             "text": "Bitte lege zuerst einen Anzeigenamen fest."})
        if pr.get("gesperrt"):
            raise HTTPException(403, "Dein Profil ist gesperrt.")
        if text:
            ok, grund = await run_in_threadpool(_text_ok, text)
            if not ok:
                raise HTTPException(400, detail={"code": "text", "text": grund})

    _dep["vitrine_pruefen"] = vitrine_pruefen

    def _kunst_karten(reihen):
        """Zu jeder Kunstseite die Karten, um die herum sie gemalt wurde.

        Danach lässt sich nach dem suchen, was man tatsächlich im Kopf hat — „Glurak",
        „Karpador" — und nach dem Jahrgang der Karten filtern. Bei mehreren Karten zählt
        jeweils die äußerste: das älteste und das neueste Erscheinungsjahr."""
        anker = {}
        for r in reihen:
            try:
                anker[r["id"]] = list(dict.fromkeys(
                    (json.loads(r["anker"] or "{}") or {}).values()))
            except Exception:
                anker[r["id"]] = []
        alle = sorted({c for liste in anker.values() for c in liste if c})
        info = {}
        if alle:
            con = get_db()
            for teil in [alle[i:i + 400] for i in range(0, len(alle), 400)]:
                marken = ",".join("?" * len(teil))
                for k in con.execute(
                        f"SELECT c.id, c.name_de, c.name_en, c.name_ja, c.release_date,"
                        f" p.name_de AS dex_de, p.name_en AS dex_en"
                        f" FROM cards c LEFT JOIN pokemon p ON p.dex_id = c.first_dex"
                        f" WHERE c.id IN ({marken})", teil):
                    info[k["id"]] = {
                        "name": k["name_de"] or k["name_en"] or k["name_ja"] or k["id"],
                        "jahr": int((k["release_date"] or "0")[:4] or 0),
                        "sucht": " ".join(x for x in (k["name_de"], k["name_en"], k["name_ja"],
                                                      k["dex_de"], k["dex_en"]) if x).lower(),
                    }
            con.close()
        aus = {}
        for aid, liste in anker.items():
            karten = [info[c] for c in liste if c in info]
            jahre = [k["jahr"] for k in karten if k["jahr"]]
            aus[aid] = {
                "karten": [{"id": c, "name": info[c]["name"]} for c in liste if c in info],
                "jahr_alt": min(jahre) if jahre else None,
                "jahr_neu": max(jahre) if jahre else None,
                "sucht": " ".join(k["sucht"] for k in karten),
            }
        return aus

    def _kunst_zeile(r, meine, karten=None):
        z = {
            "id": r["id"], "titel": r["titel"] or "Kunstseite",
            "stil": r["stil"], "layout": r["layout"],
            "besitzer": r["besitzer"] or "—", "mein": r["user_id"] in meine,
            "downloads": r["downloads"] or 0,
            "veroeffentlicht_at": r["veroeffentlicht_at"],
            "breite": r["breite"], "hoehe": r["hoehe"],
            "vorschau": f"api/artwork/{r['id']}/bild?v=vorschau",
        }
        if karten:
            z.update(karten=karten.get("karten", []), jahr_alt=karten.get("jahr_alt"),
                     jahr_neu=karten.get("jahr_neu"))
        return z

    @app.get("/api/vitrine/artwork")
    def vitrine_artwork(request: Request, sortierung: str = "neu", q: str = "", stil: str = "",
                        layout: str = "", jahr_von: int = 0, jahr_bis: int = 0,
                        limit: int = 24, offset: int = 0, fenster: str = ""):
        """Der Kunstseiten-Bereich der Vitrine."""
        limit = max(1, min(48, limit))
        user = current_user(request)
        abo = _dep["abo"]
        seit = _fenster_seit(fenster)
        con = get_db()
        reihen = con.execute(
            "SELECT a.id, a.titel, a.stil, a.layout, a.user_id, a.downloads, a.breite, a.hoehe,"
            " a.veroeffentlicht_at, a.anker, p.name AS besitzer,"
            " (SELECT COUNT(*) FROM artwork_stimmen s WHERE s.artwork_id = a.id) AS stimmen,"
            " (SELECT COUNT(*) FROM artwork_stimmen s WHERE s.artwork_id = a.id AND s.created_at >= ?) AS stimmen_fenster"
            " FROM artworks a LEFT JOIN profile p ON p.user_id = a.user_id"
            " WHERE COALESCE(a.oeffentlich,0) = 1 AND a.status = 'fertig'"
            " AND COALESCE(p.gesperrt,0) = 0", (seit,)).fetchall()
        habe, meine_herzen = set(), set()
        if user:
            habe = {x["artwork_id"] for x in con.execute(
                "SELECT artwork_id FROM artwork_freigaben WHERE user_id = ?", (user["id"],))}
            meine_herzen = {x["artwork_id"] for x in con.execute(
                "SELECT artwork_id FROM artwork_stimmen WHERE user_id = ?", (user["id"],))}
        con.close()
        karten = _kunst_karten(reihen)
        aus = []
        for r in reihen:
            k = karten.get(r["id"], {})
            if q and q.lower() not in ((r["titel"] or "") + " " + (r["besitzer"] or "") + " "
                                       + (r["stil"] or "") + " " + k.get("sucht", "")).lower():
                continue
            if stil and r["stil"] != stil:
                continue
            if layout and r["layout"] != layout:
                continue
            # Jahrgang: bei mehreren Karten zählt die äußerste. Gesucht wird über den
            # ganzen Bereich einer Seite — wer „bis 2003" filtert, will auch die Seite
            # sehen, auf der ein Base-Set-Glurak neben einer neueren Karte liegt.
            alt, neu = k.get("jahr_alt"), k.get("jahr_neu")
            if jahr_von and (neu or 9999) < jahr_von:
                continue
            if jahr_bis and (alt or 0) > jahr_bis:
                continue
            z = _kunst_zeile(r, {user["id"]} if user else set(), k)
            z["habe"] = r["id"] in habe or z["mein"]
            z["stimmen"] = r["stimmen"]
            z["stimmen_fenster"] = r["stimmen_fenster"]
            z["gestimmt"] = r["id"] in meine_herzen
            z["blatt"] = _kunst_blatt(r["id"], r["layout"], r["anker"])
            aus.append(z)
        if sortierung == "top":
            aus.sort(key=lambda a: (-a["stimmen_fenster"], -a["stimmen"], -a["downloads"],
                                    a["veroeffentlicht_at"] or ""))
        elif sortierung == "alt":
            aus.sort(key=lambda a: (a.get("jahr_alt") or 9999, a["veroeffentlicht_at"] or ""))
        elif sortierung == "jung":
            aus.sort(key=lambda a: (-(a.get("jahr_neu") or 0), a["veroeffentlicht_at"] or ""))
        else:
            aus.sort(key=lambda a: (a["veroeffentlicht_at"] or ""), reverse=True)
        jahre = [j for k in karten.values() for j in (k.get("jahr_alt"), k.get("jahr_neu")) if j]
        return {"artworks": aus[offset:offset + limit], "gesamt": len(aus),
                "preis": abo.ARTWORK_FREMD, "anteil": abo.ARTWORK_ANTEIL,
                "stile": sorted({r["stil"] for r in reihen if r["stil"]}),
                "jahr_min": min(jahre) if jahre else None, "jahr_max": max(jahre) if jahre else None,
                "fenster": fenster}

    # --- Schaufenster für die Startseite --------------------------------------

    _schaufenster_cache = {"bis": 0.0, "daten": None}

    @app.get("/api/vitrine/schaufenster")
    def schaufenster(request: Request):
        """Für die Startseite vor dem Anmelden: die beliebtesten Binder und Kunstseiten,
        ohne Konto, zehn Minuten gecacht. Das Zeitfenster wählt sich selbst — das engste,
        in dem mindestens vier Binder ein Herz haben. Sonst stünde wochenlang „Beliebt
        diese Woche" über einer leeren Reihe. Jeder Binder bringt bis zu zwölf Seiten
        mit, damit Besucher ihn auf der Startseite durchblättern können."""
        jetzt = time.time()
        c = _schaufenster_cache
        if c["daten"] and c["bis"] > jetzt:
            return c["daten"]
        gewaehlt, binder = "", []
        for f in ("woche", "monat", ""):
            d = vitrine(request, sortierung="top", limit=8, fenster=f)
            if f == "" or sum(1 for b in d["binder"] if b["stimmen_fenster"] > 0) >= 4:
                gewaehlt, binder = f, d["binder"]
                break
        kunst = vitrine_artwork(request, sortierung="top", limit=10, fenster=gewaehlt)["artworks"]
        for b in binder:
            for feld in ("gestimmt", "vorschau", "avatar_card"):
                b.pop(feld, None)
            try:
                voll = load_binder(b["id"])
                opt = voll.get("options") or {}
                b["seiten_alle"] = _seiten_vorschau(voll["items"], voll["layout"], 12,
                                                    opt.get("seitenLayouts"), hoechstens=12)["seiten"]
            except Exception:
                b["seiten_alle"] = b["blatt"]["seiten"]
        for a in kunst:
            for feld in ("gestimmt", "habe", "mein"):
                a.pop(feld, None)
        daten = {"fenster": gewaehlt, "binder": binder, "kunst": kunst}
        if not current_user(request):
            c.update(bis=jetzt + 600, daten=daten)
        return daten

    def _uebernehmen(user, artwork_id):
        """Eine fremde Kunstseite kaufen. Idempotent: wer sie schon hat, zahlt nicht noch mal."""
        abo = _dep["abo"]
        con = get_db()
        r = con.execute("SELECT id, user_id, layout, seite, anker, stil, titel, status,"
                        " COALESCE(oeffentlich,0) oeffentlich FROM artworks WHERE id = ?",
                        (artwork_id,)).fetchone()
        if not r or r["status"] != "fertig":
            con.close(); raise HTTPException(404, "Kunstseite nicht gefunden")
        if r["user_id"] == user["id"]:
            con.close()
            return {"ok": True, "bezahlt": 0, "artwork": artwork_id, "layout": r["layout"],
                    "anker": json.loads(r["anker"] or "{}")}
        if not r["oeffentlich"]:
            con.close(); raise HTTPException(403, detail={"code": "nicht_oeffentlich",
                                                          "text": "Diese Seite steht nicht öffentlich."})
        # Erst die Freigabe eintragen, dann abbuchen: INSERT OR IGNORE meldet über rowcount,
        # ob sie wirklich neu ist — zwei gleichzeitige Klicks zahlten sonst beide.
        neu = con.execute("INSERT OR IGNORE INTO artwork_freigaben (user_id, artwork_id, created_at)"
                          " VALUES (?,?,?)", (user["id"], artwork_id, _now())).rowcount
        con.commit()
        con.close()
        bezahlt = 0
        if neu:
            try:
                abo.abbuchen(user, abo.ARTWORK_FREMD, "artwork_uebernahme", artwork_id)
                bezahlt = abo.ARTWORK_FREMD
            except HTTPException:
                con = get_db()
                con.execute("DELETE FROM artwork_freigaben WHERE user_id = ? AND artwork_id = ?",
                            (user["id"], artwork_id))
                con.commit(); con.close()
                raise HTTPException(402, detail={
                    "code": "credits", "benoetigt": abo.ARTWORK_FREMD,
                    "text": f"Diese Kunstseite kostet {abo.ARTWORK_FREMD} Credits."})
            anteil = abo.artwork_anteil(r["user_id"], artwork_id)
            con = get_db()
            con.execute("UPDATE artworks SET downloads = COALESCE(downloads,0) + 1,"
                        " verdient = COALESCE(verdient,0) + ? WHERE id = ?", (anteil, artwork_id))
            con.commit(); con.close()
        return {"ok": True, "bezahlt": bezahlt, "artwork": artwork_id, "layout": r["layout"],
                "anker": json.loads(r["anker"] or "{}"), "titel": r["titel"] or "", "stil": r["stil"]}

    def _konto_frisch(user_id):
        """Nach dem Abbuchen den Stand neu lesen — der Request-Schnappschuss ist veraltet
        und die Oberfläche zeigte sonst noch das Guthaben von vor dem Kauf."""
        con = get_db()
        row = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        con.close()
        return _dep["abo"].konto_info(dict(row)) if row else None

    @app.post("/api/vitrine/artwork/{artwork_id}/uebernehmen")
    def vitrine_artwork_uebernehmen(artwork_id: str, request: Request):
        user = require_user(request)
        out = _uebernehmen(user, artwork_id)
        out["konto"] = _konto_frisch(user["id"])
        return out

    @app.get("/api/vitrine/binder/{binder_id}/kosten")
    def vitrine_binder_kosten(binder_id: str, request: Request):
        """Was das Kopieren dieses Binders kostet.

        Der Plan selbst ist frei und bleibt es: eine Liste von Kartennummern kostet uns
        nichts, und das Kopieren ist der Grund, warum sich jemand fremde Binder überhaupt
        ansieht. Geld steckt nur in den Kunstseiten darin — und die werden einzeln
        abgerechnet, zum selben Preis wie im Kunstseiten-Bereich. Wer sie nicht will,
        kopiert ohne sie."""
        user = current_user(request)
        abo = _dep["abo"]
        con = get_db()
        row = con.execute("SELECT items, user_id FROM binders WHERE id = ?", (binder_id,)).fetchone()
        if not row:
            con.close(); raise HTTPException(404, "Binder nicht gefunden")
        try:
            items = json.loads(row["items"] or "[]")
        except Exception:
            items = []
        ids = {i.get("artwork") for i in items if i.get("type") == "art" and i.get("artwork")}
        offen = []
        if ids and user and row["user_id"] != user["id"]:
            marken = ",".join("?" * len(ids))
            schon = {x["artwork_id"] for x in con.execute(
                f"SELECT artwork_id FROM artwork_freigaben WHERE user_id = ? AND artwork_id IN ({marken})",
                (user["id"], *ids))}
            offen = [x["id"] for x in con.execute(
                f"SELECT id FROM artworks WHERE id IN ({marken}) AND user_id <> ?"
                f" AND COALESCE(oeffentlich,0) = 1", (*ids, user["id"]))
                if x["id"] not in schon]
        elif ids and not user:
            offen = list(ids)
        con.close()
        return {"artwork_seiten": len(ids), "zu_zahlen": len(offen),
                "credits": len(offen) * abo.ARTWORK_FREMD, "preis": abo.ARTWORK_FREMD}

    @app.post("/api/vitrine/binder/{binder_id}/artwork_kaufen")
    def vitrine_binder_artwork_kaufen(binder_id: str, request: Request):
        """Alle Kunstseiten eines fremden Binders auf einmal übernehmen."""
        user = require_user(request)
        con = get_db()
        row = con.execute("SELECT items FROM binders WHERE id = ?", (binder_id,)).fetchone()
        con.close()
        if not row:
            raise HTTPException(404, "Binder nicht gefunden")
        try:
            items = json.loads(row["items"] or "[]")
        except Exception:
            items = []
        ids = [i for i in dict.fromkeys(
            x.get("artwork") for x in items if x.get("type") == "art" and x.get("artwork")) if i]
        gekauft, bezahlt = [], 0
        for aid in ids:
            out = _uebernehmen(user, aid)
            gekauft.append(aid)
            bezahlt += out["bezahlt"]
        return {"ok": True, "seiten": gekauft, "bezahlt": bezahlt,
                "konto": _konto_frisch(user["id"])}

    @app.post("/api/vitrine/meldung")
    async def meldung(request: Request):
        drossel = _dep.get("drossel")
        if drossel:
            drossel(request, "melden")
        data = await request.json()
        typ = str(data.get("ziel_typ") or "")[:16]
        ziel = str(data.get("ziel_id") or "")[:64]
        grund = re.sub(r"\s+", " ", str(data.get("grund") or "")).strip()[:300]
        if typ not in ("binder", "profil") or not ziel:
            raise HTTPException(400, "Unklar, was gemeldet werden soll")
        user = current_user(request)
        con = get_db()
        con.execute("INSERT INTO meldungen (ziel_typ, ziel_id, grund, melder_id, created_at)"
                    " VALUES (?,?,?,?,?)", (typ, ziel, grund, user["id"] if user else None, _now()))
        con.commit()
        con.close()
        return {"ok": True}

    @app.get("/api/admin/meldungen")
    def admin_meldungen(request: Request, key: str = "", status: str = "offen"):
        if not _admin_ok(key, request):
            raise HTTPException(403)
        con = get_db()
        reihen = con.execute("SELECT * FROM meldungen WHERE status = ? ORDER BY created_at DESC LIMIT 200",
                             (status,)).fetchall()
        con.close()
        return {"meldungen": [dict(r) for r in reihen]}

    @app.post("/api/admin/moderation")
    async def admin_moderation(request: Request, key: str = ""):
        if not admin_key() or key != admin_key():
            raise HTTPException(403)
        data = await request.json()
        typ, ziel, aktion = str(data.get("ziel_typ")), str(data.get("ziel_id")), str(data.get("aktion"))
        con = get_db()
        if typ == "binder" and aktion in ("sperren", "freigeben"):
            con.execute("UPDATE binders SET gesperrt = ? WHERE id = ?", (1 if aktion == "sperren" else 0, ziel))
        elif typ == "profil" and aktion in ("sperren", "freigeben"):
            con.execute("UPDATE profile SET gesperrt = ? WHERE lower(name) = lower(?)",
                        (1 if aktion == "sperren" else 0, ziel))
        else:
            con.close(); raise HTTPException(400, "Unbekannte Aktion")
        con.execute("UPDATE meldungen SET status='erledigt', erledigt_at=? WHERE ziel_typ=? AND ziel_id=?",
                    (_now(), typ, ziel))
        con.commit()
        con.close()
        return {"ok": True}

    def druckrecht_sichern(user, items):
        """Vor dem PDF-Export: fremde Artwork-Seiten im Binder abrechnen.

        Eigene Seiten sind frei. Fremde kosten einmalig Credits — danach gehören sie
        dem Konto und lassen sich beliebig oft drucken. Sie müssen aus einem Binder
        stammen, der wirklich in der Vitrine steht; sonst wäre die Artwork-ID der
        Schlüssel zu jeder fremden Seite."""
        abo = _dep["abo"]
        ids = {i.get("artwork") for i in items if i.get("type") == "art" and i.get("artwork")}
        if not ids or not abo:
            return []
        con = get_db()
        marken = ",".join("?" * len(ids))
        reihen = con.execute(
            f"SELECT a.id, a.user_id, a.binder_id, COALESCE(b.sichtbar,0) AS oeffentlich"
            f" FROM artworks a LEFT JOIN binders b ON b.id = a.binder_id"
            f" WHERE a.id IN ({marken})", tuple(ids)).fetchall()
        schon = {r["artwork_id"] for r in con.execute(
            f"SELECT artwork_id FROM artwork_freigaben WHERE user_id = ? AND artwork_id IN ({marken})",
            (user["id"], *ids))}
        con.close()

        offen = [r for r in reihen if r["user_id"] != user["id"] and r["id"] not in schon]
        gesperrt = [r["id"] for r in offen if not r["oeffentlich"]]
        if gesperrt:
            raise HTTPException(403, detail={"code": "fremdes_artwork",
                                             "text": "Eine Artwork-Seite in diesem Binder gehört jemand anderem "
                                                     "und steht nicht öffentlich."})
        if not offen:
            return []
        # Erst die Freigabe eintragen, dann abbuchen. INSERT OR IGNORE meldet über rowcount,
        # welche Zeilen wirklich neu sind — zwei gleichzeitige Exporte (Doppelklick auf „Drucken“)
        # bezahlten sonst beide für dieselbe Seite.
        con = get_db()
        wirklich_neu = []
        for r in offen:
            if con.execute("INSERT OR IGNORE INTO artwork_freigaben (user_id, artwork_id, created_at)"
                           " VALUES (?,?,?)", (user["id"], r["id"], _now())).rowcount:
                wirklich_neu.append(r)
        con.commit()
        con.close()
        if not wirklich_neu:
            return []
        preis = abo.ARTWORK_FREMD * len(wirklich_neu)
        try:
            abo.abbuchen(user, preis, "artwork_fremd", ",".join(r["id"] for r in wirklich_neu))
        except HTTPException:
            # Guthaben reicht nicht: die eben reservierten Freigaben wieder zurücknehmen
            con = get_db()
            con.executemany("DELETE FROM artwork_freigaben WHERE user_id = ? AND artwork_id = ?",
                            [(user["id"], r["id"]) for r in wirklich_neu])
            con.commit()
            con.close()
            raise HTTPException(402, detail={"code": "credits", "benoetigt": preis,
                                             "text": f"Für {len(wirklich_neu)} fremde Artwork-Seite(n) brauchst du "
                                                     f"{preis} Credits."})
        return [r["id"] for r in wirklich_neu]

    _dep["druckrecht_sichern"] = druckrecht_sichern

    def kennzahlen():
        con = get_db()
        n = con.execute("SELECT COUNT(*) c FROM binders WHERE sichtbar = 1").fetchone()["c"]
        m = con.execute("SELECT COUNT(*) c FROM meldungen WHERE status='offen'").fetchone()["c"]
        con.close()
        return {"oeffentlich": n, "meldungen_offen": m}

    return kennzahlen
