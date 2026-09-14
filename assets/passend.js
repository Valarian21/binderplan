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
      <button class="pa-zu" onclick="passendAus()" title="${t('pa_aus')}" aria-label="${t('pa_aus')}">✕</button>
    </div>
    <div class="pa-zeile">
      <div class="segment pa-modus" role="tablist">${seg}</div>
      ${frei ? `<button class="btn sekundaer pa-fuellen" onclick="passendFuellen()">${t('pa_fuellen').replace('{n}', frei)}</button>` : ''}
    </div>`;
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

/** Alle freien Fächer der Seite mit den besten Treffern belegen — ein Schritt, ein Rückgängig.
 *  Was nicht gefällt, wird danach einzeln aus der Liste ersetzt; dafür bleibt sie offen. */
async function passendFuellen() {
  const frei = passendFreieFaecher();
  if (!frei.length || !S.binder) return;
  const drin = new Set(S.binder.items.filter((i) => i.type === 'card').map((i) => i.id));
  const treffer = S.ergebnisse.filter((k) => !drin.has(k.id)).slice(0, frei.length);
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
