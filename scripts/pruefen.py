#!/usr/bin/env python3
"""Bauprüfung vor jedem Deploy – fängt die Fehlerklassen, die uns schon getroffen haben.

Aufruf: python3 scripts/pruefen.py [wurzel]   (Standard: Ordner über scripts/)
Rückgabe 1, sobald ein Befund vorliegt. Geprüft wird:
  1. doppelte `function name(` je Datei und über index.html + assets/*.js hinweg
  2. jedes Element mit id="modal-…" trägt die Klasse `overlay` (sonst rendert es im Fluss)
  3. jeder data-i18n-Schlüssel existiert in T.de und T.en
  4. jede eingebundene assets-Datei existiert
  5. pyflakes über alle Python-Module (wenn installiert)
"""
import os, re, subprocess, sys

wurzel = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
befunde = []
html = open(os.path.join(wurzel, 'index.html'), encoding='utf-8').read()
js_dateien = {'index.html': html}
assets_dir = os.path.join(wurzel, 'assets')
for name in sorted(os.listdir(assets_dir)):
    if name.endswith('.js'):
        js_dateien['assets/' + name] = open(os.path.join(assets_dir, name), encoding='utf-8').read()

# 1. doppelte Funktionen
gesehen = {}
for datei, quelle in js_dateien.items():
    for m in re.finditer(r'^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(', quelle, re.M):
        name = m.group(1)
        zeile = quelle.count('\n', 0, m.start()) + 1
        if name in gesehen:
            befunde.append(f'doppelte Funktion {name}: {gesehen[name]} und {datei}:{zeile}')
        else:
            gesehen[name] = f'{datei}:{zeile}'

# 2. Dialoge
for m in re.finditer(r'<div\s+class="([^"]*)"\s+id="(modal-[\w-]+)"', html):
    if 'overlay' not in m.group(1).split():
        befunde.append(f'Dialog #{m.group(2)} ohne Klasse overlay (hat: "{m.group(1)}")')
for m in re.finditer(r'<div\s+id="(modal-[\w-]+)"\s+class="([^"]*)"', html):
    if 'overlay' not in m.group(2).split():
        befunde.append(f'Dialog #{m.group(1)} ohne Klasse overlay (hat: "{m.group(2)}")')

# 3. Übersetzungsschlüssel – de reicht von „de: {" bis „en: {", en bis zum Ende des T-Objekts
t_start = html.find('const T = {')
t_ende = html.find('\n};', t_start)
de_start = html.find('  de: {', t_start); en_start = html.find('  en: {', t_start)
schl = re.compile(r'(?:^\s*|,\s+)([a-z][a-z0-9_]*):\s', re.M)
de = set(schl.findall(html[de_start:en_start]))
en = set(schl.findall(html[en_start:t_ende]))
schluessel = set(re.findall(r'data-i18n(?:-title|-ph)?="([a-z0-9_]+)"', html))
for datei, quelle in js_dateien.items():
    schluessel |= set(re.findall(r"\bt\('([a-z0-9_]+)'\)", quelle))
for k in sorted(schluessel):
    if k not in de:
        befunde.append(f'Übersetzung fehlt (de): {k}')
    elif k not in en:
        befunde.append(f'Übersetzung fehlt (en): {k}')

# 4. Assets
for m in re.finditer(r'(?:src|href)="(assets/[^"?]+)', html):
    if not os.path.exists(os.path.join(wurzel, m.group(1))):
        befunde.append(f'Asset fehlt: {m.group(1)}')

# 5. pyflakes
py = [f for f in os.listdir(wurzel) if f.endswith('.py')]
venv_py = os.path.join(wurzel, 'venv', 'bin', 'python')
for interp in (venv_py, sys.executable):
    try:
        r = subprocess.run([interp, '-m', 'pyflakes', *py], cwd=wurzel, capture_output=True, text=True, timeout=120)
        if r.returncode not in (0, 1) and 'No module named' in r.stderr:
            continue
        for zeile in r.stdout.splitlines():
            # Unbenutzte Importe stören nicht; alles andere (undefinierte Namen, Syntax) blockiert
            if 'imported but unused' in zeile or 'redefinition of unused' in zeile:
                continue
            befunde.append('pyflakes: ' + zeile)
        break
    except (FileNotFoundError, subprocess.TimeoutExpired):
        continue

if befunde:
    print('BAUPRÜFUNG FEHLGESCHLAGEN – %d Befund(e):' % len(befunde))
    for b in befunde:
        print('  -', b)
    sys.exit(1)
print('Bauprüfung ok: %d Funktionen, %d Übersetzungsschlüssel, %d Dialoge geprüft' % (
    len(gesehen), len(schluessel), len(re.findall(r'id="modal-', html))))
