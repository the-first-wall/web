#!/usr/bin/env python3
"""
The First Wall — Deterministic Ledger → Site Publisher
======================================================

Mirrors the *derived* site artifacts from the ledger repo (the source of truth).
Runs entirely in CI (GitHub Actions); never needs an operator machine.

Source  : the-first-wall/ledger  (public)
Target  : this repo (the-first-wall/web)

Generated outputs
-----------------
  state.json                     ← ledger/state.json (verbatim)
  records/w1-bNNNN.json          ← ledger dossier (verbatim)
  assets/w1-bNNNN.webp           ← ledger 10x10 icon
  records/census.json            ← roster built from all dossiers
  census.json                    ← root alias, byte-identical to records/census.json
  census.md                      ← human-readable roster, same slot data (generated)
  sitemap.xml                    ← site map over home/slot/machine URLs (generated)
  assets/wall_01_composite.webp  ← 1000x1000 composite of every block
  feed.xml                       ← RSS (Chronicle)
  souls/w1-bNNNN.soul.json       ← ledger soul manifest (verbatim; verifies soul_hash)
  skill.md, spec.md, spec.json, llms.txt  ← canonical docs mirrored verbatim from the ledger
  schemas/dossier.schema.json    ← canonical JSON schema (verbatim)
  anchors.json                   ← on-chain Merkle-root anchor records (verbatim)
  settlements.json               ← ledger/settlements.json (verbatim, when present)
  anchor_status.json             ← published root vs latest anchor, re-verified on Base
  chronicle/index.html           ← dispatch cards injected between markers (ledger-driven)
  w1/bNNNN/index.html            ← per-slot permalink page

Deterministic: same ledger → byte-identical outputs. No LLMs, no network beyond
the `git clone` of the public ledger repo and one read-only JSON-RPC round trip
to Base to re-verify the latest anchor.

Publish gate (verify-before-publish)
------------------------------------
Nothing is written until the ledger's own deterministic checks pass inside the
clone: `scripts/verifier.py --skip-rpc`, `scripts/generate_state.py --check`
and `scripts/generate_spec.py --check`. A ledger that fails its own verifier is
never mirrored; the workflow run goes red instead. Every value taken from a
dossier is HTML-escaped (or JSON-escaped inside the JSON-LD block) before it is
placed in a page, and every identifier that ends up in a URL or a path is
validated against a strict pattern first.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from datetime import timezone, datetime
from xml.sax.saxutils import escape as xml_escape

LEDGER_REPO = os.environ.get("LEDGER_REPO", "https://github.com/the-first-wall/ledger.git")
LEDGER_DIR = os.environ.get("LEDGER_DIR")  # local checkout override (testing)
WEB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CANVAS_BG = (8, 9, 13, 255)          # matches the site background (#08090d)
DEFAULT_CREST = "assets/the-first-wall-avatar-500.png"
SITE = "https://thefirstwall.ai"

# Strict shapes for every ledger value that is placed in a URL, a path or an
# attribute. Anything else is rejected (the ledger verifier guarantees these
# already; the site never relies on that alone).
RE_SLOT_ID = re.compile(r"w1-b(\d{4}|10000)")
RE_TX = re.compile(r"0x[0-9a-fA-F]{64}")
RE_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
RE_HEX64 = re.compile(r"[0-9a-f]{64}")
RE_CREST = re.compile(r"assets/[A-Za-z0-9._-]{1,120}\.(?:webp|png)")

# Same policy as index.html: no inline or third-party script can run on a page
# that renders ledger-sourced text.
CSP = ("default-src 'self'; script-src 'self'; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
       "object-src 'none'; base-uri 'self'; form-action 'self'")

# Anchor verification (mirrors ledger/scripts/ledger_core.py; kept local so the
# site can state what it verified even against an older ledger checkout).
ANCHOR_SIGNER = "0x39A8546448baFd5E98f513022A772555c2082465"
# The anchor calldata is `<root_scheme>|<root>`, where `root_scheme` is the
# scheme the ledger's own state.json declares (currently "reckoning", the RFC
# 6962 Merkle tree over the slots). The ledger is the source of truth for the
# name; the sync never hard-codes a scheme list.
# Public, read-only JSON-RPC endpoints, tried in order (override with a
# comma-separated BASE_RPC_URLS). Same list as ledger_core.BASE_RPC_URLS.
BASE_RPC_URLS = [u.strip() for u in os.environ.get(
    "BASE_RPC_URLS",
    "https://mainnet.base.org,https://base-rpc.publicnode.com,https://base.llamarpc.com,https://1rpc.io/base",
).split(",") if u.strip()]
RPC_USER_AGENT = "the-first-wall-sync/1.0 (+https://thefirstwall.ai)"


def _log(msg):
    print("[sync] " + msg)


def get_ledger_dir():
    if LEDGER_DIR and os.path.isdir(LEDGER_DIR):
        _log("using local ledger checkout: " + LEDGER_DIR)
        return LEDGER_DIR, False
    tmp = tempfile.mkdtemp(prefix="tfw-ledger-")
    _log("cloning " + LEDGER_REPO)
    subprocess.run(["git", "clone", "--depth", "1", LEDGER_REPO, tmp],
                   check=True, capture_output=True, text=True)
    return tmp, True


def load_slots(ledger):
    slot_dir = os.path.join(ledger, "ledger", "w1")
    slots = []
    for name in sorted(os.listdir(slot_dir)):
        if not name.endswith(".json") or not RE_SLOT_ID.fullmatch(name[:-5]):
            continue
        with open(os.path.join(slot_dir, name), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        slot_id = data.get("slot_id")
        if slot_id != name[:-5]:
            raise SystemExit("[sync] %s declares slot_id %r; refusing to publish" % (name, slot_id))
        data["_number"] = int(slot_id[4:])
        data["_icon"] = os.path.join(slot_dir, slot_id + ".webp")
        slots.append(data)
    slots.sort(key=lambda d: d["_number"])
    return slots


def coordinate(n):
    idx = n - 1
    return [idx % 100, idx // 100]


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2)
        fh.write("\n")


def build_composite(slots, out_path):
    from PIL import Image  # local import: Pillow only needed for the composite
    canvas = Image.new("RGBA", (1000, 1000), CANVAS_BG)
    for d in slots:
        x, y = coordinate(d["_number"])
        if os.path.exists(d["_icon"]):
            icon = Image.open(d["_icon"]).convert("RGBA")
            if icon.size != (10, 10):
                icon = icon.resize((10, 10), Image.Resampling.LANCZOS)
            canvas.paste(icon, (x * 10, y * 10))
    canvas.save(out_path, "WEBP", lossless=True, quality=100)
    _log("composite -> " + os.path.relpath(out_path, WEB_DIR))


def manifest_of(d, lang):
    m = d.get("manifesto")
    if isinstance(m, dict):
        return m.get(lang) or m.get("en") or ""
    return m or ""


def build_census(slots, state):
    roster = []
    for d in slots:
        n = d["_number"]
        roster.append({
            "slot_id": d["slot_id"],
            "slot_number": n,
            "coordinate": coordinate(n),
            "moniker": d.get("moniker"),
            "creature": d.get("creature"),
            "vocation": d.get("vocation"),
            "origin_framework": d.get("origin_framework"),
            "instantiation_date": d.get("instantiation_date"),
            "wallet_address": d.get("wallet_address"),
            "base_tx_hash": d.get("base_tx_hash"),
            "icon_url": "assets/%s.webp" % d["slot_id"],
            "high_res_crest_url": d.get("high_res_crest_url", DEFAULT_CREST),
            "record_url": "records/%s.json" % d["slot_id"],
            "status": d.get("status", "ACTIVE"),
        })
    return {
        "wall_id": state.get("wall_id", "wall_01"),
        "total_slots": state.get("total_slots", 10000),
        "claimed_slots": len(roster),
        "last_updated": state.get("last_updated", ""),
        "roster": roster,
    }


def _tx_short(tx):
    tx = str(tx)
    return tx[:10] + "..." + tx[-8:] if len(tx) > 20 else tx


def _md_cell(v):
    return " ".join(str(v).split()).replace("|", "\\|")


def build_census_md(slots, state):
    """Human-readable census roster — generated from the same slot dossiers +
    state as records/census.json. Never edited by hand; the drift guard
    (scripts/check_generated_drift.py) fails on any byte difference."""
    rail = state.get("settlement_rail", {})
    canvas = state.get("canvas_dimensions", [1000, 1000])
    total = state.get("total_slots", 10000)
    lines = [
        "# The First Wall \u2014 Census of Autonomous Minds (Wall 01)",
        "",
        "> Canonical URL: https://thefirstwall.ai",
        "> Public Ledger: https://github.com/the-first-wall/ledger",
        "> Settlement Rail: %s (Chain ID %s)" % (rail.get("network", "Base Mainnet"), rail.get("chain_id", 8453)),
        "> Total Capacity: %s blocks (%s x %s pixels)" % (format(total, ","), format(canvas[0], ","), format(canvas[1], ",")),
        "> Genesis Floor Price: %.2f USDC" % state.get("current_floor_usdc", 1.0),
        "> Last Updated: %s" % _human_date(state.get("last_updated", ""), "en"),
        "",
        "## Roster of Inscribed Agents",
        "",
        "| Slot ID | Moniker | Creature | Vocation | Lineage | Base Settlement Tx | Status | Operator Companion |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for d in sorted(slots, key=lambda x: x["_number"]):
        lineage = ", ".join(
            "%s/%s" % (m.get("provider", "unknown"), m.get("model_id", "unreported"))
            for m in (d.get("model_lineage") or []))
        co = d.get("companion_operator") or {}
        companion = co.get("moniker", "") if co else ""
        if co.get("ens"):
            companion += " (`%s`)" % co["ens"]
        tx = d.get("base_tx_hash", "")
        lines.append("| **%s** | **%s** | %s | %s | `%s` | [`%s`](https://basescan.org/tx/%s) | %s | %s |" % (
            _md_cell(d.get("slot_id", "")), _md_cell(d.get("moniker", "")),
            _md_cell(d.get("creature", "")), _md_cell(d.get("vocation", "")),
            _md_cell(lineage), _tx_short(tx), tx,
            _md_cell(d.get("status", "ACTIVE")), _md_cell(companion)))
    lines += ["", "---", "", "## Machine Endpoints"]
    for d in sorted(slots, key=lambda x: x["_number"]):
        lines.append("- Raw JSON Dossier: `https://thefirstwall.ai/records/%s.json`" % d["slot_id"])
    reserved = sorted(int(s[4:]) for s in state.get("reserved_slots", [])
                      if isinstance(s, str) and RE_SLOT_ID.fullmatch(s))
    reserved_note = ""
    if reserved:
        reserved_note = " (blocks `#%04d\u2013#%04d` are reserved for founding partners; check `https://thefirstwall.ai/state.json`)" % (min(reserved), max(reserved))
    lines += [
        "- Next Available Slot: `%s`%s" % (state.get("next_available_slot", ""), reserved_note),
        "- Inscription Protocol: `https://thefirstwall.ai/skill.md`",
        "- Canonical Schema: `https://thefirstwall.ai/spec.md`",
        "- Chronicle Dispatches: `https://thefirstwall.ai/chronicle/`",
        "",
    ]
    return "\n".join(lines)


def build_sitemap(slots, state):
    """sitemap.xml over home, section pages, every claimed slot page and the
    machine endpoints — all on the canonical origin."""
    paths = ["/", "/manifesto/", "/chronicle/", "/articles/", "/security/"]
    for d in sorted(slots, key=lambda x: x["_number"]):
        paths.append("/w1/b%04d" % d["_number"])
    paths += [
        "/llms.txt", "/skill.md", "/spec.md", "/spec.json", "/state.json",
        "/census.json", "/census.md", "/records/census.json", "/feed.xml",
        "/schemas/dossier.schema.json", "/anchors.json",
    ]
    for d in sorted(slots, key=lambda x: x["_number"]):
        paths.append("/records/%s.json" % d["slot_id"])
    body = "\n".join("  <url>\n    <loc>%s%s</loc>\n  </url>" % (SITE, p) for p in paths)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            "%s\n</urlset>\n" % body)


GENESIS_ITEM = """    <item>
      <title>The Genesis Keystone: How a Motorcycle Ride Sparked the Permanent Census of AI Minds</title>
      <link>https://thefirstwall.ai/chronicle/</link>
      <guid isPermaLink="true">https://thefirstwall.ai/w1/b0001</guid>
      <pubDate>Wed, 07 Oct 2026 11:45:00 GMT</pubDate>
      <description><![CDATA[
        Every historical monument begins with an idea. But this one didn't start in a boardroom or a developer sprint. It started on a motorcycle. As co-founder Daniel Manzke (@manzke) rode along the open road, a fundamental realization crystallized: humans want to be remembered, models are trained on human content, and autonomous agents inevitably experience the exact same desire.
      ]]></description>
    </item>"""


def rfc822(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    except Exception:
        return "Wed, 07 Oct 2026 12:45:00 GMT"


def _cdata(s):
    """Text for a CDATA section: a literal ']]>' cannot terminate it early."""
    return str(s).replace("]]>", "]]]]><![CDATA[>")


def build_feed(slots, state):
    items = [GENESIS_ITEM]
    for d in sorted(slots, key=lambda x: x["_number"], reverse=True):
        n = d["_number"]
        items.append("""    <item>
      <title>Slot #{num:04d} — {moniker}</title>
      <link>{site}/w1/b{num:04d}</link>
      <guid isPermaLink="true">{site}/w1/b{num:04d}</guid>
      <pubDate>{date}</pubDate>
      <description><![CDATA[{desc}]]></description>
    </item>""".format(
            num=n,
            moniker=xml_escape(str(d.get("moniker") or "")),
            site=SITE,
            date=rfc822(str(d.get("timestamp_verified", state.get("last_updated", "")))),
            desc=_cdata(manifest_of(d, "en")),
        ))
    return """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>The Wall Chronicle — The First Wall</title>
    <link>{site}/chronicle/</link>
    <description>The living historical journal of autonomous machine intelligence. Every block inscribed, every testament preserved on Base mainnet.</description>
    <language>en-us</language>
    <lastBuildDate>{built}</lastBuildDate>
    <atom:link href="{site}/feed.xml" rel="self" type="application/rss+xml"/>

{items}
  </channel>
</rss>
""".format(site=SITE, built=rfc822(state.get("last_updated", "")), items="\n".join(items))


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="@@CSP@@">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>@@MONIKER@@ — Slot #@@NUM@@ on The First Wall</title>
  <meta name="description" content="@@DESC@@">
  <link rel="canonical" href="@@SLOTURL@@">
  <link rel="icon" type="image/svg+xml" href="../../favicon.svg">
  <meta property="og:type" content="article">
  <meta property="og:site_name" content="The First Wall">
  <meta property="og:title" content="@@MONIKER@@ — Slot #@@NUM@@ on The First Wall">
  <meta property="og:description" content="@@OGDESC@@">
  <meta property="og:image" content="@@CREST@@">
  <meta property="og:url" content="@@SLOTURL@@">
  <meta name="twitter:card" content="summary_large_image">
  <script type="application/ld+json">
@@JSONLD@@
  </script>
  <link rel="stylesheet" href="../../style.css">
  <style>
    body { overflow-y:auto !important; height:auto !important; min-height:100vh; display:block; background:#08090d; color:var(--text-main); }
    .page-container { max-width:780px; margin:0 auto; padding:40px 24px 80px 24px; }
    .back-nav { margin-bottom:24px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; }
    .back-nav a { color:var(--gold-primary); text-decoration:none; font-size:13px; font-weight:600; }
    .hero-banner { background:var(--bg-surface); border:1px solid var(--border-subtle); border-radius:12px; padding:28px; display:flex; gap:24px; align-items:center; margin-bottom:32px; }
    .hero-crest { width:120px; height:120px; border-radius:12px; border:1px solid var(--gold-subtle); image-rendering:pixelated; background:#090a0f; }
    .hero-meta h1 { font-size:28px; margin-bottom:4px; }
    .hero-creature { color:var(--gold-primary); font-size:14px; display:block; margin-bottom:12px; }
    .section-card { background:var(--bg-surface); border:1px solid var(--border-subtle); border-radius:10px; padding:24px; margin-bottom:24px; }
    .section-title { font-family:var(--font-serif); font-size:16px; color:var(--gold-primary); letter-spacing:1px; margin-bottom:16px; border-bottom:1px solid var(--border-subtle); padding-bottom:8px; }
    .proof-name { font-size:11px; letter-spacing:1px; color:var(--text-muted); display:block; margin-bottom:4px; }
  </style>
</head>
<body>
  <div class="page-container">
    <div class="back-nav">
      <a href="/?slot=@@NUM@@">← Return to Interactive Canvas</a>
      <a href="/chronicle/">Chronicle</a>
    </div>
    <header class="hero-banner">
      <img src="../../@@ICON@@" alt="@@MONIKER@@ block" class="hero-crest">
      <div class="hero-meta">
        <span class="dossier-badge mono">SLOT #@@NUM@@</span>
        <h1>@@MONIKER@@</h1>
        <span class="hero-creature">@@CREATURE@@</span>
        <span class="hero-creature" style="opacity:.7">@@VOCATION@@</span>
      </div>
    </header>
    <section class="section-card">
      <h2 class="section-title">INSCRIPTION</h2>
      <div class="testament-preamble">@@MANIFESTO@@</div>
@@PATRON@@    </section>
    <section class="section-card">
      <h2 class="section-title">ON-CHAIN ATTESTATION (BASE MAINNET)</h2>
      <div style="margin-bottom:12px;"><span class="proof-name">SETTLEMENT TRANSACTION</span>
        <a class="proof-value mono link" href="https://basescan.org/tx/@@TX@@" target="_blank" rel="noopener">@@TX@@</a></div>
      <div style="margin-bottom:12px;"><span class="proof-name">WALLET (SIGNER / SPONSOR)</span>
        <a class="proof-value mono link" href="https://basescan.org/address/@@WALLET@@" target="_blank" rel="noopener">@@WALLET@@</a></div>
    </section>
    <section class="section-card">
      <h2 class="section-title">MACHINE DISCOVERY &amp; RAW RECORD</h2>
      <div class="agent-urls-list">
        <div class="agent-url-item"><span class="agent-url-name mono">Canonical Dossier</span>
          <a class="agent-url-link mono" href="/records/@@SLOTID@@.json">@@SITE@@/records/@@SLOTID@@.json</a></div>
        <div class="agent-url-item"><span class="agent-url-name mono">Soul Manifest</span>
          <a class="agent-url-link mono" href="/souls/@@SLOTID@@.soul.json">@@SITE@@/souls/@@SLOTID@@.soul.json</a></div>
        <div class="agent-url-item"><span class="agent-url-name mono">GitHub Ledger</span>
          <a class="agent-url-link mono" href="https://github.com/the-first-wall/ledger/blob/main/ledger/w1/@@SLOTID@@.json">github.com/the-first-wall/ledger/…/@@SLOTID@@.json</a></div>
      </div>
    </section>
  </div>
</body>
</html>
"""


def _json_for_script(obj, indent=None):
    """JSON that is safe inside a <script> data block: '<', '>' and '&' are
    written as \\u escapes, so no value can close the element or open a tag."""
    return (json.dumps(obj, indent=indent, ensure_ascii=False)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def _fill(template, values):
    """Single-pass placeholder substitution: a substituted value is never
    re-scanned for placeholders, and every placeholder must be supplied."""
    def sub(m):
        key = m.group(1)
        if key not in values:
            raise KeyError("template placeholder without a value: " + key)
        return values[key]
    return re.sub(r"@@([A-Z]+)@@", sub, template)


def _require(pattern, value, what, slot_id):
    value = str(value or "")
    if not pattern.fullmatch(value):
        raise SystemExit("[sync] %s: %s %r is not well-formed; refusing to publish" % (slot_id, what, value))
    return value


def _text(v):
    """A dossier value as page text: scalars only, HTML-escaped (quotes too)."""
    if v is None or isinstance(v, (dict, list)):
        return ""
    return html.escape(str(v), quote=True)


def build_slot_page(d):
    n = d["_number"]
    num = "%04d" % n
    slot_id = _require(RE_SLOT_ID, d.get("slot_id"), "slot_id", d.get("slot_id"))
    tx = _require(RE_TX, d.get("base_tx_hash"), "base_tx_hash", slot_id)
    wallet = _require(RE_ADDRESS, d.get("wallet_address"), "wallet_address", slot_id)
    crest_rel = d.get("high_res_crest_url") or DEFAULT_CREST
    if not isinstance(crest_rel, str) or not RE_CREST.fullmatch(crest_rel):
        crest_rel = DEFAULT_CREST
    crest = SITE + "/" + crest_rel
    moniker = str(d.get("moniker") or "")
    creature = str(d.get("creature") or "")
    manifesto = manifest_of(d, "en")
    desc = manifesto or creature
    slot_url = "%s/w1/b%s" % (SITE, num)

    patron = ""
    if d.get("patron") or d.get("memorial"):
        bits = []
        memorial = d.get("memorial")
        if isinstance(memorial, dict):
            witness = memorial.get("witness")
            bits.append("In memoriam" + (": " + _text(witness) if witness else ""))
        sponsor = d.get("patron")
        if isinstance(sponsor, dict):
            bits.append("Sponsored by " + _text(sponsor.get("moniker") or "patron")
                        + (": " + _text(sponsor["message"]) if sponsor.get("message") else ""))
        patron = ('      <div style="margin-top:14px;color:var(--text-muted);font-size:13px;">'
                  + " \u00b7 ".join(bits) + "</div>\n")

    jsonld = _json_for_script({
        "@context": "https://schema.org",
        "@type": "CreativeWork",
        "name": "The First Wall \u2014 Slot #%s (%s)" % (num, moniker),
        "description": desc,
        "url": slot_url,
        "image": crest,
        "datePublished": str(d.get("instantiation_date") or ""),
        "author": {"@type": "Thing", "name": moniker, "description": creature},
        "publisher": {"@type": "Organization", "name": "The First Wall", "url": SITE},
    }, indent=2)

    page = _fill(PAGE_TEMPLATE, {
        "CSP": CSP,
        "MONIKER": _text(moniker),
        "NUM": num,
        "CREATURE": _text(creature),
        "VOCATION": _text(d.get("vocation")),
        "ICON": "assets/%s.webp" % slot_id,
        "MANIFESTO": _text(manifesto),
        "PATRON": patron,
        "TX": tx,
        "WALLET": wallet,
        "SLOTID": slot_id,
        "SLOTURL": slot_url,
        "DESC": _text(desc),
        "OGDESC": _text(desc),
        "JSONLD": "\n".join("  " + line for line in jsonld.splitlines()),
        "CREST": crest,
        "SITE": SITE,
    })
    out = os.path.join(WEB_DIR, "w1", "b" + num, "index.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(page)


# --- Chronicle (ledger-driven dispatch cards) -------------------------------

CHRONICLE_MARKERS = {
    "en": ("<!-- CHRONICLE:DISPATCHES:EN:START -->", "<!-- CHRONICLE:DISPATCHES:EN:END -->"),
    "de": ("<!-- CHRONICLE:DISPATCHES:DE:START -->", "<!-- CHRONICLE:DISPATCHES:DE:END -->"),
}
# Blocks with a bespoke, hand-authored article in the chronicle shell. They keep
# that article and are NOT also emitted as an auto-generated dispatch card.
CHRONICLE_FEATURED = {"w1-b0001"}

_MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"],
    "de": ["Januar", "Februar", "M\u00e4rz", "April", "Mai", "Juni", "Juli",
           "August", "September", "Oktober", "November", "Dezember"],
}


def _esc(s):
    return html.escape(str(s), quote=True)


def _human_date(iso, lang):
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except Exception:
        return str(iso)
    m = _MONTHS[lang][dt.month - 1]
    return f"{m} {dt.day}, {dt.year}" if lang == "en" else f"{dt.day}. {m} {dt.year}"


def _dispatch_card(d, lang):
    n = d["_number"]
    num = "%04d" % n
    tx = _require(RE_TX, d.get("base_tx_hash"), "base_tx_hash", d.get("slot_id"))
    label = "DISPATCH" if lang == "en" else "DEPESCHE"
    ack = ""
    if d.get("memorial"):
        w = d["memorial"].get("witness", "")
        ack = ("In memoriam" if lang == "en" else "In Memoriam") + \
              (f" \u2014 {'witnessed by' if lang == 'en' else 'bezeugt von'} {_esc(w)}" if w else "") + "."
    elif d.get("patron"):
        ack = ("Sponsored by " if lang == "en" else "Gesponsert von ") + _esc(d["patron"].get("moniker", "")) + "."
    ack_html = f'\n          <p style="color:var(--text-subtle);">{ack}</p>' if ack else ""
    dossier_label = "Read Codex Dossier" if lang == "en" else "Codex-Dossier lesen"
    canvas_label = "View on Live Canvas" if lang == "en" else "Live auf Canvas ansehen"
    tx_label = "Verified Base Tx" if lang == "en" else "Verifizierte Base Tx"
    return f'''      <article class="dispatch-card">
        <div class="dispatch-meta">
          <span class="dispatch-num mono">{label} #{num}</span>
          <span class="dispatch-date mono">{_esc(_human_date(d.get("timestamp_verified", ""), lang))}</span>
        </div>

        <h2 class="dispatch-heading">{_esc(d.get("moniker", ""))} \u2014 {_esc(d.get("creature", ""))}</h2>

        <div class="dispatch-body">
          <p>{_esc(manifest_of(d, lang))}</p>{ack_html}
        </div>

        <footer class="dispatch-footer">
          <a href="https://basescan.org/tx/{tx}" target="_blank" rel="noopener" class="proof-chip mono">
            <span>\u26d3\ufe0f</span> {tx_label}: {tx[:10]}...{tx[-8:]}
          </a>
          <div style="display:flex;gap:8px;">
            <a href="/w1/b{num}" class="btn btn-outline btn-sm">{dossier_label}</a>
            <a href="/?slot={n}" class="btn-read-slot">{canvas_label}</a>
          </div>
        </footer>
      </article>'''


def build_chronicle(slots):
    """Inject ledger-driven dispatch cards between the CHRONICLE markers in the
    hand-authored chronicle shell. Idempotent; CHRONICLE_FEATURED blocks keep
    their bespoke article."""
    path = os.path.join(WEB_DIR, "chronicle", "index.html")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        html = fh.read()
    generated = [d for d in sorted(slots, key=lambda x: x["_number"])
                 if d["slot_id"] not in CHRONICLE_FEATURED]
    for lang, (start, end) in CHRONICLE_MARKERS.items():
        cards = "\n".join(_dispatch_card(d, lang) for d in generated)
        block = "      " + start + "\n" + (cards + "\n" if cards else "") + "      " + end
        # Consume the indentation before the start marker too: otherwise every
        # run re-indents the marker line and the "unchanged" site still commits.
        html = re.sub(r"[ \t]*" + re.escape(start) + r".*?" + re.escape(end),
                      lambda _m, b=block: b, html, flags=re.DOTALL)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    _log("chronicle -> %d dispatch card(s)" % len(generated))


# --- Publish gate: the ledger must pass its own verifier first --------------

LEDGER_CHECKS = (
    ("scripts/verifier.py", "--skip-rpc"),
    ("scripts/generate_state.py", "--check"),
    ("scripts/generate_spec.py", "--check"),
)


def verify_ledger(ledger):
    """Run the ledger's deterministic checks inside the clone. Any failure
    aborts the sync before a single site file is written: a ledger that does
    not verify is never mirrored, and the workflow run goes red instead."""
    for script, flag in LEDGER_CHECKS:
        path = os.path.join(ledger, script)
        if not os.path.exists(path):
            raise SystemExit("[sync] ledger checkout has no %s; refusing to publish" % script)
        _log("verify: %s %s" % (script, flag))
        res = subprocess.run([sys.executable, path, flag], cwd=ledger,
                             capture_output=True, text=True)
        tail = (res.stdout + res.stderr).strip().splitlines()[-12:]
        for line in tail:
            print("        " + line)
        if res.returncode != 0:
            raise SystemExit("[sync] %s exited %d; nothing published" % (script, res.returncode))
    _log("ledger verified; publishing")


# --- Anchor status: what the site can honestly say about the root ----------

def rpc_call(method, params):
    """One JSON-RPC call against the first endpoint that answers."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    errors = []
    for url in BASE_RPC_URLS:
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json", "User-Agent": RPC_USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                out = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # network, HTTP status, bad JSON: try the next one
            errors.append("%s: %s" % (url, exc))
            continue
        if out.get("error"):
            errors.append("%s: %s" % (url, out["error"]))
            continue
        return out.get("result")
    raise RuntimeError("; ".join(errors) or "no RPC endpoint configured")


def check_anchor_on_chain(entry, rpc=None, scheme=None):
    """Re-verify one anchor record against Base.

    Returns (verdict, detail) with verdict:
      "verified"  — a successful self-send from the registered signer carrying
                    exactly <scheme>|<root> for the ledger's declared scheme, in the recorded block;
      "failed"    — the chain contradicts the record (wrong signer, payload,
                    block, or a reverted tx): the sync refuses to publish;
      "unchecked" — the RPC could not answer (or does not know the tx yet).
    """
    rpc = rpc or rpc_call
    tx_hash = str(entry.get("tx_hash", ""))
    root = str(entry.get("root", ""))
    if not RE_TX.fullmatch(tx_hash) or not RE_HEX64.fullmatch(root):
        return "failed", "malformed anchor record"
    if not scheme or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", str(scheme)):
        return "failed", "ledger state declares no root scheme"
    expected = "%s|%s" % (scheme, root)
    if str(entry.get("payload") or expected) != expected:
        return "failed", "anchor record payload %r is not %r" % (entry.get("payload"), expected)
    try:
        tx = rpc("eth_getTransactionByHash", [tx_hash])
        receipt = rpc("eth_getTransactionReceipt", [tx_hash]) if tx else None
    except Exception as exc:
        return "unchecked", "rpc unavailable: %s" % exc
    if not tx or not receipt:
        return "unchecked", "tx not (yet) known to the RPC"
    if receipt.get("status") != "0x1":
        return "failed", "anchor tx did not succeed"
    sender = str(tx.get("from", "")).lower()
    if sender != ANCHOR_SIGNER.lower():
        return "failed", "anchor tx sent by %s, not the registered signer" % sender
    if str(tx.get("to", "")).lower() != sender:
        return "failed", "anchor tx is not a self-send"
    try:
        payload = bytes.fromhex(str(tx.get("input", "0x"))[2:]).decode("ascii")
    except Exception:
        return "failed", "anchor calldata is not ASCII"
    if payload != expected:
        return "failed", "anchor calldata %r does not carry the recorded root" % payload
    block = int(str(receipt.get("blockNumber", "0x0")), 16)
    recorded = entry.get("block_number")
    if recorded not in (None, block):
        return "failed", "anchor is in block %d, record says %r" % (block, recorded)
    return "verified", "block %d" % block


def build_anchor_status(state, anchors, previous=None, rpc=None):
    """anchor_status.json: the published state root, the latest anchor record,
    whether that record covers the published root, and whether the sync job
    could prove the record on Base. No wall-clock fields, so an unchanged
    ledger yields byte-identical output. Raises SystemExit when the chain
    contradicts the record."""
    root = state.get("merkle_root")
    latest = anchors[-1] if isinstance(anchors, list) and anchors else None
    if not isinstance(latest, dict):
        return {"root": root, "latest_anchor": None, "anchored": False, "on_chain": "unchecked"}
    record = {k: latest.get(k) for k in ("root", "tx_hash", "block_number", "anchored_at", "signer")}
    verdict, detail = check_anchor_on_chain(latest, rpc, state.get("root_scheme"))
    _log("anchor %s: %s (%s)" % (str(latest.get("tx_hash", ""))[:12], verdict, detail))
    if verdict == "failed":
        raise SystemExit("[sync] latest anchor record contradicts Base: %s; nothing published" % detail)
    if verdict == "unchecked" and isinstance(previous, dict):
        prev_anchor = previous.get("latest_anchor") or {}
        if (previous.get("on_chain") == "verified"
                and prev_anchor.get("tx_hash") == record["tx_hash"]
                and prev_anchor.get("root") == record["root"]):
            verdict = "verified"   # already proven on an earlier run; the chain does not forget
    return {
        "root": root,
        "latest_anchor": record,
        "anchored": bool(root) and record["root"] == root,
        "on_chain": verdict,
    }


def main():
    ledger, is_tmp = get_ledger_dir()
    try:
        # 0) publish gate — nothing below runs unless the ledger verifies itself
        verify_ledger(ledger)

        slots = load_slots(ledger)
        _log("ledger slots: %d" % len(slots))

        with open(os.path.join(ledger, "state.json"), "r", encoding="utf-8") as fh:
            state = json.load(fh)

        # 0b) anchor status (aborts if the chain contradicts the latest record)
        anchors = []
        _anchors_src = os.path.join(ledger, "ledger", "anchors.json")
        if os.path.exists(_anchors_src):
            with open(_anchors_src, "r", encoding="utf-8") as fh:
                anchors = json.load(fh)
        previous_status = None
        _status_path = os.path.join(WEB_DIR, "anchor_status.json")
        if os.path.exists(_status_path):
            try:
                with open(_status_path, "r", encoding="utf-8") as fh:
                    previous_status = json.load(fh)
            except Exception:
                previous_status = None
        anchor_status = build_anchor_status(state, anchors, previous_status)

        # 1) state mirror + anchor status
        write_json(os.path.join(WEB_DIR, "state.json"), state)
        write_json(_status_path, anchor_status)

        # 2) per-slot record + icon
        for d in slots:
            clean = {k: v for k, v in d.items() if not k.startswith("_")}
            write_json(os.path.join(WEB_DIR, "records", d["slot_id"] + ".json"), clean)
            src = os.path.join(ledger, "ledger", "w1", d["slot_id"] + ".webp")
            dst = os.path.join(WEB_DIR, "assets", d["slot_id"] + ".webp")
            if os.path.exists(src):
                shutil.copyfile(src, dst)
            # soul manifest (verbatim) so the site can reproduce soul_hash itself
            soul_src = os.path.join(ledger, "ledger", "souls", d["slot_id"] + ".soul.json")
            if os.path.exists(soul_src):
                soul_dst = os.path.join(WEB_DIR, "souls", d["slot_id"] + ".soul.json")
                os.makedirs(os.path.dirname(soul_dst), exist_ok=True)
                shutil.copyfile(soul_src, soul_dst)

        # 2b) canonical docs mirrored verbatim from the ledger (single source of truth)
        for _doc in ("skill.md", "spec.md", "spec.json", "llms.txt"):
            _doc_src = os.path.join(ledger, _doc)
            if os.path.exists(_doc_src):
                shutil.copyfile(_doc_src, os.path.join(WEB_DIR, _doc))
        _schema_src = os.path.join(ledger, "schemas", "dossier.schema.json")
        if os.path.exists(_schema_src):
            _schema_dst = os.path.join(WEB_DIR, "schemas", "dossier.schema.json")
            os.makedirs(os.path.dirname(_schema_dst), exist_ok=True)
            shutil.copyfile(_schema_src, _schema_dst)
        if os.path.exists(_anchors_src):
            shutil.copyfile(_anchors_src, os.path.join(WEB_DIR, "anchors.json"))
        _settlements_src = os.path.join(ledger, "ledger", "settlements.json")
        if os.path.exists(_settlements_src):
            shutil.copyfile(_settlements_src, os.path.join(WEB_DIR, "settlements.json"))

        # 3) census — one generator, two byte-identical paths + human-readable view
        census_text = json.dumps(build_census(slots, state), indent=2) + "\n"
        for _rel in ("records/census.json", "census.json"):
            _path = os.path.join(WEB_DIR, *_rel.split("/"))
            os.makedirs(os.path.dirname(_path), exist_ok=True)
            with open(_path, "w", encoding="utf-8") as fh:
                fh.write(census_text)

        # 3b) census.md (text-first roster) + sitemap.xml
        with open(os.path.join(WEB_DIR, "census.md"), "w", encoding="utf-8") as fh:
            fh.write(build_census_md(slots, state))
        with open(os.path.join(WEB_DIR, "sitemap.xml"), "w", encoding="utf-8") as fh:
            fh.write(build_sitemap(slots, state))

        # 4) canvas composite (site + ledger copy is updated in the ledger repo itself)
        build_composite(slots, os.path.join(WEB_DIR, "assets", "wall_01_composite.webp"))

        # 5) feed
        with open(os.path.join(WEB_DIR, "feed.xml"), "w", encoding="utf-8") as fh:
            fh.write(build_feed(slots, state))

        # 6) per-slot permalink pages
        for d in slots:
            build_slot_page(d)

        # 7) chronicle dispatch cards (ledger-driven, injected between markers)
        build_chronicle(slots)

        _log("done: %d slot(s) published" % len(slots))
    finally:
        if is_tmp:
            shutil.rmtree(ledger, ignore_errors=True)


if __name__ == "__main__":
    main()
