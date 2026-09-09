#!/usr/bin/env python3
"""Rauchtest gegen den laufenden Dienst – läuft im Deploy nach dem Neustart.

Aufruf: python3 scripts/rauchtest.py [basis]      (Standard http://127.0.0.1:8103)
Mit BP_TOKEN in der Umgebung (Session-Token eines Kontos) laufen zusätzlich die Konto-Prüfungen:
Binder anlegen, ändern, Konflikt (409), löschen, Sammlung, Markt, Alarme. Ohne Token nur die
öffentlichen Wege. Rückgabe 1, sobald eine Prüfung scheitert. Es gibt keine Testsuite im Repo –
das hier ist der Beweis, dass die API nach einem Deploy antwortet."""
import json, os, sys, time, urllib.request, urllib.error

BASIS = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8103").rstrip("/")
TOKEN = os.environ.get("BP_TOKEN", "").strip()
befunde, ok = [], 0


def ruf(pfad, methode="GET", daten=None, token=None, roh=False):
    req = urllib.request.Request(BASIS + pfad, method=methode)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    body = None
    if daten is not None:
        body = json.dumps(daten).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, body, timeout=60) as r:
            inhalt = r.read()
            return r.status, (inhalt if roh else (json.loads(inhalt) if inhalt[:1] in (b"{", b"[") else inhalt.decode("utf-8", "replace")))
    except urllib.error.HTTPError as e:
        inhalt = e.read()
        try:
            return e.code, json.loads(inhalt)
        except Exception:
            return e.code, inhalt.decode("utf-8", "replace")


def pruefe(name, bedingung, hinweis=""):
    global ok
    if bedingung:
        ok += 1
    else:
        befunde.append(f"{name}{(' – ' + str(hinweis)[:160]) if hinweis else ''}")


# --- öffentlich ---------------------------------------------------------------------------------
s, d = ruf("/api/health"); pruefe("health", s == 200, d)
s, d = ruf("/api/meta"); pruefe("meta", s == 200 and isinstance(d, dict) and d.get("counts", {}).get("cards", 0) > 30000, d if s != 200 else d.get("counts"))
s, d = ruf("/api/cards?q=glurak&limit=5"); pruefe("suche glurak", s == 200 and (d.get("gesamt") or d.get("total") or len(d.get("cards", d.get("karten", [])))) >= 5, s)
s, d = ruf("/api/cards/base1-4/detail"); pruefe("detail base1-4", s == 200 and d.get("name"), s)
s, d = ruf("/app"); pruefe("app-hülle", s == 200 and "assets/kern.js" in d, s)
s, d = ruf("/assets/kern.js", roh=True); pruefe("kern.js", s == 200 and len(d) > 50000, s)
s, d = ruf("/assets/app.css", roh=True); pruefe("app.css", s == 200 and len(d) > 20000, s)
s, d = ruf("/assets/tokens.css", roh=True); pruefe("tokens.css", s == 200 and b"--t-xs" in d, s)
s, d = ruf("/?landing=1"); pruefe("landingpage", s == 200 and "Binderplan" in d, s)
s, d = ruf("/set/base1"); pruefe("set-seite", s == 200 and "Glurak" in d or "Charizard" in d, s)
s, d = ruf("/sitemap.xml"); pruefe("sitemap", s == 200, s)
s, d = ruf("/sw.js", roh=True); pruefe("service worker", s == 200 and b"addEventListener" in d, s)
s, d = ruf("/api/sammlung/uebersicht"); pruefe("sammlung ohne konto = 401", s == 401, s)

# --- mit Konto ----------------------------------------------------------------------------------
if TOKEN:
    s, d = ruf("/api/auth/me", token=TOKEN); pruefe("auth/me", s == 200 and d.get("user"), s)
    s, d = ruf("/api/binders?ids=", token=TOKEN); pruefe("binderliste", s == 200 and isinstance(d.get("binder"), list), s)
    s, d = ruf("/api/binders", "POST", {"name": "Rauchtest " + time.strftime("%H:%M:%S"), "mode": "custom", "layout": "3x3",
                                        "options": {}, "items": [{"type": "card", "id": "base1-4"}]}, token=TOKEN)
    pruefe("binder anlegen", s == 200 and d.get("id"), d)
    bid = d.get("id") if isinstance(d, dict) else None
    if bid:
        s, b = ruf(f"/api/binders/{bid}", token=TOKEN); pruefe("binder laden", s == 200 and len(b.get("items", [])) == 1, s)
        stand = b.get("updated_at")
        b["items"].append({"type": "empty"})
        s, d = ruf(f"/api/binders/{bid}", "PUT", b, token=TOKEN); pruefe("binder ändern", s == 200 and d.get("updated_at"), d)
        alt = dict(b); alt["updated_at"] = "2000-01-01 00:00:00"
        s, d = ruf(f"/api/binders/{bid}", "PUT", alt, token=TOKEN); pruefe("konflikt = 409", s == 409, (s, d))
        s, d = ruf(f"/api/binders/{bid}/stapel.webp?s=1", token=TOKEN, roh=True); pruefe("stapelbild", s == 200 and d[:4] == b"RIFF", s)
        s, d = ruf(f"/api/binders/{bid}/pdf?variante=checkliste", token=TOKEN, roh=True); pruefe("checklisten-pdf", s == 200 and d[:4] == b"%PDF", s)
        s, d = ruf(f"/api/binders/{bid}", "DELETE", token=TOKEN); pruefe("binder löschen", s == 200, s)
        s, d = ruf(f"/api/binders/{bid}", token=TOKEN); pruefe("gelöscht = 404", s == 404, s)
    s, d = ruf("/api/sammlung/uebersicht", token=TOKEN); pruefe("sammlung/uebersicht", s == 200 and "wert" in d, s)
    s, d = ruf("/api/markt/heute?fenster=30", token=TOKEN); pruefe("markt/heute", s == 200 and (d.get("stand") or d.get("leer")), s)
    s, d = ruf("/api/alarme", token=TOKEN); pruefe("alarme", s == 200, s)
    s, d = ruf("/api/vitrine?bereich=kunst", token=TOKEN); pruefe("vitrine", s == 200, s)

if befunde:
    print(f"RAUCHTEST FEHLGESCHLAGEN – {len(befunde)} Befund(e), {ok} ok:")
    for b in befunde:
        print("  -", b)
    sys.exit(1)
print(f"Rauchtest ok: {ok} Prüfungen{' (mit Konto)' if TOKEN else ' (ohne Konto – BP_TOKEN setzen für die Konto-Wege)'}")
