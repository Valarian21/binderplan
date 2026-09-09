// Binderplan – Artwork-Seiten (KI) – lädt zuletzt und startet die App (boot).
// Aus index.html herausgelöst (Phase 5, 10.09.2026); die Dateien laden per defer in dieser Reihenfolge:
// kern → konto → werkbank → vitrine → preise → planer → detail → artwork → markt → sammlung.
// ---------- Artwork-Seiten (KI erweitert das Kartenmotiv über die ganze Seite) ----------
const AW_STILE = ['karte', 'comic', 'foto', 'aquarell', 'oel', 'anime', 'retro', 'pixel', 'neon', 'skizze', 'minimal', 'dunkel'];
const AW = { seite: 0, anker: new Set(), stil: 'karte', aktuell: null, poll: null, liste: [], konto: null, pokemon: [], preise: null };

function artworkOeffnen(seite) {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  if (!S.binder || S.nurAnsicht) return;
  if (!S.user) return loginOeffnen(t('aw_login'));
  if (!S.binder.items.length) return toast(t('binder_leer'));
  AW.seite = seite == null ? S.seite : seite;
  AW.aktuell = null; clearInterval(AW.poll);
  const sp = seiteInfo(AW.seite), pp = sp.laenge;
  AW.anker = new Set();
  for (let s = 0; s < pp; s++) { const it = S.binder.items[sp.start + s]; if (it && it.type === 'card') AW.anker.add(s); }
  $('aw-seite-nr').textContent = AW.seite + 1;
  $('aw-fehler').textContent = '';
  AW.pokemon = []; zeichneArtworkPoke(); $('aw-poke').value = '';
  passungLaden();
  if (!S.pokedex) ladePokedex().catch(() => {});
  if (!AW.preise) api('api/artwork/stile').then((d) => {
    AW.preise = { basis: d.preis_basis, je_karte: d.preis_je_karte, max: d.preis_max };
    AW.konto = d.konto; artworkKontingentText();
  }).catch(() => {});
  artworkAnsicht('editor');
  zeichneArtworkGrid('aw-grid', true); zeichneArtworkStile();
  modalOeffnen('modal-artwork');
  artworkGalerieLaden();
}
function artworkSchliessen() { clearInterval(AW.poll); AW.poll = null; modalSchliessen(); }

function artworkAnsicht(w) {
  $('aw-editor').classList.toggle('hidden', w !== 'editor');
  $('aw-lauf').classList.toggle('hidden', w !== 'lauf');
  $('aw-ergebnis').classList.toggle('hidden', w !== 'ergebnis');
  // Läuft die Animation nicht, steht sie auch nicht im Weg: nur solange gemalt wird.
  const l = $('aw-lauf-lader');
  if (l) l.innerHTML = w === 'lauf' ? lader('gross') : '';
}

// Seitenraster: Kartenfächer (Anker) ↔ „KI übermalt“, leere Fächer = werden gemalt
function zeichneArtworkGrid(ziel, interaktiv) {
  const spg = seiteInfo(AW.seite), pp = spg.laenge, cols = spg.cols;
  let html = '';
  for (let s = 0; s < pp; s++) {
    const it = S.binder.items[AW.seite * pp + s];
    if (it && it.type === 'card') {
      const bleibt = AW.anker.has(s);
      html += `<div class="aw-fach karte ${bleibt ? '' : 'uebermalt'}" ${interaktiv ? `onclick="artworkAnkerToggle(${s})"` : ''} title="${t(bleibt ? 'aw_karte_bleibt' : 'aw_ki_fuellt')}">
        <img src="${imgUrl(it.id)}" alt="">${interaktiv ? `<span class="aw-badge">${t(bleibt ? 'aw_karte_bleibt' : 'aw_ki_fuellt')}</span>` : ''}</div>`;
    } else {
      html += `<div class="aw-fach ki"></div>`;
    }
  }
  const box = $(ziel);
  box.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;
  box.innerHTML = html;
}
function artworkAnkerToggle(s) {
  AW.anker.has(s) ? AW.anker.delete(s) : AW.anker.add(s);
  $('aw-fehler').textContent = '';
  zeichneArtworkGrid('aw-grid', true);
  artworkKontingentText();
  passungLaden();
}

/* ---------- Passen die Karten zusammen? ----------
   Eine Seite aus einer Unterwasser- und einer Vulkankarte kann kein Bildmodell zu einer
   Landschaft verbinden. Das steht in den Bildmotiv-Daten, die für 23.461 Karten vorliegen —
   also wird es gesagt, bevor der Sammler Credits ausgibt. */
let passungUhr = null;
function passungLaden() {
  clearTimeout(passungUhr);
  passungUhr = setTimeout(async () => {
    const box = $('aw-passung');
    if (!box || !S.binder) return;
    const sp = seiteInfo(AW.seite), pp = sp.laenge;
    const belegt = [...AW.anker].filter((s) => (S.binder.items[AW.seite * pp + s] || {}).id);
    const ids = belegt.map((s) => S.binder.items[AW.seite * pp + s].id);
    if (ids.length < 2 || AW.passungAus) { box.classList.add('hidden'); return; }
    try {
      // Fächer und Raster mitgeben: die Warnung nennt auch, wenn die Karten zu weit
      // auseinanderliegen oder in sehr verschiedenem Bildausschnitt gemalt sind.
      const d = await api('api/artwork/passung?ids=' + ids.map(encodeURIComponent).join(',')
        + '&slots=' + belegt.join(',') + '&layout=' + encodeURIComponent(sp.layout || S.binder.layout));
      if (d.wert == null) { box.classList.add('hidden'); return; }
      box.classList.remove('hidden');
      box.classList.toggle('gut', d.wert >= 80);
      box.classList.toggle('mittel', d.wert < 80);
      const karten = (d.karten || []).map((k) => `${esc(k.name)}${k.ort ? ' – ' + esc(k.ort) : ''}${k.zeit ? ', ' + esc(k.zeit) : ''}`).join('<br>');
      box.innerHTML = `<strong>${d.wert >= 80 ? t('aw_p_gut') : t('aw_p_bruch')}</strong>`
        + (d.gemeinsam ? ` — ${t('aw_p_gemeinsam').replace('{o}', esc(d.gemeinsam))}` : '')
        + (d.konflikte && d.konflikte.length ? `<div style="margin-top:4px">${d.konflikte.map(esc).join('<br>')}</div>` : '')
        // Hinweise sind keine Mängel: ein weiterer Bildausschnitt heißt nur, dass diese Karte
        // in der Szene weiter hinten steht. Deshalb grau und ohne Abzug.
        + (d.hinweise && d.hinweise.length ? `<div style="margin-top:4px;color:var(--mut)">${d.hinweise.map(esc).join('<br>')}</div>` : '')
        + `<div style="margin-top:5px;color:var(--mut)">${karten}</div>`
        + (d.wert < 80 ? `<div style="margin-top:5px;color:var(--mut)">${t('aw_p_rat')}</div>` : '');
    } catch (e) { if (e && e.status === 404) AW.passungAus = true; box.classList.add('hidden'); }
  }, 250);
}
function zeichneArtworkStile() {
  $('aw-stile').innerHTML = AW_STILE.map((k) => `<button class="chip ${AW.stil === k ? 'on' : ''}" onclick="AW.stil='${k}';zeichneArtworkStile()">${t('stil_' + k)}</button>`).join('');
}
// Was diese Seite kostet: Grundpreis + Zuschlag je Ankerkarte (mehr Karten = mehr Modellkosten)
function artworkPreis() {
  const p = AW.preise;
  if (!p) return 10;
  return Math.min(p.max, p.basis + Math.max(0, AW.anker.size - 2) * p.je_karte);
}
function artworkKontingentText() {
  const k = AW.konto; const el = $('aw-kont');
  const kosten = artworkPreis();
  if (!k) { el.textContent = ''; return; }
  const reicht = k.credits >= kosten;
  el.innerHTML = `<span class="credit-chip">${ic('funke', 13)} ${kosten} ${t('credits')}</span> `
    + `<span style="color:${reicht ? 'var(--mut)' : 'var(--akzent)'}">${t('aw_kosten_hin')
        .replace('{s}', k.credits)}</span>`
    + (reicht ? '' : ` · <a href="#" onclick="event.preventDefault();upgradeOeffnen('')" style="color:var(--akzent-text);font-weight:700">${t('credits_kaufen')}</a>`);
  $('aw-start').disabled = !reicht;
}

async function artworkStarten() {
  const pp = seiteInfo(AW.seite).laenge;
  $('aw-fehler').textContent = '';
  if (!AW.anker.size) { $('aw-fehler').textContent = t('aw_kein_anker'); return; }
  if (AW.anker.size >= pp) { $('aw-fehler').textContent = t('aw_kein_platz'); return; }
  try { await _binderSichern(); } catch (e) { if (gate(e)) return; $('aw-fehler').textContent = t('fehler_speichern'); return; }
  $('aw-start').disabled = true;
  try {
    const d = await api('api/artwork', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ binder_id: S.binder.id, seite: AW.seite, stil: AW.stil, wunsch: $('aw-wunsch').value.trim(), anker: [...AW.anker], pokemon: AW.pokemon.map((p) => p.name_en) }) });
    zeichneArtworkGrid('aw-lauf-grid', false);
    artworkAnsicht('lauf');
    artworkPollen(d.id);
  } catch (e) {
    $('aw-start').disabled = false;
    if (e.code === 'keine_credits' || e.code === 'limit_artwork') {
      const dt = e.detail || {};
      return upgradeOeffnen(t('gate_credits').replace('{n}', dt.benoetigt || artworkPreis()).replace('{s}', dt.saldo || 0));
    }
    if (e.code === 'tageslimit') { $('aw-fehler').textContent = t('gate_tageslimit'); return; }
    if (e.code === 'login') return loginOeffnen(t('aw_login'));
    const texte = { kein_anker: 'aw_kein_anker', kein_platz: 'aw_kein_platz', artwork_laeuft: 'aw_laeuft_schon' };
    $('aw-fehler').textContent = texte[e.code] ? t(texte[e.code]) : (t('aw_fehler') + ' (' + (e.message || e.status) + ')');
  }
}
function artworkPollen(id) {
  clearInterval(AW.poll);
  AW.poll = setInterval(async () => {
    try {
      const d = await api('api/artwork/' + id);
      if (d.status === 'laeuft') return;
      clearInterval(AW.poll); AW.poll = null;
      AW.konto = d.konto; if (S.user && d.konto) S.user = { ...S.user, ...d.konto };
      artworkKontingentText();
      if (d.status === 'fertig') { await artworkGalerieLaden(); artworkZeigen(d); }
      else { artworkAnsicht('editor'); $('aw-fehler').textContent = t('aw_fehler') + (d.fehler ? ': ' + d.fehler : ''); $('aw-start').disabled = false; }
    } catch (e) { clearInterval(AW.poll); AW.poll = null; artworkAnsicht('editor'); $('aw-fehler').textContent = t('aw_fehler'); $('aw-start').disabled = false; }
  }, 3000);
}
// Fächer-Ansicht: die Seite in Fächer zerlegt, mit Fugen – so, wie sie gedruckt und eingesteckt aussieht.
// Jedes Fach zeigt den passenden Ausschnitt des Seitenbilds (Bild in Prozent verschoben, 63×88 mm + 4 mm Fuge).
function zeichneArtworkKacheln(aw) {
  const [cols, rows] = LAYOUTS[aw.layout] || [3, 3];
  const pw = cols * 63 + (cols - 1) * 4, ph = rows * 88 + (rows - 1) * 4;
  const box = $('aw-kacheln');
  let html = '';
  for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
    const s = r * cols + c; const karte = aw.anker && aw.anker[String(s)];
    html += `<div class="aw-kachel ${karte ? 'karte' : ''}"><img src="${aw.vorschau}" alt="" style="width:${(pw / 63 * 100).toFixed(3)}%;height:${(ph / 88 * 100).toFixed(3)}%;left:-${(c * 67 / 63 * 100).toFixed(3)}%;top:-${(r * 92 / 88 * 100).toFixed(3)}%"><span>${karte ? t('aw_karte') : t('aw_fach') + ' ' + (s + 1)}</span></div>`;
  }
  box.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;
  // Die Fuge in Prozent der Gesamtbreite, genau wie in der Vitrine. Vorher wurde sie aus der
  // gemessenen Kachelbreite gerechnet — gemessen wurde aber, bevor die Fuge stand, und jede
  // Fuge verändert die Kachelbreite wieder. Das Ergebnis passte nie zum Bildausschnitt, der
  // fest mit 4 Einheiten auf 63 rechnet.
  box.style.gap = (4 / pw * 100).toFixed(4) + '%';
  box.innerHTML = html;
}
function artworkVorschau(w) {
  AW.vorschauArt = w;
  $('aw-kacheln').classList.toggle('hidden', w !== 'faecher');
  $('aw-bild').classList.toggle('hidden', w !== 'voll');
  $('aw-tab-faecher').classList.toggle('on', w === 'faecher');
  $('aw-tab-voll').classList.toggle('on', w === 'voll');
}
function artworkZeigen(aw) {
  AW.aktuell = aw;
  $('aw-bild').src = aw.vorschau;
  zeichneArtworkKacheln(aw);
  artworkVorschau(AW.vorschauArt || 'faecher');
  $('aw-png').href = `api/artwork/${aw.id}/bild?v=voll`;
  const pp = (LAYOUTS[aw.layout] || [3, 3]).reduce((a, b) => a * b, 1);
  const k = Object.keys(aw.anker || {}).length;
  $('aw-erg-info').textContent = t('aw_erg_info').replace('{k}', k).replace('{f}', pp - k).replace('{s}', t('stil_' + aw.stil))
    + ((aw.pokemon || []).length ? ' · ' + aw.pokemon.map((p) => LANG === 'en' ? p.name_en : p.name_de).join(', ') : '');
  $('aw-seite-nr').textContent = aw.seite + 1;
  awTeilenKnopfText();
  artworkAnsicht('ergebnis');
  document.querySelectorAll('#aw-galerie .aw-mini').forEach((el) => el.classList.toggle('on', el.dataset.id === aw.id));
}
async function artworkGalerieLaden() {
  try {
    const d = await api('api/artwork?binder_id=' + encodeURIComponent(S.binder.id || ''));
    AW.liste = d.artworks.filter((a) => a.status === 'fertig');
    AW.konto = d.konto; if (S.user && d.konto) S.user = { ...S.user, ...d.konto };
    artworkKontingentText();
    // Läuft noch ein Job (Modal zwischendurch geschlossen)? Dann dort weitermachen
    const laufend = d.artworks.find((a) => a.status === 'laeuft');
    if (laufend && !AW.poll) { zeichneArtworkGrid('aw-lauf-grid', false); artworkAnsicht('lauf'); artworkPollen(laufend.id); }
  } catch (e) { AW.liste = []; }
  $('aw-galerie-lbl').classList.toggle('hidden', !AW.liste.length);
  // Freigeben ging bisher nur an der geöffneten Seite. Wer zehn fertige Seiten hat,
  // musste jede einzeln aufrufen — deshalb steht der Schalter jetzt an jeder Kachel.
  $('aw-galerie').innerHTML = AW.liste.map((a) => `<div class="aw-mini ${AW.aktuell && AW.aktuell.id === a.id ? 'on' : ''}" data-id="${a.id}" onclick="artworkZeigen(AW.liste.find((x) => x.id === '${a.id}'))">
    <img src="${a.vorschau}" alt="" loading="lazy">${t('seite')} ${a.seite + 1} · ${t('stil_' + a.stil)}${artworkImBinder(a.id) ? ' · ✓ ' + t('aw_im_binder') : ''}${a.oeffentlich ? ' · ' + t('aw_oeffentlich') + (a.downloads ? ' · ' + t('cr_uebernommen').replace('{n}', a.downloads) : '') + (a.verdient ? ' · +' + a.verdient : '') : ''}
    ${S.user ? `<button class="aw-mini-teilen ${a.oeffentlich ? 'an' : ''}" title="${t(a.oeffentlich ? 'vt_zurueckziehen' : 'awt_ja_kurz')}"
        onclick="event.stopPropagation();awGalerieTeilen('${a.id}')">${a.oeffentlich ? '★ ' + t('aw_oeffentlich') : '☆ ' + t('awt_frei_kurz')}</button>` : ''}</div>`).join('');
}
// Wunsch-Pokémon: Name aus der Pokédex-Liste → Chip mit Sprite
function artworkPokeTaste(ev) { if (ev.key === 'Enter') { ev.preventDefault(); artworkPokeAdd(); } }
function artworkPokeAdd() {
  const feld = $('aw-poke'); const name = feld.value.trim().toLowerCase();
  if (!name) return;
  if (AW.pokemon.length >= 3) { $('aw-fehler').textContent = t('aw_pokemon_max'); return; }
  const p = (S.pokedex || []).find((x) => (x.name || '').toLowerCase() === name || (x.name_en || '').toLowerCase() === name)
    || (S.pokedex || []).find((x) => (x.name || '').toLowerCase().startsWith(name) || (x.name_en || '').toLowerCase().startsWith(name));
  if (!p) { $('aw-fehler').textContent = t('aw_pokemon_unbekannt'); return; }
  if (!AW.pokemon.some((x) => x.dex === p.dex)) AW.pokemon.push({ dex: p.dex, name: p.name, name_en: p.name_en });
  feld.value = ''; $('aw-fehler').textContent = '';
  zeichneArtworkPoke();
}
function artworkPokeWeg(dex) { AW.pokemon = AW.pokemon.filter((p) => p.dex !== dex); zeichneArtworkPoke(); }
function zeichneArtworkPoke() {
  $('aw-poke-chips').innerHTML = AW.pokemon.map((p) => `<button class="chip on" onclick="artworkPokeWeg(${p.dex})" title="✕"><img src="api/img/dex/${p.dex}" alt="">${esc(LANG === 'en' ? p.name_en : p.name)} ✕</button>`).join('');
}
function artworkNochmal() {
  $('aw-start').disabled = false; $('aw-fehler').textContent = '';
  artworkKontingentText();
  artworkAnsicht('editor');
}
async function artworkPdf() {
  if (!AW.aktuell) return;
  arbeitToast(t('pdf_erzeugt'));
  try {
    const r = await fetch(`api/artwork/${AW.aktuell.id}/pdf?mit_karten=${$('aw-mit-karten').checked ? 1 : 0}`, { headers: { Authorization: 'Bearer ' + S.token } });
    if (!r.ok) { arbeitToastEnde(); return toast(t('aw_fehler') + ' (' + r.status + ')'); }
    const blob = await r.blob();
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = blobUrl; a.download = `artwork-${t('seite').toLowerCase()}-${AW.aktuell.seite + 1}.pdf`;
    document.body.appendChild(a); a.click(); a.remove();
    arbeitToastEnde();
    const el = $('toast'); $('toast-text').innerHTML = `${t('pdf_geladen')} <a href="${blobUrl}" target="_blank" rel="noopener">${t('pdf_oeffnen')}</a> ${blob.size > 50000 ? ` (${(blob.size / 1e6).toFixed(1).replace('.', LANG === 'de' ? ',' : '.')} MB)` : ''}`;
    $('toast-undo').classList.add('hidden'); el.classList.add('zeig');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove('zeig'), 15000);
  } catch (e) { arbeitToastEnde(); toast(t('aw_fehler')); }
}
// Artwork-Fächer in die Binder-Seite eintragen: leere/übermalte Fächer → {type:'art'}; Kartenfächer bleiben
/** Freigeben oder zurückziehen direkt aus der Galerie. */
async function awGalerieTeilen(id) {
  const aw = AW.liste.find((x) => x.id === id);
  if (!aw) return;
  if (aw.oeffentlich) {
    await awZurueckziehen(id);
    aw.oeffentlich = false;
    artworkGalerieLaden();
    if (AW.aktuell && AW.aktuell.id === id) { AW.aktuell.oeffentlich = false; awTeilenKnopfText(); }
  } else {
    awTeilenFragen(aw);
  }
}

/** Freigeben oder zurückziehen — dieselbe Stelle, an der die Seite auch gedruckt wird. */
function awTeilenKnopf() {
  const aw = AW.aktuell;
  if (!aw) return;
  if (aw.oeffentlich) awZurueckziehen(aw.id).then(() => { aw.oeffentlich = false; awTeilenKnopfText(); });
  else awTeilenFragen(aw);
}

function awTeilenKnopfText() {
  const b = $('aw-teilen-btn');
  if (!b) return;
  const aw = AW.aktuell;
  b.classList.toggle('hidden', !aw || !S.user);
  if (!aw) return;
  b.textContent = aw.oeffentlich
    ? t('vt_zurueckziehen') + (aw.downloads ? ' (' + aw.downloads + '×)' : '')
    : t('awt_ja_kurz');
}

function artworkUebernehmen() {
  const aw = AW.aktuell; if (!aw || !S.binder) return;
  if (aw.layout !== S.binder.layout) return toast(t('aw_layout_anders'));
  const spa = seiteInfo(aw.seite), pp = spa.laenge;
  merken('artwork');
  while (S.binder.items.length < spa.start + pp) S.binder.items.push({ type: 'empty' });
  for (let s = 0; s < pp; s++) {
    if (aw.anker && aw.anker[String(s)]) continue;
    S.binder.items[aw.seite * pp + s] = { type: 'art', artwork: aw.id, slot: s, layout: aw.layout };
  }
  S.seite = aw.seite;
  speichern(); zeichneBinder();
  artworkSchliessen();
  toastUndo(t('aw_uebernommen'));
  // Jetzt ist der richtige Moment für die Frage: die Seite hängt im Binder, sie gefällt,
  // und der Ersteller weiß, was sie ihm wert ist.
  if (!aw.oeffentlich) setTimeout(() => awTeilenFragen(aw), 600);
}

/** Frage nach dem Übernehmen: soll die Seite in die Kunstseiten-Vitrine? */
let awTeilenZiel = null;
function awTeilenFragen(aw) {
  if (!S.user || !aw) return;
  awTeilenZiel = aw;
  const u = $('awt-u');
  if (u) u.textContent = t('awt_u').replace('{p}', VT.preis || 5).replace('{a}', VT.anteil || 2);
  $('awt-titel').value = aw.titel || '';
  $('awt-fehler').textContent = '';
  modalOeffnen('modal-aw-teilen');
}

async function awTeilenSpeichern() {
  if (!awTeilenZiel) return modalSchliessen();
  const titel = ($('awt-titel').value || '').trim();
  try {
    const d = await api('api/artwork/' + encodeURIComponent(awTeilenZiel.id) + '/veroeffentlichen', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ oeffentlich: true, titel }),
    });
    awTeilenZiel.oeffentlich = true; awTeilenZiel.titel = titel;
    VT.preis = d.preis; VT.anteil = d.anteil;
    // `modalSchliessen()` macht *alle* Overlays zu, also auch den Artwork-Dialog, aus dem
    // die Freigabe kam. Wer zwei Seiten hintereinander freigeben will, stand danach wieder
    // in der Werkbank. Deshalb: schließen und den Artwork-Dialog gleich wieder öffnen.
    const zurueck = !$('modal-artwork').classList.contains('hidden');
    toast(t('awt_frei'));
    if (zurueck) dialogWechsel(() => { modalOeffnen('modal-artwork'); artworkGalerieLaden(); });
    else { modalSchliessen(); if (typeof artworkGalerieLaden === 'function') artworkGalerieLaden(); }
    if (!$('profilseite').classList.contains('hidden')) creatorLaden();
  } catch (e) {
    const f = e && e.detail && e.detail.text ? e.detail.text : (e.message || '');
    $('awt-fehler').textContent = f;
    // Fehlt Geburtsdatum oder Anzeigename, geht es danach mit der Freigabe weiter —
    // sonst verpufft der Klick, und die Seite bleibt privat, ohne dass es jemand merkt.
    const nochmal = () => awTeilenFragen({ ...awTeilenZiel, titel: titel || awTeilenZiel.titel });
    if (e && e.detail && e.detail.code === 'geburtsdatum') dialogWechsel(() => geburtsdatumOeffnen(nochmal));
    if (e && e.detail && e.detail.code === 'kein_profil') {
      profilDanach = nochmal;
      dialogWechsel(() => modalOeffnen('modal-profil'));
    }
  }
}

/** Kunstseiten der aktuellen Binderseite (Artwork-IDs, ohne Doppelte). */
function seiteKunstIds(nr) {
  if (!S.binder) return [];
  const sp = seiteInfo(nr == null ? S.seite : nr);
  if (!sp) return [];
  return [...new Set(S.binder.items.slice(sp.start, sp.start + sp.laenge)
    .filter((i) => i && i.type === 'art' && i.artwork).map((i) => i.artwork))];
}
/** Das Seiten-Menü öffnen; liegt eine Kunstseite auf der Seite, steht ihre Freigabe
 *  ganz oben — mit dem echten Zustand, sobald er geladen ist. */
function seiteMenueOeffnen() {
  const box = $('menu-seite-kunst');
  const ids = S.user ? seiteKunstIds() : [];
  box.innerHTML = ids.map((id) => `<button data-aw="${esc(id)}" onclick="kunstFreigeben('${esc(id)}')">${ic('palette', 15)} ${t('aw_seite_frei')}</button>`).join('')
    + (ids.length ? '<div class="trenn"></div>' : '');
  menuToggle('menu-seite');
  ids.forEach(async (id) => {
    try {
      const aw = await api('api/artwork/' + encodeURIComponent(id));
      const b = box.querySelector(`[data-aw="${CSS.escape(id)}"]`);
      if (b) b.innerHTML = `${ic('palette', 15)} ${aw.oeffentlich ? t('vt_zurueckziehen') : t('aw_seite_frei')}`;
    } catch (e) { /* Menü zeigt dann die allgemeine Beschriftung */ }
  });
}
/** Eine Kunstseite freigeben oder zurückziehen — von überall, ohne den Artwork-Dialog. */
async function kunstFreigeben(id) {
  if (!S.user) return loginOeffnen(t('vt_login'));
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  let aw;
  try { aw = await api('api/artwork/' + encodeURIComponent(id)); } catch (e) { return toast(t('fehler_laden')); }
  if (aw.status !== 'fertig') return toast(t('aw_laeuft_schon'));
  if (aw.oeffentlich) return awZurueckziehen(id);
  awTeilenFragen(aw);
}

/** Aus der Vitrine zurückziehen — im Artwork-Dialog neben der Seite. */
async function awZurueckziehen(id) {
  try {
    await api('api/artwork/' + encodeURIComponent(id) + '/veroeffentlichen', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ oeffentlich: false }),
    });
    toast(t('awt_privat'));
    if (typeof artworkGalerieLaden === 'function') artworkGalerieLaden();
  } catch (e) { toast(e.message); }
}
function artworkImBinder(id) { return S.binder ? S.binder.items.some((i) => i.type === 'art' && i.artwork === id) : false; }
async function artworkLoeschen() {
  if (!AW.aktuell) return;
  if (!confirm(t('aw_loeschen_frage'))) return;
  try { await api('api/artwork/' + AW.aktuell.id, { method: 'DELETE' }); } catch (e) { return toast(t('aw_fehler')); }
  // Verweise im aktuellen Binder auflösen (Fach wird wieder leer)
  if (S.binder && artworkImBinder(AW.aktuell.id)) {
    S.binder.items = S.binder.items.map((i) => (i.type === 'art' && i.artwork === AW.aktuell.id) ? { type: 'empty' } : i);
    speichern(); zeichneBinder();
  }
  AW.aktuell = null;
  toast(t('aw_geloescht'));
  await artworkGalerieLaden();
  artworkNochmal();
}

boot();
