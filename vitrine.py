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


def druckrecht_sichern(user, items):
    """Von main.py aufgerufen, bevor ein PDF entsteht."""
    f = _dep.get("druckrecht_sichern")
    return f(user, items) if f else []


def register(app, *, get_db, current_user, require_user, env, admin_key, load_binder, abo, drossel=None):
    _dep.update(get_db=get_db, current_user=current_user, require_user=require_user, env=env,
                admin_key=admin_key, load_binder=load_binder, abo=abo, drossel=drossel)

    con = get_db()
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
        con.execute("INSERT INTO profile (user_id, name, kurztext, avatar_card, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET"
                    " name=excluded.name, kurztext=excluded.kurztext, avatar_card=excluded.avatar_card,"
                    " updated_at=excluded.updated_at",
                    (user["id"], name, kurztext, avatar, _now(), _now()))
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

    def _seiten_vorschau(items, layout, seiten=1):
        """Die ersten Seiten des Binders als Raster. Eine Binderseite ist das, was den Binder
        ausmacht — vier Karten nebeneinander sahen aus wie eine beliebige Trefferliste."""
        spalten, zeilen = RASTER.get(layout or "3x3", (3, 3))
        pro_seite = spalten * zeilen
        aus = []
        for nr in range(max(1, min(3, seiten))):
            teil = items[nr * pro_seite:(nr + 1) * pro_seite]
            if not teil and nr:
                break
            faecher = [_fach(teil[i] if i < len(teil) else None) for i in range(pro_seite)]
            if nr and all(f["art"] == "leer" for f in faecher):
                break
            aus.append(faecher)
        return {"spalten": spalten, "zeilen": zeilen, "seiten": aus}

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
                art: str = "", groesse: str = ""):
        limit = max(1, min(48, limit))
        user = current_user(request)
        con = get_db()
        reihen = con.execute(
            "SELECT b.id, b.name, b.layout, b.mode, b.options, b.items, b.veroeffentlicht_at, b.user_id,"
            " p.name AS besitzer, p.avatar_card,"
            " (SELECT COUNT(*) FROM stimmen s WHERE s.binder_id = b.id) AS stimmen"
            " FROM binders b LEFT JOIN profile p ON p.user_id = b.user_id"
            " WHERE b.sichtbar = 1 AND COALESCE(b.gesperrt,0) = 0"
            " AND COALESCE(p.gesperrt,0) = 0").fetchall()

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
                "gestimmt": r["id"] in meine, "karten": len(karten),
                "seiten": max(1, -(-len(items) // (spalten * zeilen))), "layout": r["layout"],
                "art": binder_art,
                "veroeffentlicht_at": r["veroeffentlicht_at"],
                "vorschau": _vorschau(items),
                "blatt": _seiten_vorschau(items, r["layout"], 2),
                "_punkte": _punkte(r["stimmen"], r["veroeffentlicht_at"] or ""),
            })
        con.close()

        if sortierung == "top":
            aus.sort(key=lambda b: (-b["stimmen"], b["veroeffentlicht_at"] or ""))
        elif sortierung == "neu":
            aus.sort(key=lambda b: (b["veroeffentlicht_at"] or ""), reverse=True)
        else:
            aus.sort(key=lambda b: -b["_punkte"])
        gesamt = len(aus)
        for b in aus:
            b.pop("_punkte", None)
        return {"binder": aus[offset:offset + limit], "gesamt": gesamt, "sortierung": sortierung}

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
                           "seiten": max(1, -(-len(items) // (spalten * zeilen))),
                           "art": _binder_art(r["mode"], optionen, items),
                           "veroeffentlicht_at": r["veroeffentlicht_at"],
                           "vorschau": _vorschau(items, 4),
                           "blatt": _seiten_vorschau(items, r["layout"], 1)})
        con.close()
        return {"profil": {"name": p["name"], "kurztext": p["kurztext"], "avatar_card": p["avatar_card"],
                           "seit": (p["created_at"] or "")[:10],
                           "binder_anzahl": len(binder), "karten": karten_gesamt,
                           "herzen": herzen_gesamt},
                "binder": binder}

    # --- Melden & Moderation ----------------------------------------------

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
