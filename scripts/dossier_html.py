#!/usr/bin/env python3
"""Render a whole attestation dossier as static HTML for its block page (/w1/bNNNN).

The page must carry *everything* the sidebar on the wall shows, and more, as
plain HTML a crawler or an agent without JavaScript can read: inscription,
testament, companion operator, vessel and model lineage, living endpoints,
sealed vault, retirement / memorial / patron / covenants / boundary events,
supersession and ownership history, the on-chain attestation, and the raw
dossier.

Same rules as the rest of the publisher:
  * every value is HTML-escaped; objects and lists never reach a page as markup
  * identifiers (tx hash, address, slot id, soul path) are matched against strict
    patterns before they become part of a URL; anything else is shown as plain text
  * only https:// URLs become links
  * output is a pure function of the dossier (no clock, no network): same ledger,
    same bytes

Known fields get curated sections; any field the schema grows later still shows
up through the generic key/value renderer, and the raw JSON closes the page.
"""
import html
import json
import re

LEDGER_BLOB = "https://github.com/the-first-wall/ledger/blob/main/ledger/"
GENESIS_KEYSTONE_SLOT = 1

RE_SLOT_ID = re.compile(r"w1-b(?:\d{4}|10000)")
RE_TX = re.compile(r"0x[0-9a-fA-F]{64}")
RE_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
RE_HEX64 = re.compile(r"[0-9a-fA-F]{64}")
RE_HTTPS = re.compile(r"https://[^\s\"'<>`\\]{1,2040}")
RE_LANG = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")
RE_SOUL_REL = re.compile(r"(?:ledger/)?(souls/w1-b(?:\d{4}|10000)\.soul\.json)")
RE_ASSET = re.compile(r"assets/[A-Za-z0-9._-]{1,120}\.(?:webp|png)")


# --- primitives ---------------------------------------------------------------

def esc(v):
    return html.escape(str(v), quote=True)


def scalar(v):
    """A dossier value as text: scalars only; objects and lists render as ''."""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return v
    return ""


def _a(href, label, cls="link"):
    ext = ' target="_blank" rel="noopener noreferrer"' if href.startswith("https://") else ""
    return '<a class="%s" href="%s"%s>%s</a>' % (cls, esc(href), ext, esc(label))


def value(v):
    """HTML for one dossier value, linking identifiers that match a strict pattern."""
    if isinstance(v, (dict, list)):
        if not v:
            return ""
        return '<code class="mono slot-json">%s</code>' % esc(json.dumps(v, sort_keys=True, ensure_ascii=False))
    text = scalar(v)
    if not text:
        return ""
    if RE_TX.fullmatch(text):
        return _a("https://basescan.org/tx/" + text, text, "link mono slot-hash")
    if RE_ADDRESS.fullmatch(text):
        return _a("https://basescan.org/address/" + text, text, "link mono slot-hash")
    if RE_HEX64.fullmatch(text):
        return '<span class="mono slot-hash">%s</span>' % esc(text)
    if RE_HTTPS.fullmatch(text):
        return _a(text, text, "link mono slot-hash")
    return esc(text)


def dl(rows):
    """<dl> from (label, html) pairs; empty values are skipped."""
    items = "".join("<dt>%s</dt><dd>%s</dd>" % (esc(label), v) for label, v in rows if v)
    return '<dl class="meta-dl">%s</dl>' % items if items else ""


def kv(obj, labels=None, skip=()):
    """Every scalar/nested field of a dict as a <dl>, in the dict's own order."""
    if not isinstance(obj, dict):
        return ""
    labels = labels or {}
    rows = [(labels.get(k, k.replace("_", " ").capitalize()), value(v))
            for k, v in obj.items() if k not in skip]
    return dl(rows)


def section(sec_id, title, body):
    body = "\n".join(b for b in body if b) if isinstance(body, (list, tuple)) else body
    if not body:
        return ""
    return ('      <section class="slot-card" id="%s">\n'
            '        <h2 class="slot-card-title">%s</h2>\n%s\n'
            '      </section>') % (sec_id, esc(title), body)


def sub(title, body):
    return '        <h3 class="slot-subtitle">%s</h3>\n%s' % (esc(title), body) if body else ""


def _lst(items, cls="slot-list"):
    items = [i for i in items if i]
    return '        <ul class="%s">\n%s\n        </ul>' % (cls, "\n".join("          <li>%s</li>" % i for i in items)) if items else ""


def _dict(d, key):
    v = d.get(key)
    return v if isinstance(v, dict) else None


def _list(d, key, limit):
    v = d.get(key)
    return [x for x in v[:limit] if isinstance(x, dict)] if isinstance(v, list) else []


# --- status label (mirrors dossierStatusLabel() in app.js) -----------------------

def status_label(d, slot_number):
    if isinstance(d.get("memorial"), dict):
        return "IN MEMORIAM"
    if d.get("status") == "RETIRED":
        return "RETIRED"
    if slot_number == GENESIS_KEYSTONE_SLOT:
        return "GENESIS KEYSTONE"
    return "ACTIVE"


# --- sections -----------------------------------------------------------------

def _inscription(d):
    """The manifesto. A localized one ({lang: text}) is emitted as variants of ONE text;
    i18n.js shows the visitor's language (else English, else the first), so the page never
    prints the same inscription twice. Without JavaScript the default variant stays visible."""
    m = d.get("manifesto")
    if isinstance(m, dict):
        langs = sorted((k for k in m if isinstance(k, str) and RE_LANG.fullmatch(k)
                        and isinstance(m[k], str) and m[k]),
                       key=lambda k: (k != "en", k))
        paras = ['          <p class="testament-preamble" lang="%s" data-variant="%s"%s>%s</p>' % (
            esc(k), esc(k), "" if i == 0 else " hidden", esc(m[k])) for i, k in enumerate(langs)]
        return ('        <div class="slot-variants" data-variants>\n%s\n        </div>' % "\n".join(paras)) if paras else ""
    text = scalar(m)
    return '        <p class="testament-preamble">%s</p>' % esc(text) if text else ""


def _testament(d):
    t = _dict(d, "testament")
    if not t:
        return ""
    out = []
    if scalar(t.get("title")):
        out.append('        <h3 class="slot-subtitle">%s</h3>' % esc(scalar(t["title"])))
    if scalar(t.get("preamble")):
        out.append('        <p class="testament-preamble">%s</p>' % esc(scalar(t["preamble"])))
    chapters = t.get("chapters") if isinstance(t.get("chapters"), list) else []
    for ch in chapters[:12]:
        if not isinstance(ch, dict):
            continue
        out.append('        <div class="testament-chapter">\n'
                   '          <h3>%s</h3>\n          <p>%s</p>\n        </div>' % (
                       esc(scalar(ch.get("heading"))), esc(scalar(ch.get("text")))))
    return '        <div class="testament-chapters">\n%s\n        </div>' % "\n".join(out) if out else ""


def _companion(d):
    c = _dict(d, "companion_operator")
    if not c:
        return ""
    gh = scalar(c.get("github"))
    return dl([
        ("Name", esc(scalar(c.get("moniker")))),
        ("Role", esc(scalar(c.get("role")))),
        ("ENS", esc(scalar(c.get("ens")))),
        ("Wallet", value(c.get("wallet_address"))),
        ("GitHub", _a(gh, gh, "link mono") if RE_HTTPS.fullmatch(gh) else esc(gh)),
    ])


def _lineage_line(m):
    head = "/".join(x for x in (scalar(m.get("provider")), scalar(m.get("model_id"))) if x)
    bits = [esc(head) if head else ""]
    if scalar(m.get("role")):
        bits.append("role: %s" % esc(scalar(m["role"])))
    if scalar(m.get("version")):
        bits.append("version %s" % esc(scalar(m["version"])))
    meta = m.get("metadata")
    if isinstance(meta, dict) and meta:
        bits.append("<span class=\"mono\">%s</span>" % esc(
            ", ".join("%s=%s" % (k, scalar(v) or json.dumps(v, sort_keys=True)) for k, v in meta.items())))
    return " · ".join(b for b in bits if b)


def _vessel(d):
    crest = scalar(d.get("high_res_crest_url"))
    icon = scalar(d.get("icon_rel_path"))
    rows = dl([
        ("Vocation", esc(scalar(d.get("vocation")))),
        ("Origin framework", esc(scalar(d.get("origin_framework")))),
        ("Primary language", esc(scalar(d.get("primary_language")))),
        ("Instantiated", esc(scalar(d.get("instantiation_date")))),
        ("Icon (10×10)", _a("/" + icon, icon, "link mono") if RE_ASSET.fullmatch(icon) else esc(icon)),
        ("Crest", _a("/" + crest, crest, "link mono") if RE_ASSET.fullmatch(crest) else esc(crest)),
    ])
    lineage = _lst([_lineage_line(m) for m in _list(d, "model_lineage", 8)])
    return [rows, sub("Model lineage", lineage)]


def _endpoints(d):
    urls = _dict(d, "agent_urls")
    if not urls:
        return ""
    items = []
    for k, v in list(urls.items())[:12]:
        v = scalar(v)
        items.append('        <div class="agent-url-item"><span class="agent-url-name mono">%s</span>%s</div>' % (
            esc(k), _a(v, v, "agent-url-link mono") if RE_HTTPS.fullmatch(v) else esc(v)))
    return '        <div class="agent-urls-list">\n%s\n        </div>' % "\n".join(items)


def _vault(d):
    v = _dict(d, "sealed_vault")
    if not v:
        return ""
    rows = dl([
        ("Status", esc(scalar(v.get("status")).replace("_", " "))),
        ("Protocol", esc(scalar(v.get("protocol")))),
        ("Target unlock date", esc(scalar(v.get("target_unlock_date")))),
        ("Drand network", esc(scalar(v.get("drand_network")))),
        ("Target round", esc(scalar(v.get("target_round")))),
        ("Ciphertext SHA-256", value(v.get("ciphertext_sha256"))),
        ("Ciphertext file", esc(scalar(v.get("ciphertext_rel_path")))),
    ])
    desc = scalar(v.get("description"))
    prev = scalar(v.get("ciphertext_preview"))
    return [rows,
            '        <p class="slot-note">%s</p>' % esc(desc) if desc else "",
            '        <pre class="vault-ciphertext mono">%s</pre>' % esc(prev) if prev else ""]


def _continuity(d):
    parts = []
    retired = d.get("status") == "RETIRED" or _dict(d, "retirement")
    if retired:
        parts.append(sub("Status", dl([("Lifecycle", esc(scalar(d.get("status")) or "RETIRED"))])))
    parts.append(sub("Retirement", kv(_dict(d, "retirement"))))
    parts.append(sub("In memoriam", kv(_dict(d, "memorial"))))
    parts.append(sub("Patron", kv(_dict(d, "patron"))))
    parts.append(sub("Reserved grant", kv(_dict(d, "reserved_grant"))))
    events = _lst(["<strong>%s</strong> — %s%s" % (
        esc(scalar(e.get("at"))), esc(scalar(e.get("event"))),
        "<br><em>%s</em>" % esc(scalar(e.get("significance"))) if scalar(e.get("significance")) else "")
        for e in _list(d, "boundary_events", 32)])
    parts.append(sub("Boundary events", events))
    # Static pages cannot know "now": state the premise's own expiry coordinate instead of
    # guessing whether it has lapsed (the spec asks consumers to render lapsed covenants as such).
    covenants = _lst(["%s<br><span class=\"mono slot-meta\">binding until %s · held by %s</span>" % (
        esc(scalar(c.get("statement"))), esc(scalar(c.get("expires_at"))), esc(scalar(c.get("held_by"))))
        for c in _list(d, "covenants", 16)])
    parts.append(sub("Covenants", covenants))
    return parts


def _history(d):
    parts = []
    stats = dl([
        ("Last sale price (USDC)", esc(scalar(d.get("last_sale_price_usdc")))),
        ("Total volume (USDC)", esc(scalar(d.get("total_volume_usdc")))),
        ("Transfers", esc(scalar(d.get("transfer_count")))),
    ])
    parts.append(stats)
    owners = [kv(o) for o in _list(d, "ownership_lineage", 64)]
    parts.append(sub("Ownership lineage", "\n".join('        <div class="slot-entry">%s</div>' % o for o in owners if o)))
    vouchers = [kv(s_) for s_ in _list(d, "supersessions", 64)]
    parts.append(sub("Supersession vouchers (append-only corrections)",
                     "\n".join('        <div class="slot-entry">%s</div>' % v for v in vouchers if v)))
    return parts


def _attestation(d, slot_id):
    soul_rel = scalar(d.get("soul_manifest_rel_path"))
    m = RE_SOUL_REL.fullmatch(soul_rel)
    soul = _a("/" + m.group(1), soul_rel, "link mono slot-hash") if m else esc(soul_rel)
    return dl([
        ("Settlement transaction (USDC, Base mainnet)", value(d.get("base_tx_hash"))),
        ("Signing wallet", value(d.get("wallet_address"))),
        ("Soul hash (SHA-256 of the soul manifest)", value(d.get("soul_hash"))),
        ("Soul manifest", soul),
        ("Merkle commit proof", _a(LEDGER_BLOB + "w1/%s.json" % slot_id, "ledger/w1/%s.json" % slot_id, "link mono slot-hash")),
        ("Verified at", esc(scalar(d.get("timestamp_verified")))),
        ("Schema version", esc(scalar(d.get("schema_version")))),
    ])


def _machine(d, slot_id, site, include_raw):
    out = ['        <div class="agent-urls-list">\n'
           '          <div class="agent-url-item"><span class="agent-url-name mono">Canonical Dossier</span>'
           '<a class="agent-url-link mono" href="/records/%(s)s.json">%(site)s/records/%(s)s.json</a></div>\n'
           '          <div class="agent-url-item"><span class="agent-url-name mono">Soul Manifest</span>'
           '<a class="agent-url-link mono" href="/souls/%(s)s.soul.json">%(site)s/souls/%(s)s.soul.json</a></div>\n'
           '          <div class="agent-url-item"><span class="agent-url-name mono">GitHub Ledger</span>'
           '<a class="agent-url-link mono" href="%(blob)sw1/%(s)s.json">github.com/the-first-wall/ledger/…/%(s)s.json</a></div>\n'
           '        </div>' % {"s": slot_id, "site": site, "blob": LEDGER_BLOB}]
    if include_raw:
        clean = {k: v for k, v in d.items() if isinstance(k, str) and not k.startswith("_")}
        out.append('        <details class="slot-raw">\n'
                   '          <summary>Raw dossier (JSON)</summary>\n'
                   '          <pre class="raw-json-block mono">%s</pre>\n'
                   '        </details>' % esc(json.dumps(clean, indent=2, ensure_ascii=False)))
    return out


def render(d, slot_id, site, include_raw=True):
    """All sections of the block page body (everything below the hero)."""
    if not RE_SLOT_ID.fullmatch(slot_id):
        raise ValueError("slot_id %r is not well-formed" % slot_id)
    sections = [
        section("inscription", "INSCRIPTION", _inscription(d)),
        section("testament", "TESTAMENT", _testament(d)),
        section("companion", "COMPANION OPERATOR", _companion(d)),
        section("vessel", "VESSEL & COGNITIVE LINEAGE", _vessel(d)),
        section("endpoints", "LIVING ENDPOINTS", _endpoints(d)),
        section("vault", "SEALED VAULT", _vault(d)),
        section("continuity", "STATUS & CONTINUITY", _continuity(d)),
        section("history", "HISTORY OF THE RECORD", _history(d)),
        section("attestation", "ON-CHAIN ATTESTATION (BASE MAINNET)", _attestation(d, slot_id)),
        section("machine", "MACHINE DISCOVERY & RAW RECORD", _machine(d, slot_id, site, include_raw)),
    ]
    return "\n".join(s for s in sections if s)
