"""Öffentliche Seiten: Sets, Pokémon und Karten als indexierbare HTML-Seiten.

Bis September 2026 gab es außer der Startseite nichts zu finden — die App steht auf
`noindex`, die Vitrine ist JSON. Konkurrenten leben von genau diesen Seiten: „Glurak Base
Set Preis" führt zu ihnen, nicht hierher. Jede Seite hier beantwortet eine Suchanfrage
(„Was ist das Set wert?", „Was kostet Glurak?", „Welche Karten gibt es von Pikachu?") und
führt mit einem Knopf in die App, wo dieselbe Karte geplant, gesammelt und gedruckt wird.

Die Seiten werden bei jedem Aufruf aus der Datenbank gebaut — die Abfragen sind klein,
und ein Cache-Header von einer Stunde reicht, weil die Preise nur einmal am Tag wechseln.
Japanische Karten bleiben draußen (kein Zielmarkt, und die Namen wären japanisch).
"""

import datetime
import html
import json
import re

from fastapi import HTTPException, Request, Response
from fastapi.responses import HTMLResponse

_dep = {}
APP_URL = "https://binderplan.app"
SEITEN_HEADERS = {"Cache-Control": "public, max-age=3600"}
SITEMAP_HEADERS = {"Cache-Control": "public, max-age=86400"}
KARTEN_JE_SITEMAP = 10000


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def eur(n, stellen=2):
    if n is None:
        return "–"
    s = f"{n:,.{stellen}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return s + " €"


def proz(n):
    if n is None:
        return ""
    return f"{n:+.1f} %".replace(".", ",")


def kl(n):
    return "plus" if n is not None and n > 0.05 else ("minus" if n is not None and n < -0.05 else "neutral")


TYPEN_DE = {"Fire": "Feuer", "Water": "Wasser", "Grass": "Pflanze", "Lightning": "Elektro", "Psychic": "Psycho",
            "Fighting": "Kampf", "Darkness": "Finsternis", "Metal": "Metall", "Dragon": "Drache", "Fairy": "Fee",
            "Colorless": "Farblos"}


def _symbol(set_id, symbol, gross=False):
    """Set-Symbol, oder ein Platzhalter derselben Größe.

    Ein Drittel der Sets hat kein Symbol; ohne Platzhalter sprangen die Namen in der Liste
    hin und her. Der Platzhalter trägt die Set-Kurzform, die viele Sammler ohnehin kennen."""
    kl = "sym gross" if gross else "sym"
    if not symbol:
        return f'<span class="{kl} symleer">{esc(set_id[:4].upper())}</span>'
    return (f'<img class="{kl}" src="/api/img/set/{esc(set_id)}" alt="" loading="lazy" width="24" height="24"'
            f' onerror="this.outerHTML=\'<span class=&quot;{kl} symleer&quot;>{esc(set_id[:4].upper())}</span>\'">')


def _bewegung(eur_jetzt, schnitt):
    """Dieselbe Regel wie markt.py: Ausreißer und Cent-Karten tragen keine Bewegung."""
    if not eur_jetzt or not schnitt or schnitt <= 0 or eur_jetzt < 1 or schnitt < 1:
        return None
    q = eur_jetzt / schnitt
    if q > 3 or q < 1 / 3:
        return None
    return round((q - 1) * 100, 1)


SETS_SKRIPT = """
<script>
(function () {
  var q = document.getElementById('q'), zahl = document.getElementById('qn'), nix = document.getElementById('nix');
  var zeilen = [].slice.call(document.querySelectorAll('tbody tr'));
  var serien = [].slice.call(document.querySelectorAll('section.serie'));
  var anker = document.querySelector('nav.anker');
  q.addEventListener('input', function () {
    var s = q.value.trim().toLowerCase();
    var treffer = 0;
    zeilen.forEach(function (tr) {
      var passt = !s || tr.dataset.name.indexOf(s) >= 0;
      tr.hidden = !passt;
      if (passt) treffer++;
    });
    serien.forEach(function (sec) {
      sec.hidden = !sec.querySelector('tbody tr:not([hidden])');
    });
    anker.hidden = !!s;
    nix.hidden = treffer > 0;
    zahl.textContent = s ? treffer + (treffer === 1 ? ' Set' : ' Sets') : '';
  });
})();
</script>
"""

CSS = """
:root{--bg:#F6F5F0;--card:#fff;--ink:#14161C;--mut:#5F6470;--line:#DDDBD2;--blau:#2A4B9B;--blau-fg:#fff;--gelb:#F5C518;
--gruen:#1F7A4D;--rot:#C8352B;--panel:#ECEAE2;color-scheme:light}
@media(prefers-color-scheme:dark){:root{--bg:#14161C;--card:#1C1F27;--ink:#ECEEF2;--mut:#A3A8B4;--line:#2E323C;--blau:#7C9BF0;
--blau-fg:#0F1117;--gruen:#5BC489;--rot:#FF6B62;--panel:#242832;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:Archivo,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased}
a{color:var(--blau)}
.kopf{display:flex;align-items:center;gap:14px;padding:12px 18px;border-bottom:1.5px solid var(--line);background:var(--card);position:sticky;top:0;z-index:2}
.kopf .logo{font-family:Bungee,Archivo,sans-serif;font-weight:800;letter-spacing:.04em;text-decoration:none;color:var(--ink);font-size:17px;display:flex;align-items:center;gap:8px}
.kopf .logo svg{width:22px;height:22px;display:block;flex:none}
.kopf nav{margin-left:auto;display:flex;gap:14px;font-size:13.5px;font-weight:600}
.kopf nav a{text-decoration:none;color:var(--mut)}.kopf nav a.cta{color:var(--blau-fg);background:var(--blau);padding:7px 14px;border-radius:999px}
.rumpf{max-width:1080px;margin:0 auto;padding:26px 18px 60px}
.brot{font-size:12.5px;color:var(--mut);margin-bottom:10px}.brot a{color:var(--mut);text-decoration:none}.brot a:hover{color:var(--blau)}
h1{font-size:clamp(26px,4vw,38px);line-height:1.1;letter-spacing:-.02em;margin:0 0 6px;font-weight:800}
.unter{color:var(--mut);margin:0 0 18px;max-width:70ch}
h2{font-size:19px;margin:34px 0 4px;letter-spacing:-.01em}h2+.unter{margin-bottom:12px}
.kacheln{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:16px 0 8px}
.k{background:var(--card);border:1.5px solid var(--line);border-radius:12px;padding:12px 14px}
.k small{display:block;font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;color:var(--mut);font-weight:700}
.k b{display:block;font-size:22px;font-weight:800;line-height:1.15;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.k span{font-size:12px;color:var(--mut)}
.plus{color:var(--gruen)}.minus{color:var(--rot)}.neutral{color:var(--mut)}
.dl{font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap}
.karten{display:grid;grid-template-columns:repeat(auto-fill,minmax(128px,1fr));gap:12px}
.karte{text-decoration:none;color:inherit;display:block}
.karte img{width:100%;aspect-ratio:63/88;object-fit:contain;border-radius:8px;background:var(--panel);display:block}
.karte .p{font-weight:800;margin-top:6px;font-variant-numeric:tabular-nums}.karte .n{font-size:12px;color:var(--mut);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tabr{overflow-x:auto;background:var(--card);border:1.5px solid var(--line);border-radius:12px}
table{border-collapse:collapse;width:100%;font-size:13.5px}th,td{padding:8px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle}
th{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut)}tr:last-child td{border-bottom:0}
td.r,th.r{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}td a{text-decoration:none;color:inherit;font-weight:600}td a:hover{color:var(--blau)}
td img{width:30px;border-radius:3px;display:block;background:var(--panel)}
.cta{display:inline-block;background:var(--blau);color:var(--blau-fg);text-decoration:none;font-weight:800;padding:11px 20px;border-radius:999px;margin:8px 0}
.cta.zweit{background:transparent;color:var(--blau);border:1.5px solid var(--blau)}
.detail{display:grid;grid-template-columns:280px minmax(0,1fr);gap:26px;align-items:start}
.detail img.gross{width:100%;border-radius:12px;background:var(--panel)}
@media(max-width:700px){.detail{grid-template-columns:1fr}.detail img.gross{max-width:260px;margin:0 auto;display:block}}
.spark{width:100%;height:70px;display:block}
.fuss{border-top:1.5px solid var(--line);margin-top:40px;padding:18px;font-size:12.5px;color:var(--mut);text-align:center}
.fuss a{color:var(--mut)}.hin{font-size:12px;color:var(--mut);line-height:1.5}
.sym{width:24px;height:24px;object-fit:contain;vertical-align:middle;margin-right:8px;flex:none}
.sym.gross{width:32px;height:32px}
.symleer{display:inline-flex;align-items:center;justify-content:center;background:var(--panel);border-radius:5px;
  font-family:var(--mono);font-size:9px;font-weight:600;color:var(--mut);letter-spacing:.02em}
.sym.gross.symleer{font-size:11px}
.liste{columns:2;column-gap:24px;font-size:14px}.liste a{display:block;text-decoration:none;color:inherit;padding:3px 0;border-bottom:1px solid var(--line)}
.liste a:hover{color:var(--blau)}@media(max-width:700px){.liste{columns:1}}
.setsuche{display:flex;align-items:center;gap:12px;margin:20px 0 14px}
.setsuche input{flex:1;max-width:420px;font:inherit;font-size:15px;padding:10px 14px;border:1.5px solid var(--line);
  border-radius:10px;background:var(--card);color:var(--ink)}
.setsuche input:focus{outline:2px solid var(--blau);outline-offset:1px;border-color:var(--blau)}
.setsuche span{font-size:13px;color:var(--mut);font-variant-numeric:tabular-nums}
nav.anker{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 24px}
nav.anker a{font-size:12.5px;font-weight:600;text-decoration:none;color:var(--mut);background:var(--card);
  border:1px solid var(--line);border-radius:999px;padding:5px 12px}
nav.anker a:hover{color:var(--blau);border-color:var(--blau)}
section.serie h2{scroll-margin-top:70px}
.leer{color:var(--mut);padding:20px 0}
tr.gruppe td{background:var(--panel);font-size:12.5px;color:var(--mut);
  border-top:1px solid var(--line);padding-top:12px}
tr.gruppe strong{color:var(--ink);font-size:14px}
td a{display:inline-flex;align-items:center}
"""


def _seite(titel, beschreibung, pfad, inhalt, bild=None, ld=None, noindex=False):
    url = APP_URL + pfad
    kopf = f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(titel)}</title>
<meta name="description" content="{esc(beschreibung)}">
<link rel="canonical" href="{esc(url)}">
{'<meta name="robots" content="noindex">' if noindex else ''}
<meta property="og:title" content="{esc(titel)}"><meta property="og:description" content="{esc(beschreibung)}">
<meta property="og:url" content="{esc(url)}"><meta property="og:type" content="article"><meta property="og:site_name" content="Binderplan">
<meta property="og:image" content="{esc(bild or APP_URL + '/assets/hero.png')}">
<meta name="twitter:card" content="{'summary' if bild else 'summary_large_image'}">
<link rel="icon" href="/icon-192.png"><meta name="theme-color" content="#2A4B9B">
<link rel="stylesheet" href="/assets/schrift.css">
<style>{CSS}</style>
{('<script type="application/ld+json">' + json.dumps(ld, ensure_ascii=False) + '</script>') if ld else ''}
</head><body>
<header class="kopf"><a class="logo" href="/"><svg width="22" height="22" viewBox="0 0 44 44" aria-hidden="true"><rect x="2" y="2" width="40" height="40" rx="9" fill="#F5C518" stroke="#14161C" stroke-width="3"/><rect x="9.5" y="9.5" width="7.5" height="7.5" rx="1" fill="#2A4B9B"/><rect x="27" y="9.5" width="7.5" height="7.5" rx="1" fill="#E4322B"/><rect x="18.2" y="18.2" width="7.5" height="7.5" rx="1" fill="#2A4B9B"/><g stroke="#14161C" stroke-width="2" fill="none"><path d="M9 9h26v26H9z"/><path d="M17.7 9v26M26.3 9v26M9 17.7h26M9 26.3h26"/></g></svg>BINDERPLAN</a>
<nav><a href="/sets">Sets</a><a href="/app#vitrine">Vitrine</a><a class="cta" href="/app">App öffnen</a></nav></header>
<main class="rumpf">"""
    fuss = f"""</main>
<footer class="fuss">Preise: Cardmarket-Trend je Karte, täglich aktualisiert · <a href="/">Binderplan</a> · <a href="/recht">Impressum &amp; Datenschutz</a>
· Pokémon und alle Kartennamen sind Marken ihrer Inhaber; Binderplan steht in keiner Verbindung zu The Pokémon Company.</footer>
</body></html>"""
    return HTMLResponse(kopf + inhalt + fuss, headers=SEITEN_HEADERS)


def _brot(*teile):
    aus = ['<a href="/">Binderplan</a>']
    for name, href in teile:
        aus.append(f'<a href="{esc(href)}">{esc(name)}</a>' if href else esc(name))
    return '<div class="brot">' + " › ".join(aus) + "</div>"


def _kachel(lbl, wert, unter="", klasse=""):
    return f'<div class="k"><small>{esc(lbl)}</small><b class="{klasse}">{wert}</b><span>{unter}</span></div>'


def _kartenraster(karten, mit_set=False):
    aus = []
    for k in karten:
        name = k.get("name_de") or k.get("name_en") or k["id"]
        unter = f"{k.get('set_name') or ''} · " if mit_set else ""
        aus.append(f'<a class="karte" href="/karte/{esc(k["id"])}"><img loading="lazy" src="/api/img/card/{esc(k["id"])}" alt="{esc(name)}" width="128" height="179">'
                   f'<div class="p">{eur(k.get("eur"), 0 if (k.get("eur") or 0) >= 100 else 2)}</div>'
                   f'<div class="n" title="{esc(name)}">{esc(unter)}{esc(k.get("local_id") or "")} · {esc(name)}</div></a>')
    return '<div class="karten">' + "".join(aus) + "</div>"


def _sparkline(punkte):
    p = [x for x in punkte if x[1] is not None]
    if len(p) < 2:
        return ""
    werte = [x[1] for x in p]
    lo, hi = min(werte), max(werte)
    if lo == hi:
        lo, hi = lo - 1, hi + 1
    w, h = 600, 70
    xs = lambda i: 2 + i / (len(p) - 1) * (w - 4)
    ys = lambda v: 4 + (1 - (v - lo) / (hi - lo)) * (h - 8)
    d = " ".join(f"{'M' if i == 0 else 'L'}{xs(i):.1f} {ys(v):.1f}" for i, (_t, v) in enumerate(p))
    farbe = "var(--gruen)" if werte[-1] >= werte[0] else "var(--rot)"
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" preserveAspectRatio="none" aria-hidden="true">'
            f'<path d="{d}" fill="none" stroke="{farbe}" stroke-width="2.5" stroke-linejoin="round"/></svg>'
            f'<div class="hin">{esc(p[0][0])} – {esc(p[-1][0])} · {len(p)} Messpunkte</div>')


def register(app, *, get_db, app_url=None):
    global APP_URL
    _dep.update(get_db=get_db)
    if app_url:
        APP_URL = app_url.rstrip("/")

    def _markt(con, ebene, schluessel):
        try:
            tag = con.execute("SELECT MAX(datum) d FROM markt_tag").fetchone()["d"]
            if not tag:
                return None
            r = con.execute("SELECT * FROM markt_tag WHERE ebene=? AND schluessel=? AND datum=?",
                            (ebene, str(schluessel), tag)).fetchone()
            return dict(r) if r else None
        except Exception:
            return None

    KARTE_SELECT = ("SELECT c.id, c.set_id, c.local_id, c.local_num, c.name_de, c.name_en, c.rarity, c.illustrator,"
                    " c.first_dex, c.release_date, c.region, c.category, c.hp, c.types,"
                    " COALESCE(p.eur, p.eur_geschaetzt) eur, p.eur_avg7, p.eur_avg30, p.eur_low, p.eur_holo, p.usd,"
                    " p.cm_url, p.updated_at preis_stand, s.name set_name, s.name_en set_name_en, s.serie_name,"
                    " s.release_date set_datum, s.total, s.official"
                    " FROM cards c LEFT JOIN card_prices p ON p.card_id = c.id LEFT JOIN sets s ON s.id = c.set_id")

    # ------------------------------------------------------------------ robots & sitemaps

    @app.get("/robots.txt")
    def robots():
        return Response("User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /app\n"
                        f"Sitemap: {APP_URL}/sitemap.xml\n", media_type="text/plain", headers=SITEMAP_HEADERS)

    def _xml(urls):
        zeilen = "".join(f"<url><loc>{esc(u)}</loc>{f'<lastmod>{d}</lastmod>' if d else ''}</url>" for u, d in urls)
        return Response('<?xml version="1.0" encoding="UTF-8"?>'
                        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{zeilen}</urlset>',
                        media_type="application/xml", headers=SITEMAP_HEADERS)

    @app.get("/sitemap.xml")
    def sitemap_index():
        con = get_db()
        n = con.execute("SELECT COUNT(*) c FROM cards WHERE COALESCE(region,'intl')='intl'").fetchone()["c"]
        con.close()
        teile = ["sitemap-seiten.xml", "sitemap-sets.xml", "sitemap-pokemon.xml"]
        teile += [f"sitemap-karten-{i + 1}.xml" for i in range((n + KARTEN_JE_SITEMAP - 1) // KARTEN_JE_SITEMAP)]
        heute = datetime.date.today().isoformat()
        body = "".join(f"<sitemap><loc>{APP_URL}/{t}</loc><lastmod>{heute}</lastmod></sitemap>" for t in teile)
        return Response('<?xml version="1.0" encoding="UTF-8"?>'
                        f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</sitemapindex>',
                        media_type="application/xml", headers=SITEMAP_HEADERS)

    @app.get("/sitemap-seiten.xml")
    def sitemap_seiten():
        return _xml([(APP_URL + "/", None), (APP_URL + "/en", None), (APP_URL + "/sets", None)])

    @app.get("/sitemap-sets.xml")
    def sitemap_sets():
        con = get_db()
        urls = [(f"{APP_URL}/set/{r['id']}", None) for r in con.execute(
            "SELECT id FROM sets WHERE COALESCE(region,'intl')='intl' ORDER BY release_date DESC")]
        con.close()
        return _xml(urls)

    @app.get("/sitemap-pokemon.xml")
    def sitemap_pokemon():
        con = get_db()
        urls = [(f"{APP_URL}/pokemon/{r['first_dex']}", None) for r in con.execute(
            "SELECT DISTINCT first_dex FROM cards WHERE first_dex IS NOT NULL AND COALESCE(region,'intl')='intl'"
            " ORDER BY first_dex")]
        con.close()
        return _xml(urls)

    @app.get("/sitemap-karten-{n}.xml")
    def sitemap_karten(n: int):
        con = get_db()
        urls = [(f"{APP_URL}/karte/{r['id']}", None) for r in con.execute(
            "SELECT id FROM cards WHERE COALESCE(region,'intl')='intl' ORDER BY set_id, local_num, id LIMIT ? OFFSET ?",
            (KARTEN_JE_SITEMAP, max(0, n - 1) * KARTEN_JE_SITEMAP))]
        con.close()
        if not urls:
            raise HTTPException(404)
        return _xml(urls)

    # ------------------------------------------------------------------------ Sets

    @app.get("/sets")
    def sets_uebersicht():
        con = get_db()
        sets = [dict(r) for r in con.execute(
            "SELECT s.id, s.name, s.name_en, s.serie_id, s.serie_name, s.release_date, s.total, s.symbol,"
            " (SELECT COUNT(*) FROM cards c WHERE c.set_id = s.id) n"
            " FROM sets s WHERE COALESCE(s.region,'intl')='intl' ORDER BY s.release_date DESC")]
        markt = {}
        try:
            tag = con.execute("SELECT MAX(datum) d FROM markt_tag").fetchone()["d"]
            markt = {r["schluessel"]: dict(r) for r in con.execute(
                "SELECT schluessel, summe, bew30 FROM markt_tag WHERE ebene='set' AND datum=?", (tag,))}
        except Exception:
            pass
        con.close()
        # Gruppiert wird nach serie_id, nicht nach dem Textfeld serie_name: dasselbe Ären-Kürzel
        # trägt je nach Set mal den deutschen, mal den englischen Namen (swsh: 25× „Schwert &
        # Schild", 1× „Sword & Shield"). Nach Text gruppiert erschienen sechs Ären doppelt und
        # ihre Sets waren auf zwei Abschnitte verteilt (Audit 11.09.2026, E1). Die Überschrift
        # ist der häufigste Name je Kürzel.
        serien, namen = {}, {}
        for s in sets:
            sid = s.get("serie_id") or (s["serie_name"] or "weitere")
            serien.setdefault(sid, []).append(s)
            if s.get("serie_name"):
                namen.setdefault(sid, {})
                namen[sid][s["serie_name"]] = namen[sid].get(s["serie_name"], 0) + 1
        serien = {max(namen.get(sid, {"Weitere": 1}).items(), key=lambda x: x[1])[0]: liste
                  for sid, liste in serien.items()}
        # Ein Suchfeld über 203 Sets: die Seite war 14.478 px lang, und wer das Grundset
        # suchte, scrollte an 190 Sets vorbei. Gefiltert wird im Browser über die Daten, die
        # ohnehin schon auf der Seite stehen — kein zweiter Abruf, und ohne JavaScript
        # bleibt die vollständige Liste stehen.
        anker = "".join(f'<a href="#s-{i}">{esc(serie)}</a>' for i, serie in enumerate(serien))
        teile = []
        for i, (serie, liste) in enumerate(serien.items()):
            zeilen = "".join(
                f'<tr data-name="{esc(((s["name"] or "") + " " + (s["name_en"] or "") + " " + s["id"]).lower())}">'
                f'<td><a href="/set/{esc(s["id"])}">{_symbol(s["id"], s.get("symbol"))}{esc(s["name"] or s["name_en"])}</a></td>'
                f'<td class="r">{(s["release_date"] or "")[:4]}</td><td class="r">{s["n"] or s["total"] or ""}</td>'
                f'<td class="r">{eur((markt.get(s["id"]) or {}).get("summe"), 0)}</td>'
                f'<td class="r dl {kl((markt.get(s["id"]) or {}).get("bew30"))}">{proz((markt.get(s["id"]) or {}).get("bew30"))}</td></tr>'
                for s in liste)
            teile.append(f'<section class="serie" id="s-{i}"><h2>{esc(serie)}</h2>'
                         f'<div class="tabr"><table><thead><tr><th>Set</th><th class="r">Jahr</th><th class="r">Karten</th>'
                         f'<th class="r">Wert aller Karten</th><th class="r" title="Heutiger Trendpreis gegenüber dem Cardmarket-Schnitt der letzten 30 Verkäufe">gg. Schnitt</th></tr></thead><tbody>{zeilen}</tbody></table></div></section>')
        inhalt = (_brot(("Sets", None)) + "<h1>Alle Pokémon-Sets mit Preisen</h1>"
                  '<p class="unter">Jedes westliche Set von 1999 bis heute: Kartenzahl, Wert aller Karten nach Cardmarket-Trend '
                  'und den Abstand zum Cardmarket-Verkaufsschnitt. Ein Klick öffnet das Set mit allen Karten und Preisen.</p>'
                  f'<div class="setsuche"><input type="search" id="q" placeholder="Set suchen, z. B. Grundset oder base1"'
                  f' aria-label="Set suchen" autocomplete="off"><span id="qn"></span></div>'
                  f'<nav class="anker">{anker}</nav>'
                  + "".join(teile)
                  + '<p class="leer" id="nix" hidden>Kein Set mit diesem Namen.</p>'
                  + SETS_SKRIPT)
        return _seite("Alle Pokémon-Sets mit Preisen und Wert | Binderplan",
                      "Jedes Pokémon-Set von 1999 bis heute mit Kartenzahl, Gesamtwert nach Cardmarket-Trend und 30-Tage-Bewegung.",
                      "/sets", inhalt)

    @app.get("/set/{set_id}")
    def set_seite(set_id: str):
        con = get_db()
        s = con.execute("SELECT * FROM sets WHERE id = ?", (set_id,)).fetchone()
        if not s:
            con.close()
            raise HTTPException(404, "Set nicht gefunden")
        s = dict(s)
        karten = [dict(r) for r in con.execute(
            f"{KARTE_SELECT} WHERE c.set_id = ? ORDER BY c.local_num, c.local_id", (set_id,))]
        m = _markt(con, "set", set_id)
        con.close()
        jp = (s.get("region") or "intl") == "jp"
        name = s["name"] or s["name_en"] or set_id
        jahr = (s.get("release_date") or "")[:4]
        mit_preis = [k for k in karten if k["eur"]]
        # Der Wert kommt aus demselben Tagesstand wie in der Set-Liste. Vorher rechnete die
        # Detailseite selbst: derselbe Klick änderte die Zahl, weil die Liste den Tagesstand
        # nahm (nur internationale Karten, ohne geschätzte Preise) und die Seite eine eigene
        # Summe bildete. Ohne Tagesstand (frisches Set) bleibt die eigene Summe als Rückfall.
        summe = m.get("summe") if m and m.get("summe") else sum(k["eur"] for k in mit_preis)
        teuerste = sorted(mit_preis, key=lambda k: -k["eur"])[:12]
        bew = m.get("bew30") if m else None
        median = sorted(k["eur"] for k in mit_preis)[len(mit_preis) // 2] if mit_preis else None
        zeilen = "".join(
            f'<tr><td><img loading="lazy" src="/api/img/card/{esc(k["id"])}" alt="" width="30" height="42"></td>'
            f'<td><a href="/karte/{esc(k["id"])}">{esc(k["name_de"] or k["name_en"])}</a>'
            f'{(" <span class=hin>" + esc(k["name_en"]) + "</span>") if k["name_en"] and k["name_de"] and k["name_en"] != k["name_de"] else ""}</td>'
            f'<td class="r">{esc(k["local_id"] or "")}</td><td>{esc(k["rarity"] or "")}</td>'
            f'<td class="r">{eur(k["eur"])}</td>'
            f'<td class="r dl {kl(_bewegung(k["eur"], k["eur_avg30"]))}">{proz(_bewegung(k["eur"], k["eur_avg30"]))}</td></tr>'
            for k in karten)
        titel = f"{name} ({jahr}) – alle Karten mit Preisen | Binderplan" if jahr else f"{name} – alle Karten mit Preisen | Binderplan"
        beschreibung = (f"{name}: {len(karten)} Karten, Gesamtwert {eur(summe, 0)} nach Cardmarket-Trend"
                        + (f", {proz(bew)} gegenüber dem Verkaufsschnitt" if bew is not None else "")
                        + f". Teuerste Karte: {teuerste[0]['name_de'] or teuerste[0]['name_en']} ({eur(teuerste[0]['eur'], 0)})." if teuerste else ".")
        ld = {"@context": "https://schema.org", "@type": "CollectionPage", "name": name, "url": f"{APP_URL}/set/{set_id}",
              "description": beschreibung, "inLanguage": "de",
              "breadcrumb": {"@type": "BreadcrumbList", "itemListElement": [
                  {"@type": "ListItem", "position": 1, "name": "Sets", "item": f"{APP_URL}/sets"},
                  {"@type": "ListItem", "position": 2, "name": name, "item": f"{APP_URL}/set/{set_id}"}]}}
        inhalt = (_brot(("Sets", "/sets"), (name, None))
                  + f'<h1>{_symbol(set_id, s.get("symbol"), True)}{esc(name)}</h1>'
                  + f'<p class="unter">{esc(s.get("serie_name") or "")}{" · " if s.get("serie_name") else ""}{jahr} · {len(karten)} Karten'
                  + (f' · englisch: {esc(s["name_en"])}' if s.get("name_en") and s["name_en"] != name else "") + "</p>"
                  + '<div class="kacheln">'
                  + _kachel("Wert aller Karten", eur(summe, 0), f"{len(mit_preis)} von {len(karten)} Karten mit Preis")
                  + _kachel("Trend gg. Verkaufsschnitt", proz(bew) if bew is not None else "–", f"Median über {m['bew30_n']} Karten" if m and m.get("bew30_n") else "noch keine Messung", kl(bew))
                  + _kachel("Teuerste Karte", eur(teuerste[0]["eur"], 0) if teuerste else "–", esc(teuerste[0]["name_de"] or teuerste[0]["name_en"]) if teuerste else "")
                  + _kachel("Mittlere Karte", eur(median), "Median-Preis im Set")
                  + "</div>"
                  + f'<a class="cta" href="/app#set/{esc(set_id)}">Dieses Set in Binderplan planen</a> '
                  + '<a class="cta zweit" href="/app">Sammlung anlegen &amp; Fortschritt sehen</a>'
                  + "<h2>Teuerste Karten</h2>" + (_kartenraster(teuerste) if teuerste else '<p class="hin">Noch keine Preise.</p>')
                  + f"<h2>Alle {len(karten)} Karten</h2><p class='unter'>Preis = Cardmarket-Trend je Karte, Bewegung gegen den 30-Tage-Schnitt.</p>"
                  + f'<div class="tabr"><table><thead><tr><th></th><th>Karte</th><th class="r">Nr.</th><th>Seltenheit</th><th class="r">Preis</th><th class="r" title="Heutiger Trendpreis gegenüber dem Cardmarket-Schnitt der letzten 30 Verkäufe">gg. Schnitt</th></tr></thead><tbody>{zeilen}</tbody></table></div>')
        return _seite(titel, beschreibung, f"/set/{set_id}", inhalt,
                      bild=f"{APP_URL}/api/img/card/{teuerste[0]['id']}" if teuerste else None, ld=ld, noindex=jp)

    # --------------------------------------------------------------------- Pokémon

    @app.get("/pokemon/{dex}")
    def pokemon_seite(dex: int):
        con = get_db()
        p = con.execute("SELECT * FROM pokemon WHERE dex_id = ?", (dex,)).fetchone()
        if not p:
            con.close()
            raise HTTPException(404, "Pokémon nicht gefunden")
        p = dict(p)
        karten = [dict(r) for r in con.execute(
            f"{KARTE_SELECT} WHERE c.first_dex = ? AND COALESCE(c.region,'intl')='intl'"
            " ORDER BY COALESCE(p.eur, p.eur_geschaetzt) DESC, c.release_date DESC", (dex,))]
        m = _markt(con, "pokemon", dex)
        con.close()
        name = p["name_de"] or p["name_en"]
        mit_preis = [k for k in karten if k["eur"]]
        summe = sum(k["eur"] for k in mit_preis)
        jahre = {}
        for k in karten:
            j = (k.get("release_date") or "")[:4]
            if j:
                jahre.setdefault(j, []).append(k)
        # Nach Jahrzehnt gruppiert, wie die Set-Liste nach Serien: 111 Karten in einer
        # Tabelle waren für Suchmaschinen gut und für Leser eine Wand.
        nach_jahrzehnt = {}
        for k in karten:
            j = (k.get("release_date") or "")[:3]
            nach_jahrzehnt.setdefault(j + "0er" if j else "ohne Jahr", []).append(k)
        zeilen = ""
        for jz in sorted(nach_jahrzehnt, reverse=True):
            teil = sorted(nach_jahrzehnt[jz], key=lambda k: -(k["eur"] or 0))
            wert = sum(k["eur"] or 0 for k in teil)
            zeilen += (f'<tr class="gruppe"><td colspan="6"><strong>{esc(jz)}</strong> · {len(teil)} Karten'
                       f' · {eur(wert, 0)}</td></tr>')
            zeilen += "".join(
                f'<tr><td><img loading="lazy" src="/api/img/card/{esc(k["id"])}" alt="" width="30" height="42"></td>'
                f'<td><a href="/karte/{esc(k["id"])}">{esc(k["name_de"] or k["name_en"])}</a></td>'
                f'<td><a href="/set/{esc(k["set_id"])}">{esc(k["set_name"] or k["set_id"])}</a> · {esc(k["local_id"] or "")}</td>'
                f'<td class="r">{(k.get("release_date") or "")[:4]}</td><td>{esc(k["rarity"] or "")}</td><td class="r">{eur(k["eur"])}</td></tr>'
                for k in teil)
        titel = f"{name}-Karten: alle {len(karten)} Karten mit Preisen | Binderplan"
        beschreibung = (f"Alle {len(karten)} {name}-Karten von {min(jahre) if jahre else '?'} bis {max(jahre) if jahre else '?'} "
                        f"mit Cardmarket-Preisen. Zusammen {eur(summe, 0)}"
                        + (f", teuerste: {karten[0]['name_de'] or karten[0]['name_en']} aus {karten[0]['set_name']} ({eur(karten[0]['eur'], 0)})." if mit_preis else "."))
        ld = {"@context": "https://schema.org", "@type": "CollectionPage", "name": f"{name}-Karten", "url": f"{APP_URL}/pokemon/{dex}",
              "description": beschreibung, "inLanguage": "de"}
        inhalt = (_brot(("Pokémon", None), (name, None))
                  + f"<h1>{esc(name)} – alle Karten</h1>"
                  + f'<p class="unter">Pokédex-Nummer {dex}{(" · englisch: " + esc(p["name_en"])) if p.get("name_en") and p["name_en"] != name else ""} · {len(karten)} westliche Karten</p>'
                  + '<div class="kacheln">'
                  + _kachel("Alle Karten zusammen", eur(summe, 0), f"{len(mit_preis)} Karten mit Preis")
                  + _kachel("Trend gg. Verkaufsschnitt", proz(m.get("bew30")) if m and m.get("bew30") is not None else "–",
                            f"Median über {m['bew30_n']} Karten" if m and m.get("bew30_n") else "", kl(m.get("bew30") if m else None))
                  + _kachel("Teuerste Karte", eur(karten[0]["eur"], 0) if mit_preis else "–", esc(karten[0]["set_name"] or "") if mit_preis else "")
                  + _kachel("Jahrgänge", f"{min(jahre)}–{max(jahre)}" if jahre else "–", f"{len(jahre)} Jahre mit Karten")
                  + "</div>"
                  + f'<a class="cta" href="/app#pokemon/{dex}">Binder mit allen {esc(name)}-Karten planen</a>'
                  + "<h2>Teuerste Karten</h2>" + _kartenraster(karten[:12], mit_set=True)
                  + f"<h2>Alle {len(karten)} Karten</h2>"
                  + f'<div class="tabr"><table><thead><tr><th></th><th>Karte</th><th>Set · Nr.</th><th class="r">Jahr</th><th>Seltenheit</th><th class="r">Preis</th></tr></thead><tbody>{zeilen}</tbody></table></div>')
        return _seite(titel, beschreibung, f"/pokemon/{dex}", inhalt,
                      bild=f"{APP_URL}/api/img/card/{karten[0]['id']}" if karten else None, ld=ld)

    # ---------------------------------------------------------------------- Karte

    @app.get("/karte/{card_id}")
    def karte_seite(card_id: str):
        con = get_db()
        k = con.execute(f"{KARTE_SELECT} WHERE c.id = ?", (card_id,)).fetchone()
        if not k:
            con.close()
            raise HTTPException(404, "Karte nicht gefunden")
        k = dict(k)
        verlauf = [(r["datum"], r["eur"]) for r in con.execute(
            "SELECT datum, eur FROM price_history WHERE card_id = ? AND eur IS NOT NULL ORDER BY datum", (card_id,))]
        andere = []
        if k.get("first_dex"):
            andere = [dict(r) for r in con.execute(
                f"{KARTE_SELECT} WHERE c.first_dex = ? AND c.id <> ? AND COALESCE(c.region,'intl')='intl'"
                " AND (c.name_en = ? OR c.name_de = ?) ORDER BY COALESCE(p.eur, p.eur_geschaetzt) DESC LIMIT 12",
                (k["first_dex"], card_id, k["name_en"], k["name_de"]))]
        pokemon = con.execute("SELECT name_de, name_en FROM pokemon WHERE dex_id = ?", (k["first_dex"],)).fetchone() if k.get("first_dex") else None
        con.close()
        jp = (k.get("region") or "intl") == "jp"
        name = k["name_de"] or k["name_en"] or card_id
        set_name = k["set_name"] or k["set_id"]
        nummer = f"{k['local_id']}/{k['official']}" if k.get("official") and k.get("local_id") else (k.get("local_id") or "")
        # „7 Tage“ verglich bis zum 11.09.2026 den Trendpreis gegen Cardmarkets
        # Verkaufsschnitt — zwei verschiedene Preisarten. Diese Karte meldete damit
        # „+76,5 %“, während ihr eigener Verlauf in derselben Woche fiel. Jetzt kommt der
        # Vergleichswert aus price_history, derselben Reihe wie die Kurve darunter.
        # Der Verlauf steht schon oben — daraus kommt der Vergleichswert, damit Kachel und
        # Kurve nie auseinanderlaufen können. Die Historie speichert nur Bewegungen: der
        # letzte Eintrag am oder vor dem Stichtag ist genau der Preis dieses Tages.
        stichtag = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
        frueher = [w for d, w in verlauf if d <= stichtag]
        vor7 = frueher[-1] if frueher else None
        bew7 = _bewegung(k["eur"], vor7)
        bew30 = _bewegung(k["eur"], k["eur_avg30"])
        titel = f"{name} {set_name} {nummer} – Preis & Wert | Binderplan"
        beschreibung = (f"{name} aus {set_name} (Nr. {nummer}, {k['rarity'] or 'ohne Seltenheitsangabe'}): "
                        f"Cardmarket-Trend {eur(k['eur'])}" + (f", Verkaufsschnitt 30 T. {eur(k['eur_avg30'])}" if k.get("eur_avg30") else "")
                        + (f", Tiefstpreis {eur(k['eur_low'])}" if k.get("eur_low") else "") + ". Preisverlauf, andere Drucke und Set.")
        ld = {"@context": "https://schema.org", "@type": "Product", "name": f"{name} – {set_name} {nummer}",
              "image": f"{APP_URL}/api/img/card/{card_id}", "url": f"{APP_URL}/karte/{card_id}", "description": beschreibung}
        if k.get("eur"):
            ld["offers"] = {"@type": "AggregateOffer", "priceCurrency": "EUR", "lowPrice": k.get("eur_low") or k["eur"],
                            "highPrice": max(k["eur"], k.get("eur_holo") or 0), "offerCount": 1, "url": f"{APP_URL}/karte/{card_id}"}
        typen = []
        try:
            typen = json.loads(k.get("types") or "[]")
        except Exception:
            pass
        meta = " · ".join(x for x in (
            f'<a href="/set/{esc(k["set_id"])}">{esc(set_name)}</a>', esc(nummer), esc(k.get("rarity") or ""),
            ", ".join(esc(TYPEN_DE.get(t, t)) for t in typen), f"{k['hp']} HP" if k.get("hp") else "", (k.get("release_date") or "")[:4],
            f"Illustration: {esc(k['illustrator'])}" if k.get("illustrator") else "") if x)
        preise = ('<div class="kacheln">'
                  + _kachel("Cardmarket-Trend", eur(k["eur"]), f"Stand {(k.get('preis_stand') or '')[:10]}" if k.get("preis_stand") else "kein Preis")
                  + _kachel("7 Tage", proz(bew7) if bew7 is not None else "–", f"vor 7 Tagen {eur(vor7)}" if vor7 else "noch keine Historie", kl(bew7))
                  + _kachel("Gg. Verkaufsschnitt", proz(bew30) if bew30 is not None else "–", f"Schnitt 30 T. {eur(k['eur_avg30'])}" if k.get("eur_avg30") else "", kl(bew30))
                  + _kachel("Tiefstpreis", eur(k.get("eur_low")), "günstigstes Angebot")
                  + (_kachel("Holo / Reverse", eur(k.get("eur_holo")), "Trend der Holo-Ausprägung") if k.get("eur_holo") else "")
                  + (_kachel("USA", f"{k['usd']:.2f} $".replace(".", ","), "TCGplayer-Marktpreis") if k.get("usd") else "")
                  + "</div>")
        inhalt = (_brot(("Sets", "/sets"), (set_name, f"/set/{k['set_id']}"), (name, None))
                  + '<div class="detail">'
                  + f'<div><img class="gross" src="/api/img/card/{esc(card_id)}?variante=high" alt="{esc(name)} {esc(set_name)} {esc(nummer)}" width="280" height="391"></div>'
                  + f"<div><h1>{esc(name)}</h1>"
                  + (f'<p class="unter">englisch: {esc(k["name_en"])}</p>' if k.get("name_en") and k["name_en"] != name else "")
                  + f'<p class="unter">{meta}</p>' + preise
                  + (("<h2>Preisverlauf</h2>" + _sparkline(verlauf[-120:])) if len(verlauf) >= 2 else "")
                  + f'<p><a class="cta" href="/app#karte/{esc(card_id)}">In Binderplan öffnen: ins Fach, in die Sammlung, Preis-Alarm</a>'
                  + (f' <a class="cta zweit" href="{esc(k["cm_url"])}" rel="nofollow noopener" target="_blank">Bei Cardmarket ansehen</a>' if k.get("cm_url") else "")
                  + "</p></div></div>"
                  + ((f"<h2>Andere Drucke von {esc(name)}</h2>" + _kartenraster(andere, mit_set=True)) if andere else "")
                  + (f'<p style="margin-top:20px"><a href="/pokemon/{k["first_dex"]}">Alle {esc(pokemon["name_de"] or pokemon["name_en"])}-Karten ›</a> · '
                     f'<a href="/set/{esc(k["set_id"])}">Alle Karten aus {esc(set_name)} ›</a></p>' if pokemon else ""))
        return _seite(titel, beschreibung, f"/karte/{card_id}", inhalt,
                      bild=f"{APP_URL}/api/img/card/{card_id}", ld=ld, noindex=jp)

    def kennzahlen():
        return {"seiten": True}

    return kennzahlen
