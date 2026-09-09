# Binderplan – Design-System und Bauregeln

Stand 10.09.2026, eingeführt mit dem Produkt-Audit vom 09.09.2026. Gilt für die Werkbank
(`index.html` + `assets/*.js|css`). Die Startseite (`landing.html`, `landing.css`) hat ihre eigene,
lautere Sprache und übernimmt nur die Farben.

## Dateien

| Datei | Inhalt |
|---|---|
| `index.html` | nur noch Markup (≈ 1.300 Zeilen): Kopfzeile, Werkbank, Orte, Dialoge |
| `assets/tokens.css` | alle Tokens: Farben je Thema, Typo-Skala, Abstände, Ebenen, Marke |
| `assets/app.css` | Komponenten und Orte |
| `assets/kern.js` | Übersetzungen `T`, Zustand `S`, Icons, Handy-Grundlagen, Design, Sprache |
| `assets/konto.js` | `boot()`, Konto, Tarife, Startseite, Profilseite |
| `assets/werkbank.js` | Menüs & Dialoge, Binder anlegen, Filter, Suche, Binder-Panel, Blättern, Thema, Fotos |
| `assets/vitrine.js` | Vitrine, Veröffentlichen, fremde Binder, öffentliche Profile, Zurück-Taste |
| `assets/preise.js` | Sammlungs-Helfer im Binder, Preise |
| `assets/planer.js` | Fächer, Auswahl, Seiten, Speichern, PDF, Ereignisse, Rückgängig |
| `assets/detail.js` | Suche als Schublade, Inspektor, Filter, Bildmotiv, Kartendialog, Import |
| `assets/artwork.js` | Artwork-Seiten; ruft am Ende `boot()` |
| `assets/markt.js`, `assets/sammlung.js` | Markt und Sammlung (seit Welle B/C) |

Die Skripte laden per `defer` **in dieser Reihenfolge**; sie teilen sich den globalen Namensraum
wie zuvor der eine Inline-Block. Neue Funktionen kommen in die Datei ihres Bereichs; ein neuer
Bereich bekommt eine neue Datei, die in `index.html` **vor** `markt.js` eingehängt wird.

Bei jeder Änderung an `assets/*` die `?v=`-Nummer hochsetzen – das Deploy-Skript setzt sie
automatisch aus dem Zeitstempel, `scripts/pruefen.py` verweigert Assets ohne `?v=`.

## Tokens (tokens.css)

**Typo-Skala** – nur diese neun Stufen, nie freie Pixelwerte:

| Token | px | Verwendung |
|---|---|---|
| `--t-xs` | 11 | Badges, Kachel-Meta – Untergrenze, nie kleiner |
| `--t-s` | 12,5 | Hilfstexte, Tabellenköpfe, Chips |
| `--t-m` | 13 | Menüs, Formulare, Fließtext in Dialogen |
| `--t-mp` | 14 | Fließtext, Preise in Kacheln |
| `--t-l` | 16 | Dialogtext, Kartenname im Detail |
| `--t-xl` | 20 | Dialog-Titel, Ortsname |
| `--t-2xl` | 22 | Kennzahlen in Kacheln |
| `--t-3xl` | 26 | große Kennzahlen |
| `--t-4xl` | 34 | Gruß auf der Startseite |

**Abstände** – 4-px-Raster: `--r-1` 4 · `--r-2` 8 · `--r-3` 12 · `--r-4` 16 · `--r-6` 24 · `--r-8` 32.
Kacheln innen 16, Dialoge innen 24, Abschnittsabstand 32. Mindest-Trefffläche am Handy 40 × 40 px.

**Ebenen** – genau fünf: `--z-inhalt` 1 (Sticky-Köpfe) · `--z-ort` 40 (Vollseiten: Start, Sammlung,
Markt, Vitrine, Profil – es ist immer nur eine sichtbar) · `--z-leiste` 60 (Handy-Leiste, Auswahlleiste,
Sheets) · `--z-menue` 80 (Menüs, Popover, Filter-Überlagerung) · `--z-dialog` 100 (Overlays; Toasts
darüber). Keine neuen Zahlen; Abstufungen als `calc(var(--z-ort) + 1)`.

**Farben** – nur über Tokens. Thema-abhängig: `--bg --ink --mut --line --card --panel2 --akzent-*
--blau-* --gruen-* --link --aktiv-*`. Marke (in beiden Themen gleich): `--marke-blau #2A4B9B`,
`--marke-gelb #F5C518`, `--marke-rot #E4322B`, `--kontur #14161C`, `--seite-dunkel #14161a`, `--weiss`.
Neue Hex-Werte gehören in `tokens.css`, nicht in Komponenten – dann stimmt auch das dunkle Design.

## Komponenten (app.css)

- **Button** `.btn` (rot, eine je Ansicht) · `.btn.sekundaer` · `.btn.leise` (ohne Rahmen) ·
  `.btn.gefahr` (rote Schrift) · `.btn.klein`.
- **Chip** `.chip` · `.chip.on` · `.chip.zahl` mit `<b>` für die Zahl. Reiter, Filter, Segmente sind Chips.
- **Kachel** `.mk-kachel` (Kennzahl mit Bewegung), `.tk` (Trefferkarte), `.sm-karte` (Sammlung),
  `.bcard` (Binder auf der Startseite – nutzt das Stapelbild `api/binders/{id}/stapel.webp`).
- **Leerzustand** `.leer` mit `<strong>`, Satz und einem `.btn` – für Binder, Sammlung, Wunschliste,
  Vitrine, Alarme.
- **Dialog** immer `<div class="overlay hidden" id="modal-…"><div class="modal">…` – über
  `modalOeffnen()` im Ebenen-Stack, Escape und ✕ schließen. `pruefen.py` verweigert andere Formen.
- **Ort** (Vollseite): `position: fixed; top: 54px; z-index: var(--z-ort)`; wird über `ansicht(name)`
  geöffnet, die alle anderen Orte schließt. Gäste sehen einen Ort mit `gastOrt()`-Kasten statt eines
  Anmelde-Dialogs.
- **Werkbank**: der Binder ist das Dokument. Suche = Schublade links (`body.suche-offen`, Desktop) bzw.
  Sheet von unten (Handy); Inspektor rechts für genau ein gewähltes Fach (Desktop); am Handy bleibt
  das Fach-Menü.

## Backend (Phase 6, 10.09.2026)

- **Abschnitte in Dateien:** `auth.py` (Konten, Sitzungen, Limits, E-Mail), `bilder.py` (Bild-Cache),
  `binder.py` (Binder, Wertverlauf), `pdf.py` (Platzhalter, Checkliste, Kaufliste), `katalog.py` (Meta,
  Admin-Kennzahlen, Suche, Import) sind **Abschnitte der main.py**: `_abschnitt("name")` führt sie an
  ihrer alten Stelle im Namensraum von main.py aus. Kein Import, keine Exporte – dieselben Helfer,
  dieselbe Reihenfolge, nur lesbare Dateien und Tracebacks mit Dateinamen. Die eigenständigen Module
  (`abo`, `artwork`, `vitrine`, `sammlung`, `markt`, `alarme`, `analytics`, `seiten`, `themen`,
  `fotoimport`, `wert`) bleiben echte Module mit `register()`.
- **Speichern:** `PUT /api/binders/{id}` trägt `updated_at`; weicht es vom Server ab → 409 `konflikt`,
  der Browser lädt den fremden Stand und sagt es. Antwort liefert das neue `updated_at`.
- **PDF:** `GET …/pdf_stand` liefert während eines Karten-PDFs `{seiten, gesamt}`; der Toast zählt mit.
- **Hintergrund-Jobs:** `_job(name, fn, limit)` im Takt – Zeitlimit, Stand in `kv`
  (`job:<name>:letzter/dauer/fehler`), Preislauf als Kennzahl in `/api/admin/stats` (`jobs` komplett).
- **Cache:** Markt-Antworten je Tag/Argumente/Pro im Prozess; `/api/meta` zehn Minuten + ETag/304;
  Stapelbild je Binder (`/stapel.webp?s=<stand>`, ein Jahr unveränderlich, Vorwärmen nach Speichern
  und beim Start).
- **Offline:** `/sw.js` (Datei `assets/sw.js`) hält App-Hülle, Meta, Konto und zuletzt geöffnete
  Binder als Rückfall. Schreibzugriffe werden nicht gepuffert.
- **Sicherheit:** Header in nginx (HSTS, nosniff, X-Frame-Options, Referrer-Policy, Permissions-Policy);
  Login-Drossel 12 Versuche / 15 min je IP (`LIMITS` in auth.py); Sitzungen verfallen nach 365 Tagen.
- **Prüfen:** `scripts/pruefen.py` (statisch) und `scripts/rauchtest.py` (27 Prüfungen gegen den
  laufenden Dienst, mit `BP_TOKEN` auch die Konto-Wege) – beide laufen im Deploy.

## Bauregeln

1. `python3 scripts/pruefen.py` vor jedem Deploy (das Deploy-Skript ruft es): doppelte Funktionen
   über alle Dateien, Dialog-Klassen, i18n-Schlüssel in `de` **und** `en`, Assets mit `?v=`,
   Schriftgrößen unter 11 px, pyflakes.
2. Jede Funktion existiert genau einmal. Die zweite Definition gewinnt still – so blieb am 09.09.
   der Binder-Wechsler leer.
3. Jede Aufgabe hat genau einen Einstieg (Neuer Binder → Wechsler, Drucken → Binder-Kopf, Profil →
   Konto). Neue Menüeinträge nur, wenn es den Weg noch nirgends gibt.
4. Handy zuerst denken: erste Bildschirmhälfte trägt Inhalt, nicht Chrome. Chip-Reihen wischbar,
   Tabellen als Karten, Kennzahlen als Zeile.
5. Messen statt schätzen: `node scripts/messen.js <desktop|mobil|dunkel|gast|public>` (Playwright aus
   `services/browser_render`, `BP_TOKEN` für ein Konto) liefert je Ansicht Anfragen, Übertragung,
   Überlauf, Bedienelemente < 32 px, Text < 11 px, Konsolenfehler und Screenshots. `scripts/kontrast.js`
   prüft WCAG-Kontraste hell und dunkel.
