"""
Auswertungen zu Preisen — für die eigene Sammlung und für den Markt.

Zwei Bereiche auf einer Datengrundlage:

* **Sammlung** (`/api/analytics/sammlung`): was jemand besitzt, was es wert ist, was er dafür
  bezahlt hat, aufgeschlüsselt nach Seltenheit, Set, Pokémon, Typ, Art und Jahrzehnt.
* **Markt** (`/api/analytics/markt`): der ganze Katalog — Wertverteilung, teuerste Karten,
  Sets im Vergleich, Indizes für alte und neue Karten und der Abstand zwischen europäischen
  und amerikanischen Preisen.

Gerechnet wird auf `card_prices` (aktueller Stand) und `price_history` (eine Zeile je Karte
und Tag aus dem nächtlichen Preislauf in main.py). Gegradete Preise und Population fehlen
bewusst: PSA lässt sich nicht abfragen, und die freien Quellen dafür sind fremde Datenbanken
ohne Nutzungsrecht — eine Marktkapitalisierung wäre geraten, nicht gemessen.

Eine Regel zieht sich durch: **keine Zahl ohne ihre Grundlage.** Jede Antwort nennt, auf wie
vielen Karten sie beruht und über wie viele Tage die Reihe reicht. Ein Wertverlauf über drei
Tage sähe sonst aus wie eine Aussage über den Markt.
"""

import json
import time

# Eine Verlaufskurve wird erst gezeigt, wenn sie über so viele Tage reicht. Darunter ist
# jede Steigung Rauschen aus dem Erfassungslauf und keine Marktbewegung.
MIN_TAGE = 3
# Für die Indizes: so viele Karten müssen die Reihe von Anfang bis Ende tragen.
MIN_BASIS = 20
# Ein Tag zählt erst als Messpunkt, wenn er so viele Karten trägt. Der allererste Eintrag
# der Historie stammt von einer einzelnen angesehenen Karte — als Vergleichsanfang wäre er
# wertlos und würde jede Bewegungsliste auf eine Karte zusammenschrumpfen.
MIN_ZEILEN_TAG = 50
# Preisabstand zwischen den Börsen, ab dem wir von einem Datenfehler ausgehen statt von
# einem Marktunterschied: bei über 70 % vergleicht TCGdex fast immer zwei verschiedene
# Drucke derselben Karte, nicht zwei Preise für denselben.
MAX_ABSTAND = 70

AERA_GRENZE_ALT = "2003-12-31"     # WotC und die frühen Nintendo-Sets
AERA_GRENZE_NEU = "2020-01-01"     # ab Schwert & Schild

# Wertklassen für die Verteilung im Katalog (Untergrenze, Beschriftung)
KLASSEN = [(0, "unter 1 €"), (1, "1–5 €"), (5, "5–20 €"), (20, "20–50 €"),
           (50, "50–150 €"), (150, "150–500 €"), (500, "über 500 €")]


def _heute():
    return time.strftime("%Y-%m-%d", time.gmtime())


def _tage_zurueck(n):
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() - n * 86400))


def _messtage(con, ab=None):
    """Tage der Historie, die genug Karten tragen, um als Messpunkt zu gelten."""
    bed, args = "", ()
    if ab:
        bed, args = " WHERE datum >= ?", (ab,)
    return [r["datum"] for r in con.execute(
        f"SELECT datum FROM price_history{bed} GROUP BY datum HAVING COUNT(*) >= ?"
        f" ORDER BY datum", (*args, MIN_ZEILEN_TAG))]


def _reihe(con, ids, tage=90, gewicht=None):
    """Wertverlauf einer Kartenmenge auf fester Basis.

    Gezählt werden nur Karten, die am ersten *und* am letzten Tag einen Preis haben. Ohne
    diese feste Basis stiege jede Kurve allein dadurch, dass mit der Zeit mehr Karten erfasst
    wurden — das sähe aus wie Wertzuwachs und wäre keiner. `gewicht` ist die Stückzahl je
    Karte (für die Sammlung); ohne Angabe zählt jede Karte einmal.
    """
    if not ids:
        return {"punkte": [], "basis": 0}
    von = _tage_zurueck(tage)
    gueltig = set(_messtage(con, von))
    if len(gueltig) < MIN_TAGE:
        return {"punkte": [], "basis": 0, "zu_kurz": True, "tage": len(gueltig)}
    roh = []
    ids = list(dict.fromkeys(ids))
    for start in range(0, len(ids), 400):
        teil = ids[start:start + 400]
        marken = ",".join("?" * len(teil))
        roh += [(r["datum"], r["card_id"], r["eur"]) for r in con.execute(
            f"SELECT datum, card_id, eur FROM price_history"
            f" WHERE card_id IN ({marken}) AND datum >= ? AND eur IS NOT NULL", (*teil, von))]
    if not roh:
        return {"punkte": [], "basis": 0}

    nach_tag = {}
    for datum, cid, eur in roh:
        if datum in gueltig:
            nach_tag.setdefault(datum, {})[cid] = eur
    if not nach_tag:
        return {"punkte": [], "basis": 0}
    tage_sortiert = sorted(nach_tag)
    if len(tage_sortiert) < MIN_TAGE:
        return {"punkte": [], "basis": 0, "zu_kurz": True, "tage": len(tage_sortiert)}

    basis = set(nach_tag[tage_sortiert[0]]) & set(nach_tag[tage_sortiert[-1]])
    if len(basis) < 3:
        return {"punkte": [], "basis": len(basis), "zu_kurz": True, "tage": len(tage_sortiert)}

    letzte, punkte = {}, []
    for datum in tage_sortiert:
        letzte.update({k: v for k, v in nach_tag[datum].items() if k in basis})
        if len(letzte) < len(basis):
            continue          # noch nicht jede Karte der Basis hat einen Wert
        summe = sum(v * (gewicht.get(k, 1) if gewicht else 1) for k, v in letzte.items())
        punkte.append({"datum": datum, "eur": round(summe, 2)})
    return {"punkte": punkte, "basis": len(basis), "tage": len(tage_sortiert)}


def _oder_leer(v):
    """Fehlende Angaben bekommen ein Zeichen, das die Oberfläche übersetzt — sonst fielen
    sie stillschweigend aus der Aufstellung und die Summe stimmte nicht mehr mit dem
    Gesamtwert überein. Die Zeichenkette \"None\" kommt aus dem Katalog selbst."""
    if v is None or v == "" or v == "None":
        return "?"
    return v


def _gruppieren(zeilen, schluessel, wert, stueck=None, grenze=10):
    """Summe und Stückzahl je Gruppe, absteigend nach Wert, auf `grenze` gekürzt."""
    summe = {}
    for z in zeilen:
        k = schluessel(z)
        if not k:
            continue
        a, b = summe.get(k, (0, 0))
        summe[k] = (a + (wert(z) or 0), b + (stueck(z) if stueck else 1))
    liste = sorted(summe.items(), key=lambda x: -x[1][0])
    rest = liste[grenze:]
    aus = [{"name": k, "wert": round(v[0], 2), "anzahl": v[1]} for k, v in liste[:grenze]]
    if rest:
        aus.append({"name": "übrige", "wert": round(sum(v[0] for _, v in rest), 2),
                    "anzahl": sum(v[1] for _, v in rest), "rest": len(rest)})
    return aus


def register(app, *, get_db, require_user, ist_pro, ist_pro_stufe=None):
    # Die Sammlungsauswertung gehört zum Sammeln und steckt schon in Plus; die Marktzahlen
    # sind Händlerwerkzeug und bleiben Pro vorbehalten. Fehlt die Unterscheidung (ältere
    # Einbindung), gilt für beide dieselbe Schranke.
    ist_markt_erlaubt = ist_pro_stufe or ist_pro
    from fastapi import Request

    # ---------------------------------------------------------------- Sammlung

    @app.get("/api/analytics/sammlung")
    def sammlung_analyse(request: Request, tage: int = 90):
        """Alles zur eigenen Sammlung."""
        user = require_user(request)
        if not ist_pro(user):
            return {"pro": False}
        tage = max(7, min(365, tage))
        con = get_db()

        besitz = [dict(r) for r in con.execute(
            "SELECT s.card_id, s.variante, s.anzahl, s.zustand, s.kaufpreis, s.gekauft_am,"
            " c.name_de, c.name_en, c.rarity, c.set_id, c.types, c.first_dex, c.release_date,"
            " c.category, c.region, c.local_id,"
            " (SELECT name FROM sets WHERE sets.id = c.set_id) AS set_name,"
            " p.eur, p.eur_holo, p.usd"
            " FROM sammlung s JOIN cards c ON c.id = s.card_id"
            " LEFT JOIN card_prices p ON p.card_id = s.card_id"
            " WHERE s.user_id = ? AND s.anzahl > 0", (user["id"],))]
        if not besitz:
            con.close()
            return {"pro": True, "leer": True}

        # Holo- und Reverse-Exemplare kosten mehr; kennt Cardmarket dafür einen eigenen
        # Preis, gilt der.
        def preis(z):
            if z["variante"] in ("holo", "reverse") and z["eur_holo"]:
                return z["eur_holo"]
            return z["eur"]

        def stk(z):
            return z["anzahl"] or 0

        def wert_z(z):
            return (preis(z) or 0) * stk(z)

        wert = sum(wert_z(z) for z in besitz)
        stueck = sum(stk(z) for z in besitz)
        ohne_preis = sum(stk(z) for z in besitz if preis(z) is None)
        mit_kauf = [z for z in besitz if z["kaufpreis"] is not None]
        einsatz = sum(z["kaufpreis"] * stk(z) for z in mit_kauf)
        wert_gekaufte = sum(wert_z(z) for z in mit_kauf)

        pokemon_namen = {}
        dexe = {z["first_dex"] for z in besitz if z["first_dex"]}
        if dexe:
            marken = ",".join("?" * len(dexe))
            pokemon_namen = {r["dex_id"]: r["name_de"] for r in con.execute(
                f"SELECT dex_id, name_de FROM pokemon WHERE dex_id IN ({marken})", tuple(dexe))}

        def typ(z):
            try:
                t = json.loads(z["types"] or "[]")
                return t[0] if t else None
            except Exception:
                return None

        art = {"Pokemon": "Pokémon", "Trainer": "Trainer", "Energy": "Energie"}
        # Das Gewicht je Karte ist nicht einfach die Stückzahl. Die Historie führt nur den
        # Grundpreis, der Sammlungswert rechnet aber bei Holo- und Reverse-Exemplaren mit
        # deren eigenem Preis — bei einer Karte trennen die beiden Zahlen den Faktor 24.
        # Ungewichtet läge die Kurve deshalb weit über dem Sammlungswert darüber. Der
        # Faktor "heutiger Variantenpreis geteilt durch heutigen Grundpreis" hebt jede Karte
        # auf ihr richtiges Niveau; am letzten Tag trifft die Kurve den Wert dann genau.
        gewicht = {}
        for z in besitz:
            grund = z["eur"]
            if not grund:
                continue
            faktor = (preis(z) or 0) / grund
            gewicht[z["card_id"]] = gewicht.get(z["card_id"], 0) + stk(z) * faktor
        verlauf = _reihe(con, list(gewicht), tage, gewicht)
        # Wie viel der Sammlung die Kurve überhaupt trägt. Solange die Historie jung ist,
        # sind das wenige Karten — dann zeigt die Oberfläche eine Indexkurve statt Euro,
        # damit die Zahl unter dem Diagramm nicht mit dem Gesamtwert verwechselt wird.
        if verlauf.get("punkte"):
            traeger = verlauf["punkte"][-1]["eur"]
            verlauf["anteil"] = round(traeger / wert * 100, 1) if wert else 0
            verlauf["von"] = verlauf["basis"]
            verlauf["gesamt"] = len(besitz)

        einzeln = []
        for z in besitz:
            p = preis(z)
            gesamt = (p or 0) * stk(z)
            rendite = None
            if z["kaufpreis"] and p:
                rendite = round((p - z["kaufpreis"]) / z["kaufpreis"] * 100, 1)
            einzeln.append({
                "id": z["card_id"], "name": z["name_de"] or z["name_en"],
                "set": z["set_name"] or z["set_id"], "nr": z["local_id"], "variante": z["variante"],
                "anzahl": stk(z), "preis": p, "wert": round(gesamt, 2),
                "kaufpreis": z["kaufpreis"], "rendite": rendite,
                "anteil": round(gesamt / wert * 100, 1) if wert else 0,
            })
        einzeln.sort(key=lambda x: -x["wert"])

        con.close()
        return {
            "pro": True, "leer": False,
            "wert": round(wert, 2), "karten": stueck, "verschiedene": len(besitz),
            "ohne_preis": ohne_preis,
            "einsatz": round(einsatz, 2) if mit_kauf else None,
            "wert_gekaufte": round(wert_gekaufte, 2) if mit_kauf else None,
            "gewinn": round(wert_gekaufte - einsatz, 2) if mit_kauf else None,
            "mit_kaufpreis": sum(stk(z) for z in mit_kauf),
            "verlauf": verlauf,
            "nach_seltenheit": _gruppieren(besitz, lambda z: _oder_leer(z["rarity"]), wert_z, stk, 9),
            "nach_set": _gruppieren(besitz, lambda z: z["set_name"] or z["set_id"], wert_z, stk, 8),
            "nach_pokemon": _gruppieren(besitz, lambda z: pokemon_namen.get(z["first_dex"]), wert_z, stk, 8),
            "nach_typ": _gruppieren(besitz, typ, wert_z, stk, 8),
            "nach_art": _gruppieren(besitz, lambda z: art.get(z["category"]), wert_z, stk, 4),
            "nach_jahrzehnt": _gruppieren(besitz, lambda z: (z["release_date"][:3] + "0er") if z["release_date"] else None, wert_z, stk, 5),
            "nach_zustand": _gruppieren(besitz, lambda z: _oder_leer(z["zustand"]), wert_z, stk, 8),
            "karten_liste": einzeln[:60],
        }

    # ------------------------------------------------------------------- Markt

    @app.get("/api/analytics/markt")
    def markt(request: Request, tage: int = 90):
        """Der ganze Katalog. Ohne Pro nur der Stand und die teuersten Sets als Kostprobe."""
        user = require_user(request)
        voll = ist_markt_erlaubt(user)
        tage = max(7, min(365, tage))
        con = get_db()

        stand = con.execute(
            "SELECT COUNT(*) c, SUM(eur IS NOT NULL) mit_eur, SUM(usd IS NOT NULL) mit_usd,"
            " MAX(updated_at) letzter FROM card_prices").fetchone()
        messtage = _messtage(con)
        offen = con.execute("SELECT value FROM kv WHERE key='preise_offen'").fetchone()

        sets = [dict(r) for r in con.execute(
            "SELECT c.set_id, s.name AS set_name, s.release_date, COUNT(*) n,"
            " ROUND(AVG(p.eur), 2) schnitt, ROUND(SUM(p.eur), 2) summe, ROUND(MAX(p.eur), 2) teuerste"
            " FROM card_prices p JOIN cards c ON c.id = p.card_id"
            " LEFT JOIN sets s ON s.id = c.set_id"
            " WHERE p.eur IS NOT NULL AND COALESCE(c.region,'intl')='intl'"
            " GROUP BY c.set_id HAVING n >= 15 ORDER BY schnitt DESC LIMIT 20")]

        kopf = {
            "pro": voll,
            "stand": {
                "karten_mit_preis": stand["mit_eur"] or 0,
                "karten_mit_usd": stand["mit_usd"] or 0,
                "letzter_lauf": stand["letzter"],
                "reihe_beginnt": messtage[0] if messtage else None,
                "reihe_tage": len(messtage),
                "offen": int((offen["value"] if offen else "0") or 0),
            },
        }
        if not voll:
            con.close()
            kopf["sets"] = sets[:5]
            return kopf

        # Wertverteilung: wie viele Karten in welcher Preisklasse, und wie viel Wert dort liegt
        verteilung = []
        for i, (untere, name) in enumerate(KLASSEN):
            obere = KLASSEN[i + 1][0] if i + 1 < len(KLASSEN) else None
            if obere is not None:
                bed, args = "eur >= ? AND eur < ?", (untere, obere)
            else:
                bed, args = "eur >= ?", (untere,)
            r = con.execute("SELECT COUNT(*) n, ROUND(SUM(eur),2) s FROM card_prices"
                            f" WHERE {bed}", args).fetchone()
            verteilung.append({"name": name, "anzahl": r["n"] or 0, "summe": r["s"] or 0})

        teuerste = [dict(r) for r in con.execute(
            "SELECT p.card_id id, p.eur, p.usd, c.name_de, c.name_en, c.rarity, c.release_date,"
            " (SELECT name FROM sets WHERE sets.id = c.set_id) AS set_name"
            " FROM card_prices p JOIN cards c ON c.id = p.card_id"
            " WHERE p.eur IS NOT NULL ORDER BY p.eur DESC LIMIT 24")]
        for z in teuerste:
            z["name"] = z.pop("name_de", None) or z.pop("name_en", None) or z["id"]

        # Indizes: alte Karten, mittlere Jahre, neue Karten — je die 400 teuersten
        gruppen = {
            "vintage": ("c.release_date <= ?", (AERA_GRENZE_ALT,)),
            "mitte": ("c.release_date > ? AND c.release_date < ?", (AERA_GRENZE_ALT, AERA_GRENZE_NEU)),
            "modern": ("c.release_date >= ?", (AERA_GRENZE_NEU,)),
        }
        indizes = {}
        for name, (bed, args) in gruppen.items():
            ids = [r["id"] for r in con.execute(
                f"SELECT c.id FROM cards c JOIN card_prices p ON p.card_id = c.id"
                f" WHERE {bed} AND p.eur IS NOT NULL AND COALESCE(c.region,'intl')='intl'"
                f" ORDER BY p.eur DESC LIMIT 400", args)]
            r = _reihe(con, ids, tage)
            if r["punkte"] and len(r["punkte"]) >= MIN_TAGE and r["basis"] >= MIN_BASIS:
                start = r["punkte"][0]["eur"] or 1
                r["index"] = [{"datum": p["datum"], "wert": round(p["eur"] / start * 100, 2)}
                              for p in r["punkte"]]
            else:
                r["index"] = []
            indizes[name] = r

        # Größte Bewegungen zwischen dem ersten und dem letzten Tag der Reihe
        bewegung = {"hoch": [], "runter": [], "tage": 0}
        tage_liste = _messtage(con, _tage_zurueck(tage))
        if len(tage_liste) >= 2:
            frueh, spaet = tage_liste[0], tage_liste[-1]
            bewegung.update(tage=len(tage_liste), von=frueh, bis=spaet)
            roh = [dict(r) for r in con.execute(
                "SELECT a.card_id, a.eur AS alt, b.eur AS neu, c.name_de, c.name_en, c.local_id,"
                " (SELECT name FROM sets WHERE sets.id = c.set_id) AS set_name"
                " FROM price_history a JOIN price_history b ON b.card_id = a.card_id AND b.datum = ?"
                " JOIN cards c ON c.id = a.card_id"
                " WHERE a.datum = ? AND a.eur >= 2 AND b.eur IS NOT NULL", (spaet, frueh))]
            for z in roh:
                z["prozent"] = round((z["neu"] - z["alt"]) / z["alt"] * 100, 1)
            roh.sort(key=lambda z: -z["prozent"])
            fmt = lambda z: {"id": z["card_id"], "name": z["name_de"] or z["name_en"],
                             "set": z["set_name"], "nr": z["local_id"], "alt": z["alt"],
                             "neu": z["neu"], "prozent": z["prozent"]}
            bewegung["basis"] = len(roh)
            bewegung["hoch"] = [fmt(z) for z in roh[:10] if z["prozent"] > 0]
            bewegung["runter"] = [fmt(z) for z in reversed(roh[-10:]) if z["prozent"] < 0]

        # Europa gegen USA. Der Umrechnungskurs steckt im Median aller Paare, statt fest
        # verdrahtet zu sein — so wandert er mit, ohne dass jemand ihn pflegen muss.
        paare = [(r["eur"], r["usd"], r["card_id"], r["name_de"] or r["name_en"],
                  r["set_name"], r["local_id"])
                 for r in con.execute(
                     "SELECT p.card_id, p.eur, p.usd, c.name_de, c.name_en, c.local_id,"
                     " (SELECT name FROM sets WHERE sets.id = c.set_id) AS set_name"
                     " FROM card_prices p JOIN cards c ON c.id = p.card_id"
                     " WHERE p.eur >= 5 AND p.usd >= 5")]
        vergleich = {"paare": len(paare), "kurs": None, "guenstiger_eu": [], "guenstiger_us": []}
        if len(paare) >= 50:
            quotienten = sorted(e / u for e, u, *_ in paare)
            kurs = quotienten[len(quotienten) // 2]          # Median: Euro je Dollar
            vergleich["kurs"] = round(kurs, 4)
            bewertet = []
            for eur, usd, cid, name, setn, nr in paare:
                erwartet = usd * kurs
                if erwartet <= 0:
                    continue
                bewertet.append({"id": cid, "name": name, "set": setn, "nr": nr,
                                 "eur": eur, "usd": usd,
                                 "abstand": round((eur - erwartet) / erwartet * 100, 1),
                                 "differenz": round(eur - erwartet, 2)})
            # Über MAX_ABSTAND liegt fast nie ein Marktunterschied, sondern ein Datenfehler:
            # TCGdex hängt dann den Preis eines anderen Drucks an dieselbe Karte. Solche
            # Zeilen oben in einer Liste „hier günstiger" wären eine falsche Empfehlung.
            brauchbar = [z for z in bewertet if abs(z["abstand"]) <= MAX_ABSTAND]
            vergleich["verworfen"] = len(bewertet) - len(brauchbar)
            vergleich["geprueft"] = len(brauchbar)
            # Sortiert wird nach dem Betrag in Euro, nicht nach dem Prozentsatz. Wer wissen
            # will, wo sich der Blick über den Atlantik lohnt, spart an einer 200-€-Karte mit
            # 40 % mehr als an einer 12-€-Karte mit 70 %. Nach Prozent sortiert stünden zudem
            # lauter Zeilen genau an der Ausreißergrenze oben — das sähe nach Deckelung aus.
            brauchbar.sort(key=lambda z: z["differenz"])
            vergleich["guenstiger_eu"] = brauchbar[:12]
            vergleich["guenstiger_us"] = list(reversed(brauchbar[-12:]))

        con.close()
        kopf.update(indizes=indizes, bewegung=bewegung, vergleich=vergleich,
                    sets=sets, verteilung=verteilung, teuerste=teuerste)
        return kopf

    # ------------------------------------------------------- Markt: Bereiche
    # Der Überblick war eine einzige lange Bahn, in der der Börsenvergleich ganz oben
    # stand — eine Randnotiz an der prominentesten Stelle. Die Zahlen liegen jetzt in
    # vier Bereichen, die einzeln geladen werden; das hält auch die Antwortzeit klein.

    @app.get("/api/analytics/markt/struktur")
    def markt_struktur(request: Request):
        """Woraus der Katalog besteht und wo sein Wert sitzt."""
        user = require_user(request)
        if not ist_markt_erlaubt(user):
            return {"pro": False}
        con = get_db()

        def gruppe(spalte, grenze=12, bed="", extra=()):
            wo = "p.eur IS NOT NULL AND COALESCE(c.region,'intl')='intl'"
            if bed:
                wo += " AND " + bed
            return [{"name": r["k"], "anzahl": r["n"], "summe": r["s"], "schnitt": r["d"]}
                    for r in con.execute(
                        f"SELECT {spalte} k, COUNT(*) n, ROUND(SUM(p.eur),2) s, ROUND(AVG(p.eur),2) d"
                        f" FROM card_prices p JOIN cards c ON c.id = p.card_id"
                        f" WHERE {wo} AND {spalte} IS NOT NULL AND {spalte} <> ''"
                        f" GROUP BY k ORDER BY s DESC LIMIT ?", (*extra, grenze))]

        verteilung = []
        for i, (untere, name) in enumerate(KLASSEN):
            obere = KLASSEN[i + 1][0] if i + 1 < len(KLASSEN) else None
            if obere is not None:
                bed, args = "eur >= ? AND eur < ?", (untere, obere)
            else:
                bed, args = "eur >= ?", (untere,)
            r = con.execute("SELECT COUNT(*) n, ROUND(SUM(eur),2) s FROM card_prices"
                            f" WHERE {bed}", args).fetchone()
            verteilung.append({"name": name, "anzahl": r["n"] or 0, "summe": r["s"] or 0})

        # Wert je Pokémon über alle seine Karten hinweg
        pokemon = [{"name": r["nm"], "anzahl": r["n"], "summe": r["s"], "schnitt": r["d"]}
                   for r in con.execute(
                       "SELECT pk.name_de nm, COUNT(*) n, ROUND(SUM(p.eur),2) s, ROUND(AVG(p.eur),2) d"
                       " FROM card_prices p JOIN cards c ON c.id = p.card_id"
                       " JOIN pokemon pk ON pk.dex_id = c.first_dex"
                       " WHERE p.eur IS NOT NULL AND COALESCE(c.region,'intl')='intl'"
                       " GROUP BY pk.dex_id ORDER BY s DESC LIMIT 15")]

        jahre = [{"name": r["j"], "anzahl": r["n"], "summe": r["s"], "schnitt": r["d"]}
                 for r in con.execute(
                     "SELECT substr(c.release_date,1,4) j, COUNT(*) n, ROUND(SUM(p.eur),2) s,"
                     " ROUND(AVG(p.eur),2) d FROM card_prices p JOIN cards c ON c.id = p.card_id"
                     " WHERE p.eur IS NOT NULL AND c.release_date IS NOT NULL"
                     " AND COALESCE(c.region,'intl')='intl'"
                     " GROUP BY j ORDER BY j")]

        gesamt = con.execute("SELECT ROUND(SUM(eur),2) s, COUNT(*) n FROM card_prices"
                             " WHERE eur IS NOT NULL").fetchone()
        # Erst rechnen, dann schließen: im Rückgabe-Dict ausgewertet, liefe `gruppe()`
        # gegen eine bereits geschlossene Verbindung.
        aus = {"pro": True,
               "gesamt": {"summe": gesamt["s"] or 0, "karten": gesamt["n"] or 0},
               "verteilung": verteilung,
               "seltenheit": gruppe("c.rarity"),
               "art": gruppe("c.category", 5),
               "illustrator": gruppe("c.illustrator", 15),
               "pokemon": pokemon,
               "jahre": jahre}
        con.close()
        return aus

    @app.get("/api/analytics/markt/sets")
    def markt_sets(request: Request, sortier: str = "schnitt", limit: int = 40):
        """Alle Sets mit ihren Kennzahlen — die Liste, in der man selbst sucht."""
        user = require_user(request)
        if not ist_markt_erlaubt(user):
            return {"pro": False}
        spalten = {"schnitt": "schnitt DESC", "summe": "summe DESC", "teuerste": "teuerste DESC",
                   "jahr": "s.release_date DESC", "karten": "n DESC", "name": "s.name"}
        ordnung = spalten.get(sortier, spalten["schnitt"])
        con = get_db()
        sets = [dict(r) for r in con.execute(
            "SELECT c.set_id, s.name AS set_name, s.serie_name, s.release_date, s.total,"
            " COUNT(*) n, ROUND(AVG(p.eur), 2) schnitt, ROUND(SUM(p.eur), 2) summe,"
            " ROUND(MAX(p.eur), 2) teuerste,"
            " (SELECT c2.id FROM card_prices p2 JOIN cards c2 ON c2.id = p2.card_id"
            "  WHERE c2.set_id = c.set_id ORDER BY p2.eur DESC LIMIT 1) top_id"
            " FROM card_prices p JOIN cards c ON c.id = p.card_id"
            " LEFT JOIN sets s ON s.id = c.set_id"
            " WHERE p.eur IS NOT NULL AND COALESCE(c.region,'intl')='intl'"
            f" GROUP BY c.set_id HAVING n >= 10 ORDER BY {ordnung} LIMIT ?", (max(5, min(200, limit)),))]
        # Vollständigkeit: wie viel des Sets überhaupt bepreist ist
        for z in sets:
            if z.get("total"):
                z["abdeckung"] = round(z["n"] / z["total"] * 100)
        con.close()
        return {"pro": True, "sets": sets, "sortier": sortier}

    @app.get("/api/analytics/markt/regionen")
    def markt_regionen(request: Request):
        """Europa gegen USA und Japan gegen den Westen — zwei Preisgefälle."""
        user = require_user(request)
        if not ist_markt_erlaubt(user):
            return {"pro": False}
        con = get_db()

        paare = [(r["eur"], r["usd"], r["card_id"], r["name_de"] or r["name_en"],
                  r["set_name"], r["local_id"])
                 for r in con.execute(
                     "SELECT p.card_id, p.eur, p.usd, c.name_de, c.name_en, c.local_id,"
                     " (SELECT name FROM sets WHERE sets.id = c.set_id) AS set_name"
                     " FROM card_prices p JOIN cards c ON c.id = p.card_id"
                     " WHERE p.eur >= 5 AND p.usd >= 5")]
        vergleich = {"paare": len(paare), "kurs": None, "guenstiger_eu": [], "guenstiger_us": []}
        if len(paare) >= 50:
            quotienten = sorted(e / u for e, u, *_ in paare)
            kurs = quotienten[len(quotienten) // 2]
            vergleich["kurs"] = round(kurs, 4)
            bewertet = []
            for eur, usd, cid, name, setn, nr in paare:
                erwartet = usd * kurs
                if erwartet <= 0:
                    continue
                bewertet.append({"id": cid, "name": name, "set": setn, "nr": nr,
                                 "eur": eur, "usd": usd,
                                 "abstand": round((eur - erwartet) / erwartet * 100, 1),
                                 "differenz": round(eur - erwartet, 2)})
            brauchbar = [z for z in bewertet if abs(z["abstand"]) <= MAX_ABSTAND]
            vergleich["verworfen"] = len(bewertet) - len(brauchbar)
            vergleich["geprueft"] = len(brauchbar)
            brauchbar.sort(key=lambda z: z["differenz"])
            vergleich["guenstiger_eu"] = brauchbar[:12]
            vergleich["guenstiger_us"] = list(reversed(brauchbar[-12:]))

        # Japan gegen Westen: seit der Katalog auch japanische Preise trägt, lässt sich
        # zeigen, wie groß der Abstand zwischen den Märkten wirklich ist.
        jp = con.execute(
            "SELECT COUNT(*) n, ROUND(AVG(p.eur),2) schnitt, ROUND(SUM(p.eur),2) summe,"
            " ROUND(MAX(p.eur),2) hoechst FROM card_prices p JOIN cards c ON c.id = p.card_id"
            " WHERE p.eur IS NOT NULL AND c.region = 'jp'").fetchone()
        west = con.execute(
            "SELECT COUNT(*) n, ROUND(AVG(p.eur),2) schnitt, ROUND(SUM(p.eur),2) summe,"
            " ROUND(MAX(p.eur),2) hoechst FROM card_prices p JOIN cards c ON c.id = p.card_id"
            " WHERE p.eur IS NOT NULL AND COALESCE(c.region,'intl') = 'intl'").fetchone()
        jp_top = [{"id": r["id"], "name": r["name_ja"] or r["name_de"], "eur": r["eur"],
                   "set": r["set_name"]} for r in con.execute(
            "SELECT c.id, c.name_ja, c.name_de, p.eur,"
            " (SELECT name FROM sets WHERE sets.id = c.set_id) AS set_name"
            " FROM card_prices p JOIN cards c ON c.id = p.card_id"
            " WHERE c.region = 'jp' AND p.eur IS NOT NULL ORDER BY p.eur DESC LIMIT 12")]
        con.close()
        return {"pro": True, "vergleich": vergleich,
                "jp": dict(jp), "west": dict(west), "jp_top": jp_top}

    @app.get("/api/analytics/karte/{card_id}")
    def karte_analyse(request: Request, card_id: str, tage: int = 180):
        """Preisverlauf einer einzelnen Karte, europäisch und amerikanisch."""
        user = require_user(request)
        if not ist_pro(user):
            return {"pro": False, "punkte": []}
        con = get_db()
        von = _tage_zurueck(max(7, min(730, tage)))
        punkte = [{"datum": r["datum"], "eur": r["eur"], "usd": r["usd"]} for r in con.execute(
            "SELECT datum, eur, usd FROM price_history WHERE card_id = ? AND datum >= ?"
            " ORDER BY datum", (card_id, von))]
        jetzt = con.execute("SELECT eur, eur_holo, usd, usd_holo, updated_at FROM card_prices"
                            " WHERE card_id = ?", (card_id,)).fetchone()
        con.close()
        return {"pro": True, "punkte": punkte, "jetzt": dict(jetzt) if jetzt else None}

    def kennzahlen():
        con = get_db()
        n = con.execute("SELECT COUNT(*) c FROM card_prices WHERE eur IS NOT NULL").fetchone()["c"]
        t = con.execute("SELECT COUNT(DISTINCT datum) c FROM price_history").fetchone()["c"]
        con.close()
        return {"karten_mit_preis": n, "reihe_tage": t}

    return kennzahlen
