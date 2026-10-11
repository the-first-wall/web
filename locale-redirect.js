/**
 * The First Wall — redirect for the old, language-less URLs.
 *
 * Every page now lives under a language route: /en/... and /de/.... The old URL
 * (/, /manifesto/, ...) is a small stub that loads this file. It sends the visitor
 * to the same page in their language, KEEPING the query string and the fragment,
 * so a shared link like /?slot=7 still opens block 7. Language: the stored choice
 * (tfw_lang), else the browser's, else English.
 *
 * Without JavaScript the stub's <noscript> meta refresh sends everyone to the
 * English page. External file: the strict Content-Security-Policy allows no
 * inline script.
 */
(function () {
  'use strict';

  function stored() {
    try {
      var v = localStorage.getItem('tfw_lang');
      return v === 'en' || v === 'de' ? v : '';
    } catch (e) { return ''; }
  }

  var lang = stored();
  if (!lang) lang = /^de\b/i.test(navigator.language || '') ? 'de' : 'en';

  var link = document.querySelector('link[rel="alternate"][hreflang="' + lang + '"]');
  if (!link) return;

  // Only the PATH of the alternate link is used: the visitor stays on the host they came
  // by (the link carries the canonical host), and nothing but a language route of this
  // site (/en/..., /de/...) is ever a target.
  var path;
  try { path = new URL(link.href, location.href).pathname; } catch (e) { return; }
  if (!/^\/(en|de)\//.test(path)) return;

  location.replace(path + location.search + location.hash);
})();
