"""Was eine Karte wert ist — die einzige Stelle, die das entscheidet.

Bis September 2026 rechnete jede Ansicht selbst: der Markt ohne Zustand, der Wochenrückblick
ohne Zustand, die Binderkachel ohne Zustand und ohne Stückzahl, die Auswertung ohne die
geschätzten Preise, der Posten-Dialog im Browser ohne den Holo-Preis. Dieselbe Sammlung hatte
damit fünf Werte; bei einer Sammlung aus gebrauchten Karten lagen 79 % dazwischen. Wer eine
Zahl in Euro anzeigt, ruft ab jetzt hier an.

**Die Regel.** Basis ist der Holo-Preis, wenn das Exemplar holo oder reverse ist und ein
Holo-Preis vorliegt, sonst der Trendpreis (und wenn der fehlt, der aus dem US-Preis
geschätzte). Darauf der Zustandsabschlag; ohne Zustandsangabe gibt es keinen Abschlag, denn
wer nichts angegeben hat, soll nicht stillschweigend abgewertet werden. Für Poor gilt
zusätzlich der echte Tiefstpreis als Obergrenze — ein tatsächliches Angebot schlägt jede
Ableitung.

**Warum die Faktoren so aussehen.** Preise je Zustand veröffentlicht keine Börse. Der
Cardmarket-Trend steht für ein nahezu neues Exemplar; darunter liegen die Abschläge, mit denen
im Handel gerechnet wird. Dieselben Werte stehen im Kartendetail und im Posten-Dialog — sie
kommen jetzt über `/api/meta` von hier, damit die drei Kopien im Browser verschwinden.
"""

ZUSTAND_FAKTOR = {"M": 1.10, "NM": 1.00, "EX": 0.85, "GD": 0.70,
                  "LP": 0.55, "PL": 0.42, "PO": 0.30}

# Der Preis-Ausdruck für SQL. Jede Abfrage, die einen Wert bildet, nimmt diesen statt „p.eur":
# Karten ohne Cardmarket-Preis tragen den aus dem US-Preis umgerechneten Wert, sonst fielen
# japanische Karten und Promos aus jeder Summe heraus.
SQL_EUR = "COALESCE({p}.eur, {p}.eur_geschaetzt)"


def sql_eur(alias="p"):
    """`COALESCE(p.eur, p.eur_geschaetzt)` — der Preis, mit dem gerechnet wird."""
    return SQL_EUR.format(p=alias)


def posten_wert(eur, eur_holo, eur_low, variante="normal", zustand=""):
    """Was ein einzelnes Exemplar wert ist — Ausprägung und Zustand eingerechnet."""
    basis = eur_holo if (variante in ("holo", "reverse") and eur_holo) else eur
    if basis is None:
        return None
    f = ZUSTAND_FAKTOR.get((zustand or "").upper())
    if not f:
        return round(basis, 2)
    w = basis * f
    if (zustand or "").upper() == "PO" and eur_low is not None:
        w = min(w, eur_low)
    return round(w, 2)


def _feld(zeile, name, standard=None):
    """Liest ein Feld aus einer sqlite3.Row oder einem dict, ohne bei fehlender Spalte zu brechen."""
    try:
        wert = zeile[name]
    except (KeyError, IndexError):
        return standard
    return standard if wert is None else wert


def zeilen_wert(zeilen, *, anzahl_feld="anzahl", preis_alias=""):
    """Summe über Posten — jede Zeile mit ihrem eigenen Zustand und ihrer Ausprägung.

    Erwartet Zeilen mit `eur`, `eur_holo`, `eur_low`, `variante`, `zustand` und einer Stückzahl.
    `anzahl_feld=None` zählt jede Zeile einmal (Binderfächer: dort ist jedes Fach ein Exemplar).
    Gibt (Summe, Anzahl bewerteter Stück, Anzahl Stück ohne Preis) zurück."""
    summe = 0.0
    bewertet = ohne = 0
    for z in zeilen:
        n = 1 if anzahl_feld is None else (_feld(z, anzahl_feld, 0) or 0)
        w = posten_wert(_feld(z, "eur"), _feld(z, "eur_holo"), _feld(z, "eur_low"),
                        _feld(z, "variante", "normal"), _feld(z, "zustand", ""))
        if w is None:
            ohne += n
            continue
        summe += w * n
        bewertet += n
    return round(summe, 2), bewertet, ohne


# --- Bewegung ---------------------------------------------------------------
#
# Dieselbe Regel wie im Markt (markt.py): Springt der Trend über das Dreifache oder unter ein
# Drittel seines Schnitts, ist das fast nie der Markt, sondern eine falsche Cardmarket-Zuordnung
# in der Quelle. Unter einem Euro sind die Sprünge Rundung. Solche Karten tragen keine Bewegung.

AUS_UNTEN, AUS_OBEN = 1 / 3, 3.0
MIN_PREIS = 1.0


# --- Bewegung: woher der Vergleichswert kommt -------------------------------------------
#
# Bis zum 11.09.2026 verglich jede Bewegung `trend` gegen `avg7`/`avg30` aus dem
# Cardmarket-Preisverzeichnis. Das sind zwei verschiedene Preisarten: `trend` ist der
# Trendpreis der *aktuellen Angebote*, `avg7/avg30` der Durchschnitt *tatsächlich verkaufter*
# Exemplare — inklusive stark gespielter Karten. Ihr Quotient ist keine Veränderung über die
# Zeit. Gemessen über 2.480 Karten mit mindestens fünf eigenen Messpunkten in acht Tagen:
# das Vorzeichen widersprach der eigenen Historie bei 23 %, die Abweichung lag bei 37 % über
# zehn Prozentpunkten (Grundset-Glurak meldete „+76,5 % in 7 Tagen“ bei tatsächlich −3 %).
#
# Seitdem kommt der Vergleichswert aus `price_history` — derselben Reihe, aus der auch die
# Kurven gezeichnet werden. Die Tabelle speichert nur Bewegungen; der letzte Eintrag am oder
# vor dem Stichtag ist deshalb exakt der Preis dieses Tages, keine Näherung.

def historie_basis(con, card_ids, tage):
    """Preis je Karte am Stichtag heute − `tage`, aus `price_history`.

    Rückgabe: ``{card_id: (preis, tage_tatsächlich)}``. Reicht die Historie nicht so weit
    zurück (sie beginnt am 21.08.2026), kommt der älteste vorhandene Eintrag — dann sagt
    ``tage_tatsächlich``, über wie viele Tage die Bewegung wirklich läuft, damit die
    Oberfläche keine Zahl unter eine falsche Überschrift stellt. Karten ganz ohne Eintrag
    fehlen im Ergebnis; ihre Bewegung bleibt „–“ statt gegen einen fremden Maßstab gerechnet.
    """
    ids = [i for i in dict.fromkeys(card_ids) if i]
    if not ids:
        return {}
    aus = {}
    stichtag = "date('now','-%d day')" % int(tage)
    for i in range(0, len(ids), 800):
        teil = ids[i:i + 800]
        marken = ",".join("?" * len(teil))
        # 1. der jüngste Eintrag am oder vor dem Stichtag — der Preis dieses Tages
        # Das Fenster ist `tage`, auch wenn der Eintrag älter ist: die Historie speichert nur
        # Bewegungen, ein älterer Eintrag heißt also „der Preis stand am Stichtag genau so“.
        for r in con.execute(
                f"SELECT h.card_id, h.eur FROM price_history h"
                f" WHERE h.card_id IN ({marken}) AND h.eur IS NOT NULL"
                f"   AND h.datum = (SELECT MAX(x.datum) FROM price_history x"
                f"                  WHERE x.card_id = h.card_id AND x.datum <= {stichtag}"
                f"                    AND x.eur IS NOT NULL)", teil):
            aus[r[0]] = (r[1], int(tage))
        # 2. wo die Historie nicht so weit reicht: der älteste Eintrag, mit seinem echten Alter
        rest = [c for c in teil if c not in aus]
        if not rest:
            continue
        marken = ",".join("?" * len(rest))
        for r in con.execute(
                f"SELECT h.card_id, h.eur, julianday('now') - julianday(h.datum) d"
                f" FROM price_history h WHERE h.card_id IN ({marken}) AND h.eur IS NOT NULL"
                f"   AND h.datum = (SELECT MIN(x.datum) FROM price_history x"
                f"                  WHERE x.card_id = h.card_id AND x.eur IS NOT NULL)", rest):
            alter = int(round(r[2]))
            if alter >= 2:          # unter zwei Tagen ist es keine Bewegung, sondern Rauschen
                aus[r[0]] = (r[1], alter)
    return aus


def basis_fenster(basis):
    """Über wie viele Tage die Bewegungen einer `historie_basis` im Median laufen.

    Solange die Historie kürzer ist als das gewünschte Fenster, steht hier die echte Zahl —
    die Oberfläche schreibt dann „21 Tage“ und nicht „30 Tage“."""
    tage = sorted(t for _, t in basis.values())
    return tage[len(tage) // 2] if tage else None


def bewegung_prozent(eur, schnitt):
    """Prozent gegen den 7- oder 30-Tage-Schnitt; None, wenn die Zahl nichts aussagt."""
    if not eur or not schnitt or schnitt <= 0 or eur < MIN_PREIS or schnitt < MIN_PREIS:
        return None
    q = eur / schnitt
    if q > AUS_OBEN or q < AUS_UNTEN:
        return None
    return round((q - 1) * 100, 1)


def bewegung_euro(zeile, schnitt_feld, anzahl=None):
    """Was ein Posten in Euro gewonnen oder verloren hat.

    Der Schnitt gilt für den Grundpreis; ein Holo-Exemplar bewegt sich um denselben Prozentsatz,
    aber um mehr Euro. Deshalb wird die Differenz mit dem Verhältnis aus Postenwert und
    Grundpreis skaliert — sonst zeigte eine Holo-Karte die Bewegung ihrer Normalfassung."""
    eur = _feld(zeile, "eur")
    schnitt = _feld(zeile, schnitt_feld)
    if bewegung_prozent(eur, schnitt) is None:
        return None
    w = posten_wert(eur, _feld(zeile, "eur_holo"), _feld(zeile, "eur_low"),
                    _feld(zeile, "variante", "normal"), _feld(zeile, "zustand", ""))
    if w is None:
        return None
    n = anzahl if anzahl is not None else (_feld(zeile, "anzahl", 1) or 1)
    return round((eur - schnitt) * (w / eur) * n, 2)
