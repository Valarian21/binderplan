"""Ein Testlauf der Masken-Pipeline an der Bisasam-Reihe (ART 151, Seite 2) – ohne DB-Eintrag,
ohne Credits, ohne main.py zu importieren (das startete den Autosync-Thread).
  --vorbereiten  nur Vorlage, Maske, Eingabe und Prompt erzeugen (≈ 0,5 ct Text)
Ein vorhandener prompt.txt im Ausgabeordner wird wiederverwendet."""
import sys, json, pathlib, re, sqlite3
sys.path.insert(0, "/root/apps/binderplan")
import httpx
import artwork as A
import artwork_maske as AM

BASE = pathlib.Path("/root/apps/binderplan")
CACHE = BASE / "cache"
def get_db():
    con = sqlite3.connect(BASE / "app.db", timeout=30); con.row_factory = sqlite3.Row; return con
def env():
    out = {}
    for line in (BASE / ".env").read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1); out[k.strip()] = v.strip()
    return out
_V4 = httpx.Client(transport=httpx.HTTPTransport(local_address="0.0.0.0"), timeout=20, follow_redirects=True)
def card_image_path(card_id, lang="de"):
    safe = re.sub(r"[^A-Za-z0-9._%-]", "_", card_id); suffix = "" if lang != "en" else ".en"
    ziel = CACHE / "cards" / "high" / f"{safe}{suffix}.webp"
    if ziel.exists(): return ziel
    con = get_db(); row = con.execute("SELECT image_de, image_en FROM cards WHERE id=?", (card_id,)).fetchone(); con.close()
    for u in ([row["image_de"], row["image_en"]] if lang != "en" else [row["image_en"], row["image_de"]]):
        if u:
            r = _V4.get(f"{u}/high.webp")
            if r.status_code == 200 and r.content: ziel.write_bytes(r.content); return ziel
    return None
A._dep.update(get_db=get_db, env=env, CACHE=CACHE, card_image_path=card_image_path, dex_image_path=None, abo=None)

OUT = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else ".")
OUT.mkdir(parents=True, exist_ok=True)
nur = "--vorbereiten" in sys.argv
if (OUT / "prompt.txt").exists():
    fest = (OUT / "prompt.txt").read_text().strip()
    AM.szene_prompt = lambda *a, **k: (fest, 0.0)
    print("Prompt aus Datei wiederverwendet.")

con = get_db()
b = con.execute("SELECT * FROM binders WHERE id='JAebe7ZoeTI'").fetchone(); con.close()
items = json.loads(b["items"]); cols, rows = [int(v) for v in b["layout"].split("x")]
seite_idx, pp = 1, cols * rows
anker = {str(i): it["id"] for i, it in enumerate(items[seite_idx * pp:(seite_idx + 1) * pp]) if it and it.get("type") == "card"}
print("Anker:", anker)
erg = AM.seite_malen(anker, cols, rows, lang="de", stil="karte", wunsch="", groesse="2K", log=print, nur_vorbereiten=nur)
erg["vorlage"].save(OUT / "vorlage.png"); erg["maske"].save(OUT / "maske.png"); erg["eingabe"].save(OUT / "eingabe.png")
(OUT / "prompt.txt").write_text(erg["prompt"])
print("Leinwand:", erg["geo"]["cw"], "x", erg["geo"]["ch"], "| Fenster:", erg["fenster"])
print("Prompt:", erg["prompt"])
print("Kosten bisher:", round(erg["kosten"], 4), "$")
if erg["seite"] is not None:
    erg["seite"].save(OUT / "seite.png", "PNG", optimize=True)
    v = erg["seite"].copy(); v.thumbnail((1080, 1080)); v.save(OUT / "seite_vorschau.jpg", "JPEG", quality=90)
    print("Fertig:", erg["seite"].size, "Modell:", erg.get("modell"), "Gesamtkosten:", round(erg["kosten"], 4), "$")
    print(json.dumps(erg["schritte"], ensure_ascii=False)[:800])
