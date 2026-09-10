// Binderplan – Hilfe-Center: alle Anleitungen an einem Ort, Strg + / öffnet sie.
//
// Vorher gab es eine Kurzhilfe zum Sortieren (ein Modal, fünf Zeilen) und die FAQ auf
// der Landingpage. Alles dazwischen — Fotoerkennung, Platzhalter, Kunstseiten, Alarme —
// war nirgends erklärt; wer es nicht zufällig fand, fand es nicht.
//
// Die Texte stehen hier als Daten, nicht als HTML-Seiten: sie sind übersetzbar,
// durchsuchbar, und dieselbe Liste taugt als Vorlage für Anleitungs-Beiträge auf
// Instagram (das Format mit der höchsten Speicher-Quote).

const HILFE = [
  { id: 'start', tags: 'binder anlegen erste schritte vorlage master set pokedex',
    schritte: 4 },
  { id: 'suchen', tags: 'karte suchen filter nummer set seltenheit illustrator' },
  { id: 'ordnen', tags: 'sortieren ziehen tauschen schieben auswahl tastatur rückgängig' },
  { id: 'foto', tags: 'foto kamera erkennen seite abfotografieren import' },
  { id: 'drucken', tags: 'platzhalter drucken pdf checkliste kaufliste a4' },
  { id: 'sammlung', tags: 'sammlung hab ich zustand kaufpreis wunschliste ziele' },
  { id: 'preise', tags: 'preis cardmarket euro trend verlauf alarm' },
  { id: 'kunst', tags: 'kunstseite artwork ki malen vitrine credits michi' },
  { id: 'teilen', tags: 'teilen link qr freigeben vitrine zeigen' },
  { id: 'konto', tags: 'konto abo plus pro credits kündigen sprache' },
];

/** Dialog öffnen. */
function hilfeOeffnen(id) {
  modalOeffnen('modal-hilfe');
  const s = $('hz-suche');
  if (s) s.value = '';
  if (id) hilfeArtikel(id); else hilfeZeichnen('');
}

/** Trefferliste — Titel und Kurztext kommen aus der Übersetzung (hz_<id>_t / _u). */
function hilfeZeichnen(suche) {
  $('hz-artikel').classList.add('hidden');
  const liste = $('hz-liste');
  liste.classList.remove('hidden');
  const q = (suche || '').trim().toLowerCase();
  const treffer = HILFE.filter((h) => !q
    || (h.tags + ' ' + t('hz_' + h.id + '_t') + ' ' + t('hz_' + h.id + '_u')).toLowerCase().includes(q));
  liste.innerHTML = treffer.length
    ? treffer.map((h) => `<button class="hz-eintrag" onclick="hilfeArtikel('${h.id}')">
        <strong>${esc(t('hz_' + h.id + '_t'))}</strong>
        <span>${esc(t('hz_' + h.id + '_u'))}</span></button>`).join('')
    : `<p class="mut">${esc(t('hz_nichts'))}</p>`;
}

/** Ein Artikel: Titel, Einleitung, nummerierte Schritte (hz_<id>_1 … _n). */
function hilfeArtikel(id) {
  const h = HILFE.find((x) => x.id === id);
  if (!h) return hilfeZeichnen('');
  $('hz-liste').classList.add('hidden');
  const box = $('hz-artikel');
  box.classList.remove('hidden');
  const schritte = [];
  for (let i = 1; i <= 8; i++) {
    const s = t('hz_' + id + '_' + i);
    // t() gibt den Schlüssel zurück, wenn es ihn nicht gibt — daran endet die Kette.
    if (!s || s === 'hz_' + id + '_' + i) break;
    schritte.push(s);
  }
  box.innerHTML = `<button class="btn sekundaer" onclick="hilfeZeichnen('')">‹ ${esc(t('hz_zurueck'))}</button>
    <h3>${esc(t('hz_' + id + '_t'))}</h3>
    <p>${esc(t('hz_' + id + '_u'))}</p>
    <ol class="hz-schritte">${schritte.map((s) => `<li>${esc(s)}</li>`).join('')}</ol>`;
}

// Strg + / von überall — dasselbe Kürzel wie in vielen Editoren.
document.addEventListener('keydown', (ev) => {
  if ((ev.ctrlKey || ev.metaKey) && (ev.key === '/' || ev.key === '?')) {
    ev.preventDefault();
    const offen = $('modal-hilfe') && !$('modal-hilfe').classList.contains('hidden');
    if (offen) modalSchliessen(); else hilfeOeffnen();
  }
});
