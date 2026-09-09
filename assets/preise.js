// Binderplan – Sammlungs-Helfer im Binder, Preise.
// Aus index.html herausgelöst (Phase 5, 10.09.2026); die Dateien laden per defer in dieser Reihenfolge:
// kern → konto → werkbank → vitrine → preise → planer → detail → artwork → markt → sammlung.
// ---------- Sammlung ----------
// Bestand, Sets, Wunschliste, Ziele, Posten und Auswertung liegen in assets/sammlung.js
// (per defer nach diesem Skript geladen). Hier bleibt nur, was der Detail-Dialog braucht.

function detailOeffnenId(id) {
  const i = (S.ergebnisse || []).findIndex((k) => k.id === id);
  if (i >= 0) return detailOeffnen(i);
  detailOeffnen(id);
}

// Drei Beispiele direkt sichtbar: die Bildmotiv-Suche war hinter einem zugeklappten
// Knopf zwischen den Standardfiltern versteckt — und niemand sucht dort etwas, das
// er nicht erwartet.
const MOTIV_SCHNELL = [
  { art: 'wasser', wert: 3, lbl: 'th_unterwasser' },
  { art: 'zeit', wert: 'nacht', lbl: 'th_nachts' },
  { art: 'merkmal', wert: 'mehrere_pokemon', lbl: 'th_mehrere' },
];

function zeichneMotivSchnell() {
  const an = (m) => m.art === 'wasser' ? filter.artWasser === m.wert
    : m.art === 'zeit' ? filter.artZeit === m.wert : filter.artMerkmale.has(m.wert);
  $('f-motiv-schnell').innerHTML = MOTIV_SCHNELL.map((m, i) =>
    `<button class="chip ${an(m) ? 'on' : ''}" onclick="motivSchnell(${i})">${t(m.lbl)}</button>`).join('');
}

function motivSchnell(i) {
  const m = MOTIV_SCHNELL[i];
  if (m.art === 'wasser') filter.artWasser = filter.artWasser === m.wert ? 0 : m.wert;
  else if (m.art === 'zeit') filter.artZeit = filter.artZeit === m.wert ? '' : m.wert;
  else filter.artMerkmale.has(m.wert) ? filter.artMerkmale.delete(m.wert) : filter.artMerkmale.add(m.wert);
  zeichneMotivSchnell();
  if (artIndex) zeichneArtChips();
  artFilterZahl();
  sucheNeu();
}

/**
 * Die aufgeschlagene Doppelseite als PNG sichern — der natürliche Weg, wie ein
 * Binder herumgezeigt wird. Alles läuft im Browser: die Kartenbilder kommen vom
 * eigenen Server, also darf die Zeichenfläche exportiert werden.
 */
async function blaetternBild() {
  if (!S.binder) return;
  const [cols, rows] = LAYOUTS[S.binder.layout] || [3, 3];
  const KB = 300, KH = 419, LU = 14, RAND = 26, BUND = 34;
  const seitenB = RAND * 2 + cols * KB + (cols - 1) * LU;
  const seitenH = RAND * 2 + rows * KH + (rows - 1) * LU;
  const c = document.createElement('canvas');
  c.width = seitenB * 2 + BUND;
  c.height = seitenH + 74;
  const g = c.getContext('2d');
  g.fillStyle = '#14161a'; g.fillRect(0, 0, c.width, c.height);

  const laden = (url) => new Promise((fertig) => {
    const im = new Image();
    im.onload = () => fertig(im);
    im.onerror = () => fertig(null);
    im.src = url;
  });

  const malen = async (seite, x0) => {
    if (seite < 0 || seite >= bvSeitenZahl()) return;
    g.fillStyle = '#0e1013';
    g.fillRect(x0, 0, seitenB, seitenH);
    const sp = seiteInfo(seite), pp = sp.laenge;
    const teil = S.binder.items.slice(sp.start, sp.start + pp);
    const arts = teil.filter((i) => i && i.type === 'art');
    const ganz = arts.length >= 2 && arts.every((i) => i.artwork === arts[0].artwork);
    if (ganz) {
      const im = await laden(`api/artwork/${encodeURIComponent(arts[0].artwork)}/bild?v=vorschau`);
      if (im) g.drawImage(im, x0 + RAND, RAND, seitenB - RAND * 2, seitenH - RAND * 2);
    }
    for (let i = 0; i < pp; i++) {
      const it = teil[i];
      const x = x0 + RAND + (i % cols) * (KB + LU);
      const y = RAND + Math.floor(i / cols) * (KH + LU);
      if (!ganz) { g.fillStyle = '#1c1f25'; g.fillRect(x, y, KB, KH); }
      else {
        // Fugen freistellen: rings um das Fach die halbe Lücke in Seitenfarbe übermalen,
        // damit die Artwork-Seite aussieht wie im Album und nicht wie ein Poster.
        const h = LU / 2;
        g.fillStyle = '#0e1013';
        g.fillRect(x - h, y - h, KB + LU, h);            // oben
        g.fillRect(x - h, y + KH, KB + LU, h);           // unten
        g.fillRect(x - h, y - h, h, KH + LU);            // links
        g.fillRect(x + KB, y - h, h, KH + LU);           // rechts
      }
      if (it && it.type === 'card') {
        const im = await laden(imgUrl(it.id));
        if (im) g.drawImage(im, x, y, KB, KH);
      }
      g.strokeStyle = 'rgba(255,255,255,.15)'; g.lineWidth = 1.5;
      g.strokeRect(x + .75, y + .75, KB - 1.5, KH - 1.5);
    }
  };

  await malen(bvLinks(BV.spread), 0);
  await malen(bvRechts(BV.spread), seitenB + BUND);
  // Ringe im Bund
  g.fillStyle = '#c8ccd4';
  for (let i = 0; i < 3; i++) {
    g.beginPath();
    g.arc(seitenB + BUND / 2, seitenH * (i + 1) / 4, 9, 0, Math.PI * 2);
    g.fill();
  }
  // Fußzeile
  g.fillStyle = '#ffffff';
  g.font = '700 26px Archivo, sans-serif';
  g.fillText(S.binder.name || 'Binderplan', 26, seitenH + 46);
  g.fillStyle = '#F5C518';
  g.font = '700 22px Archivo, sans-serif';
  const marke = 'binderplan.app';
  g.fillText(marke, c.width - g.measureText(marke).width - 26, seitenH + 46);

  c.toBlob((blob) => {
    if (!blob) return toast(t('bv_bild_fehler'));
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${(S.binder.name || 'binder').replace(/[^\wäöüß -]/gi, '')}-seite-${BV.spread * 2 + 1}.png`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    toast(t('bv_bild_fertig'));
  }, 'image/png');
}


/** Genau einen Reiter markieren. Vorher setzte jede Ansicht ihre eigenen Klassen —
 *  schloss man den Planer, während die Startseite offen war, leuchteten zwei. */
function reiterSetzen(id) {
  for (const r of ['seg-start', 'seg-suche', 'seg-planer', 'seg-sammlung', 'seg-vitrine', 'seg-markt']) {
    const el = $(r);
    if (el) el.classList.toggle('on', r === id);
  }
}

/** Die Kopfzeile markierte weiter „Suche“, während Profil, Vitrine, Markt oder Auswertung
 *  offen waren. Sie fragt jetzt nach, was wirklich oben liegt. */
function reiterNachAnsicht() {
  const offen = (id) => { const el = $(id); return el && !el.classList.contains('hidden'); };
  if (offen('startseite')) return reiterSetzen('seg-start');
  if (offen('profilseite')) return reiterSetzen('');
  if (offen('marktseite')) return reiterSetzen('seg-markt');
  if (offen('vitrine')) return reiterSetzen('seg-vitrine');
  if (offen('sammlung')) return reiterSetzen('seg-sammlung');
  reiterSetzen('seg-suche');   // der Planer gehört zu diesem Reiter, er ist kein eigener
}

function seiteWechsel(d) {
  const seiten = seitenAnzahl();
  S.seite = Math.min(Math.max(0, S.seite + d), seiten - 1);
  zeichneBinder();
}
function slotEntfernen(idx) { fachFreimachen(idx); }

/** Fach wirklich herausnehmen — alles dahinter rückt auf. Nur noch dort, wo jemand es
 *  ausdrücklich verlangt: im Fach-Menü und über die Auswahlleiste. */
function slotHerausnehmen(idx) {
  if (!S.binder.items[idx]) return;
  merken('raus');
  S.binder.items.splice(idx, 1);
  speichern(); zeichneBinder(); zeichneErgebnisse();
  toastUndo(t('herausnehmen'));
}
/** Zählt dieser Binder zur Kaufliste? Nicht jeder soll das: man legt einen an, um etwas
 *  auszuprobieren, oder baut Kunstseiten für andere. */
function wantsUmschalten(an) {
  if (!S.binder) return;
  S.binder.options = S.binder.options || {};
  S.binder.options.wants = !!an;
  speichern();
  zeichneBinder();
  toast(an ? t('wants_an_toast') : t('wants_aus_toast'));
}

function wantsSchalterSetzen() {
  // Umgedreht seit dem 04.09.2026: ein Binder zählt nur zur Wunschliste, wenn man es
  // sagt. Vorher zählte jeder mit, dem niemand widersprochen hatte — und über einem
  // Binder, den man zum Ausprobieren angelegt hat, stand „dir fehlen 340 Karten".
  const el = $('wb-wants');
  if (el && S.binder) el.checked = !!(S.binder.options && S.binder.options.wants === true);
}

function binderLeeren() {
  if (!S.binder.items.length) return;
  merken('leeren');
  S.binder.items = []; S.seite = 0; speichern(); zeichneBinder(); zeichneErgebnisse();
  toastUndo(t('leeren'));
}

/* ---------- Umsortieren mit dem Finger ----------
 * HTML-Drag&Drop gibt es auf Touch-Geräten nicht. Bisher ging Umsortieren am Handy nur über
 * den Umweg „Planer → Auswahl → verschieben“. Ein langer Druck hebt das Fach jetzt an, der
 * Finger zieht es, Loslassen legt es ab — dieselbe Ablage-Logik wie beim Ziehen mit der Maus.
 */
const TOUCH = { idx: null, geist: null, timer: null, aktiv: false, ziel: null, start: null };

function touchStart(ev) {
  if (S.nurAnsicht || ev.touches.length !== 1) return;
  const fach = ev.currentTarget;
  const idx = Number(fach.dataset.idx);
  if (Number.isNaN(idx) || !S.binder.items[idx]) return;
  const b = ev.touches[0];
  TOUCH.start = { x: b.clientX, y: b.clientY };
  TOUCH.timer = setTimeout(() => {
    TOUCH.aktiv = true;
    TOUCH.idx = idx;
    if (navigator.vibrate) navigator.vibrate(12);      // spürbare Rückmeldung, dass es „hängt“
    const r = fach.getBoundingClientRect();
    const geist = fach.cloneNode(true);
    geist.className = 'fach-geist';
    geist.style.cssText = `position:fixed;left:${r.left}px;top:${r.top}px;width:${r.width}px;`
      + `height:${r.height}px;z-index:200;pointer-events:none;opacity:.85;transform:scale(1.06);`
      + 'box-shadow:0 12px 30px rgba(0,0,0,.4);border-radius:6px;overflow:hidden';
    document.body.appendChild(geist);
    TOUCH.geist = geist;
    fach.classList.add('wird-gezogen');
  }, 420);
}

function touchMove(ev) {
  if (!TOUCH.aktiv) {
    // Vor dem langen Druck: wer scrollt, soll scrollen dürfen
    if (TOUCH.timer && TOUCH.start) {
      const b = ev.touches[0];
      if (Math.abs(b.clientX - TOUCH.start.x) > 8 || Math.abs(b.clientY - TOUCH.start.y) > 8) {
        clearTimeout(TOUCH.timer); TOUCH.timer = null;
      }
    }
    return;
  }
  ev.preventDefault();
  const b = ev.touches[0];
  if (TOUCH.geist) {
    TOUCH.geist.style.left = (b.clientX - TOUCH.geist.offsetWidth / 2) + 'px';
    TOUCH.geist.style.top = (b.clientY - TOUCH.geist.offsetHeight / 2) + 'px';
  }
  document.querySelectorAll('.slot.dragover').forEach((el) => el.classList.remove('dragover'));
  const unter = document.elementFromPoint(b.clientX, b.clientY);
  const fach = unter && unter.closest('.slot[data-idx]');
  TOUCH.ziel = fach ? Number(fach.dataset.idx) : null;
  if (fach) fach.classList.add('dragover');
}

function touchEnde() {
  clearTimeout(TOUCH.timer); TOUCH.timer = null;
  if (!TOUCH.aktiv) { TOUCH.start = null; return; }
  if (TOUCH.geist) { TOUCH.geist.remove(); TOUCH.geist = null; }
  document.querySelectorAll('.slot.dragover, .slot.wird-gezogen')
    .forEach((el) => el.classList.remove('dragover', 'wird-gezogen'));
  const von = TOUCH.idx, nach = TOUCH.ziel;
  TOUCH.aktiv = false; TOUCH.idx = null; TOUCH.ziel = null; TOUCH.start = null;
  if (von === null || nach === null || von === nach) return;
  fachVerschieben(von, nach);
}

/** Ein Fach von A nach B — dieselbe Regel wie beim Ziehen mit der Maus. */
function fachVerschieben(von, nach) {
  merken('drag');
  const zielItem = S.binder.items[nach];
  if (!zielItem || zielItem.type === 'empty') {
    while (S.binder.items.length <= nach) S.binder.items.push({ type: 'empty' });
    S.binder.items[nach] = S.binder.items[von];
    S.binder.items[von] = { type: 'empty' };
  } else {
    const [item] = S.binder.items.splice(von, 1);
    S.binder.items.splice(nach, 0, item);
  }
  S.auswahl.clear();
  speichern(); zeichneBinder();
  toastUndo(t('verschoben'));
}

let dragIdx = null;
let dragNeu = null;      // Karte aus der Trefferliste (statt Umsortieren im Binder)

function dragStart(ev) { dragIdx = Number(ev.currentTarget.dataset.idx); dragNeu = null; ev.dataTransfer.effectAllowed = 'move'; }
function dragOver(ev) { ev.preventDefault(); ev.currentTarget.classList.add('dragover'); }
function dragLeave(ev) { ev.currentTarget.classList.remove('dragover'); }

/** Ziehen aus der Trefferliste: die Karte gibt es im Binder noch nicht, sie wird eingesetzt. */
function ergDragStart(ev, i) {
  const k = S.ergebnisse[i];
  if (!k || !S.binder || S.nurAnsicht) return;
  dragNeu = k.id; dragIdx = null;
  ev.dataTransfer.effectAllowed = 'copy';
  try { ev.dataTransfer.setData('text/plain', k.id); } catch (e) { /* ältere Browser */ }
  document.body.classList.add('zieht-karte');
}
function ergDragEnde() { dragNeu = null; document.body.classList.remove('zieht-karte'); }

/** Karte aus der Trefferliste in ein bestimmtes Fach setzen. */
function neueKarteAblegen(ziel) {
  const id = dragNeu; dragNeu = null;
  document.body.classList.remove('zieht-karte');
  if (!id || !S.binder || S.nurAnsicht) return;
  merken('drag');
  const item = { type: 'card', id };
  const zielItem = Number.isNaN(ziel) ? null : S.binder.items[ziel];
  if (Number.isNaN(ziel) || ziel == null) {
    S.binder.items.push(item);                       // auf die Fläche gezogen = ans Ende
  } else if (!zielItem || zielItem.type === 'empty') {
    while (S.binder.items.length <= ziel) S.binder.items.push({ type: 'empty' });
    S.binder.items[ziel] = item;                     // freies Fach: dort einsetzen
  } else {
    S.binder.items.splice(ziel, 0, item);            // belegtes Fach: davor einschieben
  }
  speichern(); zeichneBinder(); zeichneErgebnisse();
  toastUndo(t('in_binder'));
}

/** Ablegen auf der Seitenfläche neben den Fächern — hängt hinten an. */
function flaecheDrop(ev) {
  if (!dragNeu) return;
  ev.preventDefault();
  $('wb-slots').classList.remove('dragover');
  neueKarteAblegen(NaN);
}
function flaecheOver(ev) { if (dragNeu) { ev.preventDefault(); $('wb-slots').classList.add('dragover'); } }
function flaecheLeave() { $('wb-slots').classList.remove('dragover'); }

function dragDrop(ev) {
  ev.preventDefault(); ev.currentTarget.classList.remove('dragover');
  const ziel = Number(ev.currentTarget.dataset.idx);
  if (dragNeu) return neueKarteAblegen(ziel);
  if (dragIdx === null || ziel === dragIdx || Number.isNaN(ziel)) return;
  merken('drag');
  const zielItem = S.binder.items[ziel];
  if (!zielItem || zielItem.type === 'empty') {
    // freier Platz oder leeres Fach: Karte dorthin, am Ursprung bleibt eine Lücke (die Seiten verrutschen nicht)
    while (S.binder.items.length <= ziel) S.binder.items.push({ type: 'empty' });
    S.binder.items[ziel] = S.binder.items[dragIdx];
    S.binder.items[dragIdx] = { type: 'empty' };
  } else {
    const [item] = S.binder.items.splice(dragIdx, 1);
    S.binder.items.splice(ziel, 0, item);
  }
  dragIdx = null; S.auswahl.clear();
  speichern(); zeichneBinder();
}

// ---------- Preise ----------
async function preiseUmschalten() {
  S.preiseAn = $('preis-toggle').checked;
  if (!S.preiseAn && $('wb-verlauf')) $('wb-verlauf').classList.add('hidden');
  $('wb-preise').classList.toggle('hidden', !S.preiseAn);
  if (S.preiseAn) await preiseLaden();
  zeichneBinder();
  zeichneErgebnisse();     // Abzeichen auf den Treffern an- oder ausblenden
}
async function preiseLaden() {
  const ids = [...new Set(S.binder.items.filter((i) => i.type === 'card').map((i) => i.id))];
  if (!ids.length) { $('wb-preise').textContent = t('gesamt') + ': 0,00 €'; return; }
  $('wb-preise').textContent = '…';
  try {
    const d = await api('api/preise', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids }) });
    Object.assign(S.preise, d.preise); Object.assign(S.preiseHolo, d.holo || {});
    if (d.gedrosselt) toast(t('preise_morgen'));
    else if (d.offen > 0) toast(t('preise_offen'));
    wertverlaufLaden();
    zeichneBinder();
  } catch (e) {
    if (gate(e)) { S.preiseAn = false; $('preis-toggle').checked = false; $('wb-preise').classList.add('hidden'); return; }
    toast(t('fehler_preise'));
  }
  zeichnePreisSumme();
}
// Holo-Varianten bekommen den Holo-Trend, wenn Cardmarket einen kennt
function preisFuer(item) {
  if (item.variant === 'holo' || item.variant === 'reverse') { const h = S.preiseHolo[item.id]; if (h != null) return h; }
  return S.preise[item.id];
}
/** Wertverlauf des Binders: die Tabelle price_history sammelt täglich, sichtbar war davon
 *  bisher nichts. Zeigt die Linie der letzten 30 Tage und die Veränderung zur Vorwoche. */
async function wertverlaufLaden() {
  const box = $('wb-verlauf');
  if (!box || !S.binder || !S.binder.id || !S.preiseAn) { if (box) box.classList.add('hidden'); return; }
  try {
    const d = await api('api/binders/' + S.binder.id + '/wert?tage=30');
    if (!d.punkte || d.punkte.length < 2) { box.classList.add('hidden'); return; }
    const ys = d.punkte.map((p) => p.eur);
    const min = Math.min(...ys), max = Math.max(...ys);
    const pts = ys.map((y, i) => `${(i / (ys.length - 1) * 100).toFixed(1)},${(30 - (max === min ? 15 : (y - min) / (max - min) * 24 + 3)).toFixed(1)}`).join(' ');
    const delta = d.veraenderung;
    const zeichen = delta > 0 ? '+' : '';
    const farbe = delta > 0 ? 'var(--gruen)' : delta < 0 ? 'var(--akzent)' : 'var(--mut)';
    box.innerHTML = `<svg viewBox="0 0 100 32" preserveAspectRatio="none" class="wv-spark">
        <polyline points="${pts}" fill="none" stroke="${farbe}" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>
      <span class="wv-text">${delta == null ? '' : `<b style="color:${farbe}">${zeichen}${delta.toFixed(2).replace('.', LANG === 'de' ? ',' : '.')} €</b> ${t('wv_woche')}`}</span>`;
    box.classList.remove('hidden');
  } catch (e) { box.classList.add('hidden'); }
}

function zeichnePreisSumme() {
  if (!S.preiseAn) return;
  let summe = 0; let fehlen = 0;
  for (const i of S.binder.items) {
    if (i.type !== 'card') continue;
    const p = preisFuer(i);
    if (p == null) fehlen++; else summe += p;
  }
  const fmt = summe.toFixed(2).replace('.', LANG === 'de' ? ',' : '.');
  $('wb-preise').textContent = `${t('gesamt')}: ${fmt} €` + (fehlen ? ` (+${fehlen}?)` : '');
  $('wb-preise').title = fehlen ? t('s_ohne_preis').replace('{n}', fehlen) : '';
}

