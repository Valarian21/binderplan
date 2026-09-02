#!/usr/bin/env python3
"""Mailversand für Binderplan einrichten und dabei prüfen, bevor er scharf geschaltet wird.

Aufruf (als root auf dem VPS):
    sudo /root/apps/binderplan/venv/bin/python /root/apps/binderplan/mail_einrichten.py

Das Skript fragt nach dem Postfach, mit dem gesendet werden soll, und dessen Passwort. Es
probiert die Anmeldung, schickt eine Testnachricht und trägt die Werte erst dann in die .env
ein, wenn beides geklappt hat. So kann der Zustand „eingerichtet, aber funktioniert nicht“ gar
nicht erst entstehen — der wäre der schlimmste, weil die App dann bestätigte Adressen verlangt,
ohne Bestätigungsmails zustellen zu können.

Zwei Absender sind im Spiel und das ist Absicht:
  * das Postfach, das die Zugangsdaten hat (SMTP_USER) — es meldet sich beim Server an,
  * die Adresse, die der Kunde sieht (SMTP_FROM) — support@binderplan.app.
IONOS erlaubt einen abweichenden Absender nur, wenn die Domain im selben Vertrag liegt. Klappt
es nicht, fällt das Skript automatisch auf die Postfachadresse als Absender zurück; Antworten
gehen über Reply-To trotzdem an support@binderplan.app.
"""
import getpass
import pathlib
import re
import smtplib
import ssl
import sys
from email.message import EmailMessage

ENV = pathlib.Path("/root/apps/binderplan/.env")
HOST_STANDARD = "smtp.ionos.de"
ANTWORT_ADRESSE = "support@binderplan.app"
WUNSCH_ABSENDER = "support@binderplan.app"


def env_lesen() -> dict:
    werte = {}
    for zeile in ENV.read_text(encoding="utf-8").splitlines():
        if zeile.startswith("#") or "=" not in zeile:
            continue
        k, v = zeile.split("=", 1)
        werte[k.strip()] = v.strip()
    return werte


def env_setzen(neu: dict) -> None:
    """Vorhandene Schlüssel ersetzen, fehlende anhängen — Kommentare bleiben stehen."""
    zeilen = ENV.read_text(encoding="utf-8").splitlines()
    offen = dict(neu)
    for i, zeile in enumerate(zeilen):
        m = re.match(r"^([A-Z_]+)=", zeile)
        if m and m.group(1) in offen:
            zeilen[i] = f"{m.group(1)}={offen.pop(m.group(1))}"
    for k, v in offen.items():
        zeilen.append(f"{k}={v}")
    ENV.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    ENV.chmod(0o600)


def versuch(host, port, benutzer, passwort, absender, an) -> tuple[bool, str]:
    """Eine echte Testnachricht schicken. Gibt (geklappt, Meldung) zurück."""
    nachricht = EmailMessage()
    nachricht["Subject"] = "Binderplan – Testnachricht"
    nachricht["From"] = f"Binderplan <{absender}>"
    nachricht["To"] = an
    nachricht["Reply-To"] = ANTWORT_ADRESSE
    nachricht.set_content(
        "Diese Nachricht bestätigt, dass binderplan.app E-Mails verschicken kann.\n\n"
        f"Gesendet über: {benutzer}\nAngezeigter Absender: {absender}\n"
        f"Antworten gehen an: {ANTWORT_ADRESSE}\n\nViele Grüße\nBinderplan"
    )
    try:
        if int(port) == 465:
            server = smtplib.SMTP_SSL(host, int(port), timeout=25,
                                      context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, int(port), timeout=25)
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        with server:
            server.login(benutzer, passwort)
            server.send_message(nachricht)
        return True, "gesendet"
    except smtplib.SMTPAuthenticationError as e:
        return False, f"Anmeldung abgelehnt ({e.smtp_code}): {e.smtp_error.decode('utf-8', 'ignore')[:150]}"
    except smtplib.SMTPSenderRefused as e:
        return False, f"Absender abgelehnt ({e.smtp_code}): {e.smtp_error.decode('utf-8', 'ignore')[:150]}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:200]


def main() -> int:
    if not ENV.exists():
        print("Die Datei .env wurde nicht gefunden — bist du auf dem richtigen Server?")
        return 1
    alt = env_lesen()

    print("Mailversand für Binderplan einrichten")
    print("-" * 52)
    print("Gebraucht wird ein Postfach, das wirklich senden kann. Eine Weiterleitung")
    print(f"({ANTWORT_ADRESSE}) genügt nicht — die kann nur empfangen.\n")

    print("Zwei Wege führen zum Ziel:")
    print("  1) Ein echtes IONOS-Postfach (Adresse + Postfach-Passwort).")
    print("  2) Dein Gmail-Konto mit einem App-Passwort. Kostenlos, 500 Mails am Tag.")
    print("     Google-Konto → Sicherheit → App-Passwörter (setzt Zwei-Faktor voraus).")
    print("     Damit support@binderplan.app als Absender erlaubt ist, muss die Adresse in")
    print("     Gmail unter Einstellungen → Konten → „Senden als“ hinterlegt und bestätigt")
    print("     sein; die Bestätigungsmail kommt über deine Weiterleitung an. Zusätzlich")
    print("     gehört Google in den SPF-Eintrag der Domain (siehe Hinweis am Ende).\n")

    benutzer = input("Postfach zum Senden (volle Adresse): ").strip()
    if not benutzer or "@" not in benutzer:
        print("Das sieht nicht nach einer Adresse aus. Abgebrochen.")
        return 1
    passwort = getpass.getpass("Passwort dieses Postfachs: ")
    if not passwort:
        print("Ohne Passwort geht es nicht. Abgebrochen.")
        return 1

    # Bei einer Gmail-Adresse ist der Server ein anderer — das muss niemand auswendig wissen.
    vorschlag = "smtp.gmail.com" if benutzer.lower().endswith(("@gmail.com", "@googlemail.com")) \
        else (alt.get("SMTP_HOST") or HOST_STANDARD)
    host = input(f"SMTP-Server [{vorschlag}]: ").strip() or vorschlag
    port = input("Port [587]: ").strip() or "587"
    ziel = input(f"Testnachricht an [{ANTWORT_ADRESSE}]: ").strip() or ANTWORT_ADRESSE

    print(f"\nVersuch 1: senden über {benutzer}, angezeigter Absender {WUNSCH_ABSENDER} …")
    ok, meldung = versuch(host, port, benutzer, passwort, WUNSCH_ABSENDER, ziel)
    absender = WUNSCH_ABSENDER

    if not ok and "Absender" in meldung:
        print(f"  abgelehnt: {meldung}")
        print(f"\nVersuch 2: derselbe Zugang, aber {benutzer} als Absender …")
        ok, meldung = versuch(host, port, benutzer, passwort, benutzer, ziel)
        absender = benutzer

    if not ok:
        print(f"\nHat nicht geklappt: {meldung}")
        print("\nNichts wurde gespeichert. Häufige Ursachen:")
        print("  * Es ist eine Weiterleitung, kein Postfach — dann gibt es kein Passwort.")
        print("  * Benutzername oder Passwort stimmen nicht.")
        if "gmail" in host.lower():
            print("  * Bei Gmail braucht es ein App-Passwort, nicht das normale Kontopasswort.")
            print("    Es besteht aus 16 Buchstaben und setzt Zwei-Faktor-Anmeldung voraus.")
            print("  * „Senden als“-Adressen müssen in Gmail vorher bestätigt werden.")
        else:
            print("  * Bei IONOS muss der Zugriff über Fremdprogramme im Kundenmenü erlaubt sein.")
        return 1

    print(f"  {meldung}. Schau kurz in das Postfach {ziel}.")
    antwort = input("\nIst die Nachricht angekommen? [j/N] ").strip().lower()
    if antwort not in ("j", "ja", "y", "yes"):
        print("Nichts gespeichert — der Versand bleibt aus, bis er nachweislich funktioniert.")
        return 1

    env_setzen({
        "SMTP_HOST": host,
        "SMTP_PORT": port,
        "SMTP_USER": benutzer,
        "SMTP_PASS": passwort,
        "SMTP_FROM": absender,
        "SMTP_FROM_NAME": "Binderplan",
        "SMTP_REPLY_TO": ANTWORT_ADRESSE,
    })
    print("\nIn die .env geschrieben. Jetzt noch den Dienst neu starten:")
    print("    sudo systemctl restart app-binderplan")
    if absender != WUNSCH_ABSENDER:
        print(f"\nHinweis: Als Absender steht {absender}, weil der Server {WUNSCH_ABSENDER}")
        print(f"nicht zugelassen hat. Antworten der Kunden gehen trotzdem an {ANTWORT_ADRESSE}.")
    if "gmail" in host.lower() and absender.lower().endswith("binderplan.app"):
        print("\nWichtig für die Zustellung: Der Absender-Eintrag der Domain (SPF) erlaubt")
        print("derzeit nur IONOS-Server. Wenn Google versendet, gehört Google dazu. Im")
        print("IONOS-DNS den TXT-Eintrag von binderplan.app ändern auf:")
        print("    v=spf1 include:_spf-eu.ionos.com include:_spf.google.com ~all")
        print("Ohne diese Zeile landen die Mails leichter im Spam.")

    print("\nAb dem Neustart gilt: neue Konten müssen ihre E-Mail bestätigen, bevor sie das")
    print("Startguthaben bekommen. Kaufbestätigung, Passwort-Reset und die Kündigung per")
    print("Bestätigungslink sind dann ebenfalls aktiv.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
