/* Binderplan – „passende Karten": die Trefferliste links richtet sich nach dem, was schon
   auf der Seite liegt.

   Kein eigener Dialog und keine zweite Trefferliste: der Modus setzt nur zwei Parameter an
   die normale Kartensuche (`passend_zu`, `passend_modus`, siehe passend.py). Dadurch gelten
   alle Filter weiter, und Ziehen ins Fach, Preise, „hab ich", Wunschliste, Ablage und
   „+ Alle Treffer" funktionieren unverändert mit — das war der Grund, es so zu bauen und
   nicht als dritten Vorschlags-Dialog neben „Seite nach Thema" und der Artwork-Seite.

   Die Rangfolge kommt aus zwei gemessenen Quellen:
   * Farbe — vier dominante Farben je Karte aus dem Bild (`card_palette`),
   * Motiv — Orte, Merkmale, Tageszeit, Wasser aus dem Bildmotiv-Index (`card_art_tags`).
   Beides kostet nichts und braucht kein Modell, deshalb darf sich die Liste bei jeder
   Änderung an der Seite neu ordnen. */

// S.passend: { anker: [card_id], modus: 'farbe'|'beides'|'motiv', seite: nr|null }
// null heißt: normaler Suchmodus.

const PASSEND_MODI = ['farbe', 'beides', 'motiv'];

/* Schnellfilter über den Treffern. Sie setzen die ganz normalen Filter der Suche
   (`filter.rgroup`, `filter.illustrator`, `filter.set`, `filter.serie`) — kein zweiter
   Zustand, keine zweite Wahrheit. Wer einen davon wieder loswerden will, kann das auch
   über die Filter-Chips darunter, und die Filterspalte zeigt ihn ebenfalls an. */

// „Nur Full Arts": die Seltenheitsgruppen, deren Illustration die ganze Karte füllt.
//
// Marcel hatte den Filter am 14.09.2026 als „ohne Common, Uncommon, Rare, Rare Holo und
// Double Rare (ex)" beschrieben. So gebaut bewirkte er fast nichts: die Farbdaten gibt es
// ohnehin nur für die Seltenheiten oberhalb davon, und in den ersten zwölf Treffern blieben
// zwölf gleich. Der Filter meint deshalb jetzt, was sein Name sagt — Karten ohne
// Bildfenster. Das ist strenger als seine Liste und schließt alles ein, was er nennt:
// 5.062 von 10.014 gemessenen Karten, und 7 von 12 Spitzentreffern ändern sich.
const PASSEND_VOLLBILD = ['ultra', 'illustration', 'secret', 'shiny', 'special'];

/** Die Karten einer Seite als Ankerliste — leere Fächer und Kunstseiten zählen nicht mit. */
function passendAnkerDerSeite(nr) {
  if (!S.binder) return [];
  const sp = seiteInfo(nr);
  const aus = [];
  for (let i = 0; i < sp.laenge; i++) {
    const it = S.binder.items[sp.start + i];
    if (it && it.type === 'card' && !aus.includes(it.id)) aus.push(it.id);
  }
  return aus;
}

/** Aus der Seitenleiste, dem Fach-Menü oder dem Inspektor: Modus an. */
function passendOeffnen(nr, nurKarte) {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  if (!S.binder || S.nurAnsicht) return;
  const anker = nurKarte ? [nurKarte] : passendAnkerDerSeite(nr == null ? S.seite : nr);
  if (!anker.length) return toast(t('pa_leer'));
  S.passend = { anker, modus: (S.passend && S.passend.modus) || 'beides',
                seite: nurKarte ? null : (nr == null ? S.seite : nr) };
  passendLeiste();
  if (typeof sucheLadeOeffnen === 'function') sucheLadeOeffnen(false);
  sucheNeu(true);
}

function passendAus() {
  // Die Schnellfilter gehören zu diesem Modus: bleiben sie stehen, sucht die normale Suche
  // danach still weiter, ohne dass noch etwas davon erzählt.
  if (S.passend) {
    passendFullartGruppen().forEach((g) => filter.rgroup.delete(g));
    if (S.passend.kuenstler && filter.illustrator === S.passend.kuenstler) filter.illustrator = '';
    if (S.passend.typ && filter.typ === S.passend.typ) filter.typ = '';
    if (S.passend.set && filter.set === S.passend.set) filter.set = '';
    if (S.passend.aera && filter.serie === S.passend.aera) { filter.serie = ''; if (typeof baueSetSelect === 'function') baueSetSelect(); }
    if (typeof baueFilterLeiste === 'function') baueFilterLeiste();
    if (typeof mehrFilterZahl === 'function') mehrFilterZahl();
  }
  S.passend = null;
  passendLeiste();
  sucheNeu();
}

function passendModus(m) {
  if (!S.passend || !PASSEND_MODI.includes(m)) return;
  S.passend.modus = m;
  passendLeiste();
  sucheNeu(true);
}

/** Nach jeder Änderung am Binder: hat sich die Seite geändert, ordnet sich die Liste neu.
 *  Verglichen wird die Ankerliste selbst — sonst liefe bei jedem Neuzeichnen eine Suche. */
let _passendStand = '';
function passendPruefen() {
  if (!S.passend) { _passendStand = ''; return; }
  if (S.passend.seite == null) { passendLeiste(); return; }
  const neu = passendAnkerDerSeite(S.passend.seite);
  const kennung = neu.join(',');
  if (kennung === _passendStand) { passendLeiste(); return; }
  _passendStand = kennung;
  S.passend.anker = neu;
  passendLeiste();
  if (neu.length) sucheNeu(true); else passendAus();
}

/** Die Leiste über den Treffern: woran gemessen wird, wonach, und was man damit tun kann. */
function passendLeiste() {
  const box = $('passend-leiste');
  if (!box) return;
  box.classList.toggle('hidden', !S.passend);
  // Die Trefferliste wird in diesem Modus enger gesetzt (siehe app.css): hier sucht man mit
  // dem Auge unter vielen Karten, statt eine bestimmte zu finden. Am Handy waren von 60
  // Treffern vier zu sehen, weil über der Liste 424 px Kopf standen (gemessen 15.09.2026).
  document.body.classList.toggle('passend-an', !!S.passend);
  if (!S.passend) return;
  const a = S.passend.anker;
  const bilder = a.slice(0, 5).map((id) =>
    `<img src="${imgUrl(id)}" alt="" loading="lazy" title="${esc(id)}">`).join('');
  const woher = S.passend.seite == null ? t('pa_zu_karte')
    : t('pa_zu_seite').replace('{n}', S.passend.seite + 1).replace('{k}', a.length);
  const seg = PASSEND_MODI.map((m) =>
    `<button class="${S.passend.modus === m ? 'on' : ''}" onclick="passendModus('${m}')"
             title="${t('pa_' + m + '_t')}">${t('pa_' + m)}</button>`).join('');
  const frei = S.passend.seite == null ? 0 : passendFreieFaecher().length;
  box.innerHTML = `
    <div class="pa-kopf">
      <div class="pa-bilder">${bilder}</div>
      <div class="pa-text"><strong>${t('pa_titel')}</strong><span>${woher}</span></div>
      ${frei ? `<button class="btn sekundaer pa-fuellen" onclick="passendFuellen()">${t('pa_fuellen_kurz').replace('{n}', frei)}</button>` : ''}
      <button class="pa-zu" onclick="passendAus()" title="${t('pa_aus')}" aria-label="${t('pa_aus')}">✕</button>
    </div>
    <div class="pa-zeile">
      <div class="segment pa-modus" role="tablist">${seg}</div>
      <div class="pa-chips">${passendChips()}</div>
    </div>`;
  passendInfoLaden();
}

/** Die Gruppen, die „Nur Full Arts" anschaltet — nur die, die es im Katalog auch gibt. */
function passendFullartGruppen() {
  const da = ((S.meta && S.meta.rarity_groups) || []).map((g) => g.id);
  return PASSEND_VOLLBILD.filter((id) => da.includes(id));
}

/** Künstler, Set und Ära der Ankerkarten — einmal geholt, solange die Anker gleich bleiben. */
async function passendInfoLaden() {
  if (!S.passend) return;
  const kennung = S.passend.anker.join(',');
  if (S.passend.infoFuer === kennung) return;
  S.passend.infoFuer = kennung;
  try {
    const d = await api('api/cards/nach_ids', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                body: JSON.stringify({ ids: S.passend.anker }) });
    const karten = Object.values(d.karten || {});
    const haeufigste = (feld) => {
      const z = {};
      karten.forEach((k) => { if (k[feld]) z[k[feld]] = (z[k[feld]] || 0) + 1; });
      const beste = Object.keys(z).sort((x, y) => z[y] - z[x])[0];
      return beste || '';
    };
    S.passend.kuenstler = haeufigste('illustrator');
    S.passend.set = haeufigste('set_id');
    // Farbe und Energietyp hängen eng zusammen (Feuer ist rot, Wasser blau) — und
    // Typ-Seiten sind eine der häufigsten Binder-Ordnungen überhaupt.
    const typen = {};
    karten.forEach((k) => (k.types || []).forEach((x) => { typen[x] = (typen[x] || 0) + 1; }));
    S.passend.typ = Object.keys(typen).sort((x, y) => typen[y] - typen[x])[0] || '';
    const st = (S.meta.sets || []).find((x) => x.id === S.passend.set);
    S.passend.aera = st ? (st.aera || '') : '';
    S.passend.setName = st ? (LANG === 'en' ? (st.name_en || st.name) : (st.name || st.name_en)) : '';
    passendLeiste();
  } catch (e) { /* still: dann fehlen nur die drei Chips */ }
}

/** Einen Schnellfilter an- oder ausschalten. */
function passendSchnell(art) {
  if (!S.passend) return;
  if (art === 'fullart') {
    const gruppen = passendFullartGruppen();
    const an = gruppen.length && gruppen.every((g) => filter.rgroup.has(g));
    filter.rgroup.clear();
    if (!an) gruppen.forEach((g) => filter.rgroup.add(g));
  } else if (art === 'kuenstler') {
    filter.illustrator = filter.illustrator === S.passend.kuenstler ? '' : (S.passend.kuenstler || '');
    if ($('f-illu')) $('f-illu').value = filter.illustrator;
  } else if (art === 'set') {
    filter.set = filter.set === S.passend.set ? '' : (S.passend.set || '');
    if (typeof zeichneSetWahlKnopf === 'function') zeichneSetWahlKnopf();
  } else if (art === 'typ') {
    filter.typ = filter.typ === S.passend.typ ? '' : (S.passend.typ || '');
    document.querySelectorAll('[data-typ]').forEach((el) => el.classList.toggle('on', el.dataset.typ === filter.typ));
  } else if (art === 'aera') {
    filter.serie = filter.serie === S.passend.aera ? '' : (S.passend.aera || '');
    filter.set = '';
    if (typeof baueSetSelect === 'function') baueSetSelect();
  }
  if (typeof baueFilterLeiste === 'function') baueFilterLeiste();
  if (typeof mehrFilterZahl === 'function') mehrFilterZahl();
  passendLeiste();
  sucheNeu(true);
}

/** Die Chip-Zeile: was gerade an ist, steht als `on` da. */
function passendChips() {
  const gruppen = passendFullartGruppen();
  const chip = (art, an, text, titel) =>
    `<button class="chip klein ${an ? 'on' : ''}" onclick="passendSchnell('${art}')" title="${esc(titel || '')}">${esc(text)}</button>`;
  let html = chip('fullart', gruppen.length && gruppen.every((g) => filter.rgroup.has(g)),
                  t('pa_f_fullart'), t('pa_f_fullart_t'));
  if (S.passend.kuenstler) {
    html += chip('kuenstler', filter.illustrator === S.passend.kuenstler,
                 t('pa_f_kuenstler').replace('{n}', S.passend.kuenstler), t('pa_f_kuenstler_t'));
  }
  if (S.passend.typ) {
    html += chip('typ', filter.typ === S.passend.typ,
                 t('pa_f_typ').replace('{n}', (T[LANG].typen || {})[S.passend.typ] || S.passend.typ),
                 t('pa_f_typ_t'));
  }
  if (S.passend.set) {
    html += chip('set', filter.set === S.passend.set,
                 t('pa_f_set').replace('{n}', S.passend.setName || S.passend.set), t('pa_f_set_t'));
  }
  if (S.passend.aera && S.passend.aera !== S.passend.set) {
    html += chip('aera', filter.serie === S.passend.aera, t('pa_f_aera'), t('pa_f_aera_t'));
  }
  return html;
}

/** Die noch freien Fächer der Seite, in Leserichtung. */
function passendFreieFaecher() {
  if (!S.passend || S.passend.seite == null || !S.binder) return [];
  const sp = seiteInfo(S.passend.seite);
  const aus = [];
  for (let i = 0; i < sp.laenge; i++) {
    const it = S.binder.items[sp.start + i];
    if (!it || it.type === 'empty') aus.push(sp.start + i);
  }
  return aus;
}

/** Namensstamm wie im Backend: „Glurak-ex", „Glurak V" und „Glurak" sind dasselbe Motiv. */
function passendStamm(name) { return String(name || '').trim().split(/[ -]/)[0].toLowerCase(); }

/** Alle freien Fächer der Seite mit den besten Treffern belegen — ein Schritt, ein Rückgängig.
 *  Was nicht gefällt, wird danach einzeln aus der Liste ersetzt; dafür bleibt sie offen.
 *
 *  Je Pokémon nur eine Karte: die Rangliste darf zwei führen (man will die Wahl haben),
 *  aber automatisch nebeneinandergelegt sahen zwei Drucke desselben Motivs wie ein Versehen
 *  aus — auf drei von vier Probeseiten stand ein solches Paar (gemessen 14.09.2026). */
async function passendFuellen() {
  const frei = passendFreieFaecher();
  if (!frei.length || !S.binder) return;
  const drin = new Set(S.binder.items.filter((i) => i.type === 'card').map((i) => i.id));
  // Nur die neu gesetzten Karten werden gegeneinander geprüft: die Namen der Karten, die
  // schon im Fach liegen, stehen im Browser nicht — dort steht nur ihre Kennung. Ein zweiter
  // Druck derselben Ankerkarte ist ohnehin meist gewollt (eine Glurak-Seite).
  const staemme = new Set();
  const treffer = [];
  for (const k of S.ergebnisse) {
    if (treffer.length >= frei.length) break;
    const st = passendStamm(nm(k));
    if (drin.has(k.id) || (st && staemme.has(st))) continue;
    staemme.add(st);
    treffer.push(k);
  }
  if (!treffer.length) return toast(t('pa_nichts'));
  merken('passend');
  treffer.forEach((k, i) => { S.binder.items[frei[i]] = { type: 'card', id: k.id }; });
  if (!await binderAnlegenWennNoetig()) return;
  speichern(); zeichneBinder(); zeichneErgebnisse();
  toastUndo(t('pa_gefuellt').replace('{n}', treffer.length));
}

/** Das Abzeichen auf der Trefferkachel: wie gut die Karte passt und woran das liegt. */
function passendAbzeichen(k) {
  if (!S.passend || k.passung == null) return '';
  const stufe = k.passung >= 70 ? 'hoch' : k.passung >= 45 ? 'mittel' : 'tief';
  const grund = k.passung_grund ? ` · ${esc(k.passung_grund)}` : '';
  return `<span class="pa-wert ${stufe}" title="${t('pa_wert_t')}${grund}">${Math.round(k.passung)}</span>`;
}
