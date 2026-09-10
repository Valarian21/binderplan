// Binderplan – Menüs & Dialoge, Binder anlegen, Filter, Suche, Binder-Panel, Durchblättern, Thema, Fotos.
// Aus index.html herausgelöst (Phase 5, 10.09.2026); die Dateien laden per defer in dieser Reihenfolge:
// kern → konto → werkbank → vitrine → preise → planer → detail → artwork → markt → sammlung.
// ---------- Menüs & Modale ----------
/** Ein Ort für alle Vorlagen. Vorher lagen dieselben sechs Einstiege im Binder-Wechsler,
 *  im leeren Binder, auf der Startseite und im Mehr-Menü — jeweils mit eigener Reihenfolge. */
function vorlagenOeffnen() {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  if (!$('startseite').classList.contains('hidden')) startSchliessen();
  modalOeffnen('modal-vorlagen');
}

function vorlage(art) {
  // Der Dialog wird getauscht, nicht geschlossen und neu geöffnet: der Ebenen-Eintrag bleibt
  // bestehen (er schließt ohnehin jedes offene Modal), und es gibt keinen Verlaufssprung,
  // dessen verzögertes popstate den neuen Dialog gleich wieder zumachen würde.
  $('modal-vorlagen').classList.add('hidden');
  if (art === 'leer') { modalSchliessen(); return neuLeer(); }
  const ziel = art === 'foto' ? 'modal-foto' : art === 'import' ? 'modal-import'
             : art === 'master' ? 'modal-master' : art === 'poke' ? 'modal-poke'
             : art === 'illu' ? 'modal-kuenstler' : 'modal-dex';
  $(ziel).classList.remove('hidden');
  if (ziel === 'modal-master') zeichneSetliste();
  if (ziel === 'modal-dex') zeichneGens();
  if (ziel === 'modal-import') { $('import-vorschau').innerHTML = ''; $('import-btn').disabled = true; }
  if (ziel === 'modal-foto') fotoSchritt(1);
}

function menuToggle(id) {
  document.querySelectorAll('.menu').forEach((m) => { if (m.id !== id) m.classList.add('hidden'); });
  const m = $(id);
  m.classList.toggle('hidden');
  menuEinpassen(m);
}
/** Ein Menü, das am linken oder rechten Fensterrand hinausragt, wird hineingeschoben –
 *  das Sortiermenü der Planer-Leiste war am Handy bei x = 0 angeschnitten. */
function menuEinpassen(m) {
  m.style.transform = '';
  if (m.classList.contains('hidden')) return;
  const r = m.getBoundingClientRect(), rand = 8;
  let dx = 0;
  if (r.left < rand) dx = rand - r.left;
  else if (r.right > window.innerWidth - rand) dx = (window.innerWidth - rand) - r.right;
  if (dx) m.style.transform = `translateX(${Math.round(dx)}px)`;
}
document.addEventListener('click', (ev) => {
  // Klick daneben schließt alle Menüs
  if (!ev.target.closest('.menuwrap')) {
    document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
    return;
  }
  // Klick auf einen Eintrag IM Menü schließt es ebenfalls. Vorher blieb es offen und
  // verdeckte die Ansicht, in die man gerade gewechselt war. Ausgenommen sind Felder,
  // Schalter und Beschriftungen — dort arbeitet man im Menü weiter.
  const eintrag = ev.target.closest('.menu button, .menu a');
  if (!eintrag || ev.target.closest('input, select, textarea, label, .bw-edit, .binder-groesse')) return;
  const menu = eintrag.closest('.menu');
  if (menu) menu.classList.add('hidden');
});
// Escape schließt Modale und offene Menüs — daneben tippen schließt bewusst nichts mehr
document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape') {
    // Über die eigenen Schließer gehen, damit Aufräumarbeiten laufen: der Foto-Import bleibt
    // während des Hochladens offen, und das Artwork-Modal stoppt seine Abfrage.
    if (!$('modal-foto').classList.contains('hidden')) { fotoSchliessen(); return; }
    if (!$('modal-artwork').classList.contains('hidden')) { artworkSchliessen(); return; }
    modalSchliessen();
    document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  }
});
function modalOeffnen(id) {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  $(id).classList.remove('hidden');
  if (id === 'modal-master') zeichneSetliste();
  if (id === 'modal-dex') zeichneGens();
  ebeneOeffnen(modalZu);
}
/** Nur das DOM schließen – ohne den Verlauf anzufassen. */
function modalZu() { document.querySelectorAll('.overlay').forEach((o) => o.classList.add('hidden')); }
function modalSchliessen() { ebeneZu(modalZu); }

/** Einen Dialog schließen und danach den nächsten öffnen.
 *
 *  `modalSchliessen()` geht über `history.back()`; geschlossen wird erst im popstate der
 *  nächsten Runde — und `modalZu()` schließt *alle* Overlays. Ein sofort danach geöffneter
 *  Dialog wurde deshalb von genau diesem popstate wieder zugemacht: Wer eine Kunstseite
 *  freigeben wollte und noch kein Geburtsdatum hinterlegt hatte, sah gar nichts mehr. */
function dialogWechsel(fn) {
  let getan = false;
  const weiter = () => {
    if (getan) return;
    getan = true;
    window.removeEventListener('popstate', weiter);
    setTimeout(fn, 0);
  };
  window.addEventListener('popstate', weiter);
  modalSchliessen();
  setTimeout(weiter, 250);       // ohne Verlaufseintrag schließt ebeneZu sofort
}

// ---------- Binder anlegen ----------
async function binderSpeichernNeu(name, mode, items, options) {
  let res;
  try {
    res = await api('api/binders', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, mode, layout: (S.binder && S.binder.layout) || '3x3', options: options || {}, items }),
    });
  } catch (e) {
    if (!gate(e)) toast(e.message);
    return false;
  }
  S.binder = { id: res.id, name, mode, layout: (S.binder && S.binder.layout) || '3x3', options: options || {}, items };
  merkeBinderId(res.id);
  binderAnzeigen();
  zeichneErgebnisse();
  return true;
}

async function neuLeer(still) {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  // Leerer Binder = nur lokal; die DB-Zeile entsteht mit der ersten Karte (keine leeren Gast-Binder mehr)
  S.binder = lokalerBinder();
  binderAnzeigen(); zeichneErgebnisse();
  if (!still) { modalSchliessen(); toast(t('angelegt')); }
}

let wzSet = null;
function zeichneSetliste() {
  const q = ($('set-suche').value || '').toLowerCase();
  // Gesucht wird in beiden Sprachen und im Set-Code: „Jungle“ findet auch „Dschungel“, „sv1“ das Set
  const sets = (S.meta.sets || []).filter((s) => !q || [s.name, s.name_en, s.id].some((n) => (n || '').toLowerCase().includes(q)));
  let html = ''; let letzte = null;
  for (const s of sets) {
    const serie = (LANG === 'en' ? (s.aera_name_en || s.aera_name) : s.aera_name) || '—';
    if (serie !== letzte) { letzte = serie; html += `<div class="serie-kopf">${serie}</div>`; }
    const n = (LANG === 'en' ? (s.name_en || s.name) : (s.name || s.name_en)) || s.id;
    // Set-Symbol (TCGdex, über den gecachten Proxy) – fehlt es, wird der Platz
    // unsichtbar reserviert, damit die Namen bündig bleiben.
    const sym = s.symbol
      ? `<img class="set-sym" src="api/img/set/${encodeURIComponent(s.id)}" alt="" loading="lazy" onerror="this.classList.add('leer')">`
      : `<img class="set-sym leer" alt="">`;
    html += `<button class="setzeile ${wzSet === s.id ? 'on' : ''}" onclick="wzSet='${s.id}';zeichneSetliste()">
      ${sym}
      <span style="font-weight:600">${n}</span>
      <span style="color:var(--mut);font-size: var(--t-s)">${s.official || s.total || '?'} ${t('po_karten')}</span>
      <span class="datum">${s.release_date || ''}</span></button>`;
  }
  $('set-liste').innerHTML = html || `<div style="padding:14px;color:var(--mut)">${t('keine_sets')}</div>`;
}

/** Knopf sperren, Fehler zeigen: zwei Klicks legten zwei Binder an, ein Serverfehler ließ
 *  den Dialog stumm offen stehen. */
async function mitKnopf(knopf, fn) {
  const el = typeof knopf === 'string' ? document.querySelector(knopf) : knopf;
  if (el) { if (el.disabled) return; el.disabled = true; el.dataset.alt = el.textContent; el.textContent = t('arbeitet'); }
  try {
    await fn();
  } catch (e) {
    if (!gate(e)) toast((e.detail && e.detail.text) || e.message || t('fehler_laden'));
  } finally {
    if (el) { el.disabled = false; el.textContent = el.dataset.alt || t('anlegen'); }
  }
}

async function masterErstellen() { return mitKnopf('#modal-master .btn:not(.sekundaer)', _masterErstellen); }
async function _masterErstellen() {
  if (!wzSet) return toast(t('erst_waehlen'));
  const s = S.meta.sets.find((x) => x.id === wzSet);
  const name = 'Master Set ' + ((LANG === 'en' ? s.name_en : s.name) || s.name_en || wzSet);
  const d = await api('api/sets/' + wzSet + '/cards');
  const sortkey = $('opt-master-sort').value;
  const karten = d.karten.slice();
  if (sortkey === 'typ') karten.sort((a, b) => ((a.types[0] || 'zz') + String(parseInt(a.local_id) || 0).padStart(5, '0')).localeCompare((b.types[0] || 'zz') + String(parseInt(b.local_id) || 0).padStart(5, '0')));
  if (sortkey === 'dex') karten.sort((a, b) => (a.dex || 99999) - (b.dex || 99999));
  let items = karten.map((k) => ({ type: 'card', id: k.id }));
  const options = {};
  if ($('opt-reverse').checked) {
    options.reverse = true;
    items = items.concat(d.karten.filter((k) => k.reverse).map((k) => ({ type: 'card', id: k.id, variant: 'reverse' })));
  }
  if (!await binderSpeichernNeu(name, 'master', items, options)) return;
  modalSchliessen();
  toast(items.length + ' ' + t('angelegt'));
}

const wzGens = new Set();
function zeichneGens() {
  $('gen-chips').innerHTML = (S.meta.gens || []).map((g) =>
    `<button class="chip ${wzGens.has(g.gen) ? 'on' : ''}" onclick="genToggle(${g.gen})">${t('gen')} ${g.gen} <span style="font-weight:500">#${g.von}–${g.bis}</span></button>`
  ).join('') + `<button class="chip" onclick="S.meta.gens.forEach(g=>wzGens.add(g.gen));zeichneGens()">${t('alle_gen')}</button>`;
  const summe = [...wzGens].reduce((a, g) => { const i = S.meta.gens.find((x) => x.gen === g); return a + (i ? i.bis - i.von + 1 : 0); }, 0);
  $('gen-info').textContent = summe ? summe + ' ' + t('pokemon_gew') : '';
}
function genToggle(g) { wzGens.has(g) ? wzGens.delete(g) : wzGens.add(g); zeichneGens(); }

async function dexErstellen() { return mitKnopf('#modal-dex .btn:not(.sekundaer)', _dexErstellen); }
async function _dexErstellen() {
  if (!wzGens.size) return toast(t('erst_waehlen'));
  const gens = [...wzGens].sort((a, b) => a - b);
  const d = await api('api/pokedex?gens=' + gens.join(','));
  const items = d.pokemon.map((p) => ({ type: 'dex', dex: p.dex }));
  if (!await binderSpeichernNeu('Pokédex ' + t('gen') + ' ' + gens.join('+'), 'dex', items, {})) return;
  modalSchliessen();
  toast(items.length + ' ' + t('angelegt'));
}

// ---------- Meine Binder ----------
function binderIds() { try { return JSON.parse(localStorage.getItem('bp_binder') || '[]'); } catch (e) { return []; } }
function merkeBinderId(id) {
  const ids = binderIds().filter((x) => x !== id); ids.unshift(id);
  localStorage.setItem('bp_binder', JSON.stringify(ids.slice(0, 50)));
}
async function wechslerOeffnen() {
  menuToggle('menu-bw');
  const box = $('bw-liste');
  if ($('menu-bw').classList.contains('hidden')) return;
  box.innerHTML = `<div style="padding:6px 12px;font-size: var(--t-s);color:var(--mut)">…</div>`;
  try {
    const d = await api('api/binders?ids=' + binderIds().join(','));
    box.innerHTML = d.binder.map((b) => `
      <button class="${S.binder && S.binder.id === b.id ? 'aktiv' : ''}" onclick="binderOeffnen('${b.id}')">
        <span style="min-width:0"><span style="display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(b.name)}</span><span class="bwl-meta">${b.anzahl} ${t('faecher')} · ${b.layout.replace('x', '×')}${b.gesammelt ? ` · <span style=\"color:var(--gruen-text)\">${b.gesammelt} ✓</span>` : ''}</span></span>
        <span class="bwl-del" onclick="event.stopPropagation();binderLoeschen('${b.id}')" title="${t('bestaetigen_loeschen')}">✕</span>
      </button>`).join('') || `<div style="padding:6px 12px;font-size: var(--t-s);color:var(--mut)">–</div>`;
  } catch (e) { box.innerHTML = ''; }
}
async function meineOeffnen() { wechslerOeffnen(); }
async function binderOeffnen(id) {
  try { S.binder = await api('api/binders/' + id); binderAnzeigen(); zeichneErgebnisse(); } catch (e) { toast(t('fehler_laden')); }
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
}
async function binderLoeschen(id) {
  if (!confirm(t('bestaetigen_loeschen'))) return;
  try { await api('api/binders/' + id, { method: 'DELETE' }); } catch (e) {}
  localStorage.setItem('bp_binder', JSON.stringify(binderIds().filter((x) => x !== id)));
  if (S.binder && S.binder.id === id) { S.binder = null; await neuLeer(true); }
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  wechslerOeffnen();
}

// ---------- Filterleiste ----------
function baueFilterLeiste() {
  // Kartenarten
  $('f-kinds').innerHTML = KIND_KEYS.map((k) =>
    `<button class="chip ${filter.kinds.has(k) ? 'on' : ''}" data-kind="${k}" onclick="kindToggle('${k}')">${T[LANG].kinds[k]}</button>`).join('');
  // Typen
  $('f-typen').innerHTML = Object.keys(T.de.typen).map((en) =>
    `<button class="chip ${filter.typ === en ? 'on' : ''}" data-typ="${en}" onclick="typToggle('${en}')">${T[LANG].typen[en]}</button>`).join('');
  // Ären der gewählten Region (westlich: echte TCG-Ären; Japan: TCGdex-Serien)
  $('f-serie').innerHTML = `<option value="">${t('alle_aeren')}</option>` + (S.meta.series || []).filter((s) => (s.region || 'intl') === filter.region).map((s) =>
    `<option value="${s.id}" ${filter.serie === s.id ? 'selected' : ''}>${LANG === 'en' ? (s.name_en || s.name) : s.name}${s.von ? ` (${s.von}–${s.bis || ''})` : ''}</option>`).join('');
  // Sets (nach Ära gefiltert, chronologisch)
  baueSetSelect();
  // Seltenheit als Dropdown mit Gruppen (statt 39 Rohwerten)
  const rg = [...filter.rgroup][0] || '';
  $('f-rgroup-select').innerHTML = `<option value="">${t('alle_rar')}</option>` + (S.meta.rarity_groups || []).map((g) =>
    `<option value="${g.id}" ${rg === g.id ? 'selected' : ''}>${LANG === 'en' ? g.name_en : g.name}</option>`).join('');
  // Künstler als Dropdown: erst die meistgesammelten, dann alle alphabetisch
  const illus = S.meta.illustrators || [];
  const tops = illus.filter((i) => i.top), rest = illus.filter((i) => !i.top);
  const opt = (i) => `<option value="${esc(i.name)}" ${filter.illustrator === i.name ? 'selected' : ''}>${esc(i.name)} (${i.anzahl})</option>`;
  $('f-illu').innerHTML = `<option value="">${t('alle_illu')}</option>` + (tops.length ? `<optgroup label="${t('illu_top')}">${tops.map(opt).join('')}</optgroup>` : '') + `<optgroup label="${t('illu_alle')}">${rest.map(opt).join('')}</optgroup>`;
  $('illu-namen').innerHTML = illus.map((i) => `<option value="${esc(i.name)}">${i.anzahl} ${t('illu_karten')}</option>`).join('');
  if ($('illu-top')) $('illu-top').innerHTML = tops.slice(0, 12).map((i) => `<button class="chip klein" onclick="$('illu-name').value='${esc(i.name).replace(/'/g, "\\'")}';illuInfo()">${esc(i.name)}</button>`).join('');
  // Sonderdrucke
  zeichneMotivSchnell();
  $('f-sonder').innerHTML = `<button class="chip ${filter.first ? 'on' : ''}" onclick="filter.first=!filter.first;baueFilterLeiste();mehrFilterZahl();sucheNeu()">${t('first_ed')}</button>`;
  // Trainer-Typ, Regulation Mark
  $('f-trainer').innerHTML = `<option value="">${t('alle_trainer')}</option>` + (S.meta.trainer_types || []).map((x) => `<option value="${esc(x)}" ${filter.trainer === x ? 'selected' : ''}>${esc(x)}</option>`).join('');
  $('f-regmark').innerHTML = (S.meta.regmarks || []).map((m) => `<button class="chip klein ${filter.regmark.has(m) ? 'on' : ''}" onclick="filter.regmark.has('${m}')?filter.regmark.delete('${m}'):filter.regmark.add('${m}');baueFilterLeiste();mehrFilterZahl();sucheNeu()">${esc(m)}</button>`).join('');
  // Schnellauswahl über den Treffern
  $('f-presets').innerHTML = (S.meta.presets || []).map((p) => `<button class="chip ${filter.preset === p.id ? 'on' : ''}" onclick="filter.preset=filter.preset==='${p.id}'?'':'${p.id}';baueFilterLeiste();sucheNeu()">${LANG === 'en' ? p.name_en : p.name}</button>`).join('');
  // Seltenheiten (Rohwerte, versteckt – Detail-Panel zeigt sie)
  $('f-rarity').innerHTML = `<option value="">${t('alle_rar')}</option>` + (S.meta.rarities || []).map((r) =>
    `<option value="${r.rarity.replace(/"/g, '&quot;')}" ${filter.rarity === r.rarity ? 'selected' : ''}>${r.rarity} (${r.anzahl})</option>`).join('');
  // Sortierung
  $('f-sort').innerHTML = ['datum', 'dex', 'name', 'nummer', 'typ'].map((s) =>
    `<option value="${s}" ${filter.sort === s ? 'selected' : ''}>${t('s_' + s)}</option>`).join('');
  richtungZeichnen();
  // Im Planer war es ein Auswahlfeld *und* ein Knopf — zwei Schritte für eine Absicht.
  // Jetzt ist jede Sortierung ein Eintrag im Menü und sortiert beim Klick.
  const sl = $('planer-sortliste');
  if (sl) sl.innerHTML = ['datum', 'dex', 'name', 'nummer', 'typ', 'rarity'].map((x) =>
    `<button onclick="auswahlSortieren('${x}')">${t('s_' + x)}</button>`).join('');
}

function baueSetSelect() {
  const sets = (S.meta.sets || []).filter((s) => (s.region || 'intl') === filter.region && (!filter.serie || s.aera === filter.serie));
  $('f-set').innerHTML = `<option value="">${t('alle_sets')}</option>` + sets.map((s) => {
    const n = (LANG === 'en' ? (s.name_en || s.name) : (s.name || s.name_en)) || s.id;
    return `<option value="${s.id}" ${filter.set === s.id ? 'selected' : ''}>${n} (${(s.release_date || '?').slice(0, 4)})</option>`;
  }).join('');
  zeichneSetWahlKnopf();
}
// Set-Wähler mit Symbolen: viele Sammler kennen das Symbol, nicht den Namen
function setName(s) { return (LANG === 'en' ? (s.name_en || s.name) : (s.name || s.name_en)) || s.id; }
function setSymbol(s, cls) {
  if (s.symbol) return `<img class="${cls}" src="api/img/set/${encodeURIComponent(s.id)}" alt="" onerror="this.outerHTML='<span class=setcode>${esc(s.id)}</span>'">`;
  // ohne Symbol (japanische Sets, Promos): der Set-Code ist das, was Sammler kennen
  return `<span class="setcode">${esc(s.id)}</span>`;
}
function zeichneSetWahlKnopf() {
  const s = (S.meta.sets || []).find((x) => x.id === filter.set);
  const se = !s && filter.serie ? (S.meta.series || []).find((x) => x.id === filter.serie) : null;
  $('f-set-lbl').innerHTML = s ? `${setSymbol(s, '')}<span>${esc(setName(s))}</span>`
    : se ? `<span>${t('f_aera')}: ${esc(se.name)}</span>` : `<span>${t('alle_sets')}</span>`;
}
function setWahlToggle() {
  menuToggle('set-popover');
  const pop = $('set-popover');
  if (!pop.classList.contains('hidden')) {
    // fixed positioniert: die Filterspalte scrollt und würde ein absolutes Popover abschneiden
    const r = $('f-set-btn').getBoundingClientRect();
    const w = Math.min(380, window.innerWidth - 16);
    pop.style.position = 'fixed'; pop.style.width = w + 'px';
    pop.style.left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8)) + 'px';
    pop.style.top = (r.bottom + 4) + 'px'; pop.style.right = 'auto';
    pop.style.maxHeight = (window.innerHeight - r.bottom - 16) + 'px'; pop.style.overflow = 'auto';
    $('set-popover-suche').value = ''; zeichneSetWahl(); setTimeout(() => $('set-popover-suche').focus(), 30);
  }
}
function zeichneSetWahl() {
  const q = ($('set-popover-suche').value || '').toLowerCase();
  const sets = (S.meta.sets || []).filter((s) => (s.region || 'intl') === filter.region && (!filter.serie || s.aera === filter.serie) && (!q || setName(s).toLowerCase().includes(q) || s.id.toLowerCase().includes(q)));
  let html = `<button class="setzeile ${filter.set || filter.serie ? '' : 'on'}" onclick="aeraWaehlen('')"><img class="set-sym leer" alt=""><span style="font-weight:600">${t('alle_sets')}</span></button>`;
  let letzte = null;
  for (const s of sets) {
    const aera = (LANG === 'en' ? (s.aera_name_en || s.aera_name) : s.aera_name) || '—';
    if (aera !== letzte) {
      letzte = aera;
      // Der Gruppenkopf ist ein Knopf: „alle Sets dieser Ära" — so entfällt das eigene Ära-Feld.
      const n = sets.filter((x) => (x.aera || '') === (s.aera || '')).length;
      html += `<button class="serie-kopf wahl ${filter.serie && filter.serie === s.aera && !filter.set ? 'on' : ''}" onclick="aeraWaehlen('${esc(s.aera || '')}')" title="${t('aera_waehlen')}">${esc(aera)}<span>${n} Sets ›</span></button>`;
    }
    html += `<button class="setzeile ${filter.set === s.id ? 'on' : ''}" onclick="setWaehlen('${s.id}')">${setSymbol(s, 'set-sym')}<span style="font-weight:600">${esc(setName(s))}</span><span style="color:var(--mut);font-size: var(--t-s)">${s.official || s.total || '?'} ${t('po_karten')}</span><span class="datum">${(s.release_date || '').slice(0, 4)}</span></button>`;
  }
  $('set-popover-liste').innerHTML = html || `<div style="padding:14px;color:var(--mut)">${t('keine_sets')}</div>`;
}
/** Ganze Ära wählen (oder mit '' alles zurücksetzen): das ersetzt das frühere Ära-Feld. */
function aeraWaehlen(id) {
  filter.serie = id || ''; filter.set = '';
  $('f-serie').value = filter.serie; $('f-set').value = '';
  baueSetSelect();
  $('set-popover').classList.add('hidden');
  sucheNeu();
}
function setWaehlen(id) {
  filter.set = id; $('f-set').value = id;
  $('set-popover').classList.add('hidden');
  zeichneSetWahlKnopf(); sucheNeu();
}

function baueDexNamen() {
  if (!S.pokedex) return;
  $('dex-namen').innerHTML = S.pokedex.map((p) =>
    `<option value="${LANG === 'en' ? p.name_en : p.name}"></option>`).join('');
}

async function ladePokedex() {
  if (S.pokedex) return;
  const d = await api('api/pokedex');
  S.pokedex = d.pokemon.map((p) => ({ ...p, name_en: p.name_en || p.name }));
  baueDexNamen();
}

function regionSetzen(r) {
  if (filter.region === r) return;
  filter.region = r; filter.serie = ''; filter.set = '';
  // Merkmale, die es für japanische Karten nicht gibt, zurücknehmen
  if (r === 'jp') { filter.illustrator = ''; filter.rgroup.clear(); filter.kinds.clear(); filter.trainer = ''; filter.regmark.clear(); filter.first = false; filter.preset = ''; }
  $('reg-intl').classList.toggle('on', r === 'intl'); $('reg-jp').classList.toggle('on', r === 'jp');
  $('f-lang').classList.toggle('hidden', r === 'jp');
  // Bis die JP-Einzelkartendaten geladen sind, gibt es dort keine Künstler/Seltenheiten
  const jpOhne = r === 'jp' && !(S.meta.jp_details > 5000);
  $('f-illu-rar').classList.toggle('hidden', jpOhne); $('f-illu-suche').classList.toggle('hidden', jpOhne);
  $('f-mehr-btn').classList.toggle('hidden', jpOhne); if (jpOhne) mehrFilterToggle(false);
  $('f-jp-hinweis').classList.toggle('hidden', !jpOhne);
  baueFilterLeiste(); mehrFilterZahl(); sucheNeu();
}
function rgroupToggle(g) {
  filter.rgroup.clear(); if (g) filter.rgroup.add(g);
  $('f-rgroup-select').value = g || '';
  sucheNeu();
}
function illuSetzen(name) {
  filter.illustrator = filter.illustrator === name ? '' : name;
  $('f-illu').value = filter.illustrator;
  sucheNeu();
}
function kindToggle(k) {
  filter.kinds.has(k) ? filter.kinds.delete(k) : filter.kinds.add(k);
  document.querySelectorAll('[data-kind]').forEach((el) => el.classList.toggle('on', filter.kinds.has(el.dataset.kind)));
  mehrFilterZahl(); sucheNeu();
}
function typToggle(ty) {
  filter.typ = filter.typ === ty ? '' : ty;
  document.querySelectorAll('[data-typ]').forEach((el) => el.classList.toggle('on', el.dataset.typ === filter.typ));
  mehrFilterZahl(); sucheNeu();
}
function filterReset() {
  Object.assign(filter, { q: '', set: '', serie: '', typ: '', rarity: '', dex: 0, sort: 'datum', richtung: 'asc', illustrator: '', trainer: '', first: false, jahrVon: 0, jahrBis: 0, preset: '' });   // Region bleibt
  filter.kinds.clear(); filter.rgroup.clear(); filter.regmark.clear();
  filter.artOrte.clear(); filter.artMerkmale.clear();
  Object.assign(filter, { artZeit: '', artWasser: 0, artText: '' });
  if ($('f-art-text')) $('f-art-text').value = '';
  zeichneArtChips(); artFilterZahl(); zeichneMotivSchnell();
  $('f-suche').value = ''; $('f-dex').value = ''; $('f-illu').value = ''; $('f-illu-suche').value = ''; $('f-jahr-von').value = ''; $('f-jahr-bis').value = ''; richtungZeichnen();
  if ($('f-suche-mobil')) $('f-suche-mobil').value = '';
  baueFilterLeiste(); mehrFilterZahl(); zeichneAktivChips();
  sucheNeu();
}

/** Was gerade filtert — als wegklickbare Chips über der Trefferliste.
 *  Vorher musste man zwölf Felder durchsehen, um zu verstehen, warum acht Karten kommen. */
function aktiveFilter() {
  const raus = [];
  const setname = () => {
    const st = (S.meta && S.meta.sets || []).find((x) => x.id === filter.set);
    return st ? (LANG === 'en' ? (st.name_en || st.name) : st.name) : filter.set;
  };
  if (filter.q) raus.push([t('f_suche'), filter.q, () => { $('f-suche').value = ''; $('f-suche-mobil').value = ''; filter.q = ''; }]);
  if (filter.set) raus.push(['Set', setname(), () => { filter.set = ''; if (typeof setWaehlen === 'function') setWaehlen(''); }]);
  if (filter.dex) raus.push([t('f_pokemon'), $('f-dex').value || String(filter.dex), () => { filter.dex = 0; $('f-dex').value = ''; }]);
  if (filter.serie) {
    const se = (S.meta && S.meta.series || []).find((x) => x.id === filter.serie);
    raus.push([t('f_aera'), se ? se.name : filter.serie, () => { filter.serie = ''; $('f-serie').value = ''; }]);
  }
  if (filter.illustrator) raus.push([t('f_illu'), filter.illustrator, () => { filter.illustrator = ''; $('f-illu').value = ''; $('f-illu-suche').value = ''; }]);
  filter.rgroup.forEach((r) => raus.push([t('f_rarity'), r, () => filter.rgroup.delete(r)]));
  filter.kinds.forEach((k) => raus.push([t('f_art'), (T[LANG].arten && T[LANG].arten[k]) || k, () => filter.kinds.delete(k)]));
  if (filter.typ) raus.push([t('f_typ'), (T[LANG].typen && T[LANG].typen[filter.typ]) || filter.typ, () => { filter.typ = ''; }]);
  if (filter.trainer) raus.push([t('f_trainer'), filter.trainer, () => { filter.trainer = ''; $('f-trainer').value = ''; }]);
  filter.regmark.forEach((r) => raus.push(['Regulation', r, () => filter.regmark.delete(r)]));
  if (filter.first) raus.push([t('f_sonder'), '1st Edition', () => { filter.first = false; }]);
  if (filter.jahrVon || filter.jahrBis) raus.push([t('f_jahr'), `${filter.jahrVon || '…'}–${filter.jahrBis || '…'}`,
    () => { filter.jahrVon = 0; filter.jahrBis = 0; $('f-jahr-von').value = ''; $('f-jahr-bis').value = ''; }]);
  if (filter.preset) raus.push([t('f_preset'), filter.preset, () => { filter.preset = ''; }]);
  if (filter.artText) raus.push([t('f_art_text'), filter.artText, () => { filter.artText = ''; $('f-art-text').value = ''; }]);
  filter.artOrte.forEach((o) => raus.push([t('f_art_ort'), o, () => filter.artOrte.delete(o)]));
  filter.artMerkmale.forEach((m) => raus.push([t('f_art_merkmal'), m, () => filter.artMerkmale.delete(m)]));
  if (filter.artZeit) raus.push([t('f_art_zeit'), filter.artZeit, () => { filter.artZeit = ''; }]);
  if (filter.artWasser) raus.push([t('f_art_wasser'), String(filter.artWasser), () => { filter.artWasser = 0; }]);
  return raus;
}

let _aktivWeg = [];
function zeichneAktivChips() {
  const box = $('f-aktiv');
  if (!box) return;
  const liste = aktiveFilter();
  _aktivWeg = liste.map((x) => x[2]);
  box.innerHTML = liste.map(([lbl, wert, ], i) =>
    `<button class="chip" onclick="filterChipWeg(${i})" title="${esc(lbl)} – ${t('f_chip_weg')}"><b>${esc(String(wert).slice(0, 22))}</b><span class="w">✕</span></button>`).join('');
  const reset = $('f-reset-btn');
  if (reset) reset.classList.toggle('hidden', liste.length === 0);
  const rail = $('f-rail-zahl');
  if (rail) { rail.textContent = liste.length; rail.classList.toggle('hidden', liste.length === 0); }
  const zeigen = $('f-zeigen-btn');
  if (zeigen) zeigen.textContent = S.gesamt != null ? `${S.gesamt.toLocaleString(LANG === 'de' ? 'de-DE' : 'en-US')} ${t('karten_zeigen')}` : t('karten_zeigen');
}

function filterChipWeg(i) {
  const fn = _aktivWeg[i];
  if (!fn) return;
  fn();
  baueFilterLeiste(); mehrFilterZahl();
  if (typeof zeichneArtChips === 'function') zeichneArtChips();
  sucheNeu();
}

function filterParams() {
  const p = new URLSearchParams();
  if (filter.q) p.set('q', filter.q);
  if (filter.set) p.set('set_id', filter.set);
  if (filter.serie) p.set('serie', filter.serie);
  if (filter.typ) p.set('typ', filter.typ);
  if (filter.rarity) p.set('rarity', filter.rarity);
  if (filter.dex) p.set('dex', String(filter.dex));
  if (filter.kinds.size) p.set('kind', [...filter.kinds].join(','));
  if (filter.illustrator) p.set('illustrator', filter.illustrator);
  if (filter.rgroup.size) p.set('rgroup', [...filter.rgroup].join(','));
  if (filter.trainer) p.set('trainer_type', filter.trainer);
  if (filter.regmark.size) p.set('regmark', [...filter.regmark].join(','));
  if (filter.first) p.set('first', '1');
  if (filter.jahrVon) p.set('jahr_von', String(filter.jahrVon));
  if (filter.jahrBis) p.set('jahr_bis', String(filter.jahrBis));
  if (filter.preset) p.set('preset', filter.preset);
  if (filter.artOrte.size) p.set('art_ort', [...filter.artOrte].join(','));
  if (filter.artMerkmale.size) p.set('art_merkmal', [...filter.artMerkmale].join(','));
  if (filter.artZeit) p.set('art_zeit', filter.artZeit);
  if (filter.artWasser) p.set('art_wasser', String(filter.artWasser));
  if (filter.artText.trim()) p.set('art_text', filter.artText.trim());
  p.set('region', filter.region);
  p.set('sort', filter.sort); p.set('richtung', filter.richtung);
  return p;
}

// ---------- Suche ----------
let _sucheSeq = 0;   // gegen Race Conditions: nur die neueste Antwort zählt
async function sucheNeu() {
  if ($('f-suche-mobil') && $('f-suche-mobil').value !== $('f-suche').value) {
    $('f-suche-mobil').value = $('f-suche').value;
  } S.offset = 0; S.ergebnisse = []; await sucheLaden(); }
async function sucheLaden() {
  const meineSeq = ++_sucheSeq;
  const p = filterParams(); p.set('limit', '60'); p.set('offset', String(S.offset));
  try {
    const d = await api('api/cards?' + p);
    // Eine schnellere spätere Anfrage hat uns überholt → dieses (veraltete)
    // Ergebnis verwerfen, sonst überschreibt es die aktuelle Filterauswahl.
    if (meineSeq !== _sucheSeq) return;
    S.gesamt = d.total;
    S.ergebnisse = S.ergebnisse.concat(d.karten);
    S.offset += d.karten.length;
    zeichneErgebnisse();
  } catch (e) { if (meineSeq === _sucheSeq) toast(t('fehler_suche')); }
}

function anzahlImBinder(id) {
  return S.binder ? S.binder.items.filter((i) => i.type === 'card' && i.id === id).length : 0;
}

function zeichneErgebnisse() {
  if (!S.meta) return;
  $('erg-anzahl').innerHTML = `<strong>${anZahl(S.gesamt)}</strong> ${t('treffer')}`;
  zeichneAktivChips();
  $('btn-mehr').classList.toggle('hidden', S.offset >= S.gesamt);
  $('btn-alle').disabled = S.gesamt === 0 || S.gesamt > 2000;
  $('btn-alle').title = S.gesamt > 2000 ? t('zu_viele') : '';
  if (S.gesamt === 0) {
    $('ergebnisse').innerHTML = `<div class="erg-leer"><strong>${t('erg_leer')}</strong><button class="btn sekundaer" onclick="filterReset()">${t('f_reset')}</button></div>`;
    return;
  }
  $('ergebnisse').innerHTML = S.ergebnisse.map((k, i) => {
    const n = anzahlImBinder(k.id);
    const sn = S.user ? (S.besitz[k.id] || 0) : 0;
    const enb = KLANG === 'de' && k.img && k.img_lang && k.img_lang !== 'de' && k.region !== 'jp' ? '<span class="enb" title="Bild in Englisch">EN</span>' : '';
    const jpb = k.region === 'jp' ? '<span class="jpb">JP</span>' : '';
    // Ein Klick auf die Karte öffnet ihr Detail — so wie in der Sammlung, im Markt und auf
    // den öffentlichen Seiten. Vorher legte er sie in den Binder, und das Detail versteckte
    // sich hinter einem 18 Pixel großen „i". Hinzufügen ist der beschriftete Knopf darunter,
    // dazu weiterhin das Ziehen ins Fach.
    return `<div class="tk" draggable="true" ondragstart="ergDragStart(event,${i})" ondragend="ergDragEnde()" onclick="tkKlick(event,${i})" ontouchstart="tkDruck(${i})" ontouchend="tkLos()" ontouchmove="tkLos()" ontouchcancel="tkLos()" title="${esc(nm(k))} – ${t('tk_info')}">
      <div class="bildbox">${k.img ? `<img loading="lazy" src="${imgUrl(k.id)}" alt="" onerror="this.outerHTML='<div class=kein-bild>'+this.dataset.n+'</div>'" data-n="${esc(nm(k))}">` : `<div class="kein-bild">${nm(k)}</div>`}${preisBadge(k.id)}</div>
      ${enb}${jpb}
      ${n ? `<span class="anz" title="${t('im_binder')}">${n}</span>` : ''}
      <div class="tk-name"><span class="tkn">${nm(k)}</span></div>
      <div class="tk-meta">${setNm(k)} · ${k.local_id}${k.datum ? ' · ' + k.datum.slice(0, 4) : ''}</div>
      <div class="tk-hab">
        <button class="hinzu" onclick="event.stopPropagation();kartAdd(${i})" title="${t('tk_klick')}">＋ ${zielText()}</button>
        <button class="hat-k ${sn ? 'hat' : ''}" onclick="event.stopPropagation();sammlungAufnehmen('${esc(k.id)}')"
                title="${t('sm_aufnehmen_t')}" aria-label="${t('tk_besitz')}">${sn ? '✓ ' + sn + '×' : '✓'}</button>
        <button class="wunsch ${wunschHat(k.id) ? 'an' : ''}" onclick="event.stopPropagation();wunschToggle('${esc(k.id)}')"
                title="${t('wl_titel')}" aria-label="${t('wl_titel')}">${wunschHat(k.id) ? '★' : '☆'}</button>
        ${k.reverse ? `<button class="rev" onclick="event.stopPropagation();kartAdd(${i},'reverse')" title="${t('v_reverse')}">+ REV</button>` : ''}
        <button class="ab-knopf" onclick="event.stopPropagation();ablageDazu(${i})" title="${t('ab_dazu')}" aria-label="${t('ab_dazu')}">⊕</button>
      </div>
    </div>`;
  }).join('');
  if (S.preiseAn) preiseTrefferLaden();
}

/** Preis-Abzeichen auf einer Trefferkachel, sobald der Schalter „Preise" an ist. Vorher
 *  erschienen Preise erst, wenn die Karte im Fach lag — zum Vergleichen kam das zu spät. */
function preisBadge(id) {
  if (!S.preiseAn) return '';
  const p = S.preise[id];
  if (p != null) return `<span class="tk-preis">${p.toFixed(2).replace('.', ',')} €</span>`;
  if (S.preiseAngefragt.has(id) && (id in S.preise)) return `<span class="tk-preis leer" title="${t('kein_preis')}">?</span>`;
  return '';
}
/** Preise der sichtbaren Treffer nachladen — nur die, die noch nie angefragt wurden.
 *  Der Katalog ist nachts bepreist, die Antwort kommt deshalb aus dem Cache. */
async function preiseTrefferLaden() {
  const ids = S.ergebnisse.map((k) => k.id).filter((id) => !S.preiseAngefragt.has(id));
  if (!ids.length) return;
  ids.forEach((id) => S.preiseAngefragt.add(id));
  try {
    const d = await api('api/preise', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids }) });
    ids.forEach((id) => { if (!(id in (d.preise || {}))) S.preise[id] = null; });
    Object.assign(S.preise, d.preise); Object.assign(S.preiseHolo, d.holo || {});
    if (S.preiseAn) zeichneErgebnisse();
  } catch (e) { /* still: die Kacheln bleiben ohne Abzeichen */ }
}

/* ---------- Suchschublade im Planer ----------
   Planer und Suche waren zwei Vollbildseiten: Karte suchen hieß Ansicht wechseln,
   einsortieren hieß zurückwechseln. Die Schublade holt die Suche in den Planer.

   Sie führt keinen eigenen Zustand: dieselbe Suche, dieselben Treffer
   (`S.ergebnisse`) — nur ein zweites Bild davon. Deshalb genügt es, dass
   `zeichneErgebnisse()` am Ende hier vorbeischaut; was die Werkbank zeigt, zeigt die
   Schublade auch, ohne zweiten Netzaufruf und ohne zweite Wahrheit. */
/** Die Auswahlleiste sitzt unten und schwebt über dem, was dort noch steht. */
function plBarSetzen() {
  document.documentElement.style.setProperty('--pl-bar-unten',
    (window.innerWidth < 900 ? 68 : 14) + 'px');
}
window.addEventListener('resize', plBarSetzen);

function kartAdd(i, variant) {
  const k = S.ergebnisse[i];
  if (!k || !S.binder) return;
  kartAddId(k.id, variant);
}

async function alleHinzufuegen(naechsteSeite) {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  if (!S.binder) return;
  const p = filterParams(); p.set('limit', '2000');
  try {
    const d = await api('api/cards/ids?' + p);
    if (!d.ids.length) return;
    if (d.ids.length > 300 && !confirm(d.ids.length + ' ' + t('bestaetigen_viele'))) return;
    merken('alle');
    if (naechsteSeite) {
      const letzte = seiteInfo(Math.max(0, seitenAnzahl() - 1));
      const rest = S.binder.items.length - letzte.start;
      if (rest) for (let x = 0; x < letzte.laenge - rest; x++) S.binder.items.push({ type: 'empty' });
    }
    for (const id of d.ids) S.binder.items.push({ type: 'card', id });
    if (!await binderAnlegenWennNoetig()) return;
    speichern(); zeichneBinder(); zeichneErgebnisse();
    toastUndo(d.ids.length + ' ' + t('hinzugefuegt'));
  } catch (e) { toast(t('fehler_laden')); }
}

// ---------- Binder-Panel ----------
function zeichneLayouts() {
  $('wb-layout').innerHTML = Object.keys(LAYOUTS).map((l) =>
    `<option value="${l}" ${S.binder.layout === l ? 'selected' : ''}>${l.replace('x', '×')} (${LAYOUTS[l][0] * LAYOUTS[l][1]})</option>`).join('');
}
function layoutWechsel(l) { S.binder.layout = l; S.seite = 0; zeichneBinder(); speichern(); }
// Breite der Binder-Übersicht (persistiert). Größer = die Karten in der
// Übersicht werden größer und besser lesbar.
function binderBreite(px) {
  document.documentElement.style.setProperty('--binder-w', px + 'px');
  try { localStorage.setItem('bp_binder_w', px); } catch (e) {}
}
function binderBreiteInit() {
  // Die Binderseite neben der Suche war zu klein (324 px). Standard jetzt 486 px, also
  // anderthalbfach; ein früher gespeicherter kleinerer Wert wird einmalig angehoben.
  let px = parseInt(localStorage.getItem('bp_binder_w') || '0', 10);
  try {
    if (px && px < 486 && !localStorage.getItem('bp_binder_w2')) { px = 486; localStorage.setItem('bp_binder_w', px); }
    localStorage.setItem('bp_binder_w2', '1');
  } catch (e) {}
  const el = $('wb-groesse');
  if (px) { document.documentElement.style.setProperty('--binder-w', px + 'px'); if (el) el.value = px; }
  else if (el) el.value = 486;
}
binderBreiteInit();
function proSeite() { const [c, r] = LAYOUTS[S.binder.layout] || [3, 3]; return c * r; }

/* ---------- Seitenmodell ----------
   Ein Binder hat ein Standardraster, aber einzelne Seiten dürfen davon abweichen — im
   Regal steht ja auch niemand mit lauter gleichen Hüllen. Die Abweichungen liegen in
   `options.seitenLayouts` als {Seitennummer: Raster}; `options` wird ohnehin als freies
   JSON gespeichert, es braucht also keine Schemaänderung.

   Alles, was vorher `seite * proSeite()` gerechnet hat, fragt jetzt den Plan. Er ist die
   einzige Stelle, die weiß, wo eine Seite anfängt und wie groß sie ist. */

function seitenRaster(nr) {
  const je = (S.binder && S.binder.options && S.binder.options.seitenLayouts) || {};
  const l = je[nr];
  return LAYOUTS[l] ? l : ((S.binder && S.binder.layout) || '3x3');
}

/** Der Plan aller Seiten. `mindestens` erzwingt Einträge über das Binderende hinaus —
 *  gebraucht, wenn eine noch nicht vorhandene Seite befüllt werden soll. */
function seitenPlan(mindestens) {
  const items = (S.binder && S.binder.items) || [];
  const plan = [];
  let i = 0, nr = 0;
  do {
    const l = seitenRaster(nr);
    const [c, r] = LAYOUTS[l] || [3, 3];
    plan.push({ nr, start: i, laenge: c * r, cols: c, rows: r, layout: l });
    i += c * r; nr++;
  } while (i < items.length || nr <= (mindestens || 0));
  return plan;
}

/** Eine einzelne Seite, auch wenn sie noch gar nicht befüllt ist. */
function seiteInfo(nr) {
  const plan = seitenPlan(nr);
  return plan[nr] || plan[plan.length - 1];
}
function seitenAnzahl() { return seitenPlan().length; }
/** Zu welcher Seite gehört ein Fach? */
function seiteBei(idx) {
  const plan = seitenPlan();
  for (const p of plan) if (idx < p.start + p.laenge) return p.nr;
  return plan.length - 1;
}
/** Die Fächer einer Seite, in der Länge der Seite (fehlende als undefined). */
function seiteFaecher(nr) {
  const p = seiteInfo(nr);
  return S.binder.items.slice(p.start, p.start + p.laenge);
}


function slotInhalt(item) {
  if (!item) return '+';
  if (item.type === 'empty') return `<span>${t('leer_slot')}</span>`;
  if (item.type === 'art') return artKachel(item);
  if (item.type === 'dex') {
    const p = (S.pokedex || []).find((x) => x.dex === item.dex);
    const name = p ? (LANG === 'en' ? p.name_en : p.name) : (item.name || '#' + item.dex);
    return `<div class="dexzelle"><img loading="lazy" src="api/img/dex/${item.dex}" alt=""><span>#${String(item.dex).padStart(3, '0')} ${name}</span></div>`;
  }
  return `<img loading="lazy" src="${imgUrl(item.id)}" alt="" data-cid="${item.id}" onerror="bildFehlt(this)">`;
}

// Artwork-Fach: Ausschnitt der KI-Seite (Bild in Prozent verschoben – 63×88 mm Fach, 4 mm Fuge)
function artKachel(item) {
  const [cols, rows] = LAYOUTS[item.layout] || [3, 3];
  const pw = cols * 63 + (cols - 1) * 4, ph = rows * 88 + (rows - 1) * 4;
  const c = item.slot % cols, r = Math.floor(item.slot / cols);
  return `<div class="artfach"><img loading="lazy" src="api/artwork/${encodeURIComponent(item.artwork)}/bild?v=vorschau" alt="" style="width:${(pw / 63 * 100).toFixed(3)}%;height:${(ph / 88 * 100).toFixed(3)}%;left:-${(c * 67 / 63 * 100).toFixed(3)}%;top:-${(r * 92 / 88 * 100).toFixed(3)}%"></div>`;
}

function bildFehlt(img) {
  const span = document.createElement('span');
  span.textContent = img.dataset.cid || '?';
  span.style.cssText = 'font-size: var(--t-xs);padding:3px;color:var(--mut);text-align:center;word-break:break-all';
  img.replaceWith(span);
}

function slotExtras(item, idx) {
  if (S.nurAnsicht) {
    let html = '';
    // Wer angemeldet ist und eine Sammlung führt, sieht sofort, welche Karten er schon hat.
    // Das macht aus einem fremden Binder eine Einkaufsliste statt nur einer Galerie.
    if (S.user && item && item.type === 'card') {
      html += `<span class="hat-mark ${besitzt(item) ? 'ja' : ''}">${besitzt(item) ? '✓' : ''}</span>`;
    }
    if (item && VMARK[item.variant]) html += `<span class="vmark">${VMARK[item.variant]}</span>`;
    if (S.preiseAn && item && item.type === 'card') {   // der Preis-Schalter gilt auch in der Nur-Ansicht
      const p = preisFuer(item);
      if (p != null) html += `<span class="preis">${p.toFixed(2).replace('.', LANG === 'de' ? ',' : '.')} €</span>`;
    }
    return html;
  }
  let html = `<button class="raus" onclick="event.stopPropagation();slotEntfernen(${idx})" title="${t('s_entfernen')}">✕</button>
    <button class="mehr" onclick="event.stopPropagation();slotMenue(event,${idx})" title="${t('s_frei_davor')} / ${t('s_details')}">⋯</button>`;
  if (item && item.type === 'card') {
    html += `<button class="hat-btn" title="${t('hat_hilfe')}" onclick="event.stopPropagation();hatToggle(${idx})">✓</button>`;
  }
  if (item && item.type === 'art') html += `<span class="vmark" title="Artwork">${ic('palette', 11)}</span>`;
  // Die Variante ist ab dem 10.09.2026 ein Knopf, kein Aufkleber: ein Klick öffnet die
  // Auswahl der Drucke, die es zu genau dieser Karte gibt. Vorher musste man dafür in
  // den Inspektor — bei einem Reverse-Holo-Set einmal je Karte.
  if (item && item.type === 'card') {
    const v = item.variant || 'normal';
    html += `<button class="vmark vbtn ${v === 'normal' ? 'still' : ''}" title="${t('v_' + v)} – ${t('variante')}"
      onclick="event.stopPropagation();variantenMenue(event,${idx})">${VMARK[v] || t('v_normal_kurz')}</button>`;
  } else if (item && VMARK[item.variant]) {
    html += `<span class="vmark" title="${t('v_' + item.variant)}">${VMARK[item.variant]}</span>`;
  }
  if (item && item.etiketten && item.etiketten.length) {
    html += `<span class="etiketten">${item.etiketten.slice(0, 3).map((e) =>
      `<span class="etikett" style="background:${esc(e.farbe || '#f5c518')}" title="${esc(e.text)}">${esc(e.text)}</span>`).join('')}</span>`;
  }
  if (item && item.sprache && item.sprache !== 'de') html += `<span class="vmark spmark" title="${t('kartensprache')}">${item.sprache.toUpperCase()}</span>`;
  if (S.preiseAn && item && item.type === 'card') {
    const p = preisFuer(item);
    if (p != null) html += `<span class="preis">${p.toFixed(2).replace('.', LANG === 'de' ? ',' : '.')} €</span>`;
  }
  return html;
}

async function hatToggle(idx) {
  const item = S.binder.items[idx];
  if (!item || item.type === 'art') return;
  if (!S.user) {                      // ohne Konto bleibt das Häkchen im Binder
    item.have = !item.have;
    speichern();
    zeichneBinder();
    return;
  }
  const vorher = !!S.besitz[item.id];
  if (vorher) delete S.besitz[item.id]; else S.besitz[item.id] = 1;   // sofort sichtbar
  zeichneBinder();
  try {
    const d = await api('api/sammlung/toggle', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ card_id: item.id, variante: item.variant || 'normal' }),
    });
    if (d.besitze) S.besitz[item.id] = S.besitz[item.id] || 1; else delete S.besitz[item.id];
  } catch (e) {
    if (vorher) S.besitz[item.id] = 1; else delete S.besitz[item.id];
    zeichneBinder();
    if (!gate(e)) toast(e.message);
  }
}

/* Ein Fach — dieselbe Zeichnung, egal ob eine Seite groß oder alle nebeneinander stehen.
   Vorher gab es dafür drei Funktionen (zeichneBinder, zeichnePlaner, zeichneGalerie), und
   ein Klick bedeutete in der einen „Haken setzen" und in der anderen „auswählen". */
function fachHtml(idx) {
  const item = S.binder.items[idx];
  if (!item) {
    return S.nurAnsicht ? '<div class="slot">+</div>'
      : `<div class="slot frei" data-idx="${idx}" onclick="freiesFach(${idx})" ondragover="dragOver(event)"
              ondragleave="dragLeave(event)" ondrop="dragDrop(event)" title="${t('s_frei_danach')}">+</div>`;
  }
  const dnd = S.nurAnsicht ? '' : 'draggable="true" ondragstart="dragStart(event)" ondragover="dragOver(event)"'
    + ' ondragleave="dragLeave(event)" ondrop="dragDrop(event)"'
    + ' ontouchstart="touchStart(event)" ontouchmove="touchMove(event)" ontouchend="touchEnde()" ontouchcancel="touchEnde()"';
  const klick = S.nurAnsicht ? '' : `onclick="fachKlick(event,${idx})"`;
  return `<div class="slot ${item.type === 'empty' ? 'leer-item' : 'voll'} ${item.type === 'art' ? 'art' : ''}
      ${besitzt(item) ? 'hat' : ''} ${S.auswahl.has(idx) ? 'gewaehlt' : ''}" ${dnd} data-idx="${idx}" ${klick}>
    ${slotInhalt(item)}${slotExtras(item, idx)}</div>`;
}

/** Eine Seite mit Kopfzeile und Fächern. `werkzeuge` blendet die Seitenknöpfe ein. */
function seiteHtml(sp, seiten, werkzeuge) {
  let faecher = '';
  for (let i = 0; i < sp.laenge; i++) faecher += fachHtml(sp.start + i);
  const titel = seitenTitel(sp.nr);
  const kopf = werkzeuge ? `<div class="ps-titel"><span>${t('seite')} ${sp.nr + 1}${titel ? ' · ' + esc(titel) : ''}</span>
      <span class="ps-akt">
        <button onclick="artworkOeffnen(${sp.nr})" title="${t('aw_t')}">${ic('palette', 16)}</button>
        <button onclick="seiteWaehlen(${sp.nr})" title="${t('s_seite_wahl')}">${ic('hakenKasten', 16)}</button>
        <button onclick="seitenTitelFragen(${sp.nr})" title="${t('st_titel_leer')}">${titel ? '✎' : 'T'}</button>
        <button onclick="seiteVerschieben(${sp.nr},-1)" title="${t('s_seite_hoch')}" ${sp.nr === 0 ? 'disabled' : ''}>↑</button>
        <button onclick="seiteVerschieben(${sp.nr},1)" title="${t('s_seite_runter')}" ${sp.nr === seiten - 1 ? 'disabled' : ''}>↓</button>
        <button onclick="seiteLeeresFach(${sp.nr})" title="${t('s_seite_fach')}">＋</button>
        <button onclick="seiteEntfernen(${sp.nr})" title="${t('s_seite_weg')}">✕</button>
      </span></div>` : '';
  return `<div class="planer-seite">${kopf}
    <div class="slots" style="grid-template-columns:repeat(${sp.cols},minmax(0,1fr))">${faecher}</div></div>`;
}

/** Alle Seiten nebeneinander — der Zustand „Alle Seiten" der Binderansicht. */
function zeichneAlleSeiten() {
  const box = $('wb-alle');
  if (!box || !S.binder) return;
  const plan = seitenPlan();
  // Ohne Karten gab es hier nur Weiß – der Gast hatte gerade „Binder" getippt. Jetzt derselbe
  // Einstieg wie in „Eine Seite": Erklärung und Vorlagen.
  if (!S.binder.items.length && !S.nurAnsicht) {
    box.innerHTML = `<div class="onb" style="max-width:560px;margin:12px auto">${$('wb-onb').innerHTML}</div>`;
    auswahlAktionen();
    return;
  }
  box.innerHTML = plan.map((sp) => S.nurAnsicht && seitenTitel(sp.nr)
    ? `<div class="ps-nur-titel">${esc(seitenTitel(sp.nr))}</div>` + seiteHtml(sp, plan.length, false)
    : seiteHtml(sp, plan.length, !S.nurAnsicht)).join('');
  auswahlAktionen();
}

function zeichneBinder() {
  if (!S.binder) return;
  const plan = seitenPlan();
  const seiten = plan.length;
  if (S.seite >= seiten) S.seite = seiten - 1;
  const sp = plan[Math.max(0, S.seite)] || plan[0];
  const pp = sp.laenge, cols = sp.cols;
  $('wb-seite').textContent = S.seite + 1;
  $('wb-seiten').textContent = seiten;
  const druck = S.binder.items.filter((x) => x.type !== 'empty').length;
  $('wb-fortschritt').textContent = S.binder.items.length ? `${S.binder.items.length} ${t('faecher')} · ${seiten} ${t('seiten')} · ${Math.ceil(druck / 9)} ${t('blaetter')}` : t('binder_leer');
  const titel = $('wb-titel');
  if (titel) {
    titel.textContent = S.binder.name || t('neuer_binder');
    // Umgekehrt seit dem 04.09.2026: nicht der Normalfall wird beschriftet, sondern die
    // Ausnahme. Steht der Binder auf der Wunschliste, sagt er das — sonst wundert man
    // sich, warum seine Karten unter „Wunschliste" auftauchen.
    if (S.binder.options && S.binder.options.wants === true) {
      const m = document.createElement('span');
      m.className = 'bp-nowants an'; m.textContent = '★ ' + t('wl_titel');
      titel.appendChild(m);
    }
  }
  const zahl = $('mnav-binder-zahl');
  if (zahl) zahl.textContent = S.binder.items.length;
  const box = $('wb-slots');
  box.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;
  let html = '';
  for (let i = 0; i < pp; i++) html += fachHtml(sp.start + i);
  box.innerHTML = html;
  // Leerer Binder: Einstieg erklären und die Vorlagen zeigen – das Raster erst ab der ersten Karte
  const leer = !S.nurAnsicht && S.binder.items.length === 0;
  $('wb-onb').classList.toggle('hidden', !leer);
  $('wb-slots').classList.toggle('hidden', !!(leer || S.alleSeiten));
  zeichneStreifen();
  // Die neuen Bedienelemente hängen am Binderzustand, nicht am Ereignis, das sie
  // geändert hat — deshalb hier, an der einen Stelle, die immer läuft.
  if (typeof historieKnoepfe === 'function') historieKnoepfe();
  if (typeof ziehModusZeigen === 'function') ziehModusZeigen();
  if (typeof ablageZeigen === 'function') ablageZeigen();
  if (typeof seitenTitelZeigen === 'function') seitenTitelZeigen();
  if (typeof gastEinstiegZeigen === 'function') gastEinstiegZeigen();
  $('wb-alle').classList.toggle('hidden', !!(leer || !S.alleSeiten));
  $('wb-nav').classList.toggle('hidden', !!leer);
  if (S.alleSeiten && !leer) zeichneAlleSeiten();
  auswahlAktionen();
  // Sammel-Fortschritt
  const sammelbar = S.binder.items.filter((i) => i.type !== 'empty' && i.type !== 'art').length;
  const gesammelt = S.binder.items.filter((i) => besitzt(i)).length;
  $('wb-sammel').classList.toggle('hidden', sammelbar === 0);
  if (sammelbar) {
    $('wb-sammel-zahl').textContent = `${gesammelt} / ${sammelbar}`;
    $('wb-sammel-balken').style.width = Math.round(gesammelt / sammelbar * 100) + '%';
  }
  restkostenZeigen();
  seitenfarbeAnwenden();
  const seitenZahl = seiten;
  if ($('wb-seite-vor')) $('wb-seite-vor').disabled = S.nurAnsicht || S.seite <= 0;
  if ($('wb-seite-zurueck')) $('wb-seite-zurueck').disabled = S.nurAnsicht || S.seite >= seitenZahl - 1;
  zeichnePreisSumme();
  zeichneBinderUmschalter();
  zeichneSeitenleiste();
  if (S.binder.items.some((i) => i.type === 'dex') && !S.pokedex) ladePokedex().then(zeichneBinder);
}

// ---------- Durchblättern ----------
// Zeigt den Binder aufgeschlagen: zwei Seiten nebeneinander, dazwischen die
// Ringe. Die Fächer zeichnet `slotInhalt()` — dieselbe Funktion wie in der
// Werkbank, damit Vorschau und Arbeitsansicht nie auseinanderlaufen.
const BV = { spread: 0, dreht: false, wartet: 0 };

function bvSeitenZahl() { return S.binder ? seitenAnzahl() : 1; }
// Wie im echten Album: Seite 1 liegt allein rechts, danach folgen Doppelseiten
// (2|3, 4|5 …). Geht die Seitenzahl gerade auf, steht die letzte Seite allein links.
function bvSpreads() { return 1 + Math.ceil(Math.max(0, bvSeitenZahl() - 1) / 2); }
function bvLinks(s) { return s === 0 ? -1 : s * 2 - 1; }
function bvRechts(s) { return s === 0 ? 0 : s * 2; }

/** Eine Binderseite als HTML. `seite` ist zwölfnullbasiert; außerhalb = leeres Blatt. */
function bvSeiteHtml(seite, klasse) {
  const [cols] = LAYOUTS[S.binder.layout] || [3, 3];
  if (seite < 0 || seite >= bvSeitenZahl()) return `<div class="bv-seite leer ${klasse}"></div>`;
  const sp = seiteInfo(seite), pp = sp.laenge;
  const raster = `grid-template-columns:repeat(${cols},minmax(0,1fr))`;
  const faecher = S.binder.items.slice(seite * pp, seite * pp + pp);

  const fach = (item) => {
    if (!item) return '<div class="bv-slot frei"></div>';
    let preis = '';
    if (S.preiseAn && item.type === 'card') {
      const p = preisFuer(item);
      if (p != null) preis = `<span class="bv-preis">${p.toFixed(2).replace('.', LANG === 'de' ? ',' : '.')} €</span>`;
    }
    return `<div class="bv-slot">${slotInhalt(item)}${preis}</div>`;
  };

  // Eine KI-Seite ist EIN Bild, das über alle Fächer läuft. In der Werkbank wird
  // es je Fach ausgeschnitten — hier liegt es am Stück darunter, und darüber nur
  // die Fächer, in denen wirklich eine Karte steckt. Das ist näher am echten
  // Album und spart acht Vergrößerungen desselben Bildes, die das Umblättern
  // spürbar verzögert haben.
  const arts = faecher.filter((it) => it && it.type === 'art');
  const durchgehend = arts.length >= 2
    && arts.every((it) => it.artwork === arts[0].artwork)
    && faecher.every((it, i) => !it || it.type !== 'art' || it.slot === i);
  if (durchgehend) {
    const zellen = faecher.map((it, i) => (it && it.type === 'art' ? '<i></i>' : fach(it))).join('')
      + '<i></i>'.repeat(Math.max(0, pp - faecher.length));
    return `<div class="bv-seite ganzseite ${klasse}" style="${raster}">
      <img loading="eager" decoding="async" src="api/artwork/${encodeURIComponent(arts[0].artwork)}/bild?v=vorschau" alt="">
      <div class="bv-gitter" style="${raster}">${zellen}</div></div>`;
  }

  let html = '';
  for (let i = 0; i < pp; i++) html += fach(S.binder.items[seite * pp + i]);
  return `<div class="bv-seite ${klasse}" style="${raster}">${html}</div>`;
}

/** Die Seiten eines Nachbar-Spreads schon fertig bauen und ins unsichtbare Lager
 *  hängen. Beim Umblättern ist dann nichts mehr zu rechnen. */
function bvVorladen(spread) {
  if (!S.binder || spread < 0 || spread >= bvSpreads()) return;
  const lager = $('bv-lager');
  if (!lager) return;
  for (const seite of [bvLinks(spread), bvRechts(spread)]) {
    if (seite < 0 || seite >= bvSeitenZahl()) continue;
    const el = bvSeiteEl(seite, seite === bvLinks(spread) ? 'links' : 'rechts');
    if (!el.isConnected) lager.append(el);      // im Buch liegende Seiten nicht wegziehen
  }
}

// Jede Seite wird genau einmal gebaut und danach nur noch verschoben. Das ist der
// Unterschied zwischen einem Blatt, das sofort umschlägt, und einem, das erst eine
// Drittelsekunde steht: neu erzeugte <img> müssen dekodiert und gerastert werden.
const bvSeiten = new Map();

function bvSeiteEl(seite, klasse) {
  const schluessel = (seite < 0 || seite >= bvSeitenZahl()) ? 'leer:' + klasse : seite;
  let el = bvSeiten.get(schluessel);
  if (!el) {
    const huelle = document.createElement('div');
    huelle.innerHTML = bvSeiteHtml(seite, '');
    el = huelle.firstElementChild;
    el.insertAdjacentHTML('beforeend', '<i class="bv-schatten"></i>');
    bvSeiten.set(schluessel, el);
  }
  // Die Seite behält, was sie ist (Karten- oder Artwork-Seite), und bekommt neu
  // gesagt, wo sie gerade liegt — links, rechts oder als Rückseite des Blattes.
  const bleibt = ['bv-seite', el.classList.contains('ganzseite') && 'ganzseite'].filter(Boolean);
  el.className = [...bleibt, klasse, typeof schluessel === 'string' ? 'leer' : ''].join(' ').trim();
  return el;
}

function bvRueckenEl() {
  let el = bvSeiten.get('ruecken');
  if (!el) {
    el = document.createElement('div');
    el.className = 'bv-ruecken';
    el.innerHTML = '<i class="bv-ring"></i><i class="bv-ring"></i><i class="bv-ring"></i>';
    bvSeiten.set('ruecken', el);
  }
  return el;
}

/** Hochkant am Handy ist eine Doppelseite 350 px breit — die Karten wären Briefmarken.
 *  Dort wird nur eine Seite gezeigt; das Wischen blättert weiter wie gehabt. */
function bvEinzeln() { return window.innerWidth < 700 && window.innerHeight > window.innerWidth; }

function bvZeichne() {
  const [c, r] = LAYOUTS[S.binder.layout] || [3, 3];
  $('blaettern').style.setProperty('--bv-ratio', ((2 * (c * 63 + (c - 1) * 6 + 24) + 26) / (r * 88 + (r - 1) * 6 + 24)).toFixed(3));
  const links = bvLinks(BV.spread), rechts = bvRechts(BV.spread);
  if (bvEinzeln()) {
    const nr = rechts >= 0 && rechts < bvSeitenZahl() ? rechts : links;
    $('bv-buch').replaceChildren(bvSeiteEl(nr, 'rechts'));
  } else {
    $('bv-buch').replaceChildren(bvSeiteEl(links, 'links'), bvRueckenEl(), bvSeiteEl(rechts, 'rechts'));
  }
  const gesamt = bvSeitenZahl();
  const sichtbar = [links, rechts].filter((n) => n >= 0 && n < gesamt).map((n) => n + 1);
  $('bv-zaehler').textContent = sichtbar.length === 2
    ? `${t('seite')} ${sichtbar[0]}–${sichtbar[1]} / ${gesamt}`
    : `${t('seite')} ${sichtbar[0] || 1} / ${gesamt}`;
  $('bv-zurueck').disabled = BV.spread <= 0;
  $('bv-vor').disabled = BV.spread >= bvSpreads() - 1;
  const nachbarn = () => { bvVorladen(BV.spread + 1); bvVorladen(BV.spread - 1); };
  window.requestIdleCallback ? requestIdleCallback(nachbarn, { timeout: 900 }) : setTimeout(nachbarn, 120);
}

/**
 * Umblättern mit echtem Blattwechsel: ein Blatt legt sich über die
 * aufgeschlagene Seite und dreht sich um den Rücken. Vorne die Seite, die man
 * verlässt, hinten die, auf der man landet.
 */
function blaetternWechsel(d) {
  // Wer schnell blättert, klickt in die laufende Drehung hinein. Statt den Klick
  // zu verschlucken, wird er gemerkt und direkt danach ausgeführt.
  if (BV.dreht) { BV.wartet = d; return; }
  const ziel = BV.spread + d;
  if (ziel < 0 || ziel >= bvSpreads()) return;
  const wenigerBewegung = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (wenigerBewegung) { BV.spread = ziel; bvZeichne(); return; }

  const buch = $('bv-buch');
  // Vorne die Seite, die weggeht — hinten die, auf der man landet, darunter die,
  // die dabei zum Vorschein kommt. Alle drei liegen fertig im Speicher.
  const vorneEl = bvSeiteEl(d > 0 ? bvRechts(BV.spread) : bvLinks(BV.spread), (d > 0 ? 'rechts' : 'links') + ' vorn');
  const b = vorneEl.getBoundingClientRect();
  const wrapB = buch.parentElement.getBoundingClientRect();
  if (!b.width) { BV.spread = ziel; bvZeichne(); return; }

  const hintenEl = bvSeiteEl(d > 0 ? bvLinks(ziel) : bvRechts(ziel), 'rueck ' + (d > 0 ? 'links' : 'rechts'));
  const untenEl = bvSeiteEl(d > 0 ? bvRechts(ziel) : bvLinks(ziel), d > 0 ? 'rechts' : 'links');
  const bleibtEl = bvSeiteEl(d > 0 ? bvLinks(BV.spread) : bvRechts(BV.spread), d > 0 ? 'links' : 'rechts');

  const blatt = document.createElement('div');
  blatt.className = 'bv-blatt ' + (d > 0 ? 'vor' : 'zurueck');
  blatt.style.left = (b.left - wrapB.left) + 'px';
  blatt.style.width = b.width + 'px';
  blatt.style.transformOrigin = d > 0 ? 'left center' : 'right center';

  // Reihenfolge ist hier alles: Erst das Blatt in Bewegung setzen, dann das Buch
  // darunter umbauen. Eine reine transform-Animation läuft im Compositor und
  // stört sich nicht daran, dass der Hauptthread danach Seiten rastert — umgekehrt
  // stand das Blatt eine Drittelsekunde still, bevor es losging.
  blatt.append(vorneEl);
  buch.parentElement.append(blatt);
  blatt.getBoundingClientRect();          // Ausgangswert festschreiben
  BV.dreht = true;
  blatt.classList.add('laeuft');
  blatt.style.transform = `rotateY(${d > 0 ? -180 : 180}deg)`;

  // Alles Weitere im nächsten Bild: die Rückseite wird erst ab 90° sichtbar, und
  // die aufgedeckte Seite verdeckt das Blatt zu Beginn ohnehin.
  const wisch = document.createElement('i');
  wisch.className = 'bv-wisch';
  // Bewusst ein kleiner Abstand statt des nächsten Bildes: der Umbau kostet Rechenzeit,
  // und wenn er im selben Bild wie der Start liegt, verzögert er genau den ersten
  // Moment der Drehung. Bis 90° (rund ein Drittel der Zeit) sieht man davon nichts.
  setTimeout(() => {
    blatt.append(hintenEl);
    buch.replaceChildren(...(d > 0 ? [bleibtEl, bvRueckenEl(), untenEl] : [untenEl, bvRueckenEl(), bleibtEl]));
    untenEl.append(wisch);
    wisch.classList.add('laeuft');
  }, 80);
  // Die Abschlussmarke gehört zu DIESER Drehung, nicht zum Gesamtzustand: sonst
  // räumt der Notausgang der ersten Drehung in die zweite hinein, wenn schnell
  // hintereinander geblättert wird.
  let erledigt = false;
  const fertig = () => {
    if (erledigt) return;
    erledigt = true;
    clearTimeout(notausgang);
    wisch.remove();
    blatt.remove();
    BV.spread = ziel;
    BV.dreht = false;
    bvZeichne();
    if (BV.wartet) { const w = BV.wartet; BV.wartet = 0; blaetternWechsel(w); }
  };
  blatt.addEventListener('transitionend', fertig, { once: true });
  const notausgang = setTimeout(fertig, 1200);   // falls kein transitionend kommt
}

function blaetternOeffnen() {
  if (!S.binder || !S.binder.items.length) return toast(t('binder_leer'));
  bvSeiten.clear();
  BV.dreht = false; BV.wartet = 0;
  BV.spread = S.seite <= 0 ? 0 : Math.floor((S.seite + 1) / 2);
  $('bv-titel').textContent = t('bv_titel');
  $('bv-untertitel').textContent = S.binder.name || '';
  $('blaettern').classList.remove('hidden');
  ebeneOeffnen(blaetternZu);
  document.body.style.overflow = 'hidden';
  bvZeichne();
  bvVorladen(BV.spread + 1);
}

function blaetternSchliessen() { ebeneZu(blaetternZu); }
function blaetternZu() {
  if ($('blaettern').classList.contains('hidden')) return;
  $('blaettern').classList.add('hidden');
  document.body.style.overflow = '';
  // Dort weiterarbeiten, wo man aufgehört hat zu blättern
  S.seite = Math.min(Math.max(0, bvLinks(BV.spread) < 0 ? 0 : bvLinks(BV.spread)), bvSeitenZahl() - 1);
  zeichneBinder();
}

document.addEventListener('keydown', (e) => {
  if ($('blaettern').classList.contains('hidden')) return;
  if (e.key === 'Escape') { e.preventDefault(); blaetternSchliessen(); }
  if (e.key === 'ArrowRight') { e.preventDefault(); blaetternWechsel(1); }
  if (e.key === 'ArrowLeft') { e.preventDefault(); blaetternWechsel(-1); }
});

// Wischen auf dem Handy
(function () {
  let x0 = null;
  const el = () => $('blaettern');
  document.addEventListener('touchstart', (e) => { if (!el().classList.contains('hidden')) x0 = e.touches[0].clientX; }, { passive: true });
  document.addEventListener('touchend', (e) => {
    if (x0 === null || el().classList.contains('hidden')) return;
    const dx = e.changedTouches[0].clientX - x0;
    x0 = null;
    if (Math.abs(dx) > 55) blaetternWechsel(dx < 0 ? 1 : -1);
  }, { passive: true });
})();

// ---------- Seite nach Thema ----------
// Sucht Karten nach dem, was ihr Artwork zeigt. Die Beschreibungen dazu liegen
// serverseitig (Modul themen.py); hier wird nur gefragt und eingesetzt.
const TH = { anker: null, vorschlag: [], raus: new Set(), titel: '' };

const TH_CHIPS_DE = ['Pokémon unter Wasser', 'Stadt bei Nacht', 'Verschneiter Wald', 'Sonnenuntergang',
                     'Weltraum & Sterne', 'Vulkan und Lava', 'Herbstlaub', 'Am Strand', 'Regen',
                     'Kirschblüten', 'Alte Ruinen', 'Gemütlich zu Hause'];
const TH_CHIPS_EN = ['Pokémon underwater', 'City at night', 'Snowy forest', 'Sunset',
                     'Space & stars', 'Volcano and lava', 'Autumn leaves', 'At the beach', 'Rain',
                     'Cherry blossoms', 'Ancient ruins', 'Cosy at home'];
function thChips() { return LANG === 'en' ? TH_CHIPS_EN : TH_CHIPS_DE; }

function themaOeffnen(ankerId) {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  if (!S.binder || S.nurAnsicht) return;
  TH.anker = ankerId || null;
  TH.vorschlag = []; TH.raus.clear();
  $('th-ergebnis').classList.add('hidden');
  $('th-fehler').textContent = '';
  $('th-anzahl').value = Math.max(1, seiteInfo(S.seite || 0).laenge);
  $('th-chips').innerHTML = thChips().map((c) => `<button class="chip" onclick="$('th-thema').value=${JSON.stringify(c)};themaSuchen()">${esc(c)}</button>`).join('');
  themaScopeFuellen();
  themaAnkerZeichnen();
  modalOeffnen('modal-thema');
  themaAbdeckung();
  if (!ankerId) setTimeout(() => $('th-thema').focus(), 60);
}

function themaAnkerZeichnen() {
  const an = $('th-anker');
  an.classList.toggle('hidden', !TH.anker);
  if (!TH.anker) return;
  $('th-anker-bild').src = imgUrl(TH.anker);
  $('th-anker-name').textContent = TH.anker;
  api('api/cards/' + encodeURIComponent(TH.anker) + '/detail').then((d) => {
    $('th-anker-name').textContent = (LANG === 'en' ? d.name_en : d.name) || TH.anker;
    $('th-anker-set').textContent = (LANG === 'en' ? d.set_name_en : d.set_name) || '';
  }).catch(() => {});
}
function themaAnkerWeg() { TH.anker = null; themaAnkerZeichnen(); }

function themaScopeFuellen() {
  const opts = [`<option value="alle">${t('th_scope_alle')}</option>`];
  const set = S.binder.options && S.binder.options.set;
  if (set) opts.push(`<option value="set:${esc(set)}">${t('th_scope_set')}</option>`);
  if (S.binder.id) opts.push(`<option value="binder:${esc(S.binder.id)}">${t('th_scope_binder')}</option>`);
  $('th-scope').innerHTML = opts.join('');
}

async function themaAbdeckung() {
  try {
    const d = await api('api/themen/status?scope=' + encodeURIComponent($('th-scope').value || 'alle'));
    const anteil = d.gesamt ? Math.round(d.gesichtet / d.gesamt * 100) : 0;
    $('th-abdeckung').textContent = t('th_abdeckung')
      .replace('{n}', d.gesichtet.toLocaleString(LANG === 'en' ? 'en' : 'de'))
      .replace('{g}', d.gesamt.toLocaleString(LANG === 'en' ? 'en' : 'de'))
      .replace('{p}', anteil);
  } catch (e) { $('th-abdeckung').textContent = ''; }
}

async function themaSuchen(nochmal) {
  const thema = $('th-thema').value.trim();
  if (!thema && !TH.anker) { $('th-thema').focus(); return toast(t('th_kein_thema')); }
  const laeuft = $('th-lauf');
  laeuft.classList.remove('hidden');
  $('th-los').disabled = true;
  $('th-fehler').textContent = '';
  try {
    // „Andere Vorschläge“ heißt: dieselbe Suche, aber ohne die schon gezeigten Karten
    const ohne = nochmal ? TH.vorschlag.map((k) => k.id) : [];
    const d = await api('api/themen/plan', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ thema, anker: TH.anker || '', anzahl: Math.max(1, Math.min(16, +$('th-anzahl').value || 9)),
                             scope: $('th-scope').value || 'alle', ohne }),
    });
    TH.vorschlag = d.karten || [];
    TH.raus.clear();
    TH.titel = d.titel || thema;
    themaErgebnisZeichnen(d);
  } catch (e) {
    if (!gate(e)) $('th-fehler').textContent = e.message || String(e);
  } finally {
    laeuft.classList.add('hidden');
    $('th-los').disabled = false;
  }
}

function themaErgebnisZeichnen(d) {
  $('th-ergebnis').classList.remove('hidden');
  $('th-erg-titel').textContent = TH.titel;
  $('th-erg-zahl').textContent = t('th_gefunden').replace('{n}', TH.vorschlag.length);
  if (!TH.vorschlag.length) {
    $('th-grid').innerHTML = `<div class="unter" style="grid-column:1/-1">${t('th_nichts')}</div>`;
    return;
  }
  $('th-grid').innerHTML = TH.vorschlag.map((k, i) => `
    <div class="th-karte" id="th-k${i}" title="${esc(k.szene || '')}">
      <button class="th-weg" onclick="themaRaus(${i})" title="${t('th_raus')}">✕</button>
      <img loading="lazy" src="${imgUrl(k.id)}" alt="">
      <div class="th-text">
        <div class="th-name">${esc(k.name || k.id)}</div>
        <div class="th-set">${esc(k.set || '')}</div>
        ${k.grund ? `<div class="th-grund">${esc(k.grund)}</div>` : ''}
      </div>
    </div>`).join('');
}

function themaRaus(i) {
  if (TH.raus.has(i)) TH.raus.delete(i); else TH.raus.add(i);
  $('th-k' + i).classList.toggle('raus', TH.raus.has(i));
  $('th-erg-zahl').textContent = t('th_gefunden').replace('{n}', TH.vorschlag.length - TH.raus.size);
}

function themaUebernehmen(modus) {
  const karten = TH.vorschlag.filter((_, i) => !TH.raus.has(i)).map((k) => ({ type: 'card', id: k.id }));
  if (!karten.length) return toast(t('th_nichts_gewaehlt'));
  merken('thema');
  const letzteS = seiteInfo(Math.max(0, seitenAnzahl() - 1));
  const pp = letzteS.laenge;
  if (modus === 'anhaengen') {
    while (S.binder.items.length > letzteS.start && (S.binder.items.length - letzteS.start) % pp !== 0) S.binder.items.push({ type: 'empty' });
    S.binder.items.push(...karten);
    S.seite = Math.floor((S.binder.items.length - 1) / pp);
  } else {
    const start = S.seite * pp;
    while (S.binder.items.length < start + pp) S.binder.items.push({ type: 'empty' });
    // Belegte Fächer bleiben stehen. Vorher ersetzte „Auf diese Seite“ die ganze Seite —
    // wer eine halb gefüllte Seite ergänzen wollte, verlor die Karten, die schon drin waren.
    const belegt = S.binder.items.slice(start, start + pp).filter((i) => i && i.type !== 'empty').length;
    if (belegt && !confirm(t('th_ueberschreiben').replace('{n}', belegt))) return;
    S.binder.items.splice(start, pp, ...karten.slice(0, pp),
      ...Array.from({ length: Math.max(0, pp - karten.length) }, () => ({ type: 'empty' })));
  }
  speichern(); zeichneBinder(); zeichneErgebnisse();
  modalSchliessen();
  toastUndo(t('th_uebernommen'));
}

// ---------- Seitenfarbe ----------
// Sammelalben sind fast immer schwarz — deshalb ist Dunkel die Voreinstellung.
// Die Wahl hängt am Binder (options.seitenfarbe) und betrifft nur die Ansicht,
// nicht den Druck.
function seitenfarbeAktiv() {
  return ((S.binder && S.binder.options && S.binder.options.seitenfarbe) || 'dunkel') === 'dunkel';
}
function seitenfarbeAnwenden() {
  document.body.classList.toggle('dunkle-seiten', !!S.binder && seitenfarbeAktiv());
  const box = $('wb-seitenfarbe');
  if (box) box.innerHTML = [['dunkel', t('sf_dunkel')], ['hell', t('sf_hell')]]
    .map(([w, l]) => `<button class="chip ${seitenfarbeAktiv() === (w === 'dunkel') ? 'on' : ''}" onclick="seitenfarbeSetzen('${w}')">${l}</button>`).join('');
}
function seitenfarbeSetzen(wert) {
  if (!S.binder) return;
  S.binder.options = S.binder.options || {};
  S.binder.options.seitenfarbe = wert;
  seitenfarbeAnwenden();
  speichern();
}

/** Die gerade offene Seite als Ganzes nach vorne (-1) oder hinten (+1) schieben.
 *  Im Planer geht das schon über die Pfeile je Seite — beim Fotoimport landet man
 *  aber in der Werkbank und will die Reihenfolge dort geradeziehen. */
function seiteZiehen(d) {
  if (!S.binder || S.nurAnsicht) return;
  const seiten = seitenAnzahl();
  const ziel = S.seite + d;
  if (ziel < 0 || ziel >= seiten) return;
  seiteVerschieben(S.seite, d);
  S.seite = ziel;                       // mit der Seite mitwandern
  zeichneBinder();
  toastUndo(d < 0 ? t('s_seite_hoch') : t('s_seite_runter'));
}

// ---------- Binder aus Fotos ----------
// Die Karten werden nicht gelesen, sondern über ihren Bildfingerabdruck erkannt
// (Modul fotoimport.py). Das Modell sagt nur, wo auf dem Foto Karten liegen.
const FI = { fotos: [], seiten: [], laeuft: false };

function fotoOeffnen() {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  FI.fotos = []; FI.seiten = [];
  fotoSchritt(1);
  zeichneFotoListe();
  modalOeffnen('modal-foto');
  api('api/import/status').then((d) => {
    const anteil = d.gesamt ? Math.round(d.fingerabdruecke / d.gesamt * 100) : 0;
    $('fi-abdeckung').textContent = anteil >= 98 ? '' :
      t('foto_index').replace('{p}', anteil).replace('{n}', (d.fingerabdruecke || 0).toLocaleString(LANG === 'en' ? 'en' : 'de'));
  }).catch(() => {});
}
function fotoSchliessen() { if (!FI.laeuft) modalSchliessen(); }
function fotoSchritt(n) {
  for (const i of [1, 2, 3]) $('fi-schritt' + i).classList.toggle('hidden', i !== n);
}
function fotoZurueck() { FI.seiten = []; fotoSchritt(1); }

function fotoDateien(dateien) {
  for (const f of dateien) {
    if (!f.type.startsWith('image/')) continue;
    FI.fotos.push({ datei: f, url: URL.createObjectURL(f) });
  }
  $('fi-datei').value = '';
  zeichneFotoListe();
}
function fotoWeg(i) { URL.revokeObjectURL(FI.fotos[i].url); FI.fotos.splice(i, 1); zeichneFotoListe(); }
function fotoSchieben(i, d) {
  const j = i + d;
  if (j < 0 || j >= FI.fotos.length) return;
  [FI.fotos[i], FI.fotos[j]] = [FI.fotos[j], FI.fotos[i]];
  zeichneFotoListe();
}
function zeichneFotoListe() {
  $('fi-liste').innerHTML = FI.fotos.map((f, i) => `
    <div class="fi-foto">
      <span class="fi-nr">${i + 1}</span>
      <img src="${f.url}" alt="">
      <div class="fi-akt">
        <button onclick="fotoSchieben(${i},-1)" ${i === 0 ? 'disabled' : ''} title="${t('foto_vor')}">←</button>
        <button onclick="fotoSchieben(${i},1)" ${i === FI.fotos.length - 1 ? 'disabled' : ''} title="${t('foto_zurueck')}">→</button>
        <button onclick="fotoWeg(${i})" title="${t('entfernen')}">✕</button>
      </div>
    </div>`).join('');
  $('fi-los').disabled = !FI.fotos.length;
}

async function fotoErkennen() {
  if (!FI.fotos.length || FI.laeuft) return;
  FI.laeuft = true; FI.seiten = [];
  fotoSchritt(2);
  const layout = (S.binder && S.binder.layout) || '3x3';
  try {
    for (let i = 0; i < FI.fotos.length; i++) {
      $('fi-fortschritt').innerHTML = lader('', t('foto_lauf').replace('{i}', i + 1).replace('{n}', FI.fotos.length));
      $('fi-balken').style.width = Math.round(i / FI.fotos.length * 100) + '%';
      const fd = new FormData();
      fd.append('datei', FI.fotos[i].datei);
      const d = await api('api/import/foto?layout=' + encodeURIComponent(layout), { method: 'POST', body: fd });
      FI.seiten.push(d.karten || []);
    }
    $('fi-balken').style.width = '100%';
    zeichneFotoErgebnis();
    fotoSchritt(3);
  } catch (e) {
    fotoSchritt(1);
    if (!gate(e)) toast(e.message || String(e));
  } finally {
    FI.laeuft = false;
  }
}

function zeichneFotoErgebnis() {
  const gesamt = FI.seiten.reduce((s, k) => s + k.length, 0);
  const unsicher = FI.seiten.reduce((s, k) => s + k.filter((x) => !x.sicher).length, 0);
  $('fi-bilanz').textContent = t('foto_bilanz').replace('{n}', gesamt).replace('{s}', FI.seiten.length)
    + (unsicher ? ' ' + t('foto_unsicher').replace('{u}', unsicher) : '');
  $('fi-ergebnis').innerHTML = FI.seiten.map((karten, si) => `
    <div class="fi-seite">
      <h4>${t('seite')} ${si + 1} · ${karten.length} ${t('faecher')}</h4>
      <div class="fi-karten">
        ${karten.map((k, ki) => `
          <div class="fi-karte ${k.sicher ? '' : 'unsicher'}" onclick="fotoKarteWaehlen(${si},${ki})" title="${t('foto_tauschen')}">
            <div class="fi-paar"><img src="${k.ausschnitt}" alt=""><img src="${imgUrl(k.id)}" alt=""></div>
            <div class="fi-txt">
              <div class="fi-name">${esc(k.name || k.id)}</div>
              <div class="fi-set">${esc(k.set || '')} ${k.nr ? '· ' + esc(k.nr) : ''}</div>
              ${k.sicher ? '' : `<div class="fi-warn">${t('foto_pruefen')}</div>`}
              ${!k.sicher && k.gelesen ? `<div class="fi-set" style="opacity:.8">${t('foto_gelesen')}: ${esc(k.gelesen)}${k.gelesen_nr ? ' ' + esc(k.gelesen_nr) : ''}</div>` : ''}
            </div>
          </div>`).join('')}
      </div>
    </div>`).join('');
}

/** Antippen einer erkannten Karte: die naechstbesten Treffer anbieten. */
function fotoKarteWaehlen(si, ki) {
  const k = FI.seiten[si][ki];
  const alt = k.alternativen || [];
  const zeilen = alt.map((a, i) => `<button onclick="fotoTauschen(${si},${ki},${i})" style="display:flex;gap:8px;align-items:center;width:100%;text-align:left;padding:6px">
      <img src="${imgUrl(a.id)}" style="width:34px;border-radius:3px" alt="">
      <span><strong style="font-size: var(--t-s)">${esc(a.name)}</strong><br><span style="font-size: var(--t-xs);color:var(--mut)">${esc(a.set)} · ${esc(a.nr || '')}</span></span>
    </button>`).join('');
  const m = $('menu-slot');
  m.innerHTML = `<div class="menu-lbl">${t('foto_alternativen')}</div>${zeilen || `<div style="padding:8px;font-size: var(--t-s);color:var(--mut)">${t('foto_keine_alt')}</div>`}
    <div class="trenn"></div><button class="gefahr" onclick="fotoKarteWeg(${si},${ki})">${t('entfernen')}</button>`;
  m.classList.remove('hidden');
  const r = event.currentTarget.getBoundingClientRect();
  m.style.minWidth = '250px';
  m.style.left = Math.max(8, Math.min(window.innerWidth - 258, r.left)) + 'px';
  menuEinpassenAn(m, r);
  event.stopPropagation();
}
function fotoTauschen(si, ki, ai) {
  const k = FI.seiten[si][ki];
  const a = k.alternativen[ai];
  k.alternativen = [{ id: k.id, name: k.name, set: k.set, nr: k.nr }, ...k.alternativen.filter((_, i) => i !== ai)];
  Object.assign(k, { id: a.id, name: a.name, set: a.set, nr: a.nr, sicher: true });
  slotMenueZu(); zeichneFotoErgebnis();
}
function fotoKarteWeg(si, ki) { FI.seiten[si].splice(ki, 1); slotMenueZu(); zeichneFotoErgebnis(); }

async function fotoUebernehmen(modus) {
  const pp = seiteInfo(S.seite || 0).laenge;
  const items = [];
  for (const karten of FI.seiten) {
    for (let i = 0; i < pp; i++) items.push(karten[i] ? { type: 'card', id: karten[i].id } : { type: 'empty' });
  }
  if (!items.length) return toast(t('foto_leer'));
  if (modus === 'neu') {
    const name = t('foto_bindername') + ' ' + new Date().toLocaleDateString(LANG === 'en' ? 'en-GB' : 'de-DE');
    if (!await binderSpeichernNeu(name, 'custom', items, {})) return;
  } else {
    if (!S.binder) return;
    merken('foto');
    while (S.binder.items.length % pp) S.binder.items.push({ type: 'empty' });
    S.binder.items.push(...items);
    speichern(); zeichneBinder();
  }
  S.seite = Math.max(0, Math.ceil(S.binder.items.length / pp) - FI.seiten.length);
  zeichneBinder(); zeichneErgebnisse();
  modalSchliessen();
  toast(t('foto_fertig').replace('{n}', FI.seiten.reduce((s, k) => s + k.length, 0)));
}

