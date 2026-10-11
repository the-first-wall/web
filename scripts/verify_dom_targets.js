#!/usr/bin/env node
/**
 * Every DOM target a script looks up must exist in the page that loads it, and
 * every translation key used by a page must exist in both the EN and DE
 * dictionaries of i18n.js.
 *
 *   app.js     <-> index.html
 *   census.js  <-> census/index.html
 *   site.js    <-> every page (header, mobile menu)
 *   data-i18n / data-i18n-aria / data-i18n-title keys: every .html page
 */
const fs = require('fs');
const path = require('path');

const read = (f) => fs.readFileSync(f, 'utf8');
const i18nSrc = read('i18n.js');

/** All .html pages in the repo (partials/ included: they are stamped into every page). */
function htmlFiles(dir = '.') {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (['.git', 'node_modules', '.github'].includes(entry.name)) continue;
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...htmlFiles(p));
    else if (entry.name.endsWith('.html')) out.push(p);
  }
  return out.sort();
}

function getElementByIdTargets(src) {
  const ids = new Set();
  const re = /getElementById\(['"]([^'"]+)['"]\)/g;
  let m;
  let calls = 0;
  while ((m = re.exec(src)) !== null) { ids.add(m[1]); calls++; }
  return { ids, calls };
}

const hasId = (html, id) => new RegExp(`id=["']${id}["']`).test(html);
const hasClass = (html, cls) => new RegExp(`class="[^"]*\\b${cls}\\b`).test(html);

const missing = [];

// --- 1. app.js <-> index.html -------------------------------------------------
const appSrc = read('app.js');
const indexHtml = read('index.html');
const app = getElementByIdTargets(appSrc);
for (const id of app.ids) if (!hasId(indexHtml, id)) missing.push(`index.html: #${id} (app.js)`);

// Selector targets that MUST have at least one element (guarded lookups may be empty)
const mustExistSelectors = [
  '.btn-copy-small',        // app.js copyText feedback
  '.codex-tab-btn',         // tab switching
  '.codex-tab-panel',       // tab switching
  '.btn-lang-toggle',       // i18n language toggle
  '.lang-toggle-text',      // i18n language toggle label
];
for (const sel of mustExistSelectors) {
  if (!hasClass(indexHtml, sel.replace(/^\./, ''))) missing.push(`index.html: ${sel}`);
}
if (!hasId(indexHtml, 'btn-lang-toggle')) missing.push('index.html: #btn-lang-toggle');

// --- 2. census.js <-> census/index.html ---------------------------------------
const censusSrc = read('census.js');
const censusHtml = read('census/index.html');
const census = getElementByIdTargets(censusSrc);
for (const id of census.ids) if (!hasId(censusHtml, id)) missing.push(`census/index.html: #${id} (census.js)`);

// --- 3. site.js <-> every page -------------------------------------------------
const siteSrc = read('site.js');
const site = getElementByIdTargets(siteSrc);
const pages = htmlFiles();
for (const file of pages) {
  const html = read(file);
  const isPartial = /^partials[\\/]/.test(file);
  // every real page carries the stamped chrome; the header partial is its source
  // (build_chrome.py --check owns "every page has the markers and the right copy")
  if (isPartial ? !file.endsWith('header.html') : !html.includes('<!-- SITE:HEADER:START')) continue;
  for (const id of site.ids) if (!hasId(html, id)) missing.push(`${file}: #${id} (site.js)`);
}

// --- 4. i18n keys on every page ------------------------------------------------
function dictHas(key, lang) {
  const start = i18nSrc.indexOf(`${lang}: {`);
  if (start < 0) return false;
  const seg = i18nSrc.slice(start);
  const end = seg.indexOf('\n  }');
  return new RegExp(`\\b${key}\\s*:`).test(seg.slice(0, end > 0 ? end : seg.length));
}

const keyRe = /data-i18n(?:-aria|-title)?="([^"]+)"/g;
const allKeys = new Set();
const missingKeys = { en: new Map(), de: new Map() };   // key -> pages using it
function checkKey(key, where) {
  allKeys.add(key);
  for (const lang of ['en', 'de']) {
    if (dictHas(key, lang)) continue;
    if (!missingKeys[lang].has(key)) missingKeys[lang].set(key, new Set());
    missingKeys[lang].get(key).add(where);
  }
}
const describe = (m) => [...m].map(([k, w]) => `${k} (${[...w][0]}${w.size > 1 ? ` +${w.size - 1} more` : ''})`).join(', ');
for (const file of pages) {
  let m;
  const html = read(file);
  while ((m = keyRe.exec(html)) !== null) checkKey(m[1], file);
}
// keys that census.js attaches to nodes it creates
const dynRe = /(?:setAttribute\('data-i18n',\s*'([^']+)'\)|setStatus\([^,]+,\s*'([^']+)')/g;
let dm;
while ((dm = dynRe.exec(censusSrc)) !== null) checkKey(dm[1] || dm[2], 'census.js');

// --- Report --------------------------------------------------------------------
console.log(`app.js getElementById unique targets: ${app.ids.size} (total calls: ${app.calls})`);
console.log(`census.js getElementById unique targets: ${census.ids.size}`);
console.log(`site.js getElementById unique targets: ${site.ids.size} (checked on every page)`);
console.log(`html pages scanned: ${pages.length}`);
console.log(`distinct data-i18n / -aria / -title keys: ${allKeys.size}`);
console.log(`Missing DOM targets: ${missing.length ? missing.join(', ') : 'NONE'}`);
console.log(`Missing EN keys: ${missingKeys.en.size ? describe(missingKeys.en) : 'NONE'}`);
console.log(`Missing DE keys: ${missingKeys.de.size ? describe(missingKeys.de) : 'NONE'}`);

if (missing.length || missingKeys.en.size || missingKeys.de.size) {
  console.log('RESULT: FAIL');
  process.exit(1);
}
console.log('RESULT: PASS (0 missing)');
