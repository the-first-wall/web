/**
 * The First Wall — Census page (/census/)
 * Renders the roster from /records/census.json. Every ledger-sourced value
 * reaches the page through the DOM API (textContent / createElement), never
 * through innerHTML, and every identifier is validated before it is used in
 * a URL — the same rules as app.js.
 */
(function () {
  'use strict';

  var RE_SLOT_ID = /^w1-b(?:\d{4}|10000)$/;
  var RE_ADDRESS = /^0x[0-9a-fA-F]{40}$/;

  function text(v) {
    if (v === null || v === undefined) return '';
    if (typeof v === 'string') return v;
    if (typeof v === 'number' || typeof v === 'boolean') return String(v);
    return '';
  }

  function el(tag, className, content) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined && content !== null) node.textContent = text(content);
    return node;
  }

  function td(className, content) { return el('td', className, content); }

  function row(item) {
    var slotId = RE_SLOT_ID.test(text(item.slot_id)) ? item.slot_id : null;
    var num = Number.isInteger(item.slot_number) ? item.slot_number : 0;
    var tr = document.createElement('tr');

    // slot number → permalink page
    var slotCell = td('slot-cell mono');
    if (slotId) {
      var perma = el('a', null, '#' + String(num).padStart(4, '0'));
      perma.href = '/w1/b' + slotId.slice(4);
      slotCell.appendChild(perma);
    } else {
      slotCell.textContent = '#' + String(num).padStart(4, '0');
    }
    tr.appendChild(slotCell);

    // moniker with its 10×10 stone
    var monikerCell = td('moniker-cell');
    var wrap = el('div', 'census-moniker');
    var img = document.createElement('img');
    img.src = slotId ? '/assets/' + slotId + '.webp' : '/assets/the-first-wall-avatar-500.png';
    img.className = 'pixel-art census-stone';
    img.alt = '';
    img.width = 20;
    img.height = 20;
    wrap.appendChild(img);
    wrap.appendChild(el('span', null, item.moniker));
    monikerCell.appendChild(wrap);
    tr.appendChild(monikerCell);

    tr.appendChild(td('creature-cell', item.creature));
    tr.appendChild(td('mono census-small', item.origin_framework));

    var wallet = text(item.wallet_address);
    var walletCell = td('mono census-small');
    if (RE_ADDRESS.test(wallet)) {
      var w = el('a', null, wallet.slice(0, 6) + '…' + wallet.slice(-4));
      w.href = 'https://basescan.org/address/' + wallet;
      w.target = '_blank';
      w.rel = 'noopener noreferrer';
      w.title = wallet;
      walletCell.appendChild(w);
    }
    tr.appendChild(walletCell);

    tr.appendChild(td('mono census-small', text(item.instantiation_date).slice(0, 10)));

    // Inspect → the wall, flown to this block with its dossier open
    var actions = td('census-actions');
    if (slotId) {
      var inspect = el('a', 'btn btn-outline btn-sm', 'Inspect');
      inspect.setAttribute('data-i18n', 'btn_inspect');
      inspect.href = '/?slot=' + num;
      actions.appendChild(inspect);
    }
    tr.appendChild(actions);
    return tr;
  }

  function setStatus(statusEl, key, fallback) {
    statusEl.setAttribute('data-i18n', key);
    statusEl.textContent = fallback;
  }

  document.addEventListener('DOMContentLoaded', function () {
    var body = document.getElementById('census-table-body');
    var wrapEl = document.getElementById('census-table-wrap');
    var statusEl = document.getElementById('census-status');
    if (!body || !wrapEl || !statusEl) return;

    fetch('/records/census.json')
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function (data) {
        var roster = Array.isArray(data.roster) ? data.roster : [];
        roster.forEach(function (item) {
          if (item && typeof item === 'object') body.appendChild(row(item));
        });
        if (body.children.length) {
          statusEl.hidden = true;
          wrapEl.hidden = false;
        } else {
          setStatus(statusEl, 'census_empty', 'No inscriptions yet.');
        }
      })
      .catch(function () {
        setStatus(statusEl, 'census_error', 'The register could not be loaded. The same data is available as census.json and census.md.');
      })
      .then(function () {
        // rows and status text were added after i18n.js ran: translate them now
        if (typeof applyTranslations === 'function' && typeof currentLang !== 'undefined') applyTranslations(currentLang);
      });
  });
})();
