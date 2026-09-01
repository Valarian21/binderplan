"""KI-Themenseiten: Binderseiten nach dem *Bildinhalt* planen.

Der Kern ist eine Erkenntnis, die kein Feld der Kartendatenbank hergibt: ob eine
Karte zum Thema „Unterwasser" passt, steht nicht im Typ. Garados ist ein
Wasser-Pokémon, sein GX-Artwork zeigt aber nur ein Porträt vor blauem Grund —
Enton dagegen sitzt am Ufer. Deshalb schaut sich ein Vision-Modell jede
Illustration einmal an und legt eine kurze Beschreibung samt Schlagwörtern in
`card_art_tags` ab (Stapel zu 24 Bildern, ~0,035 ct je Karte). Diese Sichtung
passiert einmal je Karte; jede spätere Suche ist reine Datenbankarbeit.

Zwei Wege zur Seite:
  * Thema           — „Pokémon unter Wasser", „Stadt bei Nacht", „Herbstwald"
  * Ankerkarte      — „Ich habe diese Karte, plane mir die Seite drumherum"

Beide landen im selben Suchprofil (Orte, englische Suchwörter, Wasseranteil,
Tageszeit) und derselben Auswahl.
"""

import base64
import io
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi import HTTPException, Request
from PIL import Image

_dep = {}

# Sichtung: klein, günstig, erkennt die Arten zuverlässig (gemessen 09/2026:
# 2.5-flash 0,035 ct/Karte bei guter Qualität, flash-lite 0,006 ct erkennt die
# Pokémon nicht mehr, 3.5-flash 0,41 ct bringt keinen sichtbaren Mehrwert).
SICHT_MODELL = "google/gemini-2.5-flash"
PLAN_MODELL = "google/gemini-2.5-flash"
STAPEL = 24          # Bilder je Anfrage
KANTE = 256          # Kantenlänge der Miniatur in Pixeln

# Feste Ortsliste — sie macht die Suche hart filterbar. Freitext steht daneben in `szene`.
ORTE = ["unterwasser", "gewaesser", "strand", "fluss", "wald", "dschungel", "wiese", "berge",
        "hoehle", "vulkan", "wueste", "schnee", "stadt", "gebaeude", "innenraum", "ruinen",
        "himmel", "weltraum", "dunkelheit", "unterirdisch", "technik", "abstrakt", "portraet", "kampf"]

# Merkmale sind die zweite Achse neben dem Ort: was ist im Bild zu sehen oder was
# tut das Pokémon. Beides zusammen ergibt die Filter der Suche — „Wald" + „Mond"
# + „mehrere_pokemon" ist eine Abfrage, die kein Feld der Kartendatenbank hergibt.
MERKMALE = ["mond", "sterne", "sonne", "sonnenuntergang", "regenbogen", "wolken", "regen", "schnee",
            "gewitter", "nebel", "feuer", "blitz", "eis", "rauch", "blumen", "baeume", "gras",
            "felsen", "wasserfall", "gebaeude", "bruecke", "fahrzeug", "technik", "mensch",
            "mehrere_pokemon", "gegenstand", "essen", "musik", "fliegt", "springt", "rennt",
            "schlaeft", "kampf", "nahaufnahme", "silhouette", "spiegelung", "leuchtet"]

SICHT_PROMPT = (
    "You will see illustrations from Pokémon trading cards, numbered in order. For EACH image judge ONLY what the "
    "artwork depicts (ignore frame, text, HP). Return a JSON object {\"karten\":[{...}]} with one entry per image, in order:\n"
    '{"n": image number,\n'
    ' "szene": one short English sentence describing the setting and what the creature does,\n'
    ' "orte": 1-3 values from this list that match the SETTING: ' + ", ".join(ORTE) + ",\n"
    ' "zeit": "tag"|"nacht"|"daemmerung"|"unklar",\n'
    ' "wasser": 0-3 how strongly water is present (0 none, 1 background, 2 the creature touches it, 3 fully submerged),\n'
    ' "stimmung": two English adjectives,\n'
    ' "figuren": how many creatures are visible in total (1, 2, 3 ...),\n'
    ' "merkmale": every value from this list that is clearly visible or happening (0-8 values, be strict — '
    "only what you actually see): " + ", ".join(MERKMALE) + ",\n"
    ' "farben": 3 dominant colors as hex}\n'
    "Note on merkmale: \"mehrere_pokemon\" only if a second creature is really in the picture (also in the "
    "background), \"mensch\" only for a human figure, \"fliegt\" only if the creature is airborne, "
    "\"nahaufnahme\" if the creature fills almost the whole frame.\n"
    "JSON only, no prose."
)

PROFIL_PROMPT = (
    "Ein Sammler möchte eine Pokémon-Binderseite zu einem Bildthema füllen. Thema: {thema}\n\n"
    "Die Karten sind mit englischen Szenenbeschreibungen erfasst (z. B. \"Vaporeon is swimming gracefully "
    "underwater among some plants\"). Baue daraus ein Suchprofil. Antworte als JSON:\n"
    '{"worte": 8-16 englische Wörter, die in einer passenden Szenenbeschreibung vorkommen würden '
    "(Substantive und Verben, keine Pokémon-Namen, keine Mehrwortphrasen),\n"
    ' "orte": passende Werte aus dieser Liste (0-4): ' + ", ".join(ORTE) + ",\n"
    ' "wasser_min": 0-3 — wie stark Wasser zu sehen sein muss (0 wenn Wasser keine Rolle spielt),\n'
    ' "merkmale": passende Werte aus dieser Liste (0-4, nur was das Thema wirklich verlangt): ' + ", ".join(MERKMALE) + ",\n"
    ' "zeit": "tag"|"nacht"|"daemmerung"|"",\n'
    ' "titel": kurzer deutscher Titel für die Binderseite (max 4 Wörter)}\n'
    "JSON, sonst nichts."
)

AUSWAHL_PROMPT = (
    "Thema der Binderseite: {thema}\n\n"
    "Kandidaten (Nummer | Karte | Set | Szene):\n{liste}\n\n"
    "Wähle genau die {anzahl} Karten, deren ILLUSTRATION am besten zum Thema passt. Der Kartentyp ist egal — "
    "es zählt allein, was das Bild zeigt. Nimm lieber weniger als unpassende Karten. Vermeide dasselbe Pokémon "
    "mehrfach. Ordne die Auswahl so, dass die Seite als Ganzes wirkt: verwandte Motive nebeneinander, "
    "das stärkste Bild in die Mitte.\n"
    'Antwort als JSON: {{"auswahl": [{{"n": Nummer, "grund": "kurze deutsche Begründung, max 6 Wörter"}}], '
    '"titel": "deutscher Titel der Seite, max 4 Wörter"}}'
)


# --- Hilfen ------------------------------------------------------------------

def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def _key():
    key = _dep["env"]().get("OPENROUTER_KEY", "")
    if not key:
        raise RuntimeError("Kein OPENROUTER_KEY in .env")
    return key


def _openrouter(body, timeout=180):
    r = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://binderplan.app", "X-Title": "Binderplan"},
        json={**body, "usage": {"include": True}}, timeout=timeout,
    )
    d = r.json()
    if r.status_code != 200 or d.get("error"):
        raise RuntimeError(f"Modell: {(d.get('error') or {}).get('message') or r.status_code}")
    return d


def _json_aus(d):
    """Antwortinhalt als JSON — das Modell packt gern noch einen ```json-Zaun drumherum."""
    txt = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
    return json.loads(txt)


def _kosten(d):
    return float((d.get("usage") or {}).get("cost") or 0)


def _json_antwort(body, timeout=180, versuche=2):
    """Modell fragen und die Antwort als JSON lesen. Ein zweiter Versuch, weil
    abgeschnittene oder unsauber gequotete Antworten sonst die ganze Suche kippen.
    → (daten, rohantwort, kosten)"""
    letzter = None
    kosten = 0.0
    for _ in range(versuche):
        d = _openrouter(body, timeout)
        kosten += _kosten(d)
        try:
            return _json_aus(d), d, kosten
        except Exception as e:
            letzter = e
    raise RuntimeError(f"Antwort des Modells war unlesbar: {letzter}")


def _liste(wert):
    """Listenfelder robust lesen — dieselbe Frage liefert mal `["wald","schnee"]`,
    mal `"wald, schnee"`. Ohne diese Umschaltung fiel die Volltextsuche still aus."""
    if isinstance(wert, str):
        return [x.strip() for x in re.split(r"[,;/]| und ", wert) if x.strip()]
    if isinstance(wert, (list, tuple)):
        return [str(x).strip() for x in wert if str(x).strip()]
    return []


def _miniatur(card_id):
    pfad = _dep["card_image_path"](card_id, "de")
    if not pfad:
        return None
    try:
        img = Image.open(pfad).convert("RGB")
    except Exception:
        return None
    img.thumbnail((KANTE, KANTE))
    return img


def _data_url(img):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


# --- Sichtung ----------------------------------------------------------------

_schreiben = threading.Lock()


def _ohne_bild(card_ids):
    """Karten ohne beschaffbares Bild vormerken, damit der Index sie nicht ewig neu versucht."""
    if not card_ids:
        return
    with _schreiben:
        con = _dep["get_db"]()
        con.executemany(
            "INSERT OR REPLACE INTO card_art_tags (card_id, szene, orte, zeit, wasser, stimmung, farben,"
            " merkmale, figuren, modell, created_at)"
            " VALUES (?,'','','unklar',0,'','[]','',0,'ohne-bild',?)", [(c, _now()) for c in card_ids])
        con.commit()
        con.close()


def sichten(card_ids):
    """Einen Stapel Karten ansehen und verschlagworten. → (gesichtet, kosten_usd)

    Die Bilder werden nebenläufig geholt — der Download von TCGdex dauert länger
    als der Modellaufruf selbst und war anfangs die Bremse des ganzen Laufs."""
    ids = card_ids[:STAPEL]
    with ThreadPoolExecutor(max_workers=8) as pool:
        bilder = list(pool.map(_miniatur, ids))
    _ohne_bild([c for c, b in zip(ids, bilder) if b is None])

    teile = [{"type": "text", "text": SICHT_PROMPT}]
    dabei = []
    for card_id, img in zip(ids, bilder):
        if not img:
            continue
        dabei.append(card_id)
        teile.append({"type": "text", "text": f"Bild {len(dabei)}:"})
        teile.append({"type": "image_url", "image_url": {"url": _data_url(img)}})
    if not dabei:
        return 0, 0.0

    erg, d, kosten = _json_antwort({"model": _dep["env"]().get("THEMEN_SICHT_MODELL") or SICHT_MODELL,
                                    "messages": [{"role": "user", "content": teile}],
                                    "response_format": {"type": "json_object"}, "max_tokens": 8000})
    _schreiben.acquire()
    con = _dep["get_db"]()
    n = 0
    for e in erg.get("karten", []):
        try:
            card_id = dabei[int(e["n"]) - 1]
        except Exception:
            continue
        orte = [o for o in _liste(e.get("orte")) if o in ORTE]
        merkmale = [m for m in _liste(e.get("merkmale")) if m in MERKMALE]
        try:
            figuren = max(0, min(99, int(e.get("figuren") or 0)))
        except Exception:
            figuren = 0
        if figuren > 1 and "mehrere_pokemon" not in merkmale:
            merkmale.append("mehrere_pokemon")
        con.execute(
            "INSERT OR REPLACE INTO card_art_tags (card_id, szene, orte, zeit, wasser, stimmung, farben,"
            " merkmale, figuren, modell, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (card_id, str(e.get("szene") or "")[:400], " ".join(orte), str(e.get("zeit") or "unklar")[:12],
             max(0, min(3, int(e.get("wasser") or 0))), str(e.get("stimmung") or "")[:80],
             json.dumps(_liste(e.get("farben"))), " ".join(merkmale), figuren,
             d.get("model") or SICHT_MODELL, _now()))
        con.execute("DELETE FROM card_art_fts WHERE card_id = ?", (card_id,))
        con.execute("INSERT INTO card_art_fts (card_id, szene, orte, stimmung, merkmale) VALUES (?,?,?,?,?)",
                    (card_id, str(e.get("szene") or ""), " ".join(orte), str(e.get("stimmung") or ""),
                     " ".join(merkmale)))
        n += 1
    con.commit()
    con.close()
    _schreiben.release()
    return n, kosten


# --- Hintergrund-Sichtung ----------------------------------------------------

_lauf = {"aktiv": False, "gesichtet": 0, "kosten": 0.0, "fehler": "", "start": "", "stop": False, "ziel": 0}


def _offene_karten(limit, scope_ids=None):
    con = _dep["get_db"]()
    if scope_ids is not None:
        marken = ",".join("?" * len(scope_ids))
        rows = con.execute(
            f"SELECT c.id FROM cards c LEFT JOIN card_art_tags t ON t.card_id = c.id"
            f" WHERE t.card_id IS NULL AND c.id IN ({marken}) LIMIT ?", (*scope_ids, limit)).fetchall()
    else:
        # Neue Sets zuerst — dort suchen die meisten, und alte Sets sind kleiner.
        rows = con.execute(
            "SELECT c.id FROM cards c LEFT JOIN card_art_tags t ON t.card_id = c.id"
            " JOIN sets s ON s.id = c.set_id"
            " WHERE t.card_id IS NULL AND COALESCE(c.image_de, c.image_en) IS NOT NULL"
            " ORDER BY s.release_date DESC, c.local_num LIMIT ?", (limit,)).fetchall()
    con.close()
    return [r["id"] for r in rows]


SPUREN = 10         # gleichzeitige Stapel


def _index_schleife(budget_usd, scope_ids):
    """Sichtet, bis nichts mehr offen ist, das Budget aufgebraucht oder gestoppt wurde.

    Sechs Stapel laufen parallel; jeder Stapel holt seine 24 Karten selbst aus der
    Warteschlange, damit kein Arbeiter auf einen langsamen Nachbarn wartet."""
    _lauf.update(aktiv=True, gesichtet=0, kosten=0.0, fehler="", start=_now(), stop=False)
    entnahme = threading.Lock()
    gezogen = set()      # gerade in Arbeit oder eben gescheitert
    patzer = {}          # card_id → Fehlversuche

    def spur():
        while not _lauf["stop"] and _lauf["kosten"] < budget_usd:
            with entnahme:
                # In der DB steht erst nach der Sichtung etwas — bis dahin merkt
                # `gezogen`, welche Karten schon bei einer anderen Spur liegen.
                # Das Fenster wächst um die hängengebliebenen mit: sonst besteht
                # es irgendwann nur noch aus ihnen und der ganze Lauf steht still.
                vorrat = [c for c in _offene_karten(STAPEL * (SPUREN + 2) + len(gezogen), scope_ids)
                          if c not in gezogen]
                offen = vorrat[:STAPEL]
                if not offen:
                    return
                gezogen.update(offen)
            try:
                n, k = sichten(offen)
            except Exception as e:
                _lauf["fehler"] = str(e)[:300]
                with entnahme:
                    # Zweimal daneben heißt: an dieser Karte liegt es. Vormerken,
                    # damit die Warteschlange weiterläuft.
                    hin = []
                    for c in offen:
                        patzer[c] = patzer.get(c, 0) + 1
                        if patzer[c] >= 2:
                            hin.append(c)
                    gezogen.difference_update(c for c in offen if c not in hin)
                if hin:
                    _ohne_bild(hin)
                time.sleep(2)
                continue
            _lauf["gesichtet"] += n
            _lauf["kosten"] += k
            with entnahme:
                # Erledigtes fällt aus `gezogen` — es ist jetzt in der DB und
                # taucht in `_offene_karten` ohnehin nicht mehr auf.
                gezogen.difference_update(offen)

    try:
        with ThreadPoolExecutor(max_workers=SPUREN) as pool:
            for f in [pool.submit(spur) for _ in range(SPUREN)]:
                f.result()
    except Exception as e:
        _lauf["fehler"] = str(e)[:300]
    finally:
        _lauf["aktiv"] = False


# --- Suche -------------------------------------------------------------------

def _profil(thema):
    p, _d, kosten = _json_antwort({"model": _dep["env"]().get("THEMEN_PLAN_MODELL") or PLAN_MODELL,
                                   "messages": [{"role": "user", "content": PROFIL_PROMPT.replace("{thema}", thema[:400])}],
                                   "response_format": {"type": "json_object"}, "max_tokens": 2000}, timeout=60)
    return {
        "worte": [w for w in _liste(p.get("worte")) if re.fullmatch(r"[A-Za-z][A-Za-z'-]{1,20}", w)][:16],
        "orte": [o for o in _liste(p.get("orte")) if o in ORTE][:4],
        "merkmale": [m for m in _liste(p.get("merkmale")) if m in MERKMALE][:4],
        "wasser_min": max(0, min(3, int(p.get("wasser_min") or 0))),
        "zeit": p.get("zeit") if p.get("zeit") in ("tag", "nacht", "daemmerung") else "",
        "titel": str(p.get("titel") or thema)[:60],
    }, kosten


def _profil_aus_karte(card_id):
    """Ankerkarte → Suchprofil. Die Karte wird nötigenfalls sofort gesichtet."""
    con = _dep["get_db"]()
    row = con.execute("SELECT t.*, c.name_de FROM card_art_tags t JOIN cards c ON c.id = t.card_id"
                      " WHERE t.card_id = ?", (card_id,)).fetchone()
    con.close()
    if not row:
        sichten([card_id])
        con = _dep["get_db"]()
        row = con.execute("SELECT t.*, c.name_de FROM card_art_tags t JOIN cards c ON c.id = t.card_id"
                          " WHERE t.card_id = ?", (card_id,)).fetchone()
        con.close()
    if not row:
        raise HTTPException(400, "Diese Karte lässt sich nicht ansehen")
    thema = (f"Karten, deren Artwork zu diesem Bild passt: {row['szene']} "
             f"(Orte: {row['orte']}, Merkmale: {row['merkmale'] or '—'}, {row['zeit']})")
    profil, kosten = _profil(thema)
    # Die Orte der Ankerkarte wiegen schwerer als das, was das Modell dazu erfindet
    profil["orte"] = list(dict.fromkeys((row["orte"] or "").split() + profil["orte"]))[:4]
    profil["wasser_min"] = max(profil["wasser_min"], (row["wasser"] or 0) - 1)
    profil["merkmale"] = [m for m in profil["merkmale"] if m in (row["merkmale"] or "").split()][:3]
    profil["titel"] = f"Passend zu {row['name_de']}"
    return profil, kosten


def _stamm(name):
    """Namensstamm ohne Zusatz — „Glurak-ex", „Glurak V" und „Glurak" sind dasselbe Motiv."""
    return re.split(r"[ -]", (name or "").strip())[0].lower()


def _kandidaten(profil, scope_ids=None, ohne=(), limit=40):
    con = _dep["get_db"]()
    treffer = {}
    if profil["worte"]:
        frage = " OR ".join(f'"{w}"' for w in profil["worte"])
        try:
            for r in con.execute(
                    "SELECT card_id, bm25(card_art_fts) AS rang FROM card_art_fts"
                    " WHERE card_art_fts MATCH ? ORDER BY rang LIMIT 600", (frage,)):
                treffer[r["card_id"]] = -float(r["rang"])
        except Exception:
            treffer = {}
    grob = [*(("orte", o) for o in profil["orte"]), *(("merkmale", m) for m in profil.get("merkmale") or [])]
    if grob:
        marken = " OR ".join(f"{spalte} LIKE ?" for spalte, _ in grob)
        for r in con.execute(f"SELECT card_id FROM card_art_tags WHERE {marken} LIMIT 6000",
                             tuple(f"%{wert}%" for _, wert in grob)):
            treffer.setdefault(r["card_id"], 0.0)
    if not treffer:
        con.close()
        return []

    ids = [i for i in treffer if i not in ohne and (scope_ids is None or i in scope_ids)]
    if not ids:
        con.close()
        return []
    reihen = []
    for teil in [ids[i:i + 900] for i in range(0, len(ids), 900)]:
        marken = ",".join("?" * len(teil))
        reihen += con.execute(
            f"SELECT t.card_id, t.szene, t.orte, t.zeit, t.wasser, t.stimmung, t.farben, t.merkmale,"
            f" c.name_de, c.set_id, c.rarity, c.illustrator, s.name AS set_name"
            f" FROM card_art_tags t JOIN cards c ON c.id = t.card_id LEFT JOIN sets s ON s.id = c.set_id"
            f" WHERE t.card_id IN ({marken})", teil).fetchall()
    con.close()

    bewertet = []
    for r in reihen:
        orte = set((r["orte"] or "").split())
        szene = (r["szene"] or "").lower()
        punkte = treffer.get(r["card_id"], 0.0)
        punkte += 3.0 * len(orte & set(profil["orte"]))
        punkte += 2.5 * len(set((r["merkmale"] or "").split()) & set(profil.get("merkmale") or []))
        punkte += sum(0.8 for w in profil["worte"] if w.lower() in szene)
        if profil["wasser_min"]:
            if (r["wasser"] or 0) < profil["wasser_min"]:
                continue                              # Thema verlangt Wasser — ohne Wasser fliegt die Karte raus
            punkte += 1.4 * (r["wasser"] or 0)
        if profil["zeit"] and r["zeit"] == profil["zeit"]:
            punkte += 1.2
        bewertet.append((punkte, dict(r)))
    bewertet.sort(key=lambda x: -x[0])

    # Vielfalt: höchstens zwei Karten desselben Pokémon in der Vorauswahl
    gesehen, aus = {}, []
    for punkte, r in bewertet:
        st = _stamm(r["name_de"])
        if gesehen.get(st, 0) >= 2:
            continue
        gesehen[st] = gesehen.get(st, 0) + 1
        r["punkte"] = round(punkte, 2)
        aus.append(r)
        if len(aus) >= limit:
            break
    return aus


def _auswahl(thema, kandidaten, anzahl):
    """Letzte Entscheidung trifft das Modell — es sieht die Beschreibungen, nicht nur Zahlen."""
    liste = "\n".join(
        f"{i + 1} | {k['name_de']} | {k.get('set_name') or k['set_id']} | {k['szene']}"
        for i, k in enumerate(kandidaten))
    p, _d, kosten = _json_antwort({"model": _dep["env"]().get("THEMEN_PLAN_MODELL") or PLAN_MODELL,
                                   "messages": [{"role": "user", "content": AUSWAHL_PROMPT.format(
                                       thema=thema[:300], liste=liste[:12000], anzahl=anzahl)}],
                                   "response_format": {"type": "json_object"}, "max_tokens": 3000}, timeout=90)
    aus = []
    for e in (p.get("auswahl") or []):
        try:
            k = kandidaten[int(e["n"]) - 1]
        except Exception:
            continue
        if any(x["card_id"] == k["card_id"] for x in aus):
            continue
        aus.append({**k, "grund": str(e.get("grund") or "")[:80]})
    return aus[:anzahl], str(p.get("titel") or "")[:60], kosten


# --- Einhängen ---------------------------------------------------------------

def register(app, *, get_db, current_user, require_user, ist_pro, card_image_path, env, CACHE, abo, admin_key):
    _dep.update(get_db=get_db, current_user=current_user, require_user=require_user, ist_pro=ist_pro,
                card_image_path=card_image_path, env=env, CACHE=CACHE, abo=abo, admin_key=admin_key)

    con = get_db()
    # Der Volltextindex hat seit der Merkmals-Erweiterung eine Spalte mehr; FTS5-Tabellen
    # lassen sich nicht nachrüsten, also wird er einmalig neu aufgebaut.
    alt_fts = con.execute("SELECT sql FROM sqlite_master WHERE name = 'card_art_fts'").fetchone()
    if alt_fts and "merkmale" not in (alt_fts["sql"] or ""):
        con.executescript("DROP TABLE card_art_fts;")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS card_art_tags (
            card_id TEXT PRIMARY KEY, szene TEXT, orte TEXT, zeit TEXT, wasser INTEGER,
            stimmung TEXT, farben TEXT, modell TEXT, created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_art_tags_wasser ON card_art_tags(wasser);
        CREATE VIRTUAL TABLE IF NOT EXISTS card_art_fts USING fts5(
            card_id UNINDEXED, szene, orte, stimmung, merkmale, tokenize='unicode61'
        );
        """
    )
    for alter in ("ALTER TABLE card_art_tags ADD COLUMN merkmale TEXT",
                  "ALTER TABLE card_art_tags ADD COLUMN figuren INTEGER DEFAULT 0"):
        try:
            con.execute(alter)
        except Exception:
            pass
    # Sichtungen aus der Zeit vor den Merkmalen noch einmal machen — sie sind sonst
    # in jedem Merkmalsfilter unsichtbar. Betrifft nur die ersten Probeläufe.
    con.execute("DELETE FROM card_art_tags WHERE merkmale IS NULL AND modell <> 'ohne-bild'")
    con.commit()
    con.close()

    def _scope_ids(scope):
        """Suchbereich → Menge erlaubter Karten-IDs (None = alle)."""
        if not scope or scope == "alle":
            return None
        con = get_db()
        try:
            if scope.startswith("set:"):
                rows = con.execute("SELECT id FROM cards WHERE set_id = ?", (scope[4:],)).fetchall()
            elif scope.startswith("serie:"):
                rows = con.execute("SELECT c.id FROM cards c JOIN sets s ON s.id = c.set_id"
                                   " WHERE s.serie_id = ?", (scope[6:],)).fetchall()
            elif scope.startswith("binder:"):
                row = con.execute("SELECT items FROM binders WHERE id = ?", (scope[7:],)).fetchone()
                items = json.loads(row["items"]) if row else []
                return {i.get("id") for i in items if i.get("type") == "card" and i.get("id")}
            else:
                return None
        finally:
            con.close()
        return {r["id"] for r in rows}

    def _abdeckung(scope_ids):
        con = get_db()
        if scope_ids is None:
            gesamt = con.execute("SELECT COUNT(*) n FROM cards WHERE COALESCE(image_de, image_en) IS NOT NULL").fetchone()["n"]
            fertig = con.execute("SELECT COUNT(*) n FROM card_art_tags").fetchone()["n"]
        else:
            ids = list(scope_ids)
            gesamt = len(ids)
            fertig = 0
            for teil in [ids[i:i + 900] for i in range(0, len(ids), 900)]:
                marken = ",".join("?" * len(teil))
                fertig += con.execute(f"SELECT COUNT(*) n FROM card_art_tags WHERE card_id IN ({marken})", teil).fetchone()["n"]
        con.close()
        return {"gesichtet": fertig, "gesamt": gesamt}

    def _kontingent(request):
        """Kostenbremse: ohne Pro ein paar Vorschläge am Tag, sonst frei."""
        user = current_user(request)
        if user and ist_pro(user):
            return
        kennung = (str(user["id"]) if user else (request.client.host if request.client else "?"))
        schluessel = f"themen:{time.strftime('%Y-%m-%d')}:{kennung}"
        con = get_db()
        row = con.execute("SELECT value FROM kv WHERE key = ?", (schluessel,)).fetchone()
        n = int(row["value"]) if row else 0
        grenze = 12 if user else 4
        if n >= grenze:
            con.close()
            raise HTTPException(429, detail={"code": "themen_limit", "grenze": grenze})
        con.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?,?)", (schluessel, str(n + 1)))
        con.commit()
        con.close()

    @app.get("/api/themen/status")
    def themen_status(scope: str = ""):
        ab = _abdeckung(_scope_ids(scope))
        return {"aktiv": bool(env().get("OPENROUTER_KEY")), **ab,
                "lauf": {k: v for k, v in _lauf.items() if k != "stop"},
                "orte": ORTE, "merkmale": MERKMALE}

    @app.post("/api/themen/plan")
    async def themen_plan(request: Request):
        if not env().get("OPENROUTER_KEY"):
            raise HTTPException(503, "Themensuche ist gerade nicht verfügbar")
        data = await request.json()
        thema = str(data.get("thema") or "").strip()
        anker = str(data.get("anker") or "").strip()
        if not thema and not anker:
            raise HTTPException(400, "Kein Thema angegeben")
        anzahl = max(1, min(16, int(data.get("anzahl") or 9)))
        scope_ids = _scope_ids(str(data.get("scope") or "alle"))
        ohne = {str(x) for x in (data.get("ohne") or []) if x}
        _kontingent(request)

        kosten = 0.0
        if anker:
            profil, k = _profil_aus_karte(anker)
            ohne.add(anker)
        else:
            profil, k = _profil(thema)
        kosten += k

        kandidaten = _kandidaten(profil, scope_ids, ohne, limit=max(30, anzahl * 4))
        if not kandidaten:
            return {"karten": [], "titel": profil["titel"], "profil": profil,
                    "abdeckung": _abdeckung(scope_ids), "kosten_usd": round(kosten, 5)}
        gewaehlt, titel, k = _auswahl(thema or profil["titel"], kandidaten, anzahl)
        kosten += k
        if not gewaehlt:                       # Modell hat nichts gewählt → Rangliste nehmen
            gewaehlt = [{**k2, "grund": ""} for k2 in kandidaten[:anzahl]]
        return {
            "titel": titel or profil["titel"],
            "profil": profil,
            "abdeckung": _abdeckung(scope_ids),
            "kosten_usd": round(kosten, 5),
            "karten": [{"id": k2["card_id"], "name": k2["name_de"], "set": k2.get("set_name") or k2["set_id"],
                        "szene": k2["szene"], "grund": k2.get("grund") or "", "orte": (k2["orte"] or "").split(),
                        "merkmale": (k2.get("merkmale") or "").split(),
                        "illustrator": k2.get("illustrator") or ""} for k2 in gewaehlt],
        }

    @app.post("/api/themen/sichten")
    def themen_sichten(key: str = "", budget: float = 2.0, scope: str = "", stop: bool = False):
        """Hintergrund-Sichtung starten oder stoppen (Wartung, daher mit Admin-Schlüssel)."""
        if not admin_key() or key != admin_key():
            raise HTTPException(403)
        if stop:
            _lauf["stop"] = True
            return {"ok": True, "gestoppt": True}
        if _lauf["aktiv"]:
            raise HTTPException(409, "Läuft bereits")
        ids = _scope_ids(scope)
        threading.Thread(target=_index_schleife, args=(max(0.05, min(60.0, budget)), ids), daemon=True).start()
        time.sleep(0.3)
        return {"ok": True, "lauf": {k: v for k, v in _lauf.items() if k != "stop"}}

    def kennzahlen():
        con = get_db()
        n = con.execute("SELECT COUNT(*) n FROM card_art_tags").fetchone()["n"]
        con.close()
        return n

    return kennzahlen
