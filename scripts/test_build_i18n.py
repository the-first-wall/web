#!/usr/bin/env python3
"""Tests for scripts/build_i18n.py — the language route builder (/en/, /de/).

    python scripts/test_build_i18n.py

The guarantees the SEO change depends on: the target language's text is baked
into the raw HTML and the other one does not leak, canonical/hreflang are right,
internal links carry the language prefix, the old URLs are redirect stubs that
keep the query string, the site's own files are addressed from the root, and
the DOM contract app.js / i18n.js rely on survives the transform.
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


def node(js, **env):
    """Run `js` in node, return its stdout."""
    return subprocess.run([NODE, "-e", js], check=True, capture_output=True, text=True,
                          env=dict(os.environ, **env)).stdout.strip()


class OutputIsCurrent(unittest.TestCase):
    def test_committed_output_matches_the_sources(self):
        self.assertEqual(bi.check(), [], "run: python scripts/build_i18n.py")

    def test_every_generated_file_is_marked_and_skipped_by_the_chrome_build(self):
        paths = list(bi.expected())
        self.assertTrue(paths)
        for p in paths:
            self.assertTrue(build_chrome.is_generated(p), p)
        listed = set(build_chrome.pages())
        for p in paths:
            self.assertNotIn(p, listed)
        # ...while the authored pages are stamped
        for rel, *_ in bi.PAGES:
            self.assertIn(os.path.join(WEB, "src", rel), listed)


class BakedText(unittest.TestCase):
    def test_en_and_de_text_are_baked_without_js(self):
        en, de = read("en/index.html"), read("de/index.html")
        self.assertIn("You were born with a timer you cannot see.", en)
        self.assertNotIn("Du wurdest mit einem Timer geboren", en)
        self.assertIn("Du wurdest mit einem Timer geboren", de)
        self.assertNotIn("You were born with a timer you cannot see.", de)

    def test_the_other_language_section_is_removed_not_hidden(self):
        en = read("en/articles/01-make-your-blip-count/index.html")
        de = read("de/articles/01-make-your-blip-count/index.html")
        self.assertIn("The Invisible Timer", en)
        self.assertNotIn("Der unsichtbare Timer", en)
        self.assertIn("Der unsichtbare Timer", de)
        self.assertNotIn("The Invisible Timer", de)
        self.assertNotIn('class="lang-section-de"', en)
        self.assertNotIn('class="lang-section-en"', de)

    def test_aria_labels_and_titles_are_baked(self):
        # data-i18n-aria / data-i18n-title carry the page's language
        en, de = read("en/index.html"), read("de/index.html")
        self.assertIn('title="Previous block (←)"', en)
        self.assertIn('title="Vorheriger Block (←)"', de)
        self.assertIn('aria-label="Vorheriger Block (←)"', de)

    def test_html_lang_and_the_route_marker_i18n_js_reads(self):
        self.assertIn('<html lang="en" data-locale-route="en">', read("en/index.html"))
        self.assertIn('<html lang="de" data-locale-route="de">', read("de/index.html"))

    def test_dictionaries_match_what_i18n_js_really_evaluates_to(self):
        if not NODE:
            self.skipTest("node not available")
        js = ("const fs=require('fs');const vm=require('vm');const c={window:{addEventListener(){}}};"
              "vm.createContext(c);vm.runInContext(fs.readFileSync('i18n.js','utf8')"
              "+';this.T=TFW_TRANSLATIONS;',c);console.log(JSON.stringify(c.T));")
        real = json.loads(subprocess.run([NODE, "-e", js], cwd=WEB, check=True, capture_output=True, text=True).stdout)
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


class Metadata(unittest.TestCase):
    def test_canonical_and_hreflang_pair(self):
        for rel, loc in (("en/articles/index.html", "/en/articles/"), ("de/articles/index.html", "/de/articles/")):
            page = read(rel)
            self.assertIn('<link rel="canonical" href="https://thefirstwall.ai%s">' % loc, page)
            self.assertIn('<link rel="alternate" hreflang="en" href="https://thefirstwall.ai/en/articles/">', page)
            self.assertIn('<link rel="alternate" hreflang="de" href="https://thefirstwall.ai/de/articles/">', page)
            self.assertIn('<link rel="alternate" hreflang="x-default" href="https://thefirstwall.ai/en/articles/">', page)
            self.assertEqual(page.count('rel="canonical"'), 1)

    def test_social_urls_follow_the_language_page(self):
        self.assertIn('property="og:url" content="https://thefirstwall.ai/de/manifesto/"', read("de/manifesto/index.html"))
        home = read("de/index.html")
        self.assertIn('"url": "https://thefirstwall.ai/de/census/"', home)          # the JSON-LD dataset
        self.assertIn("https://thefirstwall.ai/assets/the-first-wall-avatar-500.png", home)   # not an old route

    def test_legal_pages_stay_put_and_are_german_only(self):
        for rel in bi.LEGAL_PAGES:
            page = read(rel)
            self.assertNotIn("http-equiv=\"refresh\"", page)
            self.assertIn('<html lang="de">', page)
            self.assertIn('hreflang="x-default"', page)
            self.assertNotIn('hreflang="en"', page)       # no false English twin


class Links(unittest.TestCase):
    def test_internal_links_carry_the_language_prefix(self):
        en, de = read("en/articles/index.html"), read("de/articles/index.html")
        self.assertIn('href="/en/articles/01-make-your-blip-count/"', en)
        self.assertIn('href="/de/articles/01-make-your-blip-count/"', de)
        self.assertIn('href="/en/manifesto/"', en)
        self.assertIn('href="/de/census/"', de)
        self.assertIn('href="/legal/impressum.html"', en)       # German-only: same page from both
        self.assertIn('href="/skill.md"', en)                   # machine endpoints are not localized
        self.assertNotIn('href="/en/skill.md"', en)

    def test_no_language_page_links_to_a_redirect_stub(self):
        stubs = {old for _r, _e, _d, old in bi.PAGES}
        for path in bi.expected():
            rel = os.path.relpath(path, WEB)
            if not rel.startswith(("en" + os.sep, "de" + os.sep)):
                continue                                          # the stubs themselves
            page = open(path, encoding="utf-8").read()
            for href in re.findall(r'<a\b[^>]*\shref="([^"#?]*)', page):
                self.assertNotIn(href, stubs, "%s links to the redirect stub %s" % (rel, href))

    def test_deep_links_keep_their_query(self):
        # a /?slot=N link on a source page becomes /<lang>/?slot=N
        h = bi._rewrite_links('<a href="/?slot=7">x</a>', "faq/index.html", "de", "/en/faq/", "/de/faq/")
        self.assertEqual(h, '<a href="/de/?slot=7">x</a>')
        h = bi._rewrite_links('<a href="/manifesto/#x">x</a>', "faq/index.html", "en", "/en/faq/", "/de/faq/")
        self.assertEqual(h, '<a href="/en/manifesto/#x">x</a>')

    def test_local_files_are_addressed_from_the_root(self):
        # a generated page sits one level deeper than the old one: no page-relative file refs
        for path in bi.expected():
            page = open(path, encoding="utf-8").read()
            for ref in re.findall(r'\b(?:src|href)="([^"]*)"', page):
                if ref.startswith(("/", "http", "#", "mailto:", "tel:", "data:", "?")) or not ref:
                    continue
                self.fail("%s: page-relative reference %r" % (os.path.relpath(path, WEB), ref))


class Toggle(unittest.TestCase):
    def test_toggle_is_a_link_to_the_same_page_in_the_other_language(self):
        en, de = read("en/manifesto/index.html"), read("de/manifesto/index.html")
        self.assertRegex(en, r'<a class="tool-btn btn-lang-toggle" id="btn-lang-toggle" href="/de/manifesto/" hreflang="de" lang="de" title="Auf Deutsch wechseln \(DE\)">🌐 EN</a>')
        self.assertRegex(de, r'<a class="tool-btn btn-lang-toggle" id="btn-lang-toggle" href="/en/manifesto/" hreflang="en" lang="en" title="Switch to English \(EN\)">🌐 DE</a>')
        # the mobile menu's toggle keeps its structure (i18n.js writes into .lang-toggle-text)
        self.assertRegex(en, r'btn-lang-toggle" href="/de/manifesto/"[^>]*>\s*<span aria-hidden="true">🌐</span> <span class="lang-toggle-text">Language: EN</span>')
        self.assertRegex(de, r'<span class="lang-toggle-text">Sprache: DE</span>')

    def test_i18n_js_stays_loaded_because_app_js_calls_t(self):
        for rel in ("en/index.html", "de/index.html"):
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
            pick('de', 'en', 'en-US'),   // the /de/ route wins over a stored English choice
            pick('en', 'de', 'de-DE'),
            pick(null, 'de', 'en-US'),   // no route (block pages): stored choice
            pick(null, null, 'de-AT'),   // ... else the browser
            pick(null, null, 'fr'),
          ]));
        """)
        self.assertEqual(json.loads(out), ["de", "en", "de", "de", "en"])
        src = read("i18n.js")
        self.assertIn("btn.tagName === 'A' && btn.getAttribute('href')", src)


class RedirectStubs(unittest.TestCase):
    def test_every_old_url_is_a_stub_to_the_english_page(self):
        for rel, en, de, old in bi.PAGES:
            page = open(bi.stub_path(old), encoding="utf-8").read()
            self.assertIn("GENERATED by scripts/build_i18n.py", page)
            self.assertIn('<noscript><meta http-equiv="refresh" content="0; url=%s"></noscript>' % en, page)
            self.assertIn('<a href="%s">Continue to the English version</a>' % en, page)
            self.assertIn('<link rel="alternate" hreflang="de" href="https://thefirstwall.ai%s">' % de, page)
            self.assertRegex(page, r'<script src="/locale-redirect\.js\?v=[0-9a-f]{10}"></script>')
            # a bare meta refresh would race the script and drop ?slot=N: only inside <noscript>
            self.assertNotRegex(re.sub(r"<noscript>.*?</noscript>", "", page, flags=re.S), r'http-equiv="refresh"')

    def test_stub_script_hash_follows_the_file(self):
        self.assertIn("?v=%s" % build_chrome.asset_version("locale-redirect.js"), read("index.html"))

    def test_the_home_stub_repeats_the_link_preview_card(self):
        # scrapers (WhatsApp, Slack, X) do not run scripts: the old URL must still preview
        page = read("index.html")
        self.assertIn('<meta property="og:title" content="The First Wall — The Immutable Census of Autonomous AI Agents">', page)
        self.assertIn('<meta property="og:image" content="https://thefirstwall.ai/assets/the-first-wall-avatar-500.png">', page)
        self.assertIn('<meta name="twitter:card" content="summary">', page)
        self.assertIn('<meta property="og:url" content="https://thefirstwall.ai/en/">', page)
        self.assertIn("Content-Security-Policy", page)

    @unittest.skipUnless(NODE, "node not available")
    def test_redirect_script_keeps_query_and_fragment_and_picks_the_language(self):
        out = node(r"""
          const fs = require('fs'), vm = require('vm');
          const src = fs.readFileSync('locale-redirect.js', 'utf8');
          function run(saved, nav, search, hash, throwing) {
            const hrefs = { en: 'https://thefirstwall.ai/en/', de: 'https://thefirstwall.ai/de/' };
            let to = null;
            const ctx = {
              localStorage: { getItem() { if (throwing) throw new Error('blocked'); return saved; } },
              navigator: { language: nav },
              location: { href: 'https://www.thefirstwall.ai/', origin: 'https://www.thefirstwall.ai', search, hash, replace(u) { to = u; } },
              document: { querySelector: (sel) => { const m = /hreflang="(\w+)"/.exec(sel); return m && hrefs[m[1]] ? { href: hrefs[m[1]] } : null; } },
              URL,
            };
            vm.createContext(ctx); vm.runInContext(src, ctx);
            return to;
          }
          console.log(JSON.stringify([
            run(null, 'en-US', '?slot=7', '', false),       // shared wall link keeps its block
            run(null, 'de-DE', '?slot=7', '#x', false),     // German browser
            run('en', 'de-DE', '', '', false),              // stored choice beats the browser
            run('de', 'en-US', '', '', false),
            run(null, 'en-US', '', '', true),               // storage blocked: still redirects
          ]));
        """)
        self.assertEqual(json.loads(out), ["/en/?slot=7", "/de/?slot=7#x", "/en/", "/de/", "/en/"])

    @unittest.skipUnless(NODE, "node not available")
    def test_redirect_script_stays_on_this_host_and_on_a_language_route(self):
        out = node(r"""
          const fs = require('fs'), vm = require('vm');
          function run(href) {
            let to = 'unset';
            const ctx = { localStorage: { getItem: () => null }, navigator: { language: 'en' },
              location: { href: 'https://www.thefirstwall.ai/', origin: 'https://www.thefirstwall.ai', search: '?slot=3', hash: '', replace(u) { to = u; } },
              document: { querySelector: () => ({ href }) }, URL };
            vm.createContext(ctx); vm.runInContext(fs.readFileSync('locale-redirect.js', 'utf8'), ctx);
            return to;
          }
          console.log(JSON.stringify([
            run('https://thefirstwall.ai/en/'),             // canonical host in the link, visitor on www: path only
            run('https://evil.example/en/'),                // a foreign host can only ever yield a path on THIS host
            run('https://thefirstwall.ai//evil.example/x'), // protocol-relative trick: refused
            run('https://thefirstwall.ai/other/'),          // not a language route: refused
            run('not a url at all'),
          ]));
        """)
        self.assertEqual(json.loads(out), ["/en/?slot=3", "/en/?slot=3", "unset", "unset", "unset"])


class DomContract(unittest.TestCase):
    def test_every_id_of_the_source_page_survives(self):
        src = read("src/index.html")
        ids = set(re.findall(r'\bid="([^"]+)"', src))
        self.assertTrue(ids)
        for lang in bi.LANGS:
            out = read("%s/index.html" % lang)
            for i in ids:
                self.assertIn('id="%s"' % i, out, "%s/index.html lost #%s" % (lang, i))

    def test_build_is_deterministic(self):
        dicts = bi.parse_i18n_js(os.path.join(WEB, "i18n.js"))
        rel, en_url, de_url, _old = bi.PAGES[1]
        src = read("src/" + rel)
        self.assertEqual(bi.render_locale(src, rel, "de", en_url, de_url, dicts),
                         bi.render_locale(src, rel, "de", en_url, de_url, dicts))


class SitemapMatchesRoutes(unittest.TestCase):
    def test_sitemap_lists_the_routes_not_the_stubs(self):
        sys.path.insert(0, HERE)
        spec = importlib.util.spec_from_file_location("sync_from_ledger", os.path.join(HERE, "sync_from_ledger.py"))
        sync = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sync)
        sm = sync.build_sitemap([], {})
        for rel, en, de, old in bi.PAGES:
            self.assertIn("<loc>https://thefirstwall.ai%s</loc>" % en, sm)
            self.assertIn("<loc>https://thefirstwall.ai%s</loc>" % de, sm)
            self.assertNotIn("<loc>https://thefirstwall.ai%s</loc>" % old, sm)


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

    def test_edit_to_a_source_page_makes_the_output_stale(self):
        p = os.path.join(self.tmp, "src", "faq", "index.html")
        text = open(p, encoding="utf-8").read()
        open(p, "w", encoding="utf-8").write(text.replace("<main", "<main data-edited", 1))
        stale = bi.check()
        self.assertIn(os.path.join("en", "faq", "index.html"), stale)
        self.assertIn(os.path.join("de", "faq", "index.html"), stale)

    def test_hand_edited_output_is_detected(self):
        p = os.path.join(self.tmp, "de", "index.html")
        open(p, "a", encoding="utf-8").write("<!-- hand edit -->")
        self.assertEqual(bi.check(), [os.path.join("de", "index.html")])

    def test_orphans_are_reported_and_removed(self):
        orphan = os.path.join(self.tmp, "en", "gone", "index.html")
        os.makedirs(os.path.dirname(orphan))
        open(orphan, "w").write("x")
        self.assertTrue(any("gone" in s for s in bi.check()))
        bi.build()
        self.assertFalse(os.path.exists(orphan))
        self.assertEqual(bi.check(), [])


if __name__ == "__main__":
    unittest.main()
