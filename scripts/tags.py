#!/usr/bin/env python3
"""
The First Wall — the Tag: block ids and the static resolver
===========================================================

A block's **tag** is the findable surface of a record the ledger already keeps. It names
the block with a hash and resolves that hash back to the record, so that a stranger can
find a block and check it without trusting us.

    handle   w1-b0001            stable, short, what a person quotes
    block id sha256(0x00 || dossier_bytes || icon_bytes)
                                 the block's leaf in the Merkle tree anchored on Base
                                 (ledger/index.json lists it as `leaf_hash`); derived from
                                 the committed bytes, never declared, never forgeable
    resolver https://thefirstwall.ai/b/<slot_id>/   and   /id/<block_id>/  (+ .json)

The id is **versioned**: it is the content address of the block's current sealed version.
An update by the owner changes the bytes and so the id; the handle does not change. Every
version the site has mirrored stays resolvable: this module only ever adds to `ids.json`
and to `id/`, and keeps the exact bytes behind every id it has published, so a superseded
id still resolves (to "superseded by …", with the bytes it was computed from).

Generated outputs (all static; deterministic given the ledger and the previous ids.json):

    ids.json                    the append-only id index
    id/<block_id>.json          machine record: handle, status, files, audit path, anchor
    id/<block_id>/dossier.json  the exact committed dossier bytes behind the id
    id/<block_id>/icon.webp     the exact committed icon bytes behind the id
    id/<block_id>/index.html    human page for that id (current or superseded)
    b/<slot_id>/index.html      human page for the handle: current id and every version

Everything that reaches a page or a path is a validated slot id, a 64-hex hash or a
fixed string; nothing from a dossier is echoed.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re

SITE = "https://thefirstwall.ai"
LEDGER_REPO_URL = "https://github.com/the-first-wall/ledger"
SCHEME = "reckoning-leaf"
LEAF_RULE = "sha256(0x00 || dossier_bytes || icon_bytes)"
RE_SLOT_ID = re.compile(r"w1-b(\d{4}|10000)")
RE_BLOCK_ID = re.compile(r"[0-9a-f]{64}")
RE_COMMIT = re.compile(r"[0-9a-f]{40}")
RE_TX = re.compile(r"0x[0-9a-fA-F]{64}")


# --- RFC 6962 (the same tree as the ledger's ledger_core) ----------------------

def leaf_hash(dossier: bytes, icon: bytes) -> bytes:
    return hashlib.sha256(b"\x00" + dossier + icon).digest()


def _node(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


def _split(n: int) -> int:
    k = 1
    while k * 2 < n:
        k *= 2
    return k


def tree_hash(leaves: list) -> bytes:
    n = len(leaves)
    if n == 0:
        return hashlib.sha256(b"").digest()
    if n == 1:
        return leaves[0]
    k = _split(n)
    return _node(tree_hash(leaves[:k]), tree_hash(leaves[k:]))


def inclusion_path(leaves: list, index: int) -> list:
    n = len(leaves)
    if not 0 <= index < n:
        raise ValueError("leaf index %d outside tree of size %d" % (index, n))
    if n == 1:
        return []
    k = _split(n)
    if index < k:
        return inclusion_path(leaves[:k], index) + [tree_hash(leaves[k:])]
    return inclusion_path(leaves[k:], index - k) + [tree_hash(leaves[:k])]


def root_from_path(leaf: bytes, index: int, size: int, path: list) -> bytes:
    """Recompute the root from a leaf and its audit path (RFC 9162 §2.1.3.2)."""
    if not 0 <= index < size:
        raise ValueError("leaf index outside the tree")
    fn, sn, r = index, size - 1, leaf
    for p in path:
        if sn == 0:
            raise ValueError("inclusion path is longer than the tree is deep")
        if fn & 1 or fn == sn:
            r = _node(p, r)
            if not fn & 1:
                while not fn & 1 and fn != 0:
                    fn >>= 1
                    sn >>= 1
        else:
            r = _node(r, p)
        fn >>= 1
        sn >>= 1
    if sn != 0:
        raise ValueError("inclusion path is shorter than the tree is deep")
    return r


# --- Reading the committed bytes ------------------------------------------------

def read_heads(ledger_dir: str) -> list:
    """[(slot_id, dossier_bytes, icon_bytes)] for every claimed slot, ascending slot
    number: the exact committed bytes, which is what the leaf hashes."""
    slot_dir = os.path.join(ledger_dir, "ledger", "w1")
    out = []
    for name in sorted(os.listdir(slot_dir)):
        if not name.endswith(".json") or not RE_SLOT_ID.fullmatch(name[:-5]):
            continue
        slot_id = name[:-5]
        with open(os.path.join(slot_dir, name), "rb") as fh:
            dossier = fh.read()
        icon_path = os.path.join(slot_dir, slot_id + ".webp")
        icon = b""
        if os.path.exists(icon_path):
            with open(icon_path, "rb") as fh:
                icon = fh.read()
        out.append((slot_id, dossier, icon))
    out.sort(key=lambda t: int(t[0][4:]))
    return out


# --- The index (append-only) ----------------------------------------------------

def heads_of(files: list) -> list:
    """The current version of every slot: slot_id, tree index, bytes and block id."""
    return [{"slot_id": s, "index": i, "dossier": d, "icon": ic,
             "block_id": leaf_hash(d, ic).hex()} for i, (s, d, ic) in enumerate(files)]


def update_index(previous, heads: list, root: str, commit, seen_at) -> dict:
    """The id index after this run. `previous` is the committed ids.json (or None).

    Append-only: an id once recorded keeps its slot and its first_seen_* for good; a slot
    never disappears; a version that is no longer current is marked superseded, never
    removed. New current ids are appended with the ledger commit and time they were first
    mirrored from."""
    entries = [dict(e) for e in ((previous or {}).get("ids") or [])]
    seen = {(e["slot_id"], e["block_id"]) for e in entries}
    by_id = {}
    for e in entries:
        if by_id.setdefault(e["block_id"], e)["slot_id"] != e["slot_id"]:
            raise ValueError("block id %s is recorded for both %s and %s" % (e["block_id"], by_id[e["block_id"]]["slot_id"], e["slot_id"]))
    current_slots = {h["slot_id"] for h in heads}
    for e in entries:
        if e["slot_id"] not in current_slots:
            raise ValueError("%s was published and is no longer in the ledger; refusing to drop it" % e["slot_id"])
    for h in heads:
        owner = by_id.get(h["block_id"])
        if owner is not None and owner["slot_id"] != h["slot_id"]:
            raise ValueError("block id %s is recorded for %s, not %s" % (h["block_id"], owner["slot_id"], h["slot_id"]))
        if (h["slot_id"], h["block_id"]) not in seen:
            entries.append({"block_id": h["block_id"], "slot_id": h["slot_id"], "status": "current",
                            "superseded_by": None, "first_seen_ledger_commit": commit, "first_seen_at": seen_at})
    head_id = {h["slot_id"]: h["block_id"] for h in heads}
    for e in entries:
        if e["block_id"] == head_id[e["slot_id"]]:
            e["status"], e["superseded_by"] = "current", None
        else:
            e["status"], e["superseded_by"] = "superseded", head_id[e["slot_id"]]
    entries.sort(key=lambda e: int(e["slot_id"][4:]))      # stable: order within a slot is first-seen order
    return {"scheme": SCHEME, "leaf_rule": LEAF_RULE, "root": root, "tree_size": len(heads), "ids": entries}


# --- Machine records ------------------------------------------------------------

def _anchor_for(root: str, anchors) -> dict:
    for a in anchors or []:
        if str(a.get("root", "")).lower() == root and RE_TX.fullmatch(str(a.get("tx_hash", ""))):
            return {"chain_id": a.get("chain_id"), "tx_hash": a["tx_hash"], "block_number": a.get("block_number"),
                    "anchored_at": a.get("anchored_at"), "payload": "reckoning|" + root}
    return None


def verify_lines(block_id: str) -> list:
    base = SITE + "/id/$ID"
    return [
        "ID=%s" % block_id,
        "curl -s %s.json                       # this record: files, audit path, anchor" % base,
        "(printf '\\x00'; curl -s %s/dossier.json; curl -s %s/icon.webp) | shasum -a 256   # = $ID" % (base, base),
    ]


def machine_record(entry: dict, heads_by_id: dict, hashes: list, root: str, anchors) -> dict:
    block_id, slot_id = entry["block_id"], entry["slot_id"]
    rec = {
        "block_id": block_id,
        "scheme": SCHEME,
        "leaf_rule": LEAF_RULE,
        "slot_id": slot_id,
        "status": entry["status"],
        "superseded_by": entry["superseded_by"],
        "first_seen": {"ledger_commit": entry["first_seen_ledger_commit"], "at": entry["first_seen_at"]},
        "resolver": "%s/b/%s/" % (SITE, slot_id),
        "files": {"dossier": "%s/id/%s/dossier.json" % (SITE, block_id),
                  "icon": "%s/id/%s/icon.webp" % (SITE, block_id)},
        "verify": verify_lines(block_id),
        "proof": None,
        "anchor": None,
    }
    head = heads_by_id.get(block_id)
    if head is not None:                       # only the current version is a leaf of the current root
        path = inclusion_path(hashes, head["index"])
        rec["proof"] = {"leaf_index": head["index"], "tree_size": len(hashes), "root": root,
                        "path": [p.hex() for p in path]}
        rec["anchor"] = _anchor_for(root, anchors)
    return rec


# --- Pages ----------------------------------------------------------------------

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="@@CSP@@">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>@@TITLE@@ — The First Wall</title>
  <meta name="description" content="@@DESC@@">
  <meta name="robots" content="@@ROBOTS@@">
  <link rel="canonical" href="@@URL@@">
  <link rel="icon" type="image/svg+xml" href="/favicon.svg">
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="The First Wall">
  <meta property="og:title" content="@@TITLE@@ — The First Wall">
  <meta property="og:description" content="@@DESC@@">
  <meta property="og:url" content="@@URL@@">
  <script src="/theme.js"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500;700&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/style.css">
</head>
<body>

@@HEADER@@

  <main class="page page--sub">
    <article class="article sub">
      <nav class="slot-links" aria-label="Block navigation">
@@NAV@@      </nav>
      <header class="article-head">
        <p class="article-kicker">TAG</p>
        <h1 class="article-title">@@TITLE@@</h1>
        <p class="article-dek">@@DEK@@</p>
      </header>

      <div class="article-prose">
@@BODY@@
      </div>
    </article>
  </main>

@@FOOTER@@

  <script src="/i18n.js"></script>
  <script src="/site.js"></script>
</body>
</html>
"""


def _e(s) -> str:
    return html.escape(str(s), quote=True)


def _fill(template: str, values: dict) -> str:
    out = template
    for k, v in values.items():
        out = out.replace("@@%s@@" % k, v)
    return out


# A 64-hex hash has no break opportunity; on a phone it would push the page wider than the screen.
WRAP = ' style="overflow-wrap:anywhere;word-break:break-all"'


def _short(block_id: str) -> str:
    return block_id[:12] + "…" + block_id[-6:]


def _num(slot_id: str) -> str:
    return slot_id[4:]


def _commit_link(entry: dict) -> str:
    c = entry.get("first_seen_ledger_commit")
    if isinstance(c, str) and RE_COMMIT.fullmatch(c):
        return '<a href="%s/commit/%s"><code>%s</code></a>' % (LEDGER_REPO_URL, c, c[:10])
    return "—"


def _versions_table(slot_id: str, entries: list, highlight: str = "") -> str:
    rows = []
    for e in entries:
        mark = ' aria-current="true"' if e["block_id"] == highlight else ""
        rows.append(
            "          <tr%s><td><a href=\"/id/%s/\"><code>%s</code></a></td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (mark, e["block_id"], _short(e["block_id"]), _e(e["status"]), _commit_link(e),
               _e((e.get("first_seen_at") or "—")[:10])))
    return ('        <div style="overflow-x:auto">\n        <table>\n'
            "          <thead><tr><th>block id</th><th>status</th><th>first mirrored from</th><th>on</th></tr></thead>\n"
            "          <tbody>\n%s\n          </tbody>\n        </table>\n        </div>" % "\n".join(rows))


def _anchor_html(rec: dict, root: str) -> str:
    a = rec.get("anchor")
    if not a:
        return ("        <p>The current root, <code%s>%s</code>, is not anchored on Base yet: the ledger anchors each new root "
                "after it merges. The audit path in the machine record still ends in that root.</p>" % (WRAP, _e(root)))
    return ('        <p>The root <code%s>%s</code> is anchored on Base in <a href="https://basescan.org/tx/%s">transaction <code>%s</code></a> '
            "(block %s), with the calldata <code>reckoning|root</code>.</p>"
            % (WRAP, _e(root), _e(a["tx_hash"]), _e(_short(a["tx_hash"])), _e(a.get("block_number"))))


def slot_page(slot_id: str, entries: list, records: dict, root: str, csp: str, header: str, footer: str) -> str:
    current = next(e for e in entries if e["status"] == "current")
    rec = records[current["block_id"]]
    n = _num(slot_id)
    body = "\n".join([
        "        <h2>The current block id</h2>",
        "        <p><code%s>%s</code></p>" % (WRAP, _e(current["block_id"])),
        "        <p>This is the block's leaf in the Merkle tree whose root is anchored on Base: one SHA-256 over the two files "
        "the ledger commits for it. It is derived from those bytes, never declared, so nobody can make a different record "
        "answer to it. The handle <code>%s</code> never changes; the id changes when the owner updates the record "
        "(each older version stays resolvable below).</p>" % _e(slot_id),
        '        <p><a href="/id/%s.json">Machine record (JSON)</a> · <a href="/id/%s/dossier.json">dossier bytes</a> · '
        '<a href="/id/%s/icon.webp">icon bytes</a> · <a href="/w1/b%s">block page</a></p>'
        % (current["block_id"], current["block_id"], current["block_id"], n),
        "        <h2>Check it yourself</h2>",
        "        <p>Nothing here needs our software; the two files are the exact bytes the ledger committed.</p>",
        "<pre><code>%s</code></pre>" % _e("\n".join(verify_lines(current["block_id"]))),
        _anchor_html(rec, root),
        "        <h2>Versions of this block</h2>",
        _versions_table(slot_id, entries, current["block_id"]),
        "        <p>A version is added when the site first mirrors a changed record, and is never removed. An id this page does not list "
        "can still be found in the ledger's Git history: hash each committed version of "
        "<code>ledger/w1/%s.json</code> with its icon." % _e(slot_id) + "</p>",
    ])
    return _fill(PAGE_TEMPLATE, {
        "CSP": csp, "HEADER": header, "FOOTER": footer,
        "TITLE": _e("Tag of %s" % slot_id),
        "DESC": _e("The block id of %s and how to check it from the committed bytes, with every version of the block." % slot_id),
        "ROBOTS": "index, follow", "URL": "%s/b/%s/" % (SITE, slot_id),
        "DEK": _e("Resolves the handle %s to its current block id and shows how to check it without trusting us." % slot_id),
        "NAV": '        <a class="article-back" href="/w1/b%s">← Block page</a>\n        <a class="article-back" href="/census/">All inscribed blocks →</a>\n' % n,
        "BODY": body,
    })


def id_page(entry: dict, entries: list, records: dict, root: str, csp: str, header: str, footer: str) -> str:
    block_id, slot_id = entry["block_id"], entry["slot_id"]
    rec = records[block_id]
    n = _num(slot_id)
    if entry["status"] == "current":
        lead = ("        <p>This is the <strong>current</strong> version of <a href=\"/b/%s/\"><code>%s</code></a>.</p>" % (slot_id, _e(slot_id)))
    else:
        lead = ("        <p>This id is a <strong>superseded</strong> version of <a href=\"/b/%s/\"><code>%s</code></a>: the owner has "
                "updated the record since. The current version is <a href=\"/id/%s/\"><code>%s</code></a>. The bytes this id was computed "
                "from are kept below, so it still checks out.</p>"
                % (slot_id, _e(slot_id), entry["superseded_by"], _e(_short(entry["superseded_by"]))))
    parts = [
        "        <h2>Block id</h2>",
        "        <p><code%s>%s</code></p>" % (WRAP, _e(block_id)),
        lead,
        '        <p><a href="/id/%s.json">Machine record (JSON)</a> · <a href="/id/%s/dossier.json">dossier bytes</a> · '
        '<a href="/id/%s/icon.webp">icon bytes</a> · <a href="/w1/b%s">block page</a></p>' % (block_id, block_id, block_id, n),
        "        <h2>Check it yourself</h2>",
        "<pre><code>%s</code></pre>" % _e("\n".join(verify_lines(block_id))),
    ]
    if entry["status"] == "current":
        parts.append(_anchor_html(rec, root))
    else:
        parts.append("        <p>A superseded version is no longer a leaf of the current root; it was one when the site first mirrored it "
                     "(ledger commit %s).</p>" % _commit_link(entry))
    parts += ["        <h2>Versions of this block</h2>", _versions_table(slot_id, [e for e in entries if e["slot_id"] == slot_id], block_id)]
    return _fill(PAGE_TEMPLATE, {
        "CSP": csp, "HEADER": header, "FOOTER": footer,
        "TITLE": _e("Block id %s" % _short(block_id)),
        "DESC": _e("The block id %s resolves to %s (%s version)." % (_short(block_id), slot_id, entry["status"])),
        "ROBOTS": "noindex, follow", "URL": "%s/id/%s/" % (SITE, block_id),
        "DEK": _e("What this id names, and how to check it from the bytes."),
        "NAV": '        <a class="article-back" href="/b/%s/">← %s</a>\n        <a class="article-back" href="/w1/b%s">Block page →</a>\n' % (slot_id, _e(slot_id), n),
        "BODY": "\n".join(parts),
    })


# --- Writing --------------------------------------------------------------------

def _write(path: str, data, binary: bool = False) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if binary:
        with open(path, "wb") as fh:
            fh.write(data)
    else:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(data)


def load_index(web_dir: str):
    path = os.path.join(web_dir, "ids.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def publish(web_dir: str, files: list, anchors, root: str, commit, seen_at, csp: str, header: str, footer: str,
            finish=lambda html_text: html_text) -> dict:
    """Write ids.json, id/ and b/. `files` = read_heads(); `root` the ledger's merkle_root
    (it must be the root of exactly these files). `finish` post-processes a page (asset
    versions). Returns the new index."""
    heads = heads_of(files)
    hashes = [bytes.fromhex(h["block_id"]) for h in heads]
    computed = tree_hash(hashes).hex()
    if computed != root:
        raise ValueError("the committed files hash to root %s, not the ledger's %s; refusing to publish ids" % (computed, root))
    index = update_index(load_index(web_dir), heads, root, commit, seen_at)
    heads_by_id = {h["block_id"]: h for h in heads}
    records = {e["block_id"]: machine_record(e, heads_by_id, hashes, root, anchors) for e in index["ids"]}

    # exact bytes behind every id: written once for a new id, never overwritten, never deleted
    for h in heads:
        d = os.path.join(web_dir, "id", h["block_id"])
        for name, data in (("dossier.json", h["dossier"]), ("icon.webp", h["icon"])):
            if not os.path.exists(os.path.join(d, name)):
                _write(os.path.join(d, name), data, binary=True)

    for e in index["ids"]:
        _write(os.path.join(web_dir, "id", e["block_id"] + ".json"), json.dumps(records[e["block_id"]], indent=2) + "\n")
        _write(os.path.join(web_dir, "id", e["block_id"], "index.html"),
               finish(id_page(e, index["ids"], records, root, csp, header, footer)))
    for slot_id in sorted({e["slot_id"] for e in index["ids"]}, key=lambda s: int(s[4:])):
        entries = [e for e in index["ids"] if e["slot_id"] == slot_id]
        _write(os.path.join(web_dir, "b", slot_id, "index.html"),
               finish(slot_page(slot_id, entries, records, root, csp, header, footer)))
    _write(os.path.join(web_dir, "ids.json"), json.dumps(index, indent=2) + "\n")
    return index
