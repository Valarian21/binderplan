/* Schaufenster der Startseite: die beliebtesten Binder und Kunstseiten aus der Vitrine,
   dazu ein Album, in dem Besucher einen Binder durchblättern können — vor dem Anmelden.
   Daten: GET /api/vitrine/schaufenster (öffentlich, zehn Minuten gecacht). Bleibt der
   Aufruf leer oder scheitert er, bleibt der Abschnitt einfach verborgen. */
(function () {
  var sek = document.getElementById('schau');
  if (!sek) return;
  var T = {
    de: { fenster: { woche: 'diese Woche', monat: 'diesen Monat', '': '' }, karten: 'Karten', seiten: 'Seiten',
          seite: 'Seite', von: 'von', herz: '♥', kunst_von: 'von', zurueck: 'Zurückblättern', vor: 'Weiterblättern' },
    en: { fenster: { woche: 'this week', monat: 'this month', '': '' }, karten: 'cards', seiten: 'pages',
          seite: 'Page', von: 'of', herz: '♥', kunst_von: 'by', zurueck: 'Previous', vor: 'Next' }
  };
  var lang = sek.getAttribute('data-lang') === 'en' ? 'en' : 'de';
  var t = T[lang];
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); };
  var imgUrl = function (id) { return '/api/img/card/' + encodeURIComponent(id) + '?lang=' + (/^[A-Z]/.test(id) ? 'en' : lang); };
  var LAYOUTS = { '2x2': [2, 2], '3x3': [3, 3], '3x4': [3, 4], '4x3': [4, 3], '4x4': [4, 4], '4x5': [4, 5], '5x4': [5, 4], '5x5': [5, 5] };

  /* Ein Fach: Karte, Ausschnitt einer Kunstseite, Pokédex-Platz oder leer. Die Fugenrechnung
     ist dieselbe wie in der App: 4 Einheiten Fuge auf 63 × 88 Kartenmaß. */
  function fach(f, spalten, zeilen) {
    if (f.art === 'card' && f.id) return '<div class="s-fach"><img loading="lazy" src="' + imgUrl(f.id) + '" alt=""></div>';
    if (f.art === 'artwork') {
      var l = LAYOUTS[f.layout] || [spalten, zeilen], c2 = l[0], r2 = l[1];
      var pw = c2 * 63 + (c2 - 1) * 4, ph = r2 * 88 + (r2 - 1) * 4;
      var sp = f.slot % c2, ze = Math.floor(f.slot / c2);
      return '<div class="s-fach"><span class="s-art"><img loading="lazy" src="/api/artwork/' + encodeURIComponent(f.id) + '/bild?v=vorschau" alt="" style="width:'
        + (pw / 63 * 100).toFixed(2) + '%;height:' + (ph / 88 * 100).toFixed(2) + '%;left:-' + (sp * 67 / 63 * 100).toFixed(2) + '%;top:-' + (ze * 92 / 88 * 100).toFixed(2) + '%"></span></div>';
    }
    if (f.art === 'dex') return '<div class="s-fach"><img loading="lazy" src="/api/img/dex/' + f.dex + '" alt=""></div>';
    return '<div class="s-fach leer"></div>';
  }
  function seiteHtml(d, klasse) {
    var sp = d.spalten || 3, ze = d.zeilen || 3;
    var fuge = (4 / (sp * 63 + (sp - 1) * 4) * 100).toFixed(3) + '%';
    return '<div class="s-seite ' + (klasse || '') + '" style="grid-template-columns:repeat(' + sp + ',1fr);gap:' + fuge + '">'
      + (d.faecher || []).map(function (f) { return fach(f, sp, ze); }).join('') + '</div>';
  }
  function stapel(seiten) {
    var s = seiten || [];
    return '<div class="s-stapel">' + seiteHtml(s[0] || {}, 'vorn')
      + (s[1] ? seiteHtml(s[1], 'hinten2') : '') + (s[2] ? seiteHtml(s[2], 'hinten3') : '') + '</div>';
  }

  /* Das Album: zwei Seiten nebeneinander, am Handy eine. Pfeile blättern um. */
  var ALBUM = { binder: null, pos: 0 };
  function einzeln() { return window.innerWidth < 640; }
  function albumZeichnen() {
    var b = ALBUM.binder; if (!b) return;
    var seiten = b.seiten_alle || (b.blatt && b.blatt.seiten) || [];
    var schritt = einzeln() ? 1 : 2;
    var max = Math.max(0, seiten.length - schritt);
    if (ALBUM.pos > max) ALBUM.pos = max;
    var teil = seiten.slice(ALBUM.pos, ALBUM.pos + schritt);
    document.getElementById('album-seiten').innerHTML = teil.map(function (d) { return seiteHtml(d, 'album'); }).join('');
    document.getElementById('album-name').textContent = b.name || '';
    document.getElementById('album-meta').textContent = (b.besitzer || '') + ' · ' + b.karten + ' ' + t.karten + ' · ' + b.seiten + ' ' + t.seiten;
    var bis = Math.min(seiten.length, ALBUM.pos + schritt);
    document.getElementById('album-stand').textContent = t.seite + ' ' + (ALBUM.pos + 1) + (bis > ALBUM.pos + 1 ? '–' + bis : '') + ' ' + t.von + ' ' + b.seiten;
    document.getElementById('album-zurueck').disabled = ALBUM.pos <= 0;
    document.getElementById('album-vor').disabled = ALBUM.pos >= max;
    document.getElementById('album-link').href = '/app#ansicht/' + encodeURIComponent(b.id);
    document.querySelectorAll('#schau-binder .s-kachel').forEach(function (el) { el.classList.toggle('an', el.getAttribute('data-id') === b.id); });
  }
  function albumWechsel(d) {
    var schritt = einzeln() ? 1 : 2;
    ALBUM.pos = Math.max(0, ALBUM.pos + d * schritt);
    albumZeichnen();
  }
  function albumOeffnen(b, scrollen) {
    ALBUM.binder = b; ALBUM.pos = 0; albumZeichnen();
    if (scrollen) document.getElementById('schau-album').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
  document.getElementById('album-zurueck').addEventListener('click', function () { albumWechsel(-1); });
  document.getElementById('album-vor').addEventListener('click', function () { albumWechsel(1); });
  window.addEventListener('resize', function () { if (ALBUM.binder) albumZeichnen(); });
  /* Wischen am Handy */
  (function () {
    var x0 = null, el = document.getElementById('album-seiten');
    el.addEventListener('touchstart', function (e) { x0 = e.touches[0].clientX; }, { passive: true });
    el.addEventListener('touchend', function (e) {
      if (x0 == null) return; var dx = e.changedTouches[0].clientX - x0; x0 = null;
      if (Math.abs(dx) > 40) albumWechsel(dx < 0 ? 1 : -1);
    }, { passive: true });
  })();

  fetch('/api/vitrine/schaufenster').then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
    if (!d || !(d.binder || []).length) return;
    var f = document.getElementById('schau-fenster');
    if (f) f.textContent = t.fenster[d.fenster] || '';
    document.getElementById('schau-binder').innerHTML = d.binder.map(function (b) {
      return '<div class="s-kachel" data-id="' + esc(b.id) + '" role="button" tabindex="0">'
        + '<div class="s-bild">' + stapel(b.blatt && b.blatt.seiten) + '</div>'
        + '<div class="s-txt"><strong>' + esc(b.name) + '</strong><span>' + esc(b.besitzer) + ' · ' + b.karten + ' ' + t.karten
        + (b.stimmen ? ' · ' + b.stimmen + ' ' + t.herz : '') + '</span></div></div>';
    }).join('');
    document.querySelectorAll('#schau-binder .s-kachel').forEach(function (el) {
      var b = d.binder.filter(function (x) { return x.id === el.getAttribute('data-id'); })[0];
      var auf = function () { albumOeffnen(b, true); };
      el.addEventListener('click', auf);
      el.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); auf(); } });
    });
    var kunst = d.kunst || [];
    var kb = document.getElementById('schau-kunst-block');
    if (kunst.length) {
      document.getElementById('schau-kunst').innerHTML = kunst.map(function (a) {
        return '<a class="s-kachel kunst" href="/app#vitrine">'
          + '<div class="s-bild">' + (a.blatt ? seiteHtml(a.blatt.seiten[0], 'einzeln') : '<img loading="lazy" src="' + esc(a.vorschau) + '" alt="">') + '</div>'
          + '<div class="s-txt"><strong>' + esc(a.titel) + '</strong><span>' + t.kunst_von + ' ' + esc(a.besitzer)
          + (a.stimmen ? ' · ' + a.stimmen + ' ' + t.herz : '') + '</span></div></a>';
      }).join('');
    } else if (kb) { kb.hidden = true; }
    albumOeffnen(d.binder[0], false);
    sek.hidden = false;
  }).catch(function () {});
})();
