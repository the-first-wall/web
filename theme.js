/**
 * The First Wall — theme bootstrap (external so the site can ship a strict
 * Content-Security-Policy with no inline scripts).
 *
 * 1. Applied before first paint: the stored choice wins, else the system
 *    preference. Load this file synchronously in <head>.
 * 2. Toggle: manual choice persists in localStorage('tfw-theme').
 */
(function () {
  try {
    var saved = localStorage.getItem('tfw-theme');
    var theme = (saved === 'light' || saved === 'dark')
      ? saved
      : ((window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) ? 'light' : 'dark');
    document.documentElement.setAttribute('data-theme', theme);
  } catch (e) {
    document.documentElement.setAttribute('data-theme', 'dark');
  }
})();

(function () {
  document.addEventListener('DOMContentLoaded', function () {
    var btn = document.getElementById('btn-theme');
    if (!btn) return;
    btn.addEventListener('click', function () {
      var cur = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
      var next = cur === 'light' ? 'dark' : 'light';
      document.documentElement.setAttribute('data-theme', next);
      try { localStorage.setItem('tfw-theme', next); } catch (e) {}
    });
  });
})();
