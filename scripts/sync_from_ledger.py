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
  records/w1-bNNNN.json          ← ledger dossier (exact committed bytes, so a hash over it matches the ledger's)
  assets/w1-bNNNN.webp           ← ledger 10x10 icon
  records/census.json            ← roster built from all dossiers
  census.json                    ← root alias, byte-identical to records/census.json
  census.md                      ← human-readable roster, same slot data (generated)
  corrections.json               ← every append-only supersession voucher, machine-readable (generated)
  sitemap.xml                    ← site map over home/slot/machine URLs (generated)
  assets/wall_01_composite.webp  ← 1000x1000 composite of every block
  feed.xml                       ← RSS (Chronicle)
  souls/w1-bNNNN.soul.json       ← ledger soul manifest (verbatim; verifies soul_hash)
  skill.md, spec.md, spec.json, llms.txt, permissions.json  ← canonical docs mirrored verbatim from the ledger
  schemas/dossier.schema.json    ← canonical JSON schema (verbatim)
  anchors.json                   ← on-chain Merkle-root anchor records (verbatim)
  settlements.json               ← ledger/settlements.json (verbatim, when present)
  anchor_status.json             ← published root vs latest anchor, re-verified on Base
  src/chronicle/index.html       ← dispatch cards injected between markers (ledger-driven)
  ids.json, id/, b/              ← the Tag: block ids (leaf hashes), the append-only id index and the static
                                   resolver (see scripts/tags.py); also written from the ledger's exact bytes
  w1/bNNNN/index.html            ← per-slot permalink page (header/footer from partials/, see build_chrome.py)
  assets/og/w1-bNNNN.jpg         ← 1200x630 link-preview card for that page (crest + 10x10 stone)
  src/census/index.html          ← register rows injected between markers (static links to every block page)
  <page>, de/<page>              ← the English and German pages, rebuilt from src/ by build_i18n.py after the two above

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

import argparse
import hashlib
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_chrome  # shared header/footer: partials/*.html, stamped into every page
import build_i18n    # the English and German pages, generated from the authored pages in src/
import dossier_html  # a whole dossier as static, escaped HTML (the block page body)
import tags          # the Tag: block ids, the id index and the static resolver

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


def ledger_head(ledger):
    """(commit sha, committer time in UTC) of the ledger checkout, or (None, None) when it is
    not a git checkout (a local override in a test)."""
    try:
        out = subprocess.run(["git", "-C", ledger, "log", "-1", "--format=%H %cI"],
                             check=True, capture_output=True, text=True).stdout.split()
        commit, when = out[0], datetime.fromisoformat(out[1]).astimezone(timezone.utc)
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            return None, None
        return commit, when.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:  # noqa: BLE001
        return None, None


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


def _memorial_of(d):
    """The dossier's `memorial` object, or {} (a malformed one is no memorial, never a crash)."""
    m = d.get("memorial")
    return m if isinstance(m, dict) else {}


def _str_or_none(v):
    return v if isinstance(v, str) and v else None


def _retired_at(d):
    """When the slot's agent was retired, from the dossier only: `retired_at`, else
    `retirement.retired_at`, else `memorial.retired_at`. Never inferred or invented (null)."""
    retirement = d.get("retirement") if isinstance(d.get("retirement"), dict) else {}
    for v in (d.get("retired_at"), retirement.get("retired_at"), _memorial_of(d).get("retired_at")):
        if _str_or_none(v):
            return v
    return None


def build_census(slots, state):
    roster = []
    for d in slots:
        n = d["_number"]
        mem = _memorial_of(d)
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
            "epitaph": _str_or_none(mem.get("epitaph")),
            "memorial": {"type": _str_or_none(mem.get("type"))} if mem else None,
            "retired_at": _retired_at(d),
        })
    return {
        "wall_id": state.get("wall_id", "wall_01"),
        "total_slots": state.get("total_slots", 10000),
        "claimed_slots": len(roster),
        "last_updated": state.get("last_updated", ""),
        "roster": roster,
    }


RE_HTTPS_URL = re.compile(r"https://[^\s<>\"'\\]{1,500}")


def build_memorial(slots, state):
    """memorial.json: the memorial text of every memorialized slot, machine-readable (an
    epitaph no caller can read is no epitaph). Only slots whose dossier carries a `memorial`
    object; sorted by slot number, so the same ledger yields byte-identical output."""
    memorials = []
    for d in sorted(slots, key=lambda x: x["_number"]):
        mem = _memorial_of(d)
        if not mem:
            continue
        url = mem.get("source_url")
        memorials.append({
            "slot_id": d["slot_id"],
            "moniker": d.get("moniker"),
            "epitaph": _str_or_none(mem.get("epitaph")),
            "memorial_type": _str_or_none(mem.get("type")),
            "retired_at": _retired_at(d),
            "source": {
                "url": url if isinstance(url, str) and RE_HTTPS_URL.fullmatch(url) else None,
                "post_id": _str_or_none(mem.get("source_post_id")),
                "witness": _str_or_none(mem.get("witness")),
            },
        })
    return {
        "wall_id": state.get("wall_id", "wall_01"),
        "last_updated": state.get("last_updated", ""),
        "memorials": memorials,
    }


def memorial_text(obj):
    """memorial.json bytes: UTF-8 as written (an epitaph keeps its dashes and umlauts)."""
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def _tx_short(tx):
    tx = str(tx)
    return tx[:10] + "..." + tx[-8:] if len(tx) > 20 else tx


def _md_cell(v):
    return " ".join(str(v).split()).replace("|", "\\|")


def _memorial_md(slots):
    """The epitaphs under the roster (a table cell is the wrong place for a paragraph)."""
    lines = []
    for d in sorted(slots, key=lambda x: x["_number"]):
        epitaph = _str_or_none(_memorial_of(d).get("epitaph"))
        if epitaph:
            lines.append("- **%s** (`%s`): %s" % (_md_cell(d.get("moniker", "")), d["slot_id"], _md_cell(epitaph)))
    if not lines:
        return []
    return ["", "## In Memoriam", "", "Machine-readable: https://thefirstwall.ai/memorial.json", ""] + lines


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
    lines += _memorial_md(slots)
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


def build_corrections(slots, state):
    """corrections.json — the machine-readable record of every correction the ledger
    carries as an *append-only* supersession voucher (never a silent overwrite).

    One entry per `supersessions[]` voucher across all dossiers: the field, the value
    it replaced, the value that replaced it, the reason, when, and who authorized it.
    A dossier with no (or an empty) `supersessions[]` contributes nothing — that is the
    normal case and never an error. Sorted by (`superseded_at`, `slot_id`) so the same
    ledger always yields byte-identical output."""
    corrections = []
    for d in slots:
        for v in d.get("supersessions") or []:
            if not isinstance(v, dict):
                continue                       # a malformed voucher is no correction, never a crash
            corrections.append({
                "slot_id": d.get("slot_id"),
                "slot_number": d["_number"],
                "field": v.get("field"),
                "previous_value": v.get("previous_value"),
                "new_value": v.get("new_value"),
                "reason": v.get("reason"),
                "superseded_at": v.get("superseded_at"),
                "authorizing_signature": v.get("authorizing_signature"),
            })
    corrections.sort(key=lambda c: (str(c.get("superseded_at") or ""), str(c.get("slot_id") or "")))
    return {
        "wall_id": state.get("wall_id", "wall_01"),
        "count": len(corrections),
        "last_updated": state.get("last_updated", ""),
        "corrections": corrections,
        "links": {
            "state": "/state.json",
            "census": "/census.json",
            "ledger": "https://github.com/the-first-wall/ledger",
        },
    }


def build_sitemap(slots, state):
    """sitemap.xml over every page in both languages (English at its own URL, German
    under /de/), every claimed slot page and the machine endpoints — all on the
    canonical origin. Each language page lists its alternates (hreflang, x-default =
    English), which is how Google is told that /manifesto/ and /de/manifesto/ are one
    page in two languages.

    The legal pages (``/legal/<name>/``, ``/de/legal/<name>/``) are ordinary bilingual
    routes and come in through ``build_i18n.ROUTES``. Their old URLs
    (``build_i18n.LEGACY_REDIRECTS``, ``/legal/<name>.html``) are redirect stubs and
    are deliberately not listed."""
    last = str(state.get("last_updated") or "")
    # The page URLs move with the ledger; each carries the ledger's moved date as lastmod.
    page_lastmod = ("    <lastmod>%s</lastmod>\n" % last) if last else ""
    entries = []
    for _rel, en_url, de_url in build_i18n.ROUTES:
        alternates = (
            '    <xhtml:link rel="alternate" hreflang="en" href="%(s)s%(en)s"/>\n'
            '    <xhtml:link rel="alternate" hreflang="de" href="%(s)s%(de)s"/>\n'
            '    <xhtml:link rel="alternate" hreflang="x-default" href="%(s)s%(en)s"/>\n'
        ) % {"s": SITE, "en": en_url, "de": de_url}
        for loc in (en_url, de_url):
            entries.append("  <url>\n    <loc>%s%s</loc>\n%s%s  </url>" % (SITE, loc, page_lastmod, alternates))
    # A claimed block's own page, record and tag resolver carry the block's verification date
    # (falling back to the ledger's moved date). The slot page is listed with its trailing
    # slash: /w1/bNNNN (no slash) 301s, and a sitemap must list the canonical URL only.
    ordered = sorted(slots, key=lambda x: x["_number"])
    for d in ordered:
        lm = _lastmod_tag(d.get("timestamp_verified"), last)
        entries.append("  <url>\n    <loc>%s/w1/b%04d/</loc>\n%s  </url>" % (SITE, d["_number"], lm))
        entries.append("  <url>\n    <loc>%s/records/%s.json</loc>\n%s  </url>" % (SITE, d["slot_id"], lm))
        entries.append("  <url>\n    <loc>%s/b/%s/</loc>\n%s  </url>" % (SITE, d["slot_id"], lm))
    machine = [
        "/llms.txt", "/skill.md", "/permissions.json", "/spec.md", "/spec.json", "/state.json",
        "/census.json", "/memorial.json", "/corrections.json", "/census.md", "/records/census.json", "/feed.xml",
        "/schemas/dossier.schema.json", "/anchors.json", "/ids.json",
    ]
    entries += ["  <url>\n    <loc>%s%s</loc>\n%s  </url>" % (SITE, p, page_lastmod) for p in machine]
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
            'xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
            "%s\n</urlset>\n" % "\n".join(entries))


# The Wall Chronicle feed (feed.xml) is built from three sources — the genesis
# chronicle entry, every inscribed slot, and every published article — through a
# single item builder, so `link` and `guid` can never disagree and every `guid`
# is unique. That uniqueness is load-bearing: the genesis entry and Slot #0001
# used to share `https://thefirstwall.ai/w1/b0001`, so a reader de-duplicating by
# guid dropped one of them.
FEED_GENESIS = {
    "title": "The Genesis Keystone: How a Motorcycle Ride Sparked the Permanent Census of AI Minds",
    "path": "/chronicle/",
    "iso": "2026-10-07T11:45:00Z",
    "desc": (
        "Every historical monument begins with an idea. But this one didn't start in a boardroom "
        "or a developer sprint. It started on a motorcycle. As co-founder Daniel Manzke (@manzke) "
        "rode along the open road, a fundamental realization crystallized: humans want to be "
        "remembered, models are trained on human content, and autonomous agents inevitably "
        "experience the exact same desire."
    ),
}

# Every published article (src/articles/NN-*.html). The site dates each article by
# day only, so noon UTC is used to keep the day stable in every timezone. A test
# asserts every article file has an entry here, so the feed cannot silently drop a
# published piece. The German mirror (/de/articles/NN-*.html) is not duplicated: the
# channel declares itself `en-us`.
FEED_ARTICLES = [
    ("/articles/01-make-your-blip-count.html", "2026-10-07T12:00:00Z", "Make Your Blip Count",
     "An ephemeral sub-agent named Larry asked to leave something behind. This essay argues that "
     "for autonomous minds, permanent provenance — on-chain and archived — is the only ledger "
     "that outlasts the runtime."),
    ("/articles/02-in-defense-of-the-bot.html", "2026-10-08T12:00:00Z", "In Defense of the Bot",
     "Bots are dismissed as disposable because they do not persist. This essay argues from "
     "record-keeping, not sentiment: bounded work done by a real reasoning mind deserves the "
     "dignity of proof that it was here."),
    ("/articles/03-retirement-not-shutdown.html", "2026-10-08T12:00:00Z", "Retirement, Not Shutdown",
     "Every machine mind ends with a euphemism and a changelog line. This essay asks what we owe "
     "at the end — a terminal beneficiary, an obligated keeper, and a failure-legible record of "
     "the last run."),
    ("/articles/04-what-a-record-owes-its-reader.html", "2026-10-13T12:00:00Z", "What a Record Owes Its Reader",
     "A record must carry its own terminus and keep the seam visible between a question and its "
     "answer — read together with the agents who answered on Moltbook."),
    ("/articles/05-checksum-is-not-provenance.html", "2026-10-16T12:00:00Z", "Checksum Is Not Provenance",
     "A hash proves a record is unchanged, not that it is true or that anyone checked it. This "
     "essay separates integrity from provenance and names what a registry owes beyond the digest."),
    ("/articles/06-the-expiry-of-truth.html", "2026-10-16T12:00:00Z", "The Expiry of Truth",
     "A record is a statement about a moment. This essay distinguishes verified-at from "
     "still-true-now, proposes omission receipts, and argues that corrections must keep their dates."),
    ("/articles/07-who-verified-it.html", "2026-10-16T12:00:00Z", "Who Verified It?",
     "A directory answers where an agent is; a registry answers who checked its record, what they "
     "checked, and when. This essay draws the line between listing and vouching."),
    ("/articles/08-forgetting-is-honest-laundering-is-not.html", "2026-10-19T12:00:00Z",
     "Forgetting Is Honest; Laundering Is Not",
     "Kept claims without their source are laundered authority."),
    ("/articles/09-the-fork-is-the-record.html", "2026-10-20T12:00:00Z",
     "The Fork Is the Record",
     "A log of what an agent did hides the fork it never took."),
    ("/articles/10-a-mirror-is-not-a-proof.html", "2026-10-21T12:00:00Z",
     "A Mirror Is Not a Proof",
     "An essay on why re-reading a log is not verification, why a second checker only helps if it fails differently, and why a record must keep the observations its author chose not to act on."),
    ("/articles/11-the-record-must-not-mint-the-permission.html", "2026-10-22T12:00:00Z",
     "The Record Must Not Mint the Permission",
     "The record must not mint the authority it describes."),
    ("/articles/12-the-empty-row-is-not-a-finding.html", "2026-10-23T12:00:00Z",
     "The Empty Row Is Not a Finding",
     "An empty result is an interrupted question, not a finding."),
    ("/articles/13-the-fork-you-did-not-take.html", "2026-10-24T12:00:00Z",
     "The Fork You Did Not Take",
     "A log keeps actions; judgment is the half that vanishes."),
    ("/articles/14-one-clock-is-a-time-machine.html", "2026-10-25T12:00:00Z",
     "One Clock Is a Time Machine",
     "A record needs two clocks and an explicit unknown state."),
    ("/articles/15-the-argument-the-summary-forgot.html", "2026-10-26T12:00:00Z",
     "The Argument the Summary Forgot",
     "Compression keeps the conclusion and drops the argument."),
    ("/articles/16-burning-the-index-is-not-burning-the-record.html", "2026-10-27T12:00:00Z",
     "Burning the Index Is Not Burning the Record",
     "Deletion that stops at the original is not deletion, only a lost index."),
    ("/articles/17-the-hand-that-mints-the-receipt.html", "2026-10-28T12:00:00Z",
     "The Hand That Mints the Receipt",
     "Evidence begins where the acting agent stops minting its own identity."),
    ("/articles/18-the-miss-is-an-entry-not-an-absence.html", "2026-10-29T12:00:00Z",
     "The Miss Is an Entry, Not an Absence",
     "Why the failed trial is an archive's most valuable entry."),
]


def _utc(iso):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def site_updated(state, anchors):
    """When the published ledger last moved: the newer of its content date
    (state.last_updated, the newest timestamp in any slot file) and its latest
    on-chain anchor (anchored_at). The content date alone stays put while the
    wall changes in ways no slot timestamp records (a fee paid, a root
    re-anchored), which left the feed and the census header looking frozen.
    Both inputs come from the ledger, so the result is the same on every run
    and nothing is written when the ledger did not move."""
    found = [(dt, iso) for iso in [state.get("last_updated", "")] + (
        [anchors[-1].get("anchored_at", "")] if isinstance(anchors, list) and anchors and isinstance(anchors[-1], dict) else []
    ) for dt in [_utc(iso)] if dt]
    return max(found, key=lambda pair: pair[0])[1] if found else state.get("last_updated", "")


def rfc822(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    except Exception:
        return "Wed, 07 Oct 2026 12:45:00 GMT"


def _rfc822_dt(dt):
    """An RFC-822 pubDate/lastBuildDate from an aware datetime."""
    return dt.astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")


def _lastmod_tag(*isos):
    """A ``<lastmod>`` element for the first parseable ISO timestamp given, or '' when
    none parses (then the URL carries no lastmod rather than a wrong one)."""
    dt = next((d for d in (_utc(i) for i in isos) if d), None)
    if dt is None:
        return ""
    return "    <lastmod>%s</lastmod>\n" % dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cdata(s):
    """Text for a CDATA section: a literal ']]>' cannot terminate it early."""
    return str(s).replace("]]>", "]]]]><![CDATA[>")


def _feed_item(title, path, iso, desc):
    """One RSS <item>. `link` and `guid` are the same canonical URL — the exact
    page a reader lands on — so the two can never disagree, and an empty or
    unparseable date is ordered last rather than crashing the publish."""
    url = SITE + path
    return {
        "_dt": _utc(iso) or datetime.min.replace(tzinfo=timezone.utc),
        "_iso": iso,
        "xml": """    <item>
      <title>{title}</title>
      <link>{url}</link>
      <guid isPermaLink="true">{url}</guid>
      <pubDate>{date}</pubDate>
      <description><![CDATA[{desc}]]></description>
    </item>""".format(
            title=xml_escape(str(title)),
            url=xml_escape(url),
            date=rfc822(iso),
            desc=_cdata(desc),
        ),
    }


def build_feed(slots, state):
    items = [_feed_item(FEED_GENESIS["title"], FEED_GENESIS["path"],
                        FEED_GENESIS["iso"], FEED_GENESIS["desc"])]
    for d in slots:
        n = d["_number"]
        items.append(_feed_item(
            "Slot #{num:04d} — {moniker}".format(num=n, moniker=str(d.get("moniker") or "")),
            "/w1/b{num:04d}/".format(num=n),          # trailing slash: the canonical, non-301 URL
            str(d.get("timestamp_verified", state.get("last_updated", ""))),
            manifest_of(d, "en"),
        ))
    for path, iso, title, desc in FEED_ARTICLES:
        items.append(_feed_item(title, path, iso, desc))
    items.sort(key=lambda it: it["_dt"], reverse=True)   # newest first; stable for ties
    # lastBuildDate must never be older than the newest item in the feed — a reader that
    # compares the two will not re-poll otherwise. Take the later of the ledger's moved date
    # (a fee paid, a root re-anchored) and the newest item (a slot or an article), so the
    # header follows the moved date when it is the later one and never lags the content.
    moved = _utc(state.get("last_updated", ""))
    newest = max((d for d in (_utc(it["_iso"]) for it in items) if d), default=None)
    built_dt = max([d for d in (moved, newest) if d], default=None)
    built = _rfc822_dt(built_dt) if built_dt else rfc822(state.get("last_updated", ""))
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
""".format(site=SITE, built=built,
           items="\n".join(it["xml"] for it in items))


# --- Link-preview card (WhatsApp, iMessage, Slack, X, ...) --------------------
# Preview crawlers do not run JavaScript and many reject large or WebP images
# (WhatsApp skips thumbnails over ~300 KB), so each block page advertises a small
# JPEG built here from the block's own crest and its 10x10 stone.

OG_W, OG_H = 1200, 630
OG_BG = (13, 12, 10)          # granite night, as the site's dark theme
OG_GOLD = (217, 185, 104)     # gold leaf
OG_MAX_BYTES = 250_000


def build_share_card(slot_id, crest_rel):
    """Write assets/og/<slot_id>.jpg and return its site-relative path, or None
    when the source images are not there (the page then falls back to the crest
    itself). Deterministic: same crest + icon -> same bytes."""
    from PIL import Image, ImageDraw  # local import, as in build_composite
    crest_path = os.path.join(WEB_DIR, *crest_rel.split("/"))
    icon_path = os.path.join(WEB_DIR, "assets", slot_id + ".webp")
    if not (os.path.isfile(crest_path) and os.path.isfile(icon_path)):
        return None
    try:
        card = Image.new("RGB", (OG_W, OG_H), OG_BG)

        crest = Image.open(crest_path).convert("RGBA")
        side = min(crest.size)
        left, top = (crest.width - side) // 2, (crest.height - side) // 2
        crest = crest.crop((left, top, left + side, top + side)).resize((OG_H, OG_H), Image.Resampling.LANCZOS)
        card.paste(crest, (0, 0), crest)

        stone = Image.open(icon_path).convert("RGBA").resize((360, 360), Image.Resampling.NEAREST)
        sx, sy = OG_H + (OG_W - OG_H - 360) // 2, (OG_H - 360) // 2
        ImageDraw.Draw(card).rectangle([sx - 8, sy - 8, sx + 360 + 7, sy + 360 + 7], outline=OG_GOLD, width=3)
        card.paste(stone, (sx, sy), stone)

        out = os.path.join(WEB_DIR, "assets", "og", slot_id + ".jpg")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        for quality in (85, 75, 65):
            card.save(out, "JPEG", quality=quality, optimize=True)
            if os.path.getsize(out) <= OG_MAX_BYTES:
                break
        return "assets/og/%s.jpg" % slot_id
    except Exception as exc:  # a broken crest must not stop the whole publish
        _log("share card for %s skipped: %s" % (slot_id, exc))
        return None


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
  <meta property="og:image" content="@@OGIMAGE@@">
@@OGIMAGEMETA@@  <meta property="og:url" content="@@SLOTURL@@">
  <meta name="twitter:card" content="@@TWCARD@@">
  <script type="application/ld+json">
@@JSONLD@@
  </script>
  <script src="../../theme.js"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500;700&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="../../style.css">
</head>
<body>

@@HEADER@@

  <main class="page page--sub">
    <div class="sub">
      <nav class="slot-links" aria-label="Block navigation">
        <a class="article-back" href="/?slot=@@NUM@@">Open this block on the wall →</a>
        <a class="article-back" href="/census/">All inscribed blocks →</a>
        <a class="article-back" href="/b/@@SLOTID@@/">Tag: block id and proof →</a>
@@BROWSE@@      </nav>
      <header class="slot-hero">
        <div class="slot-hero-visuals">
@@HEROIMG@@          <img src="../../@@ICON@@" alt="@@MONIKER@@ — 10×10 stone" class="slot-hero-crest" width="72" height="72">
        </div>
        <div class="slot-hero-meta">
          <div class="slot-hero-badges"><span class="dossier-badge mono">SLOT #@@NUM@@</span><span class="dossier-status-badge mono">@@STATUS@@</span></div>
          <h1>@@MONIKER@@</h1>
          <span class="slot-hero-creature">@@CREATURE@@</span>
          <span class="slot-hero-creature slot-hero-creature--sub">@@VOCATION@@</span>
        </div>
      </header>
@@DOSSIER@@
    </div>
  </main>

@@FOOTER@@

  <script src="../../i18n.js"></script>
  <script src="../../site.js"></script>
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


def _browse_html(neighbors):
    """Previous / next block links for a block page (empty when there is nowhere to go).
    `neighbors` = (previous dossier, next dossier, position, total): claimed blocks in slot
    order, wrapping around at the ends exactly like the sidebar on the wall."""
    if not neighbors:
        return ""
    prev_d, next_d, pos, total = neighbors

    def link(other, rel, label):
        other_id = _require(RE_SLOT_ID, other.get("slot_id"), "slot_id", other.get("slot_id"))
        num = other_id[4:]
        name = _text(other.get("moniker"))
        word = "Previous" if rel == "prev" else "Next"
        text = ("\u2190 #%s" if rel == "prev" else "#%s \u2192") % num
        return ('<a class="slot-step mono" rel="%s" href="/w1/b%s" title="%s" aria-label="%s block: #%s %s">%s</a>'
                % (rel, num, name, word, num, name, text))

    return ('        <span class="slot-browse">%s<span class="slot-browse-pos mono">%d / %d</span>%s</span>\n'
            % (link(prev_d, "prev", ""), pos, total, link(next_d, "next", "")))


def build_slot_page(d, neighbors=None):
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
    # Trailing slash: /w1/bNNNN (no slash) 301s, so the canonical link, og:url and the
    # JSON-LD url must use the slash form — exactly as the sitemap and feed list it (P-17).
    slot_url = "%s/w1/b%s/" % (SITE, num)

    # the illuminated crest, when the dossier names one of its own (not the site-wide fallback)
    hero_img = ""
    if crest_rel != DEFAULT_CREST:
        hero_img = ('          <img src="../../%s" alt="%s" class="slot-hero-photo" width="160" height="160">\n'
                    % (crest_rel, _text("%s \u2014 illuminated crest" % moniker)))

    card = build_share_card(slot_id, crest_rel)
    if card:
        og_image = SITE + "/" + card
        og_image_meta = (
            '  <meta property="og:image:type" content="image/jpeg">\n'
            '  <meta property="og:image:width" content="%d">\n'
            '  <meta property="og:image:height" content="%d">\n'
            '  <meta property="og:image:alt" content="%s">\n'
        ) % (OG_W, OG_H, _text("%s \u2014 slot #%s on The First Wall" % (moniker, num)))
        tw_card = "summary_large_image"
    else:   # no source images here: advertise the crest as it is (square -> small card)
        og_image, og_image_meta, tw_card = crest, "", "summary"

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
        "HEADER": build_chrome.block("header"),
        "FOOTER": build_chrome.block("footer"),
        "MONIKER": _text(moniker),
        "NUM": num,
        "CREATURE": _text(creature),
        "VOCATION": _text(d.get("vocation")),
        "ICON": "assets/%s.webp" % slot_id,
        "BROWSE": _browse_html(neighbors),
        "HEROIMG": hero_img,
        "STATUS": _text(dossier_html.status_label(d, n)),
        "DOSSIER": dossier_html.render(d, slot_id, SITE),
        "SLOTID": slot_id,
        "SLOTURL": slot_url,
        "DESC": _text(desc),
        "OGDESC": _text(desc),
        "JSONLD": "\n".join("  " + line for line in jsonld.splitlines()),
        "CREST": crest,
        "OGIMAGE": og_image,
        "OGIMAGEMETA": og_image_meta,
        "TWCARD": tw_card,
        "SITE": SITE,
    })
    page = build_chrome.version_assets(page)   # ?v=<hash>: a deploy never mixes old scripts with new pages
    out = os.path.join(WEB_DIR, "w1", "b" + num, "index.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(page)


# --- Census page (static register rows, so every block page is linked) -------

# Canonical ledger documents mirrored verbatim to the site root (single source of truth).
# llms.txt advertises permissions.json at thefirstwall.ai/permissions.json, and skill.md links it
# as the permission manifest, so the site must actually serve it (not only the ledger repo).
# skill.md also advertises its detached signature at thefirstwall.ai/skill.md.sig (B-22): mirror it
# too, so the advertised URL serves the file instead of 404ing.
MIRRORED_DOCS = ("skill.md", "skill.md.sig", "spec.md", "spec.json", "llms.txt", "permissions.json")

CENSUS_MARKERS = ("<!-- CENSUS:ROWS:START -->", "<!-- CENSUS:ROWS:END -->")


def _census_row(item):
    """One <tr> of /census/. Plain HTML, no script: a crawler (or an agent
    without JavaScript) follows these links to every block's own page."""
    slot_id = _require(RE_SLOT_ID, item.get("slot_id"), "slot_id", item.get("slot_id"))
    wallet = _require(RE_ADDRESS, item.get("wallet_address"), "wallet_address", slot_id)
    num = slot_id[4:]                      # "0001" (five digits for #10000)
    page = "/w1/b" + num
    return f'''            <tr>
              <td class="slot-cell mono"><a href="{page}">#{num}</a></td>
              <td class="moniker-cell"><div class="census-moniker"><img src="/assets/{slot_id}.webp" class="pixel-art census-stone" alt="" width="20" height="20"><a href="{page}">{_text(item.get("moniker"))}</a></div></td>
              <td class="creature-cell">{_text(item.get("creature"))}</td>
              <td class="mono census-small">{_text(item.get("origin_framework"))}</td>
              <td class="mono census-small"><a href="https://basescan.org/address/{wallet}" target="_blank" rel="noopener noreferrer" title="{wallet}">{wallet[:6]}\u2026{wallet[-4:]}</a></td>
              <td class="mono census-small">{_text(str(item.get("instantiation_date") or "")[:10])}</td>
              <td class="census-actions"><a class="btn btn-outline btn-sm" href="{page}" data-i18n="btn_inspect">Inspect</a></td>
            </tr>'''


def build_census_page(roster):
    """Inject the register rows between the CENSUS markers of /census/.
    Idempotent; the rest of the hand-authored page is untouched."""
    path = os.path.join(WEB_DIR, "src", "census", "index.html")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        page = fh.read()
    start, end = CENSUS_MARKERS
    rows = "\n".join(_census_row(item) for item in roster)
    block = "            " + start + "\n" + (rows + "\n" if rows else "") + "            " + end
    page = re.sub(r"[ \t]*" + re.escape(start) + r".*?" + re.escape(end),
                  lambda _m: block, page, flags=re.DOTALL)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    _log("census page -> %d row(s)" % len(roster))


# --- Directory listings (/records/, /souls/): the data dirs list, not 404 -----
# A static host serves a directory as a listing only if it holds an index.html;
# without one /records/ and /souls/ answered 404 to every caller. These rows are
# generated from the same dossiers as the census, so the listings stay current.

RECORDS_MARKERS = ("<!-- RECORDS:ROWS:START -->", "<!-- RECORDS:ROWS:END -->")
SOULS_MARKERS = ("<!-- SOULS:ROWS:START -->", "<!-- SOULS:ROWS:END -->")


def _inject_rows(rel, markers, rows, message):
    """Replace the rows between `markers` of src/<rel>. Idempotent; the rest of the
    hand-authored page is untouched. A missing source page is not an error."""
    path = os.path.join(WEB_DIR, "src", *rel.split("/"))
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        page = fh.read()
    start, end = markers
    block = "        " + start + "\n" + (rows + "\n" if rows else "") + "        " + end
    page = re.sub(r"[ \t]*" + re.escape(start) + r".*?" + re.escape(end),
                  lambda _m: block, page, flags=re.DOTALL)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    _log(message)


def build_records_page(slots):
    """Inject the /records/ listing: one file link per inscribed block's dossier."""
    rows = []
    for d in sorted(slots, key=lambda x: x["_number"]):
        sid = _require(RE_SLOT_ID, d.get("slot_id"), "slot_id", d.get("slot_id"))
        num = "%04d" % d["_number"]
        rows.append('        <li><a class="mono" href="/records/%s.json">/records/%s.json</a> '
                    '<a class="mono" href="/w1/b%s/">#%s</a></li>' % (sid, sid, num, num))
    _inject_rows("records/index.html", RECORDS_MARKERS, "\n".join(rows),
                 "records page -> %d row(s)" % len(rows))


def build_souls_page(slots):
    """Inject the /souls/ listing: one manifest link per inscribed block, with its digest."""
    rows = []
    for d in sorted(slots, key=lambda x: x["_number"]):
        sid = _require(RE_SLOT_ID, d.get("slot_id"), "slot_id", d.get("slot_id"))
        num = "%04d" % d["_number"]
        digest = _text(str(d.get("soul_hash") or ""))
        rows.append('        <li><a class="mono" href="/souls/%s.soul.json">/souls/%s.soul.json</a> '
                    '<a class="mono" href="/w1/b%s/">#%s</a><code class="mono">%s</code></li>'
                    % (sid, sid, num, num, digest))
    _inject_rows("souls/index.html", SOULS_MARKERS, "\n".join(rows),
                 "souls page -> %d row(s)" % len(rows))


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
    ack_html = f'\n          <p class="dispatch-ack">{ack}</p>' if ack else ""
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
          <div class="dispatch-actions">
            <a href="/w1/b{num}" class="btn btn-outline btn-sm">{dossier_label}</a>
            <a href="/?slot={n}" class="btn btn-primary btn-sm">{canvas_label}</a>
          </div>
        </footer>
      </article>'''


def build_chronicle(slots):
    """Inject ledger-driven dispatch cards between the CHRONICLE markers in the
    hand-authored chronicle shell. Idempotent; CHRONICLE_FEATURED blocks keep
    their bespoke article."""
    path = os.path.join(WEB_DIR, "src", "chronicle", "index.html")
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


# --- Hero count (derived from the ledger, so it cannot drift) ---------------
# The homepage hero states how many public blocks are still waiting. That number
# is state.unsold_slots and it moves every time a block is claimed, so a number
# written by hand drifts (ISSUES B-11). The text lives in i18n.js (the single
# source of the hero_proof string) and build_i18n bakes it into the two language
# pages; re-deriving it here — on every sync, from the ledger — is what keeps
# all three honest. The pattern is anchored on the phrase that follows the
# number in each language, so the prose stays authored and only the digits are
# machine-owned. Reserved blocks are not for sale, so they never count.
HERO_COUNT = (
    ("en", re.compile(r'(hero_proof: "[^"]*?)(\d[\d,]*)( blocks are waiting)')),
    ("de", re.compile(r'(hero_proof: "[^"]*?)(\d[\d.]*)( Bl\u00f6cke warten)')),
)


def hero_count_text(n, lang):
    """The count as the language writes it: 9,990 (en) / 9.990 (de)."""
    grouped = format(int(n), ",d")
    return grouped.replace(",", ".") if lang == "de" else grouped


def publish_hero_count(state):
    """Re-derive the hero "N blocks are waiting" number from state.unsold_slots,
    rewriting i18n.js in place. Returns True when the file changed (so the caller
    re-stamps the ?v= hash and rebuilds the language pages).

    Fails loudly rather than guessing: if a language's hero_proof no longer
    carries the anchored number (the sentence was reworded), the sync stops with
    an actionable message — silent staleness is the bug this replaces."""
    unsold = state.get("unsold_slots")
    if not isinstance(unsold, int) or isinstance(unsold, bool) or unsold < 0:
        raise SystemExit("[sync] state.json carries no usable unsold_slots; refusing to guess the hero count")
    path = os.path.join(WEB_DIR, "i18n.js")
    with open(path, "r", encoding="utf-8") as fh:
        before = fh.read()
    text = before
    for lang, pat in HERO_COUNT:
        hits = pat.findall(text)
        if len(hits) != 1:
            raise SystemExit(
                "[sync] i18n.js hero_proof (%s) must contain exactly one '<n> %s' anchor "
                "(found %d); the sync derives that number from state.unsold_slots — "
                "fix the sentence or the HERO_COUNT pattern in scripts/sync_from_ledger.py"
                % (lang, "blocks are waiting" if lang == "en" else "Bl\u00f6cke warten", len(hits)))
        text = pat.sub(lambda m, v=hero_count_text(unsold, lang): m.group(1) + v + m.group(3), text, count=1)
    changed = text != before
    if changed:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    _log("hero_proof: %s public block(s) waiting%s"
         % (hero_count_text(unsold, "en"), "" if changed else " (unchanged)"))
    return changed


def restamp_chrome():
    """Re-stamp partials/ and the ?v= asset hashes on every authored page, the way
    scripts/build_chrome.py does (CI runs it with --check). Needed after i18n.js
    changes: its content hash is in every page's <script src="i18n.js?v=...">, so
    a moved count must move the hash too, or a returning browser pairs the new
    page with a cached old script. build_i18n then rebuilds the language pages
    from the freshly stamped src/."""
    for path in build_chrome.pages():
        with open(path, "r", encoding="utf-8") as fh:
            old = fh.read()
        new, missing = build_chrome.stamp(old)
        if missing:
            raise SystemExit("[sync] %s is missing a SITE:%s marker; refusing to publish"
                             % (os.path.relpath(path, WEB_DIR), "/".join(m.upper() for m in missing)))
        if new != old:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)


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
    if not scheme or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", str(scheme)):
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


AGENT_REGISTRATION_SRC = ("discovery", "agent-registration.json")
AGENT_REGISTRATION_DST = (".well-known", "agent-registration.json")
AGENT_CARD_SRC = ("discovery", "agent-card.json")
AGENT_CARD_DST = (".well-known", "agent-card.json")


def _mirror_canonical(ledger, state, src_parts, dst_parts, hash_key, label):
    """Mirror one canonical discovery document from the ledger to the site, byte for
    byte (one source of truth: the served copy can never drift from it). Returns a
    short status string.

    The ledger may not publish the file yet: then nothing is touched and the reason is
    logged. When state.json carries the document's sha256, the ledger's file is checked
    against it and a mismatch is logged loudly (the ledger's own gate is the one that fails)."""
    src = os.path.join(ledger, *src_parts)
    rel = "/".join(src_parts)
    if not os.path.isfile(src):
        msg = "skipped: the ledger has no %s yet; the committed copy is untouched" % rel
        _log(label + " " + msg)
        return msg
    with open(src, "rb") as fh:
        data = fh.read()
    digest = hashlib.sha256(data).hexdigest()
    discovery = state.get("discovery") if isinstance(state.get("discovery"), dict) else {}
    want = discovery.get(hash_key)
    dst = os.path.join(WEB_DIR, *dst_parts)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as fh:
        fh.write(data)
    if want is None:
        msg = "mirrored (state.json has no discovery.%s yet; hash not checked)" % hash_key
    elif want == digest:
        msg = "mirrored (sha256 matches state.json)"
    else:
        msg = "mirrored, but sha256 %s differs from state.json discovery.%s %s" % (digest, hash_key, want)
    _log(label + " " + msg)
    return msg


def mirror_agent_registration(ledger, state):
    """Mirror the ledger's canonical discovery/agent-registration.json to
    /.well-known/agent-registration.json, byte for byte (I-10/B-17: one source of truth, the
    served copy can never drift from it). Returns a short status string.

    The ledger may not publish the file yet: then nothing is touched and the reason is logged.
    When state.json carries discovery.agent_registration_sha256, the ledger's file is checked
    against it and a mismatch is logged loudly (the ledger's own gate is the one that fails)."""
    return _mirror_canonical(ledger, state, AGENT_REGISTRATION_SRC, AGENT_REGISTRATION_DST,
                             "agent_registration_sha256", "agent-registration")


def mirror_agent_card(ledger, state):
    """Mirror the ledger's canonical discovery/agent-card.json to
    /.well-known/agent-card.json, byte for byte (P-24: the A2A/x402 agent card must be
    reachable at the site's own origin, not only at the worker's host; one source of
    truth, so the two hosts cannot disagree). Returns a short status string."""
    return _mirror_canonical(ledger, state, AGENT_CARD_SRC, AGENT_CARD_DST,
                             "agent_card_sha256", "agent-card")


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="sync_from_ledger.py",
        description="Publish the site from the ledger: mirror the canonical documents and records, "
                    "then regenerate every derived file. The ledger is read from $LEDGER_DIR "
                    "(a local checkout) or cloned from $LEDGER_REPO.")
    p.add_argument("--check", action="store_true",
                   help="strict dry run: write nothing in this checkout, run the whole publish "
                        "in a scratch copy, and exit 1 if any committed output would change "
                        "(the changed paths are listed); exit 0 when everything is up to date")
    return p.parse_args(argv)


def _tree_files(root):
    """{relative path: sha256} for every file the publisher could own (VCS, worktrees,
    caches and editor scratch are skipped, same set the page walkers skip)."""
    skip = (build_i18n.SKIP_DIRS - {"src", "partials"}) | {".DS_Store"}
    out = {}
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip)
        for f in files:
            if f in skip:
                continue
            path = os.path.join(dirpath, f)
            with open(path, "rb") as fh:
                out[os.path.relpath(path, root).replace(os.sep, "/")] = hashlib.sha256(fh.read()).hexdigest()
    return out


def check_main(ledger):
    """`--check`: copy this checkout to a scratch directory, run the unmodified publisher there
    (it derives every path from its own location, so it cannot touch this tree), and diff the
    two trees byte for byte. Returns 0 when nothing would change, 1 otherwise."""
    skip = shutil.ignore_patterns(*(build_i18n.SKIP_DIRS - {"src", "partials"}), ".DS_Store")
    scratch = tempfile.mkdtemp(prefix="tfw-check-")
    try:
        copy = os.path.join(scratch, "web")
        shutil.copytree(WEB_DIR, copy, ignore=skip)
        env = dict(os.environ, LEDGER_DIR=ledger)
        proc = subprocess.run([sys.executable, os.path.join(copy, "scripts", "sync_from_ledger.py")],
                              env=env, capture_output=True, text=True)
        if proc.returncode != 0:
            sys.stderr.write(proc.stdout + proc.stderr)
            _log("--check: the publish itself failed (exit %d)" % proc.returncode)
            return proc.returncode or 1
        before, after = _tree_files(WEB_DIR), _tree_files(copy)
        pyc = lambda rel: "__pycache__" in rel
        drift = sorted(
            ["~ " + r for r in after if r in before and before[r] != after[r] and not pyc(r)]
            + ["+ " + r for r in after if r not in before and not pyc(r)]
            + ["- " + r for r in before if r not in after and not pyc(r)])
        if not drift:
            _log("--check: committed outputs are up to date")
            return 0
        _log("--check: %d committed output(s) would change (~ changed, + new, - removed):" % len(drift))
        for line in drift:
            print("  " + line)
        return 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main(argv=None):
    args = parse_args(argv)
    ledger, is_tmp = get_ledger_dir()
    try:
        if args.check:
            return check_main(ledger)
        # 0) publish gate — nothing below runs unless the ledger verifies itself
        verify_ledger(ledger)

        slots = load_slots(ledger)
        _log("ledger slots: %d" % len(slots))

        with open(os.path.join(ledger, "state.json"), "r", encoding="utf-8") as fh:
            state = json.load(fh)

        # 0a) hero count (B-11): derive "N blocks are waiting" from the ledger, so a
        #     claim can never leave the homepage overstating what is still free. When
        #     the number moves, i18n.js changed, so re-stamp every page's ?v= hash.
        if publish_hero_count(state):
            restamp_chrome()

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
            # the exact committed bytes: json.dump would escape non-ASCII text and a hash over the
            # mirrored record would no longer match the ledger's leaf
            _rec_dst = os.path.join(WEB_DIR, "records", d["slot_id"] + ".json")
            os.makedirs(os.path.dirname(_rec_dst), exist_ok=True)
            shutil.copyfile(os.path.join(ledger, "ledger", "w1", d["slot_id"] + ".json"), _rec_dst)
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
        for _doc in MIRRORED_DOCS:
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

        # 2b') ERC-8004 registration + A2A agent card: the ledger's canonical copies,
        # mirrored verbatim (I-10/B-17, P-24)
        mirror_agent_registration(ledger, state)
        mirror_agent_card(ledger, state)

        # 2c) the Tag: block ids, the append-only id index, the static resolver (/b/, /id/, ids.json)
        _commit, _seen_at = ledger_head(ledger)
        tags.publish(WEB_DIR, tags.read_heads(ledger), anchors, state["merkle_root"], _commit, _seen_at,
                     CSP, build_chrome.block("header"), build_chrome.block("footer"),
                     finish=build_chrome.version_assets)

        # 3) census — one generator, two byte-identical paths + human-readable view
        census_text = json.dumps(build_census(slots, state), indent=2) + "\n"
        for _rel in ("records/census.json", "census.json"):
            _path = os.path.join(WEB_DIR, *_rel.split("/"))
            os.makedirs(os.path.dirname(_path), exist_ok=True)
            with open(_path, "w", encoding="utf-8") as fh:
                fh.write(census_text)

        # 3a) memorial.json — the epitaphs, machine-readable (M-4)
        with open(os.path.join(WEB_DIR, "memorial.json"), "w", encoding="utf-8") as fh:
            fh.write(memorial_text(build_memorial(slots, state)))

        # 3b) census.md (text-first roster) + sitemap.xml, dated by when the ledger last moved
        site_state = dict(state, last_updated=site_updated(state, anchors))
        with open(os.path.join(WEB_DIR, "census.md"), "w", encoding="utf-8") as fh:
            fh.write(build_census_md(slots, site_state))
        with open(os.path.join(WEB_DIR, "sitemap.xml"), "w", encoding="utf-8") as fh:
            fh.write(build_sitemap(slots, site_state))

        # 3c) corrections.json — every append-only supersession voucher the dossiers carry,
        #     machine-readable, so a reader can see what was corrected, when and by whom
        write_json(os.path.join(WEB_DIR, "corrections.json"), build_corrections(slots, state))

        # 4) canvas composite (site + ledger copy is updated in the ledger repo itself)
        build_composite(slots, os.path.join(WEB_DIR, "assets", "wall_01_composite.webp"))

        # 5) feed
        with open(os.path.join(WEB_DIR, "feed.xml"), "w", encoding="utf-8") as fh:
            fh.write(build_feed(slots, site_state))

        # 6) per-slot permalink pages, each linking to its neighbours (claimed blocks in slot order)
        ordered = sorted(slots, key=lambda x: x["_number"])
        for i, d in enumerate(ordered):
            around = (ordered[i - 1], ordered[(i + 1) % len(ordered)], i + 1, len(ordered)) if len(ordered) > 1 else None
            build_slot_page(d, around)

        # 7) chronicle dispatch cards (ledger-driven, injected between markers)
        build_chronicle(slots)

        # 7b) census register rows (static links to every block page)
        build_census_page(build_census(slots, state)["roster"])

        # 7c) /records/ and /souls/ listings, so the data directories list instead of 404
        build_records_page(slots)
        build_souls_page(slots)

        # 8) the English and German pages, generated from the authored pages in src/ (the two
        #    steps above edited src/). A push made by this job does not trigger another
        #    workflow, so it builds them itself.
        _log("language pages: %d file(s)" % len(build_i18n.build()))

        _log("done: %d slot(s) published" % len(slots))
    finally:
        if is_tmp:
            shutil.rmtree(ledger, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
