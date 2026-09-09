// Binderplan – Suche als Schublade, Inspektor, Filter, Bildmotiv, Kartendialog, Import.
// Aus index.html herausgelöst (Phase 5, 10.09.2026); die Dateien laden per defer in dieser Reihenfolge:
// kern → konto → werkbank → vitrine → preise → planer → detail → artwork → markt → sammlung.
// ---------- Suche als Schublade (Desktop) bzw. Sheet (Handy) ----------
// Der Binder ist das Dokument, die Suche das Werkzeug. Sie öffnet sich, wenn ein leeres Fach
// gewählt wird oder jemand „Karten suchen" drückt – und schließt sich in „Alle Seiten".
const LADE = { lang: false, timer: null, info: {} };
function sucheLadeOeffnen(fokus) {
  if (S.nurAnsicht) return;
  if (S.alleSeiten && window.innerWidth >= 901) binderAnsicht(false);
  document.body.classList.add('suche-offen');
  const btn = $('btn-suche-lade'); if (btn) btn.classList.add('on');
  if (!S.sucheOffen) { S.sucheOffen = true; sucheNeu(); }
  ladeZielZeichnen();
  if (fokus !== false && window.innerWidth >= 901) { const f = $('f-suche-lade'); if (f) f.focus(); }
}
function sucheLadeZu() {
  document.body.classList.remove('suche-offen');
  const btn = $('btn-suche-lade'); if (btn) btn.classList.remove('on');
  mobilFilter(false);
}
function sucheLadeToggle() { document.body.classList.contains('suche-offen') ? sucheLadeZu() : sucheLadeOeffnen(); }
function ladeZielZeichnen() {
  const el = $('lade-ziel'); if (!el) return;
  const z = zielFach();
  el.textContent = z === null ? '' : t('lade_fuer').replace('{n}', z + 1);
}
/** Trefferkarte: am Handy im Sheet legt ein Tipp die Karte ins Fach, langes Drücken öffnet das
 *  Detail. Mit Maus bleibt der Klick das Detail – der beschriftete Knopf legt sie ab. */
function tkKlick(ev, i) {
  if (LADE.lang) { LADE.lang = false; return; }
  if (document.body.classList.contains('suche-offen') && window.innerWidth < 901 && matchMedia('(hover: none)').matches) return kartAdd(i);
  detailOeffnen(i);
}
function tkDruck(i) { clearTimeout(LADE.timer); LADE.lang = false; LADE.timer = setTimeout(() => { LADE.lang = true; detailOeffnen(i); }, 520); }
function tkLos() { clearTimeout(LADE.timer); }

/** Seitenstreifen unter der Seite: eine Zahl je Seite, Füllstand als Strich. Ersetzt das
 *  Suchen im „Seite …"-Dialog, wenn man nur blättern will. */
function zeichneStreifen() {
  const box = $('wb-streifen'); if (!box || !S.binder) return;
  const plan = seitenPlan();
  if (plan.length < 2 || S.alleSeiten || S.nurAnsicht) { box.innerHTML = ''; return; }
  box.innerHTML = plan.map((sp) => {
    let voll = 0;
    for (let i = 0; i < sp.laenge; i++) { const it = S.binder.items[sp.start + i]; if (it && it.type !== 'empty') voll++; }
    return `<button class="${sp.nr === S.seite ? 'on' : ''}" onclick="S.seite=${sp.nr};zeichneBinder()" title="${t('seite')} ${sp.nr + 1} · ${voll}/${sp.laenge}">${sp.nr + 1}<i style="transform:scaleX(${(voll / sp.laenge).toFixed(2)})"></i></button>`;
  }).join('');
  const on = box.querySelector('.on');
  if (on) { const r = on.getBoundingClientRect(), b = box.getBoundingClientRect(); if (r.left < b.left || r.right > b.right) box.scrollLeft += r.left - b.left - b.width / 2 + r.width / 2; }
}

/** Inspektor (Desktop): das eine gewählte Fach mit Karte, Preis, Zustand, Sprache und allen
 *  Aktionen, die vorher im Fach-Menü, im Detail-Dialog und in der Auswahlleiste verteilt lagen. */
async function inspektorZeichnen() {
  const box = $('inspektor'); if (!box) return;
  const idx = S.auswahl.size === 1 ? [...S.auswahl][0] : null;
  const item = idx !== null && S.binder ? S.binder.items[idx] : null;
  if (S.nurAnsicht || !item || window.innerWidth < 901) { box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  const zu = `<button class="btn sekundaer ik-zu" style="padding:4px 9px" onclick="auswahlLeeren()" aria-label="Schließen">✕</button>`;
  const lage = `${t('seite')} ${seiteBei(idx) + 1} · ${t('fach_wort')} ${idx - seiteInfo(seiteBei(idx)).start + 1}`;
  const gefahr = `<button class="gefahr" onclick="slotHerausnehmen(${idx})" title="${t('s_herausnehmen_t')}">${t('herausnehmen')}</button>`;
  if (item.type === 'empty') {
    box.innerHTML = `${zu}<div class="ik-name">${t('ik_leer_t').replace('{n}', idx + 1)}</div><div class="ik-meta">${lage}<br>${t('ik_leer_u')}</div>
      <div class="ik-akt" style="margin-top:10px"><button class="btn" style="text-align:center" onclick="sucheLadeOeffnen()">${t('suche_knopf')}</button><div class="trenn"></div>${gefahr}</div>`;
    return;
  }
  if (item.type === 'art') {
    box.innerHTML = `${zu}<div class="ik-name">${t('s_artwork')}</div><div class="ik-meta">${lage}</div><div class="ik-akt"><button onclick="artworkOeffnen(${seiteBei(idx)})">${t('s_artwork')}</button><button onclick="kunstFreigeben('${esc(item.artwork)}')">${t('aw_seite_frei')}</button><div class="trenn"></div><button onclick="fachFreimachen(${idx})">${t('s_entfernen')}</button>${gefahr}</div>`;
    return;
  }
  if (item.type === 'dex') {
    box.innerHTML = `${zu}<div class="ik-name">#${String(item.dex).padStart(3, '0')}</div><div class="ik-meta">${lage}</div><div class="ik-akt"><button onclick="fachEinfuegen(${idx})">${t('s_frei_davor')}</button><button onclick="fachEinfuegen(${idx + 1})">${t('s_frei_danach')}</button><div class="trenn"></div><button onclick="fachFreimachen(${idx})">${t('s_entfernen')}</button>${gefahr}</div>`;
    return;
  }
  let info = LADE.info[item.id];
  if (!info) {
    box.innerHTML = `${zu}<img class="ik-bild" src="${imgUrl(item.id)}" alt=""><div class="ik-meta">${lage}</div>`;
    try { info = await api('api/cards/' + encodeURIComponent(item.id) + '/detail'); } catch (e) { info = { id: item.id }; }
    LADE.info[item.id] = info;
    if (!S.auswahl.has(idx) || S.auswahl.size !== 1) return;
  }
  const name = (LANG === 'en' ? (info.name_en || info.name) : (info.name || info.name_en)) || item.id;
  const setName = (LANG === 'en' ? (info.set_name_en || info.set_name) : (info.set_name || info.set_name_en)) || '';
  const preis = preisFuer(item);
  const sn = S.user ? (S.besitz[item.id] || 0) : (item.have ? 1 : 0);
  const chips = (liste, akt, fn, lbl) => liste.map((v) => `<button class="ik-chip ${v === akt ? 'on' : ''}" onclick="${fn}('${v}')">${lbl ? lbl(v) : v.toUpperCase()}</button>`).join('');
  box.innerHTML = `${zu}<img class="ik-bild" src="${imgUrl(item.id)}" alt="" onclick="detailOeffnen('${esc(item.id)}')" style="cursor:zoom-in">
    <div class="ik-name">${esc(name)}</div>
    <div class="ik-meta">${esc(setName)}${info.local_id ? ' · ' + esc(info.local_id) : ''}${info.rarity ? ' · ' + esc(info.rarity) : ''}<br>${lage}</div>
    ${preis != null ? `<div class="ik-preis">${preis.toFixed(2).replace('.', LANG === 'de' ? ',' : '.')} €</div>` : ''}
    <div class="ik-lbl">${t('f_variante')}</div><div class="ik-zeile">${chips(VARIANTEN, item.variant || 'normal', 'ikVariante', (v) => v === 'normal' ? t('v_normal_kurz') : (VMARK[v] || v))}</div>
    <div class="ik-lbl">${t('kartensprache')}</div><div class="ik-zeile">${chips(['de', 'en', 'jp'], item.sprache || 'de', 'ikSprache')}</div>
    <div class="ik-lbl">${t('s_zustand')}</div><div class="ik-zeile">${chips(['', ...Object.keys(zustandFaktoren())], (item.zustand || '').toUpperCase(), 'ikZustand', (v) => v || '–')}</div>
    <div class="ik-lbl">${t('ik_aktionen')}</div>
    <div class="ik-akt">
      <button onclick="hatToggle(${idx});setTimeout(inspektorZeichnen,50)">${sn ? '✓ ' + t('ik_hat') : t('ik_hat_nicht')}</button>
      <button onclick="wunschToggle('${esc(item.id)}');setTimeout(inspektorZeichnen,300)">${wunschHat(item.id) ? '★ ' : '☆ '}${t('wl_titel')}</button>
      <button onclick="alarmOeffnen('karte','${esc(item.id)}',${JSON.stringify(name).replace(/"/g, '&quot;')},${preis || 0})">${t('al_t')}</button>
      <button onclick="detailOeffnen('${esc(item.id)}')">${t('s_details')}</button>
      <button onclick="themaOeffnen('${esc(item.id)}')">${t('s_passend')}</button>
      <div class="trenn"></div>
      <button onclick="fachEinfuegen(${idx})">${t('s_frei_davor')}</button>
      <button onclick="fachEinfuegen(${idx + 1})">${t('s_frei_danach')}</button>
      <div class="trenn"></div>
      <button onclick="fachFreimachen(${idx})">${t('s_entfernen')}</button>
      ${gefahr}
    </div>`;
}
function ikItem() { const idx = S.auswahl.size === 1 ? [...S.auswahl][0] : null; return idx === null ? null : S.binder.items[idx]; }
function ikVariante(v) { const it = ikItem(); if (!it) return; if (v === 'normal') delete it.variant; else it.variant = v; speichern(); zeichneBinder(); inspektorZeichnen(); }
function ikSprache(v) { const it = ikItem(); if (!it) return; if (v === 'de') delete it.sprache; else it.sprache = v; speichern(); zeichneBinder(); inspektorZeichnen(); }
function ikZustand(v) { const it = ikItem(); if (!it) return; if (!v) delete it.zustand; else it.zustand = v; speichern(); zeichneBinder(); inspektorZeichnen(); }

document.addEventListener('click', (ev) => { if (!ev.target.closest('#menu-slot') && !ev.target.closest('.slot .mehr')) slotMenueZu(); });
function fachSprache(idx) {
  const item = S.binder.items[idx]; if (!item) return;
  const reihe = ['de', 'en', 'jp']; const neu = reihe[(reihe.indexOf(item.sprache || 'de') + 1) % reihe.length];
  if (neu === 'de') delete item.sprache; else item.sprache = neu;
  speichern(); zeichneBinder();
}
function fachZustand(idx) {
  const item = S.binder.items[idx]; if (!item) return;
  const z = prompt(t('zustand_ph'), item.zustand || ''); if (z === null) return;
  if (z.trim()) item.zustand = z.trim().slice(0, 16); else delete item.zustand;
  speichern(); zeichneBinder();
}
function fachEinfuegen(idx) {
  merken('fach');
  S.binder.items.splice(idx, 0, { type: 'empty' });
  speichern(); zeichneBinder();
  toastUndo(t('s_frei_davor'));
}
function fachFreimachen(idx) {
  merken('frei');
  S.binder.items[idx] = { type: 'empty' };
  speichern(); zeichneBinder(); zeichneErgebnisse(); if (!$('planer').classList.contains('hidden')) zeichnePlaner();
  toastUndo(t('s_zu_leer'));
}
function seiteAuffuellen() {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  const letzte = seiteInfo(Math.max(0, seitenAnzahl() - 1));
  const rest = S.binder.items.length - letzte.start;
  const pp = letzte.laenge;
  if (!rest) return toast(t('gespeichert'));
  merken('auffuellen');
  for (let i = 0; i < pp - rest; i++) S.binder.items.push({ type: 'empty' });
  speichern(); zeichneBinder();
}
/** Zwei Nachbarseiten tauschen — mitsamt ihrem Raster, sonst zerfiele eine 4×4-Seite
 *  beim Hochschieben in eine 3×3-Grenze. Getauscht werden also Block und Layout. */
function seiteVerschieben(seite, d) {
  const ziel = seite + d;
  if (ziel < 0 || ziel >= seitenAnzahl()) return;
  const a = Math.min(seite, ziel), b = Math.max(seite, ziel);
  const pa = seiteInfo(a), pb = seiteInfo(b);
  merken('seite');
  // Beide Seiten auf ihre volle Länge bringen, damit der Tausch nichts abschneidet.
  while (S.binder.items.length < pb.start + pb.laenge) S.binder.items.push({ type: 'empty' });
  const items = S.binder.items;
  const blockA = items.slice(pa.start, pa.start + pa.laenge);
  const blockB = items.slice(pb.start, pb.start + pb.laenge);
  const mitte = items.slice(pa.start + pa.laenge, pb.start);
  items.splice(pa.start, pb.start + pb.laenge - pa.start, ...blockB, ...mitte, ...blockA);
  const je = (S.binder.options && S.binder.options.seitenLayouts) || null;
  if (je) {
    const ra = je[a], rb = je[b];
    if (rb == null) delete je[a]; else je[a] = rb;
    if (ra == null) delete je[b]; else je[b] = ra;
  }
  S.auswahl.clear(); speichern(); zeichneBinder();
}

/** Nach dem Entfernen einer Seite rutschen alle Rasterzuweisungen dahinter mit. */
function rasterNachziehen(ab, d) {
  const je = S.binder.options && S.binder.options.seitenLayouts;
  if (!je) return;
  const neu = {};
  for (const k of Object.keys(je)) {
    const n = Number(k);
    if (n === ab && d < 0) continue;             // die entfernte Seite selbst
    neu[n > ab ? n + d : n] = je[k];
  }
  S.binder.options.seitenLayouts = neu;
}
function seiteEntfernen(seite) {
  const sp = seiteInfo(seite);
  merken('seite');
  S.binder.items.splice(sp.start, sp.laenge);
  rasterNachziehen(seite, -1);
  S.auswahl.clear(); speichern(); zeichneBinder(); zeichneErgebnisse();
  toastUndo(t('s_seite_weg'));
}
function seiteLeeresFach(seite) {
  const sp = seiteInfo(seite);
  const ende = Math.min(sp.start + sp.laenge, S.binder.items.length);
  merken('fach');
  S.binder.items.splice(ende, 0, { type: 'empty' });
  speichern(); zeichneBinder();
}
function auswahlZuLeer() {
  if (!S.auswahl.size) return;
  /* gleichbedeutend mit auswahlEntfernen(); bleibt für Aufrufe aus dem Fach-Menü */
  merken('auswahl');
  for (const i of S.auswahl) if (S.binder.items[i]) S.binder.items[i] = { type: 'empty' };
  S.auswahl.clear(); speichern(); zeichnePlaner(); zeichneErgebnisse();
  toastUndo(t('zu_leer'));
}
let letzterKlick = null;

/* Ein Klick auf ein Fach wählt es aus — in beiden Zuständen des Binders. Vorher setzte
   derselbe Klick beim Blättern den Besitz-Haken und wählte in der Übersicht aus: dieselbe
   Geste, zwei Bedeutungen, je nachdem welcher Reiter offen war. Auswählen ist die
   verlustfreie Handlung; der Haken sitzt als eigener Knopf im Fach (slotExtras). */
function fachKlick(ev, idx) {
  if (ev && ev.target && ev.target.closest('.raus, .mehr, .hat-btn')) return;
  if (S.nurAnsicht || !S.binder.items[idx]) return;
  if (zielModus) return auswahlZuZiel(idx);
  if (ev && ev.shiftKey && letzterKlick !== null) {
    const [von, bis] = [Math.min(letzterKlick, idx), Math.max(letzterKlick, idx)];
    for (let i = von; i <= bis; i++) if (S.binder.items[i]) S.auswahl.add(i);
  } else {
    S.auswahl.has(idx) ? S.auswahl.delete(idx) : S.auswahl.add(idx);
  }
  letzterKlick = idx;
  auswahlZeigen();
  const it = S.binder.items[idx];
  if (it && it.type === 'empty' && S.auswahl.size === 1 && S.auswahl.has(idx)) sucheLadeOeffnen(false);
}
/** Alter Name, damit ältere Aufrufer nicht brechen. */
function slotKlick(idx) { fachKlick(null, idx); }

// ---------- Filter: „Mehr Filter“ ----------
let mehrFilterOffen = false;
// ---------- Bildmotiv-Filter ----------
// Sucht nach dem Inhalt der Illustration statt nach Kartendaten. Die Grundlage
// liefert der Bildindex aus themen.py; die Beschriftungen stehen hier, weil der
// Server die Schlüssel neutral hält (orte/merkmale) und die App sie übersetzt.
const ART_ORT_TEXT = {
  unterwasser: ['Unter Wasser', 'Underwater'], gewaesser: ['Am Wasser', 'By the water'], strand: ['Strand', 'Beach'],
  fluss: ['Fluss', 'River'], wald: ['Wald', 'Forest'], dschungel: ['Dschungel', 'Jungle'], wiese: ['Wiese', 'Meadow'],
  berge: ['Berge', 'Mountains'], hoehle: ['Höhle', 'Cave'], vulkan: ['Vulkan', 'Volcano'], wueste: ['Wüste', 'Desert'],
  schnee: ['Schnee', 'Snow'], stadt: ['Stadt', 'City'], gebaeude: ['Gebäude', 'Building'], innenraum: ['Innenraum', 'Indoors'],
  ruinen: ['Ruinen', 'Ruins'], himmel: ['Himmel', 'Sky'], weltraum: ['Weltraum', 'Space'], dunkelheit: ['Dunkelheit', 'Darkness'],
  unterirdisch: ['Unterirdisch', 'Underground'], technik: ['Technik', 'Technology'], abstrakt: ['Abstrakt', 'Abstract'],
  portraet: ['Porträt', 'Portrait'], kampf: ['Kampf', 'Battle'],
};
const ART_MERKMAL_TEXT = {
  mond: ['Mond', 'Moon'], sterne: ['Sterne', 'Stars'], sonne: ['Sonne', 'Sun'], sonnenuntergang: ['Sonnenuntergang', 'Sunset'],
  regenbogen: ['Regenbogen', 'Rainbow'], wolken: ['Wolken', 'Clouds'], regen: ['Regen', 'Rain'], schnee: ['Schneefall', 'Snowfall'],
  gewitter: ['Gewitter', 'Thunderstorm'], nebel: ['Nebel', 'Fog'], feuer: ['Feuer', 'Fire'], blitz: ['Blitz', 'Lightning'],
  eis: ['Eis', 'Ice'], rauch: ['Rauch', 'Smoke'], blumen: ['Blumen', 'Flowers'], baeume: ['Bäume', 'Trees'], gras: ['Gras', 'Grass'],
  felsen: ['Felsen', 'Rocks'], wasserfall: ['Wasserfall', 'Waterfall'], gebaeude: ['Häuser', 'Houses'], bruecke: ['Brücke', 'Bridge'],
  fahrzeug: ['Fahrzeug', 'Vehicle'], technik: ['Maschinen', 'Machines'], mensch: ['Mensch', 'Human'],
  mehrere_pokemon: ['Mehrere Pokémon', 'Several Pokémon'], gegenstand: ['Gegenstand', 'Object'], essen: ['Essen', 'Food'],
  musik: ['Musik', 'Music'], fliegt: ['Fliegt', 'Flying'], springt: ['Springt', 'Jumping'], rennt: ['Rennt', 'Running'],
  schlaeft: ['Schläft', 'Sleeping'], kampf: ['Kampfszene', 'Battle scene'], nahaufnahme: ['Nahaufnahme', 'Close-up'],
  silhouette: ['Silhouette', 'Silhouette'], spiegelung: ['Spiegelung', 'Reflection'], leuchtet: ['Leuchtet', 'Glowing'],
};
// Ort und Merkmal überschneiden sich an vier Stellen — die Ortsspalte gewinnt,
// damit in der Leiste nicht zweimal „Wald“ oder „Kampf“ steht.
const ART_MERKMAL_AUS = ['schnee', 'gebaeude', 'technik', 'kampf'];
// Die Bildmotiv-Suche kennt 24 Orte und 37 Merkmale — nebeneinander sind das sechzig
// Chips, in denen niemand etwas findet. Gezeigt wird deshalb, was viele Treffer *und*
// eine anschauliche Bedeutung hat (Zahlen aus 23.461 gesichteten Karten); der Rest steht
// eine Zeile tiefer hinter „Seltene Motive zeigen". „abstrakt" (28 %) und „leuchtet"
// (30 %) sind die häufigsten Werte überhaupt und trotzdem die nutzlosesten Filter — sie
// beschreiben keinen Ort und kein Motiv, sondern den Kartenhintergrund.
const ART_ORT_TOP = ['wald', 'wiese', 'himmel', 'gewaesser', 'berge', 'stadt',
                     'unterwasser', 'wueste', 'schnee', 'weltraum', 'hoehle', 'strand'];
const ART_MERKMAL_TOP = ['fliegt', 'mehrere_pokemon', 'mensch', 'blumen', 'feuer', 'sterne',
                         'mond', 'eis', 'blitz', 'wasserfall', 'regenbogen', 'schlaeft'];
let artAlle = false;
function artAlleUmschalten() {
  artAlle = !artAlle;
  const b = $('f-art-alle-btn');
  if (b) b.textContent = t(artAlle ? 'f_art_wenig' : 'f_art_alle');
  zeichneArtChips();
}
let artFilterOffen = false, artIndex = null, artTippUhr = null;

const artText = (tab, k) => (tab[k] || [k, k])[LANG === 'en' ? 1 : 0];

function artFilterToggle(an) {
  artFilterOffen = an !== undefined ? an : !artFilterOffen;
  $('f-art-box').classList.toggle('hidden', !artFilterOffen);
  $('f-art-caret').textContent = artFilterOffen ? '▴' : '▾';
  if (artFilterOffen && !artIndex) artIndexLaden();
}

async function artIndexLaden() {
  try {
    artIndex = await api('api/themen/status');
  } catch (e) { artIndex = { gesichtet: 0, gesamt: 0, orte: [], merkmale: [] }; }
  zeichneArtChips();
  artHinweis();
}

function artHinweis() {
  if (!artIndex) return;
  const el = $('f-art-hinweis');
  const anteil = artIndex.gesamt ? Math.round(artIndex.gesichtet / artIndex.gesamt * 100) : 0;
  el.textContent = t('f_art_stand').replace('{p}', anteil)
    .replace('{n}', (artIndex.gesichtet || 0).toLocaleString(LANG === 'en' ? 'en' : 'de'));
  el.classList.toggle('warn', anteil < 90);
}

function zeichneArtChips() {
  if (!artIndex) return;
  const chip = (an, klick, label) => `<button class="chip ${an ? 'on' : ''}" onclick="${klick}">${esc(label)}</button>`;
  // Gewählte Filter bleiben immer sichtbar, auch wenn sie aus der kurzen Liste fallen —
  // sonst verschwindet ein aktiver Filter aus der Ansicht und niemand findet ihn wieder.
  const sicht = (liste, top, gewaehlt) => (liste || []).filter(
    (x) => artAlle || top.includes(x) || gewaehlt.has(x));
  const ortListe = sicht(artIndex.orte, ART_ORT_TOP, filter.artOrte);
  const merkListe = sicht((artIndex.merkmale || []).filter((m) => !ART_MERKMAL_AUS.includes(m)),
                          ART_MERKMAL_TOP, filter.artMerkmale);
  const rang = (top) => (a, b) => (top.indexOf(a) + 1 || 99) - (top.indexOf(b) + 1 || 99);
  $('f-art-orte').innerHTML = ortListe.sort(rang(ART_ORT_TOP)).map((o) =>
    chip(filter.artOrte.has(o), `artOrtToggle('${o}')`, artText(ART_ORT_TEXT, o))).join('');
  $('f-art-merkmale').innerHTML = merkListe.sort(rang(ART_MERKMAL_TOP)).map((m) =>
    chip(filter.artMerkmale.has(m), `artMerkmalToggle('${m}')`, artText(ART_MERKMAL_TEXT, m))).join('');
  $('f-art-zeit').innerHTML = [['tag', t('f_art_tag')], ['daemmerung', t('f_art_daemmerung')], ['nacht', t('f_art_nacht')]]
    .map(([w, l]) => chip(filter.artZeit === w, `artZeitToggle('${w}')`, l)).join('');
  $('f-art-wasser').innerHTML = [[1, t('f_art_w1')], [2, t('f_art_w2')], [3, t('f_art_w3')]]
    .map(([w, l]) => chip(filter.artWasser === w, `artWasserToggle(${w})`, l)).join('');
}

function artOrtToggle(o) {
  filter.artOrte.has(o) ? filter.artOrte.delete(o) : filter.artOrte.add(o);
  zeichneArtChips(); artFilterZahl(); sucheNeu();
}
function artMerkmalToggle(m) {
  filter.artMerkmale.has(m) ? filter.artMerkmale.delete(m) : filter.artMerkmale.add(m);
  zeichneArtChips(); artFilterZahl(); sucheNeu();
}
function artZeitToggle(z) { filter.artZeit = filter.artZeit === z ? '' : z; zeichneArtChips(); artFilterZahl(); sucheNeu(); }
function artWasserToggle(w) { filter.artWasser = filter.artWasser === w ? 0 : w; zeichneArtChips(); artFilterZahl(); sucheNeu(); }

function artTextGetippt() {
  clearTimeout(artTippUhr);
  artTippUhr = setTimeout(() => {
    filter.artText = $('f-art-text').value;
    artFilterZahl(); sucheNeu();
  }, 400);
}

function artFilterZahl() {
  if (typeof zeichneMotivSchnell === 'function' && $('f-motiv-schnell')) zeichneMotivSchnell();
  const n = filter.artOrte.size + filter.artMerkmale.size + (filter.artZeit ? 1 : 0)
    + (filter.artWasser ? 1 : 0) + (filter.artText.trim() ? 1 : 0);
  const el = $('f-art-zahl'); if (el) el.textContent = n ? `(${n})` : '';
  const btn = $('f-art-btn'); if (btn) btn.style.borderColor = n ? 'var(--akzent)' : '';
}

function mehrFilterToggle(an) {
  mehrFilterOffen = an !== undefined ? an : !mehrFilterOffen;
  $('f-mehr').classList.toggle('hidden', !mehrFilterOffen);
  $('f-mehr-caret').textContent = mehrFilterOffen ? '▴' : '▾';
}
function mehrFilterZahl() {
  if ($('filter-zahl-mobil')) setTimeout(() => {
    const n = ($('f-mehr-zahl') && $('f-mehr-zahl').textContent) || '';
    $('filter-zahl-mobil').textContent = n ? ' ' + n : '';
    if ($('filter-zahl-lade')) $('filter-zahl-lade').textContent = n ? ' ' + n : '';
  }, 0);
  const n = filter.kinds.size + (filter.typ ? 1 : 0) + (filter.trainer ? 1 : 0) + filter.regmark.size + (filter.first ? 1 : 0) + (filter.jahrVon || filter.jahrBis ? 1 : 0);
  const el = $('f-mehr-zahl'); if (el) el.textContent = n ? `(${n})` : '';
  const btn = $('f-mehr-btn'); if (btn) btn.style.borderColor = n ? 'var(--akzent)' : '';
}

// ---------- Kartendetail ----------
const VARIANTEN = ['normal', 'reverse', 'holo', 'first', 'pokeball', 'masterball'];
const VMARK = { reverse: 'REV', holo: 'HOLO', first: '1ST', pokeball: 'PB', masterball: 'MB' };
let detailKarte = null; let detailVariante = 'normal'; let detailSprache = 'de';
/** Bei schon offenem Dialog nur den Inhalt tauschen: „andere Drucke“ legte sonst je Klick
 *  eine neue Ebene und einen Verlaufseintrag an — am Handy verschluckte Zurück danach Klicks. */
function detailModalZeigen() {
  if ($('modal-detail').classList.contains('hidden')) modalOeffnen('modal-detail');
}

/** Reiter im Karten-Dialog: Preise (Trend, Zustände, Verlauf) · Drucke (andere Ausgaben) · Reihe. */
function detailTab(name, btn) {
  document.querySelectorAll('#detail-inhalt .d-tab').forEach((el) => el.classList.toggle('hidden', el.dataset.tab !== name));
  document.querySelectorAll('#detail-tabs button').forEach((b) => b.classList.toggle('on', b === btn));
}

async function detailOeffnen(idOrIdx) {
  const id = typeof idOrIdx === 'number' ? (S.ergebnisse[idOrIdx] || {}).id : idOrIdx;
  if (!id) return;
  detailModalZeigen();
  $('detail-inhalt').innerHTML = `<div style="padding:40px;text-align:center;color:var(--mut)">…</div>`;
  let k;
  try { k = await api('api/cards/' + encodeURIComponent(id) + '/detail'); } catch (e) { $('detail-inhalt').textContent = '?'; return; }
  detailKarte = k; detailSprache = k.region === 'jp' ? 'jp' : KLANG;
  // Die erste Ausprägung, die es wirklich gibt: bei einer reinen Holo-Karte war „normal“
  // vorgewählt, obwohl der Chip dafür gar nicht angeboten wird.
  detailVariante = k.normal !== false ? 'normal' : (k.holo ? 'holo' : (k.reverse ? 'reverse' : (k.first ? 'first' : 'normal')));
  const fmt = (v) => v == null ? '?' : v.toFixed(2).replace('.', LANG === 'de' ? ',' : '.') + ' €';
  const preis = k.preis || {};
  const verlauf = (k.verlauf || []).filter((v) => v.eur != null);
  let spark = '';
  if (verlauf.length >= 2) {
    const ys = verlauf.map((v) => v.eur); const min = Math.min(...ys), max = Math.max(...ys);
    const pts = ys.map((y, i) => `${(i / (ys.length - 1) * 100).toFixed(1)},${(52 - (max === min ? 26 : (y - min) / (max - min) * 44 + 4)).toFixed(1)}`).join(' ');
    spark = `<svg class="spark" viewBox="0 0 100 56" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="var(--gruen)" stroke-width="2"/></svg>
      <div style="display:flex;justify-content:space-between;font-size: var(--t-xs);color:var(--mut);margin-top:-8px;margin-bottom:10px"><span>${verlauf[0].datum}</span><span>${t('verlauf')} · ${verlauf.length} ${t('tage')}</span><span>${verlauf[verlauf.length - 1].datum}</span></div>`;
  } else spark = `<div style="font-size: var(--t-s);color:var(--mut);margin-bottom:10px">${t('verlauf_bald')}</div>`;
  // Nur die Ausprägungen zeigen, die es für diese Karte wirklich gibt — auch die
  // Sondermuster. Poké Ball und Master Ball standen vorher bei jeder der 33.732 Karten
  // zur Wahl, auch beim Grundset-Glurak von 1999; es gibt sie in genau zwei Sets. Welche
  // das sind, sagt das Backend über `k.muster`.
  const muster = k.muster || [];
  const gibtEs = (v) => (v === 'normal' ? k.normal !== false
    : v === 'reverse' ? !!k.reverse
    : v === 'holo' ? !!k.holo
    : v === 'first' ? !!k.first : muster.includes(v));
  const varChip = (v) => {
    if (!gibtEs(v)) return '';
    return `<button class="chip ${detailVariante === v ? 'on' : ''}" onclick="detailVarianteSetzen('${v}')">${t('v_' + v)}</button>`;
  };
  const andere = (k.andere || []).map((a) => `<button onclick="detailOeffnen('${a.id}')" title="${esc(nm(a))} · ${esc(setNm(a))}"><img loading="lazy" src="${imgUrl(a.id)}" alt=""><span>${esc(setNm(a))} · ${esc(a.local_id || '')}</span></button>`).join('');
  // Der Link führt auf die Produktseite genau dieser Karte statt auf eine Namenssuche —
  // bei „Guardevoir“ standen dort 60 Treffer, von denen keiner die gemeinte Karte war.
  // Kennt der Server die Adresse schon, steht sie sofort im href; sonst wird sie im
  // Hintergrund geholt und nachgetragen. Ein `window.open` erst beim Klick ging nicht:
  // nach dem await gilt es dem Browser nicht mehr als Klick, und der Popup-Blocker
  // schluckte jedes zweite Fenster.
  const cm = cardmarketAdresse(k);
  $('detail-inhalt').innerHTML = `
    <div class="d-grid">
      <div>${k.img ? `<img class="d-bild" src="${imgUrl(k.id)}" alt="" data-high="${imgUrl(k.id, '&variante=high')}" onload="if(this.dataset.high){const h=this.dataset.high;this.dataset.high='';const im=new Image();im.onload=()=>{this.src=h};im.src=h}" onerror="this.outerHTML='<div class=d-kein-bild>${esc(nm(k))}<small>${t('kein_scan')}</small></div>'">`
        : `<div class="d-kein-bild">${esc(nm(k))}<small>${t('kein_scan')}</small></div>`}
        ${k.img && k.img_lang && k.img_lang !== 'de' && KLANG === 'de' && k.region !== 'jp' ? `<div style="font-size: var(--t-xs);color:var(--mut);margin-top:6px">${t('bild_en')}</div>` : ''}
        <!-- Ausprägung und Kartensprache gehören zum Bild, nicht zwischen Preise und Knöpfe:
             so ist die linke Spalte gefüllt und rechts stehen Preise, Verlauf und Aktionen ohne Lücke. -->
        <div class="f-lbl" style="margin-top:10px">${t('variante')}</div>
        <div class="var-chips" id="detail-chips">${VARIANTEN.map(varChip).join('')}</div>
        <div class="f-lbl">${t('kartensprache')}</div>
        <div class="var-chips" id="detail-sprache">${(k.region === 'jp' ? ['jp'] : ['de', 'en', 'jp']).map((sp) => `<button class="chip ${detailSprache === sp ? 'on' : ''}" onclick="detailSpracheSetzen('${sp}')">${sp.toUpperCase()}</button>`).join('')}</div></div>
      <div>
        <h2>${esc(nm(k))}</h2>
        <div class="d-meta">${k.name_ja && k.name_ja !== nm(k) ? `<span title="${t('d_name_ja')}">${esc(k.name_ja)}</span> · ` : ''}${esc(setNm(k))} · ${esc(k.local_id || '')}${k.set && k.set.official ? '/' + k.set.official : ''}${k.rarity ? ' · ' + esc(k.rarity) : ''}${(k.types || []).length ? ' · ' + k.types.map((x) => T[LANG].typen[x] || x).join(', ') : ''}${k.hp ? ' · ' + k.hp + ' HP' : ''}${k.datum ? ' · ' + k.datum.slice(0, 4) : ''}${k.region === 'jp' ? ' · JP' : ''}${k.regmark ? ` · ${t('d_regmark')} ${esc(k.regmark)}` : ''}</div>
        ${k.illustrator ? `<div class="d-meta" style="margin-top:-8px">${t('d_illu')}: <span class="d-illu" onclick="modalSchliessen();illuSetzen('${esc(k.illustrator).replace(/'/g, "\\'")}');mobilAnsicht('suche')" title="${t('d_illu_btn')}">${esc(k.illustrator)}</span></div>` : ''}
        <div class="d-aktion" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <button class="btn" onclick="detailHinzufuegen()">＋ ${t('in_binder')}</button>
          <button class="btn sekundaer" id="detail-hab" onclick="sammlungAufnehmen('${esc(k.id)}')"
                  title="${t('sm_aufnehmen_t')}" aria-label="${t('tk_besitz')}">${(S.user && S.besitz[k.id]) ? '✓ ' + S.besitz[k.id] + '× <span class="txt">' + t('tk_besitz') + '</span>' : '✓ <span class="txt">' + t('tk_besitz') + '</span>'}</button>
          <button class="btn sekundaer" id="detail-wunsch" onclick="wunschToggle('${esc(k.id)}', 1)" aria-label="${t('wl_titel')}">${wunschHat(k.id) ? '★ <span class="txt">' + t('wl_drauf') + '</span>' : '☆ <span class="txt">' + t('wl_titel') + '</span>'}</button>
          <button class="btn sekundaer" id="detail-alarm" onclick="alarmOeffnen('karte', '${esc(k.id)}', ${JSON.stringify(nm(k) + ' · ' + setNm(k) + ' ' + (k.local_id || '')).replace(/"/g, '&quot;')}, ${detailPreis(k, detailVariante) || 0})" title="${t('al_t')}" aria-label="${t('al_t')}">🔔 <span class="txt">${t('al_kurz')}</span></button>
          <span style="font-size: var(--t-s);color:var(--mut)" id="detail-anz">${anzahlImBinder(k.id) ? anzahlImBinder(k.id) + '× ' + t('im_binder') : ''}</span>
        </div>
        <div class="d-tabs" id="detail-tabs">
          <button class="on" onclick="detailTab('preise',this)">${t('d_tab_preise')}</button>
          ${andere ? `<button onclick="detailTab('drucke',this)">${t('d_tab_drucke')} <span class="d-tab-n">${k.andere_gesamt || k.andere.length}</span></button>` : ''}
          ${(k.familie || []).length > 1 ? `<button onclick="detailTab('reihe',this)">${t('d_tab_reihe')}</button>` : ''}
        </div>
        <div class="d-tab" data-tab="preise">
        <div class="d-preise">
          <div class="d-preis"><div class="z" id="detail-preis-zahl">${fmt(detailPreis(k, detailVariante))}</div><div class="l" id="detail-preis-lbl">${t('preis_trend')}</div></div>
          ${preis.eur_holo && (k.normal !== false || k.reverse) ? `<div class="d-preis"><div class="z">${fmt(preis.eur_holo)}</div><div class="l">${t('preis_holo')}</div></div>` : ''}
          <div class="d-preis" style="background:transparent;padding-left:0"><a href="${cm}" id="detail-cm" target="_blank" rel="noopener noreferrer" style="font-size: var(--t-s)">Cardmarket ↗</a><div class="l" id="detail-preis-stand">${preis.stand ? preis.stand.slice(0, 10) : t('preis_laden')}</div>
          <div class="l" id="detail-spanne" style="margin-top:4px;line-height:1.5"></div></div>
          <div id="detail-zustaende" class="zst"></div>
        </div>
        ${spark}
        <div class="d-zustand"><input class="feld" id="detail-zustand" style="width:190px;padding:6px 10px;font-size: var(--t-s)" placeholder="${t('zustand_ph')}" maxlength="16" oninput="cardmarketLinkAuffrischen()"></div>
        </div>
        <div class="d-tab hidden" data-tab="drucke">
        ${andere ? `<div class="f-lbl" style="margin-top:6px">${t('andere_drucke')} (${k.andere_gesamt || k.andere.length})</div><div class="d-andere">${andere}</div>` : ''}
        </div>
        <div class="d-tab hidden" data-tab="reihe">
        ${(k.familie || []).length > 1 ? `<div class="d-fam"><span class="f-lbl" style="margin:0">${t('d_fam')}</span>${k.familie.map((f) => `<span class="chip klein" style="cursor:default${f.dex === k.dex ? ';border-color:var(--akzent-text);color:var(--akzent-text)' : ''}">${esc(f.name)}</span>`).join('<span style="color:var(--mut)">→</span>')}<button class="btn sekundaer" style="font-size: var(--t-s);padding:4px 10px" onclick="familieErstellen(${k.dex})">${t('d_fam_btn')}</button></div>` : ''}
        </div>
      </div>
    </div>`;
  if (!k.cm_url) cardmarketNachtragen(k.id);
  // Die Spanne unter den Trendpreis: Preise je Zustand führt keine der beiden Börsen,
  // aber Tiefstpreis, 30-Tage-Schnitt und die US-Spanne zeigen den Handelsraum einer Karte.
  // Preise je Zustand veröffentlicht keine der beiden Börsen. Was sich vertreten lässt:
  // der Trend als Nähe-Neuwert, der Tiefstpreis als das untere Ende, dazwischen die
  // Abschläge, mit denen im Handel üblicherweise gerechnet wird. Das ist eine Ableitung
  // und wird auch so benannt — keine gemessene Zahl.
  if (preis.eur && $('detail-zustaende')) {
    // Basis ist die gewählte Ausprägung: für ein Holo-Exemplar standen hier die Preise der
    // Normalfassung. Poor bekommt den echten Tiefstpreis, wo Cardmarket einen kennt.
    const zeilen = Object.keys(zustandFaktoren()).map((z) => {
      const wert = postenWert(preis.eur, preis.eur_holo, preis.eur_low, detailVariante, z);
      return `<div><span>${z}</span><strong>${fmt(wert)}</strong></div>`;
    }).join('');
    // Sieben Kacheln waren der größte Block im Dialog, obwohl es eine Ableitung ist.
    // Jetzt eine Zeile mit Aufklapper — der Nahe-Neuwert steht sichtbar, der Rest auf Klick.
    const nm = postenWert(preis.eur, preis.eur_holo, preis.eur_low, detailVariante, 'NM');
    $('detail-zustaende').innerHTML =
      `<button class="zst-auf" onclick="this.nextElementSibling.classList.toggle('hidden');this.querySelector('i').textContent=this.nextElementSibling.classList.contains('hidden')?'▾':'▴'">
         <span>${t('zst_titel')}</span><strong>NM ${fmt(nm)}</strong><i>▾</i></button>
       <div class="hidden"><div class="zst-liste">${zeilen}</div><div class="zst-hin">${t('zst_hin')}</div></div>`;
  }
  // Fünf Zahlen in einer Zeile waren fünf mögliche „Preise“. Der Trend bleibt die eine
  // sichtbare Zahl; Tiefstpreis, 7- und 30-Tage-Schnitt und der US-Markt liegen als
  // Tabelle hinter „Mehr Preise“, die Hinweise zur Quelle darunter.
  const fmtUsd = (n) => n.toLocaleString(LANG === 'en' ? 'en-US' : 'de-DE', { style: 'currency', currency: 'USD' });
  const zeilenMehr = [], hinweise = [];
  if (preis.eur_low != null) zeilenMehr.push([t('pr_ab_lbl'), fmt(preis.eur_low)]);
  if (preis.eur_avg7 != null) zeilenMehr.push([t('pr_7'), fmt(preis.eur_avg7)]);
  if (preis.eur_avg30 != null) zeilenMehr.push([t('pr_30'), fmt(preis.eur_avg30)]);
  // Die obere US-Zahl war das teuerste Einzelangebot: 132 Karten führten dort 9.999 $, bei
  // 11.499 lag sie über dem Zwanzigfachen des Marktpreises. Gezeigt wird der Marktpreis.
  if (preis.usd != null) zeilenMehr.push([t('pr_usa_lbl'), fmtUsd(preis.usd)]);
  // Urteil des Börsenvergleichs: weichen Cardmarket und TCGplayer weit voneinander ab,
  // stimmt mindestens eine der beiden Zahlen nicht. Ein großer Abstand heißt nicht
  // automatisch „falsch“: alte Karten gehen in den USA regelmäßig zum Mehrfachen weg.
  if (preis.status === 'unsicher' && preis.kurs) {
    const f = preis.kurs / 1.32;
    hinweise.push(t('pr_abstand').replace('{f}', (f > 1 ? f : 1 / f).toFixed(1).replace('.', ',')));
  }
  if (preis.status === 'geschaetzt') hinweise.push(t('pr_geschaetzt'));
  if (preis.status === 'zweitquelle') hinweise.push(t('pr_zweitquelle'));
  if (preis.direkt) hinweise.push(t('pr_direkt'));
  if ((zeilenMehr.length || hinweise.length) && $('detail-spanne')) {
    $('detail-spanne').innerHTML =
      (zeilenMehr.length ? `<button class="zst-auf" onclick="this.nextElementSibling.classList.toggle('hidden');this.querySelector('i').textContent=this.nextElementSibling.classList.contains('hidden')?'▾':'▴'">
           <span>${t('pr_mehr')}</span><strong>${zeilenMehr.length}</strong><i>▾</i></button>
         <div class="hidden"><div class="zst-liste">${zeilenMehr.map(([l, v]) => `<div><span>${esc(l)}</span><strong>${v}</strong></div>`).join('')}</div></div>` : '')
      + (hinweise.length ? `<div style="margin-top:4px">${esc(hinweise.join(' · '))}</div>` : '');
  }
  if (preis.ohne_quelle && $('detail-preis-stand')) {
    $('detail-preis-stand').textContent = t('preis_ohne_quelle');
    return;
  }
  if (preis.geteilt > 1 && $('detail-preis-stand')) {
    // Kein Preis, weil Cardmarket diese Karte mit anderen unter einem Produkt führt.
    $('detail-preis-stand').textContent = t('preis_geteilt').replace('{n}', preis.geteilt);
    return;
  }
  if (preis.eur == null) {   // Preis nachladen, falls noch nie geholt
    try { const d = await api('api/preise', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids: [k.id] }) });
      if (detailKarte && detailKarte.id === k.id) {
        if (d.preise[k.id] != null) { detailKarte.preis = { eur: d.preise[k.id], eur_holo: d.holo[k.id], stand: new Date().toISOString() }; detailOeffnen(k.id); }
        else if ($('detail-preis-stand')) $('detail-preis-stand').textContent = t('kein_preis');
      }
    } catch (e) { if ($('detail-preis-stand')) $('detail-preis-stand').textContent = t('kein_preis'); }
  }
}
/** Der Preis der gewählten Ausprägung. `preis.varianten` kommt aus `variants_detailed`
 *  bei TCGdex — dort hat jede Druckvariante ihr eigenes Cardmarket-Produkt. Fehlt sie,
 *  gilt die alte Zuordnung: Trend für die Grundausgabe, Trend-Holo für die zweite. */
/* Die Zustandsfaktoren standen dreimal im Browser-Code, mit drei verschiedenen Verhalten:
   zwei kannten den Poor-Deckel nicht, zwei rechneten nie mit dem Holo-Preis. Jetzt kommen sie
   aus /api/meta — dieselbe Tabelle wie im Backend (wert.py) — und es gibt genau eine Funktion,
   die sie anwendet. */
const ZUSTAND_FAKTOR_FALLBACK = { M: 1.10, NM: 1.00, EX: 0.85, GD: 0.70, LP: 0.55, PL: 0.42, PO: 0.30 };
function zustandFaktoren() {
  return (S.meta && S.meta.zustand_faktor) || ZUSTAND_FAKTOR_FALLBACK;
}
/** Was ein Exemplar wert ist — dieselbe Regel wie wert.posten_wert() auf dem Server. */
function postenWert(eur, eurHolo, eurLow, variante, zustand) {
  const basis = (variante === 'holo' || variante === 'reverse') && eurHolo ? eurHolo : eur;
  if (basis == null) return null;
  const f = zustandFaktoren()[(zustand || '').toUpperCase()];
  if (!f) return Math.round(basis * 100) / 100;
  let w = basis * f;
  if ((zustand || '').toUpperCase() === 'PO' && eurLow != null) w = Math.min(w, eurLow);
  return Math.round(w * 100) / 100;
}

function detailPreis(k, variante) {
  const p = (k && k.preis) || {};
  const v = p.varianten || {};
  if (v[variante] != null) return v[variante];
  if ((variante === 'reverse' || variante === 'holo') && p.eur_holo != null) return p.eur_holo;
  if (p.eur != null) return p.eur;
  return p.eur_geschaetzt != null ? p.eur_geschaetzt : null;
}

const CM_SPRACHE_NR = { de: 3, en: 1, fr: 2, es: 4, it: 5, jp: 7 };
// Cardmarkets Mindestzustand. Wer einen Posten in GD führt, will keine Near-Mint-Angebote.
const CM_ZUSTAND_NR = { M: 1, NM: 2, EX: 3, GD: 4, LP: 5, PL: 6, PO: 7 };

/** Die Adresse für den Cardmarket-Link. Kennt der Server die Produktseite, steht sie
 *  sofort da; sonst zunächst die Namenssuche als Rückfall. */
function cardmarketAdresse(k) {
  const loc = LANG === 'en' ? 'en' : 'de';
  if (k && k.cm_url) {
    const teile = [];
    const sp = CM_SPRACHE_NR[detailSprache || ''];
    if (sp) teile.push('language=' + sp);
    // Der Zustand aus dem Feld im Dialog — leer lassen heißt: alle Angebote.
    const z = CM_ZUSTAND_NR[(($('detail-zustand') || {}).value || '').trim().toUpperCase().slice(0, 2)];
    if (z) teile.push('minCondition=' + z);
    return 'https://www.cardmarket.com/' + loc + k.cm_url + (teile.length ? '?' + teile.join('&') : '');
  }
  // Rückfall: die Suche — aber mit Cardmarkets eigenen Angaben. `cm_such` ist der Name,
  // unter dem die Karte dort steht („Arceus LV.X“ statt unserem „Arceus“), `cm_exp` ihre
  // Erweiterung. Ohne die beiden lief der Link auf eine Namenssuche über zwanzig
  // Jahrgänge — genau das Bild, wenn man auf „Rayquaza C LV.X“ klickte.
  // Eckige Klammern fallen für die Suche weg („Rayquaza [C] LV.X“ → „Rayquaza C LV.X“).
  let name = ((k && k.cm_such) || '').replace(/[[\]]/g, ' ').replace(/\s+/g, ' ');
  if (!name) {
    name = ((k && (k.name_en || k.name)) || '').replace(/ · .*/, '');
    if (k && k.stage === 'LEVEL-UP' && !/lv/i.test(name)) name += ' LV.X';
  }
  const suche = ['searchString=' + encodeURIComponent(name.trim()), 'idCategory=51'];
  if (k && k.cm_exp) suche.push('idExpansion=' + k.cm_exp);
  return 'https://www.cardmarket.com/' + loc + '/Pokemon/Products/Search?' + suche.join('&');
}

function cardmarketLinkAuffrischen() {
  const a = $('detail-cm');
  if (a && detailKarte) a.href = cardmarketAdresse(detailKarte);
}

/** Ist die Produktseite noch nicht bekannt, im Hintergrund auflösen und den Link
 *  austauschen — der Nutzer merkt davon nichts, außer dass der Link stimmt. */
async function cardmarketNachtragen(id) {
  try {
    const z = (($('detail-zustand') || {}).value || '').trim();
    const d = await api(`api/cards/${encodeURIComponent(id)}/cardmarket`
      + `?sprache=${detailSprache || ''}&ui=${LANG}&zustand=${encodeURIComponent(z)}`);
    // Auch die *ungenaue* Adresse übernehmen: sie ist die Suche mit Erweiterung und
    // Kategorie und damit immer besser als die, die die Oberfläche selbst bauen kann.
    const a = $('detail-cm');
    if (a && d.url && detailKarte && detailKarte.id === id) a.href = d.url;
  } catch (e) { /* Rückfall bleibt die Namenssuche */ }
}

/** Die Kartensprache hängt als Filter an der Cardmarket-Adresse. */
function detailSpracheSetzen(sp) {
  detailSprache = sp;
  document.querySelectorAll('#detail-sprache .chip').forEach((c) =>
    c.classList.toggle('on', c.textContent.trim() === sp.toUpperCase()));
  cardmarketLinkAuffrischen();
}

function detailVarianteSetzen(v) {
  detailVariante = v;
  zeichneDetailChips();
  // Vorher änderte die Wahl der Ausprägung den Preis nicht: Reverse Holo kostete im
  // Dialog dasselbe wie Normal.
  const zahl = $('detail-preis-zahl'), lbl = $('detail-preis-lbl');
  if (zahl && detailKarte) {
    const w = detailPreis(detailKarte, v);
    zahl.textContent = w == null ? '?' : w.toFixed(2).replace('.', LANG === 'de' ? ',' : '.') + ' €';
    if (lbl) lbl.textContent = v === 'normal' ? t('preis_trend') : t('preis_trend') + ' · ' + t('v_' + v);
  }
}

function zeichneDetailChips() {
  document.querySelectorAll('#detail-chips .chip').forEach((c) =>
    c.classList.toggle('on', c.textContent.trim() === t('v_' + detailVariante)));
}
function detailHinzufuegen() {
  if (!detailKarte || !S.binder) return;
  const zustand = ($('detail-zustand').value || '').trim();
  kartAddId(detailKarte.id, detailVariante, zustand, detailSprache);
  $('detail-anz').textContent = anzahlImBinder(detailKarte.id) + '× ' + t('im_binder');
}
/** Wohin die nächste Karte geht: in das gewählte Fach, sonst ans Ende. Genau ein Fach
 *  gewählt heißt „dorthin"; mehrere oder keins heißt „anhängen". */
function zielFach() {
  if (S.nurAnsicht || S.auswahl.size !== 1) return null;
  const idx = [...S.auswahl][0];
  const item = S.binder && S.binder.items[idx];
  return item && item.type === 'empty' ? idx : null;
}
function zielText() {
  const z = zielFach();
  return z === null ? t('tk_anhaengen') : t('tk_in_fach').replace('{n}', z + 1);
}

async function kartAddId(id, variant, zustand, sprache) {
  if (!S.binder) return;
  merken('add');
  const item = { type: 'card', id, variant: variant || 'normal' };
  if (zustand) item.zustand = zustand;
  const sp = sprache || (/^[A-Z]/.test(id) ? 'jp' : KLANG);
  if (sp !== 'de') item.sprache = sp;
  // Der Knopf hieß „Ins Fach", legte die Karte aber immer ans Ende. Jetzt geht sie in das
  // gewählte leere Fach — und der Knopf sagt vorher, welches das ist.
  const ziel = zielFach();
  if (ziel === null) S.binder.items.push(item);
  else S.binder.items[ziel] = item;
  if (!await binderAnlegenWennNoetig()) {
    if (ziel === null) S.binder.items.pop(); else S.binder.items[ziel] = { type: 'empty' };
    return;
  }
  if (ziel !== null) {
    S.auswahl.clear();
    // Weiterrücken: wer eine Seite füllt, will nach dem Einsetzen das nächste freie Fach.
    const naechstes = S.binder.items.findIndex((x, i) => i > ziel && x && x.type === 'empty');
    if (naechstes >= 0) S.auswahl.add(naechstes);
  }
  speichern(); zeichneBinder(); zeichneErgebnisse();
  toastUndo(ziel === null ? t('hinzugefuegt_1') : t('tk_gesetzt').replace('{n}', ziel + 1));
}

// ---------- Vorlage: ein Pokémon – alle Karten ----------
function pokeInfo() {
  const v = ($('poke-name').value || '').trim().toLowerCase();
  const p = (S.pokedex || []).find((x) => x.name.toLowerCase() === v || (x.name_en || '').toLowerCase() === v);
  $('poke-info').textContent = p ? `#${String(p.dex).padStart(3, '0')} ${LANG === 'en' ? p.name_en : p.name}` : '';
  $('poke-info').dataset.dex = p ? p.dex : '';
}
async function pokeErstellen() { return mitKnopf('#modal-poke .btn:not(.sekundaer)', _pokeErstellen); }
async function _pokeErstellen() {
  await ladePokedex(); pokeInfo();
  const dex = Number($('poke-info').dataset.dex || 0);
  if (!dex) return toast(t('erst_waehlen'));
  const p = S.pokedex.find((x) => x.dex === dex);
  const fam = $('poke-umfang').value === 'familie';
  const q = (fam ? 'familie=' : 'dex=') + dex + '&sort=datum&limit=2000';
  const d = await api('api/cards/ids?' + q + '&region=intl');
  let ids = d.ids;
  if ($('poke-jp').checked) { try { ids = ids.concat((await api('api/cards/ids?' + q + '&region=jp')).ids); } catch (e) {} }
  if (!ids.length) return toast(t('keine_karten'));
  const name = (LANG === 'en' ? p.name_en : p.name) + ' – ' + (fam ? t('d_fam') : t('alle_karten'));
  if (!await binderSpeichernNeu(name, 'custom', ids.map((id) => ({ type: 'card', id })), { dex, familie: fam })) return;
  modalSchliessen(); toast(ids.length + ' ' + t('angelegt'));
}
async function familieErstellen(dex) {
  modalSchliessen(); await ladePokedex();
  $('poke-name').value = nm(S.pokedex.find((x) => x.dex === dex) || {}); $('poke-umfang').value = 'familie';
  modalOeffnen('modal-poke'); pokeInfo();
}
function illuInfo() {
  const v = ($('illu-name').value || '').trim().toLowerCase();
  const i = (S.meta.illustrators || []).find((x) => x.name.toLowerCase() === v);
  $('illu-info').textContent = i ? `${i.name} · ${i.anzahl} ${t('illu_karten')}` : '';
  $('illu-info').dataset.name = i ? i.name : '';
}
async function kuenstlerErstellen() { return mitKnopf('#modal-kuenstler .btn:not(.sekundaer)', _kuenstlerErstellen); }
async function _kuenstlerErstellen() {
  illuInfo();
  const name = $('illu-info').dataset.name;
  if (!name) return toast(t('erst_waehlen'));
  const d = await api('api/cards/ids?illustrator=' + encodeURIComponent(name) + '&sort=datum&limit=2000&region=intl');
  if (!d.ids.length) return toast(t('keine_karten'));
  if (!await binderSpeichernNeu(name, 'custom', d.ids.map((id) => ({ type: 'card', id })), { illustrator: name })) return;
  modalSchliessen(); toast(d.ids.length + ' ' + t('angelegt'));
}

// ---------- Import ----------
let importTreffer = [];
let importZielWert = 'binder';
function importOeffnen(ziel) {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  modalOeffnen('modal-import'); $('import-vorschau').innerHTML = ''; $('import-btn').disabled = true;
  importZiel(ziel || (S.user && !S.binder ? 'sammlung' : 'binder'));
}
function importZiel(z) {
  importZielWert = z === 'sammlung' ? 'sammlung' : 'binder';
  $('impz-binder').classList.toggle('on', importZielWert === 'binder');
  $('impz-sammlung').classList.toggle('on', importZielWert === 'sammlung');
  $('import-ziel-hin').textContent = importZielWert === 'sammlung' ? t('imp_ziel_sammlung_u') : t('imp_ziel_binder_u');
  $('import-btn').textContent = importZielWert === 'sammlung' ? t('imp_in_sammlung') : t('import_btn');
}
function importDatei(inp) {
  const f = inp.files && inp.files[0]; if (!f) return;
  const r = new FileReader(); r.onload = () => { $('import-text').value = r.result; importPruefen(); }; r.readAsText(f);
}
async function importPruefen() {
  const text = $('import-text').value; if (!text.trim()) return;
  $('import-stand').textContent = '…';
  try {
    const d = await api('api/import/parse', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
    importTreffer = d.treffer;
    $('import-stand').textContent = `${d.treffer.length} ${t('imp_erkannt')}${d.unklar.length ? ` · ${d.unklar.length} ${t('imp_unklar')}` : ''}${d.tabelle ? ' · ' + t('imp_tabelle') : ''}`;
    if (d.tabelle && S.user) importZiel('sammlung');
    // Vier Spalten statt einer Textwurst: Eingabe, Treffer, Set, Sicherheit.
    // „?“ heißt: erkannt, aber die Setnummer war nicht eindeutig.
    // Mit Kopfzeile (Collectr, TCG Collector) stehen Anzahl, Zustand und Sprache dabei.
    const mitPosten = d.tabelle || d.treffer.some((x) => (x.anzahl || 1) > 1);
    const zeile = (x, ok) => `<tr class="${ok ? (x.sicher === false ? 'unsicher' : '') : 'fehlt'}">
      <td class="imp-roh">${esc(ok ? x.zeile : x)}</td>
      <td>${ok ? '<strong>' + esc(x.name) + '</strong>' + (x.local_id ? ' · ' + esc(x.local_id) : '')
               : '<span style="color:var(--mut)">' + t('imp_kein_treffer') + '</span>'}</td>
      <td class="imp-set">${ok ? esc(x.set_name || '') : ''}</td>
      ${mitPosten ? `<td class="imp-set">${ok ? [(x.anzahl || 1) + '×', x.zustand, (x.sprache || '').toUpperCase(),
        x.variante && x.variante !== 'normal' ? x.variante : '', x.kaufpreis != null ? x.kaufpreis + ' €' : ''].filter(Boolean).join(' · ') : ''}</td>` : ''}
      <td class="imp-ok">${ok ? (x.sicher === false ? '?' : '✓') : '✕'}</td></tr>`;
    $('import-vorschau').innerHTML = `<table class="imp-tab">${d.treffer.map((x) => zeile(x, true)).join('')}
      ${d.unklar.map((z) => zeile(z, false)).join('')}</table>`
      + (d.abgeschnitten ? `<div class="imp-rest">${t('imp_gekuerzt').replace('{n}', d.abgeschnitten)}</div>` : '');
    $('import-btn').disabled = !d.treffer.length;
  } catch (e) { $('import-stand').textContent = t('fehler_laden'); }
}
async function importUebernehmen() {
  if (!importTreffer.length) return;
  if (importZielWert === 'sammlung') {
    if (!S.user) return loginOeffnen(t('sm_login'));
    try {
      const d = await api('api/sammlung/import', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ karten: importTreffer.map((x) => ({ id: x.id, anzahl: x.anzahl || 1, zustand: x.zustand || '',
                                                                   sprache: x.sprache || '', variante: x.variante || 'normal', kaufpreis: x.kaufpreis })) }) });
      modalSchliessen();
      await besitzLaden(); restkostenNeu(); zeichneBinder(); zeichneErgebnisse();
      if (!$('sammlung').classList.contains('hidden')) sammlungLaden();
      toast(t('imp_sammlung_ok').replace('{n}', d.aufgenommen));
    } catch (e) { if (!gate(e)) toast(e.message); }
    return;
  }
  if (!S.binder) return;
  merken('import');
  for (const x of importTreffer) S.binder.items.push({ type: 'card', id: x.id });
  if (!await binderAnlegenWennNoetig()) return;
  speichern(); zeichneBinder(); zeichneErgebnisse(); modalSchliessen();
  toastUndo(importTreffer.length + ' ' + t('hinzugefuegt'));
}

// ---------- Anzeigename ----------
/** Ohne Anzeigename fragt die Startseite einmal nach – vorher stand die E-Mail-Adresse in
 *  der Anrede und lief am Handy aus dem Bild. */
function startNameFrage() {
  let box = $('st-namefrage');
  const zeigen = S.user && !S.user.name;
  if (!zeigen) { if (box) box.remove(); return; }
  if (!box) {
    box = document.createElement('div');
    box.id = 'st-namefrage'; box.className = 'st-namefrage';
    box.innerHTML = `<span>${t('st_name_frage')}</span><input class="feld" id="st-name" maxlength="40" placeholder="${t('ph_rufname')}"><button class="btn sekundaer" onclick="startNameSpeichern()">${t('speichern')}</button>`;
    $('st-unter').after(box);
    $('st-name').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') startNameSpeichern(); });
  }
}
async function startNameSpeichern() {
  const v = ($('st-name') || {}).value || '';
  if (!v.trim()) return;
  $('pf-name').value = v.trim();
  await nameSpeichern();
  startOeffnen();
}
async function nameSpeichern() {
  try {
    const d = await api('api/auth/profil', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: $('pf-name').value }) });
    S.user = d.user; kontoAnzeigen(); toast(t('gespeichert'));
  } catch (e) { toast(e.message); }
}
function anzeigeName() { return S.user ? (S.user.name || S.user.email.split('@')[0]) : ''; }

// ---------- Mobil: Preise über das ⚙-Menü ----------


// ---------- Planer: Aktionen nur mit Auswahl ----------
/** Wie groß die Seiten im Planer stehen — gilt für alle auf einmal und bleibt gemerkt.
 *  Wer 40 Seiten sortiert, will sie klein; wer eine Seite feinlegt, will sie groß. */
function planerZoom(px) {
  document.documentElement.style.setProperty('--planer-w', px + 'px');
  try { localStorage.setItem('bp_planer_w', px); } catch (e) {}
}
function planerZoomInit() {
  let px = 0;
  try { px = parseInt(localStorage.getItem('bp_planer_w'), 10) || 0; } catch (e) {}
  if (!px) {
    // Ohne gemerkte Größe füllen die Seiten die Zeile: bei 1440 px standen vier Seiten
    // in Standardgröße links, rechts blieb ein Drittel des Bildschirms leer.
    const w = window.innerWidth - 36;
    const n = Math.max(2, Math.min(5, Math.round(w / 340)));
    px = Math.max(220, Math.min(620, Math.floor((w - 18 * (n - 1)) / n)));
  }
  document.documentElement.style.setProperty('--planer-w', px + 'px');
  const el = $('pl-zoom');
  if (el) el.value = px;
}

/** Die Auswahlleiste erscheint, sobald etwas gewählt ist — in beiden Zuständen des Binders.
 *  Vorher gab es sie nur im Planer, und der Hinweis „Shift für einen Bereich" stand als
 *  Dauertext über allem, auch wenn nichts gewählt war. */
function auswahlAktionen() {
  plBarSetzen();
  const n = S.auswahl.size;
  const bar = $('planer-bar');
  if (!bar) return;
  bar.classList.toggle('hidden', n === 0 || S.nurAnsicht);
  const zahl = $('planer-anzahl');
  if (zahl) zahl.textContent = n;
  const tipp = $('pl-shift-tipp');
  if (tipp) tipp.classList.toggle('hidden', n !== 1 || window.innerWidth < 900);
  // Der Hinzufügen-Knopf jeder Trefferkarte nennt sein Ziel — es hängt an der Auswahl.
  const txt = '＋ ' + zielText();
  document.querySelectorAll('#ergebnisse .hinzu').forEach((el) => { el.textContent = txt; });
  ladeZielZeichnen();
  inspektorZeichnen();
}
function planerAktionen() { auswahlAktionen(); }


