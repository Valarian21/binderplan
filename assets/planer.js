// Binderplan – Planer (Fächer, Auswahl, Seiten), Speichern & PDF, Ereignisse, Rückgängig.
// Aus index.html herausgelöst (Phase 5, 10.09.2026); die Dateien laden per defer in dieser Reihenfolge:
// kern → konto → werkbank → vitrine → preise → planer → detail → artwork → markt → sammlung.
// ---------- Planer ----------
function ansicht(w) {
  profilseiteSchliessen();
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  if (w !== 'profil' && !$('profilseite').classList.contains('hidden')) profilSchliessen();   // Reiter über der Profilseite
  if (w === 'start') { startOeffnen(); return; }
  if (w === 'profil') { profilOeffnen(); return; }
  if (typeof startSchliessen === 'function' && !$('startseite').classList.contains('hidden')) startSchliessen();
  if (w !== 'vitrine') vitrineSchliessen();
  if (w !== 'sammlung' && w !== 'auswertung') sammlungSchliessen();
  if (w !== 'markt') marktSchliessen();
  if (w !== 'auswertung') auswertungSchliessen();
  if (w === 'sammlung') { ansichtSammlung(); return; }
  if (w === 'vitrine') { ansichtVitrine(); return; }
  if (w === 'markt') { marktOeffnen(); return; }
  if (w === 'auswertung') { auswertungOeffnen(); return; }
  // „suche" und „planer" sind derselbe Ort in zwei Zuständen. Beide Adressen bleiben
  // gültig, damit geteilte Links und der Zurück-Knopf weiter funktionieren.
  if (w === 'suche' || w === 'planer') {
    // Beide Adressen zeigen denselben Ort. „planer" schaltet auf die Übersicht, „suche"
    // lässt den zuletzt gewählten Zustand stehen — er gehört dem Nutzer, nicht der Adresse.
    if (w === 'planer' && !S.alleSeiten) binderAnsicht(true);
    else { breiteAnpassen(); zeichneBinder(); }
    werkbankZeigen();
    reiterSetzen('seg-suche');
    mnavMarkieren('mnav-binder');
    if (window.innerWidth < 901) document.body.classList.add('binder-an');
  }
}
/* Die beiden Namen bleiben, weil sie an vielen Stellen gerufen werden — sie schalten jetzt
   nur noch den Zustand um, statt einen eigenen Ort zu öffnen und zu schließen. */
function planerOeffnen() { binderAnsicht(true); }
function planerSchliessen() { /* kein eigener Ort mehr */ }
function zeichnePlaner() { if (S.alleSeiten) zeichneAlleSeiten(); }


/* ==========================================================================
   Auswertungen: eigene Sammlung und Markt

   Die Diagramme sind von Hand gezeichnetes SVG. Die App hat keinen Build-Schritt,
   und zwei Kurventypen rechtfertigen keine nachgeladene Bibliothek — dafür passen
   sie sich den Farbtokens an und funktionieren hell wie dunkel ohne Sonderfall.
   ========================================================================== */

const AN = { daten: null, markt: null, gruppe: 'nach_seltenheit', kurven: {}, nr: 0 };

const anEur = (n, stellen) => (n || 0).toLocaleString(LANG === 'en' ? 'en' : 'de',
  { minimumFractionDigits: stellen == null ? 2 : stellen, maximumFractionDigits: stellen == null ? 2 : stellen }) + ' €';
const anZahl = (n) => (n || 0).toLocaleString(LANG === 'en' ? 'en' : 'de');
const anProz = (n) => (n > 0 ? '+' : '') + (n || 0).toLocaleString(LANG === 'en' ? 'en' : 'de',
  { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + ' %';
/** Datum kurz: „2. Sep." bzw. „Sep 2". */
function anTag(iso) {
  const d = new Date(iso + 'T00:00:00Z');
  if (isNaN(d)) return iso;
  return d.toLocaleDateString(LANG === 'en' ? 'en' : 'de', { day: 'numeric', month: 'short', timeZone: 'UTC' });
}

/** Eine Kennzahl-Kachel. `art` färbt die Zahl grün oder rot. */
function anKachel(lbl, zahl, neben, art) {
  return `<div class="an-kachel"><div class="lbl">${esc(lbl)}</div>`
    + `<div class="zahl ${art || ''}">${zahl}</div>`
    + (neben ? `<div class="neben">${neben}</div>` : '') + '</div>';
}

/** Hinweis, solange die Messreihe zu kurz ist. Lieber ein ehrlicher Satz als eine
 *  Kurve, die aus drei Punkten eine Marktaussage macht. */
function anDuenn(text) { return `<div class="an-duenn">${text}</div>`; }

/** Waagerechte Balken: Beschriftung, Balken, Zahl. `items` = [{name, wert, anzahl}]. */
/** Die Auswertung liefert zwei Platzhalter, die erst hier eine Sprache bekommen:
 *  „?" für fehlende Angaben und „übrige" für den zusammengefassten Rest. */
function anGruppenname(n) {
  if (n === '?') return t('an_ohne_angabe');
  if (n === 'übrige') return t('an_uebrige');
  return n;
}

function anBalken(items, opt) {
  opt = opt || {};
  if (!items || !items.length) return anDuenn(t('an_keine_daten'));
  const max = Math.max(...items.map((i) => Math.abs(i.wert))) || 1;
  const farbe = opt.farbe || 'var(--d2)';
  return '<div class="an-balken">' + items.map((i) => {
    const b = Math.max(2, Math.round(Math.abs(i.wert) / max * 100));
    const zusatz = i.anzahl != null ? ` · ${anZahl(i.anzahl)} ${t('an_stk')}` : '';
    const nm = anGruppenname(i.name) + (i.rest ? ` (${i.rest})` : '');
    return `<div class="nm" title="${esc(nm)}${zusatz}">${esc(nm)}</div>`
      + `<div class="spur"><div class="bar" style="width:${b}%;background:${farbe}"></div></div>`
      + `<div class="zl">${opt.roh ? anZahl(i.wert) : anEur(i.wert, 0)}</div>`;
  }).join('') + '</div>';
}

/** Liniendiagramm. `reihen` = [{name, farbe, punkte:[{datum, wert}]}].
 *  Beschriftet jede Kurve am Ende direkt — so trägt nicht die Farbe allein die Identität. */
function anLinie(reihen, opt) {
  opt = opt || {};
  reihen = (reihen || []).filter((r) => r.punkte && r.punkte.length >= 2);
  if (!reihen.length) return anDuenn(opt.leer || t('an_reihe_kurz'));
  // Der viewBox wird auf die tatsächliche Breite gelegt, damit eine SVG-Einheit einem
  // Bildschirmpunkt entspricht. Sonst schrumpft die Beschriftung am Handy auf ein Drittel.
  const eng = window.innerWidth < 700;
  const W = eng ? 340 : 760, H = opt.hoehe || (eng ? 190 : 210);
  const L = eng ? 34 : 52, R = eng ? 12 : 96, O = 12, U = eng ? 22 : 26;
  const sg = eng ? 9 : 10.5;
  const alle = reihen.flatMap((r) => r.punkte.map((p) => p.wert));
  let min = Math.min(...alle), max = Math.max(...alle);
  if (min === max) { min -= 1; max += 1; }
  const alleNichtNegativ = alle.every((v) => v >= 0);
  const luft = (max - min) * 0.12; min -= luft; max += luft;
  if (alleNichtNegativ && min < 0) min = 0;                        // keine erfundenen Minuswerte
  if (!opt.index && min > 0 && min < (max - min) * 0.6) min = 0;   // Nulllinie, wo sie ehrlich ist
  const tage = reihen[0].punkte.map((p) => p.datum);
  const x = (i) => L + (tage.length < 2 ? 0 : i / (tage.length - 1) * (W - L - R));
  const y = (v) => O + (1 - (v - min) / (max - min)) * (H - O - U);
  const fmt = opt.fmt || ((v) => anEur(v, 0));

  let g = '';
  for (let k = 0; k <= 3; k++) {
    const v = min + (max - min) * k / 3, yy = y(v);
    g += `<line x1="${L}" y1="${yy.toFixed(1)}" x2="${W - R}" y2="${yy.toFixed(1)}" stroke="var(--d-gitter)" stroke-width="1"/>`
      + `<text x="${L - 6}" y="${(yy + 3.5).toFixed(1)}" text-anchor="end" font-size="${sg}" fill="var(--mut)">${fmt(v)}</text>`;
  }
  const marken = tage.length <= 3 ? tage.map((_, i) => i) : [0, Math.floor((tage.length - 1) / 2), tage.length - 1];
  marken.forEach((i) => {
    g += `<text x="${x(i).toFixed(1)}" y="${H - 7}" text-anchor="${i === 0 ? 'start' : i === tage.length - 1 ? 'end' : 'middle'}" font-size="${sg}" fill="var(--mut)">${esc(anTag(tage[i]))}</text>`;
  });

  reihen.forEach((r) => {
    const d = r.punkte.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(p.wert).toFixed(1)}`).join(' ');
    const le = r.punkte.length - 1;
    g += `<path d="${d}" fill="none" stroke="${r.farbe}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`
      + `<circle cx="${x(le).toFixed(1)}" cy="${y(r.punkte[le].wert).toFixed(1)}" r="3.5" fill="${r.farbe}" stroke="var(--card)" stroke-width="2"/>`
      + (eng ? '' : `<text x="${W - R + 8}" y="${(y(r.punkte[le].wert) + 3.5).toFixed(1)}" font-size="11" font-weight="700" fill="${r.farbe}">${esc(r.name)}</text>`);
  });

  const legende = (eng || opt.legende) && reihen.length
    ? `<div class="an-legende">${reihen.map((r) => `<span><i style="background:${r.farbe}"></i>${esc(r.name)}</span>`).join('')}</div>`
    : '';
  const id = 'anl' + (++AN.nr);
  AN.kurven[id] = { reihen: reihen, tage: tage, L: L, R: R, W: W, fmt: fmt };
  return `${opt.legende_oben === false ? '' : legende}<div style="position:relative">
    <svg class="an-svg" id="${id}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opt.titel || '')}"
         onmousemove="anZeiger(event,'${id}')" onmouseleave="anZeigerWeg('${id}')">
      ${g}<line class="an-zeiger hidden" y1="${O}" y2="${H - U}" stroke="var(--mut)" stroke-width="1" stroke-dasharray="3 3"/>
    </svg>
    <div class="an-tip hidden" id="${id}-tip"></div></div>`;
}

/** Fadenkreuz und Werte am Mauszeiger. */
function anZeiger(ev, id) {
  const c = AN.kurven[id], svg = $(id); if (!c || !svg) return;
  const b = svg.getBoundingClientRect(), sk = c.W / b.width;
  const px = (ev.clientX - b.left) * sk;
  const spanne = c.W - c.L - c.R;
  let i = Math.round((px - c.L) / (spanne || 1) * (c.tage.length - 1));
  i = Math.max(0, Math.min(c.tage.length - 1, i));
  const xx = c.L + (c.tage.length < 2 ? 0 : i / (c.tage.length - 1) * spanne);
  const linie = svg.querySelector('.an-zeiger');
  linie.setAttribute('x1', xx); linie.setAttribute('x2', xx); linie.classList.remove('hidden');
  const tip = $(id + '-tip');
  tip.innerHTML = `<strong>${esc(anTag(c.tage[i]))}</strong>`
    + c.reihen.map((r) => r.punkte[i] ? `<span><i style="background:${r.farbe}"></i>${esc(r.name)} ${c.fmt(r.punkte[i].wert)}</span>` : '').join('');
  tip.classList.remove('hidden');
  const links = Math.min(Math.max(0, xx / sk - 60), b.width - 130);
  tip.style.left = links + 'px';
}
function anZeigerWeg(id) {
  const svg = $(id); if (!svg) return;
  svg.querySelector('.an-zeiger').classList.add('hidden');
  $(id + '-tip').classList.add('hidden');
}

/** Die Pro-Schranke — was es gibt und warum es etwas kostet. */
/** Die Sperrseite zeigt jetzt, was dahinter liegt: eine echte Kachelreihe und eine
 *  Kurve, weichgezeichnet, mit genau einer scharfen Zahl. Vorher standen dort vier
 *  Stichpunkte und darunter 500 px Weiß — wer nie eine Zahl gesehen hat, kauft keine. */
function anSperre(punkte, titel, stufe, probe) {
  const kurve = [12, 18, 15, 24, 30, 27, 38, 44, 41, 52];
  const pts = kurve.map((y, i) => `${i / (kurve.length - 1) * 100},${60 - y}`).join(' ');
  return `<div class="an-vorschau" aria-hidden="true">
      <div class="an-band">
        ${(probe || [[t('an_wert'), '•••'], [t('an_einsatz'), '•••'], [t('an_gewinn'), '•••']])
          .map(([l, v]) => `<div class="an-kachel"><div class="lbl">${esc(l)}</div><div class="zahl">${esc(v)}</div></div>`).join('')}
      </div>
      <svg viewBox="0 0 100 62" preserveAspectRatio="none" class="an-kurve">
        <polyline points="${pts}" fill="none" stroke="var(--gruen)" stroke-width="2.5"/></svg>
    </div>
    <div class="an-sperre">
    <span class="an-marke">${stufe || 'Pro'}</span>
    <h3 style="margin-top:12px">${titel || t('an_pro_t')}</h3>
    <p>${t('an_pro_u')}</p>
    <ul>${punkte.map((p) => `<li>${p}</li>`).join('')}</ul>
    <button class="btn" onclick="upgradeOeffnen('')">${t('upgrade')}</button></div>`;
}

/* -------------------------------------------------- Auswertung der Sammlung */
// Seit September 2026 ein Reiter der Sammlung: siehe assets/sammlung.js.

/** Lange Listen werden gekürzt gezeigt. Vollständig war die Marktseite am Handy über
 *  8.000 Punkte hoch — niemand scrollt so weit, und der Rest steht einen Klick entfernt. */
function anMehr(id, zahl) {
  const el = $(id); if (!el) return;
  el.querySelectorAll('.an-rest').forEach((r) => r.classList.remove('an-rest'));
  const k = $(id + '-mehr'); if (k) k.remove();
}
function anKuerzen(liste, n) {
  return { sicht: liste.slice(0, n), rest: Math.max(0, liste.length - n) };
}
function anMehrKnopf(id, rest) {
  return rest ? `<button class="btn sekundaer" id="${id}-mehr" style="font-size: var(--t-s);padding:7px 14px;margin-top:12px"
    onclick="anMehr('${id}')">${t('an_mehr').replace('{n}', anZahl(rest))}</button>` : '';
}

/* ------------------------------------------------------------------ Markt */

function marktFensterZeichnen() {
  const s = $('mkf-7'), l = $('mkf-30');
  if (!s || !l) return;
  s.classList.toggle('on', MKT.fenster === 7);
  l.classList.toggle('on', MKT.fenster === 30);
}
function marktOeffnen() {
  if (!S.user) return gastOrt('marktseite', marktSchliessen, 'seg-markt', 'mnav-markt', t('gast_markt_t'), t('gast_markt_u'), t('an_markt_login'));
  gastKarteWeg('marktseite');
  hashSetzen('markt');
  planerSchliessen(); vitrineSchliessen(); sammlungSchliessen(); auswertungSchliessen(); profilseiteSchliessen();
  $('marktseite').classList.remove('hidden');
  ebeneOeffnen(marktSchliessen);
  reiterSetzen('seg-markt');
  mnavMarkieren('mnav-markt');
  const marke = $('mk-marke');
  if (marke) marke.classList.toggle('hidden', !!(S.user && S.user.plan && S.user.plan !== 'free'));
  marktFensterZeichnen();
  marktLaden();
}
/** Ein Ort für Gäste: die Fläche bleibt, darüber liegt ein Kasten mit dem Angebot und den
 *  beiden Wegen ins Konto. Vorher sprang ein Anmelde-Dialog auf, und der Reiter wirkte kaputt. */
function gastOrt(id, schliesser, reiter, mnav, titel, text, grund) {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  vitrineSchliessen(); if (typeof sammlungSchliessen === 'function') sammlungSchliessen(); profilseiteSchliessen();
  if (typeof marktSchliessen === 'function' && id !== 'marktseite') marktSchliessen();
  if (!$('startseite').classList.contains('hidden')) startSchliessen();
  const ort = $(id);
  const war = !ort.classList.contains('hidden');
  ort.classList.remove('hidden');
  if (!war) ebeneOeffnen(schliesser);
  reiterSetzen(reiter); mnavMarkieren(mnav);
  let karte = ort.querySelector('.gast-karte');
  if (!karte) { karte = document.createElement('div'); karte.className = 'gast-karte'; ort.appendChild(karte); }
  karte.innerHTML = `<div class="gast-box"><strong>${esc(titel)}</strong><span>${esc(text)}</span>
    <div class="gast-knoepfe"><button class="btn" onclick="loginOeffnen(${JSON.stringify(grund).replace(/"/g, '&quot;')})">${t('gast_registrieren')}</button>
    <button class="btn sekundaer" onclick="loginOeffnen('');authTab('login')">${t('gast_anmelden')}</button></div></div>`;
}
function gastKarteWeg(id) { const k = $(id) && $(id).querySelector('.gast-karte'); if (k) k.remove(); }

function marktSchliessen() {
  if (!$('marktseite') || $('marktseite').classList.contains('hidden')) return;
  ebeneAufraeumen(marktSchliessen);
  $('marktseite').classList.add('hidden');
  routeZurueck();
  if ($('startseite').classList.contains('hidden')) reiterSetzen('seg-suche');
}






/** Bereich „Regionen": zwei Preisgefälle nebeneinander. */
function zeichneMarktRegionen(d) {
  const vg = d.vergleich || {};
  const vgTafel = vg.kurs
    ? `<div class="an-zwei">
        <div><div class="unter" style="margin-bottom:8px;font-weight:700">${t('an_hier_guenstiger')}</div>${anVgTabelle(vg.guenstiger_eu, 'mk-eu')}</div>
        <div><div class="unter" style="margin-bottom:8px;font-weight:700">${t('an_dort_guenstiger')}</div>${anVgTabelle(vg.guenstiger_us, 'mk-us')}</div>
       </div>
       <div class="unter" style="margin:12px 0 0;font-size: var(--t-s)">${t('an_kurs_hin')
          .replace('{k}', vg.kurs.toFixed(3)).replace('{n}', anZahl(vg.geprueft || vg.paare))}
          ${vg.verworfen ? ' ' + t('an_verworfen').replace('{n}', anZahl(vg.verworfen)) : ''}</div>`
    : anDuenn(t('an_vg_kurz'));
  const jp = d.jp || {}, west = d.west || {};
  $('mk-rumpf').innerHTML = `<h1>${t('mk_regionen')}</h1>
    <div class="unter">${t('mk_regionen_u')}</div>

    <div class="an-tafel"><h3>${t('mk_jp')}</h3>
      <div class="unter">${t('mk_jp_u')}</div>
      <div class="an-band" style="margin-bottom:16px">
        ${anKachel(t('mk_jp_karten'), anZahl(jp.n || 0), t('mk_jp_schnitt').replace('{p}', anEur(jp.schnitt || 0)))}
        ${anKachel(t('mk_west_karten'), anZahl(west.n || 0), t('mk_jp_schnitt').replace('{p}', anEur(west.schnitt || 0)))}
        ${anKachel(t('mk_faktor'), (west.schnitt && jp.schnitt ? (west.schnitt / jp.schnitt).toFixed(1) : '—') + '×', t('mk_faktor_u'))}
      </div>
      <div class="unter" style="margin-bottom:8px;font-weight:700">${t('mk_jp_top')}</div>
      <div class="an-karten">${(d.jp_top || []).map((k) => `<button class="an-karte" onclick="detailOeffnen('${esc(k.id)}')">
        <img loading="lazy" src="${imgUrl(k.id)}" alt="" data-n="${esc(k.name || k.id)}"
             onerror="this.outerHTML='<div class=kein-scan>'+this.dataset.n+'</div>'">
        <div class="p">${anEur(k.eur, 0)}</div><div class="n" title="${esc(k.name || '')}">${esc(k.name || '')}</div></button>`).join('')}</div>
    </div>

    <div class="an-tafel"><h3>${t('an_eu_us')}</h3>
      <div class="unter">${t('an_eu_us_u')}</div>${vgTafel}</div>`;
}



function anVgTabelle(liste, id) {
  if (!liste || !liste.length) return anDuenn(t('an_keine_daten'));
  const grenze = window.innerWidth < 700 ? 5 : 10;
  return `<div id="${id}"><div class="an-tab-rahmen"><table class="an-tab" style="min-width:0">
    <thead><tr><th></th><th>${t('an_karte')}</th><th class="r">${t('an_beide_boersen')}</th><th class="r">${t('an_abstand')}</th></tr></thead>
    <tbody>${liste.map((k, i) => `<tr class="${i >= grenze ? 'an-rest' : ''}">
      <td><img loading="lazy" src="${imgUrl(k.id)}" alt="" onerror="this.style.visibility='hidden'"></td>
      <td><div class="nm">${esc(k.name)}</div><div class="set">${esc(k.set || '')}${k.nr ? ' · ' + esc(k.nr) : ''}</div></td>
      <td class="r">${anEur(k.eur)}<div class="set">$ ${k.usd.toFixed(2)}</div></td>
      <td class="r"><span class="${k.abstand <= 0 ? 'an-plus' : 'an-minus'}">${anProz(k.abstand)}</span>
        ${k.differenz != null ? `<div class="set">${(k.differenz > 0 ? '+' : '') + anEur(k.differenz, 0)}</div>` : ''}</td>
    </tr>`).join('')}</tbody></table></div>${anMehrKnopf(id, Math.max(0, liste.length - grenze))}</div>`;
}


/** Verteilung der Karten im Binder: nach Jahrzehnt, Seltenheit und Set. Zahlen, die man
 *  sonst nirgends sieht, und der schnellste Weg zu „was sammle ich hier eigentlich“. */
async function statistikOeffnen() {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  if (!S.binder || !S.binder.items.length) return toast(t('binder_leer'));
  modalOeffnen('modal-statistik');
  $('stat-inhalt').innerHTML = `<div style="padding:30px;text-align:center">${lader('gross', t('laedt'))}</div>`;
  const ids = [...new Set(S.binder.items.filter((i) => i.type === 'card' && i.id).map((i) => i.id))];
  let karten = [];
  try {
    const d = await api('api/cards/nach_ids', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids }),
    });
    karten = Object.values(d.karten || {});
  } catch (e) { $('stat-inhalt').textContent = t('fehler_laden'); return; }

  const zaehl = (fn) => {
    const m = new Map();
    for (const k of karten) {
      const v = fn(k);
      if (!v) continue;
      m.set(v, (m.get(v) || 0) + 1);
    }
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  };
  const jahre = zaehl((k) => (k.datum || '').slice(0, 4)).sort((a, b) => a[0].localeCompare(b[0]));
  const setz = zaehl((k) => setNm(k)).slice(0, 8);
  const selten = zaehl((k) => k.rarity).slice(0, 8);
  const illus = zaehl((k) => k.illustrator).slice(0, 5);

  // Ein Balken ist ein Filter: der Illustrator-Balken öffnet die Suche mit genau diesem Künstler.
  const balken = (liste, gesamt, klick) => liste.map(([name, n]) => `
    <div class="stat-zeile ${klick ? 'klickbar' : ''}" ${klick ? `onclick="${klick(name)}" role="button" tabindex="0"` : ''}>
      <span class="stat-name">${esc(name)}</span>
      <span class="stat-bar"><i style="width:${Math.max(3, n / gesamt * 100).toFixed(1)}%"></i></span>
      <span class="stat-n">${n}</span>
    </div>`).join('');
  const groesste = Math.max(1, ...karten.length ? [Math.max(...jahre.map((j) => j[1]), 1)] : [1]);

  $('stat-inhalt').innerHTML = `
    <div class="stat-kopf">
      <div class="po-zahl"><b>${karten.length}</b><span>${t('po_karten')}</span></div>
      <div class="po-zahl"><b>${new Set(karten.map((k) => k.set_id)).size}</b><span>${t('st_sets')}</span></div>
      <div class="po-zahl"><b>${jahre.length ? jahre[0][0] + '–' + jahre[jahre.length - 1][0] : '–'}</b><span>${t('st_jahre')}</span></div>
    </div>
    ${jahre.length > 1 ? `<h3 class="stat-h">${t('st_jahrgang')}</h3>${balken(jahre, groesste)}` : ''}
    ${setz.length ? `<h3 class="stat-h">${t('st_topsets')}</h3>${balken(setz, setz[0][1])}` : ''}
    ${selten.length ? `<h3 class="stat-h">${t('st_selten')}</h3>${balken(selten, selten[0][1])}` : ''}
    ${illus.length ? `<h3 class="stat-h">${t('st_illu')}</h3>${balken(illus, illus[0][1], (n) => `modalSchliessen();illuSetzen(${JSON.stringify(n).replace(/"/g, '&quot;')});sucheLadeOeffnen()`)}` : ''}`;
}

function planerKlick(ev, idx) { fachKlick(ev, idx); }

/** Nur die Markierung umschalten. Ein Pokédex-Binder hat bis zu 1.000 Fächer — den ganzen
 *  Baum für einen Klick neu zu bauen, ließ jede Auswahl ruckeln. */
function auswahlZeigen() {
  document.querySelectorAll('.spalte-binder .slot[data-idx]').forEach((el) => {
    el.classList.toggle('gewaehlt', S.auswahl.has(Number(el.dataset.idx)));
  });
  auswahlAktionen();
}
/** Tastatur im Binder. Wer 500 Karten sortiert, will nicht für jeden Schritt zur Maus greifen.
 *  Entf entfernt die Auswahl, Pfeile verschieben sie, Strg+A wählt alles, Escape hebt auf.
 *  Gilt seit der Zusammenlegung in beiden Zuständen — auch beim Blättern durch eine Seite. */
document.addEventListener('keydown', (ev) => {
  if (S.nurAnsicht || !S.binder || $('sammlung') && !$('sammlung').classList.contains('hidden')) return;
  if (!$('vitrine').classList.contains('hidden') || !$('startseite').classList.contains('hidden')) return;
  if (!$('marktseite').classList.contains('hidden')) return;
  const feld = ev.target && /^(INPUT|TEXTAREA|SELECT)$/.test(ev.target.tagName);
  if (feld) return;
  if (ev.key === 'Escape' && S.auswahl.size) { ev.preventDefault(); auswahlLeeren(); return; }
  if (ev.key === 'Escape' && document.body.classList.contains('suche-offen') && !document.querySelector('.overlay:not(.hidden)')) { ev.preventDefault(); sucheLadeZu(); return; }
  if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'a') {
    ev.preventDefault();
    S.binder.items.forEach((_, i) => S.auswahl.add(i));
    auswahlZeigen();
    return;
  }
  // Strg+Z / Strg+Y (und Strg+Umschalt+Z, wie im Mac üblich)
  if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'z' && !ev.shiftKey) { ev.preventDefault(); rueckgaengig(); return; }
  if ((ev.ctrlKey || ev.metaKey) && (ev.key.toLowerCase() === 'y' || (ev.key.toLowerCase() === 'z' && ev.shiftKey))) { ev.preventDefault(); wiederholen(); return; }
  if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'c' && S.auswahl.size) { ev.preventDefault(); auswahlKopieren(); return; }
  if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'x' && S.auswahl.size) { ev.preventDefault(); auswahlKopieren(true); return; }
  if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'v') { ev.preventDefault(); ablageEinfuegen(); return; }
  if (!S.auswahl.size) return;
  if (ev.key === 'Delete' || ev.key === 'Backspace') { ev.preventDefault(); auswahlEntfernen(); return; }
  if (ev.key === 'ArrowLeft' || ev.key === 'ArrowUp') { ev.preventDefault(); auswahlVerschieben(-1); return; }
  if (ev.key === 'ArrowRight' || ev.key === 'ArrowDown') { ev.preventDefault(); auswahlVerschieben(1); return; }
});

/* ==========================================================================
   Zwischenablage („Ablage")

   Zwei Aufgaben in einem Behälter, weil es für den Nutzer eine Sache ist:
   Karten aus dem Binder kopieren (Strg+C) und Karten aus der Suche sammeln,
   bevor sie platziert werden. Beides endet in `S.ablage`, beides wird mit
   demselben Knopf ausgeleert.

   Die Ablage liegt bewusst **nicht** in der System-Zwischenablage: dort ließe
   sich ein Kartenobjekt nur als Text ablegen, und beim Einfügen müsste man
   raten, ob der Text von uns stammt. Innerhalb der App ist ein Array die
   ehrlichere Lösung — sie überlebt auch den Binderwechsel, was der eigentliche
   Zweck ist („diese neun Karten in den anderen Binder").
   ========================================================================== */

/** Auswahl in die Ablage legen; `ausschneiden` entfernt sie danach aus dem Binder. */
function auswahlKopieren(ausschneiden) {
  if (!S.auswahl.size) return toast(t('erst_waehlen'));
  const idx = [...S.auswahl].sort((a, b) => a - b);
  S.ablage = idx.map((i) => S.binder.items[i]).filter(Boolean).map((it) => JSON.parse(JSON.stringify(it)));
  if (ausschneiden) auswahlHerausnehmen();
  else toast(t('ab_kopiert').replace('{n}', S.ablage.length));
  ablageZeigen();
}

/** Alles aus der Ablage in den Binder legen — ab dem ersten gewählten Fach, sonst ans Ende. */
function ablageEinfuegen() {
  if (!S.ablage || !S.ablage.length) return toast(t('ab_leer'));
  if (S.nurAnsicht) return;
  merken('ablage');
  const kopien = S.ablage.map((it) => JSON.parse(JSON.stringify(it)));
  const ziel = S.auswahl.size ? Math.min(...S.auswahl) : S.binder.items.length;
  S.binder.items.splice(ziel, 0, ...kopien);
  S.auswahl.clear();
  speichern(); zeichneBinder(); zeichneErgebnisse();
  toastUndo(t('ab_eingefuegt').replace('{n}', kopien.length));
}

/** Ablage leeren. */
function ablageLeeren() { S.ablage = []; ablageZeigen(); }

/** Die Leiste unten: wie viele Karten warten, und was man mit ihnen tun kann. */
function ablageZeigen() {
  const box = $('wb-ablage');
  if (!box) return;
  const n = (S.ablage || []).length;
  box.classList.toggle('hidden', !n || !!S.nurAnsicht);
  if (!n) return;
  $('wb-ablage-zahl').textContent = t('ab_wartet').replace('{n}', n);
  const bilder = $('wb-ablage-bilder');
  if (bilder) {
    bilder.innerHTML = S.ablage.slice(0, 8).map((it) => {
      const b = it && it.type === 'card' && it.id ? imgUrl(it.id) : '';
      return b ? `<img src="${b}" alt="" loading="lazy">` : '<span class="ab-leer">◻</span>';
    }).join('') + (n > 8 ? `<span class="ab-mehr">+${n - 8}</span>` : '');
  }
}

function seiteWaehlen(seite) {
  const sp = seiteInfo(seite);
  for (let s = 0; s < sp.laenge; s++) {
    const idx = sp.start + s;
    if (S.binder.items[idx]) S.auswahl.add(idx);
  }
  auswahlZeigen();
}
function auswahlLeeren() { S.auswahl.clear(); auswahlZeigen(); }

function auswahlVerschieben(wohin) {
  if (!S.auswahl.size) return toast(t('erst_waehlen'));
  if (typeof wohin === 'number') return auswahlRuecken(wohin);
  merken('verschieben');
  const idxe = [...S.auswahl].sort((a, b) => a - b);
  const bewegt = idxe.map((i) => S.binder.items[i]);
  const rest = S.binder.items.filter((_, i) => !S.auswahl.has(i));
  S.binder.items = wohin === 'anfang' ? bewegt.concat(rest) : rest.concat(bewegt);
  S.auswahl.clear();
  speichern(); zeichnePlaner(); zeichneBinder();
}

/** Die Auswahl um `d` Fächer rücken, ohne die Ordnung ringsum zu zerstören: der Block
 *  tauscht mit dem, was auf der Zielseite steht. Vorher schoben die Pfeiltasten die
 *  Auswahl unbemerkt ans Binderende — ein Tastendruck, 300 Karten verschoben. */
function auswahlRuecken(d) {
  const idxe = [...S.auswahl].sort((a, b) => a - b);
  const ziel = idxe.map((i) => i + d);
  if (ziel[0] < 0) return;
  merken('ruecken');
  const laenge = Math.max(S.binder.items.length, ziel[ziel.length - 1] + 1);
  for (let i = S.binder.items.length; i < laenge; i++) S.binder.items[i] = { type: 'empty' };
  const bewegt = idxe.map((i) => S.binder.items[i]);
  // Zuerst die Quellfächer räumen, dann füllen — sonst überschreibt ein Block sich selbst.
  const verdraengt = ziel.map((z) => (idxe.includes(z) ? null : S.binder.items[z]));
  idxe.forEach((i) => { S.binder.items[i] = { type: 'empty' }; });
  ziel.forEach((z, k) => { S.binder.items[z] = bewegt[k]; });
  // Was im Weg stand, wandert auf die frei gewordenen Plätze zurück.
  verdraengt.forEach((v, k) => {
    if (!v || v.type === 'empty') return;
    const frei = idxe.find((i) => S.binder.items[i] && S.binder.items[i].type === 'empty');
    if (frei != null) S.binder.items[frei] = v;
  });
  S.auswahl = new Set(ziel);
  speichern(); zeichnePlaner(); auswahlZeigen();
}

/** Auswahl an ein Zielfach setzen: erst markieren, dann „Hierher" und ein Fach anklicken.
 *  Das ist der Weg für viele Karten auf einmal — Ziehen taugt für eine. */
let zielModus = false;
function zielModusAn() {
  if (!S.auswahl.size) return toast(t('erst_waehlen'));
  zielModus = true;
  document.body.classList.add('ziel-modus');
  toast(t('ziel_hinweis'));
}
function zielModusAus() {
  zielModus = false;
  document.body.classList.remove('ziel-modus');
}
function auswahlZuZiel(idx) {
  const idxe = [...S.auswahl].sort((a, b) => a - b);
  zielModusAus();
  if (idxe.includes(idx)) return;
  auswahlRuecken(idx - idxe[0]);
}
/** Auswahl löschen heißt: die Fächer werden leer, die Ordnung bleibt. */
function auswahlEntfernen() {
  if (!S.auswahl.size) return toast(t('erst_waehlen'));
  merken('auswahl');
  const n = S.auswahl.size;
  for (const i of S.auswahl) if (S.binder.items[i]) S.binder.items[i] = { type: 'empty' };
  S.auswahl.clear();
  speichern(); zeichnePlaner(); zeichneErgebnisse();
  toastUndo(n + ' ' + t('entfernen'));
}

/** Auswahl herausnehmen: alles dahinter rückt auf. Ausdrücklich benannt, weil es
 *  jede Seitenaufteilung dahinter verschiebt. */
function auswahlHerausnehmen() {
  if (!S.auswahl.size) return toast(t('erst_waehlen'));
  merken('auswahl');
  const n = S.auswahl.size;
  S.binder.items = S.binder.items.filter((_, i) => !S.auswahl.has(i));
  S.auswahl.clear();
  speichern(); zeichnePlaner(); zeichneErgebnisse();
  toastUndo(n + ' ' + t('herausnehmen'));
}

/** Alle leeren Fächer entfernen und alles zusammenschieben. Das Gegenstück zum
 *  Freimachen: erst räumen, dann in einem Schritt schließen. */
function lueckenSchliessen() {
  if (!S.binder) return;
  const vorher = S.binder.items.length;
  const rest = S.binder.items.filter((i) => i && i.type !== 'empty');
  if (rest.length === vorher) return toast(t('keine_luecken'));
  merken('luecken');
  S.binder.items = rest;
  S.auswahl.clear();
  speichern(); zeichneBinder(); zeichneErgebnisse();
  toastUndo(t('luecken_zu').replace('{n}', vorher - rest.length));
}
/** Neue leere Seite anhängen und gleich dorthin springen. */
function seiteAnhaengen() {
  if (!S.binder || S.nurAnsicht) return;
  leereSeite();
  S.seite = Math.max(0, seitenAnzahl() - 1);
  zeichneBinder();
  toastUndo(t('neue_seite'));
}

function leereSeite() {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  const pp = seiteInfo(seitenAnzahl()).laenge;
  merken('seite');
  for (let i = 0; i < pp; i++) S.binder.items.push({ type: 'empty' });
  speichern(); zeichneBinder();
}

async function auswahlSortieren(key) {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  if (S.auswahl.size < 2) return toast(t('erst_waehlen'));
  key = key || 'datum';
  const idxe = [...S.auswahl].sort((a, b) => a - b);
  const items = idxe.map((i) => S.binder.items[i]);
  const cardIds = [...new Set(items.filter((i) => i.type === 'card').map((i) => i.id))];
  let daten = {};
  if (cardIds.length) {
    const d = await api('api/cards/nach_ids', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids: cardIds }) });
    daten = d.karten;
  }
  const wert = (item) => {
    if (item.type === 'empty') return ['￿'];
    if (item.type === 'art') return ['￿', '1'];
    if (item.type === 'dex') {
      if (key === 'dex') return [String(item.dex).padStart(5, '0')];
      const p = (S.pokedex || []).find((x) => x.dex === item.dex);
      return [p ? nm(p).toLowerCase() : String(item.dex)];
    }
    const k = daten[item.id] || {};
    if (key === 'datum') return [(k.datum || '9999'), k.set_id || '', String(parseInt(k.local_id) || 99999).padStart(6, '0')];
    if (key === 'dex') return [String(k.dex == null ? 99999 : k.dex).padStart(5, '0'), k.datum || '9999'];
    if (key === 'name') return [(nm(k) || '').toLowerCase()];
    if (key === 'nummer') return [k.set_id || '', String(parseInt(k.local_id) || 99999).padStart(6, '0')];
    if (key === 'typ') return [(k.types && k.types[0]) || 'zz', (nm(k) || '').toLowerCase()];
    if (key === 'rarity') return [k.rarity || 'zz', (nm(k) || '').toLowerCase()];
    return [''];
  };
  const sortiert = items.map((it) => ({ it, w: wert(it) }))
    .sort((a, b) => { for (let i = 0; i < Math.max(a.w.length, b.w.length); i++) { const x = a.w[i] || '', y = b.w[i] || ''; if (x < y) return -1; if (x > y) return 1; } return 0; })
    .map((x) => x.it);
  idxe.forEach((pos, i) => { S.binder.items[pos] = sortiert[i]; });
  speichern(); zeichnePlaner();
}

// ---------- Speichern & PDF ----------
function speichern() {
  if (!S.binder || S.nurAnsicht) return;
  if (!S.binder.id) { if (S.binder.items.length) binderAnlegenWennNoetig(); return; }
  $('wb-status').textContent = t('speichert');
  clearTimeout(S.speicherTimer);
  // Den Binder hier festhalten, nicht erst beim Ausführen: wer innerhalb der 700 ms den Binder
  // wechselt, schrieb sonst die Daten des alten in den neuen — und der alte blieb ungespeichert.
  const binder = S.binder;
  const inhalt = JSON.stringify(binder);
  S.speicherTimer = setTimeout(() => sichereJetzt(binder, inhalt), 700);
}

async function sichereJetzt(binder, inhalt) {
  clearTimeout(S.speicherTimer);
  try {
    const d = await api('api/binders/' + binder.id, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: inhalt });
    if (d && d.updated_at) binder.updated_at = d.updated_at;
    if (S.binder === binder) $('wb-status').textContent = t('gespeichert');
  } catch (e) {
    if (e.status === 409) return konfliktBehandeln(binder);
    if (S.binder === binder) $('wb-status').textContent = t('fehler_speichern');
  }
}

/** 409 beim Speichern: ein anderes Gerät hat diesen Binder inzwischen geändert. Der Stand von
 *  dort wird geladen und gezeigt – die eigene, nicht gespeicherte Änderung geht verloren, aber
 *  bewusst und mit Ansage statt stumm die fremde. */
async function konfliktBehandeln(binder) {
  try {
    const neu = await api('api/binders/' + binder.id);
    if (S.binder === binder) { S.binder = neu; S.auswahl.clear(); zeichneBinder(); if (S.alleSeiten) zeichneAlleSeiten(); $('wb-status').textContent = ''; }
    toast(t('konflikt_neu'));
  } catch (e) { if (S.binder === binder) $('wb-status').textContent = t('fehler_speichern'); }
}

async function _binderSichern() {
  if (S.nurAnsicht) return;
  if (!S.binder.id && !await binderAnlegenWennNoetig()) return;
  clearTimeout(S.speicherTimer);
  S.binder.options = { ...(S.binder.options || {}), sprache: KLANG };
  const binder = S.binder;
  try {
    const d = await api('api/binders/' + binder.id, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(binder) });
    if (d && d.updated_at) binder.updated_at = d.updated_at;
  } catch (e) {
    if (e.status === 409) { await konfliktBehandeln(binder); throw e; }
    throw e;
  }
}

/* ---------- Druckdialog ----------
   Vorher gab es zwei Menüeinträge, die sofort ein PDF des ganzen Binders erzeugten.
   Bei 13 Seiten sind das 7 A4-Blätter, die niemand wollte, wenn nur eine Seite fehlte —
   und ein verbrauchter Export im Monatskontingent. */

const DR = { umfang: 'alle' };

function druckOeffnen() {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  if (!S.binder) return;
  if (!S.binder.items.length) return toast(t('binder_leer'));
  if (!S.user) return loginOeffnen(t('gate_pdf'));
  DR.umfang = 'alle';
  druckArt('karten');
  $('dr-seiten').value = '';
  $('dr-fehlend').checked = false;
  $('dr-nurart').checked = false;
  $('dr-farbe').checked = false;
  druckUmfangZeichnen();
  druckVorschau();
  modalOeffnen('modal-druck');
}

function druckUmfangZeichnen() {
  const seite = (S.seite || 0) + 1;
  $('dr-umfang').innerHTML = [
    ['alle', t('dr_alle')],
    ['aktuell', t('dr_aktuell').replace('{n}', seite)],
    ['frei', t('dr_frei')],
  ].map(([k, l]) => `<button class="chip ${DR.umfang === k ? 'on' : ''}" onclick="druckUmfang('${k}')">${esc(l)}</button>`).join('');
}
function druckUmfang(k) {
  DR.umfang = k;
  if (k === 'aktuell') $('dr-seiten').value = String((S.seite || 0) + 1);
  if (k === 'alle') $('dr-seiten').value = '';
  druckUmfangZeichnen(); druckVorschau();
}
/** Tippt jemand selbst etwas ein, ist das der freie Bereich. */
function druckUmfangFrei() {
  if (DR.umfang !== 'frei') { DR.umfang = 'frei'; druckUmfangZeichnen(); }
  druckVorschau();
}

/** Dieselbe Auswahl wie im Backend, damit die Zahl im Dialog stimmt. */
function druckSeitenMenge(text, gesamt) {
  if (!text || !text.trim()) return null;
  const menge = new Set();
  for (const teil of text.replace(/\s/g, '').split(',')) {
    if (!teil) continue;
    if (teil.includes('-')) {
      const [a, b] = teil.split('-');
      const von = a ? parseInt(a, 10) : 1;
      const bis = b ? parseInt(b, 10) : gesamt;
      if (isNaN(von) || isNaN(bis)) continue;
      for (let n = Math.max(1, von); n <= Math.min(gesamt, bis); n++) menge.add(n);
    } else {
      const n = parseInt(teil, 10);
      if (!isNaN(n) && n >= 1 && n <= gesamt) menge.add(n);
    }
  }
  return menge.size ? menge : null;
}

function druckVorschau() {
  const plan = seitenPlan();
  const gesamt = plan.length;
  const menge = druckSeitenMenge($('dr-seiten').value, gesamt);
  const fehlend = $('dr-fehlend').checked, nurArt = $('dr-nurart').checked;
  let n = 0, art = 0;
  S.binder.items.forEach((it, idx) => {
    if (!it || it.type === 'empty') return;
    if (fehlend && it.have) return;
    if (nurArt && it.type !== 'art') return;
    if (menge && !menge.has(seiteBei(idx) + 1)) return;
    n++; if (it.type === 'art') art++;
  });
  const blaetter = Math.max(1, Math.ceil(n / 9));
  const seitenTxt = menge ? t('dr_v_seiten').replace('{n}', menge.size).replace('{g}', gesamt) : t('dr_v_alle').replace('{g}', gesamt);
  $('dr-vorschau').innerHTML = n
    ? `<div class="bestell-zeile"><span>${seitenTxt}</span><span></span></div>
       <div class="bestell-zeile"><span>${t('dr_v_faecher')}</span><span><strong>${n}</strong>${art ? ` (${art} ${t('dr_v_art')})` : ''}</span></div>
       <div class="bestell-zeile gesamt"><span>${t('dr_v_blaetter')}</span><span>${blaetter}</span></div>`
    : `<div class="bestell-zeile"><span>${t('dr_v_nichts')}</span><span></span></div>`;
  $('dr-vorschau').dataset.n = n;
}

/** Art des Drucks: Platzhalter (mit allen Optionen), Checkliste, Kaufliste als CSV. */
function druckArt(art) {
  DR.art = art;
  ['karten', 'checkliste', 'kaufliste'].forEach((a) => { const b = $('dra-' + a); if (b) b.classList.toggle('on', a === art); });
  $('dr-optionen').classList.toggle('hidden', art !== 'karten');
  const hin = $('dr-art-hinweis');
  hin.classList.toggle('hidden', art === 'karten');
  hin.textContent = art === 'checkliste' ? t('dr_check_u') : art === 'kaufliste' ? t('dr_kauf_u') : '';
  $('dr-start-btn').textContent = art === 'checkliste' ? t('dr_start_check') : art === 'kaufliste' ? t('dr_start_kauf') : t('dr_start');
}

function druckStarten() {
  if (DR.art === 'checkliste') { modalSchliessen(); return exportPdf('checkliste', 0); }
  if (DR.art === 'kaufliste') { modalSchliessen(); return kaufliste(); }
  if (Number($('dr-vorschau').dataset.n || 0) === 0) return toast(t('dr_v_nichts'));
  const seiten = DR.umfang === 'alle' ? '' : $('dr-seiten').value.trim();
  modalSchliessen();
  exportPdf('karten', $('dr-fehlend').checked ? 1 : 0, {
    seiten, farbe: $('dr-farbe').checked ? 1 : 0, nur_art: $('dr-nurart').checked ? 1 : 0,
  });
}

async function exportPdf(variante, nurFehlende, opt) {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  if (!S.binder) return;
  if (!S.binder.items.length) return toast(t('binder_leer'));
  if (!S.user) return loginOeffnen(t('gate_pdf'));
  try { await _binderSichern(); } catch (e) { if (gate(e)) return; }
  opt = opt || {};
  const karten = (variante || 'karten') === 'karten';
  if (karten) {
    // Schritt 1: Bilder parallel in den Druck-Cache (JPEG in Druckauflösung)
    arbeitToast(t('pdf_bilder'));
    try { await api(`api/binders/${S.binder.id}/pdf_vorbereiten?farbe=${opt.farbe || 0}`, { method: 'POST' }); } catch (e) { arbeitToastEnde(); if (gate(e)) return; }
  }
  arbeitToast(t('pdf_erzeugt'));
  // Solange das PDF entsteht, sagt der Hinweis, auf welchem Blatt der Server gerade ist.
  const bid = S.binder.id;
  const stand = karten ? setInterval(async () => {
    try { const st = await api(`api/binders/${bid}/pdf_stand`); if (st && st.gesamt) arbeitToast(t('pdf_seite').replace('{a}', st.seiten).replace('{b}', st.gesamt)); } catch (e) {}
  }, 800) : null;
  const url = `api/binders/${S.binder.id}/pdf?variante=${variante || 'karten'}&nur_fehlende=${nurFehlende ? 1 : 0}`
    + `&seiten=${encodeURIComponent(opt.seiten || '')}&farbe=${opt.farbe || 0}&nur_art=${opt.nur_art || 0}`;
  try {
    const r = await fetch(url, { headers: { Authorization: 'Bearer ' + S.token } });
    clearInterval(stand);
    if (!r.ok) {
      arbeitToastEnde();
      let code = null;
      try { code = ((await r.json()).detail || {}).code; } catch (e) {}
      const err = new Error('Export'); err.code = code;
      if (!gate(err)) toast('Export fehlgeschlagen (' + r.status + ')');
      return;
    }
    const blob = await r.blob();
    const blobUrl = URL.createObjectURL(blob);
    // Download statt window.open: ein Popup nach längerem Warten blockieren viele Browser
    const a = document.createElement('a');
    a.href = blobUrl; a.download = (S.binder.name || 'binder').replace(/[^\wäöüÄÖÜß -]/g, '') + (karten ? '' : '-checkliste') + '.pdf';
    document.body.appendChild(a); a.click(); a.remove();
    arbeitToastEnde();
    const el = $('toast'); $('toast-text').innerHTML = `${t('pdf_geladen')} <a href="${blobUrl}" target="_blank" rel="noopener">${t('pdf_oeffnen')}</a> ${blob.size > 50000 ? ` (${(blob.size / 1e6).toFixed(1).replace('.', LANG === 'de' ? ',' : '.')} MB)` : ''}`;
    $('toast-undo').classList.add('hidden'); el.classList.add('zeig');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove('zeig'), 15000);
    if (S.user && S.user.exporte_limit != null && karten) S.user.exporte_benutzt += 1;
  } catch (e) { clearInterval(stand); arbeitToastEnde(); toast(t('fehler_export')); }
}
// Dauerhafter Arbeits-Hinweis (Spinner) – verschwindet erst mit arbeitToastEnde()
function arbeitToast(text) {
  const el = $('toast'); $('toast-text').textContent = text; $('toast-undo').classList.add('hidden');
  el.classList.add('zeig', 'arbeit'); clearTimeout(toastTimer);
}
function arbeitToastEnde() { const el = $('toast'); el.classList.remove('arbeit', 'zeig'); }

async function kaufliste() {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  if (!S.binder) return;
  if (!S.user) return loginOeffnen(t('gate_pro'));
  try { await _binderSichern(); } catch (e) {}
  try {
    const r = await fetch('api/binders/' + S.binder.id + '/kaufliste?format=csv', { headers: { Authorization: 'Bearer ' + S.token } });
    if (!r.ok) {
      let code = null;
      try { code = ((await r.json()).detail || {}).code; } catch (e) {}
      const err = new Error('Kaufliste'); err.code = code;
      if (!gate(err)) toast(t('fehler_kaufliste'));
      return;
    }
    const a = document.createElement('a');
    a.href = URL.createObjectURL(await r.blob());
    a.download = (S.binder.name || 'binder') + '-kaufliste.csv';
    a.click();
  } catch (e) { toast(t('fehler_kaufliste')); }
}

function teilen() {
  document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
  if (!S.binder || !S.binder.id) return toast(t('binder_leer'));
  // /b/<id> liefert Vorschaubild und Beschreibung an Chats und Netzwerke und schickt
  // Menschen sofort weiter in die Ansicht. Der alte #ansicht-Link funktioniert weiter.
  const url = location.origin + '/b/' + S.binder.id;
  if (navigator.share && matchMedia('(hover: none)').matches) { navigator.share({ title: S.binder.name || 'Binderplan', url }).catch(() => {}); return; }
  (navigator.clipboard ? navigator.clipboard.writeText(url) : Promise.reject())
    .then(() => toast(t('link_kopiert')))
    .catch(() => prompt('Link:', url));
}

// ---------- Ereignisse ----------
/** Am Handy steht das Suchfeld über der Trefferliste, am Rechner in der Filterspalte.
 *  Beide schreiben in dasselbe Filterfeld, damit sie nie auseinanderlaufen. */
function sucheMobilSync(wert) {
  $('f-suche').value = wert;
  ['f-suche-mobil', 'f-suche-lade'].forEach((id) => { const el = $(id); if (el && el.value !== wert) el.value = wert; });
  sucheBegriff(wert);
}
/** Suchbegriff aus einem der drei Felder übernehmen und suchen.
 *
 *  Den Wert in `#f-suche` zu schreiben genügt nicht: gesucht wird nach `filter.q`, und
 *  das setzt nur der `input`-Listener dieses Feldes — den eine Zuweisung aus dem Code
 *  nicht auslöst. Die Handy-Suche schickte deshalb bei jedem Tastendruck den vorigen
 *  Begriff los und zeigte Treffer zu etwas anderem, als im Feld stand. */
function sucheBegriff(wert) {
  clearTimeout(suchTimer);
  suchTimer = setTimeout(() => { filter.q = (wert || '').trim(); sucheNeu(); }, 300);
}

let suchTimer = null;
$('f-suche').addEventListener('input', () => { if ($('f-suche-lade')) $('f-suche-lade').value = $('f-suche').value; clearTimeout(suchTimer); suchTimer = setTimeout(() => { filter.q = $('f-suche').value.trim(); sucheNeu(); }, 350); });
$('f-dex').addEventListener('focus', ladePokedex);
$('f-dex').addEventListener('input', () => {
  const v = $('f-dex').value.trim().toLowerCase();
  if (!v) { if (filter.dex) { filter.dex = 0; sucheNeu(); } return; }
  const p = (S.pokedex || []).find((x) => x.name.toLowerCase() === v || (x.name_en || '').toLowerCase() === v);
  if (p) { filter.dex = p.dex; sucheNeu(); }
});
$('f-serie').addEventListener('change', () => { filter.serie = $('f-serie').value; filter.set = ''; baueSetSelect(); sucheNeu(); });
$('f-set').addEventListener('change', () => { filter.set = $('f-set').value; sucheNeu(); });
$('f-rarity').addEventListener('change', () => { filter.rarity = $('f-rarity').value; mehrFilterZahl(); sucheNeu(); });
$('f-sort').addEventListener('change', () => { filter.sort = $('f-sort').value; richtungZeichnen(); sucheNeu(); });
/** Was liegt oben? Der Knopf sagt es in Worten, je nach Sortierung — „neueste zuerst"
 *  ist verständlicher als „absteigend". */
function richtungText(sort, richtung) {
  const auf = richtung !== 'desc';
  if (sort === 'datum' || sort === 'neu') return auf ? t('ri_alt') : t('ri_neu');
  if (sort === 'name') return auf ? t('ri_az') : t('ri_za');
  if (sort === 'wert') return auf ? t('ri_guenstig') : t('ri_teuer');
  if (sort === 'anzahl') return auf ? t('ri_wenige') : t('ri_viele');
  return auf ? t('ri_auf') : t('ri_ab');
}
function richtungZeichnen() {
  const b = $('f-richtung'); if (!b) return;
  b.innerHTML = `<span class="pf">${filter.richtung === 'desc' ? '↓' : '↑'}</span>${esc(richtungText(filter.sort, filter.richtung))}`;
  b.title = t('ri_t');
}
function richtungWechseln() {
  filter.richtung = filter.richtung === 'desc' ? 'asc' : 'desc';
  richtungZeichnen(); sucheNeu();
}
richtungZeichnen();
$('poke-name').addEventListener('focus', ladePokedex);
$('f-illu').addEventListener('change', () => { filter.illustrator = $('f-illu').value; $('f-illu-suche').value = ''; sucheNeu(); });
$('f-illu-suche').addEventListener('change', () => {
  const v = $('f-illu-suche').value.trim().toLowerCase();
  const i = (S.meta.illustrators || []).find((x) => x.name.toLowerCase() === v) || (v ? (S.meta.illustrators || []).find((x) => x.name.toLowerCase().includes(v)) : null);
  filter.illustrator = i ? i.name : ''; $('f-illu').value = filter.illustrator; sucheNeu();
});
$('f-rgroup-select').addEventListener('change', () => { rgroupToggle($('f-rgroup-select').value); });
$('f-trainer').addEventListener('change', () => { filter.trainer = $('f-trainer').value; mehrFilterZahl(); sucheNeu(); });
$('f-jahr-von').addEventListener('change', () => { filter.jahrVon = Number($('f-jahr-von').value) || 0; mehrFilterZahl(); sucheNeu(); });
$('f-jahr-bis').addEventListener('change', () => { filter.jahrBis = Number($('f-jahr-bis').value) || 0; mehrFilterZahl(); sucheNeu(); });
$('wb-name').addEventListener('input', () => { if (S.binder) { S.binder.name = $('wb-name').value; $('wb-titel').textContent = S.binder.name || t('neuer_binder'); speichern(); } });


// ============================================================================
// 2026-08-27: Undo, Sammel-Modus, Detail-Panel, Varianten, Import, Vorlagen,
// Gast-Binder erst bei der ersten Karte, PDF-Vorbereitung, Anzeigename.
// ============================================================================

// ---------- Rückgängig ----------
/* ==========================================================================
   Rückgängig / Wiederholen

   Bis zum 10.09.2026 merkte sich der Planer **einen** Stand: `merken()` überschrieb
   den vorigen, und wer zweimal etwas tat, kam nur einen Schritt zurück. Beim
   Umsortieren von 400 Karten ist das der Unterschied zwischen „ärgerlich" und
   „von vorn anfangen".

   Jetzt ein Stapel. Gespeichert wird der ganze `items`-Array je Schritt — bei 500
   Fächern sind das ein paar hundert Kilobyte im Speicher, und das ist die
   billigere Lösung als ein Diff-Format, das bei jeder neuen Aktion mitgepflegt
   werden müsste. Der Stapel hängt an der Binder-ID: wer den Binder wechselt,
   fängt mit leerer Historie an, damit ein Rückgängig nie in den falschen Binder
   schreibt.
   ========================================================================== */

const HISTORIE_MAX = 40;
const H = { zurueck: [], vor: [], binderId: null };

/** Stapel leeren — beim Binderwechsel und nach dem Laden. */
function historieLeeren() { H.zurueck = []; H.vor = []; H.binderId = S.binder ? S.binder.id : null; historieKnoepfe(); }

/** Zustand der beiden Knöpfe in der Werkzeugleiste. */
function historieKnoepfe() {
  const z = $('wb-undo'), v = $('wb-redo');
  if (z) { z.disabled = !H.zurueck.length; z.title = H.zurueck.length ? t('undo') + ': ' + H.zurueck[H.zurueck.length - 1].label : t('undo'); }
  if (v) { v.disabled = !H.vor.length; v.title = H.vor.length ? t('redo') + ': ' + H.vor[H.vor.length - 1].label : t('redo'); }
}

let undo = null;   // letzter Schritt, nur noch für den Knopf im Toast
function merken(label) {
  if (!S.binder) return;
  const stand = { label: t('h_' + label) || label, items: JSON.parse(JSON.stringify(S.binder.items)), binderId: S.binder.id };
  if (H.binderId !== S.binder.id) historieLeeren();
  H.zurueck.push(stand);
  if (H.zurueck.length > HISTORIE_MAX) H.zurueck.shift();
  // Ein neuer Schritt macht die Vorwärts-Kette ungültig — sonst spränge „Wiederholen"
  // in einen Zustand, der zu den jetzigen Karten nicht mehr passt.
  H.vor = [];
  undo = stand;
  historieKnoepfe();
}

/** Den aktuellen Stand für die Gegenrichtung sichern. */
function historieStand(label) {
  return { label, items: JSON.parse(JSON.stringify(S.binder.items)), binderId: S.binder.id };
}

function historieAnwenden(stand, richtung) {
  richtung.push(historieStand(stand.label));
  S.binder.items = stand.items;
  S.auswahl.clear();
  speichern(); zeichneBinder(); zeichneErgebnisse();
  historieKnoepfe();
  toast(t(richtung === H.vor ? 'h_zurueck' : 'h_vor').replace('{was}', stand.label));
}
function toastUndo(text) {
  const el = $('toast'); $('toast-text').textContent = text;
  const b = $('toast-undo'); b.textContent = t('undo'); b.classList.toggle('hidden', !undo);
  el.classList.add('zeig');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.classList.remove('zeig'); b.classList.add('hidden'); }, 7000);
}
function rueckgaengig() {
  if (!S.binder) return;
  $('toast').classList.remove('zeig'); $('toast-undo').classList.add('hidden');
  const stand = H.zurueck.pop();
  if (!stand || stand.binderId !== S.binder.id) { historieLeeren(); return; }
  undo = null;
  historieAnwenden(stand, H.vor);
  return;
}

/** Einen zurückgenommenen Schritt wieder ausführen. */
function wiederholen() {
  if (!S.binder) return;
  const stand = H.vor.pop();
  if (!stand || stand.binderId !== S.binder.id) { H.vor = []; historieKnoepfe(); return; }
  historieAnwenden(stand, H.zurueck);
}

/* ==========================================================================
   Seitentitel, Etiketten, Variantenwechsel am Fach

   Drei kleine Dinge mit demselben Muster: sie hängen am Fach oder an der Seite,
   nicht am Binder, und sie sind nach dem Vorbild dessen gebaut, was Sammler in
   echten Bindern tun — Trennblatt beschriften, Post-it aufkleben, sich merken,
   welchen Druck man besitzt.
   ========================================================================== */

/** Titel einer Seite, oder '' — die Titel liegen in `options.seitenTitel`. */
function seitenTitel(nr) {
  const o = (S.binder && S.binder.options) || {};
  return (o.seitenTitel && (o.seitenTitel[String(nr)] || o.seitenTitel[nr])) || '';
}

/** Titel setzen oder löschen. Ein leerer Text entfernt den Eintrag ganz — sonst
 *  sammelten sich leere Zeichenketten in den Optionen und die Seite zeigte ein
 *  einsames „ · ". */
function seitenTitelSetzen(nr, text) {
  if (!S.binder || S.nurAnsicht) return;
  const o = S.binder.options = S.binder.options || {};
  o.seitenTitel = o.seitenTitel || {};
  const sauber = String(text || '').trim().slice(0, 40);
  if (sauber) o.seitenTitel[String(nr)] = sauber; else delete o.seitenTitel[String(nr)];
  speichern(); zeichneBinder();
}

function seitenTitelFragen(nr) {
  if (S.nurAnsicht) return;
  const seite = nr == null ? S.seite : nr;
  const jetzt = seitenTitel(seite);
  const neu = prompt(t('st_titel_frage'), jetzt);
  if (neu === null) return;
  seitenTitelSetzen(seite, neu);
}

/** Der Knopf neben der Seitenzahl: zeigt den Titel, oder lädt ein, einen zu setzen. */
function seitenTitelZeigen() {
  const b = $('wb-seitentitel');
  if (!b) return;
  const titel = seitenTitel(S.seite);
  b.textContent = titel || t('st_titel_leer');
  b.classList.toggle('leer', !titel);
  b.classList.toggle('hidden', !!S.nurAnsicht);
}

/** Einen Treffer aus der Suche in die Ablage legen, statt ihn sofort zu platzieren.
 *
 *  Der Unterschied ist der Arbeitsablauf: „alle Glurak dieser Ära" sucht man einmal,
 *  will sie aber zusammen an eine bestimmte Stelle legen. Ohne Ablage klickt man
 *  zwischen Suche und Binder hin und her und sortiert danach von Hand nach. */
function ablageDazu(i) {
  const k = (S.ergebnisse || [])[i];
  if (!k) return;
  S.ablage = S.ablage || [];
  S.ablage.push({ type: 'card', id: k.id });
  ablageZeigen();
  toast(t('ab_dazu_ok').replace('{n}', S.ablage.length));
}

/* --- Etiketten ---------------------------------------------------------- */

const ETIKETT_FARBEN = ['#f5c518', '#4ea3f5', '#5fc48a', '#ef7b73', '#b79cf0', '#8c9097'];

/** Etiketten eines Fachs bearbeiten: Text eingeben, Farbe rotiert durch. */
function etikettFragen(idx) {
  if (S.nurAnsicht || !S.binder) return;
  const item = S.binder.items[idx];
  if (!item || item.type === 'empty') return;
  const vorhanden = item.etiketten || [];
  if (vorhanden.length >= 3) return toast(t('et_voll'));
  const text = prompt(t('et_frage'), '');
  if (text === null) return;
  merken('etikett');
  const sauber = String(text).trim().slice(0, 18);
  if (!sauber) {
    // Leerer Text nimmt das zuletzt gesetzte Etikett wieder weg — der Weg zurück
    // ohne einen zweiten Dialog.
    if (vorhanden.length) { item.etiketten = vorhanden.slice(0, -1); toast(t('et_weg')); }
  } else {
    item.etiketten = vorhanden.concat([{ text: sauber, farbe: ETIKETT_FARBEN[vorhanden.length % ETIKETT_FARBEN.length] }]);
    toast(t('et_dran'));
  }
  if (item.etiketten && !item.etiketten.length) delete item.etiketten;
  speichern(); zeichneBinder();
}

/* --- Variante am Fach --------------------------------------------------- */

/** Welche Drucke es zu dieser Karte gibt — aus den Kartendaten, nicht geraten.
 *
 *  Die Suche liefert `normal` und `reverse` je Treffer (katalog.py). Steht die Karte
 *  gerade nicht in den Ergebnissen — etwa weil der Binder frisch geladen wurde —, gibt
 *  es keine Auskunft, und dann ist die volle Liste die ehrlichere Antwort: lieber eine
 *  Auswahl zu viel als eine fehlende. */
function variantenFuer(item) {
  const k = (S.ergebnisse || []).find((x) => x.id === item.id);
  if (!k || k.reverse == null) return VARIANTEN;
  const moeglich = ['normal'];
  if (k.reverse) moeglich.push('reverse');
  moeglich.push('holo');
  return moeglich;
}

/** Kleines Menü am Fach: Druck wählen, Preis folgt. */
function variantenMenue(ev, idx) {
  if (S.nurAnsicht || !S.binder) return;
  const item = S.binder.items[idx];
  if (!item || item.type !== 'card') return;
  const alt = document.getElementById('vmenu');
  if (alt) alt.remove();
  const liste = variantenFuer(item);
  const box = document.createElement('div');
  box.id = 'vmenu';
  box.className = 'vmenu';
  box.innerHTML = liste.map((v) => `<button class="${(item.variant || 'normal') === v ? 'an' : ''}"
      onclick="varianteSetzen(${idx},'${v}')">${esc(t('v_' + v))}</button>`).join('')
    + `<div class="trenn"></div><button onclick="etikettFragen(${idx});vmenuZu()">${esc(t('et_neu'))}</button>`;
  document.body.appendChild(box);
  const r = ev.currentTarget.getBoundingClientRect();
  box.style.left = Math.min(window.innerWidth - box.offsetWidth - 8, r.left) + 'px';
  box.style.top = (r.bottom + 4) + 'px';
  setTimeout(() => document.addEventListener('click', vmenuZu, { once: true }), 0);
}

function vmenuZu() { const m = document.getElementById('vmenu'); if (m) m.remove(); }

function varianteSetzen(idx, v) {
  const item = S.binder.items[idx];
  if (!item) return;
  merken('variante');
  if (v === 'normal') delete item.variant; else item.variant = v;
  vmenuZu();
  speichern(); zeichneBinder();
  // Der Holo-Preis liegt in einer eigenen Tabelle; ohne Nachladen zeigte das Fach
  // weiter den Grundpreis, obwohl das Etikett schon HOLO sagte.
  if (S.preiseAn && typeof preiseLaden === 'function') preiseLaden();
}

// ---------- Gast-Binder: erst anlegen, wenn die erste Karte kommt ----------
function lokalerBinder() {
  return { id: null, name: t('neuer_binder'), mode: 'custom', layout: (S.binder && S.binder.layout) || '3x3', options: {}, items: [] };
}
let binderAnlage = null;   // laufender POST, damit zwei schnelle Klicks nicht zwei Binder anlegen
async function binderAnlegenWennNoetig() {
  if (!S.binder || S.binder.id) return true;
  if (binderAnlage) return binderAnlage;
  binderAnlage = _binderAnlegen().finally(() => { binderAnlage = null; });
  return binderAnlage;
}
async function _binderAnlegen() {
  let res;
  try {
    res = await api('api/binders', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: S.binder.name, mode: S.binder.mode, layout: S.binder.layout, options: S.binder.options || {}, items: S.binder.items }) });
  } catch (e) { if (!gate(e)) toast(e.message); return false; }
  S.binder.id = res.id;
  merkeBinderId(res.id);
  if (undo && undo.binderId === null) undo.binderId = res.id;   // Rückgängig nach der ersten Karte bleibt möglich
  hashSetzen('binder/' + res.id);
  return true;
}

// ---------- Sammel-Modus ----------
// Der Haken „hab ich“ ist der meistgenutzte Klick im Binder und braucht keinen eigenen Modus
// mehr: er ist immer sichtbar. Der Klick auf das Fach selbst setzt ihn.
document.body.classList.add('sammeln');
function sammelnToggle() { zeichneBinder(); }

// ---------- Fächer frei anordnen ----------
function freiesFach(idx) {
  // Klick auf einen unbelegten Platz: bis dorthin mit leeren Fächern auffüllen (freier Platz
  // im Raster) — und dieses Fach gleich als Ziel wählen, damit die nächste Karte dort landet.
  if (!S.binder || S.nurAnsicht) return;
  merken('frei');
  while (S.binder.items.length <= idx) S.binder.items.push({ type: 'empty' });
  S.auswahl.clear();
  S.auswahl.add(idx);
  speichern(); zeichneBinder();
  sucheLadeOeffnen(false);
}
/**
 * Ein aufgeklapptes Menü so legen, dass es ganz im Sichtfeld bleibt.
 *
 * Vorher rechneten beide Aufrufer mit einer geratenen Höhe (220 bzw. 260 px)
 * und schoben das Menü nur nach oben, wenn diese Zahl nicht mehr passte. Ein
 * Fach mit Karte öffnet aber zehn Einträge und damit 300 px: in der untersten
 * Reihe ragte das Menü gemessene 80 px aus dem Fenster, „Entfernen" und
 * „Herausnehmen" lagen außerhalb des Sichtfelds. Die Höhe wird deshalb
 * gemessen, nicht geraten — und passt sie unten nicht, klappt das Menü nach
 * oben auf. Passt sie auf keiner Seite, bekommt es die größere Seite und darin
 * eine eigene Bildlaufleiste.
 *
 * `m` muss dafür bereits sichtbar sein, sonst ist `offsetHeight` null.
 */
function menuEinpassenAn(m, r, rand = 8) {
  m.style.maxHeight = ''; m.style.overflowY = '';
  const h = m.offsetHeight;
  const unten = window.innerHeight - r.bottom - 6 - rand;
  const oben = r.top - 6 - rand;
  if (h <= unten) { m.style.top = (r.bottom + 6) + 'px'; return; }
  if (h <= oben) { m.style.top = (r.top - 6 - h) + 'px'; return; }
  const platz = Math.max(unten, oben, 140);
  m.style.maxHeight = platz + 'px';
  m.style.overflowY = 'auto';
  m.style.top = (unten >= oben ? r.bottom + 6 : Math.max(rand, r.top - 6 - platz)) + 'px';
}
function slotMenue(ev, idx) {
  const item = S.binder.items[idx]; if (!item) return;
  const m = $('menu-slot');
  const r = ev.currentTarget.getBoundingClientRect();
  m.innerHTML = `
    ${item.type === 'card' ? `<button onclick="slotMenueZu();detailOeffnen('${item.id}')">${t('s_details')}</button><button onclick="slotMenueZu();themaOeffnen('${item.id}')">${t('s_passend')}</button><div class="trenn"></div>` : ''}
    ${item.type === 'art' ? `<button onclick="slotMenueZu();artworkOeffnen(${seiteBei(idx)})">${t('s_artwork')}</button>
      <button onclick="slotMenueZu();kunstFreigeben('${esc(item.artwork)}')">${t('aw_seite_frei')}</button><div class="trenn"></div>` : ''}
    <button onclick="slotMenueZu();fachEinfuegen(${idx})">${t('s_frei_davor')}</button>
    <button onclick="slotMenueZu();fachEinfuegen(${idx + 1})">${t('s_frei_danach')}</button>
    ${item.type === 'card' ? `<button onclick="slotMenueZu();fachSprache(${idx})">${t('kartensprache')}: <strong>${(item.sprache || 'de').toUpperCase()}</strong> ${ic('wechsel', 15)}</button><button onclick="slotMenueZu();fachZustand(${idx})">${t('s_zustand')}${item.zustand ? `: <strong>${esc(item.zustand)}</strong>` : ' …'}</button>` : ''}
    <div class="trenn"></div>
    ${item.type !== 'empty' ? `<button onclick="slotMenueZu();fachFreimachen(${idx})">${t('s_entfernen')}</button>` : ''}
    <button class="gefahr" onclick="slotMenueZu();slotHerausnehmen(${idx})" title="${t('s_herausnehmen_t')}">${t('herausnehmen')}</button>`;
  m.classList.remove('hidden');
  const w = 260; m.style.width = w + 'px';
  m.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, r.left - w / 2 + r.width / 2)) + 'px';
  menuEinpassenAn(m, r);
  ev.stopPropagation();
}
function slotMenueZu() { $('menu-slot').classList.add('hidden'); }

