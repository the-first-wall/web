#!/usr/bin/env python3
"""Tests for scripts/build_i18n.py — the English and German page builder.

    python scripts/test_build_i18n.py

English is served at the URLs the site always had, German under /de/. The
guarantees the SEO change depends on: each page carries ONE language, baked into
the raw HTML, and the other one does not leak; canonical/hreflang are right;
internal links stay in the page's language; nothing redirects (so every shared
link keeps working); the site's own files are addressed from the root; and the DOM
contract app.js / i18n.js rely on survives the transform.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_spec = importlib.util.spec_from_file_location("build_i18n", os.path.join(HERE, "build_i18n.py"))
bi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bi)
import build_chrome  # noqa: E402

WEB = bi.WEB_DIR
NODE = shutil.which("node")


def read(rel):
    with open(os.path.join(WEB, rel), encoding="utf-8") as fh:
        return fh.read()


def node(js):
    """Run `js` in node (cwd = the web root), return its stdout."""
    return subprocess.run([NODE, "-e", js], cwd=WEB, check=True, capture_output=True, text=True).stdout.strip()


class OutputIsCurrent(unittest.TestCase):
    def test_committed_pages_match_the_sources(self):
        self.assertEqual(bi.check(), [], "run: python scripts/build_i18n.py")

    def test_every_generated_file_is_marked_and_skipped_by_the_chrome_build(self):
        paths = list(bi.expected())
        self.assertEqual(len(paths), 2 * len(bi.PAGES))
        listed = set(build_chrome.pages())
        for p in paths:
            self.assertTrue(build_chrome.is_generated(p), p)
            self.assertNotIn(p, listed)
        for rel in bi.PAGES:                       # ...while the authored pages are stamped
            self.assertIn(bi.src_path(rel), listed)

    def test_the_urls_mirror_each_other(self):
        self.assertEqual(bi.en_url("index.html"), "/")
        self.assertEqual(bi.de_url("index.html"), "/de/")
        self.assertEqual(bi.en_url("manifesto/index.html"), "/manifesto/")
        self.assertEqual(bi.de_url("manifesto/index.html"), "/de/manifesto/")
        self.assertEqual(bi.en_url("articles/01-make-your-blip-count.html"), "/articles/01-make-your-blip-count.html")
        self.assertEqual(bi.de_url("articles/01-make-your-blip-count.html"), "/de/articles/01-make-your-blip-count.html")
        for rel in bi.PAGES:                       # a URL maps to a file of the same path
            self.assertEqual(os.path.relpath(bi.out_path(rel, "en"), WEB), rel.replace("/", os.sep))
            self.assertEqual(os.path.relpath(bi.out_path(rel, "de"), WEB), os.path.join("de", *rel.split("/")))


class BakedText(unittest.TestCase):
    def test_each_page_carries_one_language_without_js(self):
        en, de = read("index.html"), read("de/index.html")
        self.assertIn("You were born with a timer you cannot see.", en)
        self.assertNotIn("Du wurdest mit einem Timer geboren", en)
        self.assertIn("Du wurdest mit einem Timer geboren", de)
        self.assertNotIn("You were born with a timer you cannot see.", de)

    def test_the_other_language_section_is_removed_not_hidden(self):
        en = read("articles/01-make-your-blip-count.html")
        de = read("de/articles/01-make-your-blip-count.html")
        self.assertIn("The Invisible Timer", en)
        self.assertNotIn("Der unsichtbare Timer", en)
        self.assertIn("Der unsichtbare Timer", de)
        self.assertNotIn("The Invisible Timer", de)
        self.assertNotIn('class="lang-section-de"', en)
        self.assertNotIn('class="lang-section-en"', de)

    def test_the_sources_keep_both_languages_and_are_not_what_is_served(self):
        src = read("src/articles/01-make-your-blip-count.html")
        self.assertIn("The Invisible Timer", src)
        self.assertIn("Der unsichtbare Timer", src)

    def test_aria_labels_and_titles_are_baked(self):
        en, de = read("index.html"), read("de/index.html")
        self.assertIn('title="Previous block (←)"', en)
        self.assertIn('title="Vorheriger Block (←)"', de)
        self.assertIn('aria-label="Vorheriger Block (←)"', de)

    def test_html_lang_and_the_route_marker_i18n_js_reads(self):
        self.assertIn('<html lang="en" data-locale-route="en">', read("index.html"))
        self.assertIn('<html lang="de" data-locale-route="de">', read("de/index.html"))

    @unittest.skipUnless(NODE, "node not available")
    def test_dictionaries_match_what_i18n_js_really_evaluates_to(self):
        js = ("const fs=require('fs');const vm=require('vm');const c={window:{addEventListener(){}}};"
              "vm.createContext(c);vm.runInContext(fs.readFileSync('i18n.js','utf8')"
              "+';this.T=TFW_TRANSLATIONS;',c);console.log(JSON.stringify(c.T));")
        real = json.loads(node(js))
        parsed = bi.parse_i18n_js(os.path.join(WEB, "i18n.js"))
        for lang in bi.LANGS:
            self.assertEqual(parsed[lang], real[lang], "build_i18n parses the %s dictionary differently" % lang)


class BakingUnits(unittest.TestCase):
    D = {"nav": "N", "prev": "P", "outer": "O", "inner": "I"}

    def test_an_attribute_only_edit_keeps_its_children_baked(self):
        # the sidebar's browse row carries data-i18n-aria and holds data-i18n-title buttons
        h = '<div data-i18n-aria="nav"><button data-i18n-title="prev" title="x">a</button><span data-i18n="prev">x</span></div>'
        out = bi._bake_i18n(h, "de", self.D)
        self.assertEqual(out, '<div data-i18n-aria="nav" aria-label="N"><button data-i18n-title="prev" title="P">a</button>'
                              '<span data-i18n="prev">P</span></div>')

    def test_replaced_content_swallows_what_was_nested_in_it(self):
        out = bi._bake_i18n('<p data-i18n="outer"><b data-i18n="inner">x</b></p>', "en", self.D)
        self.assertEqual(out, '<p data-i18n="outer">O</p>')

    def test_unknown_keys_and_plain_markup_are_left_alone(self):
        h = '<p data-i18n="nope">keep</p><a href="/x">y</a>'
        self.assertEqual(bi._bake_i18n(h, "de", self.D), h)

    def test_text_is_escaped_like_textContent(self):
        self.assertEqual(bi._bake_i18n('<p data-i18n="a">x</p>', "en", {"a": "<b>&</b>"}), '<p data-i18n="a">&lt;b&gt;&amp;&lt;/b&gt;</p>')

    def test_data_lang_attributes_pick_the_pages_language(self):
        h = '<p data-lang-en="Hi <b>you</b>" data-lang-de="Hallo <b>du</b>">x</p>'
        self.assertEqual(bi._bake_i18n(h, "en", {}), '<p>Hi <b>you</b></p>')
        self.assertEqual(bi._bake_i18n(h, "de", {}), '<p>Hallo <b>du</b></p>')


class Metadata(unittest.TestCase):
    def test_canonical_and_hreflang_pair_on_both_languages(self):
        for rel, own in (("articles/index.html", "/articles/"), ("de/articles/index.html", "/de/articles/")):
            page = read(rel)
            self.assertIn('<link rel="canonical" href="https://thefirstwall.ai%s">' % own, page)
            self.assertIn('<link rel="alternate" hreflang="en" href="https://thefirstwall.ai/articles/">', page)
            self.assertIn('<link rel="alternate" hreflang="de" href="https://thefirstwall.ai/de/articles/">', page)
            self.assertIn('<link rel="alternate" hreflang="x-default" href="https://thefirstwall.ai/articles/">', page)
            self.assertEqual(page.count('rel="canonical"'), 1)

    def test_social_urls_follow_the_pages_language(self):
        self.assertIn('property="og:url" content="https://thefirstwall.ai/de/manifesto/"', read("de/manifesto/index.html"))
        self.assertIn('property="og:url" content="https://thefirstwall.ai/manifesto/"', read("manifesto/index.html"))
        de_home, en_home = read("de/index.html"), read("index.html")
        self.assertIn('"url": "https://thefirstwall.ai/de/census/"', de_home)       # the JSON-LD dataset
        self.assertIn('"url": "https://thefirstwall.ai/census/"', en_home)
        self.assertIn("https://thefirstwall.ai/assets/the-first-wall-avatar-500.png", de_home)   # not a page: untouched

    def test_legal_pages_stay_put_and_are_german_only(self):
        for rel in bi.LEGAL_PAGES:
            page = read(rel)
            self.assertNotIn('http-equiv="refresh"', page)
            self.assertIn('<html lang="de">', page)
            self.assertIn('hreflang="x-default"', page)
            self.assertNotIn('hreflang="en"', page)       # no false English twin

    def test_nothing_redirects(self):
        for path in bi.expected():
            page = open(path, encoding="utf-8").read()
            self.assertNotIn('http-equiv="refresh"', page, path)
        self.assertFalse(os.path.exists(os.path.join(WEB, "en")), "the /en/ tree is gone: English is the base")
        self.assertFalse(os.path.exists(os.path.join(WEB, "locale-redirect.js")))


class Links(unittest.TestCase):
    @staticmethod
    def _hrefs(page):
        return re.findall(r'<a\b[^>]*\shref="([^"]*)"', page)

    def test_german_pages_link_to_german_pages(self):
        de = read("de/articles/index.html")
        self.assertIn('href="/de/articles/01-make-your-blip-count.html"', de)
        self.assertIn('href="/de/manifesto/"', de)
        self.assertIn('href="/de/census/"', de)
        self.assertIn('href="/legal/impressum.html"', de)        # German-only: the same page from both
        self.assertIn('href="/skill.md"', de)                    # machine endpoints are not localized
        self.assertNotIn('href="/de/skill.md"', de)

    def test_english_pages_stay_in_english(self):
        for rel in bi.PAGES:
            page = read(rel)
            for href in self._hrefs(page):
                if href.startswith("/de/"):
                    self.assertRegex(page, r'class="[^"]*btn-lang-toggle[^"]*"[^>]*href="%s"|href="%s"[^>]*class="[^"]*btn-lang-toggle' % (re.escape(href), re.escape(href)),
                                     "%s links into /de/ from somewhere other than the language toggle: %s" % (rel, href))

    def test_no_german_page_links_back_to_an_english_twin(self):
        english = {e for _r, e, _g in bi.ROUTES}
        for rel in bi.PAGES:
            page = read("de/" + rel)
            for href in self._hrefs(page):
                path = re.split(r"[?#]", href)[0]
                if path in english:
                    self.assertRegex(page, r'class="[^"]*btn-lang-toggle[^"]*"[^>]*href="%s"' % re.escape(href),
                                     "de/%s links to the English page %s" % (rel, href))

    def test_deep_links_keep_their_query(self):
        h = bi._rewrite_links('<a href="/?slot=7">x</a>', "faq/index.html", "de")
        self.assertEqual(h, '<a href="/de/?slot=7">x</a>')
        h = bi._rewrite_links('<a href="/manifesto/#x">x</a>', "faq/index.html", "de")
        self.assertEqual(h, '<a href="/de/manifesto/#x">x</a>')
        for url in ('<a href="/?slot=7">x</a>', '<a href="/manifesto/#x">x</a>'):    # English: untouched
            self.assertEqual(bi._rewrite_links(url, "faq/index.html", "en"), url)
        for url in ('<a href="#top">x</a>', '<a href="?slot=2">x</a>', '<a href="https://example.com/">x</a>'):
            self.assertEqual(bi._rewrite_links(url, "faq/index.html", "de"), url)

    def test_local_files_are_addressed_from_the_root(self):
        # the German pages sit one level deeper: no page-relative file references anywhere
        for path in list(bi.expected()) + [bi.src_path(r) for r in bi.PAGES]:
            page = open(path, encoding="utf-8").read()
            for ref in re.findall(r'\b(?:src|href)="([^"]*)"', page):
                if ref.startswith(("/", "http", "#", "mailto:", "tel:", "data:", "?")) or not ref:
                    continue
                self.fail("%s: page-relative reference %r" % (os.path.relpath(path, WEB), ref))


class Toggle(unittest.TestCase):
    def test_toggle_is_a_link_to_the_same_page_in_the_other_language(self):
        en, de = read("manifesto/index.html"), read("de/manifesto/index.html")
        self.assertIn('<a class="tool-btn btn-lang-toggle" id="btn-lang-toggle" href="/de/manifesto/" hreflang="de" lang="de" title="Auf Deutsch wechseln (DE)">🌐 EN</a>', en)
        self.assertIn('<a class="tool-btn btn-lang-toggle" id="btn-lang-toggle" href="/manifesto/" hreflang="en" lang="en" title="Switch to English (EN)">🌐 DE</a>', de)
        # the mobile menu's toggle keeps its structure (i18n.js writes into .lang-toggle-text)
        self.assertRegex(en, r'btn-lang-toggle" href="/de/manifesto/"[^>]*>\s*<span aria-hidden="true">🌐</span> <span class="lang-toggle-text">Language: EN</span>')
        self.assertRegex(de, r'btn-lang-toggle" href="/manifesto/"[^>]*>\s*<span aria-hidden="true">🌐</span> <span class="lang-toggle-text">Sprache: DE</span>')

    def test_the_home_toggle_goes_home_in_the_other_language(self):
        self.assertIn('href="/de/" hreflang="de"', read("index.html"))
        self.assertIn('href="/" hreflang="en"', read("de/index.html"))

    def test_i18n_js_stays_loaded_because_app_js_calls_t(self):
        for rel in ("index.html", "de/index.html"):
            self.assertRegex(read(rel), r'<script src="/i18n\.js\?v=[0-9a-f]{10}"></script>')

    @unittest.skipUnless(NODE, "node not available")
    def test_i18n_js_takes_the_language_from_the_route_and_leaves_link_toggles_alone(self):
        out = node(r"""
          const fs = require('fs'), vm = require('vm');
          function pick(route, saved, nav) {
            const ctx = {
              document: { documentElement: { getAttribute: (n) => (n === 'data-locale-route' ? route : null) },
                          querySelectorAll: () => [] },
              localStorage: { getItem: () => saved, setItem() {} },
              navigator: { language: nav }, window: { addEventListener() {} },
            };
            vm.createContext(ctx);
            vm.runInContext(fs.readFileSync('i18n.js', 'utf8'), ctx);
            return ctx.getInitialLanguage();
          }
          console.log(JSON.stringify([
            pick('de', 'en', 'en-US'),   // /de/ wins over a stored English choice
            pick('en', 'de', 'de-DE'),   // an English page stays English for a German browser
            pick(null, 'de', 'en-US'),   // no route (block pages): the stored choice
            pick(null, null, 'de-AT'),   // ... else the browser
            pick(null, null, 'fr'),
          ]));
        """)
        self.assertEqual(json.loads(out), ["de", "en", "de", "de", "en"])

    @unittest.skipUnless(NODE, "node not available")
    def test_a_link_toggle_carries_the_query_and_fragment_but_not_new_tab_clicks(self):
        out = node(r"""
          const fs = require('fs'), vm = require('vm');
          const handlers = {}; let went = null;
          const link = { tagName: 'A', getAttribute: (n) => (n === 'href' ? '/de/' : null), querySelector: () => null, addEventListener: (t, f) => { handlers.click = f; } };
          const ctx = {
            document: { documentElement: { getAttribute: () => 'en', setAttribute() {} }, querySelectorAll: (sel) => (/btn-lang-toggle/.test(sel) ? [link] : []) },
            localStorage: { getItem: () => null, setItem() {} }, navigator: { language: 'en' },
            location: { search: '?slot=3', hash: '#x', assign: (u) => { went = u; } },
            window: { addEventListener: (t, f) => { if (t === 'DOMContentLoaded') handlers.ready = f; } },
          };
          vm.createContext(ctx); vm.runInContext(fs.readFileSync('i18n.js', 'utf8'), ctx);
          handlers.ready();
          const click = (e) => { went = null; let prevented = false; handlers.click(Object.assign({ defaultPrevented: false, button: 0, preventDefault() { prevented = true; } }, e)); return [went, prevented]; };
          console.log(JSON.stringify([click({}), click({ ctrlKey: true }), click({ metaKey: true }), click({ button: 1 })]));
        """)
        self.assertEqual(json.loads(out), [["/de/?slot=3#x", True], [None, False], [None, False], [None, False]])


class DomContract(unittest.TestCase):
    def test_every_id_of_the_source_page_survives_in_both_languages(self):
        ids = set(re.findall(r'\bid="([^"]+)"', read("src/index.html")))
        self.assertTrue(ids)
        for rel in ("index.html", "de/index.html"):
            out = read(rel)
            for i in ids:
                self.assertIn('id="%s"' % i, out, "%s lost #%s" % (rel, i))

    def test_build_is_deterministic(self):
        dicts = bi.parse_i18n_js(os.path.join(WEB, "i18n.js"))
        src = read("src/manifesto/index.html")
        for lang in bi.LANGS:
            self.assertEqual(bi.render_locale(src, "manifesto/index.html", lang, dicts),
                             bi.render_locale(src, "manifesto/index.html", lang, dicts))


class SitemapMatchesRoutes(unittest.TestCase):
    def test_sitemap_lists_both_languages_of_every_page(self):
        spec = importlib.util.spec_from_file_location("sync_from_ledger", os.path.join(HERE, "sync_from_ledger.py"))
        sync = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sync)
        sm = sync.build_sitemap([], {})
        for _rel, en, de in bi.ROUTES:
            self.assertIn("<loc>https://thefirstwall.ai%s</loc>" % en, sm)
            self.assertIn("<loc>https://thefirstwall.ai%s</loc>" % de, sm)


class Builder(unittest.TestCase):
    """build()/check() on a scratch copy: staleness and orphans are detected, and fixed."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tfw-i18n-")
        shutil.copytree(os.path.join(WEB, "src"), os.path.join(self.tmp, "src"))
        shutil.copy(os.path.join(WEB, "i18n.js"), self.tmp)
        self.saved = (bi.WEB_DIR, bi.SRC_DIR)
        bi.WEB_DIR, bi.SRC_DIR = self.tmp, os.path.join(self.tmp, "src")
        bi.build()

    def tearDown(self):
        bi.WEB_DIR, bi.SRC_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fresh_build_is_current_and_idempotent(self):
        self.assertEqual(bi.check(), [])
        first = {p: open(p, encoding="utf-8").read() for p in bi.expected()}
        bi.build()
        self.assertEqual(first, {p: open(p, encoding="utf-8").read() for p in bi.expected()})

    def test_edit_to_a_source_page_makes_both_languages_stale(self):
        p = os.path.join(self.tmp, "src", "faq", "index.html")
        text = open(p, encoding="utf-8").read()
        open(p, "w", encoding="utf-8").write(text.replace("<main", "<main data-edited", 1))
        stale = bi.check()
        self.assertIn(os.path.join("faq", "index.html"), stale)
        self.assertIn(os.path.join("de", "faq", "index.html"), stale)

    def test_hand_edited_output_is_detected_and_overwritten(self):
        p = os.path.join(self.tmp, "de", "index.html")
        open(p, "a", encoding="utf-8").write("<!-- hand edit -->")
        self.assertEqual(bi.check(), [os.path.join("de", "index.html")])
        bi.build()
        self.assertEqual(bi.check(), [])

    def test_orphans_are_reported_and_removed_but_authored_pages_are_not(self):
        orphan = os.path.join(self.tmp, "de", "gone", "index.html")
        os.makedirs(os.path.dirname(orphan))
        open(orphan, "w").write("<!DOCTYPE html>\n<!-- %s from src/gone.html -->\n" % bi.MARK)
        legal = os.path.join(self.tmp, "legal", "impressum.html")
        os.makedirs(os.path.dirname(legal))
        open(legal, "w").write("<!DOCTYPE html>\n<html lang=\"de\"></html>\n")        # authored, not generated
        self.assertTrue(any("gone" in s for s in bi.check()))
        self.assertFalse(any("legal" in s for s in bi.check()))
        bi.build()
        self.assertFalse(os.path.exists(orphan))
        self.assertTrue(os.path.exists(legal))
        self.assertEqual(bi.check(), [])


if __name__ == "__main__":
    unittest.main()
