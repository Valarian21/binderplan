// Binderplan – Vitrine, Profil, Veröffentlichen, fremde Binder, öffentliche Profile, Zurück-Taste.
// Aus index.html herausgelöst (Phase 5, 10.09.2026); die Dateien laden per defer in dieser Reihenfolge:
// kern → konto → werkbank → vitrine → preise → planer → detail → artwork → markt → sammlung.
// ---------- Vitrine ----------
// Bewusst ohne Kommentare und ohne Nachrichten: es gibt nur Herzen. Damit gibt es
// keine Gespräche zu moderieren, und die drei Textfelder (Anzeigename, Satz zur
// Person, Bindername) werden serverseitig beim Speichern geprüft.
const VT = { sortierung: 'trend', binder: [], seq: 0, art: '', groesse: '',
             doppelseite: localStorage.getItem('bp_vt_dop') === '1', meldung: null,
             fenster: localStorage.getItem('bp_vt_fenster') || '',
             // Kunstseiten stehen vorn: es gibt dreieinhalbmal so viele wie Binder, sie tragen
             // sichtbar Arbeit und sind das Einzige, was kein Konkurrent hat. Ein Binder-Stapel
             // ist ein Kartenraster — hübsch, aber erklärungsbedürftig.
             bereich: 'kunst', kunst: [], kunstSort: 'neu', kunstStil: '', stile: [],
             jahrVon: '', jahrBis: '', jahrMin: null, jahrMax: null,
             preis: 5, anteil: 2 };

function ansichtVitrine() {
  hashSetzen('vitrine');
  mnavMarkieren('mnav-vitrine');
  planerSchliessen();
  $('vitrine').classList.remove('hidden');
  ebeneOeffnen(vitrineSchliessen);
  reiterSetzen('seg-vitrine');
  zeichneVitrineBereich();
  zeichneVitrineTabs();
  vitrineLaden();
}

/** Zwischen Bindern und Kunstseiten umschalten. */
function vitrineBereich(w) {
  VT.bereich = w;
  zeichneVitrineBereich();
  zeichneVitrineTabs();
  vitrineLaden();
}

function zeichneVitrineBereich() {
  $('vtb-binder').classList.toggle('on', VT.bereich === 'binder');
  $('vtb-kunst').classList.toggle('on', VT.bereich === 'kunst');
  const u = $('vt-unter');
  if (u) u.textContent = VT.bereich === 'kunst' ? t('vt_u_kunst') : t('vt_u');
  const suche = $('vt-suche');
  if (suche) {
    suche.placeholder = VT.bereich === 'kunst' ? t('vt_suche_kunst') : t('vt_suche');
    // Auch das Suchfeld erst, wenn es etwas zu durchsuchen gibt.
    const n = VT.bereich === 'kunst' ? (VT.kunstGesamt || 0) : (VT.gesamt || 0);
    suche.classList.toggle('hidden', n < VT_FILTER_AB && !suche.value);
  }
}
function vitrineSchliessen() {
  if ($('vitrine').classList.contains('hidden')) return;
  ebeneAufraeumen(vitrineSchliessen);
  $('vitrine').classList.add('hidden');
  // Fehlte als einzige der Vollbildansichten: die Adresse blieb auf /app/vitrine stehen,
  // ein Neuladen landete danach wieder in der Vitrine statt im Binder.
  routeZurueck();
  if ($('startseite').classList.contains('hidden')) reiterSetzen('seg-suche');
}

function zeichneVitrineTabs() {
  // Zwei Reiter, in beiden Bereichen dieselben Wörter. Vorher hießen dieselben drei
  // Ordnungen bei Bindern „Im Trend · Bestenliste · Frisch" und bei Kunstseiten
  // „Frisch · Beliebt". Der Trend-Reiter kommt zurück, sobald genug veröffentlicht wird —
  // eine Bestenliste über vier Herzen ist keine Liste.
  const tabs = [['neu', t('vt_neu')], ['top', t('vt_beliebt')]];
  const aktiv = VT.bereich === 'kunst' ? VT.kunstSort : VT.sortierung;
  $('vt-tabs').innerHTML = tabs
    .map(([w, l]) => `<button class="${aktiv === w ? 'on' : ''}" onclick="vitrineSortierung('${w}')">${l}</button>`).join('');
  zeichneVitrineFilter();
}

/** Filter über dem Gitter: wonach jemand stöbert, ist entweder eine Art von Binder
 *  („zeig mir Pokédex-Binder“) oder eine Größe („eine schöne Seite, kein Mammutprojekt“). */
// Jahrgänge als Ären — zwei Zahlenfelder waren am Handy eine Fummelei, und Sammler fragen
// ohnehin nach „alte WotC-Karten", nicht nach „1999 bis 2003".
const VT_JAHRGAENGE = [[1996, 2003, '1996–2003 · WotC'], [2004, 2010, '2004–2010 · EX bis Platin'],
                       [2011, 2016, '2011–2016 · HGSS bis XY'], [2017, 2019, '2017–2019 · Sonne & Mond'],
                       [2020, 2022, '2020–2022 · Schwert & Schild'], [2023, 2035, '2023+ · Karmesin & Purpur']];
/** Ab wie vielen Objekten Filter überhaupt etwas ausrichten. Darunter sind sie Lärm:
 *  über sechs Bindern standen zwei Wähler, ein Suchfeld, drei Reiter und ein Schalter. */
const VT_FILTER_AB = 12;

function zeichneVitrineFilter() {
  const box = $('vt-filter');
  if (!box) return;
  const anzahl = VT.bereich === 'kunst' ? (VT.kunstGesamt || 0) : (VT.gesamt || 0);
  const suche = VT.bereich === 'kunst' ? VT.kunstSuche : VT.suche;
  if (anzahl < VT_FILTER_AB && !suche) { box.innerHTML = ''; box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  // Die Bestenliste bekommt ein Zeitfenster: „beliebt diese Woche" statt einer Liste,
  // die für immer dieselbe bleibt. Voreinstellung „Immer", solange die Vitrine klein ist.
  const aktivTab = VT.bereich === 'kunst' ? VT.kunstSort : VT.sortierung;
  const fenster = aktivTab !== 'top' ? '' : `<span class="vt-fensterwahl">${
    [['woche', t('vt_fenster_woche')], ['monat', t('vt_fenster_monat')], ['', t('vt_fenster_immer')]]
      .map(([w, l]) => `<button class="chip ${VT.fenster === w ? 'on' : ''}" onclick="vitrineFenster('${w}')">${l}</button>`).join('')}</span>`;
  if (VT.bereich === 'kunst') {
    // Ein Wähler statt einer Chip-Reihe: die Stile sind zwölf, das wären zwölf Chips.
    // Der Jahrgang ist der Filter, nach dem Sammler fragen: „zeig mir Seiten mit alten
    // Karten". Bei mehreren Karten auf einer Seite zählt die äußerste — gefiltert wird
    // über den Bereich vom ältesten bis zum neuesten Jahr.
    box.innerHTML = fenster + `
      <span class="vt-lbl">${t('aw_stil')}</span>
      <select class="feld" style="width:180px" onchange="VT.kunstStil=this.value;vitrineLaden()">
        <option value="">${t('vt_f_alle')}</option>
        ${VT.stile.map((x) => `<option value="${esc(x)}" ${VT.kunstStil === x ? 'selected' : ''}>${esc(t('stil_' + x) !== 'stil_' + x ? t('stil_' + x) : x)}</option>`).join('')}
      </select>
      <select class="feld" style="width:190px" onchange="const [v, b] = this.value.split('-'); VT.jahrVon = v || ''; VT.jahrBis = b || ''; vitrineLaden()">
        <option value="">${t('vt_k_jahrgang')}: ${t('vt_f_alle')}</option>
        ${VT_JAHRGAENGE.map(([v, b, l]) => `<option value="${v}-${b}" ${String(VT.jahrVon) === String(v) && String(VT.jahrBis) === String(b) ? 'selected' : ''}>${l}</option>`).join('')}
      </select>
      <div style="flex:1"></div>
      <span class="vt-lbl">${t('vt_k_preis').replace('{n}', VT.preis)}</span>`;
    return;
  }
  // Zwei beschriftete Chip-Reihen wurden zu zwei Wählern: sie brauchen eine Zeile statt zwei
  // und am Handy kein seitliches Wischen.
  box.innerHTML = fenster + `
    <select class="feld" style="width:170px" onchange="vitrineFilterSetzen('art', this.value)">
      <option value="">${t('vt_f_art')}: ${t('vt_f_alle')}</option>
      ${['master', 'dex', 'pokemon', 'kuenstler', 'artwork'].map((a) => `<option value="${a}" ${VT.art === a ? 'selected' : ''}>${t(VT_ARTEN[a])}</option>`).join('')}
    </select>
    <select class="feld" style="width:170px" onchange="vitrineFilterSetzen('groesse', this.value)">
      <option value="">${t('vt_f_groesse')}: ${t('vt_f_alle')}</option>
      <option value="klein" ${VT.groesse === 'klein' ? 'selected' : ''}>${t('vt_f_klein')}</option>
      <option value="mittel" ${VT.groesse === 'mittel' ? 'selected' : ''}>${t('vt_f_mittel')}</option>
      <option value="gross" ${VT.groesse === 'gross' ? 'selected' : ''}>${t('vt_f_gross')}</option>
    </select>
    <div style="flex:1"></div>
    <button class="chip ${VT.doppelseite ? 'on' : ''}" onclick="vitrineDoppelseite()" title="${t('vt_f_dop_t')}">${t('vt_f_dop')}</button>`;
}

function vitrineFilter(feld, wert) {
  VT[feld] = VT[feld] === wert ? '' : wert;
  zeichneVitrineFilter();
  vitrineLaden();
}

function vitrineFilterSetzen(feld, wert) {
  VT[feld] = wert;
  vitrineLaden();
}
function vitrineFenster(w) {
  VT.fenster = w;
  try { localStorage.setItem('bp_vt_fenster', w); } catch (e) {}
  zeichneVitrineFilter();
  vitrineLaden();
}

function vitrineDoppelseite() {
  VT.doppelseite = !VT.doppelseite;
  try { localStorage.setItem('bp_vt_dop', VT.doppelseite ? '1' : ''); } catch (e) {}
  zeichneVitrineFilter();
  zeichneVitrine();
}
function vitrineSortierung(w) {
  if (VT.bereich === 'kunst') VT.kunstSort = w; else VT.sortierung = w;
  zeichneVitrineTabs(); vitrineLaden();
}
let vtTimer = null;
function vitrineSuchen() { clearTimeout(vtTimer); vtTimer = setTimeout(vitrineLaden, 350); }

async function vitrineLaden() {
  // Nur die jüngste Anfrage zählt. Vorher sperrte ein Schalter jede zweite Anfrage: wer
  // schnell von „Binder" auf „Kunstseiten" tippte, sah unter dem Kunst-Reiter Binder.
  const seq = ++VT.seq;
  $('vt-gitter').innerHTML = `<div class="vt-leer">${lader('gross', t('laedt'))}</div>`;
  try {
    const q = encodeURIComponent($('vt-suche').value.trim());
    if (VT.bereich === 'kunst') VT.kunstSuche = $('vt-suche').value.trim(); else VT.suche = $('vt-suche').value.trim();
    if (VT.bereich === 'kunst') {
      const d = await api(`api/vitrine/artwork?sortierung=${VT.kunstSort}&limit=36&q=${q}&fenster=${VT.fenster}`
        + `&stil=${encodeURIComponent(VT.kunstStil || '')}`
        + `&jahr_von=${parseInt(VT.jahrVon) || 0}&jahr_bis=${parseInt(VT.jahrBis) || 0}`);
      if (seq !== VT.seq) return;
      VT.kunst = d.artworks || [];
      VT.stile = d.stile || [];
      VT.jahrMin = d.jahr_min; VT.jahrMax = d.jahr_max;
      VT.kunstGesamt = d.gesamt != null ? d.gesamt : (d.artworks || []).length;
      VT.preis = d.preis; VT.anteil = d.anteil;
      zeichneVitrineFilter();
      zeichneKunst();
    } else {
      const d = await api(`api/vitrine?sortierung=${VT.sortierung}&limit=36&q=${q}&fenster=${VT.fenster}`
        + `&art=${VT.art || ''}&groesse=${VT.groesse || ''}`);
      if (seq !== VT.seq) return;
      VT.binder = d.binder || [];
      VT.gesamt = d.gesamt != null ? d.gesamt : VT.binder.length;
      zeichneVitrineFilter();
      zeichneVitrine();
    }
  } catch (e) {
    if (seq === VT.seq) $('vt-gitter').innerHTML = `<div class="vt-leer">${esc(e.message || '')}</div>`;
  }
}

/** Der Kunstseiten-Bereich. Eine Seite kostet Credits; der Ersteller bekommt einen Teil
 *  davon zurück. Beides steht auf der Kachel, damit niemand raten muss. */
function zeichneKunst() {
  if (!VT.kunst.length) {
    $('vt-gitter').classList.add('kunst');
    $('vt-gitter').innerHTML = `<div class="vt-leer">${t('vt_k_leer')}</div>`;
    return;
  }
  $('vt-gitter').classList.add('kunst');
  $('vt-gitter').innerHTML = VT.kunst.map((a) => {
    // Ohne Konto steht hier „Ansehen": ein Preis in Credits sagt jemandem nichts, der weder
    // ein Konto noch je von Credits gehört hat. Was eine Seite kostet, erklärt die Großansicht.
    const knopf = !S.user
      ? `<span class="kansehen">${t('vt_k_ansehen')}</span>`
      : a.mein
        ? `<span class="habe">${t('vt_k_meine')}</span>`
        : a.habe
          ? `<button class="btn sekundaer" onclick="event.stopPropagation();kunstInBinder('${esc(a.id)}')">${t('vt_k_einfuegen')}</button>`
          : `<button class="btn sekundaer" onclick="event.stopPropagation();kunstUebernehmen('${esc(a.id)}')">${t('vt_k_holen').replace('{n}', VT.preis)}</button>`;
    // Die Kachel ist eine Binderseite mit Fächern und Fugen — genau das, was geliefert
    // wird: ein Druckbogen für die leeren Hüllen, die Ankerkarte bleibt echt im Fach.
    // Vorher stand das Bild als Poster da, und daneben zeigte der Binder-Bereich Seiten
    // mit Fächern: zwei Darstellungen für dasselbe Produkt. Tipp öffnet weiter das
    // ganze Bild; die Melde-Fahne liegt dort, nicht auf jeder Kachel.
    // Die ganze Kachel öffnet die Seite; Herz, Besitzer und Aktionsknopf halten den Klick an.
    // Vorher war nur das Bild klickbar, beim Binder dagegen Bild und Titel — zwei Regeln im
    // selben Raster.
    return `<div class="kk" role="button" tabindex="0" onclick="kunstGross('${esc(a.id)}')"
                 onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();kunstGross('${esc(a.id)}')}">
      <div class="kbild">${a.blatt ? vitrineBlatt(a.blatt, false, true)
        : `<img loading="lazy" src="${esc(a.vorschau)}" alt="" style="width:100%;display:block;border-radius:6px">`}</div>
      <div class="txt">
        <div class="kt">${esc(a.titel)}</div>
        <div class="km"><a onclick="event.stopPropagation();profilseiteOeffnen('${esc(a.besitzer)}')" style="color:var(--blau);cursor:pointer">${esc(a.besitzer)}</a>
          · ${esc(a.layout.replace('x', '×'))}${a.downloads ? ` · <strong>${a.downloads}× ${t('vt_k_geholt')}</strong>` : ''}</div>
        <div class="km karten">${(a.karten || []).length ? `${a.jahr_alt ? `<strong>${a.jahr_alt}${a.jahr_neu && a.jahr_neu !== a.jahr_alt ? '–' + a.jahr_neu : ''}</strong> · ` : ''}${esc((a.karten || []).map((k) => k.name).join(', '))}` : ''}</div>
        <div class="kf">
          <button class="vt-herz ${a.gestimmt ? 'an' : ''}" onclick="event.stopPropagation();vitrineKunstStimme('${esc(a.id)}')">
            ${ic('herz', 15, a.gestimmt ? 'voll' : '')}<span>${a.stimmen || 0}</span></button>
          <div style="flex:1"></div>${knopf}
        </div>
      </div>
    </div>`;
  }).join('');
}

/* Großansicht einer Kunstseite.
   Sie zeigt zuerst die Binderseite mit Fächern und Fugen — das ist, was man ausdruckt und in
   die Hüllen steckt; das Bild am Stück ist der Sonderfall und liegt auf dem Umschalter. Und man
   blättert von hier aus weiter durch die Liste, statt für jede Seite zurück ins Gitter zu gehen. */
const KW = { liste: [], pos: 0, art: 'faecher', box: null };

function kunstGross(id) {
  KW.liste = VT.kunst || [];
  KW.pos = Math.max(0, KW.liste.findIndex((a) => a.id === id));
  if (!KW.liste.length) return;
  if (!KW.box) {
    KW.box = document.createElement('div');
    KW.box.className = 'kw-box';
    KW.box.onclick = (ev) => { if (ev.target === KW.box || ev.target.classList.contains('kw-buehne')) kunstGrossZu(); };
    document.body.appendChild(KW.box);
    document.addEventListener('keydown', kunstGrossTaste);
  }
  KW.box.classList.remove('hidden');
  document.body.classList.add('kw-offen');
  zeichneKunstGross();
}

function kunstGrossZu() {
  if (KW.box) KW.box.classList.add('hidden');
  document.body.classList.remove('kw-offen');
}

function kunstGrossTaste(ev) {
  if (!KW.box || KW.box.classList.contains('hidden')) return;
  if (ev.key === 'Escape') { ev.preventDefault(); kunstGrossZu(); }
  else if (ev.key === 'ArrowRight') { ev.preventDefault(); kunstGrossWechsel(1); }
  else if (ev.key === 'ArrowLeft') { ev.preventDefault(); kunstGrossWechsel(-1); }
}

function kunstGrossWechsel(d) {
  if (!KW.liste.length) return;
  KW.pos = (KW.pos + d + KW.liste.length) % KW.liste.length;
  zeichneKunstGross();
}

function kunstGrossAnsicht(w) { KW.art = w; zeichneKunstGross(); }

function zeichneKunstGross() {
  const a = KW.liste[KW.pos];
  if (!a || !KW.box) return;
  const [sp, ze] = LAYOUTS[a.layout] || [3, 3];
  const pw = sp * 63 + (sp - 1) * 4, ph = ze * 88 + (ze - 1) * 4;
  const knopf = a.mein
    ? `<span class="habe">${t('vt_k_meine')}</span>`
    : a.habe
      ? `<button class="btn sekundaer" onclick="event.stopPropagation();kunstInBinder('${esc(a.id)}')">${t('vt_k_einfuegen')}</button>`
      : `<button class="btn sekundaer" onclick="event.stopPropagation();kunstUebernehmen('${esc(a.id)}')">${t('vt_k_holen').replace('{n}', VT.preis)}</button>`;
  const bild = KW.art === 'voll' || !a.blatt
    ? `<img src="api/artwork/${encodeURIComponent(a.id)}/bild?v=vorschau" alt="${esc(a.titel)}">`
    : vitrineBlatt(a.blatt, false, true);
  KW.box.innerHTML = `
    <div class="kw-kopf" onclick="event.stopPropagation()">
      <div class="kw-titel"><strong>${esc(a.titel)}</strong>
        <span>${esc(a.besitzer)} · ${esc(a.layout.replace('x', '×'))}${a.stimmen ? ' · ' + a.stimmen + ' ♥' : ''}</span></div>
      <div class="segment klein kw-um">
        <button class="${KW.art === 'faecher' ? 'on' : ''}" onclick="kunstGrossAnsicht('faecher')">${t('aw_ansicht_faecher')}</button>
        <button class="${KW.art === 'voll' ? 'on' : ''}" onclick="kunstGrossAnsicht('voll')">${t('aw_ansicht_voll')}</button>
      </div>
      <button class="kw-x" onclick="kunstGrossZu()" title="${t('schliessen_t')}" aria-label="${t('schliessen_t')}">✕</button>
    </div>
    <div class="kw-buehne">
      <button class="kw-pfeil" onclick="event.stopPropagation();kunstGrossWechsel(-1)"
              aria-label="${t('bv_zurueck')}" ${KW.liste.length < 2 ? 'hidden' : ''}>‹</button>
      <div class="kw-seite" style="aspect-ratio:${pw}/${ph}" onclick="event.stopPropagation()">${bild}</div>
      <button class="kw-pfeil" onclick="event.stopPropagation();kunstGrossWechsel(1)"
              aria-label="${t('bv_vor')}" ${KW.liste.length < 2 ? 'hidden' : ''}>›</button>
    </div>
    <div class="kw-fuss" onclick="event.stopPropagation()">
      <button class="vt-herz ${a.gestimmt ? 'an' : ''}" onclick="vitrineKunstStimme('${esc(a.id)}').then(zeichneKunstGross)">
        ${ic('herz', 15, a.gestimmt ? 'voll' : '')}<span>${a.stimmen || 0}</span></button>
      <span class="kw-stand">${KW.pos + 1} / ${KW.liste.length}</span>
      ${knopf}
      <button class="kw-melden" onclick="kunstGrossZu();meldenOeffnen('artwork','${esc(a.id)}')" title="${t('md_t')}">${ic('fahne', 15)}</button>
    </div>`;
}

/** Herz an einer Kunstseite — wie beim Binder. */
async function vitrineKunstStimme(id) {
  if (!S.user) return loginOeffnen(t('vt_login'));
  try {
    const d = await api('api/vitrine/artwork/stimme', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ artwork_id: id }),
    });
    const a = VT.kunst.find((x) => x.id === id);
    if (a) { a.gestimmt = d.gestimmt; a.stimmen = d.stimmen; }
    zeichneKunst();
  } catch (e) { if (!gate(e)) toast(e.message); }
}


/** Kaufen und sofort einsetzen. Bezahlt wird genau einmal je Seite und Konto. */
async function kunstUebernehmen(id) {
  if (!S.user) return loginOeffnen(t('vt_login'));
  const a = VT.kunst.find((x) => x.id === id);
  if (!confirm(t('vt_k_frage').replace('{n}', VT.preis).replace('{t}', a ? a.titel : ''))) return;
  try {
    const d = await api('api/vitrine/artwork/' + encodeURIComponent(id) + '/uebernehmen', { method: 'POST' });
    if (a) a.habe = true;
    if (d.konto && S.user) { S.user = { ...S.user, ...d.konto }; if (typeof AW === 'object') AW.konto = d.konto; }
    zeichneKunst();
    toast(d.bezahlt ? t('vt_k_gekauft').replace('{n}', d.bezahlt) : t('vt_k_schon'));
    kunstEinsetzen(d);
  } catch (e) { if (!gate(e)) toast(e.message); }
}

async function kunstInBinder(id) {
  try {
    const d = await api('api/vitrine/artwork/' + encodeURIComponent(id) + '/uebernehmen', { method: 'POST' });
    kunstEinsetzen(d);
  } catch (e) { if (!gate(e)) toast(e.message); }
}

/** Die gekaufte Seite als neue Binderseite anhängen: Ankerkarten in ihre Fächer, der Rest
 *  ist Kunstfläche. Ohne die Ankerkarten ergäbe das Bild keinen Sinn — es wurde um sie
 *  herum gemalt. */
function kunstEinsetzen(d) {
  if (!S.binder) return toast(t('vt_k_kein_binder'));
  if (d.layout && d.layout !== S.binder.layout) return toast(t('aw_layout_anders'));
  merken('kunst');
  const [cols, rows] = LAYOUTS[d.layout || S.binder.layout] || [3, 3];
  const pp = cols * rows;
  while (S.binder.items.length % pp !== 0) S.binder.items.push({ type: 'empty' });
  const start = S.binder.items.length;
  const seite = Math.floor(start / pp);
  for (let i = 0; i < pp; i++) {
    const anker = d.anker && d.anker[String(i)];
    S.binder.items.push(anker ? { type: 'card', id: anker }
                              : { type: 'art', artwork: d.artwork, slot: i, layout: d.layout });
  }
  S.seite = seite;
  speichern();
  vitrineSchliessen();
  ansicht('suche');
  zeichneBinder();
  toastUndo(t('vt_k_eingesetzt'));
}

/** Eine Binderseite als Vorschau zeichnen. `blatt` kommt vom Server und enthält das Raster
 *  samt leeren Fächern — nur so sieht die Kachel aus wie eine echte aufgeschlagene Seite. */
/** Binder-Kachel als ein Bild vom Server (die ersten drei Seiten als Stapel). Vorher baute
 *  vitrineBlatt() denselben Stapel aus bis zu 27 Kartenbildern – 193 Anfragen auf der
 *  Startseite. Der Stand in der Adresse macht das Bild ein Jahr lang cachebar. */
function stapelBild(b) {
  if (!b || !b.id) return '';
  const stand = String(b.updated_at || b.veroeffentlicht_at || '').replace(/[^0-9]/g, '').slice(0, 14);
  if (!stand) return b.blatt ? vitrineBlatt(b.blatt, false) : '';
  return `<div class="vt-blatt stapel bild"><img loading="lazy" decoding="async" src="api/binders/${encodeURIComponent(b.id)}/stapel.webp?s=${stand}" alt=""></div>`;
}

function vitrineBlatt(blatt, doppelt, einzeln) {
  if (!blatt || !blatt.seiten || !blatt.seiten.length) return '';
  const fach = (f, spalten, zeilen) => {
    if (f.art === 'card') return `<div class="vt-fach"><img loading="lazy" src="${imgUrl(f.id)}" alt=""></div>`;
    if (f.art === 'artwork') {
      // Auch in der Vorschau bleibt es eine Binderseite: jedes Fach zeigt seinen Ausschnitt
      // des Artworks, die Fugen dazwischen sind sichtbar. Gerechnet wie im Binder selbst,
      // mit 4 Einheiten Fuge auf 63 × 88 Kartenmaß.
      const [c2, r2] = LAYOUTS[f.layout] || [spalten, zeilen];
      const pw = c2 * 63 + (c2 - 1) * 4, ph = r2 * 88 + (r2 - 1) * 4;
      const sp = f.slot % c2, ze = Math.floor(f.slot / c2);
      return `<div class="vt-fach"><span class="artfach"><img loading="lazy" src="api/artwork/${encodeURIComponent(f.id)}/bild?v=vorschau" alt=""
        style="width:${(pw / 63 * 100).toFixed(2)}%;height:${(ph / 88 * 100).toFixed(2)}%;left:-${(sp * 67 / 63 * 100).toFixed(2)}%;top:-${(ze * 92 / 88 * 100).toFixed(2)}%"></span></div>`;
    }
    if (f.art === 'dex') return `<div class="vt-fach"><img loading="lazy" src="api/img/dex/${f.dex}" alt=""></div>`;
    return '<div class="vt-fach leer"></div>';
  };
  // Die Fuge muss zum Ausschnitt passen: 4 Einheiten auf eine Zeile aus <spalten> Karten.
  // In Prozent der Gesamtbreite gerechnet, damit sie bei jeder Kachelgröße stimmt.
  const fuge = (sp) => (4 / (sp * 63 + (sp - 1) * 4) * 100).toFixed(3) + '%';
  // Jede Seite bringt ihr eigenes Raster mit — einzelne Seiten dürfen vom Standard des
  // Binders abweichen. Ältere Antworten lieferten je Seite nur die Fächerliste.
  const seitenDaten = (nr) => {
    const d = blatt.seiten[nr];
    return Array.isArray(d)
      ? { spalten: blatt.spalten, zeilen: blatt.zeilen, faecher: d }
      : { spalten: d.spalten || blatt.spalten, zeilen: d.zeilen || blatt.zeilen, faecher: d.faecher || [] };
  };
  const seite = (nr, klasse) => {
    const d = seitenDaten(nr);
    return `<div class="vt-seite ${klasse || ''}" style="grid-template-columns:repeat(${d.spalten},1fr);gap:${fuge(d.spalten)}">
      ${d.faecher.map((f) => fach(f, d.spalten, d.zeilen)).join('')}</div>`;
  };
  if (doppelt && blatt.seiten.length > 1) {
    return `<div class="vt-blatt dop">${seite(0)}${seite(1)}</div>`;
  }
  // Eine einzelne Seite ohne Fächer-Stapel — für Kunstseiten, die nur aus einer bestehen.
  if (einzeln) return `<div class="vt-blatt einzeln">${seite(0, 'vorn')}</div>`;
  // Standard: die ersten Seiten leicht versetzt hintereinander — das sieht aus wie ein Binder,
  // in dem schon geblättert wurde, und zeigt auf einen Blick, dass mehr als eine Seite drin ist.
  const mitte = blatt.seiten.length > 1 ? seite(1, 'hinten2') : '';
  const hinten = blatt.seiten.length > 2 ? seite(2, 'hinten3') : '';
  return `<div class="vt-blatt stapel">${seite(0, 'vorn')}${mitte}${hinten}</div>`;
}

const VT_ARTEN = { dex: 'vt_a_dex', artwork: 'vt_a_artwork', kuenstler: 'vt_a_kuenstler',
                   master: 'vt_a_master', pokemon: 'vt_a_pokemon', frei: '' };

function zeichneVitrine() {
  $('vt-gitter').classList.remove('kunst');
  if (!VT.binder.length) {
    $('vt-gitter').innerHTML = `<div class="vt-leer">${t('vt_leer')}</div>`;
    return;
  }
  // Die ganze Kachel ist der Klick. Vorher waren es vier Stellen — Bild, Titel, Besitzer,
  // Herz —, und ausgerechnet die Zeile mit Kartenzahl, Seiten und Wert war tot.
  $('vt-gitter').innerHTML = VT.binder.map((b) => `
    <div class="vt-karte" role="button" tabindex="0" onclick="binderGross('${esc(b.id)}')"
         onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();binderGross('${esc(b.id)}')}">
      <div style="padding:10px 10px 0;background:${VT.doppelseite ? '#14161a' : 'var(--panel2)'}">
        ${VT.doppelseite ? vitrineBlatt(b.blatt, true) : stapelBild(b)}
      </div>
      <div class="vt-txt">
        <div class="vt-name">${esc(b.name)}</div>
        <div class="vt-meta"><a onclick="event.stopPropagation();profilseiteOeffnen('${esc(b.besitzer)}')" style="color:var(--blau);cursor:pointer">${esc(b.besitzer)}</a> · ${b.karten} ${t('karten_wort')} · ${b.seiten} ${t('seiten_wort')}${
          b.wert ? ` · ≈ ${anEur(b.wert, 0)}${b.bew30 != null ? ` <span class="${b.bew30 > 0.05 ? 'an-plus' : b.bew30 < -0.05 ? 'an-minus' : ''}">${anProz(b.bew30)}</span>` : ''}` : ''}</div>
        <div class="vt-fuss">
          <button class="vt-herz ${b.gestimmt ? 'an' : ''}" onclick="event.stopPropagation();vitrineStimme('${esc(b.id)}')">
            ${ic('herz', 15, b.gestimmt ? 'voll' : '')}<span>${b.stimmen}</span></button>
          ${VT_ARTEN[b.art] ? `<span class="vt-marke ${b.art}">${t(VT_ARTEN[b.art])}</span>` : ''}
        </div>
      </div>
    </div>`).join('');
}

/* Ein Binder öffnet dieselbe Großansicht wie eine Kunstseite. Vorher verließ ein Klick auf
   einen Binder die Vitrine per Adresswechsel, ein Klick auf eine Kunstseite legte ein Overlay
   darüber: zwei Zurück-Wege im selben Raster. Jetzt blättert man in beiden Bereichen durch die
   Kacheln; „Ganzen Binder öffnen" führt weiter, wenn man wirklich hineinwill. */
const BW = { liste: [], pos: 0, box: null, seite: 0 };

function binderGross(id) {
  BW.liste = VT.binder || [];
  BW.pos = Math.max(0, BW.liste.findIndex((b) => b.id === id));
  BW.seite = 0;
  if (!BW.liste.length) return;
  if (!BW.box) {
    BW.box = document.createElement('div');
    BW.box.className = 'kw-box';
    BW.box.onclick = (ev) => { if (ev.target === BW.box || ev.target.classList.contains('kw-buehne')) binderGrossZu(); };
    document.body.appendChild(BW.box);
    document.addEventListener('keydown', binderGrossTaste);
  }
  BW.box.classList.remove('hidden');
  document.body.classList.add('kw-offen');
  zeichneBinderGross();
}
function binderGrossZu() {
  if (BW.box) BW.box.classList.add('hidden');
  document.body.classList.remove('kw-offen');
}
function binderGrossTaste(ev) {
  if (!BW.box || BW.box.classList.contains('hidden')) return;
  if (ev.key === 'Escape') { ev.preventDefault(); binderGrossZu(); }
  else if (ev.key === 'ArrowRight') { ev.preventDefault(); binderGrossWechsel(1); }
  else if (ev.key === 'ArrowLeft') { ev.preventDefault(); binderGrossWechsel(-1); }
}
function binderGrossWechsel(d) {
  if (!BW.liste.length) return;
  BW.pos = (BW.pos + d + BW.liste.length) % BW.liste.length;
  BW.seite = 0;
  zeichneBinderGross();
}
function binderGrossSeite(d) {
  const b = BW.liste[BW.pos];
  const n = (((b && b.blatt) || {}).seiten || []).length;
  BW.seite = Math.min(Math.max(0, BW.seite + d), Math.max(0, n - 1));
  zeichneBinderGross();
}

function zeichneBinderGross() {
  const b = BW.liste[BW.pos];
  if (!b || !BW.box) return;
  const [sp, ze] = LAYOUTS[b.layout] || [3, 3];
  const pw = sp * 63 + (sp - 1) * 4, ph = ze * 88 + (ze - 1) * 4;
  const blatt = b.blatt || {};
  const alle = blatt.seiten || [];
  const seiten = alle.length;
  BW.box.innerHTML = `
    <div class="kw-kopf" onclick="event.stopPropagation()">
      <div class="kw-titel"><strong>${esc(b.name)}</strong>
        <span>${esc(b.besitzer)} · ${b.karten} ${t('karten_wort')} · ${b.seiten} ${t('seiten_wort')}${
          b.wert ? ` · ≈ ${anEur(b.wert, 0)}` : ''}${b.stimmen ? ' · ' + b.stimmen + ' ♥' : ''}</span></div>
      <div style="flex:1"></div>
      <button class="kw-x" onclick="binderGrossZu()" title="${t('schliessen_t')}" aria-label="${t('schliessen_t')}">✕</button>
    </div>
    <div class="kw-buehne">
      <button class="kw-pfeil" onclick="event.stopPropagation();binderGrossWechsel(-1)"
              aria-label="${t('bv_zurueck')}" ${BW.liste.length < 2 ? 'hidden' : ''}>‹</button>
      <div class="kw-seite" style="aspect-ratio:${pw}/${ph}" onclick="event.stopPropagation()">
        ${seiten ? vitrineBlatt({ ...blatt, seiten: [alle[Math.min(BW.seite, seiten - 1)]] }, false, true) : ''}</div>
      <button class="kw-pfeil" onclick="event.stopPropagation();binderGrossWechsel(1)"
              aria-label="${t('bv_vor')}" ${BW.liste.length < 2 ? 'hidden' : ''}>›</button>
    </div>
    <div class="kw-fuss" onclick="event.stopPropagation()">
      <button class="vt-herz ${b.gestimmt ? 'an' : ''}" onclick="vitrineStimme('${esc(b.id)}').then(zeichneBinderGross)">
        ${ic('herz', 15, b.gestimmt ? 'voll' : '')}<span>${b.stimmen}</span></button>
      ${seiten > 1 ? `<span class="kw-blaettern">
        <button onclick="binderGrossSeite(-1)" ${BW.seite === 0 ? 'disabled' : ''} aria-label="${t('bv_zurueck')}">‹</button>
        <span>${t('vt_seite_n').replace('{n}', BW.seite + 1).replace('{g}', seiten)}</span>
        <button onclick="binderGrossSeite(1)" ${BW.seite >= seiten - 1 ? 'disabled' : ''} aria-label="${t('bv_vor')}">›</button>
      </span>` : ''}
      <span class="kw-stand">${BW.pos + 1} / ${BW.liste.length}</span>
      <button class="btn sekundaer" onclick="vitrineOeffnen('${esc(b.id)}')">${t('vt_ganz_oeffnen')}</button>
      <button class="kw-melden" onclick="binderGrossZu();meldenOeffnen('binder','${esc(b.id)}')" title="${t('md_t')}">${ic('fahne', 15)}</button>
    </div>`;
}

function vitrineOeffnen(id) {
  // Der Verlaufseintrag, auf den „Zurück“ später springt, soll die Vitrine sein – nicht die Werkbank.
  binderGrossZu();
  try { sessionStorage.setItem('bp_von_vitrine', '1'); history.replaceState(null, '', location.pathname + '#vitrine'); } catch (e) {}
  location.assign(APP_BASIS + '/ansicht/' + id);
}

async function vitrineStimme(id) {
  if (!S.user) return loginOeffnen(t('vt_login'));
  try {
    const d = await api('api/vitrine/stimme', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ binder_id: id }),
    });
    const b = VT.binder.find((x) => x.id === id);
    if (b) { b.gestimmt = d.gestimmt; b.stimmen = d.stimmen; }
    zeichneVitrine();
    return d;
  } catch (e) { if (!gate(e)) toast(e.message); }
}

// ---------- Profil ----------
async function vitrineProfilDialog() {
  if (!S.user) return loginOeffnen(t('vt_login'));
  $('vpf-fehler').textContent = '';
  try {
    const d = await api('api/profil');
    $('vpf-name').value = (d.profil && d.profil.name) || '';
    $('vpf-text').value = (d.profil && d.profil.kurztext) || '';
    if ($('vpf-tausch')) $('vpf-tausch').checked = !!(d.profil && d.profil.tauschliste);
  } catch (e) {}
  modalOeffnen('modal-profil');
}
// Wie beim Geburtsdatum: wer hier landet, wollte eigentlich etwas veröffentlichen.
let profilDanach = null;
async function profilSpeichern() {
  $('vpf-fehler').textContent = '';
  try {
    await api('api/profil', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: $('vpf-name').value, kurztext: $('vpf-text').value,
                             tauschliste: $('vpf-tausch') && $('vpf-tausch').checked }),
    });
    toast(t('gespeichert'));
    const weiter = profilDanach; profilDanach = null;
    if (weiter) dialogWechsel(weiter); else modalSchliessen();
  } catch (e) { $('vpf-fehler').textContent = e.message; }
}

// ---------- Geburtsdatum nachtragen ----------
/* Veröffentlichen verlangt ein Geburtsdatum (ab 16, Art. 8 DSGVO). Fehlte es, wurde der
   Dialog geschlossen, nach dem Datum gefragt — und danach *immer* der Binder umgeschaltet.
   Wer eine Kunstseite freigeben wollte, stand am Ende ohne Freigabe da und ohne Hinweis,
   warum. Jetzt merkt sich der Dialog, was angefangen wurde, und macht dort weiter. */
/** Datumsfeld von Hand: das native <input type=date> zeigte im deutschen Dialog
 *  „mm/dd/yyyy“ und vertauschte Tag und Monat. Getippt wird TT.MM.JJJJ, gesendet ISO. */
function gebTippen(el) {
  const z = el.value.replace(/\D/g, '').slice(0, 8);
  let s = z.slice(0, 2);
  if (z.length > 2) s += '.' + z.slice(2, 4);
  if (z.length > 4) s += '.' + z.slice(4);
  el.value = s;
}
/** ISO-Datum aus der Datenbank in die Anzeige TT.MM.JJJJ. */
function isoZuDatum(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '');
  return m ? `${m[3]}.${m[2]}.${m[1]}` : '';
}
function gebIso(v) {
  const m = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec((v || '').trim());
  if (!m) return '';
  const tag = +m[1], monat = +m[2], jahr = +m[3];
  const d = new Date(Date.UTC(jahr, monat - 1, tag));
  if (d.getUTCFullYear() !== jahr || d.getUTCMonth() !== monat - 1 || d.getUTCDate() !== tag) return '';
  if (jahr < 1900 || d > new Date()) return '';
  return `${m[3]}-${m[2]}-${m[1]}`;
}
let gebDanach = null;
function geburtsdatumOeffnen(danach) {
  gebDanach = typeof danach === 'function' ? danach : null;
  $('gb-fehler').textContent = '';
  modalOeffnen('modal-geb');
}
async function geburtsdatumSpeichern() {
  $('gb-fehler').textContent = '';
  try {
    await api('api/profil/geburtsdatum', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ geburtsdatum: gebIso($('gb-datum').value) }),
    });
    const weiter = gebDanach; gebDanach = null;
    // Auch hier über dialogWechsel: sonst schließt der popstate des eigenen Zugehens
    // gleich den Dialog wieder, der danach aufgeht.
    if (weiter) dialogWechsel(weiter); else { modalSchliessen(); veroeffentlichenUmschalten(); }
  } catch (e) { $('gb-fehler').textContent = e.message; }
}

// ---------- Veröffentlichen ----------
async function veroeffentlichenUmschalten() {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  if (!S.binder) return;
  if (!S.user) return loginOeffnen(t('vt_login'));
  if (!S.binder.id) return toast(t('binder_leer'));
  const jetzt = !!S.binder.sichtbar;
  if (!jetzt && !confirm(t('vt_warnung'))) return;
  try {
    await api('api/vitrine/veroeffentlichen', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ binder_id: S.binder.id, oeffentlich: !jetzt }),
    });
    S.binder.sichtbar = jetzt ? 0 : 1;
    vitrineKnopf();
    toast(jetzt ? t('vt_zurueckgezogen') : t('vt_steht_drin'));
  } catch (e) {
    const code = e.detail && e.detail.code;
    if (code === 'geburtsdatum') return geburtsdatumOeffnen();
    if (code === 'kein_profil') return vitrineProfilDialog();
    if (!gate(e)) toast((e.detail && e.detail.text) || e.message);
  }
}

// ---------- Melden ----------
function meldenOeffnen(typ, id) { VT.meldung = { typ, id }; $('md-grund').value = ''; modalOeffnen('modal-melden'); }
async function meldenSenden() {
  if (!VT.meldung) return;
  try {
    await api('api/vitrine/meldung', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ziel_typ: VT.meldung.typ, ziel_id: VT.meldung.id, grund: $('md-grund').value }),
    });
    modalSchliessen(); toast(t('md_danke'));
  } catch (e) { toast(e.message); }
}

// ---------- Fremden Binder ansehen und übernehmen ----------
// Das Übernehmen ist der eigentliche Grund, warum jemand fremde Binder anschaut —
// und es kostet nichts: übernommen wird die Liste der Karten, nicht die Karten.
const POF = { name: '', binder: null };

async function ansichtBegleitung() {
  if (!S.binder || !S.nurAnsicht || $('vt-ansichtleiste')) return;
  let d = null;
  try { d = await api('api/vitrine/binder/' + encodeURIComponent(S.binder.id)); } catch (e) { return; }
  POF.binder = d;
  const leiste = document.createElement('div');
  leiste.className = 'vt-ansichtleiste';
  leiste.id = 'vt-ansichtleiste';
  leiste.innerHTML = `
    <span class="vt-wer">${d.besitzer
      ? t('vt_von').replace('{n}', `<a onclick="profilseiteOeffnen('${esc(d.besitzer)}')">${esc(d.besitzer)}</a>`)
      : t('vt_geteilt')}</span>
    ${d.oeffentlich ? `<button class="vt-herz ${d.gestimmt ? 'an' : ''}" id="vt-herz-einzel" onclick="ansichtStimme()">
        ${ic('herz', 15, d.gestimmt ? 'voll' : '')}<span>${d.stimmen}</span></button>` : ''}
    <div style="flex:1"></div>
    <span class="vt-hab" id="vt-hab"></span>
    <button class="btn sekundaer" style="font-size: var(--t-s)" onclick="seiteUebernehmen()">${t('vt_seite_uebernehmen')}</button>
    <button class="btn sekundaer hidden" style="font-size: var(--t-s)" id="vt-fehlend" onclick="fehlendeUebernehmen()">${t('vt_fehlende')}</button>
    <button class="btn" style="font-size: var(--t-s)" onclick="binderKopieren()">${t('vt_binder_kopieren')}</button>
    <span class="vt-hab" id="vt-kunstkosten"></span>
    ${d.oeffentlich ? `<button style="font-size: var(--t-xs);color:var(--mut)" onclick="meldenOeffnen('binder','${esc(S.binder.id)}')" title="${t('md_t')}">${ic('fahne', 15)}</button>` : ''}`;
  const banner = document.querySelector('.ansicht-banner');
  (banner || document.querySelector('.kopf')).after(leiste);
  if (d.oeffentlich) document.body.classList.add('von-vitrine');   // öffentliche Binder haben immer einen Weg zurück
  ansichtGroesse();
  habStandZeigen();
  kunstkostenZeigen();
}

/** „12 von 27 hast du schon“ – nur mit Konto, denn ohne Sammlung gibt es nichts zu vergleichen. */
function habStandZeigen() {
  const feld = $('vt-hab');
  if (!feld || !S.binder) return;
  const karten = S.binder.items.filter((i) => i && i.type === 'card');
  if (!S.user || !karten.length) { feld.textContent = ''; return; }
  const habe = karten.filter(besitzt).length;
  feld.textContent = t('vt_hab').replace('{n}', habe).replace('{g}', karten.length);
  const knopf = $('vt-fehlend');
  if (knopf) knopf.classList.toggle('hidden', habe >= karten.length);
}

/** Nur die Karten übernehmen, die in der eigenen Sammlung fehlen. */
async function fehlendeUebernehmen() {
  if (!S.user) return loginOeffnen(t('vt_login'));
  const fehlen = S.binder.items.filter((i) => i && i.type === 'card' && !besitzt(i)).map(fremdesFach);
  if (!fehlen.length) return toast(t('vt_nichts_fehlt'));
  const ziel = await zielBinder();
  if (!ziel) return uebernehmenAlsNeu(fehlen, t('vt_kopie_name').replace('{n}', S.binder.name));
  if (!confirm(t('vt_seite_frage').replace('{n}', fehlen.length).replace('{b}', ziel.name))) return;
  const pp = LAYOUTS[ziel.layout] ? (LAYOUTS[ziel.layout][0] * LAYOUTS[ziel.layout][1]) : 9;
  while (ziel.items.length % pp) ziel.items.push({ type: 'empty' });
  ziel.items.push(...fehlen);
  try {
    await api('api/binders/' + ziel.id, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(ziel),
    });
    toast(t('vt_uebernommen').replace('{n}', fehlen.length).replace('{b}', ziel.name));
  } catch (e) { if (!gate(e)) toast(e.message); }
}

async function ansichtStimme() {
  if (!S.user) return loginOeffnen(t('vt_login'));
  try {
    const d = await api('api/vitrine/stimme', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ binder_id: S.binder.id }),
    });
    const k = $('vt-herz-einzel');
    k.classList.toggle('an', d.gestimmt);
    k.innerHTML = `${ic('herz', 15, d.gestimmt ? 'voll' : '')}<span>${d.stimmen}</span>`;
  } catch (e) { if (!gate(e)) toast(e.message); }
}

/** Wohin übernehmen? In den zuletzt benutzten eigenen Binder, sonst in einen neuen. */
async function zielBinder() {
  for (const id of binderIds()) {
    if (id === S.binder.id) continue;
    try {
      const b = await api('api/binders/' + id);
      return b;
    } catch (e) { /* gelöscht */ }
  }
  return null;
}

/** Aus einem fremden Binder kommen nur Karte, Variante und Sprache mit – nicht das
 *  Häkchen, der Zustand oder ein KI-Artwork des anderen (das ist dessen bezahltes Bild). */
function fremdesFach(i) {
  if (!i || i.type === 'art') return { type: 'empty' };
  const n = { type: i.type };
  if (i.id) n.id = i.id;
  if (i.dex) n.dex = i.dex;
  if (i.variant && i.variant !== 'normal') n.variant = i.variant;
  if (i.sprache) n.sprache = i.sprache;
  return n;
}

async function seiteUebernehmen() {
  if (!S.user) return loginOeffnen(t('vt_login'));
  const teil = seiteFaecher(S.seite).filter(Boolean);
  if (!teil.length) return toast(t('vt_seite_leer'));
  const ziel = await zielBinder();
  if (!ziel) return uebernehmenAlsNeu(teil, t('vt_kopie_name').replace('{n}', S.binder.name));
  if (!confirm(t('vt_seite_frage').replace('{n}', teil.length).replace('{b}', ziel.name))) return;
  while (ziel.items.length % pp) ziel.items.push({ type: 'empty' });
  ziel.items.push(...teil.map(fremdesFach));
  try {
    await api('api/binders/' + ziel.id, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(ziel),
    });
    toast(t('vt_uebernommen').replace('{n}', teil.length).replace('{b}', ziel.name));
  } catch (e) { if (!gate(e)) toast(e.message); }
}

/** Was das Kopieren kostet: der Plan nichts, die Kunstseiten darin je ARTWORK_FREMD
 *  Credits. Der Hinweis steht vor dem Klick, nicht danach. */
async function kunstkostenZeigen() {
  const feld = $('vt-kunstkosten');
  if (!feld || !S.binder) return;
  try {
    const d = await api('api/vitrine/binder/' + encodeURIComponent(S.binder.id) + '/kosten');
    POF.kunstkosten = d;
    feld.textContent = d.zu_zahlen
      ? t('vt_k_binder_kosten').replace('{n}', d.zu_zahlen).replace('{c}', d.credits) : '';
  } catch (e) { feld.textContent = ''; }
}

async function binderKopieren() {
  if (!S.user) return loginOeffnen(t('vt_login'));
  const k = POF.kunstkosten;
  let mitKunst = true;
  if (k && k.zu_zahlen) {
    // Der Plan bleibt frei; nur die Kunstseiten kosten. Wer sie nicht will, kopiert ohne sie.
    mitKunst = confirm(t('vt_k_binder_frage').replace('{n}', k.zu_zahlen).replace('{c}', k.credits));
    if (mitKunst) {
      try {
        const d = await api('api/vitrine/binder/' + encodeURIComponent(S.binder.id) + '/artwork_kaufen', { method: 'POST' });
        if (d.konto && S.user) S.user = { ...S.user, ...d.konto };
        if (d.bezahlt) toast(t('vt_k_gekauft').replace('{n}', d.bezahlt));
      } catch (e) { if (!gate(e)) toast(e.message); return; }
    }
  }
  const items = S.binder.items.map(fremdesFach)
    .map((i) => (!mitKunst && i && i.type === 'art') ? { type: 'empty' } : i);
  await uebernehmenAlsNeu(items, t('vt_kopie_name').replace('{n}', S.binder.name));
}

async function uebernehmenAlsNeu(items, name) {
  try {
    const res = await api('api/binders', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name.slice(0, 60), mode: 'custom', layout: S.binder.layout,
                             options: S.binder.options || {}, items }),
    });
    merkeBinderId(res.id);
    toast(t('vt_kopiert'));
    setTimeout(() => { location.assign(APP_BASIS + '/binder/' + res.id); }, 700);
  } catch (e) { if (!gate(e)) toast(e.message); }
}

// ---------- Öffentliche Profilseite ----------
async function profilseiteOeffnen(name) {
  POF.name = name;
  POF.vonVitrine = !$('vitrine').classList.contains('hidden');   // nach dem Schließen dorthin zurück
  // Die Vitrine wird nur verdeckt, nicht geschlossen: ihr Ebenen-Eintrag bleibt darunter
  // liegen, damit Zurück von der Profilseite wieder in der Vitrine landet.
  if (POF.vonVitrine) $('vitrine').classList.add('hidden'); else vitrineSchliessen();
  planerSchliessen();
  $('profil-oeffentlich').classList.remove('hidden');
  ebeneOeffnen(profilseiteSchliessen);
  $('po-gitter').innerHTML = `<div class="vt-leer">${lader('gross', t('laedt'))}</div>`;
  try {
    const d = await api('api/vitrine/profil/' + encodeURIComponent(name));
    POF.daten = d;
    POF.sortierung = POF.sortierung || 'neu';
    POF.art = POF.art || '';
    $('po-name').textContent = d.profil.name;
    $('po-text').textContent = d.profil.kurztext || '';
    $('po-seit').textContent = d.profil.seit ? t('po_seit').replace('{d}', d.profil.seit) : '';
    // Ein Profil ohne Zahlen sagt nichts. Drei genügen: wie viel, wie groß, wie gut angekommen.
    $('po-zahlen').innerHTML = [
      [d.profil.binder_anzahl, t('po_binder_zahl')],
      [d.profil.karten, t('po_karten')],
      [d.profil.herzen, t('po_herzen')],
    ].map(([n, l]) => `<div class="po-zahl"><b>${n == null ? '–' : n}</b><span>${l}</span></div>`).join('');
    const bild = $('po-bild');
    // Ohne eigenes Bild die erste Karte des neuesten Binders nehmen — besser als eine leere Fläche
    const ersatz = (d.binder[0]?.vorschau || []).find((v) => v.art === 'card');
    const bildId = d.profil.avatar_card || (ersatz && ersatz.id);
    bild.classList.toggle('hidden', !bildId);
    if (bildId) bild.src = imgUrl(bildId);
    zeichneProfilBinder();
    zeichneTauschliste(d.tausch || []);
  } catch (e) {
    $('po-gitter').innerHTML = `<div class="vt-leer">${esc(e.message || '')}</div>`;
  }
}
/** Die Binder eines Sammlers: sortierbar und nach Art filterbar. Wer zwölf Binder hat,
 *  braucht einen Weg, den einen zu finden, den er zeigen wollte. */
function zeichneProfilBinder() {
  const d = POF.daten;
  if (!d) return;
  let liste = (d.binder || []).slice();
  if (POF.art) liste = liste.filter((b) => b.art === POF.art);
  if (POF.sortierung === 'herzen') liste.sort((a, b) => b.stimmen - a.stimmen);
  else if (POF.sortierung === 'gross') liste.sort((a, b) => b.karten - a.karten);
  else liste.sort((a, b) => (b.veroeffentlicht_at || '').localeCompare(a.veroeffentlicht_at || ''));

  const arten = [...new Set((d.binder || []).map((b) => b.art))].filter((a) => VT_ARTEN[a]);
  const knopf = (feld, wert, text) => `<button class="chip ${POF[feld] === wert ? 'on' : ''}"
    onclick="profilFilter('${feld}','${wert}')">${esc(text)}</button>`;
  $('po-filter').innerHTML = `
    <span class="vt-lbl">${t('po_sortierung')}</span>
    ${knopf('sortierung', 'neu', t('po_s_neu'))}
    ${knopf('sortierung', 'herzen', t('po_s_herzen'))}
    ${knopf('sortierung', 'gross', t('po_s_gross'))}
    ${arten.length > 1 ? `<span class="trenner"></span><span class="vt-lbl">${t('vt_f_art')}</span>
      ${knopf('art', '', t('vt_f_alle'))}${arten.map((a) => knopf('art', a, t(VT_ARTEN[a]))).join('')}` : ''}`;

  $('po-gitter').innerHTML = liste.map((b) => `
    <div class="vt-karte">
      <div onclick="vitrineOeffnen('${esc(b.id)}')" style="cursor:pointer;padding:10px 10px 0;background:var(--panel2)">
        ${stapelBild(b)}
      </div>
      <div class="vt-txt">
        <div class="vt-name" onclick="vitrineOeffnen('${esc(b.id)}')" style="cursor:pointer">${esc(b.name)}</div>
        <div class="vt-meta">${b.karten} ${t('karten_wort')} · ${b.seiten} ${t('seiten_wort')}</div>
        <div class="vt-fuss">
          <span class="vt-herz">${ic('herz', 15, 'voll')}<span>${b.stimmen}</span></span>
          ${VT_ARTEN[b.art] ? `<span class="vt-marke ${b.art}">${t(VT_ARTEN[b.art])}</span>` : ''}
        </div>
      </div>
    </div>`).join('') || `<div class="vt-leer">${t('po_leer')}</div>`;
}

/** Doppelte Karten des Sammlers. Bewusst nur eine Liste: keine Nachrichten, keine Preise,
 *  kein Kontaktfeld — die Vitrine bleibt frei von Text zwischen Nutzern. */
function zeichneTauschliste(liste) {
  const box = $('po-tausch');
  if (!box) return;
  box.classList.toggle('hidden', !liste.length);
  if (!liste.length) return;
  box.innerHTML = `<h3 class="stat-h" style="margin-top:26px">${t('po_tausch')}</h3>
    <div class="tausch-gitter">${liste.map((k) => `
      <div class="tausch-karte" title="${esc(k.name)} · ${esc(k.set)}">
        <img loading="lazy" src="${imgUrl(k.id)}" alt="${esc(k.name)}">
        <span class="tausch-n">${k.ueber}×</span>
      </div>`).join('')}</div>
    <div class="unter" style="font-size: var(--t-s);margin-top:8px">${t('po_tausch_u')}</div>`;
}

function profilFilter(feld, wert) {
  POF[feld] = feld === 'sortierung' ? wert : (POF[feld] === wert ? '' : wert);
  zeichneProfilBinder();
}

function profilseiteSchliessen() {
  if ($('profil-oeffentlich').classList.contains('hidden')) return;
  ebeneAufraeumen(profilseiteSchliessen);
  $('profil-oeffentlich').classList.add('hidden');
  if (POF.vonVitrine) { POF.vonVitrine = false; $('vitrine').classList.remove('hidden'); reiterSetzen('seg-vitrine'); }
}

// ---------- Zurück-Taste ----------
// Auf dem Handy ist die Wischgeste nach rechts der meistbenutzte Knopf überhaupt.
// Ohne eigenen Verlaufseintrag verlässt sie beim ersten offenen Dialog die ganze
// App — man verliert die Ansicht und landet auf der vorherigen Seite. Jede Ebene
// (Dialog, Planer, Vitrine, Durchblättern) legt deshalb einen Eintrag an; Zurück
// schließt die oberste Ebene, statt zu navigieren.
const EBENEN = [];

/* ---------- Adressen ----------
   Jede Ansicht hat eine eigene Adresse unter /app: /app/vitrine, /app/planer,
   /app/binder/<id> und so fort. Vorher hing der Zustand am Hash und wurde nur für
   „start" und „binder/<id>" überhaupt gesetzt — wer in der Vitrine neu lud, landete
   in der Suche. Alte Hash-Adressen (#ansicht/<id> in geteilten Links) werden beim
   Start weiterhin gelesen und einmalig auf die neue Form umgeschrieben. */

const APP_BASIS = '/app';

/** Adresszeile nachführen, ohne Verlaufseintrag und ohne popstate (das würde die
 *  oberste Ebene schließen). Der Name bleibt, die Adresse ist jetzt ein Pfad. */
function hashSetzen(h) {
  const ziel = APP_BASIS + (h ? '/' + h : '');
  if (location.pathname === ziel && !location.hash) return;
  try { history.replaceState(history.state, '', ziel); } catch (e) { /* egal */ }
}

/** Die aktuelle Route als „vitrine", „binder/xy" … — egal ob als Pfad oder als
 *  alter Hash notiert. */
/** Nach dem Schließen einer Vollbildansicht: zurück auf den offenen Binder, sonst
 *  auf die nackte App-Adresse. */
function routeZurueck() {
  if (S.nurAnsicht) return;
  hashSetzen(S.binder && S.binder.id ? 'binder/' + S.binder.id : '');
}

function routeLesen() {
  const h = location.hash.slice(1);
  if (h) return h;
  const p = location.pathname;
  if (p === APP_BASIS || p === APP_BASIS + '/') return '';
  if (p.startsWith(APP_BASIS + '/')) return p.slice(APP_BASIS.length + 1).replace(/\/+$/, '');
  return '';
}

// Ansichten, die eine eigene Adresse führen
const ROUTEN = ['start', 'suche', 'planer', 'sammlung', 'vitrine', 'markt', 'auswertung', 'profil'];

function ebeneOeffnen(schliesser) {
  EBENEN.push(schliesser);
  try { history.pushState({ bpEbene: EBENEN.length }, ''); } catch (e) { /* alte Browser */ }
}

/** Eine Ansicht wurde direkt geschlossen (Reiterwechsel, nicht über Zurück): den Eintrag
 *  aus dem Stapel nehmen. Liegt er obenauf, geht auch der Verlaufseintrag zurück. */
function ebeneAufraeumen(schliesser) {
  const i = EBENEN.lastIndexOf(schliesser);
  if (i === -1) return;
  EBENEN.splice(i, 1);
  // Der Verlaufseintrag bleibt bewusst stehen. Ihn hier per history.back() einzusammeln wäre
  // asynchron und käme dem pushState der Ansicht in die Quere, die gerade aufgeht.
  // Ein übrig gebliebener Eintrag ist harmlos: der popstate-Handler prüft dann die Route.
}

/** Ebene programmatisch schließen (Klick auf ✕). Nimmt den Verlaufseintrag mit. */
function ebeneZu(schliesser) {
  const i = EBENEN.lastIndexOf(schliesser);
  if (i === -1) { schliesser(); return; }
  if (i === EBENEN.length - 1 && history.state && history.state.bpEbene === EBENEN.length) {
    // Oberste Ebene mit eigenem Verlaufseintrag: nur zurückgehen – popstate nimmt sie vom
    // Stapel und schließt sie. Vorher wurde hier geschlossen UND popstate schloss danach die
    // nächste Ebene darunter (Planer ging mit dem Detail-Dialog zu).
    try { history.back(); return; } catch (e) { /* alte Browser */ }
  }
  EBENEN.splice(i, 1);
  schliesser();
}

window.addEventListener('popstate', (ev) => {
  if (EBENEN.length) { const schliessen = EBENEN.pop(); try { schliessen(); } catch (e) {} return; }
  // Ein Eintrag, den eine längst geschlossene Ebene hinterlassen hat. Er trägt die Adresse
  // von damals — wer seither den Binder gewechselt hat, würde hier zurück in den alten
  // geworfen, samt Neuladen. Der Eintrag wird eingesammelt, die Adresse geradegezogen,
  // sonst passiert nichts.
  if (ev.state && ev.state.bpEbene) { routeZurueck(); return; }
  // Nichts offen: dann hat sich die Route geändert – etwa Zurück aus der Nur-Ansicht in die
  // Vitrine oder zu einem anderen Binder. Die Seite baut sich dafür sauber neu auf; vorher
  // blieb die alte Ansicht einfach stehen und wirkte, als käme man nie wieder heraus.
  let h = routeLesen();
  if (h === 'vitrine/kunst') { VT.bereich = 'kunst'; h = 'vitrine'; }
  const istAnsicht = h.startsWith('ansicht/');
  const andererBinder = (istAnsicht || h.startsWith('binder/')) && S.binder && h.split('/')[1] !== S.binder.id;
  if (istAnsicht !== !!S.nurAnsicht || andererBinder) { location.reload(); return; }
  if (ROUTEN.includes(h)) ansicht(h);
  else if (!h || h.startsWith('binder/')) ansicht('suche');
});

// ---------- Vitrine-Teil des Profils ----------
// Bewusst getrennt vom Rufnamen: der eine steht in der Begrüßung, der andere
// unter jedem veröffentlichten Binder. Beide „Anzeigename“ zu nennen war der
// sicherste Weg, versehentlich den Klarnamen öffentlich zu machen.
async function vitrineProfilLaden() {
  if (!S.user) return;
  $('pf-vfehler').textContent = '';
  let d = null;
  try { d = await api('api/profil'); } catch (e) { return; }
  const name = (d.profil && d.profil.name) || '';
  $('pf-vname').value = name;
  if ($('pf-vtausch')) $('pf-vtausch').checked = !!(d.profil && d.profil.tauschliste);
  $('pf-vtext').value = (d.profil && d.profil.kurztext) || '';
  const jahre = d.alter;
  $('pf-alter').innerHTML = jahre == null
    ? `<span style="color:var(--mut)">${t('pf_alter_offen')}</span>`
    : (jahre >= d.mindestalter
        ? `<span style="color:var(--gruen-text);font-weight:700">${t('pf_alter_ok')}</span>`
        : `<span style="color:var(--akzent-text);font-weight:700">${t('pf_alter_zu_jung').replace('{a}', d.mindestalter)}</span>`);
  const knopf = $('pf-meinprofil');
  knopf.classList.toggle('hidden', !name);
  if (name) knopf.onclick = () => { profilSchliessen(); profilseiteOeffnen(name); };
  try {
    const v = await api('api/vitrine?limit=48');
    const meine = (v.binder || []).filter((b) => b.besitzer === name).length;
    $('pf-oeffzahl').textContent = name ? String(meine) : '–';
  } catch (e) { $('pf-oeffzahl').textContent = '–'; }
}

async function vitrineProfilSpeichern() {
  $('pf-vfehler').textContent = '';
  try {
    await api('api/profil', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: $('pf-vname').value, kurztext: $('pf-vtext').value,
                             tauschliste: $('pf-vtausch') && $('pf-vtausch').checked }),
    });
    toast(t('gespeichert'));
    vitrineProfilLaden();
  } catch (e) { $('pf-vfehler').textContent = e.message; }
}

// ---------- E-Mail-Bestätigung ----------
// Das Startguthaben gibt es erst nach dem Klick im Postfach. Ohne eingerichteten
// Mailversand gilt jedes Konto als bestätigt — sonst käme niemand mehr an Credits.
function bestaetigungAnzeigen() {
  const b = $('bestaetigen-banner');
  const offen = S.user && S.user.mail_moeglich && !S.user.email_bestaetigt;
  b.classList.toggle('hidden', !offen);
  if (offen) $('best-text').textContent = t('best_offen').replace('{m}', S.user.email);
}

async function bestaetigungNeu() {
  try {
    const d = await api('api/auth/bestaetigung_neu', { method: 'POST' });
    toast(d.schon ? t('best_schon') : t('best_gesendet'));
    if (d.schon) { S.user.email_bestaetigt = true; bestaetigungAnzeigen(); }
  } catch (e) { toast((e.detail && e.detail.text) || e.message); }
}

/** Link aus der Mail: ?bestaetigen=… */
async function bestaetigungEinloesen(token) {
  try {
    const d = await api('api/auth/bestaetigen', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    });
    setToken(d.token);
    S.user = d.user;
    kontoAnzeigen();
    bestaetigungAnzeigen();
    toast(d.credits_neu ? t('best_danke_credits').replace('{n}', S.user.credits || 0) : t('best_danke'));
  } catch (e) {
    toast((e.detail && e.detail.text) || e.message);
  }
}

