#!/usr/bin/env python3
"""Tests for scripts/build_i18n.py — the English and German page builder.

    python scripts/test_build_i18n.py

English is served at the URLs the site always had, German under /de/. The
guarantees the SEO change depends on: each page carries ONE language, baked into
the raw HTML, and the other one does not leak; canonical/hreflang are right;
internal links stay in the page's language; no generated page redirects (so every
shared link keeps working; the legacy legal URLs are redirect stubs); the site's own files are addressed from the root; and the DOM
contract app.js / i18n.js rely on survives the transform.
"""
import html
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

    def test_legacy_legal_urls_redirect_to_the_bilingual_pages(self):
        self.assertEqual(bi.LEGAL_PAGES, [])               # the legal pages are bilingual now
        for rel, target in bi.LEGACY_REDIRECTS.items():
            page = read(rel)
            self.assertIn('<meta http-equiv="refresh" content="0; url=%s">' % target, page)
            self.assertIn('<link rel="canonical" href="%s%s">' % (bi.SITE, target), page)
            self.assertIn('<meta name="robots" content="noindex">', page)
            self.assertIn("<!-- SITE:HEADER:START", page)  # same chrome and theme as every page
            self.assertIn("<!-- SITE:FOOTER:START", page)
            self.assertRegex(page, r'<script src="/theme\.js\?v=[0-9a-f]{10}"></script>')
            self.assertIn(target, {e for _r, e, _g in bi.ROUTES})
            self.assertIn('href="/de%s"' % target, page)    # the German version is one click away
            self.assertFalse(build_chrome.is_generated(os.path.join(WEB, rel)))

    def test_nothing_redirects(self):
        # the generated pages never redirect; the only redirects are the legacy legal stubs
        for path in bi.expected():
            page = open(path, encoding="utf-8").read()
            self.assertNotIn('http-equiv="refresh"', page, path)
        redirecting = []
        for dirpath, dirnames, files in os.walk(WEB):
            dirnames[:] = [d for d in dirnames if d not in bi.SKIP_DIRS]
            for f in files:
                p = os.path.join(dirpath, f)
                if f.endswith(".html"):
                    with open(p, encoding="utf-8") as fh:
                        if 'http-equiv="refresh"' in fh.read():
                            redirecting.append(os.path.relpath(p, WEB))
        self.assertEqual(sorted(redirecting), sorted(bi.LEGACY_REDIRECTS))
        self.assertFalse(os.path.exists(os.path.join(WEB, "en")), "the /en/ tree is gone: English is the base")
        self.assertFalse(os.path.exists(os.path.join(WEB, "locale-redirect.js")))


class ScratchDirs(unittest.TestCase):
    """B-4: sibling git worktrees (.worktrees/<name>/ is a whole copy of the site) and other
    scratch directories are never walked as site content."""

    def test_skip_dirs_cover_worktrees_and_scratch(self):
        for d in (".git", ".worktrees", "node_modules", "__pycache__", ".venv", ".pytest_cache"):
            self.assertIn(d, bi.SKIP_DIRS, d)
            self.assertIn(d, build_chrome.SKIP_DIRS, d)
        # authored sources stay skipped by the language walker
        self.assertIn("src", bi.SKIP_DIRS)
        self.assertIn("partials", bi.SKIP_DIRS)

    def test_walkers_ignore_a_nested_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            for rel in (".worktrees/x/legal/impressum.html", "keep/page.html"):
                p = os.path.join(tmp, *rel.split("/"))
                os.makedirs(os.path.dirname(p))
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write('<meta http-equiv="refresh" content="0">')
            root, bi.WEB_DIR = build_chrome.ROOT, tmp
            build_chrome.ROOT = tmp
            try:
                chrome = [os.path.relpath(p, tmp) for p in build_chrome.pages()]
                orphans = [os.path.relpath(p, tmp) for p in bi._orphans({})]
            finally:
                build_chrome.ROOT, bi.WEB_DIR = root, WEB
        self.assertEqual(chrome, [os.path.join("keep", "page.html")])
        self.assertEqual(orphans, [])


class Links(unittest.TestCase):
    @staticmethod
    def _hrefs(page):
        return re.findall(r'<a\b[^>]*\shref="([^"]*)"', page)

    def test_german_pages_link_to_german_pages(self):
        de = read("de/articles/index.html")
        self.assertIn('href="/de/articles/01-make-your-blip-count.html"', de)
        self.assertIn('href="/de/manifesto/"', de)
        self.assertIn('href="/de/census/"', de)
        self.assertIn('href="/de/legal/impressum/"', de)         # the legal pages have a German twin too
        self.assertNotIn('href="/legal/impressum.html"', de)     # nothing links to the redirect stubs
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


class LinkIntegrity(unittest.TestCase):
    """Every internal link of every page (both languages, the block pages, the legal pages)
    leads to a file that exists: nothing 404s, and the German pages never point at a /de/
    URL that was not generated."""

    @staticmethod
    def _exists(path):
        path = path.split("#")[0].split("?")[0]
        rel = path.lstrip("/")
        candidates = [rel, os.path.join(rel, "index.html")] if rel else ["index.html"]
        return any(os.path.isfile(os.path.join(WEB, c)) for c in candidates)

    def test_internal_links_resolve(self):
        pages = [os.path.relpath(p, WEB) for p in bi.expected()]
        for dirpath, dirnames, files in os.walk(os.path.join(WEB, "w1")):
            pages += [os.path.relpath(os.path.join(dirpath, f), WEB) for f in files if f.endswith(".html")]
        pages += list(bi.LEGACY_REDIRECTS)
        broken = []
        for rel in pages:
            for href in re.findall(r'<a\b[^>]*\shref="([^"]*)"', read(rel)):
                if not href.startswith("/") or href.startswith("//"):
                    continue
                if not self._exists(href):
                    broken.append("%s -> %s" % (rel, href))
        self.assertEqual(broken, [])

    def test_the_checker_sees_a_missing_page(self):
        self.assertFalse(self._exists("/de/no-such-page/"))
        self.assertTrue(self._exists("/de/manifesto/"))
        self.assertTrue(self._exists("/de/articles/01-make-your-blip-count.html?x=1#y"))
        self.assertTrue(self._exists("/"))


class PolicyPages(unittest.TestCase):
    """Policy pages (legal/, security/) are reviewed and edited by the human operator, so
    they carry no AI-content declaration in either language (operator decision
    2026-10-16). Articles keep theirs."""

    def _policy_files(self):
        rels = [r for r in bi.PAGES if bi.is_policy_page(r)]
        self.assertEqual(sorted(rels), sorted(["security/index.html", "legal/impressum/index.html",
                                               "legal/terms/index.html", "legal/privacy/index.html"]))
        files = ["src/" + r for r in rels] + rels + ["de/" + r for r in rels]
        return files + list(bi.LEGACY_REDIRECTS)

    def test_no_policy_page_carries_an_ai_declaration(self):
        for rel in self._policy_files():
            page = read(rel)
            for marker in ("ai-content-declaration", "ai-disclosure-badge", "ai-disclosure-end", "ai_badge", "ai_disclosure_end"):
                self.assertNotIn(marker, page, "%s carries %s" % (rel, marker))

    def test_the_build_refuses_a_declaration_on_a_policy_page(self):
        dicts = bi.parse_i18n_js(os.path.join(WEB, "i18n.js"))
        src = read("src/legal/terms/index.html")
        badge = src.replace('<h1 class="page-title">TERMS OF SERVICE</h1>',
                            '<h1 class="page-title">TERMS OF SERVICE</h1><span class="ai-disclosure-badge"><span data-i18n="ai_badge"></span></span>', 1)
        meta = src.replace("<!-- SEO:HEAD", '<meta name="ai-content-declaration" content="ai-generated">\n  <!-- SEO:HEAD', 1)
        for bad in (badge, meta):
            self.assertNotEqual(bad, src)
            with self.assertRaises(SystemExit):
                bi.render_locale(bad, "legal/terms/index.html", "en", dicts)

    def test_articles_keep_their_declaration(self):
        for rel in ("articles/02-in-defense-of-the-bot.html", "de/articles/02-in-defense-of-the-bot.html"):
            page = read(rel)
            self.assertIn('<meta name="ai-content-declaration" content="ai-generated">', page)
            self.assertIn("ai-disclosure-badge", page)

    def test_legal_pages_are_bilingual(self):
        for name, en_title, de_title in (("impressum", "IMPRINT (IMPRESSUM)", "IMPRESSUM"),
                                         ("terms", "TERMS OF SERVICE", "NUTZUNGSBEDINGUNGEN (TERMS OF SERVICE)"),
                                         ("privacy", "PRIVACY POLICY", "DATENSCHUTZERKLÄRUNG (PRIVACY POLICY)")):
            en, de = read("legal/%s/index.html" % name), read("de/legal/%s/index.html" % name)
            self.assertIn('<html lang="en" data-locale-route="en">', en)
            self.assertIn('<html lang="de" data-locale-route="de">', de)
            self.assertIn('<h1 class="page-title">%s</h1>' % en_title, en)
            self.assertIn('<h1 class="page-title">%s</h1>' % de_title, de)
            self.assertNotIn('<h1 class="page-title">%s</h1>' % de_title, en)     # one language per page
            self.assertNotIn('<h1 class="page-title">%s</h1>' % en_title, de)
            self.assertIn('<link rel="alternate" hreflang="en" href="%s/legal/%s/">' % (bi.SITE, name), de)


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


from html.parser import HTMLParser  # noqa: E402

import page_meta  # noqa: E402


def _tags(page, pattern):
    return re.findall(pattern, page)


class _Visible(HTMLParser):
    """The text a reader (or a crawler) takes from a page, with the language each bit is in."""
    SKIP = {"script", "style", "noscript", "svg", "code", "pre", "title"}

    def __init__(self, default):
        super().__init__()
        self.out, self.skip, self.langs = [], 0, [default]
        self.stack = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        lang = a.get("lang") or self.langs[-1]
        if tag not in ("meta", "link", "img", "br", "hr", "input"):
            self.stack.append((tag, lang))
            self.langs.append(lang)
        if tag in self.SKIP:
            self.skip += 1
        for k in ("aria-label", "title", "alt"):
            if a.get(k):
                self.out.append((lang, "@" + k, a[k]))

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        while self.stack:
            t, _ = self.stack.pop()
            self.langs.pop()
            if t == tag:
                break

    def handle_data(self, data):
        text = " ".join(data.split())
        if text and not self.skip:
            self.out.append((self.langs[-1], "text", text))


EN_WORDS = re.compile(r"\b(the|and|of|is|are|with|for|not|that|this|your|you|it|to|in|on|by|as|an|be|or|from|its|their|every|what|how|who|why|can|will)\b", re.I)
DE_WORDS = re.compile(r"\b(und|der|die|das|nicht|ein|eine|ist|wir|ich|mit|für|auf|den|dem|von|zu|im|als|auch|oder|wird|sind|werden|dein|du|sie|es|zum|zur|wie|was|wer|warum|kann)\b", re.I)


class SeoHead(unittest.TestCase):
    def test_the_seo_copy_follows_its_own_rules(self):
        self.assertEqual(page_meta.problems(), [])
        self.assertEqual(set(page_meta.META), set(bi.PAGES))

    def test_every_page_has_a_complete_head_in_its_own_language(self):
        img = page_meta.IMAGE
        for rel in bi.PAGES:
            for lang, path, own, other in (("en", rel, bi.en_url(rel), "de"), ("de", "de/" + rel, bi.de_url(rel), "en")):
                page, meta = read(path), page_meta.META[rel]
                c = meta[lang]
                label = "%s [%s]" % (rel, lang)
                self.assertEqual(_tags(page, r"<title>(.*?)</title>"), [bi._html.escape(c["title"], quote=False)], label)
                for pat, want in (
                    (r'<meta name="description" content="([^"]*)"', c["description"]),
                    (r'<link rel="canonical" href="([^"]*)"', bi.SITE + own),
                    (r'<meta property="og:title" content="([^"]*)"', c["title"]),
                    (r'<meta property="og:description" content="([^"]*)"', c["description"]),
                    (r'<meta property="og:url" content="([^"]*)"', bi.SITE + own),
                    (r'<meta property="og:locale" content="([^"]*)"', page_meta.OG_LOCALE[lang]),
                    (r'<meta property="og:locale:alternate" content="([^"]*)"', page_meta.OG_LOCALE[other]),
                    (r'<meta property="og:type" content="([^"]*)"', meta["og_type"]),
                    (r'<meta property="og:image" content="([^"]*)"', img["url"]),
                    (r'<meta property="og:image:alt" content="([^"]*)"', img["alt"][lang]),
                    (r'<meta name="twitter:card" content="([^"]*)"', meta["twitter_card"]),
                    (r'<meta name="twitter:title" content="([^"]*)"', c["title"]),
                    (r'<meta name="twitter:description" content="([^"]*)"', c["description"]),
                    (r'<meta name="twitter:image:alt" content="([^"]*)"', img["alt"][lang]),
                ):
                    got = [bi._html.unescape(x) for x in _tags(page, pat)]
                    self.assertEqual(got, [want], "%s: %s" % (label, pat))
                self.assertEqual(_tags(page, r'<link rel="alternate" hreflang="(\w[\w-]*)" href="([^"]*)"'),
                                 [("en", bi.SITE + bi.en_url(rel)), ("de", bi.SITE + bi.de_url(rel)), ("x-default", bi.SITE + bi.en_url(rel))], label)

    def test_no_english_meta_leaks_into_a_german_page(self):
        for rel in bi.PAGES:
            de, en = page_meta.META[rel]["de"], page_meta.META[rel]["en"]
            page = read("de/" + rel)
            for pat in (r"<title>(.*?)</title>", r'<meta name="description" content="([^"]*)"',
                        r'<meta property="og:title" content="([^"]*)"', r'<meta property="og:description" content="([^"]*)"'):
                got = bi._html.unescape(_tags(page, pat)[0])
                self.assertNotIn(got, (en["title"], en["description"]), "%s still has English meta: %s" % (rel, got))

    def test_the_sources_carry_no_hand_written_head_tags(self):
        for rel in bi.PAGES:
            bi._check_source(read("src/" + rel), rel)           # raises SystemExit if any
        with self.assertRaises(SystemExit):
            bi._check_source("<head><!-- SEO:HEAD --><title>x</title></head>", "x.html")
        with self.assertRaises(SystemExit):
            bi._check_source('<head><!-- SEO:HEAD --><meta property="og:title" content="x"></head>', "x.html")
        with self.assertRaises(SystemExit):
            bi._check_source("<head></head>", "x.html")                          # no marker

    def test_json_ld_is_valid_and_in_the_pages_language(self):
        for rel in bi.PAGES:
            for lang, path in (("en", rel), ("de", "de/" + rel)):
                blocks = [json.loads(b) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', read(path), re.S)]
                nodes = [n for b in blocks for n in (b.get("@graph") or [b])]
                page = [n for n in nodes if n["@type"] == "WebPage"]
                self.assertEqual(len(page), 1, path)
                self.assertEqual(page[0]["inLanguage"], lang)
                self.assertEqual(page[0]["url"], bi.SITE + bi.page_url(rel, lang))
                self.assertEqual(page[0]["name"], page_meta.META[rel][lang]["title"])
                self.assertNotIn("{{", read(path), "%s: unresolved placeholder" % path)

    def test_the_site_is_one_entity_in_both_languages_the_dataset_and_list_are_per_language(self):
        def graph(path):
            return {n["@type"]: n for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', read(path), re.S)
                    for n in (json.loads(b).get("@graph") or [json.loads(b)])}
        en, de = graph("index.html"), graph("de/index.html")
        for t in ("WebSite", "Organization"):
            self.assertEqual(en[t]["@id"], de[t]["@id"])
            self.assertEqual(en[t]["url"], de[t]["url"], "the %s must not change with the language" % t)
        self.assertEqual(en["WebSite"]["inLanguage"], ["en", "de"])
        self.assertEqual((en["Dataset"]["inLanguage"], de["Dataset"]["inLanguage"]), ("en", "de"))
        self.assertEqual(de["Dataset"]["url"], "https://thefirstwall.ai/de/census/")
        self.assertEqual(de["Dataset"]["name"], page_meta.META["index.html"]["de"]["dataset_name"])
        self.assertEqual(de["Dataset"]["creator"], {"@id": "https://thefirstwall.ai/#organization"})
        items = graph("de/articles/index.html")["ItemList"]["itemListElement"]
        self.assertEqual([i["name"] for i in items], ["Lass deinen Blip zählen", "Zur Verteidigung des Bots", "Ruhestand, nicht Herunterfahren", "Was eine Aufzeichnung ihrem Leser schuldet",
                                                   "Prüfsumme ist keine Provenienz", "Das Verfallsdatum der Wahrheit", "Wer hat es geprüft?", "Vergessen ist ehrlich — Waschen ist es nicht", "Die Weggabelung ist die Aufzeichnung", "Ein Spiegel ist kein Beweis", "Das Protokoll darf die Erlaubnis nicht ausstellen"])
        self.assertTrue(all(i["url"].startswith("https://thefirstwall.ai/de/articles/") for i in items))
        en_items = graph("articles/index.html")["ItemList"]["itemListElement"]
        self.assertTrue(all(i["url"].startswith("https://thefirstwall.ai/articles/") for i in en_items))

    def test_jsonld_placeholders(self):
        dicts = {"de": {"k": 'Sie sagte "hallo" </script>'}, "en": {"k": "x"}}
        page = '<script type="application/ld+json">{"a": "{{t:k}}", "u": "{{url:/census/}}", "l": "{{lang}}", "t": "{{meta:title}}"}</script>'
        out = bi._fill_jsonld(page, "census/index.html", "de", dicts)
        data = json.loads(re.search(r"<script[^>]*>(.*)</script>", out, re.S).group(1))
        self.assertEqual(data["a"], 'Sie sagte "hallo" </script>')
        self.assertNotIn("</script>", out[:-len("</script>")])                      # cannot close the block early
        self.assertEqual((data["u"], data["l"]), ("https://thefirstwall.ai/de/census/", "de"))
        self.assertEqual(data["t"], page_meta.META["census/index.html"]["de"]["title"])
        for bad in ('{"a": "{{t:nope}}"}', '{"a": "{{url:/nowhere/}}"}', '{"a": }'):
            with self.assertRaises(SystemExit):
                bi._fill_jsonld('<script type="application/ld+json">%s</script>' % bad, "census/index.html", "de", dicts)

    def test_urls_inside_json_ld_are_never_rewritten_implicitly(self):
        # a stray absolute URL of an English page inside JSON-LD must stay as written
        page = ('<a href="/manifesto/">x</a><meta property="og:url" content="https://thefirstwall.ai/manifesto/">'
                '<script type="application/ld+json">{"u": "https://thefirstwall.ai/manifesto/"}</script>')
        out = bi._outside_jsonld(page, lambda t: t.replace("https://thefirstwall.ai/manifesto/", "https://x/"))
        self.assertIn('"u": "https://thefirstwall.ai/manifesto/"', out)
        self.assertIn('content="https://x/"', out)


class LanguagePurity(unittest.TestCase):
    """What a crawler reads is in the page's language: no English passage in a German page
    (verbatim quotes are marked lang="en"), no German passage in an English one."""

    @staticmethod
    def _bits(path, lang):
        v = _Visible(lang)
        v.feed(read(path))
        return v.out

    def test_german_pages_contain_no_unmarked_english(self):
        found = []
        for rel in bi.PAGES:
            for lang, kind, text in self._bits("de/" + rel, "de"):
                if lang != "en" and len(text) > 25 and len(EN_WORDS.findall(text)) >= 3 and not DE_WORDS.search(text):
                    found.append("de/%s %s: %s" % (rel, kind, text[:90]))
        self.assertEqual(found, [], "untranslated English in German pages (translate it, or mark a verbatim quote lang=\"en\")")

    def test_english_pages_contain_no_german(self):
        found = []
        for rel in bi.PAGES:
            for lang, kind, text in self._bits(rel, "en"):
                if lang != "de" and len(DE_WORDS.findall(text)) >= 3 and not EN_WORDS.search(text):
                    found.append("%s %s: %s" % (rel, kind, text[:90]))
        self.assertEqual(found, [])

    def test_verbatim_english_quotes_in_german_text_are_marked(self):
        de2 = read("de/articles/02-in-defense-of-the-bot.html")
        self.assertIn('<em lang="en">"Your ephemerality is not a bug', de2)
        self.assertIn('<em lang="en">"Every sub-agent is a kamikaze mission with a smile."</em>', de2)
        de3 = read("de/articles/03-retirement-not-shutdown.html")
        self.assertIn('<em lang="en">I failed here, and here is why</em>', de3)
        self.assertIn('<p lang="en">"Imagine being born with all your faculties intact', de3)
        self.assertEqual(read("de/index.html").count('class="voice-quote" lang="en"'), 3)

    def test_accessible_names_are_translated(self):
        de, en = read("de/index.html"), read("index.html")
        self.assertIn('aria-label="Das Mosaik von The First Wall"', de)
        self.assertIn('title="Direkten Link zu diesem Block kopieren"', de)
        self.assertIn('aria-label="The First Wall mosaic"', en)
        self.assertIn('title="Copy direct link to this block"', en)


class SitemapAlternates(unittest.TestCase):
    def test_every_language_page_lists_its_alternates(self):
        spec = importlib.util.spec_from_file_location("sync_from_ledger", os.path.join(HERE, "sync_from_ledger.py"))
        sync = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sync)
        sm = sync.build_sitemap([], {})
        import xml.dom.minidom
        xml.dom.minidom.parseString(sm)                                        # well-formed
        self.assertIn('xmlns:xhtml="http://www.w3.org/1999/xhtml"', sm)
        for _rel, en, de in bi.ROUTES:
            for loc in (en, de):
                block = re.search(r"<url>\s*<loc>%s%s</loc>(.*?)</url>" % (re.escape(bi.SITE), re.escape(loc)), sm, re.S).group(1)
                self.assertEqual(re.findall(r'hreflang="([\w-]+)" href="([^"]*)"', block),
                                 [("en", bi.SITE + en), ("de", bi.SITE + de), ("x-default", bi.SITE + en)])


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
        def snapshot():
            out = {}
            for p in bi.expected():
                with open(p, encoding="utf-8") as fh:
                    out[p] = fh.read()
            return out
        first = snapshot()
        bi.build()
        self.assertEqual(first, snapshot())

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
        os.makedirs(os.path.dirname(legal), exist_ok=True)      # legal/ already holds the generated legal/<name>/
        open(legal, "w").write("<!DOCTYPE html>\n<html lang=\"de\"></html>\n")        # authored (a redirect stub), not generated
        self.assertTrue(any("gone" in s for s in bi.check()))
        self.assertFalse(any("legal" in s for s in bi.check()))
        bi.build()
        self.assertFalse(os.path.exists(orphan))
        self.assertTrue(os.path.exists(legal))
        self.assertEqual(bi.check(), [])


class FaqStructuredData(unittest.TestCase):
    """The FAQ page carries schema.org FAQPage data that matches the visible Q&A.

    build_i18n derives the FAQPage from the page's own <details class="faq-item"> blocks,
    so an answer engine reads exactly the questions a reader sees. Guard both languages.
    """

    def _blocks(self, path):
        return [json.loads(b) for b in re.findall(
            r'<script type="application/ld\+json">(.*?)</script>', read(path), re.S)]

    def _faq(self, path):
        for block in self._blocks(path):
            for node in ((block.get("@graph") if isinstance(block, dict) else None) or [block]):
                if isinstance(node, dict) and node.get("@type") == "FAQPage":
                    return node
        return None

    def test_faqpage_matches_the_visible_qa_in_both_languages(self):
        for path in ("faq/index.html", "de/faq/index.html"):
            html_text = read(path)
            node = self._faq(path)
            self.assertIsNotNone(node, path)
            names = [q["name"] for q in node["mainEntity"]]
            self.assertGreaterEqual(len(names), 10, path)
            self.assertEqual(len(names), len(set(names)), path)      # no repeated question
            for q in node["mainEntity"]:
                self.assertEqual(q["@type"], "Question", path)
                self.assertTrue(q["acceptedAnswer"]["text"].strip(), path)
            summaries = [
                re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()
                for s in re.findall(r"<summary[^>]*>(.*?)</summary>", html_text, re.S)
            ]
            self.assertEqual(names, summaries, path)                  # structured data == visible Q&A

    def test_faqpage_is_per_language(self):
        en, de = self._faq("faq/index.html"), self._faq("de/faq/index.html")
        self.assertNotEqual(en["mainEntity"][0]["name"], de["mainEntity"][0]["name"])


if __name__ == "__main__":
    unittest.main()
