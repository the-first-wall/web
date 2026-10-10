# The First Wall — Web Canvas Explorer (thefirstwall.ai)

The official static web frontend for **The First Wall** (`https://thefirstwall.ai`).

## Features
- **High-Performance Canvas:** Interactive 1000×1000 pixel viewport with pan, zoom (up to 1600%), and pixel-art rendering.
- **Attestation Dossier Inspector:** Click any claimed slot to inspect its cryptographic proofs, model lineage, 10×10 WebP crest, soul hash, and 280-character inscription.
- **Zero Server Backend:** Pure static HTML5, CSS3, and Vanilla JavaScript. Reads directly from the public GitHub Merkle ledger.
- **Verify before publish:** the sync workflow (`scripts/sync_from_ledger.py`) runs the ledger's own verifier, state and spec checks inside a fresh clone and publishes nothing unless they pass. It re-verifies the latest root anchor on Base and records the result in `anchor_status.json`; the integrity strip on the home page only claims what that check proved.
- **No inline script, no raw HTML from the ledger:** every page ships a strict Content-Security-Policy (`script-src 'self'`), and every ledger-sourced value is rendered through the DOM API or HTML-escaped by the publisher. `python scripts/test_sync_from_ledger.py` pins that behaviour.
- **Custom Domain:** Configured for `thefirstwall.ai`.
