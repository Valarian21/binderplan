// Kontrastprüfung nach jedem Deploy: fährt die Hauptansichten in hell und dunkel ab und
// listet jeden Text unter der WCAG-Grenze (4,5:1, große/fette Schrift 3:1).
//
//   node scripts/kontrast.js                      # gegen die Produktion
//   BASE=http://127.0.0.1:8103 node scripts/kontrast.js
//
// Braucht ein Anmelde-Token in scripts/.kontrast-token (eine Zeile) für die Ansichten
// hinter dem Login; ohne Datei laufen nur die öffentlichen Seiten.
const { chromium } = require(process.env.PLAYWRIGHT_PFAD
  || '/home/developer/ai_empire/services/browser_render/node_modules/playwright');
const fs = require('fs'); const path = require('path');
const OUT = process.env.SHOTS ? path.join(__dirname, process.env.SHOTS) : null;
if (OUT) fs.mkdirSync(OUT, { recursive: true });
const TOKEN_DATEI = path.join(__dirname, '.kontrast-token');
const TOKEN = fs.existsSync(TOKEN_DATEI) ? fs.readFileSync(TOKEN_DATEI, 'utf8').trim() : '';
const BASE = process.env.BASE || 'https://binderplan.app';
const KANDA = 'u87gvZqgoZo';
const befunde = []; const log = [];

// Kontrastmessung im Browser: jedes Element mit eigenem Text, Farbe gegen den ersten
// nicht-transparenten Hintergrund der Vorfahren. Näherung (keine Bilder, keine Verläufe).
const KONTRAST_JS = `(() => {
  const lum = (r,g,b) => { const f = (c) => { c /= 255; return c <= .03928 ? c/12.92 : Math.pow((c+.055)/1.055, 2.4); }; return .2126*f(r)+.7152*f(g)+.0722*f(b); };
  const parse = (s) => { const m = s.match(/rgba?\\(([^)]+)\\)/); if (!m) return null; const p = m[1].split(',').map(Number); return { r:p[0], g:p[1], b:p[2], a: p.length > 3 ? p[3] : 1 }; };
  const blend = (fg, bg) => ({ r: fg.r*fg.a + bg.r*(1-fg.a), g: fg.g*fg.a + bg.g*(1-fg.a), b: fg.b*fg.a + bg.b*(1-fg.a), a: 1 });
  const hintergrund = (el) => {
    let e = el; let acc = null;
    while (e && e !== document.documentElement) {
      const cs = getComputedStyle(e);
      const bg = parse(cs.backgroundColor);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') return null;   // Bild/Verlauf: nicht messbar
      if (bg && bg.a > 0) { acc = acc ? blend(acc, bg) : bg; if (acc.a >= .99 || bg.a >= .99) { if (acc.a < .99) acc = blend(acc, {r:255,g:255,b:255,a:1}); return acc; } }
      e = e.parentElement;
    }
    const body = parse(getComputedStyle(document.body).backgroundColor);
    const grund = body && body.a > 0 ? body : { r:255, g:255, b:255, a:1 };
    return acc ? blend(acc, grund) : grund;
  };
  const sichtbar = (el) => { const r = el.getBoundingClientRect(); if (r.width < 2 || r.height < 2) return false; if (r.bottom < 0 || r.top > innerHeight || r.right < 0 || r.left > innerWidth) return false; const cs = getComputedStyle(el); return cs.visibility !== 'hidden' && cs.display !== 'none' && parseFloat(cs.opacity) > .05; };
  const out = [];
  document.querySelectorAll('body *').forEach((el) => {
    if (['SCRIPT','STYLE','SVG','PATH','IMG','CANVAS','INPUT','SELECT','TEXTAREA'].includes(el.tagName)) return;
    const txt = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).join(' ').trim();
    if (!txt || txt.length < 2) return;
    if (!sichtbar(el)) return;
    const cs = getComputedStyle(el);
    let fg = parse(cs.color); if (!fg) return;
    const bg = hintergrund(el); if (!bg) return;
    if (fg.a < 1) fg = blend(fg, bg);
    const l1 = lum(fg.r,fg.g,fg.b), l2 = lum(bg.r,bg.g,bg.b);
    const ratio = (Math.max(l1,l2)+.05)/(Math.min(l1,l2)+.05);
    const gross = parseFloat(cs.fontSize) >= 18.66 || (parseFloat(cs.fontSize) >= 14 && parseInt(cs.fontWeight,10) >= 700);
    const grenze = gross ? 3 : 4.5;
    if (ratio < grenze) {
      const sel = el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\\s+/).slice(0,2).join('.') : '');
      out.push({ sel, txt: txt.slice(0, 40), ratio: Math.round(ratio*100)/100, grenze, fg: cs.color, bg: 'rgb(' + Math.round(bg.r) + ',' + Math.round(bg.g) + ',' + Math.round(bg.b) + ')', px: parseFloat(cs.fontSize) });
    }
  });
  return out;
})()`;

async function ctx(browser, vp, login, theme) {
  const c = await browser.newContext({ viewport: vp, deviceScaleFactor: 1, hasTouch: vp.width < 900, isMobile: vp.width < 900, locale: 'de-DE', colorScheme: theme === 'dunkel' ? 'dark' : 'light' });
  await c.addInitScript(({ tok, login, theme }) => { localStorage.setItem('bp_lang', 'de'); localStorage.setItem('bp_theme', theme); if (login) localStorage.setItem('bp_token', tok); else localStorage.removeItem('bp_token'); }, { tok: TOKEN, login, theme });
  if (login) await c.addCookies([{ name: 'bp_token', value: TOKEN, domain: new URL(BASE).hostname, path: '/' }]);
  return c;
}
async function mess(p, name) {
  try {
    const r = await p.evaluate(KONTRAST_JS);
    r.forEach((x) => befunde.push({ ansicht: name, ...x }));
    note(name, `${r.length} Kontrast-Treffer`);
  } catch (e) { note(name, 'Messfehler ' + e.message.split('\n')[0]); }
}
const note = (n, m) => log.push(`${n}: ${m}`);
async function shot(p, name, wait = 900, full = false) {
  try { await p.waitForTimeout(wait); if (OUT) await p.screenshot({ path: path.join(OUT, name + '.png'), fullPage: full }); await mess(p, name); }
  catch (e) { note(name, 'FEHLER ' + e.message.split('\n')[0]); }
}
async function ev(p, js) { try { return await p.evaluate(js); } catch (e) { note('eval', e.message.split('\n')[0] + ' <- ' + js.slice(0, 50)); } }

async function lauf(browser, theme, vp, P) {
  const c = await ctx(browser, vp, true, theme); const p = await c.newPage();
  const m = vp.width < 900;
  await p.goto(BASE + '/app#start', { waitUntil: 'networkidle' }); await shot(p, P + '01-start', 2000);
  await p.goto(BASE + '/app#binder/' + KANDA, { waitUntil: 'networkidle' }); await p.waitForTimeout(1500);
  await ev(p, `ansicht('suche')`); await shot(p, P + '02-werkbank', 2000);
  if (m) { await ev(p, `mobilFilter(true)`); await shot(p, P + '03-filter', 800); await ev(p, `mobilFilter(false)`); }
  else { await ev(p, `mehrFilterToggle(true)`); await shot(p, P + '03-filter-erweitert', 800); await ev(p, `mehrFilterToggle(false)`); }
  await ev(p, `kontoMenue()`); await shot(p, P + '04-konto-menue', 500);
  await ev(p, `document.querySelectorAll('.menu').forEach(x=>x.classList.add('hidden')); menuToggle('${m ? 'mobil-mehr' : 'menu-binder'}')`); await shot(p, P + '05-menue', 500);
  await ev(p, `document.querySelectorAll('.menu').forEach(x=>x.classList.add('hidden')); detailOeffnenId('base1-4')`); await shot(p, P + '06-detail', 2500);
  await ev(p, `document.querySelector('#detail-spanne .zst-auf')?.click(); document.querySelector('#detail-zustaende .zst-auf')?.click()`); await shot(p, P + '06b-detail-auf', 500);
  await ev(p, `modalZu(); ansicht('planer')`); await shot(p, P + '07-planer', 2000);
  await ev(p, `document.querySelectorAll('#planer .slot').forEach((s,i)=>{ if(i<2) s.click(); })`); await shot(p, P + '07b-planer-auswahl', 500);
  await ev(p, `auswahlLeeren(); druckOeffnen()`); await shot(p, P + '08-druck', 1200);
  await ev(p, `modalZu(); artworkOeffnen(0)`); await shot(p, P + '09-artwork', 2500);
  await ev(p, `modalZu(); statistikOeffnen()`); await shot(p, P + '10-statistik', 1500);
  await ev(p, `modalZu(); themaOeffnen()`); await shot(p, P + '11-thema', 1200);
  await ev(p, `modalZu(); upgradeOeffnen('')`); await shot(p, P + '12-upgrade', 2000);
  await ev(p, `modalZu(); vorlage('master')`); await shot(p, P + '13-master', 1500);
  await ev(p, `modalZu(); importOeffnen()`); await shot(p, P + '14-import', 800);
  await ev(p, `modalZu(); fotoOeffnen()`); await shot(p, P + '15-foto', 800);
  await ev(p, `modalZu(); blaetternOeffnen()`); await shot(p, P + '16-blaettern', 2000);
  await ev(p, `blaetternZu(); modalZu(); ansicht('sammlung')`); await shot(p, P + '17-sammlung', 2500);
  await ev(p, `ansicht('vitrine')`); await shot(p, P + '18-vitrine', 3000);
  await ev(p, `vitrineBereich('kunst')`); await shot(p, P + '19-vitrine-kunst', 3000);
  await ev(p, `vitrineBereich('binder'); const k=document.querySelector('#vitrine [onclick*="profilseiteOeffnen"]'); if(k) k.click();`); await shot(p, P + '20-vitrine-profil', 3000);
  await ev(p, `ansicht('markt')`); await shot(p, P + '21-markt', 3500);
  await ev(p, `ansicht('auswertung')`); await shot(p, P + '22-auswertung', 3500);
  await ev(p, `profilOeffnen()`); await shot(p, P + '23-profil', 2000, true);
  await ev(p, `profilSchliessen(); loginOeffnen(''); authTab('reg')`); await shot(p, P + '24-auth', 800);
  await c.close();
  const g = await ctx(browser, vp, false, theme); const q = await g.newPage();
  await q.goto(BASE + '/app#ansicht/' + KANDA, { waitUntil: 'networkidle' }); await shot(q, P + '25-geteilt', 2500);
  await q.goto(BASE + '/?landing=1', { waitUntil: 'networkidle' }); await shot(q, P + '26-landing', 1500);
  await q.evaluate(() => window.scrollTo(0, 2400)); await shot(q, P + '27-landing-mitte', 800);
  await q.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight - 1200)); await shot(q, P + '28-landing-ende', 800);
  await q.goto(BASE + '/recht', { waitUntil: 'networkidle' }); await shot(q, P + '29-recht', 800);
  await q.goto(BASE + '/kuendigen', { waitUntil: 'networkidle' }); await shot(q, P + '30-kuendigen', 800);
  await g.close();
}
(async function haupt() {
  const browser = await chromium.launch();
  await lauf(browser, 'dunkel', { width: 1440, height: 900 }, 'dd-');
  await lauf(browser, 'dunkel', { width: 390, height: 844 }, 'dm-');
  await lauf(browser, 'hell', { width: 1440, height: 900 }, 'hd-');
  await browser.close();
  // Nach Ursache gruppieren: 600 Roh-Treffer sind meist ein Dutzend CSS-Regeln.
  const nach = new Map();
  befunde.forEach((b) => {
    const k = `${b.ansicht.slice(0, 2)}|${b.sel}|${b.fg}|${b.bg}`;
    const a = nach.get(k) || { modus: b.ansicht.slice(0, 2), sel: b.sel, fg: b.fg, bg: b.bg, ratio: 99, n: 0, bsp: b.txt };
    a.n++; a.ratio = Math.min(a.ratio, b.ratio); nach.set(k, a);
  });
  const zeilen = [...nach.values()].sort((a, b) => a.ratio - b.ratio);
  fs.writeFileSync(path.join(__dirname, 'kontrast-bericht.json'), JSON.stringify(zeilen, null, 1));
  console.log(log.filter((l) => /FEHLER|Messfehler|eval/.test(l)).join('\n') || 'Ansichten ok');
  if (!zeilen.length) { console.log('Keine Kontrastfehler.'); return; }
  console.log(`\n${zeilen.length} Ursachen, ${befunde.length} Treffer:\n`);
  zeilen.forEach((z) => console.log(
    `  ${z.modus} ${z.ratio.toFixed(2).padStart(5)}  ×${String(z.n).padEnd(4)} ${z.sel.slice(0, 42).padEnd(42)} ${z.fg} auf ${z.bg}  „${z.bsp.slice(0, 26)}“`));
  process.exitCode = 1;
})();
