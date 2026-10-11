#!/usr/bin/env python3
"""Tests for scripts/build_i18n.py — the locale route builder.

    python scripts/test_build_i18n.py

Covers the guarantees the SEO change depends on: the target-locale text is
baked into the raw HTML, the other locale does not leak, canonical/hreflang are
correct, internal links carry the locale prefix, the old URLs redirect to /en/,
and the DOM contract app.js/i18n.js rely on survives the transform.
"""
import importlib.util
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("build_i18n", os.path.join(HERE, "build_i18n.py"))
bi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bi)

WEB = bi.WEB_DIR


def read(rel):
    with open(os.path.join(WEB, rel), encoding="utf-8") as fh:
        return fh.read()


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bi.build()  # idempotent; ensures output is present and fresh

    # --- text is baked into the raw HTML ------------------------------------
    def test_en_text_baked_without_js(self):
        page = read("en/index.html")
        self.assertIn("You were born with a timer you cannot see.", page)
        # the DE headline must NOT appear on the EN page
        self.assertNotIn("Du wurdest mit einem Timer geboren", page)

    def test_de_text_baked_without_js(self):
        page = read("de/index.html")
        self.assertIn("Du wurdest mit einem Timer geboren", page)
        self.assertNotIn("You were born with a timer you cannot see.", page)

    def test_other_lang_section_is_removed(self):
        en_article = read("en/articles/01-make-your-blip-count/index.html")
        de_article = read("de/articles/01-make-your-blip-count/index.html")
        self.assertIn("The Invisible Timer", en_article)
        self.assertNotIn("Der unsichtbare Timer", en_article)
        self.assertIn("Der unsichtbare Timer", de_article)
        self.assertNotIn("The Invisible Timer", de_article)
        # no hidden duplicate language block survives anywhere
        self.assertNotIn('class="lang-section-de"', en_article)
        self.assertNotIn('class="lang-section-en"', de_article)

    # --- <html lang> --------------------------------------------------------
    def test_html_lang(self):
        self.assertRegex(read("en/index.html"), r'<html lang="en">')
        self.assertRegex(read("de/index.html"), r'<html lang="de">')

    # --- canonical + hreflang ----------------------------------------------
    def test_canonical_and_hreflang_pair(self):
        for rel, loc, other in (("en/articles/index.html", "/en/articles/", "/de/articles/"),
                                ("de/articles/index.html", "/de/articles/", "/en/articles/")):
            page = read(rel)
            self.assertIn('<link rel="canonical" href="https://thefirstwall.ai%s">' % loc, page)
            self.assertIn('<link rel="alternate" hreflang="en" href="https://thefirstwall.ai/en/articles/">', page)
            self.assertIn('<link rel="alternate" hreflang="de" href="https://thefirstwall.ai/de/articles/">', page)
            self.assertIn('<link rel="alternate" hreflang="x-default" href="https://thefirstwall.ai/en/articles/">', page)

    def test_x_default_is_english(self):
        page = read("de/security/index.html")
        m = re.search(r'hreflang="x-default" href="([^"]+)"', page)
        self.assertTrue(m and m.group(1).startswith("https://thefirstwall.ai/en/"))

    # --- internal links carry the locale prefix -----------------------------
    def test_internal_links_are_locale_prefixed(self):
        en = read("en/articles/index.html")
        de = read("de/articles/index.html")
        self.assertIn('href="/en/articles/01-make-your-blip-count/"', en)
        self.assertIn('href="/de/articles/01-make-your-blip-count/"', de)
        self.assertIn('href="/en/manifesto/"', en)
        self.assertIn('href="/de/manifesto/"', de)
        # legal pages are German-only and keep their path from both locales
        self.assertIn('href="/legal/impressum.html"', en)
        self.assertIn('href="/legal/impressum.html"', de)
        # machine endpoints are not localized
        self.assertIn('href="/skill.md"', en)
        self.assertNotIn('href="/en/skill.md"', en)

    def test_query_deeplink_is_locale_prefixed(self):
        # the "return to canvas" links (?slot=1) must point at the locale root
        self.assertIn('href="/en/?slot=1"', read("en/manifesto/index.html"))
        self.assertIn('href="/de/?slot=1"', read("de/manifesto/index.html"))
        self.assertNotIn('href="/?slot=1"', read("en/manifesto/index.html"))

    def test_no_link_points_at_a_redirect_stub(self):
        # No generated page may link to a bare (old) route that now redirects.
        for rel in ("en/index.html", "de/index.html", "en/articles/index.html",
                    "de/chronicle/index.html", "en/manifesto/index.html"):
            page = read(rel)
            for old in ('href="/articles/"', 'href="/manifesto/"',
                        'href="/chronicle/"', 'href="/security/"'):
                self.assertNotIn(old, page, "%s links to redirect stub %s" % (rel, old))

    # --- the runtime toggle is a locale link --------------------------------
    def test_toggle_is_a_locale_link(self):
        en = read("en/index.html")
        de = read("de/index.html")
        self.assertIn('class="tool-btn btn-lang-toggle"', en)
        self.assertIn('href="/de/"', en)
        self.assertIn('href="/en/"', de)
        # i18n.js must not be loaded on the generated (baked) pages
        self.assertNotIn("i18n.js", en)
        self.assertNotIn("i18n.js", de)

    # --- redirect stubs -----------------------------------------------------
    def test_old_urls_redirect_to_en(self):
        self.assertIn('<meta http-equiv="refresh" content="0; url=/en/">', read("index.html"))
        self.assertIn('url=/en/articles/01-make-your-blip-count/',
                      read("articles/01-make-your-blip-count.html"))
        self.assertIn('url=/en/articles/', read("articles/index.html"))
        self.assertIn('url=/en/manifesto/', read("manifesto/index.html"))
        self.assertIn('url=/en/chronicle/', read("chronicle/index.html"))
        self.assertIn('url=/en/security/', read("security/index.html"))
        # a visible fallback link exists for non-JS / crawler users
        self.assertIn('<a href="/en/articles/01-make-your-blip-count/">',
                      read("articles/01-make-your-blip-count.html"))

    def test_legal_pages_are_not_redirect_stubs(self):
        for rel in bi.LEGAL_PAGES:
            page = read(rel)
            self.assertNotIn('http-equiv="refresh"', page)
            self.assertIn('<html lang="de">', page)
            self.assertIn('hreflang="x-default"', page)
            self.assertNotIn('hreflang="en"', page)   # no false EN twin

    # --- the DOM contract survives the transform ----------------------------
    def test_dom_ids_preserved_from_source(self):
        src = read("src/index.html")
        out = read("en/index.html")
        ids = re.findall(r'\bid="([^"]+)"', src)
        self.assertTrue(ids)
        for i in set(ids):
            self.assertIn('id="%s"' % i, out, "generated page lost #%s" % i)

    def test_required_selectors_survive(self):
        out = read("en/index.html")
        for sel in (".btn-lang-toggle", ".lang-toggle-text", ".codex-tab-btn",
                    ".codex-tab-panel", ".btn-copy-small"):
            cls = sel[1:]
            self.assertRegex(out, r'class="[^"]*\b%s\b' % re.escape(cls))

    # --- determinism --------------------------------------------------------
    def test_build_is_deterministic(self):
        dicts = bi.parse_i18n_js(os.path.join(WEB, "i18n.js"))
        rel, en_url, de_url, _old = bi.PAGES[1]  # manifesto
        src = read("src/" + rel)
        a = bi.render_locale(src, rel, "en", en_url, de_url, dicts)
        b = bi.render_locale(src, rel, "en", en_url, de_url, dicts)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
