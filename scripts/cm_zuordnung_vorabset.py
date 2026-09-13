#!/usr/bin/env python3
"""Cardmarket-Produkte einem von Hand angelegten Vorab-Set zuordnen.

Warum es dieses Skript gibt: Die reguläre Zuordnung in `cm_import()` entscheidet über die
Attackenkette (`cards.merkmale`). Ein Vorab-Set aus Serebii-Scans hat dieses Feld nicht —
deshalb greift dort nur der Namensvergleich, und der versagt genau bei den Karten, die
mehrfach im Set stehen: 30 Karten heißen „Pikachu", jedes ex gibt es als Double Rare, als
Special Illustration Rare und teils als Futuristic Rare. Am 13.09.2026 waren so 97 von 188
Produkten des 30th-Celebration-Sets ohne Zuordnung — darunter alle sieben teuersten.

Die Regel, die stattdessen trägt (an cel30 gemessen, nicht geraten):

  Cardmarket legt die Produkte eines Sets in Kartenreihenfolge an. Innerhalb einer Gruppe
  gleichen Basisnamens folgt `idProduct` also der Kartennummer. Nachzügler — Karten, die
  erst später enthüllt wurden — bekommen einen viel höheren Block und durchbrechen die
  Reihenfolge; sie sind an ihrem `dateAdded` erkennbar und gehören zu den Karten, für die
  es noch keinen Scan gibt.

Deshalb wird je Basisnamen-Gruppe zuerst der Nachzügler-Block auf die noch nicht enthüllten
Karten verteilt, danach der Rest aufsteigend. Zugeordnet wird nur, wenn die Gruppe auf
beiden Seiten gleich viele Einträge hat.

Die Selbstkontrolle ist der eigentliche Kern: Bevor irgendetwas geschrieben wird, läuft der
Algorithmus über die bereits bestätigten Zuordnungen. Widerspricht er auch nur einer, bricht
das Skript ab und schreibt nichts.

Aufruf (als root):
  venv/bin/python scripts/cm_zuordnung_vorabset.py cel30            # Probelauf, schreibt nichts
  venv/bin/python scripts/cm_zuordnung_vorabset.py cel30 --schreiben
"""
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

BASIS = Path(__file__).resolve().parent.parent
DB = Path(os.environ.get("BP_DB") or (BASIS / "app.db"))
CM = Path(os.environ.get("BP_CM") or (BASIS / "cardmarket"))


def basisname(name: str) -> str:
    """Produktname ohne die Attackenkette in eckigen Klammern und ohne Beiwerk."""
    n = re.sub(r"\s*\[.*", "", str(name or "")).strip()
    n = re.sub(r"\s+(Lv\.?\s*\d+|LV\.X|δ\s*Delta Species)$", "", n).strip()
    return re.sub(r"[^a-z0-9]", "", n.lower())


def kartenname(r) -> str:
    return re.sub(r"[^a-z0-9]", "", str(r["name_en"] or r["name_de"] or "").lower())


def lade_produkte(expansion: int):
    roh = json.loads((CM / "products_singles_6.json").read_text(encoding="utf-8"))
    alle = roh["products"] if isinstance(roh, dict) else roh
    return sorted([p for p in alle if p.get("idExpansion") == expansion],
                  key=lambda p: p["idProduct"])


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    set_id = sys.argv[1]
    schreiben = "--schreiben" in sys.argv
    korrigieren = "--korrigieren" in sys.argv

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    bekannt = {r["cm_produkt"]: r["card_id"] for r in con.execute(
        "SELECT p.cm_produkt, p.card_id FROM card_prices p JOIN cards c ON c.id = p.card_id"
        " WHERE c.set_id = ? AND p.cm_produkt IS NOT NULL", (set_id,))}
    if not bekannt:
        print(f"{set_id}: keine einzige bestätigte Zuordnung — ohne Anker ist die Regel nicht prüfbar.")
        return 1

    # Die Erweiterung aus den bestätigten Zuordnungen lernen, nicht raten.
    exp_zaehler = defaultdict(int)
    roh = json.loads((CM / "products_singles_6.json").read_text(encoding="utf-8"))
    alle = roh["products"] if isinstance(roh, dict) else roh
    exp_je_produkt = {p["idProduct"]: p.get("idExpansion") for p in alle}
    for pid in bekannt:
        e = exp_je_produkt.get(pid)
        if e is not None:
            exp_zaehler[e] += 1
    expansion = max(exp_zaehler, key=exp_zaehler.get)
    produkte = lade_produkte(expansion)

    karten = list(con.execute(
        "SELECT id, local_id, local_num, name_de, name_en, rarity, image_alt, image_de, image_en"
        " FROM cards WHERE set_id = ? ORDER BY local_num, local_id", (set_id,)))
    print(f"{set_id}: Erweiterung {expansion} · {len(produkte)} Produkte · {len(karten)} Karten"
          f" · {len(bekannt)} bestätigte Zuordnungen")

    # Nachzügler-Block: das jüngste dateAdded, sofern es eine Minderheit betrifft.
    daten = defaultdict(list)
    for p in produkte:
        daten[str(p.get("dateAdded") or "")[:10]].append(p)
    juengstes = max(daten) if daten else ""
    nachzuegler = set()
    if juengstes and len(daten[juengstes]) * 4 < len(produkte):
        nachzuegler = {p["idProduct"] for p in daten[juengstes]}
    print(f"Nachzügler-Block ({juengstes}): {len(nachzuegler)} Produkte")

    p_gruppe, k_gruppe = defaultdict(list), defaultdict(list)
    for p in produkte:
        p_gruppe[basisname(p["name"])].append(p)
    for k in karten:
        k_gruppe[kartenname(k)].append(k)

    vorschlag, uebersprungen = {}, []
    for name, ps in p_gruppe.items():
        ks = k_gruppe.get(name, [])
        if len(ps) != len(ks):
            uebersprungen.append((name, len(ps), len(ks)))
            continue
        spaet = sorted([p for p in ps if p["idProduct"] in nachzuegler], key=lambda x: x["idProduct"])
        frueh = sorted([p for p in ps if p["idProduct"] not in nachzuegler], key=lambda x: x["idProduct"])
        # Ohne Scan = noch nicht enthüllt. Genau diese Karten trägt der Nachzügler-Block.
        offen = [k for k in ks if not (k["image_alt"] or k["image_de"] or k["image_en"])]
        rest = [k for k in ks if k not in offen]
        if spaet and len(spaet) != len(offen):
            uebersprungen.append((name, f"Nachzügler {len(spaet)}", f"unenthüllt {len(offen)}"))
            continue
        for p, k in list(zip(spaet, offen)) + list(zip(frueh, rest)):
            vorschlag[p["idProduct"]] = k["id"]

    # --- Selbstkontrolle: erst die bestätigten Zuordnungen reproduzieren -----------------
    treffer = [pid for pid, cid in bekannt.items() if vorschlag.get(pid) == cid]
    fehler = [(pid, cid, vorschlag.get(pid)) for pid, cid in bekannt.items()
              if pid in vorschlag and vorschlag[pid] != cid]
    stumm = [pid for pid in bekannt if pid not in vorschlag]
    print(f"\nSelbstkontrolle: {len(treffer)} von {len(bekannt)} bestätigten Zuordnungen reproduziert,"
          f" {len(fehler)} Widersprüche, {len(stumm)} nicht abgedeckt")
    if stumm:
        print("Nicht abgedeckt (Gruppe übersprungen, bestehende Zuordnung bleibt):")
        for pid in stumm[:10]:
            print(f"   Produkt {pid} -> {bekannt[pid]}")
    if fehler:
        print("\nWidersprüche zwischen Bestand und Regel:")
        for pid, soll, ist in fehler[:20]:
            print(f"   Produkt {pid}: eingetragen {soll}, Regel sagt {ist}")
        if not korrigieren:
            print("\nABBRUCH. Jeden Fall von Hand prüfen, dann mit --korrigieren erneut aufrufen —"
                  " dann werden diese Einträge überschrieben.")
            return 1
        print("   --korrigieren gesetzt: diese Einträge werden überschrieben.")

    neu = {pid: cid for pid, cid in vorschlag.items()
           if pid not in bekannt or (korrigieren and bekannt[pid] != cid)}
    print(f"Neu zuzuordnen: {len(neu)} Produkte")
    if uebersprungen:
        print(f"Übersprungen (Gruppengrößen ungleich): {len(uebersprungen)}")
        for n, a, b in uebersprungen[:10]:
            print(f"   {n}: Produkte {a}, Karten {b}")

    namen = {p["idProduct"]: p["name"] for p in produkte}
    kname = {k["id"]: f'{k["local_id"]} {k["name_de"] or k["name_en"]} ({k["rarity"]})' for k in karten}
    print("\nDie 12 neuen Zuordnungen mit den höchsten Produktnummern:")
    for pid in sorted(neu, reverse=True)[:12]:
        print(f"   {pid}  {namen[pid][:44]:<46} -> {kname[neu[pid]]}")

    if not schreiben:
        print("\nProbelauf — nichts geschrieben. Mit --schreiben ausführen.")
        return 0

    for pid, cid in neu.items():
        # Hing das Produkt bisher an einer anderen Karte, muss die es zuerst abgeben —
        # sonst zeigen zwei Karten auf dasselbe Produkt und teilen sich einen Preis.
        con.execute("UPDATE card_prices SET cm_produkt = NULL, eur = NULL, eur_low = NULL,"
                    " eur_avg7 = NULL, eur_avg30 = NULL, eur_holo = NULL"
                    " WHERE cm_produkt = ? AND card_id <> ?", (pid, cid))
        con.execute("INSERT INTO card_prices (card_id, cm_produkt) VALUES (?, ?)"
                    " ON CONFLICT(card_id) DO UPDATE SET cm_produkt = excluded.cm_produkt",
                    (cid, pid))
    con.commit()
    print(f"\n{len(neu)} Zuordnungen geschrieben.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
