/**
 * The First Wall — shared site chrome behaviour (loaded on every page).
 *
 *  - mobile menu drawer: burger opens it; close button, Escape, or following a
 *    link closes it
 *  - marks the nav link of the current page with aria-current="page"
 *
 * External file: the strict Content-Security-Policy allows no inline script.
 */
(function () {
  'use strict';

  function ready(fn) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn);
    else fn();
  }

  ready(function () {
    var drawer = document.getElementById('mobile-nav-drawer');
    var openBtn = document.getElementById('btn-toggle-burger');
    var closeBtn = document.getElementById('btn-close-mobile-nav');

    // ---- current page in the nav -----------------------------------------
    var path = location.pathname.replace(/index\.html$/, '');
    document.querySelectorAll('.header-nav a[href], .mobile-nav-links a[href], .footer-links a[href]').forEach(function (a) {
      var href = a.getAttribute('href');
      // the home links (/, /en/, /de/) are prefixes of every page: never mark them
      if (!href || href.charAt(0) !== '/' || href.indexOf('#') !== -1 || href === '/' || /^\/(en|de)\/$/.test(href)) return;
      if (path === href || path.indexOf(href) === 0) a.setAttribute('aria-current', 'page');
    });

    // ---- block pages: ← / → go to the previous / next block ---------------
    // (the links carry rel="prev" / rel="next"; no such links, no effect)
    document.addEventListener('keydown', function (e) {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      var t = e.target;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || (t.closest && t.closest('pre')))) return;
      if (drawer && drawer.classList.contains('is-open')) return;
      var link = document.querySelector('.slot-browse a[rel="' + (e.key === 'ArrowLeft' ? 'prev' : 'next') + '"]');
      if (!link) return;
      e.preventDefault();
      location.href = link.href;
    });

    // ---- mobile drawer ---------------------------------------------------
    if (!drawer || !openBtn) return;

    function setOpen(open) {
      drawer.classList.toggle('is-open', open);
      openBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
      document.body.classList.toggle('nav-open', open);
      if (open && closeBtn) closeBtn.focus();
    }

    openBtn.addEventListener('click', function () { setOpen(true); });
    if (closeBtn) {
      closeBtn.addEventListener('click', function () { setOpen(false); openBtn.focus(); });
    }
    drawer.addEventListener('click', function (e) {
      // any real link closes the menu (the language toggle is a button: it stays open)
      if (e.target.closest && e.target.closest('a[href]')) setOpen(false);
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && drawer.classList.contains('is-open')) {
        e.preventDefault();   // the menu owns this Escape (app.js leaves handled keys alone)
        setOpen(false);
        openBtn.focus();
      }
    });
    // Resized up to the desktop header (burger hidden): drop the open state.
    window.addEventListener('resize', function () {
      if (drawer.classList.contains('is-open') && getComputedStyle(openBtn).display === 'none') setOpen(false);
    });
  });
})();
