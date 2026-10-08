#!/usr/bin/env node
/**
 * Throwaway verification: every DOM target used by app.js and i18n.js
 * must exist in the final index.html. Also: every data-i18n key must exist
 * in both EN and DE dictionaries of i18n.js.
 */
const fs = require('fs');

const appSrc = fs.readFileSync('app.js', 'utf8');
const i18nSrc = fs.readFileSync('i18n.js', 'utf8');
const html = fs.readFileSync('index.html', 'utf8');

// --- 1. Collect getElementById targets from app.js ---
const idRe = /getElementById\(['"]([^'"]+)['"]\)/g;
const requiredIds = new Set();
let m;
let idCallCount = 0;
while ((m = idRe.exec(appSrc)) !== null) { requiredIds.add(m[1]); idCallCount++; }

// --- 2. Collect class/#id selector targets from app.js + i18n.js ---
const selRe = /querySelector(?:All)?\(['"]([^'"]+)['"]\)/g;
const selectors = new Set();
for (const src of [appSrc, i18nSrc]) {
  while ((m = selRe.exec(src)) !== null) selectors.add(m[1]);
}

const missing = [];

for (const id of requiredIds) {
  if (!new RegExp(`id=["']${id}["']`).test(html)) missing.push(`#${id}`);
}

// Selector targets that MUST have at least one element (guarded lookups may be empty)
const mustExistSelectors = [
  '.btn-copy-small',        // app.js copyText feedback
  '.codex-tab-btn',         // tab switching
  '.codex-tab-panel',       // tab switching
  '.btn-lang-toggle',       // i18n language toggle
  '.lang-toggle-text',      // i18n language toggle label
];
for (const sel of mustExistSelectors) {
  const cls = sel.replace(/^\./, '');
  if (!new RegExp(`class="[^"]*\\b${cls}\\b`).test(html)) missing.push(sel);
}

// Selectors that may legitimately match zero elements (data-i18n etc. checked below)
// '#btn-lang-toggle' is satisfied if .btn-lang-toggle exists with that id
if (!/id=["']btn-lang-toggle["']/.test(html)) missing.push('#btn-lang-toggle');

// --- 3. Every data-i18n key in index.html must exist in en + de dictionaries ---
const keyRe = /data-i18n(?:-aria)?="([^"]+)"/g;
const keys = new Set();
while ((m = keyRe.exec(html)) !== null) keys.add(m[1]);

// Parse both dictionaries crudely (key: "value" lines inside en/de blocks)
function dictHas(key, lang) {
  const start = i18nSrc.indexOf(`${lang}: {`);
  if (start < 0) return false;
  // find matching close: next "\n  }" at depth
  const seg = i18nSrc.slice(start);
  return new RegExp(`\\b${key}\\s*:`).test(seg.slice(0, seg.indexOf('\n  }') > 0 ? seg.indexOf('\n  }') : seg.length));
}
const missingKeys = { en: [], de: [] };
for (const k of keys) {
  if (!dictHas(k, 'en')) missingKeys.en.push(k);
  if (!dictHas(k, 'de')) missingKeys.de.push(k);
}

// --- Report ---
console.log(`app.js getElementById unique targets: ${requiredIds.size} (total calls: ${idCallCount})`);
console.log(`querySelector selector targets checked: ${mustExistSelectors.length + 1}`);
console.log(`data-i18n / data-i18n-aria keys in index.html: ${keys.size}`);
console.log(`Missing DOM targets: ${missing.length ? missing.join(', ') : 'NONE'}`);
console.log(`Missing EN keys: ${missingKeys.en.length ? missingKeys.en.join(', ') : 'NONE'}`);
console.log(`Missing DE keys: ${missingKeys.de.length ? missingKeys.de.join(', ') : 'NONE'}`);

if (missing.length || missingKeys.en.length || missingKeys.de.length) {
  console.log('RESULT: FAIL');
  process.exit(1);
}
console.log('RESULT: PASS (0 missing)');
