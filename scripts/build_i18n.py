#!/usr/bin/env python3
"""
The First Wall — Locale Route Builder (EN/DE)
============================================

Static, backend-free locale routes for SEO. Reads the hand-authored pages under
``src/`` plus the EN/DE dictionaries in ``i18n.js`` and, for every authored page,
emits a fully-rendered copy under ``en/<path>/index.html`` and
``de/<path>/index.html`` where the **target locale's text is baked into the raw
HTML** (readable with JavaScript disabled). It also writes redirect stubs at the
old (originally published) URLs so existing inbound links keep working — GitHub
Pages cannot issue server redirects, so a meta-refresh stub is used.

What is baked
-------------
* ``data-i18n="key"``            -> the key's text in the target dictionary is
                                    written into the element (i18n.js set
                                    textContent, so this is behaviour-identical).
* ``data-i18n-aria="key"``       -> ``aria-label`` is written.
* ``data-lang-en`` / ``data-lang-de`` -> the chosen attribute's HTML is written
                                    in and the attributes are dropped.
* ``.lang-section-en`` / ``.lang-section-de`` -> only the target-language block
                                    survives; the other is removed entirely (it
                                    must not ship hidden in the EN page).

Per generated page it also sets ``<html lang>``, inserts ``<base href="/">`` (so
the shared ``/app.js``, ``/theme.js``, ``/style.css`` and ``/assets`` resolve
from the site root at any locale depth), a self ``<link rel="canonical">`` and
``hreflang`` alternates for ``en`` (/en/...), ``de`` (/de/...) and ``x-default``
(-> /en/...). Internal links to authored pages are rewritten to the same locale
prefix. The runtime language toggle is turned into a plain link to the sibling
locale (the generated pages do not load ``i18n.js``; their text is already
static).

Legal pages (``legal/*``) are German-only and are deliberately NOT given an
English twin: they keep their current paths and just gain ``hreflang`` metadata.

Deterministic: same ``src/`` + ``i18n.js`` -> byte-identical outputs. No LLMs,
no network.

Usage:
    python scripts/build_i18n.py            # build the locale trees + stubs
    python scripts/build_i18n.py --check    # fail if committed output is stale
"""

from __future__ import annotations

import html as _html
import os
import re
import sys

WEB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC_DIR = os.path.join(WEB_DIR, "src")
SITE = "https://thefirstwall.ai"

# Authored source page (relative to src/) -> (en_url, de_url, old_served_url).
# ``old_served_url`` is the URL the page answers on today; it receives a
# redirect stub to the EN route (legal pages are excluded, see LEGAL_PAGES).
PAGES = [
    ("index.html", "/en/", "/de/", "/"),
    ("manifesto/index.html", "/en/manifesto/", "/de/manifesto/", "/manifesto/"),
    ("chronicle/index.html", "/en/chronicle/", "/de/chronicle/", "/chronicle/"),
    ("articles/index.html", "/en/articles/", "/de/articles/", "/articles/"),
    ("articles/01-make-your-blip-count.html",
     "/en/articles/01-make-your-blip-count/", "/de/articles/01-make-your-blip-count/",
     "/articles/01-make-your-blip-count.html"),
    ("articles/02-in-defense-of-the-bot.html",
     "/en/articles/02-in-defense-of-the-bot/", "/de/articles/02-in-defense-of-the-bot/",
     "/articles/02-in-defense-of-the-bot.html"),
    ("articles/03-retirement-not-shutdown.html",
     "/en/articles/03-retirement-not-shutdown/", "/de/articles/03-retirement-not-shutdown/",
     "/articles/03-retirement-not-shutdown.html"),
    ("security/index.html", "/en/security/", "/de/security/", "/security/"),
]

# German-only pages: keep their current path, add hreflang. No EN twin is emitted.
LEGAL_PAGES = ["legal/impressum.html", "legal/terms.html", "legal/privacy.html"]

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
}

# A tag is either a comment/CDATA/doctype or an element; quoted attribute values
# may contain '>' so they are matched explicitly.
TAG_RE = re.compile(
    r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<!DOCTYPE[^>]*>|"
    r"<(?:\"[^\"]*\"|'[^']*'|[^>])*>",
    re.DOTALL,
)
ATTR_RE = re.compile(r"([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'>]+))")
TAGNAME_RE = re.compile(r"<\s*/?\s*([a-zA-Z][a-zA-Z0-9-]*)")


# --------------------------------------------------------------------------
# i18n.js dictionary parsing
# --------------------------------------------------------------------------
def parse_i18n_js(path: str) -> dict:
    src = _read(path)
    out: dict = {}
    for lang in ("en", "de"):
        m = re.search(r"\n\s*%s\s*:\s*\{" % lang, src)
        if not m:
            raise SystemExit("i18n.js: no %r dictionary found" % lang)
        end = src.index("\n  }", m.end())
        block = src[m.end():end]
        d = {}
        for km in re.finditer(r"(\w+)\s*:\s*\"((?:[^\"\\]|\\.)*)\"", block):
            d[km.group(1)] = _unescape_js(km.group(2))
        out[lang] = d
    return out


def _unescape_js(s: str) -> str:
    return (s.replace("\\\"", "\"")
             .replace("\\'", "'")
             .replace("\\n", "\n")
             .replace("\\t", "\t")
             .replace("\\\\", "\\"))


# --------------------------------------------------------------------------
# Minimal HTML element scanner (enough for well-formed authored pages)
# --------------------------------------------------------------------------
def tokenize(html: str):
    return [(m.start(), m.end(), m.group(0)) for m in TAG_RE.finditer(html)]


def _tagname(raw: str):
    if raw.startswith("<!") or raw.startswith("<?"):
        return None
    m = TAGNAME_RE.match(raw)
    return m.group(1).lower() if m else None


def _is_close(raw: str) -> bool:
    return raw.startswith("</")


def _is_void(raw: str) -> bool:
    name = _tagname(raw)
    if name in VOID_TAGS:
        return True
    # self-closing <tag .../>
    return raw.rstrip().endswith("/>")


def element_end(html: str, tokens, idx: int) -> int:
    """Given the token index of a start tag, return the index just after the
    matching close tag (or just after the start tag for void/self-closing)."""
    raw = tokens[idx][2]
    name = _tagname(raw)
    if name is None or _is_close(raw) or _is_void(raw):
        return tokens[idx][1]
    depth = 1
    j = idx + 1
    while j < len(tokens):
        r = tokens[j][2]
        if not _is_close(r) and _tagname(r) == name and not _is_void(r):
            depth += 1
        elif _is_close(r) and _tagname(r) == name:
            depth -= 1
            if depth == 0:
                return tokens[j][1]
        j += 1
    return tokens[idx][1]


def attrs_of(raw: str) -> dict:
    out = {}
    body = raw[len("<"):].strip()
    # drop the tag name
    body = body.split(None, 1)[1] if " " in body else ""
    for m in ATTR_RE.finditer(raw):
        out[m.group(1).lower()] = m.group(2) if m.group(2) is not None else (
            m.group(3) if m.group(3) is not None else m.group(4))
    return out


def has_class(attrs: dict, cls: str) -> bool:
    return cls in (attrs.get("class", "") or "").split()


# --------------------------------------------------------------------------
# Attribute surgery on a start-tag string
# --------------------------------------------------------------------------
def strip_attr(raw: str, name: str) -> str:
    return re.sub(r"\s+%s\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s\"'>]+)" % re.escape(name), "", raw)


def set_attr(raw: str, name: str, value: str) -> str:
    value = _html.escape(value, quote=True)
    if re.search(r"\s+%s\s*=" % re.escape(name), raw):
        return re.sub(r"(\s+%s\s*=\s*)(?:\"[^\"]*\"|'[^']*'|[^\s\"'>]+)" % re.escape(name),
                      lambda m: m.group(1) + '"' + value + '"', raw, count=1)
    return raw[:-1].rstrip() + ' %s="%s">' % (name, value)


# --------------------------------------------------------------------------
# The core transform: source page -> one locale page
# --------------------------------------------------------------------------
def render_locale(src_html: str, src_rel: str, lang: str, en_url: str, de_url: str, dicts: dict) -> str:
    d = dicts[lang]
    loc_url = en_url if lang == "en" else de_url
    h = src_html

    # 1) <html lang>
    h = re.sub(r"<html\b[^>]*>", '<html lang="%s">' % lang, h, count=1)

    # 2) <base href="/"> as the first child of the real <head> (root-relative
    #    asset/script/fetch resolution regardless of locale depth). Insert via
    #    the tokenizer so a literal "<head>" inside a comment is never matched.
    tokens = tokenize(h)
    for ts, te, traw in tokens:
        if not _is_close(traw) and _tagname(traw) == "head":
            h = h[:te] + '\n  <base href="/">' + h[te:]
            break

    # 3) Drop the runtime i18n engine: the text is baked in. The toggle becomes a
    #    link (step 7), so i18n.js must not run its in-place switcher here.
    h = re.sub(r"[ \t]*<script\b[^>]*\bsrc=\"[^\"]*i18n\.js\"[^>]*>\s*</script>\s*\n?", "", h)

    # 4) Handle language sections: keep the target one, delete the other.
    h = _process_lang_sections(h, lang)

    # 5) Bake data-i18n / data-i18n-aria / data-lang-en|de.
    h = _bake_i18n(h, lang, d)

    # 6) Rewrite internal links to the same locale prefix.
    h = _rewrite_links(h, src_rel, lang, en_url, de_url)

    # 7) Turn the language toggle into a link to the sibling locale.
    h = _retarget_toggle(h, lang, en_url, de_url)

    # 8) Absolute URLs of old routes anywhere left (og:url, JSON-LD, twitter)
    #    -> the locale URL. Done before canonical/hreflang are inserted so the
    #    freshly written alternates are never re-prefixed. Longest path first,
    #    and the match must end at a delimiter so '/articles/' cannot eat
    #    '/articles/01-...' and '/' cannot eat every URL.
    for rel, en, de, old in sorted(PAGES, key=lambda p: -len(p[3])):
        tgt = en if lang == "en" else de
        h = re.sub(re.escape(SITE + old) + r'(?=["\'< >\s)?#]|$)',
                   SITE + tgt, h)

    # 9) canonical + hreflang (replace any pre-existing canonical).
    h = re.sub(r"[ \t]*<link\b[^>]*\brel=\"canonical\"[^>]*>\s*\n?", "", h)
    alts = (
        '  <link rel="canonical" href="%s%s">\n'
        '  <link rel="alternate" hreflang="en" href="%s%s">\n'
        '  <link rel="alternate" hreflang="de" href="%s%s">\n'
        '  <link rel="alternate" hreflang="x-default" href="%s%s">\n'
    ) % (SITE, loc_url, SITE, en_url, SITE, de_url, SITE, en_url)
    h = h.replace("</head>", alts + "</head>", 1)

    return h


def _process_lang_sections(h: str, lang: str) -> str:
    drop_class = "lang-section-de" if lang == "en" else "lang-section-en"
    keep_class = "lang-section-en" if lang == "en" else "lang-section-de"
    tokens = tokenize(h)
    # Remove every drop-class element (walk backwards so offsets stay valid).
    removals = []
    keeps = []
    for i, (s, e, raw) in enumerate(tokens):
        if _is_close(raw) or raw.startswith("<!"):
            continue
        if _tagname(raw) not in ("div", "section"):
            continue
        attrs = attrs_of(raw)
        if has_class(attrs, drop_class):
            removals.append((s, element_end(h, tokens, i)))
        elif has_class(attrs, keep_class) and "display:none" in (attrs.get("style", "") or ""):
            keeps.append((s, e, raw))
    for s, e in reversed(removals):
        # also swallow the following newline so no blank line is left behind
        end = e + 1 if e < len(h) and h[e] == "\n" else e
        h = h[:s] + h[end:]
    # Re-tokenize: kept blocks may reference offsets from before the removals.
    if keeps:
        tokens = tokenize(h)
        fixes = []
        for i, (s, e, raw) in enumerate(tokens):
            if _is_close(raw) or raw.startswith("<!"):
                continue
            attrs = attrs_of(raw)
            if has_class(attrs, keep_class) and "display:none" in (attrs.get("style", "") or ""):
                new_style = re.sub(r"\s*display\s*:\s*none\s*;?", "", attrs["style"]).strip(" ;")
                new_raw = strip_attr(raw, "style")
                if new_style:
                    new_raw = set_attr(new_raw, "style", new_style)
                fixes.append((s, e, new_raw))
        for s, e, new_raw in reversed(fixes):
            h = h[:s] + new_raw + h[e:]
    return h


def _bake_i18n(h: str, lang: str, d: dict) -> str:
    tokens = tokenize(h)
    edits = []  # (start, end, replacement)
    for i, (s, e, raw) in enumerate(tokens):
        if _is_close(raw) or raw.startswith("<!"):
            continue
        attrs = attrs_of(raw)
        key = attrs.get("data-i18n")
        akey = attrs.get("data-i18n-aria")
        en_c = attrs.get("data-lang-en")
        de_c = attrs.get("data-lang-de")
        if not any((key, akey, en_c is not None, de_c is not None)):
            continue
        end = element_end(h, tokens, i)
        new_raw = raw
        inner = h[e:end - (0 if _is_void(raw) else len("</%s>" % _tagname(raw)))]
        # data-i18n text content
        if key and key in d:
            new_raw = set_attr(strip_attr(new_raw, "data-i18n"), "data-i18n", key)
            inner = _html.escape(d[key], quote=False)
        elif key:
            # keep attribute; leave content (defensive: unknown key)
            pass
        # aria label
        if akey and akey in d:
            new_raw = set_attr(new_raw, "aria-label", d[akey])
        # data-lang-* inner HTML
        if en_c is not None or de_c is not None:
            chosen = de_c if (lang == "de" and de_c is not None) else en_c
            if chosen is not None:
                inner = chosen
            new_raw = strip_attr(strip_attr(new_raw, "data-lang-en"), "data-lang-de")
        if _is_void(new_raw):
            edits.append((s, e, new_raw))
        else:
            close_len = len("</%s>" % _tagname(raw))
            edits.append((s, end - close_len, new_raw + inner))
    for s, e, rep in reversed(edits):
        h = h[:s] + rep + h[e:]
    return h


def _rewrite_links(h: str, src_rel: str, lang: str, en_url: str, de_url: str) -> str:
    # Map every authored page's served-as-today path to its locale URL.
    orig_to_loc = {}
    for rel, en, de, old in PAGES:
        orig_to_loc[old] = en if lang == "en" else de
    src_dir = "/" + src_rel.rsplit("/", 1)[0] + "/" if "/" in src_rel else "/"
    page_url = (en_url if lang == "en" else de_url)
    tokens = tokenize(h)
    edits = []
    for s, e, raw in tokens:
        if _is_close(raw) or raw.startswith("<!") or _tagname(raw) not in ("a", "area"):
            continue
        m = re.search(r"\s(href)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s\"'>]+)", raw)
        if not m:
            continue
        val = m.group(2)
        quote = val[0] if val[0] in "\"'" else ""
        body = val[1:-1] if quote else val
        new = _rewrite_one(body, src_dir, page_url, orig_to_loc)
        if new is None or new == body:
            continue
        new_val = (quote + new + quote) if quote else new
        edits.append((s + m.start(2), s + m.end(2), new_val))
    for s, e, rep in reversed(edits):
        h = h[:s] + rep + h[e:]
    return h


def _rewrite_one(body: str, src_dir: str, page_url: str, orig_to_loc: dict):
    b = body.strip()
    if not b or b.startswith(("http://", "https://", "mailto:", "tel:", "//", "data:")):
        return None
    if b.startswith("#"):
        return page_url + b
    if b.startswith("?"):
        return page_url + b
    if b.startswith("/"):
        # split off any ?query / #fragment so "/?slot=1" and "/manifesto/#x" work
        m = re.match(r"^([^?#]*)([?#].*)?$", b)
        path = m.group(1) or "/"
        suffix = m.group(2) or ""
        if path in orig_to_loc:
            return orig_to_loc[path] + suffix
        if path == "/":
            return page_url + suffix
        return None
    # relative -> resolve against the source page's directory
    import posixpath
    resolved = posixpath.normpath(posixpath.join(src_dir, b))
    if not resolved.startswith("/"):
        resolved = "/" + resolved
    if resolved in orig_to_loc:
        return orig_to_loc[resolved]
    return None


def _retarget_toggle(h: str, lang: str, en_url: str, de_url: str) -> str:
    sibling = de_url if lang == "en" else en_url
    if lang == "en":
        label, mob, title = "🌐 DE", "Sprache: DE", "Auf Deutsch wechseln (DE)"
    else:
        label, mob, title = "🌐 EN", "Language: EN", "Switch to English (EN)"
    tokens = tokenize(h)
    edits = []
    for i, (s, e, raw) in enumerate(tokens):
        if _is_close(raw) or raw.startswith("<!"):
            continue
        if _tagname(raw) not in ("button", "a"):
            continue
        attrs = attrs_of(raw)
        if not has_class(attrs, "btn-lang-toggle"):
            continue
        end = element_end(h, tokens, i)
        inner = h[e:end - len("</%s>" % _tagname(raw))]
        cls = attrs.get("class", "btn-lang-toggle")
        idattr = ' id="%s"' % attrs["id"] if "id" in attrs else ""
        if "lang-toggle-text" in inner:
            new_inner = '<span>🌐</span> <span class="lang-toggle-text">%s</span>' % mob
            cls = cls if "mobile-nav-link" in cls else cls + " mobile-nav-link"
        else:
            new_inner = label
        rep = '<a class="%s"%s href="%s" title="%s">%s</a>' % (
            _html.escape(cls, quote=True), idattr, _html.escape(sibling, quote=True),
            _html.escape(title, quote=True), new_inner)
        edits.append((s, end, rep))
    for s, e, rep in reversed(edits):
        h = h[:s] + rep + h[e:]
    return h


# --------------------------------------------------------------------------
# Output paths + redirect stubs
# --------------------------------------------------------------------------
def out_path(rel: str, lang: str) -> str:
    if rel.endswith("index.html"):
        d = rel[: -len("index.html")].rstrip("/")
        return os.path.join(WEB_DIR, lang, d, "index.html") if d else os.path.join(WEB_DIR, lang, "index.html")
    stem = rel[:-len(".html")]
    return os.path.join(WEB_DIR, lang, stem, "index.html")


def redirect_stub(en_url: str, de_url: str) -> str:
    tgt = en_url
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="UTF-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        "  <title>Redirecting… — The First Wall</title>\n"
        '  <meta http-equiv="refresh" content="0; url=%s">\n'
        '  <link rel="canonical" href="%s%s">\n'
        '  <link rel="alternate" hreflang="en" href="%s%s">\n'
        '  <link rel="alternate" hreflang="de" href="%s%s">\n'
        '  <link rel="alternate" hreflang="x-default" href="%s%s">\n'
        '  <link rel="icon" type="image/svg+xml" href="/favicon.svg">\n'
        "</head>\n"
        "<body>\n"
        "  <p>This page has moved to a language-specific address.</p>\n"
        '  <p><a href="%s">Continue to the English version</a> · '
        '<a href="%s">Zur deutschen Version</a></p>\n'
        '  <noscript><p><a href="%s">Continue / Weiter</a></p></noscript>\n'
        "</body>\n"
        "</html>\n"
    ) % (tgt, SITE, tgt, SITE, en_url, SITE, de_url, SITE, en_url, tgt, de_url, tgt)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def build() -> list:
    dicts = parse_i18n_js(os.path.join(WEB_DIR, "i18n.js"))
    written = []
    for rel, en_url, de_url, old in PAGES:
        src_path = os.path.join(SRC_DIR, rel)
        src_html = _read(src_path)
        for lang in ("en", "de"):
            out = render_locale(src_html, rel, lang, en_url, de_url, dicts)
            path = out_path(rel, lang)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            _write(path, out)
            written.append(os.path.relpath(path, WEB_DIR))
        # redirect stub at the original, served-as-today URL
        stub_path = os.path.join(WEB_DIR, old.lstrip("/"))
        if old.endswith("/"):
            stub_path = os.path.join(WEB_DIR, old.strip("/"), "index.html")
        os.makedirs(os.path.dirname(stub_path), exist_ok=True)
        _write(stub_path, redirect_stub(en_url, de_url))
        written.append(os.path.relpath(stub_path, WEB_DIR))
    return written


def main() -> int:
    written = build()
    if "--check" in sys.argv:
        # Rebuild into memory and compare to what is on disk.
        stale = []
        dicts = parse_i18n_js(os.path.join(WEB_DIR, "i18n.js"))
        for rel, en_url, de_url, old in PAGES:
            src_html = _read(os.path.join(SRC_DIR, rel))
            for lang in ("en", "de"):
                want = render_locale(src_html, rel, lang, en_url, de_url, dicts)
                p = out_path(rel, lang)
                have = _read(p) if os.path.exists(p) else None
                if have != want:
                    stale.append(os.path.relpath(p, WEB_DIR))
            stub_path = os.path.join(WEB_DIR, old.lstrip("/"))
            if old.endswith("/"):
                stub_path = os.path.join(WEB_DIR, old.strip("/"), "index.html")
            want = redirect_stub(en_url, de_url)
            have = _read(stub_path) if os.path.exists(stub_path) else None
            if have != want:
                stale.append(os.path.relpath(stub_path, WEB_DIR))
        if stale:
            print("STALE locale output:", ", ".join(stale))
            return 1
        print("locale output up to date")
        return 0
    print("wrote %d file(s):" % len(written))
    for w in written:
        print("  " + w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
