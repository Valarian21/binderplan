"""Legt die Binderplan-Produkte und -Preise in Stripe an und schreibt die IDs in die .env.

Idempotent: vorhandene Preise mit passendem lookup_key werden wiederverwendet, nichts wird
doppelt angelegt. Fasst ausschließlich Produkte mit metadata.app=binderplan an —
die Lehreule-Produkte im selben Stripe-Konto bleiben unberührt.

Aufruf:  sudo /root/apps/binderplan/venv/bin/python stripe_setup.py [--archiviere-alt]
"""
import sys
from pathlib import Path

import httpx

BASE = Path("/root/apps/binderplan")
API = "https://api.stripe.com/v1"


def env():
    out = {}
    for line in (BASE / ".env").read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


KEY = env()["STRIPE_SECRET_KEY"]


def flach(prefix, wert, out):
    if isinstance(wert, dict):
        for k, v in wert.items():
            flach(f"{prefix}[{k}]" if prefix else k, v, out)
    elif isinstance(wert, (list, tuple)):
        for i, v in enumerate(wert):
            flach(f"{prefix}[{i}]", v, out)
    elif wert is not None:
        out[prefix] = "true" if wert is True else ("false" if wert is False else str(wert))
    return out


def api(pfad, daten=None, methode="POST"):
    r = httpx.request(methode, f"{API}/{pfad}", data=flach("", daten or {}, {}), auth=(KEY, ""), timeout=30)
    d = r.json()
    if r.status_code >= 400:
        raise SystemExit(f"Stripe-Fehler bei {pfad}: {d.get('error', {}).get('message')}")
    return d


def produkt(name, beschreibung):
    """Produkt anhand des Namens finden oder anlegen (nur Binderplan-Produkte)."""
    for p in api("products?limit=100&active=true", methode="GET")["data"]:
        if p["name"] == name:
            return p["id"]
    p = api("products", {"name": name, "description": beschreibung,
                         "metadata": {"app": "binderplan"}})
    print(f"  + Produkt angelegt: {name}")
    return p["id"]


def preis(produkt_id, lookup_key, cent, intervall=None):
    """Preis über lookup_key finden oder anlegen."""
    treffer = api(f"prices?lookup_keys[]={lookup_key}&limit=1&active=true", methode="GET")["data"]
    if treffer:
        return treffer[0]["id"]
    daten = {"product": produkt_id, "unit_amount": cent, "currency": "eur",
             "lookup_key": lookup_key, "metadata": {"app": "binderplan"}}
    if intervall:
        daten["recurring"] = {"interval": intervall}
    p = api("prices", daten)
    print(f"  + Preis angelegt: {lookup_key} = {cent/100:.2f} € {intervall or 'einmalig'}")
    return p["id"]


print("Binderplan – Stripe-Einrichtung")
plus = produkt("Binderplan Plus", "Unbegrenzt planen und drucken, 80 Credits im Monat.")
pro = produkt("Binderplan Pro", "Unbegrenzt planen und drucken, 200 Credits im Monat.")
credits = produkt("Binderplan Credits", "Guthaben für KI-Artwork-Seiten. Einmalig, ohne Verfall.")

ids = {
    "STRIPE_PLUS_MONAT": preis(plus, "bp_plus_monat", 399, "month"),
    "STRIPE_PLUS_JAHR": preis(plus, "bp_plus_jahr", 3999, "year"),
    "STRIPE_PRO_MONAT": preis(pro, "bp_pro_monat_v2", 799, "month"),
    "STRIPE_PRO_JAHR": preis(pro, "bp_pro_jahr_v2", 7999, "year"),
    "STRIPE_PAKET_100": preis(credits, "bp_paket_100", 499),
    "STRIPE_PAKET_250": preis(credits, "bp_paket_250", 1099),
    "STRIPE_PAKET_600": preis(credits, "bp_paket_600", 2399),
}

# .env aktualisieren (bestehende Zeilen ersetzen, fehlende anhängen)
pfad = BASE / ".env"
zeilen = pfad.read_text().splitlines()
for key, wert in ids.items():
    for i, z in enumerate(zeilen):
        if z.startswith(key + "="):
            zeilen[i] = f"{key}={wert}"
            break
    else:
        zeilen.append(f"{key}={wert}")
pfad.write_text("\n".join(zeilen) + "\n")
print("\n.env aktualisiert:")
for k, v in ids.items():
    print(f"  {k}={v}")

if "--archiviere-alt" in sys.argv:
    # Alte Preise des Vormodells deaktivieren (3,99/24,99 Pro, 49,99 Lifetime).
    # Aktive Abos wären davon nicht betroffen – es gibt keine.
    alt = env()
    for key in ("STRIPE_PRICE_MONAT", "STRIPE_PRICE_JAHR", "STRIPE_PRICE_LIFETIME"):
        pid = alt.get(key)
        if pid and pid not in ids.values():
            api(f"prices/{pid}", {"active": False})
            print(f"  – archiviert: {key} ({pid})")
    for p in api("products?limit=100&active=true", methode="GET")["data"]:
        if p["name"] == "Binderplan Lifetime":
            api(f"products/{p['id']}", {"active": False})
            print(f"  – Produkt archiviert: {p['name']}")
print("\nFertig.")
