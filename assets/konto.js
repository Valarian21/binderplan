// Binderplan – Start (boot), Konto, Tarife, Startseite, Profilseite.
// Aus index.html herausgelöst (Phase 5, 10.09.2026); die Dateien laden per defer in dieser Reihenfolge:
// kern → konto → werkbank → vitrine → preise → planer → detail → artwork → markt → sammlung.
// ---------- Start ----------
async function boot() {
  try { S.meta = await api('api/meta'); } catch (e) { toast(t('fehler_server')); return; }
  if (S.token) { try { S.user = (await api('api/auth/me')).user; } catch (e) {} }
  if (!S.user && S.token) setToken('');
  syncBanner();
  baueFilterLeiste();
  baueDexNamen();
  setLang(LANG);
  kontoAnzeigen();
  autofillSperren();

  // Passwort-Reset-Link?
  const q0 = new URLSearchParams(location.search);
  if (q0.get('reset')) {
    resetToken = q0.get('reset');
    history.replaceState(null, '', location.pathname + location.hash);
    $('modal-auth').classList.remove('hidden');
    authTab('reset');
  }

  if (q0.get('hinweis') === 'privat') {
    history.replaceState(null, '', location.pathname + location.hash);
    setTimeout(() => toast(t('binder_privat')), 400);
  }
  // Rückkehr von Stripe?
  const q = new URLSearchParams(location.search);
  if (q.get('zahlung')) {
    toast(q.get('zahlung') === 'ok' ? t('zahlung_ok') : t('zahlung_abbruch'));
    history.replaceState(null, '', location.pathname + location.hash);
    if (q.get('zahlung') === 'ok') setTimeout(async () => {
      try { S.user = (await api('api/auth/me')).user; kontoAnzeigen(); } catch (e) {}
    }, 2500);
  }

  let hash = routeLesen();
  if (hash === 'vitrine/kunst') hash = 'vitrine';        // alter Sprung, Kunst ist jetzt die Erstansicht
  if (hash === 'vitrine/binder') { VT.bereich = 'binder'; hash = 'vitrine'; }
  // Einstiege von den öffentlichen Seiten (/set/…, /pokemon/…, /karte/…): direkt zum Set,
  // zum Pokémon oder in den Kartendialog — nicht auf die Startseite.
  let einstieg = null;
  const mEin = hash.match(/^(set|pokemon|karte)\/(.+)$/);
  if (mEin) { einstieg = { art: mEin[1], id: decodeURIComponent(mEin[2]) }; hash = 'suche'; history.replaceState(null, '', APP_BASIS + '#suche'); }
  if (hash.startsWith('ansicht/')) {
    try {
      S.binder = await api('api/binders/' + hash.split('/')[1]);
      S.nurAnsicht = true;
      ansichtAktivieren();
      return;
    } catch (e) { S.binder = null; }
  }
  if (hash.startsWith('binder/')) {
    try {
      S.binder = await api('api/binders/' + hash.split('/')[1]);
      merkeBinderId(S.binder.id);
    } catch (e) { S.binder = null; }
  }
  if (!S.binder) {
    const ids = binderIds();
    if (ids.length) { try { S.binder = await api('api/binders/' + ids[0]); } catch (e) {} }
  }
  if (!S.binder && S.user) {
    // Neustes Konto-Binder als Rückfall (z. B. neues Gerät ohne lokale Liste)
    try {
      const d = await api('api/binders?ids=');
      if (d.binder.length) { S.binder = await api('api/binders/' + d.binder[0].id); merkeBinderId(S.binder.id); }
    } catch (e) {}
  }
  const zeigeStart = (S.user && !hash) || hash === 'start';
  if (!S.binder && !zeigeStart) S.binder = lokalerBinder();   // wird erst mit der ersten Karte gespeichert
  if (S.binder) binderAnzeigen();
  if (einstieg && einstieg.art === 'set') { filter.set = einstieg.id; $('f-set').value = einstieg.id; zeichneSetWahlKnopf(); }
  if (einstieg && einstieg.art === 'pokemon') {
    filter.dex = parseInt(einstieg.id, 10) || 0;
    try { await ladePokedex(); } catch (e) {}
    const pn = (S.pokedex || []).find((x) => x.dex === filter.dex || x.dex_id === filter.dex || x.id === filter.dex);
    if (pn && $('f-dex')) $('f-dex').value = (LANG === 'en' ? pn.name_en : pn.name) || '';
  }
  mehrFilterZahl();
  // Die Trefferliste gehört zur Suche, nicht zur Startseite. Wer die App auf der Startseite
  // öffnet, lud vorher 51 Kartenbilder eines Sets im Rücken — 242 Anfragen beim Start.
  if (zeigeStart) { S.sucheOffen = false; startOeffnen(); } else { S.sucheOffen = false; }
  if (einstieg && einstieg.art === 'karte') setTimeout(() => detailOeffnenId(einstieg.id), 300);
  if (new URLSearchParams(location.search).get('upgrade')) { history.replaceState(null, '', location.pathname + location.hash); upgradeOeffnen(''); }
  const bestToken = new URLSearchParams(location.search).get('bestaetigen');
  if (bestToken) {
    history.replaceState(null, '', location.pathname + location.hash);
    await bestaetigungEinloesen(bestToken);
  }
  // Die Ansicht wiederherstellen, in der zuletzt gearbeitet wurde. „suche" ist der
  // Grundzustand und braucht kein eigenes Öffnen.
  if (ROUTEN.includes(hash) && hash !== 'suche' && !(hash === 'start' && zeigeStart)) ansicht(hash);
  bestaetigungAnzeigen();
  // Werkbank-Grundzustand: am Handy ist der Binder immer da (die Suche ein Sheet); am Desktop
  // öffnet sich die Schublade nur, wenn der Binder noch leer ist.
  if (window.innerWidth < 901) document.body.classList.add('binder-an');
  else if (!zeigeStart && S.binder && !S.binder.items.length && !S.nurAnsicht) sucheLadeOeffnen(false);
}

function ansichtAktivieren() {
  document.querySelector('.spalte-filter').classList.add('hidden');
  document.querySelector('.spalte-mitte').classList.add('hidden');
  document.body.classList.add('nur-ansicht');
  document.querySelectorAll('#wb-sammel, .mobil-only').forEach((el) => el.classList.add('hidden'));
  $('wb-titel').textContent = S.binder.name;
  // Mobil: direkt die Binder-Ansicht zeigen, Such-/Filter-Knöpfe ausblenden
  document.body.classList.add('binder-an');
  ['mnav-filter', 'mnav-suche', 'mnav-binder'].forEach((id) => { const el = $(id); if (el) el.classList.add('hidden'); });
  document.querySelector('.spalte-binder').style.margin = '0 auto';
  if (sessionStorage.getItem('bp_von_vitrine')) document.body.classList.add('von-vitrine');
  window.addEventListener('resize', ansichtGroesse);
  const banner = document.createElement('div');
  banner.className = 'ansicht-banner';
  banner.textContent = t('nur_ansicht') + ' – ' + S.binder.name;
  document.querySelector('.kopf').after(banner);
  $('wb-name').value = S.binder.name;
  $('wb-name').readOnly = true;
  S.seite = 0;
  zeichneLayouts();
  zeichneBinder();
  // Beim Stöbern ist die Übersicht der bessere Einstieg; wer lieber blättert, schaltet um.
  S.alleSeiten = localStorage.getItem('bp_alle_seiten') !== '0';   // Nur-Ansicht: Übersicht ist der bessere Einstieg
  binderAnsicht(S.alleSeiten);
  ansichtGroesse();
  ansichtBegleitung();
}

/**
 * Nur-Ansicht: die Binderseite soll ohne Scrollen ganz zu sehen sein. Die Spaltenbreite
 * wird deshalb aus der freien Höhe und dem Seitenverhältnis des Rasters berechnet
 * (63×88-mm-Fächer plus Fugen), statt fest 700 px zu sein. Handy behält sein gestapeltes Layout.
 */
/** Alle Seiten des Binders auf einmal. Beim Stöbern in einem fremden Binder ist das der
 *  natürliche Blick: man will sehen, was drin ist, nicht zwölfmal auf „weiter“ klicken. */
/** Zwischen „Eine Seite" und „Alle Seiten" umschalten. Die Wahl bleibt gespeichert.
 *  Vorher waren das zwei Orte: die Galerie im Reiter „Suchen" und der Planer als eigener
 *  Reiter — beide zeigten dieselben Seiten mit denselben Fächern. */
function binderAnsicht(alle) {
  S.alleSeiten = !!alle;
  if (S.alleSeiten && window.innerWidth >= 901 && typeof sucheLadeZu === 'function') sucheLadeZu();
  try { localStorage.setItem('bp_alle_seiten', S.alleSeiten ? '1' : ''); } catch (e) {}
  if (!S.alleSeiten) S.auswahl.clear();
  hashSetzen(S.alleSeiten ? 'planer' : 'suche');     // beide Adressen bleiben gültig
  breiteAnpassen();
  zeichneBinder();
}

/** Bei „Alle Seiten" bekommt der Binder die freie Fläche, sofern die Suche zu ist. */
function breiteAnpassen() {
  const sp = document.querySelector('.spalte-binder');
  const mitte = document.querySelector('.spalte-mitte');
  if (!sp || S.nurAnsicht) return;
  const breit = !!S.alleSeiten;
  sp.style.width = breit ? 'auto' : '';
  sp.style.flex = breit ? '1 1 auto' : '';
  if (mitte) mitte.classList.toggle('hidden', breit);
  document.body.classList.toggle('alle-seiten', breit);
}

/** Klick auf eine Seitenüberschrift in der Übersicht: diese Seite groß. */
function seiteOeffnen(nr) {
  if (S.nurAnsicht) return;
  S.seite = nr;
  binderAnsicht(false);
}

/** Der Umschalter über dem Binder — derselbe für Besitzer und Besucher. */
function zeichneBinderUmschalter() {
  const box = $('gal-umschalter');
  if (!box || !S.binder) return;
  box.classList.remove('hidden');
  box.innerHTML = `
    <div class="segment klein" role="tablist">
      <button class="${S.alleSeiten ? '' : 'on'}" onclick="binderAnsicht(false)">${t('gal_einzeln')}</button>
      <button class="${S.alleSeiten ? 'on' : ''}" onclick="binderAnsicht(true)">${t('gal_alle')}</button>
    </div>
    <div style="flex:1"></div>
    ${S.alleSeiten && !S.nurAnsicht ? `<span class="pl-hilfe" id="wb-auswahl-hilfe"></span>` : ''}
    <button class="chip" onclick="blaetternOeffnen()">${t('bv_titel')}</button>`;
  const h = $('wb-auswahl-hilfe');
  if (h) h.innerHTML = `<button class="chip" onclick="modalOeffnen('modal-planerhilfe')" title="${t('pl_hilfe_t')}">?</button>`;
}

function ansichtGroesse() {
  if (!S.nurAnsicht || !S.binder) return;
  const sp = document.querySelector('.spalte-binder');
  if (S.alleSeiten) { sp.style.width = 'min(1200px, 100%)'; return; }   // die Übersicht darf breit sein
  if (window.innerWidth < 900) { sp.style.width = ''; return; }
  const [cols, rows] = LAYOUTS[S.binder.layout] || [3, 3];
  const oben = document.querySelector('.rumpf').getBoundingClientRect().top;   // Kopf, Banner, Begleitleiste
  const frei = window.innerHeight - oben - 150;                               // Statuszeile, Schalter, Seitennavigation
  const verhaeltnis = (cols * 63 + (cols - 1) * 7 + 20) / (rows * 88 + (rows - 1) * 7 + 20);
  const breite = Math.round(Math.min(700, window.innerWidth - 40, Math.max(300, frei * verhaeltnis + 28)));
  sp.style.width = breite + 'px';
}

// ---------- Konto ----------
function setToken(token) {
  S.token = token;
  if (token) {
    localStorage.setItem('bp_token', token);
    document.cookie = 'bp_token=' + token + '; path=/; max-age=31536000; SameSite=Lax';
  } else {
    localStorage.removeItem('bp_token');
    document.cookie = 'bp_token=; path=/; max-age=0';
    S.user = null;
  }
}

function kontoAnzeigen() {
  // Tarif-Marken (Plus/Pro) nur zeigen, wenn die Funktion für dieses Konto gesperrt ist
  const frei = !!(S.user && S.user.plan && S.user.plan !== 'free');
  document.querySelectorAll('.plan-marke').forEach((e) => e.classList.toggle('hidden', frei));
  if (typeof mnavAnpassen === 'function') mnavAnpassen();
  if (typeof bestaetigungAnzeigen === 'function') bestaetigungAnzeigen();
  if (typeof besitzLaden === 'function') besitzLaden().then(() => {
    if (S.binder) zeichneBinder();
    if (S.alleSeiten) zeichneAlleSeiten();
    if (typeof habStandZeigen === 'function') habStandZeigen();
  });
  const btn = $('btn-konto');
  if (S.user) {
    // Ein Kreis mit dem Anfangsbuchstaben, der Name daneben – die volle Adresse stand
    // hier 260 px breit und drängte am Handy den Binder-Namen aus der Kopfzeile.
    const n = anzeigeName();
    btn.innerHTML = `<span class="avatar" aria-hidden="true">${esc(n.slice(0, 1).toUpperCase())}</span><span class="konto-name">${esc(n)}</span>`;
    btn.title = S.user.email;
  } else {
    btn.textContent = t('anmelden');
    btn.title = '';
  }
}

function kontoMenue() {
  if (!S.user) return loginOeffnen('');
  menuToggle('menu-konto');
  const u = S.user;
  const planLbl = u.plan_name || t('tarif_frei');
  const exporte = u.exporte_limit == null ? '∞' : `${Math.max(0, u.exporte_limit - u.exporte_benutzt)}/${u.exporte_limit}`;
  $('menu-konto').innerHTML = `
    <div class="konto-mail"><strong style="color:var(--ink)">${esc(anzeigeName())}</strong><br>${esc(u.email)}<br>
      ${planLbl}${u.plan !== 'free' ? ' ' : ''} · ${exporte} ${t('exporte_uebrig')}<br>
      <span class="credit-chip" style="margin-top:5px"><svg class="ic" width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m10 3 1.6 4.6L16.2 9.2l-4.6 1.6L10 15.4l-1.6-4.6L3.8 9.2l4.6-1.6z"/></svg> ${u.credits || 0} ${t('credits')}</span></div>
    <div class="trenn"></div>
    <button onclick="startOeffnen()">${t('start')}</button>
    <button onclick="profilOeffnen()">${t('profil')}</button>
    <div class="trenn"></div>
    <button onclick="upgradeOeffnen('')">${u.plan === 'free' ? t('upgrade') : t('credits_kaufen')}</button>
    ${u.plan !== 'free' && u.plan !== 'lifetime' ? `<button onclick="aboVerwalten()">${t('abo_verwalten')}</button>` : ''}
    <button onclick="window.open('recht','_blank')">${t('recht_link')}</button>
    <button onclick="window.open('/kuendigen','_blank')">${t('kuend_link')}</button>
    <button onclick="abmelden()">${t('abmelden')}</button>`;
}

let authModus = 'login';
let resetToken = '';
function loginOeffnen(grund) {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  $('auth-grund').textContent = grund || '';
  $('modal-auth').classList.remove('hidden');
  authTab(grund ? 'reg' : 'login');
}
function authTab(modus) {
  authModus = modus;
  const reset = modus === 'reset';
  $('auth-tab-login').classList.toggle('on', modus === 'login');
  $('auth-tab-reg').classList.toggle('on', modus === 'reg');
  $('auth-tab-login').parentElement.classList.toggle('hidden', reset);
  $('auth-email').classList.toggle('hidden', reset);
  $('auth-email-lbl').classList.toggle('hidden', reset);
  $('auth-agb').classList.toggle('hidden', modus !== 'reg');
  $('auth-geb-box').classList.toggle('hidden', modus !== 'reg');
  $('auth-pw-hint').classList.toggle('hidden', modus === 'login');
  $('auth-vergessen').classList.toggle('hidden', modus !== 'login');
  $('auth-titel').textContent = reset ? t('pw_neu_t') : t('anmelden_t');
  $('auth-senden').textContent = reset ? t('pw_neu_senden') : (modus === 'reg' ? t('registrieren') : t('anmelden'));
  $('auth-fehler').textContent = '';
}
async function passwortVergessen() {
  const email = $('auth-email').value.trim();
  if (!email) { $('auth-fehler').textContent = t('pw_email_noetig'); return; }
  try {
    await api('api/auth/passwort_vergessen', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    modalSchliessen();
    toast(t('pw_mail_raus'));
  } catch (e) {
    $('auth-fehler').textContent = e.code === 'mail' ? t('mail_fehlt') : e.message;
  }
}
async function authAbsenden() {
  const email = $('auth-email').value.trim();
  const pw = $('auth-pw').value;
  if (authModus === 'reset') {
    try {
      const d = await api('api/auth/passwort_neu', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: resetToken, passwort: pw }),
      });
      setToken(d.token); S.user = d.user;
      modalSchliessen(); kontoAnzeigen();
      toast(t('pw_geaendert'));
    } catch (e) { $('auth-fehler').textContent = e.message; }
    return;
  }
  if (authModus === 'reg' && !$('auth-agb-check').checked) {
    $('auth-fehler').textContent = t('agb_noetig');
    return;
  }
  if (authModus === 'reg' && !gebIso($('auth-geb').value)) {
    $('auth-fehler').textContent = t('geb_noetig');
    return;
  }
  try {
    const d = await api('api/auth/' + (authModus === 'reg' ? 'register' : 'login'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, passwort: pw, geburtsdatum: gebIso($('auth-geb').value) }),
    });
    setToken(d.token);
    S.user = d.user;
    const nameFragen = authModus === 'reg';
    // Anonyme Binder dem Konto zuordnen
    const ids = binderIds();
    if (ids.length) {
      try {
        const c = await api('api/auth/claim', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids }) });
        if (c.uebernommen) toast(t('uebernommen'));
      } catch (e) {}
      S.user = (await api('api/auth/me')).user;
    }
    modalSchliessen();
    kontoAnzeigen();
    toast(t('willkommen') + ' ' + d.user.email);
    if (nameFragen) { $('modal-name').classList.remove('hidden'); setTimeout(() => $('name-neu').focus(), 50); }
  } catch (e) {
    $('auth-fehler').textContent = e.message;
  }
}
async function nameNeuSpeichern() {
  const name = $('name-neu').value.trim();
  modalSchliessen();
  if (!name) return;
  try { const d = await api('api/auth/profil', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) }); S.user = d.user; kontoAnzeigen(); } catch (e) {}
}
async function abmelden() {
  try { await api('api/auth/logout', { method: 'POST' }); } catch (e) {}
  setToken('');
  // Konto-Binder gehören nicht in die anonyme Sitzung — frisch als Gast starten
  localStorage.removeItem('bp_binder');
  location.href = location.pathname;
}

// ---------- Tarife, Credits, Bestellung ----------
const TARIF = { daten: null, zeitraum: 'monat', auswahl: null };

async function tarifeLaden() {
  if (TARIF.daten) return TARIF.daten;
  TARIF.daten = await api('api/tarife');
  return TARIF.daten;
}
function upgradeOeffnen(grund) {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  if (!S.user) return loginOeffnen(grund || t('up_login'));
  $('up-grund').textContent = grund ? grund + ' ' + t('up_u') : t('up_u');
  $('modal-upgrade').classList.remove('hidden');
  tarifeLaden().then(zeichneTarife).catch(() => toast(t('aw_fehler')));
}
function tarifZeitraum(z) {
  TARIF.zeitraum = z;
  $('tz-monat').classList.toggle('on', z === 'monat');
  $('tz-jahr').classList.toggle('on', z === 'jahr');
  zeichneTarife();
}
function zeichneTarife() {
  const d = TARIF.daten; if (!d) return;
  const jahr = TARIF.zeitraum === 'jahr';
  const mein = (S.user || {}).plan || 'free';
  // Lifetime hat alles – Abo-Kacheln mit „Wählen" wären ein Abstieg, den niemand will.
  $('tarif-karten').innerHTML = mein === 'lifetime' ? `<div class="unter" style="grid-column:1/-1">${t('tarif_life_hinweis')}</div>` : d.tarife.map((tf) => {
    const preis = jahr ? tf.preis_jahr : tf.preis_monat;
    const ist = mein === tf.id;
    const zeilen = [];
    zeilen.push(`<li>${tf.binder == null ? t('unbegrenzt_binder') : tf.binder + ' ' + t('binder_wort')}</li>`);
    zeilen.push(`<li>${tf.exporte == null ? t('unbegrenzt_pdf') : tf.exporte + ' ' + t('pdf_monat')}</li>`);
    zeilen.push(`<li>${t('checkliste_frei')}</li>`);
    zeilen.push(`<li class="${tf.preise_live ? '' : 'aus'}">${tf.preise_live ? t('preise_live') : t('preise_tag')}</li>`);
    zeilen.push(`<li class="${tf.kaufliste ? '' : 'aus'}">${t('kaufliste_csv')}</li>`);
    const cr = tf.credits ? `${tf.credits} ${t('credits')} ${t('pro_monat')}`
      : `${d.start_credits} ${t('credits')} ${t('zum_start')}`;
    return `<div class="tk-tarif ${tf.id === 'plus' ? 'empfohlen' : ''}">
      ${tf.id === 'plus' ? `<span class="empf-band">${t('beliebt')}</span>` : ''}
      <div class="tk-name">${esc(tf.name)}</div>
      <div class="tk-preis">${preis ? preis.toFixed(2).replace('.', ',') + ' €' : '0 €'}
        <small>${preis ? (jahr ? t('pro_jahr') : t('pro_monat')) : ''}</small></div>
      <div class="tk-credits"><svg class="ic" width="13" height="13" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m10 3 1.6 4.6L16.2 9.2l-4.6 1.6L10 15.4l-1.6-4.6L3.8 9.2l4.6-1.6z"/></svg> ${cr}</div>
      <ul>${zeilen.join('')}</ul>
      ${ist ? `<button class="btn sekundaer" disabled>${t('aktuell')}</button>`
        : (preis ? `<button class="btn" onclick="bestellungOeffnen('${tf.id}','${jahr ? 'jahr' : 'monat'}')">${t('waehlen')}</button>` : '')}
    </div>`;
  }).join('');
  $('paket-karten').innerHTML = d.pakete.map((pk) => `
    <div class="paket" onclick="bestellungOeffnen('paket','${pk.id}')">
      <div class="p-cr">${pk.credits} ${t('credits')}</div>
      <div class="p-eur">${pk.preis.toFixed(2).replace('.', ',')} €</div>
      <div class="p-je">${(pk.preis / pk.credits * 100).toFixed(1).replace('.', ',')} ct / ${t('credits').slice(0, -1)}</div>
    </div>`).join('');
}

// Bestellübersicht: Pflicht nach § 312j BGB (Button-Lösung) samt beider Zustimmungen
function bestellungOeffnen(art, variante) {
  const d = TARIF.daten; if (!d) return;
  TARIF.auswahl = { art, variante };
  let name, betrag, laufzeit;
  if (art === 'paket') {
    const pk = d.pakete.find((x) => x.id === variante);
    name = `${pk.credits} ${t('credits')}`; betrag = pk.preis; laufzeit = t('best_einmalig');
  } else {
    const tf = d.tarife.find((x) => x.id === art);
    betrag = variante === 'jahr' ? tf.preis_jahr : tf.preis_monat;
    name = `Binderplan ${tf.name} – ${variante === 'jahr' ? t('jaehrl') : t('mtl')}`;
    laufzeit = `${variante === 'jahr' ? t('jaehrl') : t('mtl')}, ${t('best_verlaengert')}`;
  }
  $('bestell-box').innerHTML = `
    <div class="bestell-zeile"><span>${t('best_leistung')}</span><strong>${esc(name)}</strong></div>
    <div class="bestell-zeile"><span>${t('best_laufzeit')}</span><span style="text-align:right;max-width:60%">${laufzeit}</span></div>
    ${art !== 'paket' ? `<div class="bestell-zeile"><span><svg class="ic" width="13" height="13" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m10 3 1.6 4.6L16.2 9.2l-4.6 1.6L10 15.4l-1.6-4.6L3.8 9.2l4.6-1.6z"/></svg> ${t('credits')}</span><strong>${d.tarife.find((x) => x.id === art).credits} ${t('pro_monat')}</strong></div>` : ''}
    <div class="bestell-zeile gesamt"><span>${t('best_gesamt')}</span><span>${betrag.toFixed(2).replace('.', ',')} €</span></div>
    <div style="font-size: var(--t-xs);color:var(--mut);margin-top:6px">${t('up_recht_kurz')}</div>`;
  $('best-agb-text').innerHTML = t('best_agb_html');
  $('best-widerruf-text').textContent = d.widerruf_text;
  $('best-agb').checked = false; $('best-widerruf').checked = false;
  $('best-fehler').textContent = '';
  $('best-senden').disabled = false;
  $('modal-upgrade').classList.add('hidden');
  $('modal-bestellung').classList.remove('hidden');
}
async function bestellungAbsenden() {
  if (!$('best-agb').checked || !$('best-widerruf').checked) {
    $('best-fehler').textContent = t('best_fehlt'); return;
  }
  $('best-senden').disabled = true;
  try {
    const d = await api('api/stripe/checkout', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...TARIF.auswahl, agb: true, widerruf: true }),
    });
    location.href = d.url;
  } catch (e) {
    $('best-senden').disabled = false;
    if (!gate(e)) $('best-fehler').textContent = e.message || t('aw_fehler');
  }
}
async function creditsOeffnen() {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  if (!S.user) return loginOeffnen('');
  await tarifeLaden();
  upgradeOeffnen('');
}
async function aboVerwalten() {
  try {
    const d = await api('api/stripe/portal', { method: 'POST' });
    location.href = d.url;
  } catch (e) { toast(e.message); }
}

// ---------- Übersichtsseite ----------
async function startOeffnen() {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  // sammlung.js kommt per defer nach diesem Skript – beim Start kann es noch fehlen
  vitrineSchliessen(); if (typeof sammlungSchliessen === 'function') sammlungSchliessen();
  if (typeof marktSchliessen === 'function') marktSchliessen();
  $('profilseite').classList.add('hidden');
  $('profilseite').classList.add('hidden');
  $('startseite').classList.remove('hidden');
  $('startseite').scrollTop = 0;
  mnavMarkieren('mnav-start');
  reiterSetzen('seg-start');
  hashSetzen('start');
  if (typeof zeichneSeitenleiste === 'function') zeichneSeitenleiste();
  $('st-gruss').textContent = S.user && S.user.name ? t('hallo') + ', ' + S.user.name + '!' : t('hallo') + '!'; startNameFrage();
  await startLaden();
}
function startSchliessen() {
  // Wer die Startseite verlässt, landet in der Werkbank — dann wird die Trefferliste gebraucht.
  $('startseite').classList.add('hidden');
  reiterSetzen('seg-suche');
  if (S.binder && S.binder.id) hashSetzen('binder/' + S.binder.id);
  else neuLeer(true);
}
async function startLaden() {
  // Drei Anfragen ohne Datenabhängigkeit liefen nacheinander: bis zur ersten Zahl vergingen
  // drei Server-Rundreisen. Jetzt starten sie zusammen.
  const [bl, smAntwort, vt] = await Promise.allSettled([
    api('api/binders?ids=' + binderIds().join(',')),
    S.user ? api('api/sammlung/uebersicht') : Promise.resolve(null),
    api('api/vitrine/artwork?sortierung=top&limit=4'),
  ]);
  const liste = bl.status === 'fulfilled' ? (bl.value.binder || []) : [];
  const sm = smAntwort.status === 'fulfilled' ? smAntwort.value : null;
  const faecher = liste.reduce((a, b) => a + b.anzahl, 0);

  // Kopf
  $('st-gruss').textContent = !S.user ? t('st_gast_gruss') : S.user.name ? t('hallo') + ', ' + S.user.name + '!' : t('hallo') + '!'; startNameFrage();
  const bezahlt = S.user && S.user.plan && S.user.plan !== 'free';
  $('st-plan').textContent = (S.user && S.user.plan_name) || t('tarif_frei');
  $('st-plan').classList.toggle('bezahlt', !!bezahlt);
  $('st-plan').classList.toggle('hidden', !S.user);
  $('st-profil-btn').textContent = S.user ? t('profil') : t('anmelden');
  $('st-unter').textContent = !S.user ? t('st_gast_u') : liste.length ? t('st_unter_voll') : t('st_unter_leer');
  // Gäste: dieselbe Seite, ein Kasten mit dem Angebot statt der Konto-Blöcke
  $('st-gast').hidden = !!S.user;
  if (!S.user) $('st-gast').innerHTML = `<strong>${t('gast_start_t')}</strong><span>${t('gast_start_u')}</span>
      <div class="gast-knoepfe"><button class="btn" onclick="loginOeffnen(t('gast_start_grund'))">${t('gast_registrieren')}</button>
      <button class="btn sekundaer" onclick="loginOeffnen('');authTab('login')">${t('gast_anmelden')}</button></div>`;

  if (S.user) {
    creatorNeuLaden();
    if (typeof zieleStartLaden === 'function') zieleStartLaden();
    if (typeof alarmeStartLaden === 'function') alarmeStartLaden();
    if (typeof digestStartLaden === 'function') digestStartLaden();
  } else {
    ['st-alarme-block', 'st-ziele-block', 'st-digest-block', 'st-creator-neu'].forEach((id) => { const el = $(id); if (el) el.hidden = true; });
  }

  // Zahlenband: drei Kacheln statt vier. Zwei der alten vier sagten, wie viel *geplant* ist,
  // und keine sagte, was sich verändert hat — dabei rechnet die Sammlung die 7-Tage-Bewegung
  // seit September. „Karten geplant" ist eine Zahl ohne Handlung; die offenen Fächer, die
  // daneben stehen, sind eine.
  const eur = (n) => (n || 0).toLocaleString(LANG === 'en' ? 'en' : 'de',
    { minimumFractionDigits: 0, maximumFractionDigits: 0 }) + ' €';
  const hatZahlen = liste.length > 0 || (sm && sm.karten > 0);
  $('st-band').classList.toggle('hidden', !hatZahlen);
  const offen = liste.reduce((a, b) => a + Math.max(0, (b.anzahl || 0) - (b.gesammelt || 0)), 0);
  const bew = sm && sm.bew7 != null
    ? `<span class="${sm.bew7 > 0.05 ? 'an-plus' : sm.bew7 < -0.05 ? 'an-minus' : ''}">${anProz(sm.bew7)}</span> · ${t('mk_7t')}`
    : '';
  $('st-band').innerHTML = !hatZahlen ? '' : [
    [sm ? eur(sm.wert) : '–', t('st_sm_wert'), sm && sm.wert > 0 ? 'gruen' : '', "startSchliessen();ansicht('sammlung')", bew],
    [sm ? sm.karten : '–', t('st_karten_besitz'), '', "startSchliessen();ansicht('sammlung')",
      sm && sm.verschiedene ? t('st_verschieden_n').replace('{n}', sm.verschiedene) : ''],
    [offen, t('st_offen'), '', 'startSchliessen()', t('st_offen_u').replace('{n}', liste.length)],
  ].map(([z, l, k, fn, unten]) => `<button class="st-zahl ${k}" onclick="${fn}"><div class="z">${z}</div><div class="l">${esc(l)}</div>${unten ? `<div class="u">${unten}</div>` : ''}</button>`).join('');

  // Weiter, wo du warst
  // Der Binder, in dem man gerade steckt, hat Vorrang vor dem zuletzt geänderten — sonst
  // schlägt die Startseite einen anderen vor als den, den man eben offen hatte.
  const zuletzt = (S.binder && S.binder.id && liste.find((b) => b.id === S.binder.id))
    || liste.slice().sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))[0];
  $('st-weiter-block').hidden = !zuletzt;
  if (zuletzt) {
    const bilder = (zuletzt.vorschau || []).slice(0, 5).map((id) => `<img loading="lazy" src="${imgUrl(id)}" alt="">`).join('');
    $('st-weiter').innerHTML = `
      <div class="vor">${bilder || ''}</div>
      <div class="txt"><strong>${esc(zuletzt.name)}</strong>
        <div>${zuletzt.karten != null ? zuletzt.karten : zuletzt.anzahl} ${t('karten_wort')} · ${zuletzt.seiten} ${t('seiten_wort')} · ${t('st_zuletzt')} ${(zuletzt.updated_at || '').slice(0, 10)}</div></div>
      <button class="btn" style="font-size: var(--t-m)" onclick="startBinderOeffnen('${zuletzt.id}')">${t('st_weiterplanen')}</button>`;
  }

  // Der Block „Was möchtest du tun" ist weg: drei Einstiege auf einem Bildschirm
  // konkurrierten miteinander, und alle vier Wege stehen ohnehin hinter „Neuer Binder".
  zeichneStartBinder(liste);

  // Aus der Vitrine
  // Kunstseiten statt Binder: die Vitrine führt sie seit September vorn, und sie sind das
  // Einzige hier, in dem echte Arbeit steckt. Ein Klick öffnet die Vitrine bei dieser Seite.
  try {
    const v = vt.status === 'fulfilled' ? vt.value : { artworks: [] };
    const kunst = (v.artworks || []).slice(0, 4);
    $('st-vitrine-block').hidden = !kunst.length;
    $('st-vitrine').innerHTML = kunst.map((a) => `
      <div class="st-vk" onclick="startSchliessen();ansicht('vitrine');setTimeout(() => kunstGross('${esc(a.id)}'), 700)">
        <div class="bilder eins"><img loading="lazy" src="api/artwork/${encodeURIComponent(a.id)}/bild?v=vorschau" alt=""></div>
        <div class="t"><strong>${esc(a.titel)}</strong><span>${esc(a.besitzer)}${a.downloads ? ` · ${a.downloads}× ${t('vt_k_geholt')}` : ''}${a.stimmen ? ` · ${a.stimmen} ♥` : ''}</span></div>
      </div>`).join('');
  } catch (e) { $('st-vitrine-block').hidden = true; }
}

function zeichneStartBinder(liste) {
  const modeLbl = { master: t('mode_master'), dex: t('mode_dex'), custom: t('mode_custom') };
  $('st-grid').innerHTML = liste.map((b) => {
    const proz = b.anzahl ? Math.round((b.gesammelt || 0) / b.anzahl * 100) : 0;
    // Seit die Startseite denselben Stapel zeigt wie die Vitrine, sieht ein Binder überall
    // gleich aus. Die alten drei Miniaturen bleiben als Rückfall, falls kein Raster kommt.
    const stapel = stapelBild(b);
    const thumbs = (b.vorschau || []).map((id) => `<img loading="lazy" src="${imgUrl(id)}" alt="">`).join('')
      + (b.dex_vorschau || []).slice(0, 3 - (b.vorschau || []).length).map((d) => `<img loading="lazy" src="api/img/dex/${d}" alt="" style="object-fit:contain;padding:4px">`).join('');
    return `<div class="bcard" onclick="startBinderOeffnen('${b.id}')">
      ${stapel ? `<div class="bstapel">${stapel}</div>`
               : `<div class="bthumbs">${thumbs || '<span></span><span></span><span></span>'}</div>`}
      <span class="badge">${modeLbl[b.mode] || t('mode_custom')}</span>
      <div class="bname"><span>${esc(b.name)}</span>
        <span onclick="event.stopPropagation();startBinderLoeschen('${b.id}')" title="${t('bestaetigen_loeschen')}" style="color:var(--mut)">${ic('muell', 16)}</span></div>
      <div class="bmeta">${b.karten != null ? b.karten : b.anzahl} ${t('karten_wort')} · ${b.seiten} ${t('seiten_wort')}${b.gemischt ? ' ' + t('gemischt') : ''}${b.updated_at ? ' · ' + t('st_zuletzt') + ' ' + b.updated_at.slice(0, 10) : ''}${
        b.anzahl ? ` · <span class="${b.gesammelt ? 'an-plus' : ''}">${b.gesammelt || 0} / ${b.anzahl} ✓</span>` : ''}</div>
      ${b.wert ? `<div class="bmeta bwert">${t('st_komplett_heute')} ≈ <strong>${anEur(b.wert, 0)}</strong>${b.bew30 != null ? ` · <span class="${b.bew30 > 0.05 ? 'an-plus' : b.bew30 < -0.05 ? 'an-minus' : ''}">${anProz(b.bew30)}</span>` : ''}</div>` : ''}
      ${b.anzahl ? `<div class="bfort"><div style="width:${proz}%"></div></div>` : ''}
    </div>`;
  }).join('') + `
    <div class="bcard neu" onclick="vorlagenOeffnen()">
      <div style="font-size: var(--t-3xl)">+</div><div style="font-weight:700">${t('st_neu')}</div>
    </div>`;
  // Ein leeres Konto sah bisher nur diese eine gestrichelte Karte. Die drei Wege, die die
  // Sammlung in ihrem eigenen Leerzustand anbietet, gehören auf die Seite, auf der ein
  // neues Konto landet.
  const leerBlock = $('st-leer');
  if (leerBlock) leerBlock.hidden = liste.length > 0;
  // Am Handy stehen drei Binder, der Rest hinter einem Knopf — sonst war die Seite fünf
  // Bildschirme lang, bevor irgendetwas anderes kam.
  const mehr = $('st-mehr-binder');
  if (mehr) {
    mehr.hidden = liste.length <= 3;
    mehr.textContent = t('st_alle_binder').replace('{n}', liste.length);
    $('st-grid').classList.remove('alle');
  }
}


async function startBinderOeffnen(id) {
  await binderOeffnen(id);
  $('startseite').classList.add('hidden');
  // In den Binder, nicht in das, was zuletzt offen war. Vorher lag unter der
  // Startseite noch die Sammlung — wer von „Weiter, wo du warst" auf einen Binder
  // klickte, landete in der Sammlung und sah seinen Binder gar nicht.
  ansicht('planer');
}
async function startBinderLoeschen(id) {
  if (!confirm(t('bestaetigen_loeschen'))) return;
  try { await api('api/binders/' + id, { method: 'DELETE' }); } catch (e) {}
  localStorage.setItem('bp_binder', JSON.stringify(binderIds().filter((x) => x !== id)));
  if (S.binder && S.binder.id === id) S.binder = null;
  startLaden();
}
async function startNeu(art) {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  if (art === 'leer') { $('startseite').classList.add('hidden'); await neuLeer(); return; }
  $('startseite').classList.add('hidden');
  if (art === 'vorlagen') return vorlagenOeffnen();
  // Ohne aktiven Binder (frisches Konto, gelöschter Binder) stünde die Werkbank auf null und jeder
  // Klick würfe einen Fehler – ein lokaler Gast-Binder ist der sichere Boden, bis die Vorlage kommt.
  if (!S.binder) { S.binder = lokalerBinder(); binderAnzeigen(); }
  if (S.binder.id) hashSetzen('binder/' + S.binder.id);
  if (art === 'import') return importOeffnen();
  modalOeffnen(art === 'master' ? 'modal-master' : art === 'poke' ? 'modal-poke' : 'modal-dex');
}

// ---------- Profilseite ----------
function profilOeffnen() {
  setTimeout(reiterNachAnsicht, 0);
  if (!S.user) return loginOeffnen('');
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  // Markt, Sammlung und Vitrine sind feste Ebenen (z-index 40) – ohne diese Kette lag die
  // Profilseite darunter und „Mein Profil" tat aus dem Markt heraus nichts.
  vitrineSchliessen(); sammlungSchliessen();
  if (typeof marktSchliessen === 'function') marktSchliessen();
  $('startseite').classList.add('hidden');
  $('profilseite').classList.remove('hidden');
  $('profilseite').scrollTop = 0;
  ebeneOeffnen(() => $('profilseite').classList.add('hidden'));
  profilFuellen();
}
function profilSchliessen() {
  setTimeout(reiterNachAnsicht, 0);
  $('profilseite').classList.add('hidden');
  if ($('startseite').classList.contains('hidden')) startOeffnen();
}
function profilFuellen() {
  const u = S.user;
  if (!u) return;
  $('pf-email').textContent = u.email;
  // Wann wurde das Passwort zuletzt geändert? Eine Zeile, die zeigt, dass der Wechsel ankam.
  if ($('pf-pw-stand')) $('pf-pw-stand').textContent = u.pw_geaendert_am ? t('pf_pw_stand').replace('{d}', u.pw_geaendert_am) : '';
  $('pf-name').value = u.name || '';
  $('pf-plan').textContent = u.plan_name || t('tarif_frei');
  const bis = u.abo_kuendigt && u.abo_bis ? `${t('gekuendigt')} ${u.abo_bis}` : (u.abo_bis ? `${t('abo_bis')} ${u.abo_bis}` : '');
  $('pf-plan').title = bis;
  if ($('pf-credits')) {
    $('pf-credits').innerHTML = `<span class="credit-chip">${ic('funke', 13)} ${u.credits || 0} ${t('credits')}</span>`
      + (u.credits_monat ? ` <span style="font-size: var(--t-s);color:var(--mut)">(${u.credits_abo || 0} ${t('guthaben_abo')}, ${u.credits_gekauft || 0} ${t('guthaben_gekauft')})</span>` : '');
  }
  if ($('pf-kuend')) {
    $('pf-kuend').classList.toggle('hidden', u.plan === 'free' || u.plan === 'lifetime');
    $('pf-kuend').textContent = u.abo_kuendigt ? t('reaktivieren') : t('kuendigen');
  }
  journalLaden();
  vitrineProfilLaden();
  creatorLaden();
  $('pf-binder').textContent = u.binder_anzahl + ' / ' + (u.binder_limit == null ? '∞' : u.binder_limit);
  $('pf-exporte').textContent = u.exporte_limit == null ? '∞' : Math.max(0, u.exporte_limit - u.exporte_benutzt) + ' / ' + u.exporte_limit;
  $('pf-upgrade').classList.toggle('hidden', false);
  $('pf-upgrade').textContent = u.plan === 'free' ? t('upgrade') : t('credits_kaufen');
  $('pf-abo').classList.toggle('hidden', u.plan === 'free' || u.plan === 'lifetime');
  $('pf-life').classList.toggle('hidden', u.plan !== 'lifetime');
  $('pf-lang-de').classList.toggle('on', LANG === 'de');
  $('pf-lang-en').classList.toggle('on', LANG === 'en');
  themeAnwenden();
}
// --- Creator-Übersicht -----------------------------------------------------
// Startseiten-Karte: nur, wenn seit dem letzten Wegklicken etwas passiert ist.
async function creatorNeuLaden() {
  const box = $('st-creator-neu'); if (!box || !S.user) return;
  try {
    const d = await api('api/creator?nur=neu');
    const n = d.neu || {};
    const teile = [];
    if (n.uebernahmen === 1) teile.push(t('cr_neu_ueb1'));
    else if (n.uebernahmen > 1) teile.push(t('cr_neu_ueb').replace('{n}', n.uebernahmen));
    if (n.credits > 0) teile.push('<b>' + t('cr_neu_credits').replace('{n}', n.credits) + '</b>');
    if (n.herzen === 1) teile.push(t('cr_neu_herz1'));
    else if (n.herzen > 1) teile.push(t('cr_neu_herzen').replace('{n}', n.herzen));
    box.classList.toggle('hidden', !teile.length);
    if (!teile.length) return;
    box.innerHTML = `<span class="herz">♥</span><div class="txt"><strong>${t('cr_neu_t')}</strong><span>${teile.join(' · ')}</span></div>
      <button class="btn sekundaer" style="font-size: var(--t-s)" onclick="creatorGesehen(true)">${t('cr_ansehen')}</button>`;
  } catch (e) { box.classList.add('hidden'); }
}
async function creatorGesehen(oeffnen) {
  $('st-creator-neu').classList.add('hidden');
  try { await api('api/creator/gesehen', { method: 'POST' }); } catch (e) {}
  if (oeffnen) { profilOeffnen(); setTimeout(() => { const b = $('pf-creator'); if (b && !b.classList.contains('hidden')) b.scrollIntoView({ behavior: 'smooth', block: 'start' }); }, 80); }
}
// Profilblock: Summen und eine Zeile je Seite. Nur Zahlen > 0 werden gezeigt; eine Seite ohne
// Reaktion steht einfach mit ihrem Titel da. Keine Sortierung nach Beliebtheit.
async function creatorLaden() {
  const block = $('pf-creator'); if (!block || !S.user) return;
  let d;
  try { d = await api('api/creator'); } catch (e) { block.classList.add('hidden'); return; }
  const seiten = d.seiten || [];
  block.classList.toggle('hidden', !seiten.length);
  if (!seiten.length) return;
  const su = d.summe || {};
  const zahlen = [[su.seiten, 'cr_seiten'], [su.vitrine, 'cr_vitrine'], [su.uebernahmen, 'cr_uebernahmen'],
                  [su.verdient, 'cr_verdient'], [su.herzen, 'cr_herzen']].filter(([z]) => z > 0);
  $('cr-zahlen').innerHTML = zahlen.map(([z, k]) => `<div class="cr-zahl"><b>${z}</b><span>${t(k)}</span></div>`).join('');
  $('cr-hinweis').textContent = t('cr_hinweis').replace('{a}', VT.anteil || 2);
  const pn = (p) => (LANG === 'en' ? (p.name_en || p.name_de) : (p.name_de || p.name_en)) || '';
  $('cr-seiten').innerHTML = seiten.map((a) => {
    const k = [];
    if (a.herzen > 0) k.push(`<span><b>${a.herzen}</b> ♥</span>`);
    if (a.uebernahmen > 0) k.push(`<span>${t('cr_uebernommen').replace('{n}', '<b>' + a.uebernahmen + '</b>')}</span>`);
    if (a.verdient > 0) k.push(`<span><b>+${a.verdient}</b></span>`);
    if (!k.length) k.push(a.oeffentlich ? `<span>${t('cr_in_vitrine')}</span>`
      : `<span class="btn sekundaer" style="font-size: var(--t-s);padding:4px 10px">${t('cr_freigeben')}</span>`);
    const titel = a.titel || (t('seite') + ' ' + (a.seite + 1));
    const unter = [t('seite') + ' ' + (a.seite + 1), a.pokemon.map(pn).filter(Boolean).join(', ') || null, a.oeffentlich ? null : t('cr_privat')].filter(Boolean).join(' · ');
    return `<div class="cr-seite" onclick="creatorSeiteOeffnen('${a.id}', ${a.oeffentlich ? 'true' : 'false'}, ${JSON.stringify(a.titel || '').replace(/"/g, '&quot;')})"><img loading="lazy" src="${a.vorschau}" alt="">
      <div><div class="t">${esc(titel)}</div><div class="u">${esc(unter)}</div></div><div class="k">${k.join('')}</div></div>`;
  }).join('');
}
function creatorSeiteOeffnen(id, oeffentlich, titel) {
  // Öffentliche Seite: in der Kunstseiten-Vitrine groß zeigen. Private: Freigabe anbieten.
  if (!oeffentlich) return creatorFreigeben(id, titel);
  profilSchliessen();
  ansicht('vitrine');
  vitrineBereich('kunst');
  let n = 0;
  const warte = setInterval(() => {
    n += 1;
    if ((VT.kunst || []).some((a) => a.id === id)) { clearInterval(warte); kunstGross(id); }
    else if (n > 20) clearInterval(warte);
  }, 150);
}
function creatorFreigeben(id, titel) {
  awTeilenFragen({ id, titel: titel || '' });
}
async function datenExport() {
  // Auskunft nach Art. 15/20 DSGVO als JSON-Datei
  try {
    const r = await fetch('api/auth/export', { headers: { Authorization: 'Bearer ' + S.token } });
    if (!r.ok) return toast(t('aw_fehler'));
    const blob = await r.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = 'binderplan-daten.json';
    document.body.appendChild(a); a.click(); a.remove();
  } catch (e) { toast(t('aw_fehler')); }
}
async function journalLaden() {
  const box = $('pf-journal'); if (!box) return;
  try {
    const d = await api('api/credits');
    S.user = { ...S.user, ...d.konto };
    box.innerHTML = d.buchungen.length ? d.buchungen.map((b) => `<div>
        <span>${(T[LANG] && T[LANG]['j_' + b.grund]) || b.grund}<br><span style="color:var(--mut);font-size: var(--t-xs)">${b.titel ? esc(b.titel) + ' · ' : ''}${(b.created_at || '').slice(0, 16).replace('T', ' ')}</span></span>
        <span class="${b.delta > 0 ? 'plus' : 'minus'}">${b.delta > 0 ? '+' : ''}${b.delta}</span></div>`).join('')
      : `<div style="color:var(--mut)">–</div>`;
  } catch (e) { box.innerHTML = ''; }
}
async function aboKuendigen() {
  const u = S.user || {};
  try {
    if (u.abo_kuendigt) {
      await api('api/abo/reaktivieren', { method: 'POST' });
      toast(t('reaktiviert'));
    } else {
      if (!confirm(t('kuendigen_frage'))) return;
      const d = await api('api/abo/kuendigen', { method: 'POST' });
      toast(`${t('gekuendigt')} ${d.bis}`);
    }
    const me = await api('api/auth/me');
    S.user = me.user; profilFuellen(); kontoAnzeigen();
  } catch (e) { toast(e.message || t('aw_fehler')); }
}
async function pwAendern() {
  const alt = $('pf-pw-alt').value, neu = $('pf-pw-neu').value;
  try {
    const d = await api('api/auth/passwort_aendern', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ alt, neu }),
    });
    setToken(d.token);
    $('pf-pw-alt').value = ''; $('pf-pw-neu').value = '';
    toast(t('pf_pw_ok'));
  } catch (e) { toast(e.message); }
}
async function kontoLoeschen() {
  const pw = $('pf-del-pw').value;
  if (!confirm(t('pf_loeschen_frage'))) return;
  try {
    await api('api/auth/konto_loeschen', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ passwort: pw }),
    });
    setToken('');
    localStorage.removeItem('bp_binder');
    alert(t('pf_geloescht'));
    location.href = location.pathname;
  } catch (e) { toast(e.message); }
}
async function profilAbmelden() { await abmelden(); }

function syncBanner() {
  const b = $('sync-banner');
  const sync = S.meta && S.meta.sync;
  if (sync && (sync.running || S.meta.counts.cards === 0)) {
    b.classList.remove('hidden');
    b.textContent = sync.error ? 'Sync-Fehler: ' + sync.error : `${t('sync')} ${sync.step || ''} (${sync.done}/${sync.total || '?'})`;
    setTimeout(async () => { try { S.meta = await api('api/meta'); } catch (e) {} syncBanner(); }, 4000);
  } else b.classList.add('hidden');
}

function vitrineKnopf() {
  const k = $('wb-vitrine-knopf');
  if (k && S.binder) k.textContent = S.binder.sichtbar ? t('vt_zurueckziehen') : t('vt_zeigen');
}
function binderAnzeigen() {
  // Auch ohne Kennung die Adresse geradeziehen: ein frisch angelegter, noch nicht
  // gespeicherter Binder ließ sie auf dem vorherigen stehen — nach einem Neuladen war
  // man wieder im alten.
  hashSetzen(S.binder.id ? 'binder/' + S.binder.id : '');
  vitrineKnopf();
  $('wb-name').value = S.binder.name;
  $('wb-titel').textContent = S.binder.name || t('neuer_binder');
  S.seite = 0; S.preise = {};
  zeichneLayouts();
  wantsSchalterSetzen();
  zeichneBinder();
  werkbankZeigen();
  // Wer in „Alle Seiten" den Binder wechselt, will dort den neuen sehen.
  S.auswahl.clear();
  if (S.preiseAn && typeof preiseLaden === 'function') preiseLaden();
}

/** Die Werkbank in einen brauchbaren Zustand bringen — egal, woher man kommt.
 *  Der Binder rechts war nach manchen Wegen erst nach einem Neuladen wieder da, und die
 *  Galerie hielt die Trefferliste versteckt, nachdem man zwischendurch woanders war. */
function werkbankZeigen() {
  const sp = document.querySelector('.spalte-binder');
  const mitte = document.querySelector('.spalte-mitte');
  if (!sp || S.nurAnsicht) return;
  sp.classList.remove('hidden');
  breiteAnpassen();
  // Wenn der Binder Fächer hat, das Raster aber leer ist, noch einmal zeichnen.
  const box = S.alleSeiten ? $('wb-alle') : $('wb-slots');
  if (S.binder && S.binder.items && S.binder.items.length && box && box.children.length === 0) zeichneBinder();
}

