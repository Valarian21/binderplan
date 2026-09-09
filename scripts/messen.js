// Messlauf (aus dem Produkt-Audit 09.09.2026): fotografiert jede Ansicht, jedes Modal und jedes Menü als Marcel
// (Token aus token.txt) in einer Fenstergröße und sammelt Messwerte je Ansicht.
// Aufruf: node shots.js <desktop|mobil|laptop|dunkel|gast|public>
const { chromium } = require(process.env.PLAYWRIGHT_PFAD || '/home/developer/ai_empire/services/browser_render/node_modules/playwright');
const fs = require('fs');
const path = require('path');

const SP = __dirname;
const MODUS = process.argv[2] || 'desktop';
const TOKEN = process.env.BP_TOKEN || (fs.existsSync(path.join(SP, 'token.txt')) ? fs.readFileSync(path.join(SP, 'token.txt'), 'utf8').trim() : '');
const BASE = 'https://binderplan.app';
const OUT = path.join(process.env.MESS_AUSGABE || path.join(SP, '..', 'messung'), MODUS);
fs.mkdirSync(OUT, { recursive: true });

const VP = {
  desktop: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 },
  laptop: { viewport: { width: 1100, height: 720 }, deviceScaleFactor: 1 },
  mobil: { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, hasTouch: true, isMobile: true,
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1' },
  dunkel: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, colorScheme: 'dark' },
  gast: { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, hasTouch: true, isMobile: true },
  public: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 },
}[MODUS];

const metrik = {};
let requests = 0, fehler = [];

async function settle(page, ms = 900) {
  try { await page.waitForLoadState('networkidle', { timeout: 6000 }); } catch (e) {}
  await page.waitForTimeout(ms);
}

async function messen(page, name) {
  const m = await page.evaluate(() => {
    const iw = window.innerWidth;
    const hor = Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - iw;
    // sichtbare Bedienelemente mit zu kleiner Trefffläche
    const els = [...document.querySelectorAll('button, a, input, select, [onclick]')];
    let klein = 0, kleinListe = [], winzigText = 0;
    for (const el of els) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0 || r.bottom < 0 || r.top > innerHeight) continue;
      const st = getComputedStyle(el);
      if (st.visibility === 'hidden' || st.display === 'none') continue;
      if (r.height < 32 || r.width < 32) { klein++; if (kleinListe.length < 8) kleinListe.push(`${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}${el.className && typeof el.className === 'string' ? '.' + el.className.split(' ')[0] : ''} ${Math.round(r.width)}×${Math.round(r.height)} "${(el.textContent || '').trim().slice(0, 18)}"`); }
    }
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) {
      if (!n.textContent.trim()) continue;
      const p = n.parentElement; if (!p) continue;
      const r = p.getBoundingClientRect();
      if (r.width === 0 || r.bottom < 0 || r.top > innerHeight) continue;
      const fs = parseFloat(getComputedStyle(p).fontSize);
      if (fs < 11) winzigText++;
    }
    const overlays = [...document.querySelectorAll('.overlay:not(.hidden), .menu:not(.hidden)')].map((o) => o.id);
    // welches Element scrollt wirklich?
    const scroller = [...document.querySelectorAll('*')].filter((e) => { const s = getComputedStyle(e); return /(auto|scroll)/.test(s.overflowY) && e.scrollHeight > e.clientHeight + 4 && e.clientHeight > 100; })
      .map((e) => `${e.tagName.toLowerCase()}${e.id ? '#' + e.id : ''}${typeof e.className === 'string' && e.className ? '.' + e.className.split(' ')[0] : ''} ${e.scrollHeight}/${e.clientHeight}`).slice(0, 5);
    const h1 = [...document.querySelectorAll('h1,h2,h3')].filter((h) => h.getBoundingClientRect().width > 0).map((h) => h.textContent.trim().slice(0, 50)).slice(0, 12);
    const text = document.body.innerText.replace(/\n{2,}/g, '\n').slice(0, 2500);
    return { iw, hor, docH: document.documentElement.scrollHeight, klein, kleinListe, winzigText, overlays, scroller, h1, text, hash: location.hash, title: document.title };
  });
  m.requests = requests; m.fehler = fehler.slice();
  metrik[name] = m;
  requests = 0; fehler = [];
}

async function foto(page, name, opt = {}) {
  await settle(page, opt.wait || 700);
  await messen(page, name);
  await page.screenshot({ path: path.join(OUT, name + '.png'), fullPage: !!opt.full });
  process.stdout.write(name + ' ');
}

async function js(page, code) {
  try { await page.evaluate(code); } catch (e) { fehler.push('JS ' + code.slice(0, 40) + ': ' + e.message.split('\n')[0]); }
}

async function zu(page) {
  await js(page, "modalZu(); document.querySelectorAll('.menu').forEach((m)=>m.classList.add('hidden')); if(typeof mobilFilter==='function') try{mobilFilter(false)}catch(e){}; document.querySelectorAll('#m-backdrop').forEach(b=>b.classList.remove('an','zeig'))");
  await page.keyboard.press('Escape');
  await page.waitForTimeout(250);
}

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ ...VP, locale: 'de-DE' });
  const eingeloggt = !['gast', 'public'].includes(MODUS);
  await ctx.addInitScript(({ tok, dunkel, gast }) => {
    localStorage.setItem('bp_lang', 'de');
    if (dunkel) localStorage.setItem('bp_theme', 'dunkel');
    if (!gast) localStorage.setItem('bp_token', tok);
  }, { tok: TOKEN, dunkel: MODUS === 'dunkel', gast: !eingeloggt });
  if (eingeloggt) await ctx.addCookies([{ name: 'bp_token', value: TOKEN, domain: 'binderplan.app', path: '/' }]);
  const page = await ctx.newPage();
  page.on('request', () => requests++);
  page.on('console', (m) => { if (m.type() === 'error') fehler.push('console: ' + m.text().slice(0, 160)); });
  page.on('pageerror', (e) => fehler.push('pageerror: ' + String(e).slice(0, 160)));
  page.on('response', (r) => { if (r.status() >= 400) fehler.push(`${r.status()} ${r.url().replace(BASE, '')}`); });

  if (MODUS === 'public') {
    const seiten = [['landing', '/?landing=1'], ['landing-en', '/en?landing=1'], ['sets', '/sets'], ['set-base1', '/set/base1'], ['pokemon-6', '/pokemon/6'],
      ['karte-base1-4', '/karte/base1-4'], ['b-oeffentlich', '/b/jit9mJ6xv1U'], ['recht', '/recht'], ['kuendigen', '/kuendigen'], ['app-gast', '/app'], ['ansicht-geteilt', '/app#ansicht/jit9mJ6xv1U']];
    for (const [n, u] of seiten) {
      const t0 = Date.now();
      await page.goto(BASE + u, { waitUntil: 'domcontentloaded' });
      await foto(page, n, { full: true, wait: 1500 });
      metrik[n].ladezeit = Date.now() - t0;
    }
    // Handybreite der öffentlichen Seiten
    await page.setViewportSize({ width: 390, height: 844 });
    for (const [n, u] of seiten) {
      await page.goto(BASE + u, { waitUntil: 'domcontentloaded' });
      await foto(page, n + '-mobil', { full: true, wait: 1200 });
    }
    // Gast im Werkzeug: Anmeldedialog
    await page.goto(BASE + '/app', { waitUntil: 'domcontentloaded' });
    await settle(page, 1200);
    await js(page, "loginOeffnen('')"); await foto(page, 'gast-login-mobil');
    await zu(page);
    await js(page, "upgradeOeffnen('')"); await foto(page, 'gast-upgrade-mobil');
    fs.writeFileSync(path.join(OUT, 'metrik.json'), JSON.stringify(metrik, null, 1));
    await browser.close(); return;
  }

  if (MODUS === 'gast') {
    await page.goto(BASE + '/app', { waitUntil: 'domcontentloaded' });
    await foto(page, 'gast-suche', { wait: 1500 });
    await js(page, "binderAnsicht(true)"); await foto(page, 'gast-binder');
    await js(page, "menuToggle('mobil-mehr')"); await foto(page, 'gast-mehr'); await zu(page);
    await js(page, "ansicht('sammlung')"); await foto(page, 'gast-sammlung-login'); await zu(page);
    await js(page, "ansicht('vitrine')"); await foto(page, 'gast-vitrine');
    await js(page, "ansicht('markt')"); await foto(page, 'gast-markt');
    await js(page, "loginOeffnen('')"); await foto(page, 'gast-login'); await zu(page);
    fs.writeFileSync(path.join(OUT, 'metrik.json'), JSON.stringify(metrik, null, 1));
    await browser.close(); return;
  }

  const t0 = Date.now();
  await page.goto(BASE + '/app#start', { waitUntil: 'domcontentloaded' });
  await foto(page, '01-start', { wait: 2500 });
  metrik['01-start'].ladezeit = Date.now() - t0;
  const bereiche = await page.evaluate(() => ({ sm: typeof SM_BEREICHE !== 'undefined' ? SM_BEREICHE : [], mk: typeof MK_BEREICHE !== 'undefined' ? MK_BEREICHE : [], user: !!(window.S && S.user), binder: S && S.binder && S.binder.name }));
  console.log('\nBereiche', JSON.stringify(bereiche));
  if (MODUS === 'dunkel') {
    await js(page, "binderAnsicht(false); ansicht('suche')"); await foto(page, '02-suche');
    await js(page, "binderAnsicht(true)"); await foto(page, '03-binder-alle');
    await js(page, "ansichtSammlung('karten')"); await foto(page, '04-sammlung');
    await js(page, "ansicht('vitrine')"); await foto(page, '05-vitrine');
    await js(page, "ansicht('markt')"); await foto(page, '06-markt');
    await js(page, "profilOeffnen()"); await foto(page, '07-profil');
    await js(page, "ansicht('suche'); detailOeffnenId('base1-4')"); await foto(page, '08-detail'); await zu(page);
    await js(page, "upgradeOeffnen('')"); await foto(page, '09-upgrade'); await zu(page);
    await js(page, "druckOeffnen()"); await foto(page, '10-druck'); await zu(page);
    fs.writeFileSync(path.join(OUT, 'metrik.json'), JSON.stringify(metrik, null, 1));
    await browser.close(); return;
  }

  // Startseite ganz nach unten scrollen (innerer Scroller)
  await js(page, "const s=document.querySelector('#startseite'); if(s) s.scrollTop=99999; window.scrollTo(0,99999)");
  await foto(page, '01b-start-unten');
  await js(page, "const s=document.querySelector('#startseite'); if(s) s.scrollTop=0; window.scrollTo(0,0)");

  // Werkbank
  await js(page, "binderAnsicht(false); ansicht('suche')"); await foto(page, '02-suche-eine-seite', { wait: 1500 });
  if (MODUS === 'mobil') {
    await js(page, "mobilAnsicht('suche')"); await foto(page, '02a-mobil-suche');
    await js(page, "mobilFilter(true)"); await foto(page, '02b-mobil-filter'); await zu(page);
    await js(page, "mobilAnsicht('binder')"); await foto(page, '02c-mobil-binder');
    await js(page, "document.getElementById('m-seitenleiste').classList.remove('hidden')"); await foto(page, '02d-mobil-seitenleiste'); await zu(page);
    await js(page, "menuToggle('mobil-mehr')"); await foto(page, '02e-mobil-mehr'); await zu(page);
  } else {
    // Filterspalte: erweiterte Filter aufklappen
    await js(page, "document.querySelectorAll('details').forEach(d=>d.open=true)"); await foto(page, '02a-filter-erweitert');
    await js(page, "document.querySelectorAll('details').forEach(d=>d.open=false)");
    await js(page, "menuToggle('set-popover')"); await foto(page, '02b-set-popover'); await zu(page);
    await js(page, "menuToggle('menu-alle')"); await foto(page, '02c-menu-alle'); await zu(page);
  }
  await js(page, "binderAnsicht(true)"); await foto(page, '03-binder-alle-seiten', { wait: 1500 });
  await js(page, "const s=document.querySelector('#wb-alle'); if(s) s.scrollTop=99999"); await foto(page, '03a-binder-alle-unten');
  await js(page, "const s=document.querySelector('#wb-alle'); if(s) s.scrollTop=0");
  // ein Fach wählen → Auswahlleiste
  await js(page, "const sl=document.querySelector('#wb-alle .slot:not(.frei), #wb-slots .slot:not(.frei)'); if(sl) sl.click()"); await foto(page, '03b-fach-gewaehlt');
  await js(page, "menuToggle('menu-pl-mehr')"); await foto(page, '03c-auswahl-mehr'); await zu(page);
  await js(page, "menuToggle('menu-pl-sort')"); await foto(page, '03d-auswahl-sort'); await zu(page);
  await js(page, "if(typeof auswahlLeeren==='function') auswahlLeeren(); else if(typeof auswahlAufheben==='function') auswahlAufheben();");
  await js(page, "binderAnsicht(false)");
  await js(page, "const sl=document.querySelector('#wb-slots .slot:not(.frei)'); if(sl){ if(typeof slotMenue==='function') slotMenue({currentTarget: sl, target: sl, stopPropagation(){}, preventDefault(){}, clientX: sl.getBoundingClientRect().left+20, clientY: sl.getBoundingClientRect().top+20}, 0); }"); await foto(page, '03e-fach-menue'); await zu(page);
  await js(page, "seiteMenueOeffnen()"); await foto(page, '03f-seiten-menue'); await zu(page);
  // Menüs der Kopfzeile
  for (const m of ['menu-bw', 'menu-binder', 'menu-export', 'menu-einst', 'menu-konto']) {
    await js(page, `menuToggle('${m}')`); await foto(page, '04-' + m); await zu(page);
  }
  // Modale
  const modale = [
    ['detail', "detailOeffnenId('base1-4')"], ['druck', 'druckOeffnen()'], ['vorlagen', 'vorlagenOeffnen()'], ['master', "modalOeffnen('modal-master')"],
    ['dex', "modalOeffnen('modal-dex')"], ['poke', "modalOeffnen('modal-poke')"], ['kuenstler', "modalOeffnen('modal-kuenstler')"], ['import', "importOeffnen('binder')"],
    ['upgrade', "upgradeOeffnen('')"], ['credits', 'creditsOeffnen()'], ['bestellung', "bestellungOeffnen('plus','monat')"], ['artwork', 'artworkOeffnen(1)'],
    ['statistik', 'statistikOeffnen()'], ['thema', 'themaOeffnen(null)'], ['foto', 'fotoOeffnen()'], ['blaettern', 'blaetternOeffnen()'],
    ['planerhilfe', "modalOeffnen('modal-planerhilfe')"], ['alarm', "alarmOeffnen('karte','base1-4','Glurak',300)"], ['sm-posten', "smPostenOeffnen('base1-4')"],
    ['melden', "meldenOeffnen('binder','jit9mJ6xv1U')"], ['profil-modal', "modalOeffnen('modal-profil')"], ['name', "modalOeffnen('modal-name')"], ['aw-teilen', "modalOeffnen('modal-aw-teilen')"],
    ['geb', "modalOeffnen('modal-geb')"], ['kunstkosten', 'kunstkostenZeigen()'], ['teilen', 'teilen()'],
  ];
  for (const [n, code] of modale) {
    await js(page, code); await foto(page, '05-' + n, { wait: 1200 }); await zu(page);
  }
  // Sammlung
  for (const b of bereiche.sm) {
    await js(page, b === bereiche.sm[0] ? `ansichtSammlung('${b}')` : `sammlungBereich('${b}')`); await foto(page, '06-sammlung-' + b, { wait: 1500 });
  }
  await js(page, "sammlungBereich('sets'); setTimeout(()=>{const a=document.querySelector('#sammlung [onclick^=\"smSetOeffnen\"]'); if(a) a.click();},900)"); await foto(page, '06z-sammlung-setseite', { wait: 2200 });
  await js(page, "sammlungBereich('karten'); setTimeout(()=>{ if(typeof smWahlModus==='function') smWahlModus(true); const k=document.querySelector('#sammlung .karte, #sammlung .kachel, #sammlung .sm-karte'); if(k) k.click(); },900)"); await foto(page, '06y-sammlung-auswahl', { wait: 2000 });
  await js(page, "if(typeof smWahlModus==='function') smWahlModus(false); menuToggle('menu-sm-export')"); await foto(page, '06x-sammlung-export'); await zu(page);
  // Vitrine
  await js(page, "ansicht('vitrine')"); await foto(page, '07-vitrine-kunst', { wait: 1800 });
  await js(page, "vitrineBereich('binder')"); await foto(page, '07-vitrine-binder', { wait: 1800 });
  await js(page, "const k=document.querySelector('#vitrine .vt-kachel, #vitrine .kachel, #vitrine [onclick*=\"binderGross\"]'); if(k) k.click()"); await foto(page, '07a-vitrine-overlay', { wait: 1800 }); await zu(page);
  await js(page, "vitrineBereich('kunst'); setTimeout(()=>{const k=document.querySelector('#vitrine .vt-kachel, #vitrine .kachel'); if(k) k.click();},600)"); await foto(page, '07b-vitrine-kunst-overlay', { wait: 2000 }); await zu(page);
  await js(page, "meineOeffnen()"); await foto(page, '07c-vitrine-meine', { wait: 1500 });
  await js(page, "profilseiteOeffnen('Marcel')"); await foto(page, '07d-profil-oeffentlich', { wait: 1500 });
  // Markt
  for (const b of bereiche.mk) {
    await js(page, b === bereiche.mk[0] ? "ansicht('markt')" : `marktBereich('${b}')`); await foto(page, '08-markt-' + b, { wait: 1500 });
  }
  await js(page, "marktSetOeffnen('base1')"); await foto(page, '08z-markt-setseite', { wait: 1800 });
  await js(page, "marktPokemonOeffnen(6)"); await foto(page, '08y-markt-pokemonseite', { wait: 1800 });
  // Profil
  await js(page, "profilOeffnen()"); await foto(page, '09-profil', { wait: 1500 });
  await js(page, "const s=document.querySelector('#profilseite'); if(s) s.scrollTop=99999; window.scrollTo(0,99999)"); await foto(page, '09a-profil-unten');
  await js(page, "const aw=(S.artworks||[])[0]; if(typeof creatorSeiteOeffnen==='function' && aw) creatorSeiteOeffnen(aw.id,false,aw.titel)"); await foto(page, '09b-creator', { wait: 1500 });
  // Geteilte Ansicht + Album
  await page.goto(BASE + '/app#ansicht/jit9mJ6xv1U', { waitUntil: 'domcontentloaded' }); await foto(page, '10-ansicht-geteilt', { wait: 2000 });
  fs.writeFileSync(path.join(OUT, 'metrik.json'), JSON.stringify(metrik, null, 1));
  console.log('\nfertig', MODUS);
  await browser.close();
})().catch((e) => { console.error(e); fs.writeFileSync(path.join(OUT, 'metrik.json'), JSON.stringify(metrik, null, 1)); process.exit(1); });
