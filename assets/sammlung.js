/* Sammlung — was du wirklich besitzt, und was daraus wird.
 *
 * Eigene Datei wie markt.js: index.html trägt nur noch das Gerüst (HTML, CSS, Übersetzungen),
 * die Logik liegt hier und läuft per `defer` nach dem Hauptskript. Alles, was von außen
 * gerufen wird (besitzt, sammlungAufnehmen, wunschToggle, ansichtSammlung …), bleibt global.
 *
 * Eine Wahrheit, zwei Blicke: der Binder ist der Plan, die Sammlung der Bestand. Der Haken
 * im Fach schreibt in die Sammlung und liest von dort — es gibt kein zweites Häkchen.
 *
 * Seit September 2026 ist die Sammlung um das Set organisiert, in dem Sammler denken
 * („Grundset 34 von 102"), nicht um die Einzelkarte: vier Reiter (Karten · Sets ·
 * Wunschliste · Auswertung), Bewegung gegen den Cardmarket-Schnitt, Ziele mit Datum.
 */

const SM = { bereich: 'karten', filter: '', karten: [], zahlen: null, kopf: null, laeuft: false,
             posten: null, postenAlt: {}, umgekehrt: false, setSort: 'fortschritt',
             seite: null, seitenDaten: null, anTage: 90, kauf: null, ziele: null };
const SM_BEREICHE = ['karten', 'sets', 'wunsch', 'auswertung'];
S.besitz = {};          // card_id → Anzahl, für die Fachanzeige im Binder

async function besitzLaden() {
  if (!S.user) { S.besitz = {}; return; }
  try {
    const d = await api('api/sammlung/besitz');
    S.besitz = d.besitz || {};
    S.wunsch = new Set(d.wants || []);
  } catch (e) { S.besitz = {}; S.wunsch = new Set(); }
}

/** Gehört mir diese Karte? Ohne Konto zählt weiterhin das Häkchen im Binder. */
function besitzt(item) {
  if (!item || item.type !== 'card') return false;
  return S.user ? !!S.besitz[item.id] : !!item.have;
}

/* ------------------------------------------------------------------ Öffnen & Reiter */

function ansichtSammlung(bereich) {
  if (!S.user) return loginOeffnen(t('sm_login'));
  if (bereich && SM_BEREICHE.includes(bereich)) SM.bereich = bereich;
  else if (!bereich) {
    try { SM.bereich = localStorage.getItem('bp_sm') || 'karten'; } catch (e) { SM.bereich = 'karten'; }
    if (!SM_BEREICHE.includes(SM.bereich)) SM.bereich = 'karten';
  }
  hashSetzen(SM.bereich === 'auswertung' ? 'auswertung' : 'sammlung');
  mnavMarkieren('mnav-sammlung');
  planerSchliessen(); vitrineSchliessen(); profilseiteSchliessen();
  if (typeof marktSchliessen === 'function') marktSchliessen();
  const war = !$('sammlung').classList.contains('hidden');
  $('sammlung').classList.remove('hidden');
  if (!war) ebeneOeffnen(sammlungSchliessen);
  reiterSetzen('seg-sammlung');
  SM.seite = null;
  zeichneSammlungReiter();
  sammlungLaden();
}
function sammlungSchliessen() {
  if ($('sammlung').classList.contains('hidden')) return;
  ebeneAufraeumen(sammlungSchliessen);
  $('sammlung').classList.add('hidden');
  routeZurueck();
  if ($('startseite').classList.contains('hidden')) reiterSetzen('seg-suche');
}
/** Die Auswertung war eine eigene Seite mit eigenem Rückweg. Sie ist ein Reiter. */
function auswertungOeffnen() { ansichtSammlung('auswertung'); }
function auswertungSchliessen() { /* kein eigener Ort mehr — bleibt für alte Aufrufer */ }

function sammlungBereich(b) {
  if (!SM_BEREICHE.includes(b)) b = 'karten';
  SM.bereich = b; SM.seite = null;
  try { localStorage.setItem('bp_sm', b); } catch (e) {}
  hashSetzen(b === 'auswertung' ? 'auswertung' : 'sammlung');
  zeichneSammlungReiter();
  sammlungLaden();
}

function zeichneSammlungReiter() {
  smAuswahlenFuellen();
  const z = SM.zahlen || {};
  for (const b of SM_BEREICHE) {
    const el = $('smb-' + b); if (el) el.classList.toggle('on', SM.bereich === b && !SM.seite);
  }
  const w = $('smb-wunsch');
  if (w) w.innerHTML = `${t('sm_fehlt')}${z.fehlt != null ? ` <strong>${z.fehlt}</strong>` : ''}`;
  const kartenTeil = $('sm-karten-teil');
  kartenTeil.classList.toggle('hidden', !!SM.seite || !['karten', 'wunsch'].includes(SM.bereich));
  $('sm-band').classList.toggle('hidden', !!SM.seite);
  // Suche, Sortierung und Filter gelten nur für den Bestand — die Wunschliste ist eine feste Liste.
  const wunsch = SM.bereich === 'wunsch';
  $('sm-suche').classList.toggle('hidden', wunsch);
  $('sm-sortwahl').classList.toggle('hidden', wunsch);
  $('sm-filter').innerHTML = wunsch ? '' : [
    ['', t('sm_alle'), z.verschiedene],
    ['doppelt', t('sm_doppelt'), z.doppelte],
    ['ohne_binder', t('sm_ohne_binder'), z.ohne_binder],
  ].map(([f, l, n]) => `<button class="chip ${SM.filter === f ? 'on' : ''}" onclick="sammlungFilter('${f}')">${l}${n != null ? ` <strong>${n}</strong>` : ''}</button>`).join('');
  smRichtungZeichnen();
}
function sammlungFilter(f) { SM.filter = f; zeichneSammlungReiter(); sammlungLaden(); }

/** Die Auswahllisten des Postendialogs einmal befüllen. */
function smAuswahlenFuellen() {
  const v = $('smp-variante'); if (!v || v.options.length) return;
  v.innerHTML = ['normal', 'reverse', 'holo', 'first', 'pokeball', 'masterball']
    .map((x) => `<option value="${x}">${t('v_' + x) || x}</option>`).join('');
  $('smp-zustand').innerHTML = SM_ZUSTAENDE
    .map((x) => `<option value="${x}">${x || t('sm_ohne_angabe')}</option>`).join('');
  $('smp-sprache').innerHTML = SM_SPRACHEN
    .map((x) => `<option value="${x}">${x ? x.toUpperCase() : t('sm_ohne_angabe')}</option>`).join('');
}

/** Reihenfolge umdrehen. Die natürliche Richtung hängt an der Sortierung: „Zuletzt
 *  hinzugefügt" und „Wert" beginnen oben mit dem Größten, „Name" mit A. */
function smRichtungZeichnen() {
  const b = $('sm-richtung'); if (!b) return;
  const sort = $('sm-sort').value;
  const natuerlichAuf = sort === 'name';
  const richtung = (natuerlichAuf !== SM.umgekehrt) ? 'asc' : 'desc';
  b.innerHTML = `<span class="pf">${richtung === 'desc' ? '↓' : '↑'}</span>${esc(richtungText(sort, richtung))}`;
  b.title = t('ri_t');
}
function smRichtungWechseln() { SM.umgekehrt = !SM.umgekehrt; smRichtungZeichnen(); sammlungLaden(); }

let smTimer = null;
function sammlungSuchen() { clearTimeout(smTimer); smTimer = setTimeout(sammlungLaden, 350); }

/* ------------------------------------------------------------------------ Laden */

async function sammlungLaden() {
  if (SM.laeuft) return;
  SM.laeuft = true;
  const rumpf = $('sm-rumpf');
  try {
    if (SM.seite) { await smSeiteLaden(); return; }
    // Kopfzahlen und Kennzahlen parallel — die Kacheln stehen auf jedem Reiter.
    const [kopf, zahlen] = await Promise.all([api('api/sammlung/kopf'), api('api/sammlung/uebersicht')]);
    SM.kopf = kopf; SM.zahlen = zahlen;
    zeichneSammlungReiter();
    zeichneSammlungKopf();
    if (SM.bereich === 'karten') {
      rumpf.innerHTML = '';
      $('sm-gitter').innerHTML = `<div class="sm-leer">${lader('gross', t('laedt'))}</div>`;
      const q = encodeURIComponent($('sm-suche').value.trim());
      const d = await api(`api/sammlung?nur=${SM.filter}&q=${q}&sortierung=${$('sm-sort').value}&umgekehrt=${SM.umgekehrt ? 1 : 0}&limit=120`);
      SM.karten = d.karten || [];
      zeichneSammlung();
    } else if (SM.bereich === 'wunsch') {
      $('sm-gitter').innerHTML = `<div class="sm-leer">${lader('gross', t('laedt'))}</div>`;
      const [d, g] = await Promise.all([api('api/wants'), api('api/sammlung/guenstig')]);
      SM.wunsch = d;
      S.wunsch = new Set(d.einzeln || []);
      const einzeln = new Set(d.einzeln || []);
      SM.karten = (d.karten || []).map((k) => ({ ...k, anzahl: 0, fehlt: true, wunsch_einzeln: einzeln.has(k.id) }));
      rumpf.innerHTML = zeichneGuenstig(g) + zeichneWunschHinweis();
      zeichneSammlung();
    } else if (SM.bereich === 'sets') {
      rumpf.innerHTML = `<div style="padding:40px;text-align:center">${lader('gross', t('laedt'))}</div>`;
      const d = await api('api/sammlung/sets?sortier=' + SM.setSort);
      zeichneSammlungSets(d.sets || []);
    } else if (SM.bereich === 'auswertung') {
      rumpf.innerHTML = `<div style="padding:40px;text-align:center">${lader('gross', t('laedt'))}</div>`;
      AN.daten = await api('api/analytics/sammlung?tage=' + SM.anTage);
      zeichneAuswertung();
    }
  } catch (e) {
    if (!gate(e)) rumpf.innerHTML = anDuenn(esc(e.message || t('an_fehler')));
  } finally { SM.laeuft = false; }
}

/* -------------------------------------------------------------------- Kopfkacheln */

/** Vier Zahlen, die auf jedem Reiter stehen: Wert und Bewegung, Einsatz und Gewinn,
 *  Sets, Rest zu den Zielen. Vorher war es eine Zeile ohne Bewegung und ohne Ziel. */
function zeichneSammlungKopf() {
  const k = SM.kopf || {}, band = $('sm-band');
  if (k.leer) { band.innerHTML = ''; band.classList.add('hidden'); return; }
  band.classList.toggle('hidden', !!SM.seite);
  const gewinnText = k.einsatz != null
    ? `${k.gewinn >= 0 ? '+' : ''}${anEur(k.gewinn, 0)} · ${anProz(k.gewinn_proz)}` : t('an_kein_kaufpreis');
  band.innerHTML = mkKachel({ lbl: t('sm_k_wert'), zahl: anEur(k.wert, 0), delta: k.bew7,
                              unter: k.bew7_eur != null ? `${k.bew7_eur >= 0 ? '+' : ''}${anEur(k.bew7_eur, 0)} · ${t('mk_7t')}` : t('mk_7t') })
    + mkKachel({ lbl: t('an_einsatz'), zahl: k.einsatz != null ? anEur(k.einsatz, 0) : '—',
                 delta: k.einsatz != null ? k.gewinn_proz : undefined, unter: gewinnText,
                 klick: `sammlungBereich('auswertung')` })
    + mkKachel({ lbl: t('sm_k_sets'), zahl: anZahl(k.sets),
                 unter: t('sm_k_sets_u').replace('{f}', k.sets_fast).replace('{k}', k.sets_komplett),
                 klick: `sammlungBereich('sets')` })
    + mkKachel({ lbl: t('sm_k_ziel'), zahl: k.ziele ? anEur(k.ziel_rest, 0) : '—',
                 unter: k.ziele ? t('sm_k_ziel_u').replace('{n}', k.ziele).replace('{k}', anZahl(k.ziel_fehlt)) : t('sm_k_ziel_leer'),
                 klick: `sammlungBereich('sets')` });
}

/** Kachel wie im Markt: Zahl, Bewegung, Zeile darunter. Fällt auf eine schlichte Form
 *  zurück, falls markt.js einmal nicht geladen ist. */
function mkKachelSm(o) { return mkKachel(o); }

/* ------------------------------------------------------------------------ Karten */

function zeichneSammlung() {
  if (!SM.karten.length) {
    const hinweise = { '': t('sm_leer'), doppelt: t('sm_leer_doppelt'), ohne_binder: t('sm_leer_ohne') };
    const text = SM.bereich === 'wunsch' ? t('wl_leer') : (hinweise[SM.filter] || t('sm_leer'));
    // Eine graue Zeile war zu wenig: es gibt drei Wege, die Sammlung zu füllen, und
    // keiner davon stand hier.
    const wege = (SM.bereich === 'wunsch' || SM.filter) ? '' : `<div style="display:flex;gap:10px;flex-wrap:wrap;justify-content:center;margin-top:14px">
        <button class="btn" onclick="sammlungSchliessen();fotoOeffnen()">${t('tpl_foto')}</button>
        <button class="btn sekundaer" onclick="sammlungSchliessen();ansicht('suche')">${t('sm_weg_suche')}</button>
        <button class="btn sekundaer" onclick="sammlungSchliessen();importOeffnen()">${t('tpl_import')}</button>
      </div>`;
    $('sm-gitter').innerHTML = `<div class="sm-leer">${text}${wege}</div>`;
    return;
  }
  $('sm-gitter').innerHTML = SM.karten.map((k) => `
    <div class="sm-karte" onclick="detailOeffnenId('${esc(k.id)}')">
      ${k.anzahl > 1 ? `<span class="sm-anz">${k.anzahl}×</span>` : ''}
      ${k.fehlt
        ? (k.wunsch_einzeln ? `<button class="sm-weg" onclick="event.stopPropagation();wunschToggle('${esc(k.id)}');setTimeout(sammlungLaden,300)" title="${t('wl_weg')}">✕</button>` : '')
        : `<button class="sm-weg" onclick="event.stopPropagation();sammlungWeg('${esc(k.id)}')" title="${t('sm_entfernen')}">✕</button>`}
      <img loading="lazy" src="${imgUrl(k.id)}" alt="">
      <div class="sm-txt">
        <div class="sm-name">${esc(k.name || k.id)}</div>
        <div class="sm-meta">${esc(k.set_name || '')} · ${esc(k.local_id || '')}</div>
        ${(k.posten || []).length ? `<div class="sm-posten">${k.posten.map((p, pi) =>
          `<button class="sm-pchip" onclick="event.stopPropagation();smPostenAus('${esc(k.id)}',${pi})"
                   title="${t('sm_posten_aendern')}">${p.anzahl}× ${smPostenLabel(p)}${k.posten.length > 1 && p.stueckwert != null
                     ? ` <b>${p.stueckwert.toFixed(2).replace('.', ',')} €</b>` : ''}</button>`).join('')}
          <button class="sm-pchip neu" onclick="event.stopPropagation();smPostenOeffnen('${esc(k.id)}')"
                  title="${t('sm_posten_neu')}">＋</button></div>` : ''}
        ${k.eur != null ? `<div class="sm-wert">${(k.wert != null ? k.wert : k.eur).toFixed(2).replace('.', ',')} €${
          k.preis_quelle ? `<span class="sm-quelle" title="${esc(t('pr_q_' + k.preis_quelle))}">≈</span>` : ''}${
          k.bew30 != null ? ` <span class="sm-bew ${k.bew30 > 0.05 ? 'plus' : k.bew30 < -0.05 ? 'minus' : ''}" title="${t('mk_s_bew30')}">${anProz(k.bew30)}</span>` : ''}</div>` : ''}
      </div>
    </div>`).join('');
}

/* ------------------------------------------------------------------------- Sets */

const SM_SET_SORTEN = [['fortschritt', 'sm_s_fortschritt'], ['rest', 'sm_s_rest'], ['wert', 'sm_s_wert'],
                       ['bewegung', 'mk_s_bew30'], ['jahr', 'mk_jahr'], ['name', 'sm_s_name']];
function smSetSort(s) { SM.setSort = s; sammlungLaden(); }

function smFortschritt(prozent, klein) {
  return `<span class="sm-prog${klein ? ' klein' : ''}"><i style="width:${Math.min(100, prozent || 0)}%"></i></span>`;
}

function zeichneSammlungSets(sets) {
  const r = $('sm-rumpf');
  if (!sets.length) { r.innerHTML = anDuenn(t('sm_leer')); return; }
  r.innerHTML = `<div class="an-tafel">
    <h3>${t('sm2_sets_t')}</h3><div class="unter">${t('sm2_sets_u')}</div>
    <div class="an-chips">${SM_SET_SORTEN.map(([k, l]) =>
      `<button class="chip ${SM.setSort === k ? 'on' : ''}" onclick="smSetSort('${k}')">${t(l)}</button>`).join('')}</div>
    <div class="set-kopf"><span>${t('an_set')}</span><span>${t('sm_s_fortschritt')}</span><span class="r"></span>
      <span class="r">${t('sm_besessen')}</span><span class="r">${t('sm_s_rest')}</span><span class="r">${t('mk_30t')}</span></div>
    ${sets.map((s) => `<div class="set-zeile" role="button" tabindex="0" onclick="smSetOeffnen('${esc(s.set_id)}')">
      <span class="nm"><b>${esc(s.name)}</b><small>${esc(s.jahr || '')}${s.jahr ? ' · ' : ''}${anZahl(s.gesamt)} ${t('an_karten')}${
        s.ziel ? ` · <span class="sm-zielmarke">🎯 ${anTag(s.ziel)}</span>` : ''}</small></span>
      ${smFortschritt(s.prozent)}
      <span class="pz">${s.prozent} %</span>
      <span class="n">${anZahl(s.besessen)} / ${anZahl(s.gesamt)}</span>
      <span class="rest">${s.fehlt ? anEur(s.rest, 0) : `<span class="sm-komplett">✓</span>`}</span>
      ${mkDelta(s.bew30)}
    </div>`).join('')}
    <div class="mk-fuss">${t('sm2_sets_fuss')}</div></div>`;
}

/* --------------------------------------------------------------- Set-Seite (Häkchen) */

function smSetOeffnen(id) { SM.seite = { set_id: id }; zeichneSammlungReiter(); sammlungLaden(); }
function smSeiteZu() { SM.seite = null; SM.seitenDaten = null; zeichneSammlungReiter(); sammlungLaden(); }

async function smSeiteLaden() {
  const r = $('sm-rumpf');
  r.innerHTML = `<div style="padding:40px;text-align:center">${lader('gross', t('laedt'))}</div>`;
  SM.seitenDaten = await api('api/sammlung/set/' + encodeURIComponent(SM.seite.set_id));
  zeichneSmSetSeite();
}

function zeichneSmSetSeite() {
  const d = SM.seitenDaten, s = d.set || {}, st = d.stand || {};
  const prozent = d.gesamt ? Math.round(d.besessen / d.gesamt * 100) : 0;
  const ziel = d.ziel;
  const zielKachel = ziel
    ? `<div class="mk-kachel"><small>${t('sm_ziel')}</small><b>${anTag(ziel.datum) || '—'}</b>
        <span class="mk-neben">${ziel.datum ? t('sm_ziel_tage').replace('{n}', smTageBis(ziel.datum)) : t('sm_ziel_ohne_datum')}</span>
        <button class="mk-link" onclick="smZielLoeschen('${esc(s.id)}')">${t('sm_ziel_weg')}</button></div>`
    : `<div class="mk-kachel" id="sm-zielkachel"><small>${t('sm_ziel')}</small><b>—</b>
        <span class="mk-neben">${t('sm_ziel_u')}</span>
        <button class="mk-link" onclick="smZielFormular('${esc(s.id)}')">${t('sm_ziel_setzen')}</button></div>`;
  const teuer = (d.teuerste_fehlend || []);
  $('sm-rumpf').innerHTML = `
    <div class="mk-kopfzeile"><button class="btn sekundaer mk-zurueck" onclick="smSeiteZu()">‹ ${t('sm2_sets')}</button>
      <h1>${esc(s.name || s.id)}</h1>
      <span class="mk-stand">${esc(s.serie_name || '')}${s.release_date ? ' · ' + s.release_date.slice(0, 4) : ''} · ${anZahl(d.gesamt)} ${t('an_karten')}</span></div>
    <div class="mk-band">
      <div class="mk-kachel"><small>${t('sm_s_fortschritt')}</small><b>${anZahl(d.besessen)} / ${anZahl(d.gesamt)}</b>
        ${smFortschritt(prozent)}<span class="mk-neben">${prozent} % · ${t('sm_fehlen_n').replace('{n}', anZahl(d.gesamt - d.besessen))}</span></div>
      ${mkKachel({ lbl: t('sm_s_rest'), zahl: d.gesamt - d.besessen ? anEur(d.rest, 0) : '✓',
                   unter: d.rest_ohne_preis ? t('rk_ohne').replace('{n}', d.rest_ohne_preis) : t('sm_rest_u') })}
      ${mkKachel({ lbl: t('sm_dein_wert'), zahl: anEur(st.wert || 0, 0), delta: st.rendite,
                   unter: st.einsatz != null ? `${t('an_einsatz')} ${anEur(st.einsatz, 0)}` : t('an_kein_kaufpreis') })}
      ${zielKachel}
    </div>
    ${teuer.length ? `<div class="an-tafel"><h3>${t('sm_teuer_fehlend')}</h3><div class="unter">${t('sm_teuer_fehlend_u')}</div>
      <div class="an-karten">${teuer.map((k) => `<button class="an-karte" onclick="detailOeffnenId('${esc(k.id)}')">
        <img loading="lazy" src="${imgUrl(k.id)}" alt="" onerror="this.style.visibility='hidden'">
        <div class="p">${anEur(k.eur, 0)}</div><div class="n" title="${esc(k.name)}">${esc(k.local_id)} · ${esc(k.name)}</div></button>`).join('')}</div></div>` : ''}
    <div class="an-tafel"><h3>${t('sm_raster')}</h3><div class="unter">${t('sm_raster_u')}</div>
      <div class="hk-gitter">${(d.karten || []).map((k) => `
        <button class="hk-karte ${k.anzahl ? 'hab' : ''}" id="hk-${esc(k.id)}" onclick="smHaken('${esc(k.id)}')" title="${esc(k.name)}">
          <img loading="lazy" src="${imgUrl(k.id)}" alt="">
          <span class="hk-nr">${esc(k.local_id || '')}</span>
          <span class="hk-hab">✓${k.anzahl > 1 ? k.anzahl : ''}</span>
          <span class="hk-preis">${k.eur != null ? anEur(k.eur, k.eur >= 100 ? 0 : 2) : '–'}</span>
        </button>`).join('')}</div></div>`;
}

function smTageBis(iso) {
  try { return Math.round((new Date(iso) - new Date(new Date().toISOString().slice(0, 10))) / 86400000); } catch (e) { return '?'; }
}

/** Ein Klick auf eine Karte im Raster: gehört mir / gehört mir nicht. Derselbe Weg wie
 *  der Haken im Binder — auf dem unbestimmten Posten, bestimmte Posten bleiben. */
async function smHaken(id) {
  const el = $('hk-' + id);
  try {
    const d = await api('api/sammlung/toggle', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                 body: JSON.stringify({ card_id: id, variante: 'normal' }) });
    const drin = d.drin != null ? d.drin : !(el && el.classList.contains('hab'));
    if (drin) S.besitz[id] = d.anzahl || 1; else delete S.besitz[id];
    if (el) {
      el.classList.toggle('hab', drin);
      el.querySelector('.hk-hab').textContent = '✓' + ((d.anzahl || 0) > 1 ? d.anzahl : '');
    }
    const k = (SM.seitenDaten.karten || []).find((x) => x.id === id);
    if (k) k.anzahl = drin ? (d.anzahl || 1) : 0;
    smSetKopfNeu();
    restkostenNeu(); zeichneBinder(); zeichneErgebnisse();
  } catch (e) { if (!gate(e)) toast(e.message); }
}
/** Nach einem Haken die Kopfzahlen der Set-Seite nachrechnen, ohne alles neu zu laden. */
function smSetKopfNeu() {
  const d = SM.seitenDaten; if (!d) return;
  const fehlend = (d.karten || []).filter((k) => !k.anzahl);
  d.besessen = d.gesamt - fehlend.length;
  d.rest = Math.round(fehlend.reduce((a, k) => a + (k.eur || 0), 0) * 100) / 100;
  d.teuerste_fehlend = fehlend.filter((k) => k.eur).sort((a, b) => b.eur - a.eur).slice(0, 6);
  // Nur die Kacheln neu, das Raster behält seinen Zustand (und die Bildlaufposition).
  const rumpf = $('sm-rumpf'), oben = rumpf.scrollTop;
  const raster = rumpf.querySelector('.hk-gitter');
  const rasterHtml = raster ? raster.outerHTML : '';
  zeichneSmSetSeite();
  const neu = rumpf.querySelector('.hk-gitter');
  if (neu && rasterHtml) neu.outerHTML = rasterHtml;
  rumpf.scrollTop = oben;
}

/* ------------------------------------------------------------------------- Ziele */

function smZielFormular(setId) {
  const k = $('sm-zielkachel'); if (!k) return;
  k.innerHTML = `<small>${t('sm_ziel')}</small>
    <input class="feld" id="sm-zieldatum" type="text" inputmode="numeric" autocomplete="off" maxlength="10"
           placeholder="TT.MM.JJJJ" oninput="gebTippen(this)" style="margin:4px 0">
    <div style="display:flex;gap:6px"><button class="btn" style="font-size:12.5px;padding:6px 12px" onclick="smZielSpeichern('${esc(setId)}')">${t('speichern')}</button>
      <button class="btn sekundaer" style="font-size:12.5px;padding:6px 12px" onclick="zeichneSmSetSeite()">${t('abbrechen')}</button></div>`;
  $('sm-zieldatum').focus();
}
async function smZielSpeichern(setId) {
  const roh = ($('sm-zieldatum') || {}).value || '';
  const iso = roh.trim() ? gebIso(roh) : null;
  if (roh.trim() && !iso) return toast(t('geb_ungueltig') || 'TT.MM.JJJJ');
  try {
    await api('api/sammlung/ziele', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                      body: JSON.stringify({ set_id: setId, ziel_datum: iso }) });
    toast(t('sm_ziel_gesetzt'));
    SM.ziele = null;
    await smSeiteLaden();
  } catch (e) { if (!gate(e)) toast(e.message); }
}
async function smZielLoeschen(setId) {
  try {
    await api('api/sammlung/ziele', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                      body: JSON.stringify({ set_id: setId, loeschen: true }) });
    SM.ziele = null;
    await smSeiteLaden();
  } catch (e) { toast(e.message); }
}

/** Die Ziele auf der Startseite: Set, Fortschrittsbalken, Rest, Tage. Der Grund, jede
 *  Woche wiederzukommen. Wird von startLaden() gerufen. */
async function zieleStartLaden() {
  const block = $('st-ziele-block'); if (!block || !S.user) return;
  let d;
  try { d = await api('api/sammlung/ziele'); } catch (e) { block.hidden = true; return; }
  const ziele = d.ziele || [];
  block.hidden = !ziele.length;
  if (!ziele.length) return;
  $('st-ziele').innerHTML = ziele.map((z) => `<button class="st-ziel" onclick="startSchliessen();ansichtSammlung('sets');smSetOeffnen('${esc(z.set_id)}')">
      <div class="zk"><b>${esc(z.name)}</b><span>${anZahl(z.besessen)} / ${anZahl(z.gesamt)}${
        z.ziel_datum ? ` · ${z.tage >= 0 ? t('sm_ziel_tage').replace('{n}', z.tage) : t('sm_ziel_vorbei')}` : ''}</span></div>
      ${smFortschritt(z.prozent)}
      <div class="zu"><span>${z.prozent} %</span><span>${z.fehlt ? t('sm_rest_kurz').replace('{e}', anEur(z.rest, 0)) : '✓ ' + t('sm_komplett')}</span></div>
    </button>`).join('');
}

/* ---------------------------------------------------------------- Wunschliste */

function zeichneGuenstig(g) {
  const liste = (g && g.karten) || [];
  if (!liste.length) return '';
  return `<div class="an-tafel"><h3>${t('sm_guenstig')}</h3><div class="unter">${t('sm_guenstig_u')}</div>
    <div class="an-tab-rahmen"><table class="an-tab" style="min-width:0">
      <thead><tr><th></th><th>${t('an_karte')}</th><th class="r">${t('mk_schnitt_jetzt')}</th><th class="r">${t('mk_30t')}</th></tr></thead>
      <tbody>${liste.map((k) => `<tr onclick="detailOeffnenId('${esc(k.id)}')" style="cursor:pointer">
        <td><img loading="lazy" src="${imgUrl(k.id)}" alt="" onerror="this.style.visibility='hidden'"></td>
        <td><div class="nm">${esc(k.name)}</div><div class="set">${esc(k.set_name || '')}${k.local_id ? ' · ' + esc(k.local_id) : ''}</div></td>
        <td class="r">${anEur(k.avg30)} → <strong>${anEur(k.eur)}</strong></td>
        <td class="r">${mkDelta(k.prozent)}</td></tr>`).join('')}</tbody></table></div></div>`;
}

/** Unter der Wunschliste steht, woraus sie besteht — und dort stellt man ganze Binder
 *  dazu. Ohne diese Zeile sucht man den Schalter im Binder-Menü und findet ihn nicht. */
function zeichneWunschHinweis() {
  const w = SM.wunsch || {};
  return `<div class="sm-hinweis"><div style="margin-bottom:5px">${t('wl_binder')} — <span style="opacity:.85">${t('wl_binder_u')}</span></div>
    <div class="f-chips">${(w.binder || []).map((b) =>
      `<button class="chip ${b.an ? 'on' : ''}" onclick="wunschBinder('${esc(b.id)}', ${b.an ? 0 : 1})"
        title="${esc(b.name)} · ${b.karten}">${b.an ? '★ ' : '＋ '}${esc(b.name)}</button>`).join('')
      || `<span style="opacity:.7">–</span>`}</div>
    <div style="margin-top:6px">${t('wl_einzeln').replace('{n}', (w.einzeln || []).length)}${
      w.summe ? ' · ' + t('wl_summe').replace('{s}', anEur(w.summe, 0)) : ''}</div></div>`;
}

/** Einen ganzen Binder auf die Wunschliste stellen oder herunternehmen. */
async function wunschBinder(id, an) {
  try {
    await api('api/wants/binder', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                    body: JSON.stringify({ binder_id: id, an: !!an }) });
    if (S.binder && S.binder.id === id) {
      S.binder.options = S.binder.options || {};
      S.binder.options.wants = !!an;
      wantsSchalterSetzen();
    }
    sammlungLaden();
  } catch (e) { toast(e.message); }
}

function wunschHat(id) { return !!(S.wunsch && S.wunsch.has(id)); }

async function wunschToggle(id, imDetail) {
  if (!S.user) return loginOeffnen(t('gate_login'));
  const an = !wunschHat(id);
  S.wunsch = S.wunsch || new Set();
  an ? S.wunsch.add(id) : S.wunsch.delete(id);
  if (imDetail) {
    const b = $('detail-wunsch');
    if (b) b.innerHTML = an ? '★ ' + t('wl_drauf') : '☆ ' + t('wl_titel');
  } else zeichneErgebnisse();
  try {
    await api('api/wants', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                             body: JSON.stringify({ card_id: id, an }) });
    toast(an ? t('wl_dazu') : t('wl_weg'));
  } catch (e) {
    an ? S.wunsch.delete(id) : S.wunsch.add(id);
    toast(e.message);
  }
}

/* ------------------------------------------------------------------- Export */

function smExportMenu() { menuToggle('menu-sm-export'); }

async function sammlungExport(format) {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  if (!S.user) return loginOeffnen(t('gate_pro'));
  try {
    const r = await fetch('api/sammlung/export?format=' + format, { headers: { Authorization: 'Bearer ' + S.token } });
    if (!r.ok) {
      let code = null;
      try { code = ((await r.json()).detail || {}).code; } catch (e) {}
      const err = new Error('Export'); err.code = code;
      if (!gate(err)) toast(t('an_fehler'));
      return;
    }
    const a = document.createElement('a');
    a.href = URL.createObjectURL(await r.blob());
    a.download = 'sammlung-' + new Date().toISOString().slice(0, 10) + '.' + format;
    a.click();
  } catch (e) { toast(t('an_fehler')); }
}

/* ---------------------------------------------------------------------- Posten */
/* Zustand und Sprache gehören zum einzelnen Exemplar, nicht zur Karte: dieselbe Karte
   einmal auf Deutsch in NM und einmal auf Englisch in GD sind zwei Posten. Genauso lösen
   es Collectr, die TCGplayer-Sammlung und der Dragon-Shield-Manager. */

const SM_ZUSTAENDE = ['', 'M', 'NM', 'EX', 'GD', 'LP', 'PL', 'PO'];
const SM_SPRACHEN = ['', 'de', 'en', 'fr', 'it', 'es', 'pt', 'jp', 'kr', 'cn', 'ru'];

/** Karte aus der Suche aufnehmen — ohne Umweg über einen Binder. Danach fragt ein Toast
 *  nach dem Kaufpreis: das ist der Moment, in dem man ihn noch weiß. */
async function sammlungAufnehmen(id, opt) {
  if (!S.user) return loginOeffnen(t('sm_login'));
  opt = opt || {};
  try {
    const d = await api('api/sammlung/aufnehmen', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ card_id: id, variante: opt.variante || 'normal',
                             zustand: opt.zustand || '', sprache: opt.sprache || '',
                             anzahl: opt.anzahl || 1 }),
    });
    S.besitz[id] = d.anzahl;
    restkostenNeu();
    zeichneErgebnisse(); zeichneBinder();
    // Steht der Kartendialog offen, gehört die neue Zahl auch auf seinen Knopf.
    const hab = $('detail-hab');
    if (hab && detailKarte && detailKarte.id === id) {
      hab.innerHTML = '✓ ' + d.anzahl + '× <span class="txt">' + t('tk_besitz') + '</span>';
    }
    toastKaufpreis(id, opt, d.anzahl);
  } catch (e) { toast(e.message); }
}

let toastKaufTimer = null;
/** „Aufgenommen · Gekauft für … €" — ein Preisfeld im Toast statt eines Dialogs, der
 *  vorher hinter einem kleinen Chip lag. Wer nichts eintippt, verliert nichts. */
function toastKaufpreis(id, opt, anzahl) {
  const el = $('toast-kauf'); if (!el) return toast(t('sm_aufgenommen').replace('{n}', anzahl));
  const normal = $('toast'); if (normal) normal.classList.remove('zeig');
  SM.kauf = { id, opt: opt || {} };
  $('toast-kauf-text').textContent = t('sm_aufgenommen').replace('{n}', anzahl) + ' · ' + t('sm_kauf_frage');
  const inp = $('toast-kauf-preis'); inp.value = '';
  el.classList.add('zeig');
  clearTimeout(toastKaufTimer);
  toastKaufTimer = setTimeout(() => el.classList.remove('zeig'), 9000);
  inp.focus();
}
async function toastKaufSpeichern() {
  const k = SM.kauf, el = $('toast-kauf');
  const roh = ($('toast-kauf-preis').value || '').trim().replace(',', '.');
  el.classList.remove('zeig'); clearTimeout(toastKaufTimer);
  if (!k || !roh || isNaN(parseFloat(roh))) return;
  try {
    await api('api/sammlung/kaufpreis', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ card_id: k.id, variante: k.opt.variante || 'normal', zustand: k.opt.zustand || '',
                             sprache: k.opt.sprache || '', kaufpreis: roh }) });
    toast(t('sm_kauf_gemerkt').replace('{e}', anEur(parseFloat(roh))));
    if (!$('sammlung').classList.contains('hidden')) sammlungLaden();
  } catch (e) { toast(e.message); }
}
function toastKaufTaste(ev) { if (ev.key === 'Enter') toastKaufSpeichern(); if (ev.key === 'Escape') $('toast-kauf').classList.remove('zeig'); }

/** Der Dialog für einen einzelnen Posten. `posten` leer heißt: neu anlegen. */
function smPostenOeffnen(cardId, posten) {
  if (!S.user) return loginOeffnen(t('sm_login'));
  SM.posten = { card_id: cardId, ...(posten || {}) };
  SM.postenAlt = { zustand: (posten || {}).zustand || '', sprache: (posten || {}).sprache || '' };
  const p = SM.posten;
  $('smp-titel').textContent = posten ? t('sm_posten_aendern') : t('sm_posten_neu');
  $('smp-variante').value = p.variante || 'normal';
  $('smp-zustand').value = p.zustand || '';
  $('smp-sprache').value = p.sprache || '';
  $('smp-anzahl').value = p.anzahl || 1;
  $('smp-kaufpreis').value = p.kaufpreis != null ? p.kaufpreis : '';
  $('smp-gekauft').value = isoZuDatum(p.gekauft_am);
  $('smp-notiz').value = p.notiz || '';
  $('smp-loeschen').classList.toggle('hidden', !posten);
  smPostenWert();
  modalOeffnen('modal-sm-posten');
}

/** Was dieses Exemplar mit dem gewählten Zustand wert ist — sofort beim Umstellen.
 *  Dieselben Faktoren wie im Backend; sie stehen dort als ZUSTAND_FAKTOR. */
const ZUSTAND_FAKTOR = { M: 1.10, NM: 1.00, EX: 0.85, GD: 0.70, LP: 0.55, PL: 0.42, PO: 0.30 };
function smPostenWert() {
  const el = $('smp-wert'); if (!el) return;
  const k = (SM.karten || []).find((x) => x.id === SM.posten.card_id);
  const basis = k && k.eur;
  if (!basis) { el.textContent = ''; return; }
  const z = $('smp-zustand').value;
  const f = ZUSTAND_FAKTOR[z] || 1;
  const anz = Math.max(1, parseInt($('smp-anzahl').value, 10) || 1);
  const stueck = basis * f;
  el.innerHTML = `${t('sm_wert_posten')}: <strong>${(stueck * anz).toFixed(2).replace('.', ',')} €</strong>`
    + (anz > 1 ? ` <span style="opacity:.7">(${stueck.toFixed(2).replace('.', ',')} € ${t('sm_je_stueck')})</span>` : '')
    + (z ? '' : ` <span style="opacity:.7">– ${t('sm_ohne_zustand')}</span>`);
}

async function smPostenSpeichern(loeschen) {
  const p = SM.posten;
  const daten = {
    card_id: p.card_id,
    variante: $('smp-variante').value,
    zustand: $('smp-zustand').value,
    sprache: $('smp-sprache').value,
    alt_zustand: SM.postenAlt.zustand,
    alt_sprache: SM.postenAlt.sprache,
    anzahl: loeschen ? 0 : Math.max(1, parseInt($('smp-anzahl').value, 10) || 1),
    kaufpreis: $('smp-kaufpreis').value.trim().replace(',', '.') || null,
    gekauft_am: gebIso($('smp-gekauft').value) || null,
    notiz: $('smp-notiz').value.trim(),
  };
  try {
    await api('api/sammlung/eintrag', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(daten),
    });
    modalSchliessen();
    await besitzLaden();
    restkostenNeu();
    sammlungLaden(); zeichneBinder(); zeichneErgebnisse();
  } catch (e) { toast(e.message); }
}

/** Kurzform eines Postens: „NM · DE" — leere Angaben werden weggelassen. */
function smPostenLabel(p) {
  const teile = [];
  if (p.variante && p.variante !== 'normal') teile.push(t('v_' + p.variante) || p.variante);
  if (p.zustand) teile.push(p.zustand);
  if (p.sprache) teile.push(p.sprache.toUpperCase());
  return teile.length ? esc(teile.join(' · ')) : t('sm_ohne_angabe');
}
/** Den Posten aus der geladenen Liste holen und den Dialog damit öffnen. */
function smPostenAus(cardId, index) {
  const k = (SM.karten || []).find((x) => x.id === cardId);
  smPostenOeffnen(cardId, k && k.posten ? k.posten[index] : null);
}

async function sammlungWeg(id) {
  try {
    await api('api/sammlung/eintrag', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ card_id: id, anzahl: 0 }),
    });
    delete S.besitz[id];
    sammlungLaden(); zeichneBinder();
  } catch (e) { toast(e.message); }
}

/* ------------------------------------------------------------------ Restkosten */
/* „Dir fehlen 12 Karten, zusammen etwa 87 €" — die Frage, die jeder mit einem halb
   vollen Binder hat. Die Zahlen lagen längst vor, sie wurden nur nie zusammengerechnet. */

const REST = { binder: null, daten: null, laeuft: false };

async function restkostenZeigen() {
  const box = $('wb-rest'); if (!box) return;
  // Nur für Binder, die ausdrücklich auf der Wunschliste stehen. Sonst stand über jedem
  // Plan eine Kaufsumme, die niemand ausgeben wollte.
  if (!S.user || S.nurAnsicht || !S.binder || !S.binder.id
      || !(S.binder.options && S.binder.options.wants === true)) {
    box.classList.add('hidden'); return;
  }
  if (REST.binder === S.binder.id && REST.daten) return restkostenMalen();
  if (REST.laeuft) return;
  REST.laeuft = true;
  try {
    const d = await api('api/sammlung/restkosten?binder=' + encodeURIComponent(S.binder.id));
    REST.binder = S.binder.id;
    REST.daten = (d.binder || [])[0] || { fehlt: 0, summe: 0, ohne_preis: 0 };
  } catch (e) { REST.daten = null; }
  REST.laeuft = false;
  restkostenMalen();
}
function restkostenMalen() {
  const box = $('wb-rest'), d = REST.daten;
  if (!d || !d.fehlt) { box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  box.innerHTML = `<div class="wr-zeile"><span>${t('rk_fehlen').replace('{n}', d.fehlt)}</span>
      <strong>${d.summe ? '≈ ' + anEur(d.summe, 0) : '–'}</strong></div>`
    + (d.ohne_preis ? `<div class="wr-hin">${t('rk_ohne').replace('{n}', d.ohne_preis)}</div>` : '')
    + `<button class="wr-link" onclick="kaufliste()">${t('ex_kauf')}</button>`;
}
/** Nach jeder Änderung an der Sammlung neu rechnen. */
function restkostenNeu() { REST.binder = null; REST.daten = null; restkostenZeigen(); }

/* ---------------------------------------------------------------- Auswertung */
/* Wert, Einsatz, Gewinn, Verlauf, Aufteilung, größte Posten — und seit September die
   Bewegung der letzten sieben Tage nach Betrag. Plus-Funktion; die Kopfkacheln darüber
   sieht jedes Konto. */

function smAnTage(n) { SM.anTage = n; sammlungLaden(); }

function zeichneAuswertung() {
  const d = AN.daten, r = $('sm-rumpf');
  if (!d.pro) {
    r.innerHTML = anSperre([t('an_pro_1'), t('an_pro_2'), t('an_pro_3'), t('an_pro_4')], null, 'Plus');
    return;
  }
  if (d.leer) {
    r.innerHTML = anDuenn(t('an_leer'))
      + `<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:16px">
          <button class="btn" onclick="ansicht('suche')">${t('an_leer_suchen')}</button>
          <button class="btn sekundaer" onclick="fotoOeffnen()">${t('foto_menu')}</button>
         </div>`;
    return;
  }
  // Solange die Historie jung ist, tragen nur wenige Karten die Kurve. Ihre Euro-Summe
  // wäre dann ein Bruchteil des Sammlungswerts direkt darüber — als Zahl unter einem
  // Diagramm namens „Wertverlauf" schlicht irreführend. Deshalb erst ab 60 % Deckung
  // Euro, darunter eine Indexkurve, die nur die Bewegung zeigt.
  const v = d.verlauf || {};
  const deckung = v.anteil == null ? 0 : v.anteil;
  const inEuro = deckung >= 60;
  let kurve;
  if (v.punkte && v.punkte.length >= 2) {
    const start = v.punkte[0].eur || 1;
    kurve = anLinie([{ name: inEuro ? t('an_wert') : t('an_bewegung_kurz'), farbe: 'var(--d1)',
                       punkte: v.punkte.map((p) => ({ datum: p.datum, wert: inEuro ? p.eur : p.eur / start * 100 })) }],
                    { titel: t('an_verlauf'), index: !inEuro, fmt: inEuro ? null : (x) => x.toFixed(0) })
      + `<div class="unter" style="margin:10px 0 0;font-size:11.5px">${
          (inEuro ? t('an_basis') : t('an_basis_index'))
            .replace('{n}', anZahl(v.von || v.basis)).replace('{g}', anZahl(v.gesamt || 0))
            .replace('{p}', deckung.toLocaleString(LANG === 'en' ? 'en' : 'de'))}</div>`;
  } else {
    kurve = anDuenn(t('an_reihe_kurz_s').replace('{n}', anZahl(v.tage || 0)));
  }
  const zeiten = [[30, t('an_30')], [90, t('an_90')], [365, t('an_365')]];

  const gruppen = [
    ['nach_seltenheit', t('an_g_seltenheit')], ['nach_set', t('an_g_set')],
    ['nach_pokemon', t('an_g_pokemon')], ['nach_typ', t('an_g_typ')],
    ['nach_art', t('an_g_art')], ['nach_jahrzehnt', t('an_g_jahrzehnt')],
    ['nach_zustand', t('an_g_zustand')],
  ].filter(([k]) => (d[k] || []).length);
  if (!gruppen.some(([k]) => k === AN.gruppe)) AN.gruppe = gruppen.length ? gruppen[0][0] : '';

  const bw = d.bewegung || [];
  r.innerHTML = `
    <div class="an-tafel">
      <h3>${t('sm_meine_beweg')}</h3>
      <div class="unter">${t('sm_meine_beweg_u')}${d.bewegung_summe != null ? ` · ${t('sm_beweg_summe').replace('{e}', (d.bewegung_summe >= 0 ? '+' : '') + anEur(d.bewegung_summe, 0))}` : ''}</div>
      ${bw.length ? `<div class="an-tab-rahmen"><table class="an-tab" style="min-width:0">
        <thead><tr><th></th><th>${t('an_karte')}</th><th class="r">${t('mk_diff')}</th><th class="r">${t('mk_7t')}</th></tr></thead>
        <tbody>${bw.map((c) => `<tr onclick="detailOeffnenId('${esc(c.id)}')" style="cursor:pointer">
          <td><img loading="lazy" src="${imgUrl(c.id)}" alt="" onerror="this.style.visibility='hidden'"></td>
          <td><div class="nm">${esc(c.name)}${c.anzahl > 1 ? ` <span class="mk-anz">${c.anzahl}×</span>` : ''}</div>
            <div class="set">${esc(c.set || '')}${c.nr ? ' · ' + esc(c.nr) : ''}</div></td>
          <td class="r"><strong class="${c.diff >= 0 ? 'an-plus' : 'an-minus'}">${c.diff >= 0 ? '+' : ''}${anEur(c.diff)}</strong></td>
          <td class="r">${mkDelta(c.prozent)}</td></tr>`).join('')}</tbody></table></div>` : anDuenn(t('sm_beweg_leer'))}
    </div>

    <div class="an-tafel">
      <h3>${t('an_verlauf')}</h3>
      <div class="unter">${t('an_verlauf_u')}</div>
      <div class="an-chips">${zeiten.map(([n, l]) => `<button class="chip ${SM.anTage === n ? 'on' : ''}" onclick="smAnTage(${n})">${l}</button>`).join('')}</div>
      ${kurve}
    </div>

    <div class="an-tafel">
      <h3>${t('an_aufteilung')}</h3>
      <div class="unter">${t('an_aufteilung_u')}</div>
      <div class="an-chips" id="an-gruppen">${gruppen.map(([k, l]) =>
        `<button class="chip ${AN.gruppe === k ? 'on' : ''}" onclick="anGruppe('${k}')">${esc(l)}</button>`).join('')}</div>
      <div id="an-gruppe-inhalt">${anBalken(d[AN.gruppe])}</div>
    </div>

    <div class="an-tafel">
      <h3>${t('an_posten')}</h3>
      <div class="unter">${t('an_posten_u')}</div>
      <div id="an-posten">${anPostenTabelle(d.karten_liste)}</div>
    </div>`;
}

function anGruppe(k) {
  AN.gruppe = k;
  document.querySelectorAll('#an-gruppen .chip').forEach((b) => b.classList.toggle('on', b.getAttribute('onclick').includes(`'${k}'`)));
  $('an-gruppe-inhalt').innerHTML = anBalken(AN.daten[k]);
}

function anPostenTabelle(liste) {
  if (!liste || !liste.length) return anDuenn(t('an_keine_daten'));
  return `<div class="an-tab-rahmen"><table class="an-tab">
    <thead><tr><th></th><th>${t('an_karte')}</th><th class="r">${t('an_anzahl')}</th>
      <th class="r">${t('an_stueckpreis')}</th><th class="r">${t('an_wert')}</th>
      <th class="r">${t('an_gekauft')}</th><th class="r">${t('an_rendite')}</th></tr></thead>
    <tbody>${liste.map((k, i) => `<tr class="${i >= 15 ? 'an-rest' : ''}">
      <td><img loading="lazy" src="${imgUrl(k.id)}" alt="" onerror="this.style.visibility='hidden'"></td>
      <td><div class="nm">${esc(k.name)}</div><div class="set">${esc(k.set || '')}${k.nr ? ' · ' + esc(k.nr) : ''}${k.variante && k.variante !== 'normal' ? ' · ' + esc(k.variante) : ''}</div></td>
      <td class="r">${anZahl(k.anzahl)}</td>
      <td class="r">${k.preis != null ? anEur(k.preis) : '—'}</td>
      <td class="r"><strong>${anEur(k.wert)}</strong><div class="set">${k.anteil} %</div></td>
      <td class="r">${k.kaufpreis != null ? anEur(k.kaufpreis) : '—'}</td>
      <td class="r">${k.rendite != null ? `<span class="${k.rendite >= 0 ? 'an-plus' : 'an-minus'}">${anProz(k.rendite)}</span>` : '—'}</td>
    </tr>`).join('')}</tbody></table></div>${anMehrKnopf('an-posten', Math.max(0, liste.length - 15))}`;
}
