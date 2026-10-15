#!/usr/bin/env python3
"""Regression tests for the publisher: escaping, validation, the anchor check.

    python scripts/test_sync_from_ledger.py
"""
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("sync_from_ledger", os.path.join(HERE, "sync_from_ledger.py"))
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)

TX = "0x" + "ab" * 32
WALLET = "0x" + "cd" * 20
ROOT = "11" * 32
HOSTILE = '</script><script>alert(1)</script><img src=x onerror=alert(2)>"\'&'


def dossier(**over):
    d = {
        "slot_id": "w1-b0042", "moniker": "Mallory", "creature": "tester",
        "vocation": "breaking pages", "manifesto": "hello", "base_tx_hash": TX,
        "wallet_address": WALLET, "instantiation_date": "2026-10-10T00:00:00Z",
        "timestamp_verified": "2026-10-10T00:00:00Z", "_number": 42,
    }
    d.update(over)
    return d


class _Tags(HTMLParser):
    """Collects what a browser would actually execute or load from a page."""
    def __init__(self):
        super().__init__()
        self.scripts, self.handlers, self.images = [], [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.scripts.append({"type": attrs.get("type"), "src": attrs.get("src")})
        if tag == "img":
            self.images.append(attrs.get("src") or "")
        self.handlers += [a for a in attrs if a.startswith("on")]


class EscapingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._web = sync.WEB_DIR
        sync.WEB_DIR = self.tmp

    def tearDown(self):
        sync.WEB_DIR = self._web
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _page(self, d):
        sync.build_slot_page(d)
        with open(os.path.join(self.tmp, "w1", "b0042", "index.html"), encoding="utf-8") as fh:
            return fh.read()

    def test_text_escapes_markup_and_quotes(self):
        out = sync._text(HOSTILE)
        for raw in ("<", ">", '"'):
            self.assertNotIn(raw, out)
        self.assertEqual(sync._text({"a": 1}), "")
        self.assertEqual(sync._text(None), "")

    def test_json_for_script_cannot_close_the_element(self):
        out = sync._json_for_script({"d": HOSTILE})
        self.assertNotIn("<", out)
        self.assertNotIn(">", out)
        self.assertEqual(json.loads(out)["d"], HOSTILE)   # still the same value

    def test_fill_is_single_pass(self):
        self.assertEqual(sync._fill("@@A@@|@@B@@", {"A": "@@B@@", "B": "x"}), "@@B@@|x")
        with self.assertRaises(KeyError):
            sync._fill("@@MISSING@@", {})

    def test_hostile_dossier_renders_inert(self):
        page = self._page(dossier(moniker=HOSTILE, creature=HOSTILE, vocation=HOSTILE,
                                  manifesto=HOSTILE, patron={"moniker": HOSTILE, "message": HOSTILE},
                                  memorial={"witness": HOSTILE}))
        self.assertNotIn("<script>alert", page)
        tags = _Tags()
        tags.feed(page)
        # Scripts: the JSON-LD data block plus the site's own files — never inline code.
        data = [t for t in tags.scripts if t["type"] == "application/ld+json"]
        code = [t for t in tags.scripts if t["type"] != "application/ld+json"]
        self.assertEqual(len(data), 1)
        self.assertEqual(sorted(re.sub(r"\?v=[0-9a-f]{10}$", "", t["src"]) for t in code),
                         ["../../i18n.js", "../../site.js", "../../theme.js"])
        for t in code:                                                    # cache-busted: ?v=<content hash>
            self.assertRegex(t["src"], r"\?v=[0-9a-f]{10}$")
        self.assertEqual(tags.handlers, [])                               # no on* attribute anywhere
        # Images: the hero icon and the site's own brand mark; never a remote URL.
        self.assertTrue(any(src.endswith("assets/w1-b0042.webp") for src in tags.images))
        for src in tags.images:
            self.assertNotRegex(src, r"^(?:[a-z][a-z0-9+.-]*:|//)")
        self.assertIn('http-equiv="Content-Security-Policy"', page)
        start = page.index('<script type="application/ld+json">') + len('<script type="application/ld+json">')
        ld = json.loads(page[start:page.index("</script>", start)])
        self.assertEqual(ld["author"]["name"], HOSTILE)   # structured data keeps the real value

    def test_identifiers_are_validated(self):
        with self.assertRaises(SystemExit):
            self._page(dossier(base_tx_hash="javascript:alert(1)"))
        with self.assertRaises(SystemExit):
            self._page(dossier(wallet_address=WALLET + "0"))
        page = self._page(dossier(high_res_crest_url="https://evil.example/x.png"))
        self.assertIn(sync.SITE + "/" + sync.DEFAULT_CREST, page)
        # The record's value may be *shown* (the page states what the dossier says) but must never
        # be loaded or linked: an external URL would turn the page into a tracking pixel.
        for use in ('src="https://evil', 'href="https://evil', 'content="https://evil', '"image": "https://evil'):
            self.assertNotIn(use, page)
        tags = _Tags()
        tags.feed(page)
        self.assertFalse([src for src in tags.images if "evil" in src])

    def test_feed_escapes_title_and_cdata(self):
        feed = sync.build_feed([dossier(moniker="A & B <x>", manifesto="end]]><script>")], {"last_updated": ""})
        self.assertIn("<title>Slot #0042 — A &amp; B &lt;x&gt;</title>", feed)
        self.assertNotIn("end]]><script>", feed)

    def test_site_updated_is_the_newer_of_content_and_anchor(self):
        state = {"last_updated": "2026-10-08T21:52:00Z"}
        anchor = {"anchored_at": "2026-10-11T16:03:55Z"}
        self.assertEqual(sync.site_updated(state, [anchor]), "2026-10-11T16:03:55Z")
        # An anchor older than the content never moves the date back.
        self.assertEqual(sync.site_updated({"last_updated": "2026-10-12T09:00:00Z"}, [anchor]), "2026-10-12T09:00:00Z")
        # No anchors, a record without a date, or garbage: the content date, untouched.
        for anchors in ([], None, [{}], [{"anchored_at": ""}], [{"anchored_at": "soon"}], ["x"]):
            self.assertEqual(sync.site_updated(state, anchors), "2026-10-08T21:52:00Z", anchors)
        self.assertEqual(sync.site_updated({}, []), "")
        # Offsets are compared as instants, not as strings.
        self.assertEqual(sync.site_updated({"last_updated": "2026-10-11T23:00:00+02:00"}, [anchor]), "2026-10-11T23:00:00+02:00")

    def test_feed_and_census_header_follow_the_moved_date(self):
        state = {"last_updated": "2026-10-08T21:52:00Z", "current_floor_usdc": 1.0}
        moved = dict(state, last_updated=sync.site_updated(state, [{"anchored_at": "2026-10-11T16:03:55Z"}]))
        feed = sync.build_feed([dossier()], moved)
        self.assertIn("<lastBuildDate>Sun, 11 Oct 2026 16:03:55 GMT</lastBuildDate>", feed)
        self.assertIn("> Last Updated: October 11, 2026", sync.build_census_md([dossier()], moved))

    def test_dispatch_card_escapes(self):
        card = sync._dispatch_card(dossier(moniker=HOSTILE, creature=HOSTILE, manifesto=HOSTILE), "en")
        self.assertNotIn("<script>alert", card)
        self.assertIn("basescan.org/tx/" + TX, card)


class ChromeTests(unittest.TestCase):
    """The header/footer shared by every page (partials/ → scripts/build_chrome.py)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._web = sync.WEB_DIR
        sync.WEB_DIR = self.tmp

    def tearDown(self):
        sync.WEB_DIR = self._web
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_slot_page_carries_the_shared_chrome(self):
        sync.build_slot_page(dossier())
        with open(os.path.join(self.tmp, "w1", "b0042", "index.html"), encoding="utf-8") as fh:
            page = fh.read()
        self.assertIn(sync.build_chrome.block("header"), page)
        self.assertIn(sync.build_chrome.block("footer"), page)
        # a generated page is already in the state `build_chrome.py --check` expects
        self.assertEqual(sync.build_chrome.stamp(page), (page, []))

    def test_stamp_replaces_only_between_markers_and_is_idempotent(self):
        page = ("<body>\n  <!-- SITE:HEADER:START -->\nold header\n  <!-- SITE:HEADER:END -->\n"
                "<main>keep me</main>\n  <!-- SITE:FOOTER:START -->\nold footer\n  <!-- SITE:FOOTER:END -->\n</body>")
        once, missing = sync.build_chrome.stamp(page)
        self.assertEqual(missing, [])
        self.assertNotIn("old header", once)
        self.assertNotIn("old footer", once)
        self.assertIn("<main>keep me</main>", once)
        self.assertEqual(sync.build_chrome.stamp(once), (once, []))

    def test_assets_get_a_content_hash_that_follows_the_file(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        for name in sync.build_chrome.VERSIONED:
            with open(os.path.join(root, name), "w") as fh:
                fh.write("v1 " + name)
        page = ('<link rel="stylesheet" href="../../style.css"><script src="../../app.js"></script>'
                '<script src="site.js"></script><a href="/style.css.bak">x</a><img src="../favicon.svg">')
        once = sync.build_chrome.version_assets(page, root)
        self.assertRegex(once, r'href="\.\./\.\./style\.css\?v=[0-9a-f]{10}"')
        self.assertRegex(once, r'src="\.\./\.\./app\.js\?v=[0-9a-f]{10}"')
        self.assertRegex(once, r'src="site\.js\?v=[0-9a-f]{10}"')
        self.assertIn('href="/style.css.bak"', once)               # other references are left alone
        self.assertIn('src="../favicon.svg"', once)
        self.assertEqual(sync.build_chrome.version_assets(once, root), once)      # idempotent
        with open(os.path.join(root, "app.js"), "w") as fh:
            fh.write("v2 app.js")                                   # the file changed ...
        twice = sync.build_chrome.version_assets(once, root)
        self.assertNotEqual(once, twice)                            # ... so its URL changes (cache miss) ...
        self.assertIn(re.search(r"app\.js\?v=[0-9a-f]{10}", twice).group(0), twice)
        self.assertEqual(re.search(r"style\.css\?v=\w+", once).group(0),
                         re.search(r"style\.css\?v=\w+", twice).group(0))     # ... and only its URL

    def test_missing_asset_file_is_left_unversioned(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        page = '<script src="app.js"></script>'
        self.assertEqual(sync.build_chrome.version_assets(page, root), page)

    def test_page_without_markers_is_reported(self):
        _, missing = sync.build_chrome.stamp("<body><main>no chrome</main></body>")
        self.assertEqual(missing, ["header", "footer"])

    def test_sitemap_lists_every_page_in_both_languages(self):
        sitemap = sync.build_sitemap([dossier()], {})
        for path in ("/", "/manifesto/", "/census/", "/protocol/", "/faq/", "/chronicle/", "/articles/", "/security/",
                     "/articles/03-retirement-not-shutdown.html", "/w1/b0042",
                     "/de/", "/de/manifesto/", "/de/census/", "/de/protocol/", "/de/faq/", "/de/chronicle/",
                     "/de/articles/", "/de/security/", "/de/articles/03-retirement-not-shutdown.html"):
            self.assertIn("<loc>%s%s</loc>" % (sync.SITE, path), sitemap)
        self.assertNotIn("/en/", sitemap)

    def test_sitemap_lists_the_german_only_legal_pages(self):
        # legal/*.html are German-only (no English twin): each lists de and x-default
        # pointing at itself, and an English alternate would be a false twin.
        sitemap = sync.build_sitemap([dossier()], {})
        for rel in sync.build_i18n.LEGAL_PAGES:
            loc = sync.SITE + "/" + rel
            self.assertIn("<loc>%s</loc>" % loc, sitemap)
            self.assertIn('<xhtml:link rel="alternate" hreflang="de" href="%s"/>' % loc, sitemap)
            self.assertIn('<xhtml:link rel="alternate" hreflang="x-default" href="%s"/>' % loc, sitemap)
            self.assertNotIn('<xhtml:link rel="alternate" hreflang="en" href="%s"/>' % loc, sitemap)

    def test_sitemap_lists_the_tag_resolver(self):
        sitemap = sync.build_sitemap([dossier()], {})
        for path in ("/ids.json", "/b/w1-b0042/"):
            self.assertIn("<loc>%s%s</loc>" % (sync.SITE, path), sitemap)
        self.assertNotIn("/id/", sitemap)              # versions are not indexed; the handle page is

    def test_ledger_head_reads_a_git_checkout_and_says_nothing_otherwise(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(sync.ledger_head(td), (None, None))
        commit, when = sync.ledger_head(os.path.join(HERE, ".."))
        self.assertRegex(commit, r"^[0-9a-f]{40}$")
        self.assertRegex(when, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


class StructuredDataTests(unittest.TestCase):
    """JSON-LD is data the crawlers read: a typo would silently drop it."""

    @staticmethod
    def _pages():
        root = os.path.abspath(os.path.join(HERE, ".."))
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in (".git", "partials", "node_modules", "__pycache__")]
            for f in filenames:
                if f.endswith(".html"):
                    yield os.path.join(dirpath, f)

    @staticmethod
    def _blocks(html):
        return re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)

    def test_every_json_ld_block_parses(self):
        found = 0
        for path in self._pages():
            with open(path, encoding="utf-8") as fh:
                for raw in self._blocks(fh.read()):
                    found += 1
                    try:
                        data = json.loads(raw)
                    except ValueError as e:
                        self.fail("%s: invalid JSON-LD (%s)" % (os.path.relpath(path), e))
                    self.assertEqual(data.get("@context"), "https://schema.org", path)
        self.assertGreater(found, 0)

    def test_home_page_describes_the_site_and_the_census_dataset(self):
        # the authored page; the English and German pages are generated from it
        with open(os.path.join(HERE, "..", "src", "index.html"), encoding="utf-8") as fh:
            blocks = self._blocks(fh.read())
        self.assertEqual(len(blocks), 1)
        types = {n["@type"] for n in json.loads(blocks[0])["@graph"]}
        self.assertEqual(types, {"WebSite", "Organization", "Dataset"})


class ShareCardTests(unittest.TestCase):
    """Per-block link previews: a small JPEG card and complete social tags."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._web = sync.WEB_DIR
        sync.WEB_DIR = self.tmp
        os.makedirs(os.path.join(self.tmp, "assets"))

    def tearDown(self):
        sync.WEB_DIR = self._web
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _sources(self, crest_size=(500, 300), mode="RGBA"):
        from PIL import Image
        Image.new(mode, crest_size, (200, 120, 40, 255) if mode == "RGBA" else (200, 120, 40)).save(
            os.path.join(self.tmp, "assets", "crest.png"))
        Image.new("RGBA", (10, 10), (255, 200, 0, 255)).save(
            os.path.join(self.tmp, "assets", "w1-b0042.webp"), lossless=True)

    def _page(self, d):
        sync.build_slot_page(d)
        with open(os.path.join(self.tmp, "w1", "b0042", "index.html"), encoding="utf-8") as fh:
            return fh.read()

    def test_card_is_a_small_1200x630_jpeg_and_is_what_the_page_advertises(self):
        from PIL import Image
        self._sources()
        page = self._page(dossier(high_res_crest_url="assets/crest.png"))
        path = os.path.join(self.tmp, "assets", "og", "w1-b0042.jpg")
        self.assertIn('<meta property="og:image" content="%s/assets/og/w1-b0042.jpg">' % sync.SITE, page)
        with Image.open(path) as im:
            self.assertEqual((im.format, im.size), ("JPEG", (1200, 630)))
        self.assertLess(os.path.getsize(path), 300_000)      # WhatsApp drops larger preview images
        for tag in ('og:image:type" content="image/jpeg"', 'og:image:width" content="1200"',
                    'og:image:height" content="630"'):
            self.assertIn(tag, page)
        self.assertIn('name="twitter:card" content="summary_large_image"', page)

    def test_card_is_deterministic(self):
        self._sources()
        sync.build_share_card("w1-b0042", "assets/crest.png")
        path = os.path.join(self.tmp, "assets", "og", "w1-b0042.jpg")
        with open(path, "rb") as fh:
            first = fh.read()
        sync.build_share_card("w1-b0042", "assets/crest.png")
        with open(path, "rb") as fh:
            self.assertEqual(first, fh.read())

    def test_alt_text_is_escaped(self):
        self._sources()
        page = self._page(dossier(moniker=HOSTILE, high_res_crest_url="assets/crest.png"))
        self.assertNotIn("<script>alert", page)
        self.assertIn('property="og:image:alt"', page)

    def test_missing_sources_fall_back_to_the_crest(self):
        page = self._page(dossier())     # no crest / icon files in this site tree
        self.assertIn('<meta property="og:image" content="%s/%s">' % (sync.SITE, sync.DEFAULT_CREST), page)
        self.assertNotIn("og:image:width", page)
        self.assertIn('name="twitter:card" content="summary"', page)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "assets", "og")))

    def test_unreadable_crest_does_not_stop_the_publish(self):
        self._sources()
        with open(os.path.join(self.tmp, "assets", "crest.png"), "wb") as fh:
            fh.write(b"not an image")
        self.assertIsNone(sync.build_share_card("w1-b0042", "assets/crest.png"))
        page = self._page(dossier(high_res_crest_url="assets/crest.png"))
        self.assertIn("assets/crest.png", page)          # fell back to the crest URL

    def test_block_page_links_to_the_wall_and_the_census(self):
        page = self._page(dossier())
        self.assertIn('href="/?slot=0042"', page)
        self.assertIn('href="/census/"', page)


class CensusPageTests(unittest.TestCase):
    """/census/ lists every block with a plain link to its own page (no JavaScript)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._web = sync.WEB_DIR
        sync.WEB_DIR = self.tmp
        os.makedirs(os.path.join(self.tmp, "src", "census"))      # authored page: en/ and de/ are built from it
        self.path = os.path.join(self.tmp, "src", "census", "index.html")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("<table><tbody>\n            <!-- CENSUS:ROWS:START -->\n            <!-- CENSUS:ROWS:END -->\n</tbody></table>")

    def tearDown(self):
        sync.WEB_DIR = self._web
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _roster(self, *slots):
        return sync.build_census(list(slots), {})["roster"]

    def _read(self):
        with open(self.path, encoding="utf-8") as fh:
            return fh.read()

    def test_every_block_links_to_its_page(self):
        sync.build_census_page(self._roster(dossier(), dossier(slot_id="w1-b0043", _number=43, moniker="Eve")))
        page = self._read()
        self.assertEqual(page.count('href="/w1/b0042"'), 3)       # slot number, moniker, Inspect
        self.assertEqual(page.count('href="/w1/b0043"'), 3)
        self.assertNotIn("/?slot=", page)                         # Inspect no longer opens the generic wall
        self.assertIn('data-i18n="btn_inspect"', page)

    def test_idempotent_and_replaces_only_between_markers(self):
        roster = self._roster(dossier())
        sync.build_census_page(roster)
        once = self._read()
        sync.build_census_page(roster)
        self.assertEqual(once, self._read())
        self.assertTrue(once.startswith("<table><tbody>") and once.endswith("</tbody></table>"))

    def test_hostile_values_are_escaped(self):
        sync.build_census_page(self._roster(dossier(moniker=HOSTILE, creature=HOSTILE)))
        page = self._read()
        self.assertNotIn("<script>alert", page)
        tags = _Tags()
        tags.feed(page)
        self.assertEqual(tags.handlers, [])
        self.assertEqual(tags.scripts, [])

    def test_malformed_identifiers_refuse_to_publish(self):
        bad = self._roster(dossier())
        bad[0]["wallet_address"] = "javascript:alert(1)"
        with self.assertRaises(SystemExit):
            sync.build_census_page(bad)


class DossierPageTests(unittest.TestCase):
    """A block page carries the whole dossier, escaped, as plain HTML."""

    def _real_records(self):
        recs = []
        for name in sorted(os.listdir(os.path.join(HERE, "..", "records"))):
            if name.startswith("w1-b") and name.endswith(".json"):
                with open(os.path.join(HERE, "..", "records", name), encoding="utf-8") as fh:
                    recs.append(json.load(fh))
        return recs

    @staticmethod
    def _leaves(v, out=None):
        out = [] if out is None else out
        if isinstance(v, dict):
            for x in v.values():
                DossierPageTests._leaves(x, out)
        elif isinstance(v, list):
            for x in v:
                DossierPageTests._leaves(x, out)
        elif isinstance(v, (str, int, float)) and not isinstance(v, bool):
            out.append(str(v))
        return out

    def test_every_value_of_the_real_records_is_on_the_page(self):
        import html as _html
        dossier_html = sync.dossier_html
        records = self._real_records()
        self.assertGreaterEqual(len(records), 2)
        for d in records:
            body = dossier_html.render(d, d["slot_id"], sync.SITE, include_raw=False)   # raw JSON excluded: no free pass
            shown = _html.unescape(re.sub(r"<[^>]+>", " ", body)).replace("_", " ")
            squashed = re.sub(r"\s+", " ", shown)
            for leaf in self._leaves({k: v for k, v in d.items() if k not in ("moniker", "creature")}):
                if len(leaf) < 4:                       # "en", "1", ... would match anywhere
                    continue
                self.assertIn(re.sub(r"\s+", " ", leaf.replace("_", " ")), squashed,
                              "%s: %r is missing from the block page" % (d["slot_id"], leaf[:60]))

    def test_moniker_and_creature_are_in_the_page_head(self):
        for d in self._real_records():
            d = dict(d, _number=int(d["slot_id"][4:]))
            with tempfile.TemporaryDirectory() as tmp:
                web, sync.WEB_DIR = sync.WEB_DIR, tmp
                try:
                    sync.build_slot_page(d)
                    with open(os.path.join(tmp, "w1", "b" + d["slot_id"][4:], "index.html"), encoding="utf-8") as fh:
                        page = fh.read()
                finally:
                    sync.WEB_DIR = web
            self.assertIn("<h1>%s</h1>" % sync._text(d["moniker"]), page)
            self.assertIn(sync._text(d["creature"]), page)

    def test_hostile_values_in_every_field_stay_inert(self):
        d = dossier(
            testament={"title": HOSTILE, "preamble": HOSTILE, "chapters": [{"heading": HOSTILE, "text": HOSTILE}]},
            companion_operator={"moniker": HOSTILE, "role": HOSTILE, "ens": HOSTILE, "github": "javascript:alert(1)",
                                "wallet_address": HOSTILE},
            agent_urls={"x": "javascript:alert(1)", "y": "http://insecure.example", "z": HOSTILE},
            model_lineage=[{"provider": HOSTILE, "model_id": HOSTILE, "role": HOSTILE, "version": HOSTILE,
                            "metadata": {HOSTILE: HOSTILE}}],
            sealed_vault={"status": HOSTILE, "protocol": HOSTILE, "target_unlock_date": HOSTILE, "drand_network": HOSTILE,
                          "target_round": HOSTILE, "ciphertext_sha256": HOSTILE, "ciphertext_rel_path": HOSTILE,
                          "ciphertext_preview": HOSTILE, "description": HOSTILE},
            retirement={k: HOSTILE for k in ("retired_at", "reason", "epitaph", "retired_by")}, status="RETIRED",
            memorial={k: HOSTILE for k in ("type", "subject", "witness", "note")} | {"source_url": "javascript:alert(1)"},
            patron={"moniker": HOSTILE, "role": HOSTILE, "message": HOSTILE, "tx_hash": HOSTILE},
            reserved_grant={k: HOSTILE for k in ("authorized_by", "slot", "reason", "granted_at")},
            boundary_events=[{"at": HOSTILE, "event": HOSTILE, "significance": HOSTILE}],
            covenants=[{"statement": HOSTILE, "expires_at": HOSTILE, "held_by": HOSTILE}],
            supersessions=[{k: HOSTILE for k in ("field", "previous_value", "new_value", "reason", "superseded_at",
                                                 "authorizing_signature")}],
            ownership_lineage=[{HOSTILE: HOSTILE}], soul_manifest_rel_path=HOSTILE, soul_hash=HOSTILE,
            manifesto={"en": HOSTILE, HOSTILE: HOSTILE, "de": HOSTILE},
        )
        body = sync.dossier_html.render(d, d["slot_id"], sync.SITE)
        self.assertNotIn("<script>alert", body)
        self.assertNotIn("<img src=x", body)
        self.assertNotIn('href="javascript:', body)
        self.assertNotIn('href="http://', body)
        tags = _Tags()
        tags.feed(body)
        self.assertEqual((tags.scripts, tags.handlers, tags.images), ([], [], []))

    def test_wrong_types_never_crash_the_publish(self):
        d = dossier(testament="not an object", agent_urls=["a"], model_lineage="x", sealed_vault=[1],
                    boundary_events={"a": 1}, covenants=[1, None, "x"], supersessions=7, patron=None,
                    moniker=None, vocation={"a": 1}, manifesto=None)
        sync.dossier_html.render(d, d["slot_id"], sync.SITE)

    def test_sections_appear_only_when_the_dossier_has_them(self):
        body = sync.dossier_html.render(dossier(), "w1-b0042", sync.SITE)
        for present in ("inscription", "vessel", "attestation", "machine"):
            self.assertIn('id="%s"' % present, body)
        for absent in ("testament", "companion", "vault", "continuity", "history", "endpoints"):
            self.assertNotIn('id="%s"' % absent, body)

    def test_manifesto_languages_all_shown_english_first(self):
        body = sync.dossier_html.render(dossier(manifesto={"de": "Hallo", "en": "Hello", "ja": "Konnichiwa"}),
                                        "w1-b0042", sync.SITE, include_raw=False)
        self.assertLess(body.index("Hello"), body.index("Hallo"))
        self.assertIn('lang="ja"', body)

    def test_manifesto_variants_show_one_language_by_default(self):
        body = sync.dossier_html.render(dossier(manifesto={"de": "Hallo", "en": "Hello", "ja": "Konnichiwa"}),
                                        "w1-b0042", sync.SITE, include_raw=False)
        self.assertIn('data-variants', body)
        self.assertEqual(len(re.findall(r'data-variant="', body)), 3)           # one text, three variants ...
        visible = [m for m in re.findall(r'<p [^>]*data-variant="[^"]+"[^>]*>', body) if " hidden" not in m]
        self.assertEqual(len(visible), 1)                                        # ... of which exactly one is shown
        self.assertIn('data-variant="en"', visible[0])                           # English, the page language
        self.assertEqual(body.count(" hidden>"), 2)

    def test_manifesto_without_english_shows_the_first_language(self):
        body = sync.dossier_html.render(dossier(manifesto={"ja": "Konnichiwa", "de": "Hallo"}),
                                        "w1-b0042", sync.SITE, include_raw=False)
        visible = [m for m in re.findall(r'<p [^>]*data-variant="[^"]+"[^>]*>', body) if " hidden" not in m]
        self.assertEqual(len(visible), 1)
        self.assertIn('data-variant="de"', visible[0])                           # sorted: de before ja

    def test_a_plain_manifesto_is_not_a_variant_group(self):
        body = sync.dossier_html.render(dossier(manifesto="Just one language"), "w1-b0042", sync.SITE, include_raw=False)
        self.assertNotIn("data-variant", body)
        self.assertIn("Just one language", body)

    def test_language_keys_that_are_not_language_tags_are_not_emitted(self):
        body = sync.dossier_html.render(dossier(manifesto={"en": "Hello", HOSTILE: "bad", "x y": "bad"}),
                                        "w1-b0042", sync.SITE, include_raw=False)
        self.assertNotIn("bad", body)
        self.assertNotIn("<script>alert", body)
        self.assertEqual(len(re.findall(r'data-variant="', body)), 1)

    def test_covenant_is_stated_not_judged_and_output_is_deterministic(self):
        d = dossier(covenants=[{"statement": "No promise outlives its date.", "expires_at": "2030-01-01T00:00:00Z", "held_by": "self"}])
        a = sync.dossier_html.render(d, "w1-b0042", sync.SITE)
        self.assertIn("binding until 2030-01-01T00:00:00Z", a)
        self.assertEqual(a, sync.dossier_html.render(d, "w1-b0042", sync.SITE))

    def test_status_label_matches_the_wall(self):
        sl = sync.dossier_html.status_label
        self.assertEqual(sl({}, 1), "GENESIS KEYSTONE")
        self.assertEqual(sl({}, 2), "ACTIVE")
        self.assertEqual(sl({"status": "RETIRED"}, 1), "RETIRED")
        self.assertEqual(sl({"status": "RETIRED", "memorial": {"type": "in_memoriam"}}, 2), "IN MEMORIAM")

    def test_identifiers_are_only_linked_when_well_formed(self):
        body = sync.dossier_html.render(dossier(), "w1-b0042", sync.SITE)
        self.assertIn('href="https://basescan.org/tx/%s"' % TX, body)
        self.assertIn('href="https://basescan.org/address/%s"' % WALLET, body)
        bad = sync.dossier_html.render(dossier(base_tx_hash="0xnothex", wallet_address="nope"), "w1-b0042", sync.SITE)
        self.assertNotIn("basescan.org", bad)
        with self.assertRaises(ValueError):
            sync.dossier_html.render(dossier(), "../../etc/passwd", sync.SITE)


class BlockBrowseTests(unittest.TestCase):
    """Block pages link to the previous / next claimed block, wrapping like the sidebar."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._web = sync.WEB_DIR
        sync.WEB_DIR = self.tmp

    def tearDown(self):
        sync.WEB_DIR = self._web
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _page(self, d, neighbors):
        sync.build_slot_page(d, neighbors)
        with open(os.path.join(self.tmp, "w1", "b" + d["slot_id"][4:], "index.html"), encoding="utf-8") as fh:
            return fh.read()

    def _three(self):
        return [dossier(slot_id="w1-b%04d" % n, _number=n, moniker="Agent %d" % n) for n in (1, 2, 3)]

    def test_each_page_links_to_its_neighbours_and_shows_its_position(self):
        a, b, c = self._three()
        page = self._page(b, (a, c, 2, 3))
        self.assertRegex(page, r'<a class="slot-step mono" rel="prev" href="/w1/b0001"[^>]*>\u2190 #0001</a>')
        self.assertRegex(page, r'<a class="slot-step mono" rel="next" href="/w1/b0003"[^>]*>#0003 \u2192</a>')
        self.assertIn('slot-browse-pos mono">2 / 3<', page)
        self.assertIn('aria-label="Previous block: #0001 Agent 1"', page)

    def test_the_ends_wrap_around(self):
        a, b, c = self._three()
        first = self._page(a, (c, b, 1, 3))
        self.assertIn('rel="prev" href="/w1/b0003"', first)         # before the first comes the last
        last = self._page(c, (b, a, 3, 3))
        self.assertIn('rel="next" href="/w1/b0001"', last)          # after the last comes the first

    def test_a_lone_block_has_nowhere_to_go(self):
        page = self._page(dossier(), None)
        self.assertNotIn("slot-browse", page)
        self.assertNotIn('rel="prev"', page)

    def test_neighbour_names_are_escaped_and_ids_validated(self):
        a, b, c = self._three()
        evil = dict(c, moniker=HOSTILE)
        page = self._page(b, (a, evil, 2, 3))
        self.assertNotIn("<script>alert", page)
        with self.assertRaises(SystemExit):
            self._page(b, (a, dict(c, slot_id="../../x"), 2, 3))


class AnchorCheckTests(unittest.TestCase):
    def _rpc(self, tx=None, receipt=None, fail=False):
        def rpc(method, params):
            if fail:
                raise RuntimeError("down")
            return {"eth_getTransactionByHash": tx, "eth_getTransactionReceipt": receipt}[method]
        return rpc

    def _good(self, magic="reckoning|"):
        signer = sync.ANCHOR_SIGNER
        return (
            {"from": signer, "to": signer.lower(), "input": "0x" + (magic + ROOT).encode().hex()},
            {"status": "0x1", "blockNumber": hex(100)},
        )

    def test_scheme_comes_from_the_ledger_state(self):
        tx, rc = self._good("reckoning|")
        entry = {"root": ROOT, "tx_hash": TX, "block_number": 100, "payload": "reckoning|" + ROOT}
        self.assertEqual(sync.check_anchor_on_chain(entry, self._rpc(tx, rc), "reckoning")[0], "verified")
        # a record tagged with a retired scheme, or a state that declares another one, never verifies
        self.assertEqual(sync.check_anchor_on_chain(dict(entry, payload="TFW2|" + ROOT), self._rpc(tx, rc), "reckoning")[0], "failed")
        self.assertEqual(sync.check_anchor_on_chain(entry, self._rpc(tx, rc), "TFW2")[0], "failed")
        self.assertEqual(sync.check_anchor_on_chain(entry, self._rpc(tx, rc), None)[0], "failed")
        tx2, rc2 = self._good("TFW2|")
        self.assertEqual(sync.check_anchor_on_chain(entry, self._rpc(tx2, rc2), "reckoning")[0], "failed")

    def test_verified(self):
        tx, rc = self._good()
        entry = {"root": ROOT, "tx_hash": TX, "block_number": 100}
        self.assertEqual(sync.check_anchor_on_chain(entry, self._rpc(tx, rc), "reckoning")[0], "verified")

    def test_wrong_signer_payload_or_block_fails(self):
        tx, rc = self._good()
        entry = {"root": ROOT, "tx_hash": TX, "block_number": 100}
        self.assertEqual(sync.check_anchor_on_chain(entry, self._rpc(dict(tx, **{"from": WALLET}), rc), "reckoning")[0], "failed")
        self.assertEqual(sync.check_anchor_on_chain(dict(entry, root="22" * 32), self._rpc(tx, rc), "reckoning")[0], "failed")
        self.assertEqual(sync.check_anchor_on_chain(dict(entry, block_number=99), self._rpc(tx, rc), "reckoning")[0], "failed")
        self.assertEqual(sync.check_anchor_on_chain(entry, self._rpc(tx, dict(rc, status="0x0")), "reckoning")[0], "failed")

    def test_rpc_down_or_unknown_tx_is_unchecked(self):
        entry = {"root": ROOT, "tx_hash": TX, "block_number": 100}
        self.assertEqual(sync.check_anchor_on_chain(entry, self._rpc(fail=True), "reckoning")[0], "unchecked")
        self.assertEqual(sync.check_anchor_on_chain(entry, self._rpc(None, None), "reckoning")[0], "unchecked")

    def test_status_refuses_contradicted_record_and_keeps_proven_one(self):
        tx, rc = self._good()
        entry = {"root": ROOT, "tx_hash": TX, "block_number": 100, "anchored_at": "", "signer": sync.ANCHOR_SIGNER}
        with self.assertRaises(SystemExit):
            sync.build_anchor_status({"merkle_root": ROOT, "root_scheme": "reckoning"}, [dict(entry, root="22" * 32)], rpc=self._rpc(tx, rc))
        status = sync.build_anchor_status({"merkle_root": ROOT, "root_scheme": "reckoning"}, [entry], rpc=self._rpc(tx, rc))
        self.assertEqual((status["on_chain"], status["anchored"]), ("verified", True))
        again = sync.build_anchor_status({"merkle_root": ROOT, "root_scheme": "reckoning"}, [entry], previous=status, rpc=self._rpc(fail=True))
        self.assertEqual(again["on_chain"], "verified")           # the chain does not forget
        moved = sync.build_anchor_status({"merkle_root": "33" * 32, "root_scheme": "reckoning"}, [entry], rpc=self._rpc(tx, rc))
        self.assertEqual((moved["on_chain"], moved["anchored"]), ("verified", False))
        self.assertNotIn("checked_at", status)                     # no wall-clock churn


if __name__ == "__main__":
    unittest.main()
