/* Markt — was sich im Katalog bewegt.
 *
 * Eigene Datei, weil index.html ohnehin an der Grenze des Lesbaren ist. Alles Weitere
 * (Diagramm-Helfer, Übersetzungen, api()) kommt aus dem Hauptskript; diese Datei wird
 * mit `defer` danach geladen und hängt sich in `ansicht('markt')` ein.
 *
 * Die Zahlen kommen aus `markt_tag`, einem Tagesstand je Set, Ära, Pokémon und Illustrator.
 * Die Bewegung ist der heutige Trend gegen den 7- bzw. 30-Tage-Schnitt von Cardmarket,
 * als Median über die Karten der Gruppe und ohne Ausreißer. Warum das so ist, steht in
 * markt.py — hier wird es nur angezeigt.
 */

const MKT = { bereich: 'heute', fenster: 30, daten: null, sortier: 'bewegung30',
              ebene: 'set', seite: null, nr: 0 };
// „Meine Karten" ist entfallen: der Reiter zeigte dieselben Sets wie die Sammlung, nur mit
// einer anderen Rechnung. Der Markt ist der Blick nach außen, die Sammlung der nach innen.
const MK_BEREICHE = ['heute', 'sets', 'aeren', 'pokemon', 'regionen'];

/* ---------------------------------------------------------------- Bausteine */

/** Kleine Kurve ohne Achsen: zeigt die Form, nicht den Wert.
 *  `opt.farbe` setzt der Aufrufer aus derselben Zahl, die daneben steht — sonst konnte eine
 *  rote fallende Kurve neben „+15,9 %“ stehen (Audit 11.09.2026, B2): die Kurve las die
 *  eigene Reihe, die Zahl kam aus einer anderen Quelle. */
function anSparkline(punkte, opt) {
  opt = opt || {};
  const w = opt.breite || 104, hh = opt.hoehe || 28;
  const p = (punkte || []).filter((x) => x && x.wert != null);
  if (p.length < 2) return `<span class="spk-leer" title="${t('mk_spark_leer')}">–</span>`;
  const werte = p.map((x) => x.wert);
  let min = Math.min(...werte), max = Math.max(...werte);
  if (min === max) { min -= 1; max += 1; }
  const x = (i) => 1 + i / (p.length - 1) * (w - 2);
  const y = (v) => 2 + (1 - (v - min) / (max - min)) * (hh - 4);
  const steigt = werte[werte.length - 1] >= werte[0];
  const farbe = opt.farbe || (steigt ? 'var(--gruen-text)' : 'var(--akzent-text)');
  const d = p.map((q, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(q.wert).toFixed(1)}`).join(' ');
  const le = p.length - 1;
  return `<svg class="spk" viewBox="0 0 ${w} ${hh}" width="${w}" height="${hh}" aria-hidden="true"
    ><path d="${d}" fill="none" stroke="${farbe}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/
    ><circle cx="${x(le).toFixed(1)}" cy="${y(p[le].wert).toFixed(1)}" r="2.8" fill="${farbe}"/></svg>`;
}

/** Die Farbe einer Bewegung — eine Quelle für Kurve, Zahl und Balken. */
function mkFarbe(wert) {
  if (wert == null) return 'var(--mut)';
  return wert > 0.05 ? 'var(--gruen-text)' : wert < -0.05 ? 'var(--akzent-text)' : 'var(--mut)';
}

/** Bewegung als Zahl mit Vorzeichen und Farbe. */
function mkDelta(wert, opt) {
  opt = opt || {};
  if (wert == null) return `<span class="mk-dl neutral">${opt.leer || '–'}</span>`;
  const kl = wert > 0.05 ? 'plus' : wert < -0.05 ? 'minus' : 'neutral';
  return `<span class="mk-dl ${kl}">${anProz(wert)}</span>`;
}

/** Säulen über eine Klassenachse — für Verteilungen, wo zwei Balkenlisten standen. */
function anHistogramm(items, opt) {
  opt = opt || {};
  if (!items || !items.length) return anDuenn(t('an_keine_daten'));
  const max = Math.max(...items.map((i) => i.anzahl)) || 1;
  return `<div class="mk-hist">${items.map((i) => {
    const h = Math.max(3, Math.round(i.anzahl / max * 100));
    const titel = `${esc(i.name)}: ${anZahl(i.anzahl)} ${t('an_karten')} · ${anEur(i.summe, 0)}`;
    return `<div class="hs" title="${titel}"><span class="wert">${anZahl(i.anzahl)}</span>
      <i style="height:${h}%"></i><span class="lbl">${esc(i.name)}</span></div>`;
  }).join('')}</div>`;
}

/** Zweiseitiger Balken: gefallen links, gestiegen rechts, Nulllinie in der Mitte. */
function anZweiseitig(items, opt) {
  opt = opt || {};
  if (!items || !items.length) return anDuenn(t('an_keine_daten'));
  const max = Math.max(...items.map((i) => Math.abs(i.wert))) || 1;
  return `<div class="mk-div">${items.map((i) => `<div class="dz">
      <span class="l" title="${esc(i.name)}">${opt.klick
        ? `<button onclick="${opt.klick.replace('{id}', esc(i.id))}">${esc(i.name)}</button>`
        : esc(i.name)}</span>
      <span class="track"><i class="${i.wert >= 0 ? 'p' : 'm'}" style="width:${(Math.abs(i.wert) / max * 50).toFixed(1)}%;${
        i.wert >= 0 ? 'left:50%' : 'right:50%'}"></i></span>
      ${mkDelta(i.wert)}</div>`).join('')}</div>`;
}

/** Kachel mit Zahl, Bewegung und Kurve — die Grundform des ganzen Markts. */
function mkKachel(o) {
  return `<div class="mk-kachel${o.klick ? ' klickbar' : ''}"${o.klick ? ` onclick="${o.klick}" role="button" tabindex="0"` : ''}>
    <small>${esc(o.lbl)}</small>
    <b>${o.zahl}</b>
    ${o.delta !== undefined ? mkDelta(o.delta) + (o.unter ? ` <span class="mk-neben">${esc(o.unter)}</span>` : '')
      : o.unter ? `<span class="mk-neben">${esc(o.unter)}</span>` : ''}
    ${o.verlauf ? anSparkline(o.verlauf, { breite: 128 }) : ''}</div>`;
}

/** Eine Zeile der Ranglisten: Rang, Name, Kurve, Bewegung, Basis. */
function mkZeile(z, i, opt) {
  opt = opt || {};
  const feld = MKT.fenster === 7 ? 'bew7' : 'bew30';
  const farbe = mkFarbe(z[feld]);
  const basis = MKT.fenster === 7 ? z.bew7_n : z.bew30_n;
  return `<div class="mk-zeile"${opt.klick ? ` onclick="${opt.klick.replace('{id}', esc(z.schluessel))}" role="button" tabindex="0"` : ''}>
    <span class="rg">${i + 1}</span>
    <span class="nm"><b>${esc(z.name || z.schluessel)}</b><small>${esc(opt.meta ? opt.meta(z) : '')}</small></span>
    ${anSparkline(z.verlauf, { farbe })}
    ${mkDelta(z[feld])}
    <span class="n">${anZahl(basis || z.n)}</span></div>`;
}

/* ------------------------------------------------------------------ Steuerung */

function marktReiterZeichnen() {
  $('mk-reiter').innerHTML = MK_BEREICHE.map((b) =>
    `<button class="chip ${MKT.bereich === b ? 'on' : ''}" onclick="marktBereich('${b}')">${t('mk_r_' + b)}</button>`).join('');
  const w = $('mk-fenster');
  if (w) w.classList.toggle('hidden', !['heute', 'sets', 'aeren', 'pokemon'].includes(MKT.bereich));
}

function marktBereich(b) {
  MKT.bereich = b; MKT.seite = null;
  try { localStorage.setItem('bp_markt', b); } catch (e) {}
  marktReiterZeichnen();
  marktLaden();
}

function marktFenster(tage) {
  MKT.fenster = tage;
  MKT.sortier = tage === 7 ? 'bewegung7' : 'bewegung30';
  marktLaden();
}

async function marktLaden() {
  const r = $('mk-rumpf');
  MKT.bereich = MKT.bereich || (() => { try { return localStorage.getItem('bp_markt') || 'heute'; } catch (e) { return 'heute'; } })();
  if (!MK_BEREICHE.includes(MKT.bereich)) MKT.bereich = 'heute';
  marktReiterZeichnen();
  // Eine geöffnete Set- oder Pokémon-Seite hat Vorrang vor dem Reiter.
  if (MKT.seite) return zeichneMarktSeite();
  r.innerHTML = `<div style="padding:40px;text-align:center">${lader('gross', t('laedt'))}</div>`;
  const f = MKT.fenster;
  const pfade = {
    heute: 'api/markt/heute?fenster=' + f,
    sets: `api/markt/rangliste?ebene=set&sortier=${MKT.sortier}&limit=60`,
    aeren: `api/markt/rangliste?ebene=aera&sortier=${MKT.sortier}&limit=20`,
    illustrator: `api/markt/rangliste?ebene=illustrator&sortier=${MKT.sortier}&limit=40`,
    pokemon: `api/markt/rangliste?ebene=pokemon&sortier=${MKT.sortier}&limit=40`,
    regionen: 'api/analytics/markt/regionen',
  };
  let d;
  try { d = await api(pfade[MKT.bereich]); } catch (e) { r.innerHTML = anDuenn(t('an_fehler')); return; }
  MKT.daten = d;
  if (d.pro === false && MKT.bereich !== 'heute') {
    r.innerHTML = anSperre([t('an_mpro_1'), t('an_mpro_2'), t('an_mpro_3'), t('an_mpro_4')], t('an_mpro_t'));
    return;
  }
  if (MKT.bereich === 'heute') zeichneMarktHeute(d);
  else if (MKT.bereich === 'regionen') zeichneMarktRegionen(d);
  else zeichneMarktRangliste(d);
  if (MKT.bereich === 'aeren') marktIllustratoren();
}

/** Unter den Ären stehen die Illustratoren: dieselbe Frage („wo liegt der Wert"),
 *  eine Ebene tiefer. Wird nachgeladen, damit die Ären sofort dastehen. */
async function marktIllustratoren() {
  let d;
  try { d = await api(`api/markt/rangliste?ebene=illustrator&sortier=${MKT.sortier}&limit=25`); }
  catch (e) { return; }
  if (MKT.bereich !== 'aeren' || MKT.seite || !d.zeilen) return;
  const feld = MKT.fenster === 7 ? 'bew7' : 'bew30';
  const kasten = document.createElement('div');
  kasten.className = 'an-tafel';
  kasten.innerHTML = `<h3>${t('mk_r_illu')}</h3><div class="unter">${t('mk_illu_u2')}</div>
    <div class="an-tab-rahmen"><table class="an-tab mk-tab">
      <thead><tr><th>${t('mk_name_sp')}</th><th class="r">${t('an_karten')}</th>
        <th class="r">${t('mk_wert')}</th><th>${t('mk_verlauf')}</th>
        <th class="r">${MKT.fenster === 7 ? t('mk_7t') : t('mk_30t')}</th></tr></thead>
      <tbody>${d.zeilen.map((z) => `<tr>
        <td><div class="nm">${esc(z.name || z.schluessel)}</div></td>
        <td class="r">${anZahl(z.n)}</td>
        <td class="r">${anEur(z.summe, 0)}</td>
        <td>${anSparkline(z.verlauf, { farbe: mkFarbe(z[feld]) })}</td>
        <td class="r">${mkDelta(z[feld])}</td></tr>`).join('')}</tbody></table></div>`;
  $('mk-rumpf').appendChild(kasten);
}

/* --------------------------------------------------------------------- Heute */

function zeichneMarktHeute(d) {
  const r = $('mk-rumpf');
  if (d.leer) { r.innerHTML = anDuenn(t('mk_kein_stand')); return; }
  const g = d.gesamt || {};
  const feld = MKT.fenster === 7 ? 'bew7' : 'bew30';
  const fensterWort = MKT.fenster === 7 ? t('mk_7t') : t('mk_30t');

  // Der Kopf ist für alle sichtbar — auch ohne Pro. Eine Seite, die nur einen unscharfen
  // Platzhalter zeigt, verkauft nichts; die erste Zeile echter Zahlen schon.
  const band = `<div class="mk-band">
    ${mkKachel({ lbl: t('mk_katalogwert'), zahl: anEur(g.summe, 0), delta: g[feld],
                 unter: fensterWort, verlauf: g.verlauf })}
    ${d.monat ? mkKachel({ lbl: t('mk_set_monat'), zahl: esc(d.monat.name), delta: d.monat.bew30,
                           unter: t('mk_median_n').replace('{n}', anZahl(d.monat.bew30_n || d.monat.n)),
                           klick: `marktSetOeffnen('${esc(d.monat.schluessel)}')` }) : ''}
    ${d.woche ? mkKachel({ lbl: t('mk_set_woche'), zahl: esc(d.woche.name), delta: d.woche.bew7,
                           unter: t('mk_median_n').replace('{n}', anZahl(d.woche.bew7_n || d.woche.n)),
                           klick: `marktSetOeffnen('${esc(d.woche.schluessel)}')` }) : ''}
    ${d.geplant ? mkKachel({ lbl: t('mk_geplant'), zahl: esc(d.geplant.name),
                             unter: t('mk_geplant_u').replace('{n}', anZahl(d.geplant.geplant)),
                             klick: `marktSetOeffnen('${esc(d.geplant.schluessel)}')` }) : ''}
  </div>`;

  if (!d.pro) {
    r.innerHTML = `<h1>${t('an_markt_t')}</h1><div class="unter">${t('mk_u')}</div>${band}`
      + anSperre([t('an_mpro_1'), t('an_mpro_2'), t('an_mpro_3'), t('an_mpro_4')], t('an_mpro_t'));
    return;
  }

  const tafel = (titel, unter, inhalt, fuss) => `<div class="an-tafel"><h3>${titel}</h3>
    <div class="unter">${unter}</div>${inhalt}${fuss ? `<div class="mk-fuss">${fuss}</div>` : ''}</div>`;
  const liste = (zeilen, leer) => zeilen && zeilen.length
    ? zeilen.map((z, i) => mkZeile(z, i, { klick: `marktSetOeffnen('{id}')`,
        meta: (x) => `${x.serie_name || ''}${x.n ? (x.serie_name ? ' · ' : '') + anZahl(x.n) + ' ' + t('an_karten') : ''}` })).join('')
    : anDuenn(leer);

  const bw = d.karten || {};
  r.innerHTML = `<h1>${t('an_markt_t')}</h1>
    <div class="unter">${t('mk_u')} <span class="mk-stand">${t('mk_stand').replace('{d}', anTag(d.stand))}</span></div>
    ${band}
    <div class="an-zwei">
      ${tafel(t('mk_aufwind'), t('mk_aufwind_u').replace('{f}', fensterWort),
              liste(d.aufwind, t('mk_keine_bewegung')),
              `<button class="mk-link" onclick="marktBereich('sets')">${t('mk_alle_sets')}</button>`)}
      ${tafel(t('mk_druck'), t('mk_druck_u'), liste(d.druck, t('mk_keine_bewegung')),
              t('mk_ausreisser_hin'))}
    </div>
    ${tafel(t('mk_aeren'), t('mk_aeren_u'), `<div class="mk-mult">${(d.aeren || []).map((a) => `
        <button class="mm" onclick="marktBereich('aeren')">
          <small>${esc(a.name)}</small>
          ${anSparkline(a.verlauf, { breite: 150, hoehe: 42 })}
          <span class="z">${anEur(a.summe, 0)}</span>${mkDelta(a[feld])}</button>`).join('')}</div>`)}
    <div class="an-zwei">
      ${tafel(t('an_gewinner'), t('mk_karten_u').replace('{f}', fensterWort), mkKartenTabelle(bw.hoch, 'mk-hoch'))}
      ${tafel(t('an_verlierer'), t('mk_karten_u').replace('{f}', fensterWort), mkKartenTabelle(bw.runter, 'mk-runter'))}
    </div>
    <div class="mk-fuss">${t('mk_basis_karten').replace('{n}', anZahl(bw.basis || 0))}</div>`;
}

function mkKartenTabelle(liste, id) {
  if (!liste || !liste.length) return anDuenn(t('an_keine_daten'));
  const grenze = window.innerWidth < 700 ? 5 : 10;
  return `<div id="${id}"><div class="an-tab-rahmen"><table class="an-tab" style="min-width:0">
    <thead><tr><th></th><th>${t('an_karte')}</th><th class="r">${t('mk_schnitt_jetzt')}</th><th class="r">${t('an_aenderung')}</th></tr></thead>
    <tbody>${liste.map((k, i) => `<tr class="${i >= grenze ? 'an-rest' : ''}" onclick="detailOeffnen('${esc(k.id)}')">
      <td><img loading="lazy" src="${imgUrl(k.id)}" alt="" onerror="this.style.visibility='hidden'"></td>
      <td><div class="nm">${esc(k.name || '')}</div><div class="set">${esc(k.set || '')}${k.nr ? ' · ' + esc(k.nr) : ''}</div></td>
      <td class="r">${anEur(k.alt)} → <strong>${anEur(k.neu)}</strong></td>
      <td class="r">${mkDelta(k.prozent)}</td></tr>`).join('')}</tbody></table></div>${
    anMehrKnopf(id, Math.max(0, liste.length - grenze))}</div>`;
}

/* ---------------------------------------------------------------- Ranglisten */

const MK_SORTEN = [['bewegung30', 'mk_s_bew30'], ['bewegung7', 'mk_s_bew7'], ['summe', 'mk_s_summe'],
                   ['median', 'mk_s_median'], ['hoechst', 'mk_s_hoechst'], ['karten', 'mk_s_karten'],
                   ['geplant', 'mk_s_geplant'], ['name', 'mk_s_name']];

function marktSortier(s) { MKT.sortier = s; marktLaden(); }

function zeichneMarktRangliste(d) {
  const ebene = d.ebene;
  const klick = { set: `marktSetOeffnen('{id}')`, pokemon: `marktPokemonOeffnen('{id}')` }[ebene] || '';
  const sorten = MK_SORTEN.filter(([k]) => ebene === 'set' || k !== 'geplant');
  const titel = { set: t('mk_r_sets'), aera: t('mk_r_aeren'), pokemon: t('mk_r_pokemon'),
                  illustrator: t('mk_r_illu') }[ebene] || t('an_markt_t');
  const unter = { set: t('mk_sets_u'), aera: t('mk_aeren_u2'), pokemon: t('mk_pokemon_u'),
                  illustrator: t('mk_illu_u2') }[ebene] || '';
  const feld = MKT.fenster === 7 ? 'bew7' : 'bew30';
  const zeilen = d.zeilen || [];
  const kopfExtra = ebene === 'set'
    ? `<th class="r">${t('mk_abdeckung')}</th><th class="r">${t('mk_s_geplant')}</th>` : '';
  $('mk-rumpf').innerHTML = `<h1>${titel}</h1><div class="unter">${unter}</div>
    ${ebene === 'aera' ? `<div class="mk-mult gross">${zeilen.map((a) => `<div class="mm">
        <small>${esc(a.name)}</small>${anSparkline(a.verlauf, { breite: 180, hoehe: 52 })}
        <span class="z">${anEur(a.summe, 0)}</span>${mkDelta(a[feld])}</div>`).join('')}</div>` : ''}
    <div class="an-chips">${sorten.map(([k, l]) =>
      `<button class="chip ${MKT.sortier === k ? 'on' : ''}" onclick="marktSortier('${k}')">${t(l)}</button>`).join('')}</div>
    <div class="an-tafel" style="padding-top:14px">
      <div class="an-tab-rahmen"><table class="an-tab mk-tab">
        <thead><tr><th>${t('mk_name_sp')}</th><th class="r">${t('an_karten')}</th>${kopfExtra}
          <th class="r">${t('mk_wert')}</th><th class="r">${t('an_teuerste_karte')}</th>
          <th>${t('mk_verlauf')}</th><th class="r">${MKT.fenster === 7 ? t('mk_7t') : t('mk_30t')}</th></tr></thead>
        <tbody>${zeilen.map((z) => `<tr${klick ? ` onclick="${klick.replace('{id}', esc(z.schluessel))}" class="klickbar"` : ''}>
          <td><div class="nm">${esc(z.name || z.schluessel)}</div>
            <div class="set">${esc(z.serie_name || '')}${z.release_date ? (z.serie_name ? ' · ' : '') + z.release_date.slice(0, 4) : ''}</div></td>
          <td class="r">${anZahl(z.n)}</td>
          ${ebene === 'set' ? `<td class="r">${z.abdeckung != null ? z.abdeckung + ' %' : '—'}</td>
            <td class="r">${z.geplant ? anZahl(z.geplant) : '—'}</td>` : ''}
          <td class="r">${anEur(z.summe, 0)}</td>
          <td class="r">${anEur(z.hoechst, 0)}</td>
          <td>${anSparkline(z.verlauf, { farbe: mkFarbe(z[feld]) })}</td>
          <td class="r">${mkDelta(z[feld])}</td></tr>`).join('')}</tbody></table></div>
      <div class="mk-fuss">${t('mk_tab_fuss').replace('{n}', ebene === 'set' ? 25 : 20)} ${t('mk_ausreisser_hin')}</div>
    </div>`;
}

/* ------------------------------------------------------- Set- und Pokémonseite */

function marktSetOeffnen(id) { MKT.seite = { art: 'set', id }; marktSeiteLaden(); }
function marktPokemonOeffnen(dex) { MKT.seite = { art: 'pokemon', id: dex }; marktSeiteLaden(); }
function marktSeiteZu() { MKT.seite = null; marktLaden(); }

async function marktSeiteLaden() {
  const r = $('mk-rumpf');
  r.innerHTML = `<div style="padding:40px;text-align:center">${lader('gross', t('laedt'))}</div>`;
  const s = MKT.seite;
  try {
    MKT.seitenDaten = await api(s.art === 'set' ? 'api/markt/set/' + encodeURIComponent(s.id)
                                                : 'api/markt/pokemon/' + encodeURIComponent(s.id));
  } catch (e) { r.innerHTML = anDuenn(t('an_fehler')); return; }
  zeichneMarktSeite();
}

function zeichneMarktSeite() {
  const d = MKT.seitenDaten;
  if (!d) return marktLaden();
  if (MKT.seite.art === 'set') zeichneMarktSetSeite(d); else zeichneMarktPokemonSeite(d);
}

function mkZurueck(text) {
  return `<button class="btn sekundaer mk-zurueck" onclick="marktSeiteZu()">‹ ${esc(text)}</button>`;
}

function zeichneMarktSetSeite(d) {
  const s = d.set || {}, k = d.kopf || {};
  const komplett = d.karten_gesamt;
  const bewegung = (d.bewegung || []).map((c) => ({ id: c.id, name: c.name, wert: c.bewegung }));
  $('mk-rumpf').innerHTML = `
    <div class="mk-kopfzeile">${mkZurueck(t('mk_r_sets'))}
      <h1>${esc(s.name || s.id)}</h1>
      <span class="mk-stand">${esc(s.serie_name || '')}${s.release_date ? ' · ' + s.release_date.slice(0, 4) : ''}${
        komplett ? ' · ' + anZahl(komplett) + ' ' + t('an_karten') : ''}</span></div>
    <div class="mk-band">
      ${mkKachel({ lbl: t('mk_set_bewegung'), zahl: anProz(k.bew30 == null ? 0 : k.bew30),
                   unter: t('mk_median_n').replace('{n}', anZahl(k.bew30_n || 0)), verlauf: d.verlauf })}
      ${mkKachel({ lbl: t('mk_komplett'), zahl: anEur(k.summe, 0),
                   unter: t('mk_komplett_u').replace('{n}', anZahl(k.n || 0)) })}
      ${mkKachel({ lbl: t('an_teuerste_karte'), zahl: anEur(k.hoechst, 0),
                   unter: (d.teuerste || [])[0] ? d.teuerste[0].name : '' })}
      ${mkKachel({ lbl: t('mk_in_bindern'), zahl: anZahl(d.geplant || 0), unter: t('mk_in_bindern_u') })}
    </div>
    <div class="an-zwei">
      <div class="an-tafel"><h3>${t('mk_verteilung')}</h3>
        <div class="unter">${t('mk_verteilung_u')}</div>${anHistogramm(d.verteilung)}</div>
      <div class="an-tafel"><h3>${t('mk_bewegung_karten')}</h3>
        <div class="unter">${t('mk_bewegung_karten_u')}</div>
        ${anZweiseitig(bewegung, { klick: `detailOeffnen('{id}')` })}</div>
    </div>
    <div class="an-tafel"><h3>${t('an_teuerste')}</h3>
      <div class="unter">${t('mk_teuerste_set_u')}</div>
      <div class="an-karten">${(d.teuerste || []).map((c) => `<button class="an-karte" onclick="detailOeffnen('${esc(c.id)}')">
        <img loading="lazy" src="${imgUrl(c.id)}" alt="" onerror="this.style.visibility='hidden'">
        <div class="p">${anEur(c.eur, 0)}</div><div class="n" title="${esc(c.name)}">${esc(c.name)}</div></button>`).join('')}</div></div>`;
}

function zeichneMarktPokemonSeite(d) {
  const k = d.kopf || {};
  $('mk-rumpf').innerHTML = `
    <div class="mk-kopfzeile">${mkZurueck(t('mk_r_pokemon'))}
      <h1>${esc(d.name)}</h1>
      <span class="mk-stand">${t('mk_dex_nr').replace('{n}', d.dex)}</span></div>
    <div class="mk-band">
      ${mkKachel({ lbl: t('mk_wert_alle'), zahl: anEur(k.summe, 0),
                   unter: t('mk_komplett_u').replace('{n}', anZahl(k.n || 0)), verlauf: d.verlauf })}
      ${mkKachel({ lbl: t('mk_bewegung'), zahl: anProz(k.bew30 == null ? 0 : k.bew30),
                   unter: t('mk_median_n').replace('{n}', anZahl(k.bew30_n || 0)) })}
      ${mkKachel({ lbl: t('an_teuerste_karte'), zahl: anEur(k.hoechst, 0),
                   unter: (d.karten || [])[0] ? d.karten[0].set_name : '' })}
      ${mkKachel({ lbl: t('mk_schnitt_karte'), zahl: anEur(k.median, 2), unter: t('mk_median_u') })}
    </div>
    <div class="an-tafel"><h3>${t('mk_nach_jahr')}</h3>
      <div class="unter">${t('mk_nach_jahr_u')}</div>
      ${anBalken((d.jahre || []).map((j) => ({ name: j.name, wert: j.summe, anzahl: j.anzahl })), { farbe: 'var(--d2)' })}</div>
    <div class="an-tafel"><h3>${t('mk_teuerste_karten')}</h3>
      <div class="unter">${t('mk_teuerste_poke_u')}</div>
      <div class="an-tab-rahmen"><table class="an-tab">
        <thead><tr><th></th><th>${t('an_karte')}</th><th class="r">${t('preis')}</th><th class="r">${t('mk_30t')}</th></tr></thead>
        <tbody>${(d.karten || []).slice(0, 20).map((c) => `<tr onclick="detailOeffnen('${esc(c.id)}')">
          <td><img loading="lazy" src="${imgUrl(c.id)}" alt="" onerror="this.style.visibility='hidden'"></td>
          <td><div class="nm">${esc(c.name)}</div><div class="set">${esc(c.set_name || '')}${c.local_id ? ' · ' + esc(c.local_id) : ''}</div></td>
          <td class="r"><strong>${anEur(c.eur)}</strong></td>
          <td class="r">${mkDelta(c.bewegung)}</td></tr>`).join('')}</tbody></table></div></div>`;
}
