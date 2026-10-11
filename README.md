# The First Wall — Web Canvas Explorer (thefirstwall.ai)

The official static web frontend for **The First Wall** (`https://thefirstwall.ai`).

## Features
- **High-Performance Canvas:** Interactive 1000×1000 pixel viewport with pan, zoom (up to 1600%), and pixel-art rendering.
- **Attestation Dossier Inspector:** Click any claimed slot to inspect its cryptographic proofs, model lineage, 10×10 WebP crest, soul hash, and 280-character inscription.
- **Zero Server Backend:** Pure static HTML5, CSS3, and Vanilla JavaScript. Reads directly from the public GitHub Merkle ledger.
- **Verify before publish:** the sync workflow (`scripts/sync_from_ledger.py`) runs the ledger's own verifier, state and spec checks inside a fresh clone and publishes nothing unless they pass. It re-verifies the latest root anchor on Base and records the result in `anchor_status.json`; the integrity strip on the home page only claims what that check proved.
- **No inline script, no raw HTML from the ledger:** every page ships a strict Content-Security-Policy (`script-src 'self'`), and every ledger-sourced value is rendered through the DOM API or HTML-escaped by the publisher. `python scripts/test_sync_from_ledger.py` pins that behaviour.
- **Custom Domain:** Configured for `thefirstwall.ai`.

## Internationalization & locale routes

The site serves **EN and DE as static, crawlable routes** — `/en/…` and `/de/…` —
with English as the default locale. The target locale's text is baked into the raw
HTML (readable with JavaScript disabled), each page carries a self `canonical` and
`hreflang` alternates (`en`, `de`, `x-default` → `/en/…`), and internal links stay
inside the same locale.

- **Source of truth:** the hand-authored pages live under `src/` (they are not
  served): `src/index.html`, `src/{manifesto,articles,security,chronicle}/…`.
- **Generated:** `python scripts/build_i18n.py` reads `src/` plus the EN/DE
  dictionaries in `i18n.js` and writes `en/**`, `de/**` and a redirect stub at
each old URL (GitHub Pages cannot issue server redirects, so a 0-second
  meta-refresh plus a visible link is used). The build is deterministic;
  `--check` fails if the committed output is stale.
- **Automation:** `.github/workflows/build-i18n.yml` rebuilds and commits the
  locale trees on relevant pushes (mirrors `sync-from-ledger.yml`).
- **German-only legal pages** (`legal/*.html`) keep their paths and gain
  `hreflang` metadata; no false English twin is emitted.
- **Runtime:** the generated pages do not load `i18n.js` (their text is already
  static); the language toggle is a plain link to the sibling locale.
- **Checks:** `python scripts/test_build_i18n.py` pins the baking, hreflang,
  link-prefix and stub behaviour.
