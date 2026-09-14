"""Binderplan – „passende Karten": Rangfolge nach Farbgebung und Bildmotiv.

Ein Abschnitt der main.py in eigener Datei (wie katalog.py, bilder.py): wird in deren
Namensraum ausgeführt, kein importierbares Modul.

Die Frage, die hier beantwortet wird: *auf der Seite liegen schon eine oder mehrere Karten —
welche Karten aus dem Katalog passen dazu?* Zwei Achsen:

* **Farbe** aus `card_palette` (vier gemessene Farben je Karte, Lab + Flächenanteil;
  gefüllt von `scripts/paletten.py`, ohne Modellaufruf, 20 ms je Karte).
* **Motiv** aus `card_art_tags` (Orte, Merkmale, Tageszeit, Wasser — derselbe Bildmotiv-Index,
  der auch „Seite nach Thema" trägt).

Der Unterschied zu `themen.py`: dort beschreibt ein Mensch ein Thema in Worten und ein
Textmodell baut daraus ein Suchprofil. Hier sind die Karten auf der Seite selbst die Frage,
es fällt kein Modellaufruf an, und die Antwort kommt in Millisekunden — deshalb kann sie in
der normalen Trefferliste links stehen und sich bei jeder Änderung der Seite erneuern.

Angebunden ist das an `/api/cards` (`passend_zu=`, `passend_modus=`) statt an einen eigenen
Endpunkt: so gelten alle vorhandenen Filter weiter, und Ziehen ins Fach, Preise, „hab ich",
Wunschliste, Ablage und „+ Alle Treffer" funktionieren unverändert mit.
"""

import heapq  # noqa: E402  (Abschnitt der main.py; dort nicht importiert)

# --- Einstellgrößen ----------------------------------------------------------

PASSEND_MAX = 600            # so lang ist die Rangliste; der Rest ist nie erreichbar
PASSEND_JE_STAMM = 2         # höchstens zwei Karten desselben Pokémon in der Liste
PASSEND_SEITENFARBEN = 10    # so viele Farben behält die Seite, egal wie viele Karten darauf liegen
PASSEND_VORAUSWAHL = 2500    # so viele Karten gehen aus der groben in die genaue Runde
PASSEND_GEWICHT = {"farbe": 1.0, "beides": 0.55, "motiv": 0.0}
# Farbabstand → Punkte: 0 Lab = 100 Punkte, ab PASSEND_SKALA = 0 Punkte. Gemessen an 4.000
# zufälligen Kartenpaaren (14.09.2026): Median 22,2, oberes Viertel ab 28,5, 95 % unter 40,8.
# Der beste Treffer aus 800 Kandidaten liegt im Median bei 7,5. Mit 30 heißt das: ein
# zufälliges Paar bekommt rund 26 Punkte, ein guter Treffer 75, ein sehr guter über 90 —
# die Schwelle „grün" im Abzeichen (70) trennt damit echte Treffer von Zufall.
PASSEND_SKALA = 30.0
# Energiekarten haben kein Motiv, zu dem etwas passen könnte, und lägen über ihre ruhige
# Fläche ständig weit vorn.
PASSEND_OHNE_KATEGORIE = ("Energy",)
# Trainerkarten ohne Seltenheit sind Beilagen aus Themendecks und japanischen Sets
# („Energy Search" aus tk-dp-m). Sie haben eine kleine Illustration in einer großen weißen
# Fläche, treffen damit jede helle Seite und will trotzdem niemand im Binder haben — 233
# Stück im Bestand. Full-Art-Trainer, Charakter-Rares und Gold-Trainer tragen eine
# Seltenheit und bleiben drin.
PASSEND_TRAINER_OHNE = ("Trainer",)

# --- Daten im Speicher -------------------------------------------------------
# 10.000 Karten mit vier Farben sind rund 3 MB. Einmal laden und im Prozess halten ist
# billiger, als sie bei jedem Tastendruck in der Suche aus SQLite zu ziehen. Nachgeladen
# wird, sobald die Tabelle gewachsen ist — der Messlauf schreibt nebenher weiter.

_pas = {"karten": {}, "anzahl": -1, "geprueft": 0.0}


def passend_daten():
    """→ {card_id: dict} mit Palette (Lab + Anteil), Name, Kategorie und Motivmarken."""
    jetzt = time.time()
    if _pas["karten"] and jetzt - _pas["geprueft"] < 60:
        return _pas["karten"]
    con = get_db()
    try:
        anzahl = con.execute("SELECT COUNT(*) c FROM card_palette").fetchone()["c"]
    except sqlite3.Error:
        con.close()
        return {}
    _pas["geprueft"] = jetzt
    if anzahl == _pas["anzahl"]:
        con.close()
        return _pas["karten"]
    karten = {}
    for r in con.execute(
            "SELECT p.card_id, p.lab, c.name_de, c.name_en, c.category, c.rarity,"
            "       t.orte, t.merkmale, t.zeit, t.wasser"
            " FROM card_palette p"
            " JOIN cards c ON c.id = p.card_id"
            " LEFT JOIN card_art_tags t ON t.card_id = p.card_id"):
        try:
            labs = [(x[0], x[1], x[2], x[3]) for x in json.loads(r["lab"])]
        except (ValueError, TypeError, IndexError):
            continue
        if not labs:
            continue
        karten[r["card_id"]] = {
            "lab": labs,
            "stamm": _pas_stamm(r["name_de"] or r["name_en"]),
            "kategorie": r["category"] or "",
            "selten": r["rarity"] or "None",
            "orte": (r["orte"] or "").split(),
            "merkmale": (r["merkmale"] or "").split(),
            "zeit": r["zeit"] or "",
            "wasser": r["wasser"] or 0,
        }
    con.close()
    _pas["karten"], _pas["anzahl"] = karten, anzahl
    log.info("Farbpaletten geladen: %d Karten", len(karten))
    return karten


def _pas_stamm(name):
    """Namensstamm — „Glurak-ex", „Glurak V" und „Glurak" sind dasselbe Motiv."""
    return re.split(r"[ -]", (name or "").strip())[0].lower()


# Muss zum Zuschnitt in scripts/paletten.py passen, sonst sind gemessene und nachgemessene
# Karten nicht vergleichbar.
PASSEND_AUSSCHNITT = (0.12, 0.14, 0.88, 0.45)


def _pas_lab(r, g, b):
    """sRGB (0-255) → CIE-Lab; dort entsprechen Abstände ungefähr dem, was das Auge sieht."""
    def f(u):
        u /= 255
        return u / 12.92 if u <= 0.04045 else ((u + 0.055) / 1.055) ** 2.4
    r, g, b = f(r), f(g), f(b)
    x = (r * .4124 + g * .3576 + b * .1805) / .95047
    y = r * .2126 + g * .7152 + b * .0722
    z = (r * .0193 + g * .1192 + b * .9505) / 1.08883

    def h(t):
        return t ** (1 / 3) if t > .008856 else 7.787 * t + 16 / 116
    x, y, z = h(x), h(y), h(z)
    return round(116 * y - 16, 2), round(500 * (x - y), 2), round(200 * (y - z), 2)


def passend_nachmessen(card_id):
    """Die Palette einer einzelnen Karte jetzt messen und speichern → True bei Erfolg.

    Der Messlauf deckt nur die Seltenheiten ab, die auf gestalteten Seiten vorkommen
    (`scripts/paletten.py`). Im Fach liegen darf aber jede Karte — auch eine Common aus dem
    Grundset. Ohne diesen Weg fiele die Farbsuche für so eine Seite still aus, und der Nutzer
    sähe nur eine leere Liste. 20 ms, plus einmalig das Bild."""
    pfad = _card_image_path(card_id, "de", "low")
    if not pfad:
        return False
    try:
        with Image.open(pfad) as roh:
            im = roh.convert("RGB")
            w, h = im.size
            if w < 40 or h < 56:
                return False
            im = im.crop((int(w * PASSEND_AUSSCHNITT[0]), int(h * PASSEND_AUSSCHNITT[1]),
                          int(w * PASSEND_AUSSCHNITT[2]), int(h * PASSEND_AUSSCHNITT[3])))
            im.thumbnail((72, 72))
            q = im.quantize(colors=6, method=Image.MEDIANCUT)
        pal = q.getpalette()
        zaehl = sorted(q.getcolors() or [], reverse=True)
        if not zaehl:
            return False
        gesamt = sum(n for n, _ in zaehl)
        labs, hexe = [], []
        for n, i in zaehl[:4]:
            rgb = tuple(pal[i * 3:i * 3 + 3])
            labs.append([*_pas_lab(*rgb), round(n / gesamt, 4)])
            hexe.append("#%02x%02x%02x" % rgb)
    except Exception:
        log.warning("Palette für %s nicht messbar", card_id)
        return False
    hell = sum(x[0] * x[3] for x in labs) / max(1e-6, sum(x[3] for x in labs))
    bunt = sum((x[1] ** 2 + x[2] ** 2) ** 0.5 * x[3] for x in labs) / max(1e-6, sum(x[3] for x in labs))
    con = get_db()
    con.execute("INSERT OR REPLACE INTO card_palette"
                " (card_id, lab, hex, helligkeit, buntheit, ausschnitt, gemessen_am)"
                " VALUES (?,?,?,?,?,?,datetime('now'))",
                (card_id, json.dumps(labs), json.dumps(hexe), round(hell, 2), round(bunt, 2),
                 ",".join(str(x) for x in PASSEND_AUSSCHNITT)))
    con.commit()
    con.close()
    _pas["anzahl"] = -1          # beim nächsten Zugriff neu laden
    _pas["geprueft"] = 0.0
    return True


def _pas_abstand(a, b):
    """Abstand zweier Paletten in Lab, symmetrisch und nach Flächenanteil gewichtet.

    Jede Farbe von A sucht ihre nächste in B (und umgekehrt) und geht mit ihrem Flächenanteil
    ein: ein Farbtupfer in der Ecke zählt weniger als der Himmel über dem halben Bild. Nur in
    eine Richtung zu messen wäre unfair — eine graue Karte läge dann nah an jeder bunten, weil
    ihr einziges Grau irgendwo immer vorkommt.

    Ausgeschriebene Schleifen statt `min(… for …)`: dieselbe Rechnung, aber ohne den
    Generator je Farbpaar. Über 7.224 Karten gemessen 209 statt 484 ms, Ergebnis identisch."""
    s = 0.0
    for L, x, y, g in a:
        m = 1e18
        for L2, x2, y2, _ in b:
            dl = L - L2
            dx = x - x2
            dy = y - y2
            d = dl * dl + dx * dx + dy * dy
            if d < m:
                m = d
        s += g * m ** 0.5
    for L, x, y, g in b:
        m = 1e18
        for L2, x2, y2, _ in a:
            dl = L - L2
            dx = x - x2
            dy = y - y2
            d = dl * dl + dx * dx + dy * dy
            if d < m:
                m = d
        s += g * m ** 0.5
    return s / 2


def _pas_grob(lab, seite):
    """Vorauswahl: nur die größte Farbe der Karte gegen die Seitenpalette, quadriert und ohne
    Wurzel. Wer damit weit weg ist, kann auch im vollen Abstand nicht vorn landen — die
    Top-20 waren in der Gegenprobe an allen 20 Plätzen identisch, und die Rangfolge kostet
    71 statt 209 ms. Erst ab Platz 500 weicht die Liste überhaupt ab."""
    L, x, y, _ = lab[0]
    m = 1e18
    for L2, x2, y2, _ in seite:
        dl = L - L2
        dx = x - x2
        dy = y - y2
        d = dl * dl + dx * dx + dy * dy
        if d < m:
            m = d
    return m


def _pas_seitenpalette(anker, karten):
    """Die Farben der Karten auf der Seite zu einer Palette zusammenfassen.

    Nicht mitteln — der Mittelwert aus Rot und Blau ist Grau, und danach passt nichts mehr.
    Stattdessen alle Farben nebeneinanderlegen und ihre Anteile durch die Kartenzahl teilen:
    eine Seite aus einer roten und einer blauen Karte sucht Karten, die rot ODER blau sind."""
    dabei = [i for i in anker if i in karten]
    if not dabei:
        return []
    farben = [[L, a, b, g / len(dabei)] for i in dabei for L, a, b, g in karten[i]["lab"]]
    # Neun Ankerkarten ergäben 36 Farben; jeder Vergleich würde damit neunmal so teuer, ohne
    # dass die Seite neun verschiedene Farbwelten hätte. Die beiden jeweils ähnlichsten werden
    # deshalb zusammengelegt (nach Fläche gemittelt), bis höchstens PASSEND_SEITENFARBEN übrig
    # sind — was am Ende übrig bleibt, sind die Farben, die die Seite wirklich ausmachen.
    while len(farben) > PASSEND_SEITENFARBEN:
        best, bi, bj = None, 0, 1
        for i in range(len(farben)):
            for j in range(i + 1, len(farben)):
                dl = farben[i][0] - farben[j][0]
                dx = farben[i][1] - farben[j][1]
                dy = farben[i][2] - farben[j][2]
                d = dl * dl + dx * dx + dy * dy
                if best is None or d < best:
                    best, bi, bj = d, i, j
        A, B = farben[bi], farben[bj]
        gg = A[3] + B[3]
        farben[bi] = [(A[0] * A[3] + B[0] * B[3]) / gg, (A[1] * A[3] + B[1] * B[3]) / gg,
                      (A[2] * A[3] + B[2] * B[3]) / gg, gg]
        farben.pop(bj)
    return [tuple(f) for f in farben]


def _pas_motivprofil(anker):
    """Was die Karten auf der Seite zeigen, als zählendes Profil — oder None ohne Motivdaten."""
    if not anker:
        return None
    con = get_db()
    marken = ",".join("?" * len(anker))
    reihen = con.execute(
        f"SELECT orte, merkmale, zeit, wasser FROM card_art_tags WHERE card_id IN ({marken})",
        list(anker)).fetchall()
    con.close()
    if not reihen:
        return None
    orte, merkmale, zeiten, wasser = {}, {}, {}, []
    for r in reihen:
        for o in (r["orte"] or "").split():
            orte[o] = orte.get(o, 0) + 1
        for m in (r["merkmale"] or "").split():
            merkmale[m] = merkmale.get(m, 0) + 1
        if r["zeit"] and r["zeit"] != "unklar":
            zeiten[r["zeit"]] = zeiten.get(r["zeit"], 0) + 1
        wasser.append(r["wasser"] or 0)
    n = len(reihen)
    profil = {
        "orte": {k: v / n for k, v in orte.items()},
        "merkmale": {k: v / n for k, v in merkmale.items()},
        "zeit": max(zeiten, key=zeiten.get) if zeiten else "",
        "wasser": sum(wasser) / len(wasser) if wasser else 0,
    }
    # Wogegen normiert wird: was eine Karte höchstens erreichen kann, wenn sie jeden Ort und
    # jedes Merkmal der Seite teilt. Ohne das hinge die Punktzahl daran, wie viele Marken die
    # Ankerkarten zufällig tragen.
    profil["max"] = (3.0 * sum(profil["orte"].values()) + 2.0 * sum(profil["merkmale"].values())
                     + (1.5 if profil["zeit"] else 0) + 1.0)
    return profil if profil["max"] > 1.0 else None


def _pas_motivpunkte(profil, karte):
    """→ (0–100, kurzer Grund). Geteilte Orte zählen am meisten, dann Merkmale, dann
    Tageszeit und der Wasseranteil."""
    p, grund = 0.0, []
    for o in karte["orte"]:
        if o in profil["orte"]:
            p += 3.0 * profil["orte"][o]
            grund.append(o)
    for m in karte["merkmale"]:
        if m in profil["merkmale"]:
            p += 2.0 * profil["merkmale"][m]
            grund.append(m)
    if profil["zeit"] and karte["zeit"] == profil["zeit"]:
        p += 1.5
        grund.append(profil["zeit"])
    p += 1.0 * max(0.0, 1 - abs(karte["wasser"] - profil["wasser"]) / 3)
    return min(100.0, 100.0 * p / profil["max"]), " · ".join(grund[:3])


def passend_rangliste(anker, modus="beides", ohne=(), grenze=PASSEND_MAX):
    """→ [(card_id, punkte 0-100, grund)] absteigend, höchstens `grenze` Einträge.

    Fehlt den Ankerkarten die Farbe, bleibt das Motiv; fehlt beides, kommt nichts zurück —
    dann zeigt die Oberfläche einen Hinweis statt einer stillen leeren Liste."""
    anker = [a for a in dict.fromkeys(anker) if a]
    if not anker:
        return []
    gewicht = PASSEND_GEWICHT.get(modus, PASSEND_GEWICHT["beides"])
    karten = passend_daten()
    # Ankerkarten, die der Messlauf nicht erfasst hat (jede Seltenheit darf im Fach liegen),
    # jetzt nachmessen — sonst fiele die Farbsuche für diese Seite still aus.
    if gewicht > 0:
        fehlt = [a for a in anker if a not in karten]
        if fehlt and any(passend_nachmessen(a) for a in fehlt[:9]):
            karten = passend_daten()
    seite = _pas_seitenpalette(anker, karten) if gewicht > 0 else []
    profil = _pas_motivprofil(anker) if gewicht < 1 else None
    if not seite and not profil:
        return []
    if not seite:
        gewicht = 0.0
    elif not profil:
        gewicht = 1.0

    raus = set(anker) | set(ohne or ())
    kandidaten = [(cid, k) for cid, k in karten.items()
                  if cid not in raus and k["kategorie"] not in PASSEND_OHNE_KATEGORIE
                  and not (k["kategorie"] in PASSEND_TRAINER_OHNE and k["selten"] == "None")]
    if gewicht > 0 and len(kandidaten) > PASSEND_VORAUSWAHL:
        kandidaten = heapq.nsmallest(PASSEND_VORAUSWAHL, kandidaten,
                                     key=lambda ck: _pas_grob(ck[1]["lab"], seite))
    bewertet = []
    for cid, k in kandidaten:
        punkte, grund = 0.0, ""
        if gewicht > 0:
            punkte = gewicht * max(0.0, 100.0 * (1 - _pas_abstand(seite, k["lab"]) / PASSEND_SKALA))
        if gewicht < 1:
            m, grund = _pas_motivpunkte(profil, k)
            punkte += (1 - gewicht) * m
        bewertet.append((punkte, cid, grund))
    bewertet.sort(key=lambda x: -x[0])

    # Vielfalt: sonst stehen acht Ausprägungen desselben Glurak nebeneinander und die Seite
    # sieht aus wie ein Versehen. Dieselbe Regel wie in themen.py.
    gesehen, aus = {}, []
    for punkte, cid, grund in bewertet:
        st = karten[cid]["stamm"]
        if gesehen.get(st, 0) >= PASSEND_JE_STAMM:
            continue
        gesehen[st] = gesehen.get(st, 0) + 1
        aus.append((cid, round(punkte, 1), grund))
        if len(aus) >= grenze:
            break
    return aus


@app.get("/api/passend/probe")
def passend_probe(request: Request, ids: str = "", modus: str = "beides", anzahl: int = 12):
    """Die Rangfolge ohne den Umweg über die Kartensuche — zum Messen und zum Nachsehen,
    warum eine Karte weit vorn steht. Braucht ein Konto, kostet nichts."""
    _require_user(request)
    anker = [x for x in (ids or "").split(",") if x][:9]
    t0 = time.time()
    liste = passend_rangliste(anker, modus, grenze=max(1, min(anzahl, 60)))
    namen = {}
    if liste:
        con = get_db()
        marken = ",".join("?" * len(liste))
        for r in con.execute(f"SELECT id, name_de, name_en, set_id FROM cards WHERE id IN ({marken})",
                             [c for c, _, _ in liste]):
            namen[r["id"]] = f'{r["name_de"] or r["name_en"]} ({r["set_id"]})'
        con.close()
    return {"dauer_ms": round((time.time() - t0) * 1000),
            "paletten": len(passend_daten()),
            "treffer": [{"id": c, "punkte": p, "grund": g, "name": namen.get(c, c)}
                        for c, p, g in liste]}
