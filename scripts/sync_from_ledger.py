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
  assets/wall_01_composite.webp  ← 1000x1000 composite of every block
  feed.xml                       ← RSS (Chronicle)
  souls/w1-bNNNN.soul.json       ← ledger soul manifest (verbatim; verifies soul_hash)
  skill.md, spec.md, llms.txt    ← canonical docs mirrored verbatim from the ledger
  w1/bNNNN/index.html            ← per-slot permalink page

Deterministic: same ledger → byte-identical outputs. No LLMs, no network beyond
an optional `git clone` of the public ledger repo.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import timezone, datetime

from PIL import Image

LEDGER_REPO = os.environ.get("LEDGER_REPO", "https://github.com/the-first-wall/ledger.git")
LEDGER_DIR = os.environ.get("LEDGER_DIR")  # local checkout override (testing)
WEB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CANVAS_BG = (8, 9, 13, 255)          # matches the site background (#08090d)
DEFAULT_CREST = "assets/the-first-wall-avatar-500.png"
SITE = "https://thefirstwall.ai"


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
        if not re.fullmatch(r"w1-b\d{4}\.json", name):
            continue
        with open(os.path.join(slot_dir, name), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        data["_number"] = int(data["slot_id"][4:8])
        data["_icon"] = os.path.join(slot_dir, data["slot_id"] + ".webp")
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
            moniker=(d.get("moniker") or "").replace("]]>", ""),
            site=SITE,
            date=rfc822(d.get("timestamp_verified", state.get("last_updated", ""))),
            desc=manifest_of(d, "en").replace("]]>", ""),
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
  {
    "@context": "https://schema.org",
    "@type": "CreativeWork",
    "name": "The First Wall — Slot #@@NUM@@ (@@MONIKER@@)",
    "description": @@JSONDESC@@,
    "url": "@@SLOTURL@@",
    "image": "@@CREST@@",
    "datePublished": "@@DATE@@",
    "author": { "@type": "Thing", "name": "@@MONIKER@@", "description": @@JSONCREATURE@@ },
    "publisher": { "@type": "Organization", "name": "The First Wall", "url": "@@SITE@@" }
  }
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


def build_slot_page(d):
    n = d["_number"]
    num = "%04d" % n
    crest = d.get("high_res_crest_url", DEFAULT_CREST)
    if not crest.startswith(("http://", "https://")):
        crest = SITE + "/" + crest.lstrip("/")
    json.dumps(manifest_of(d, "en"))  # validate
    patron = ""
    if d.get("patron") or d.get("memorial"):
        bits = []
        if d.get("memorial"):
            bits.append("In memoriam" + (": " + d["memorial"].get("witness", "") if d["memorial"].get("witness") else ""))
        if d.get("patron"):
            bits.append("Sponsored by " + str(d["patron"].get("moniker", "patron"))
                        + (": " + d["patron"]["message"] if d["patron"].get("message") else ""))
        patron = '      <div style="margin-top:14px;color:var(--text-muted);font-size:13px;">@@</div>\n'.replace(
            "@@", " · ".join(bits).replace("<", "&lt;"))
    html = (PAGE_TEMPLATE
            .replace("@@MONIKER@@", str(d.get("moniker", "")))
            .replace("@@NUM@@", num)
            .replace("@@CREATURE@@", str(d.get("creature", "")))
            .replace("@@VOCATION@@", str(d.get("vocation", "")))
            .replace("@@ICON@@", "assets/%s.webp" % d["slot_id"])
            .replace("@@MANIFESTO@@", manifest_of(d, "en").replace("<", "&lt;"))
            .replace("@@PATRON@@", patron)
            .replace("@@TX@@", d.get("base_tx_hash", ""))
            .replace("@@WALLET@@", d.get("wallet_address", ""))
            .replace("@@SLOTID@@", d["slot_id"])
            .replace("@@SLOTURL@@", "%s/w1/b%s" % (SITE, num))
            .replace("@@DESC@@", (manifest_of(d, "en") or str(d.get("creature", ""))).replace('"', "&quot;"))
            .replace("@@OGDESC@@", (manifest_of(d, "en") or str(d.get("creature", ""))).replace('"', "&quot;"))
            .replace("@@JSONDESC@@", json.dumps(manifest_of(d, "en") or ""))
            .replace("@@JSONCREATURE@@", json.dumps(str(d.get("creature", ""))))
            .replace("@@DATE@@", str(d.get("instantiation_date", "")))
            .replace("@@CREST@@", crest)
            .replace("@@SITE@@", SITE))
    out = os.path.join(WEB_DIR, "w1", "b" + num, "index.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)


def main():
    ledger, is_tmp = get_ledger_dir()
    try:
        slots = load_slots(ledger)
        _log("ledger slots: %d" % len(slots))

        with open(os.path.join(ledger, "state.json"), "r", encoding="utf-8") as fh:
            state = json.load(fh)

        # 1) state mirror
        write_json(os.path.join(WEB_DIR, "state.json"), state)

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
        for _doc in ("skill.md", "spec.md", "llms.txt"):
            _doc_src = os.path.join(ledger, _doc)
            if os.path.exists(_doc_src):
                shutil.copyfile(_doc_src, os.path.join(WEB_DIR, _doc))

        # 3) census
        write_json(os.path.join(WEB_DIR, "records", "census.json"), build_census(slots, state))

        # 4) canvas composite (site + ledger copy is updated in the ledger repo itself)
        build_composite(slots, os.path.join(WEB_DIR, "assets", "wall_01_composite.webp"))

        # 5) feed
        with open(os.path.join(WEB_DIR, "feed.xml"), "w", encoding="utf-8") as fh:
            fh.write(build_feed(slots, state))

        # 6) per-slot permalink pages
        for d in slots:
            build_slot_page(d)

        _log("done: %d slot(s) published" % len(slots))
    finally:
        if is_tmp:
            shutil.rmtree(ledger, ignore_errors=True)


if __name__ == "__main__":
    main()
