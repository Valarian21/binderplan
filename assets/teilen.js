// Binderplan – einen Binder per Link teilen: Schalter, Frist, QR-Code.
//
// Vorher kopierte „Binder-Link teilen" nur die Adresse in die Zwischenablage. Das
// verschwieg zwei Dinge: dass der Link für Fremde gar nicht funktionierte, solange der
// Binder privat war (siehe teilen.py), und dass man ihn wieder abschalten kann.
// Der Dialog macht beides sichtbar und fügt die Frist hinzu.

/** Dialog öffnen und den aktuellen Stand vom Server holen. */
async function teilenOeffnen() {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  if (!S.binder || !S.binder.id) return toast(t('binder_leer'));
  if (!S.user) return toast(t('gate_login'));
  modalOeffnen('modal-teilen');
  try {
    teilenZeigen(await api('api/binders/' + encodeURIComponent(S.binder.id) + '/teilen'));
  } catch (e) {
    toast(t('fehler_laden'));
  }
}

/** Schalter oder Frist geändert. */
async function teilenSetzen(an, frist) {
  if (!S.binder || !S.binder.id) return;
  const wahl = frist !== undefined ? frist : ($('tl-frist') ? $('tl-frist').value : '');
  try {
    teilenZeigen(await api('api/binders/' + encodeURIComponent(S.binder.id) + '/teilen', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ an: !!an, frist: wahl || null }),
    }));
    toast(t(an ? 'tl_ist_an' : 'tl_ist_aus'));
  } catch (e) {
    toast(t('fehler_server'));
  }
}

/** Den Stand in den Dialog schreiben. */
function teilenZeigen(d) {
  const an = $('tl-an');
  if (an) an.checked = !!d.geteilt;
  $('tl-details').classList.toggle('hidden', !d.geteilt);
  $('tl-url').value = d.url;
  const qr = 'api/binders/' + encodeURIComponent(S.binder.id) + '/qr.png';
  $('tl-qr-bild').src = qr;
  $('tl-qr-dl').href = qr + '?groesse=14';
  // Der Status sagt nur etwas, wenn es etwas zu sagen gibt: abgelaufen oder in der Vitrine.
  const st = $('tl-status');
  st.textContent = d.abgelaufen ? t('tl_abgelaufen')
    : d.bis ? t('tl_laeuft_bis').replace('{d}', new Date(d.bis).toLocaleString(LANG === 'en' ? 'en' : 'de', { dateStyle: 'short', timeStyle: 'short' }))
    : d.in_vitrine ? t('tl_in_vitrine') : '';
  st.classList.toggle('hidden', !st.textContent);
  if ($('tl-frist') && !d.bis) $('tl-frist').value = '';
}

function teilenKopieren() {
  const url = $('tl-url').value;
  (navigator.clipboard ? navigator.clipboard.writeText(url) : Promise.reject())
    .then(() => toast(t('link_kopiert')))
    .catch(() => { $('tl-url').select(); if (document.execCommand) document.execCommand('copy'); });
}

/* ==========================================================================
   Der Einstieg für Gäste in einer geteilten Ansicht

   Ein geteilter Binder ist der häufigste erste Kontakt mit Binderplan: jemand
   bekommt einen Link von einem Sammler, den er kennt. Bis zum 10.09.2026 endete
   dieser Besuch in einer Ansicht ohne jeden Hinweis, dass man dasselbe selbst
   tun kann — die Seite warb für alles außer sich selbst.

   Der Streifen erscheint nur für Nicht-Angemeldete und nur in der Nur-Ansicht,
   und er trägt `?ref=b` weiter, damit messbar wird, ob geteilte Binder Konten
   bringen.
   ========================================================================== */

function gastEinstiegZeigen() {
  const box = $('gast-einstieg');
  if (!box) return;
  const zeigen = !!S.nurAnsicht && !S.user;
  box.classList.toggle('hidden', !zeigen);
  if (!zeigen) return;
  const el = $('gast-einstieg-titel');
  if (el) el.textContent = t('ge_t');
  // Der Streifen steht im Markup in der Binder-Spalte, gehört aber an den Anfang der
  // Seite: unter dem Binder sieht ihn niemand, der nach zwei Seiten wieder geht.
  // Verschoben wird er erst hier, weil die Ansichtsleiste (vitrine.js) asynchron
  // entsteht — vorher gäbe es nichts, worunter man ihn hängen könnte.
  const anker = document.getElementById('vt-ansichtleiste') || document.querySelector('.ansicht-banner');
  if (anker && anker.nextElementSibling !== box) anker.after(box);
}

/** „So einen will ich auch": leerer Binder, ohne Konto, mit Herkunftsmarke. */
function gastEigenerBinder() {
  try { sessionStorage.setItem('bp_ref', 'b'); } catch (e) { /* privates Fenster */ }
  location.href = 'app?ref=b#planer';
  location.reload();
}
