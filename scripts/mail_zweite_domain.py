#!/usr/bin/env python3
"""Darf das eingerichtete Versand-Postfach auch mit einer anderen Domain als Absender senden?

Hintergrund: Binderplan und Lehreule liegen beide bei IONOS, aber ob ein Postfach der einen
Domain mit einer Adresse der anderen senden darf, hängt daran, ob beide im selben Vertrag
liegen. Das lässt sich nicht nachlesen — nur ausprobieren. Klappt es, reicht eine einzige
Mail-Lizenz für alle Produkte; klappt es nicht, braucht Lehreule ein eigenes Postfach.

Aufruf (als root, nach mail_einrichten.py):
    sudo /root/apps/binderplan/venv/bin/python /root/apps/binderplan/scripts/mail_zweite_domain.py \
        --absender support@lehreule.de --an deine@adresse.de
"""
import argparse
import pathlib
import smtplib
import ssl
import sys
from email.message import EmailMessage

ENV = pathlib.Path("/root/apps/binderplan/.env")


def env_lesen() -> dict:
    werte = {}
    for zeile in ENV.read_text(encoding="utf-8").splitlines():
        if zeile.startswith("#") or "=" not in zeile:
            continue
        k, v = zeile.split("=", 1)
        werte[k.strip()] = v.strip()
    return werte


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--absender", required=True, help="Adresse, die im Absender stehen soll")
    p.add_argument("--an", required=True, help="Wohin die Testnachricht geht")
    args = p.parse_args()

    env = env_lesen()
    benutzer, passwort = env.get("SMTP_USER", ""), env.get("SMTP_PASS", "")
    if not (benutzer and passwort):
        print("In der .env steht noch kein Postfach. Erst mail_einrichten.py laufen lassen.")
        return 1

    host, port = env.get("SMTP_HOST", "smtp.ionos.de"), int(env.get("SMTP_PORT", "587") or 587)
    n = EmailMessage()
    n["Subject"] = "Testnachricht – Absender einer zweiten Domain"
    n["From"] = f"Test <{args.absender}>"
    n["To"] = args.an
    n.set_content(f"Gesendet über {benutzer}, angezeigter Absender {args.absender}.\n"
                  "Kommt diese Nachricht an, reicht eine Mail-Lizenz für beide Produkte.")
    try:
        if port == 465:
            s = smtplib.SMTP_SSL(host, port, timeout=25, context=ssl.create_default_context())
        else:
            s = smtplib.SMTP(host, port, timeout=25)
            s.ehlo(); s.starttls(context=ssl.create_default_context()); s.ehlo()
        with s:
            s.login(benutzer, passwort)
            s.send_message(n)
    except smtplib.SMTPSenderRefused as e:
        print(f"Abgelehnt: {args.absender} darf über dieses Postfach nicht senden.")
        print(f"  Serverantwort: {e.smtp_code} {e.smtp_error.decode('utf-8', 'ignore')[:150]}")
        print("  → Lehreule braucht ein eigenes Postfach (Lizenz aufstocken).")
        return 2
    except Exception as e:
        print(f"Fehlgeschlagen: {type(e).__name__}: {e}")
        return 1

    print(f"Angenommen. Sieh in {args.an} nach — kommt sie an, trägt eine Lizenz beide Produkte.")
    print("Wichtig: Der SPF-Eintrag der zweiten Domain muss IONOS ebenfalls erlauben.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
