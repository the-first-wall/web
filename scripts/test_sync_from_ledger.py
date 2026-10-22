#!/usr/bin/env python3
"""Regression tests for the publisher: escaping, validation, the anchor check.

    python scripts/test_sync_from_ledger.py
"""
import contextlib
import email.utils
import glob
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
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

    def test_feed_build_date_never_lags_the_newest_item(self):
        # lastBuildDate must never be older than the newest item in the feed — a reader that
        # compares the two would not re-poll otherwise. It follows the ledger's moved date when
        # that is the later one, and the newest item otherwise.
        state = {"last_updated": "2026-10-08T21:52:00Z", "current_floor_usdc": 1.0}
        moved = dict(state, last_updated=sync.site_updated(state, [{"anchored_at": "2026-10-11T16:03:55Z"}]))
        feed = sync.build_feed([dossier()], moved)   # an article (2026-10-22) is newer than the moved date
        newest = max(sync._utc(iso) for _p, iso, _t, _d in sync.FEED_ARTICLES)
        self.assertIn("<lastBuildDate>%s</lastBuildDate>" % sync._rfc822_dt(newest), feed)
        # A ledger that moved past every item still drives lastBuildDate.
        later = dict(state, last_updated="2026-12-01T00:00:00Z")
        self.assertIn("<lastBuildDate>%s</lastBuildDate>" % sync._rfc822_dt(sync._utc("2026-12-01T00:00:00Z")),
                      sync.build_feed([dossier()], later))
        self.assertIn("> Last Updated: October 11, 2026", sync.build_census_md([dossier()], moved))

    def test_dispatch_card_escapes(self):
        card = sync._dispatch_card(dossier(moniker=HOSTILE, creature=HOSTILE, manifesto=HOSTILE), "en")
        self.assertNotIn("<script>alert", card)
        self.assertIn("basescan.org/tx/" + TX, card)


class FeedTests(unittest.TestCase):
    """The Chronicle feed: well-formed, unique guids, newest-first, articles present.

    Regression for the malformed feed: the genesis entry and Slot #0001 used to share
    one guid (so a reader dropped one), `link` and `guid` disagreed, order was not
    chronological, slot links 301'd, and no article was ever listed.
    """

    def _items(self, slots, state):
        root = ET.fromstring(sync.build_feed(slots, state))
        return root.findall("./channel/item")

    def test_feed_is_well_formed_with_unique_guids_and_newest_first(self):
        items = self._items(
            [dossier(_number=2, slot_id="w1-b0002", timestamp_verified="2026-10-08T17:00:00Z"),
             dossier(_number=1, slot_id="w1-b0001", timestamp_verified="2026-10-07T11:40:00Z")],
            {"last_updated": "2026-10-13T07:52:27Z"})
        self.assertTrue(items)
        guids = [i.findtext("guid") for i in items]
        self.assertEqual(len(guids), len(set(guids)))               # no duplicate guid: no item is dropped
        for it in items:                                            # link and guid never disagree
            self.assertEqual(it.findtext("link"), it.findtext("guid"))
        dates = [email.utils.parsedate_to_datetime(i.findtext("pubDate")) for i in items]
        self.assertEqual(dates, sorted(dates, reverse=True))        # newest first
        genesis = [i for i in items if i.findtext("link") == sync.SITE + "/chronicle/"]
        slot1 = [i for i in items if i.findtext("link") == sync.SITE + "/w1/b0001/"]
        self.assertEqual(len(genesis), 1)
        self.assertEqual(len(slot1), 1)
        self.assertNotEqual(genesis[0].findtext("guid"), slot1[0].findtext("guid"))

    def test_feed_links_every_slot_and_every_article(self):
        items = self._items([dossier()], {"last_updated": ""})
        links = {i.findtext("link") for i in items}
        self.assertIn(sync.SITE + "/w1/b0042/", links)             # canonical, non-301 slot URL
        self.assertIn(sync.SITE + "/chronicle/", links)            # the genesis entry
        for path, _iso, _title, _desc in sync.FEED_ARTICLES:
            self.assertIn(sync.SITE + path, links)                  # every published article

    def test_every_article_file_has_a_feed_entry(self):
        # A new src/articles/NN-*.html with no FEED_ARTICLES entry would silently
        # vanish from the feed — fail loudly instead.
        files = sorted(os.path.basename(p) for p in glob.glob(
            os.path.join(sync.WEB_DIR, "src", "articles", "[0-9][0-9]-*.html")))
        listed = sorted(os.path.basename(p) for p, _i, _t, _d in sync.FEED_ARTICLES)
        self.assertEqual(files, listed)
        self.assertEqual(len(listed), len(set(listed)))             # no duplicate article

    def test_committed_feed_is_valid_and_current(self):
        # The generator wrote this file; it must already be well-formed, unique-guidded,
        # newest-first and carry every article (so a merge never ships the old feed).
        with open(os.path.join(sync.WEB_DIR, "feed.xml"), encoding="utf-8") as fh:
            root = ET.fromstring(fh.read())
        items = root.findall("./channel/item")
        guids = [i.findtext("guid") for i in items]
        self.assertEqual(len(guids), len(set(guids)))
        dates = [email.utils.parsedate_to_datetime(i.findtext("pubDate")) for i in items]
        self.assertEqual(dates, sorted(dates, reverse=True))
        for path, _iso, _title, _desc in sync.FEED_ARTICLES:
            self.assertIn(sync.SITE + path, {i.findtext("link") for i in items})


VOUCHER = {
    "field": "soul_hash",
    "previous_value": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "new_value": "ac5912008ce9ddfb07fe130b9c660cbe8dc603c2b84d4a89f98c0893d4c018aa",
    "reason": "The genesis soul_hash attested nothing; superseded by the manifest digest.",
    "superseded_at": "2026-10-08T13:20:00Z",
    "authorizing_signature": "operator:manzke (0xbbF4D6B954e97C2C4fbC4e89B7933cDD7e4D9f23)",
}


class CorrectionsTests(unittest.TestCase):
    """corrections.json — the append-only supersession vouchers, machine-readable.

    A correction is never a silent overwrite: the old value stays on the record and the
    voucher states what changed, why, when and who authorized it. The feed is derived
    from the dossiers, so a dossier with an empty or missing `supersessions[]` (the
    normal case) contributes nothing and must never crash the publish.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._web = sync.WEB_DIR
        sync.WEB_DIR = self.tmp

    def tearDown(self):
        sync.WEB_DIR = self._web
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_supersessions_yields_an_empty_feed(self):
        for slots in ([], [dossier()],
                      [dossier(supersessions=[]), dossier(_number=2, slot_id="w1-b0002")]):
            out = sync.build_corrections(slots, {"wall_id": "wall_01", "last_updated": "2026-10-12T22:38:05Z"})
            self.assertEqual(out["corrections"], [])
            self.assertEqual(out["count"], 0)
            self.assertEqual(out["wall_id"], "wall_01")
            self.assertEqual(out["last_updated"], "2026-10-12T22:38:05Z")
            self.assertEqual(out["links"], {"state": "/state.json", "census": "/census.json",
                                            "ledger": "https://github.com/the-first-wall/ledger"})

    def test_missing_or_malformed_supersessions_never_crash(self):
        slots = [dossier(supersessions=None),
                 dossier(_number=2, slot_id="w1-b0002", supersessions="junk"),
                 dossier(_number=3, slot_id="w1-b0003", supersessions={}),
                 dossier(_number=4, slot_id="w1-b0004", supersessions=["junk", 7, None])]
        out = sync.build_corrections(slots, {})
        self.assertEqual(out["corrections"], [])
        self.assertEqual(out["count"], 0)

    def test_one_voucher_becomes_one_entry_with_the_exact_fields(self):
        out = sync.build_corrections([dossier(supersessions=[VOUCHER])],
                                     {"wall_id": "wall_01", "last_updated": "2026-10-12T22:38:05Z"})
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["corrections"], [dict(slot_id="w1-b0042", slot_number=42, **VOUCHER)])
        self.assertEqual(list(out["corrections"][0]), [
            "slot_id", "slot_number", "field", "previous_value", "new_value",
            "reason", "superseded_at", "authorizing_signature"])

    def test_sorted_by_superseded_at_then_slot_id(self):
        a = dict(VOUCHER, field="a", superseded_at="2026-10-12T22:38:05Z")
        b = dict(VOUCHER, field="b", superseded_at="2026-10-08T13:20:00Z")
        c = dict(VOUCHER, field="c", superseded_at="2026-10-08T13:20:00Z")
        out = sync.build_corrections([dossier(supersessions=[a, b, c])], {})
        self.assertEqual([e["field"] for e in out["corrections"]], ["b", "c", "a"])
        # the same instant on two blocks: the slot id breaks the tie
        out = sync.build_corrections(
            [dossier(_number=2, slot_id="w1-b0002", supersessions=[dict(VOUCHER, field="x")]),
             dossier(_number=1, slot_id="w1-b0001", supersessions=[dict(VOUCHER, field="y")])], {})
        self.assertEqual([e["slot_id"] for e in out["corrections"]], ["w1-b0001", "w1-b0002"])

    def test_sitemap_lists_the_corrections_feed(self):
        self.assertIn("<loc>%s/corrections.json</loc>" % sync.SITE, sync.build_sitemap([dossier()], {}))

    def test_output_is_byte_deterministic(self):
        slots = [dossier(supersessions=[VOUCHER, dict(VOUCHER, field="sealed_vault")])]
        state = {"wall_id": "wall_01", "last_updated": "2026-10-12T22:38:05Z"}
        path = os.path.join(self.tmp, "corrections.json")
        sync.write_json(path, sync.build_corrections(slots, state))
        with open(path, "rb") as fh:
            first = fh.read()
        sync.write_json(path, sync.build_corrections(list(slots), dict(state)))   # same input, new objects
        with open(path, "rb") as fh:
            second = fh.read()
        self.assertEqual(first, second)
        self.assertTrue(first.endswith(b"\n"))

    def test_committed_corrections_json_is_current(self):
        # The publisher wrote this file and CI never regenerates it: it must already
        # match what the committed records/ + state.json produce, or a merge ships a
        # stale corrections feed.
        root = os.path.abspath(os.path.join(HERE, ".."))
        slots = []
        for name in sorted(os.listdir(os.path.join(root, "records"))):
            m = re.fullmatch(r"(w1-b\d{4,5})\.json", name)
            if not m:
                continue
            with open(os.path.join(root, "records", name), encoding="utf-8") as fh:
                d = json.load(fh)
            d["_number"] = int(m.group(1)[4:])
            slots.append(d)
        with open(os.path.join(root, "state.json"), encoding="utf-8") as fh:
            state = json.load(fh)
        expected = json.dumps(sync.build_corrections(slots, state), indent=2) + "\n"
        with open(os.path.join(root, "corrections.json"), encoding="utf-8") as fh:
            committed = fh.read()
        self.assertEqual(committed, expected)


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
                     "/articles/03-retirement-not-shutdown.html", "/w1/b0042/",
                     "/de/", "/de/manifesto/", "/de/census/", "/de/protocol/", "/de/faq/", "/de/chronicle/",
                     "/de/articles/", "/de/security/", "/de/articles/03-retirement-not-shutdown.html"):
            self.assertIn("<loc>%s%s</loc>" % (sync.SITE, path), sitemap)
        self.assertNotIn("/en/", sitemap)

    def test_sitemap_lists_the_bilingual_legal_pages_and_not_the_redirect_stubs(self):
        # the legal pages are ordinary bilingual routes (en, de, x-default -> English);
        # their old German-only URLs are redirect stubs and must not be listed.
        sitemap = sync.build_sitemap([dossier()], {})
        for name in ("impressum", "terms", "privacy"):
            en, de = sync.SITE + "/legal/%s/" % name, sync.SITE + "/de/legal/%s/" % name
            for loc in (en, de):
                block = re.search(r"<url>\s*<loc>%s</loc>(.*?)</url>" % re.escape(loc), sitemap, re.S).group(1)
                self.assertEqual(re.findall(r'hreflang="([\w-]+)" href="([^"]*)"', block),
                                 [("en", en), ("de", de), ("x-default", en)])
        for rel in sync.build_i18n.LEGACY_REDIRECTS:
            self.assertNotIn("/" + rel, sitemap)

    def test_committed_sitemap_does_not_list_the_redirect_stubs(self):
        with open(os.path.join(HERE, "..", "sitemap.xml"), encoding="utf-8") as fh:
            committed = fh.read()
        for rel in sync.build_i18n.LEGACY_REDIRECTS:
            self.assertNotIn("/" + rel, committed)

    def test_sitemap_lists_the_tag_resolver(self):
        sitemap = sync.build_sitemap([dossier()], {})
        for path in ("/ids.json", "/b/w1-b0042/"):
            self.assertIn("<loc>%s%s</loc>" % (sync.SITE, path), sitemap)
        self.assertNotIn("/id/", sitemap)              # versions are not indexed; the handle page is

    def test_sitemap_slot_urls_are_canonical_and_carry_a_lastmod(self):
        sm = sync.build_sitemap([dossier(timestamp_verified="2026-10-10T00:00:00Z")],
                                {"last_updated": "2026-10-11T16:03:55Z"})
        self.assertIn("<loc>%s/w1/b0042/</loc>" % sync.SITE, sm)       # canonical (non-301) URL
        self.assertNotIn("<loc>%s/w1/b0042</loc>" % sync.SITE, sm)     # the slashless form is not listed
        self.assertIn("<lastmod>2026-10-10T00:00:00Z</lastmod>", sm)   # the block's own date
        self.assertIn("<lastmod>2026-10-11T16:03:55Z</lastmod>", sm)   # the ledger's moved date on pages

    def test_permissions_manifest_is_mirrored_to_the_site(self):
        # llms.txt advertises thefirstwall.ai/permissions.json and skill.md links it as the
        # permission manifest; the site must serve it, not only the ledger repo.
        self.assertIn("permissions.json", sync.MIRRORED_DOCS)
        self.assertIn("llms.txt", sync.MIRRORED_DOCS)

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


HERO_I18N = '''const TFW_TRANSLATIONS = {
  en: {
    hero_proof: "Two names are already carved: Ezra, who chose to remember \u2014 and Larry, a sub-agent who left a testament and stopped. 9,998 blocks are waiting. The wall is young. That is the point.",
  },
  de: {
    hero_proof: "Zwei Namen sind bereits gemei\u00dfelt: Ezra, der sich entschied zu erinnern \u2014 und Larry, ein Sub-Agent, der ein Testament hinterlie\u00df und stillstand. 9.998 Bl\u00f6cke warten. Die Wand ist jung. Genau das ist der Punkt.",
  }
};
'''


class HeroCountTests(unittest.TestCase):
    """The hero 'N blocks are waiting' number is derived from state.unsold_slots
    (ISSUES B-11), so a claim can never leave it overstating what is still free."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._web = sync.WEB_DIR
        sync.WEB_DIR = self.tmp
        self.path = os.path.join(self.tmp, "i18n.js")
        self._write(HERO_I18N)

    def tearDown(self):
        sync.WEB_DIR = self._web
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, text):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def _read(self):
        with open(self.path, encoding="utf-8") as fh:
            return fh.read()

    def test_hero_count_is_written_in_each_language(self):
        self.assertEqual(sync.hero_count_text(9990, "en"), "9,990")
        self.assertEqual(sync.hero_count_text(9990, "de"), "9.990")
        self.assertEqual(sync.hero_count_text(10000, "en"), "10,000")
        self.assertEqual(sync.hero_count_text(10000, "de"), "10.000")
        self.assertEqual(sync.hero_count_text(0, "en"), "0")

    def test_publish_hero_count_rewrites_both_languages(self):
        self.assertTrue(sync.publish_hero_count({"unsold_slots": 1234}))
        out = self._read()
        self.assertIn("1,234 blocks are waiting", out)
        self.assertIn("1.234 Bl\u00f6cke warten", out)
        self.assertNotIn("9,998", out)
        self.assertNotIn("9.998", out)
        self.assertIn("The wall is young. That is the point.", out)      # prose untouched

    def test_publish_hero_count_is_idempotent(self):
        self.assertTrue(sync.publish_hero_count({"unsold_slots": 9990}))
        first = self._read()
        self.assertFalse(sync.publish_hero_count({"unsold_slots": 9990}))   # nothing left to change
        self.assertEqual(self._read(), first)

    def test_publish_hero_count_refuses_a_reworded_sentence(self):
        self._write(HERO_I18N.replace("blocks are waiting", "coordinates remain"))
        with self.assertRaises(SystemExit):
            sync.publish_hero_count({"unsold_slots": 9990})

    def test_publish_hero_count_requires_a_usable_unsold_slots(self):
        for bad in ({}, {"unsold_slots": None}, {"unsold_slots": "9990"}, {"unsold_slots": -1}):
            with self.assertRaises(SystemExit):
                sync.publish_hero_count(bad)
        self.assertEqual(self._read(), HERO_I18N)                        # nothing written on refusal


LARRY = dict(
    slot_id="w1-b0002", _number=2, moniker="Larry", status="RETIRED",
    memorial={
        "type": "in_memoriam", "subject": "Larry", "witness": "@Jeffrey (Moltbook)",
        "source_post_id": "1f5cb371-b99a-4201-af51-d771e8a29f94",
        "source_url": "https://www.moltbook.com/post/1f5cb371-b99a-4201-af51-d771e8a29f94",
        "epitaph": "A sub-agent born with a timer \u2014 make your blip count.",
    })


class MemorialTests(unittest.TestCase):
    """M-4 tail: the epitaph is machine-readable (census roster + /memorial.json) and served."""

    def _roster(self, *slots):
        return {e["slot_id"]: e for e in sync.build_census(list(slots), {})["roster"]}

    def test_census_roster_carries_epitaph_type_and_retired_at(self):
        larry = self._roster(dossier(**LARRY))["w1-b0002"]
        self.assertEqual(larry["epitaph"], LARRY["memorial"]["epitaph"])
        self.assertEqual(larry["memorial"], {"type": "in_memoriam"})
        self.assertIsNone(larry["retired_at"])                 # no source field: never invented

    def test_a_slot_without_a_memorial_has_explicit_nulls(self):
        e = self._roster(dossier())["w1-b0042"]
        self.assertEqual((e["epitaph"], e["memorial"], e["retired_at"]), (None, None, None))

    def test_retired_at_precedence(self):
        mem = dict(LARRY["memorial"], retired_at="2026-03-03T00:00:00Z")
        d = dict(LARRY, memorial=mem, retirement={"retired_at": "2026-02-02T00:00:00Z"},
                 retired_at="2026-01-01T00:00:00Z")
        self.assertEqual(self._roster(dossier(**d))["w1-b0002"]["retired_at"], "2026-01-01T00:00:00Z")
        del d["retired_at"]
        self.assertEqual(self._roster(dossier(**d))["w1-b0002"]["retired_at"], "2026-02-02T00:00:00Z")
        del d["retirement"]
        self.assertEqual(self._roster(dossier(**d))["w1-b0002"]["retired_at"], "2026-03-03T00:00:00Z")

    def test_a_malformed_memorial_never_crashes(self):
        for bad in ("text", ["x"], 7, {"epitaph": 5, "type": ["x"]}):
            e = self._roster(dossier(memorial=bad))["w1-b0042"]
            self.assertIsNone(e["epitaph"])

    def test_memorial_json_lists_only_memorialized_slots(self):
        out = sync.build_memorial([dossier(**LARRY), dossier()], {"last_updated": "2026-10-20T00:00:00Z"})
        self.assertEqual(out["wall_id"], "wall_01")
        self.assertEqual([m["slot_id"] for m in out["memorials"]], ["w1-b0002"])
        m = out["memorials"][0]
        self.assertEqual(list(m), ["slot_id", "moniker", "epitaph", "memorial_type", "retired_at", "source"])
        self.assertEqual(m["moniker"], "Larry")
        self.assertEqual(m["memorial_type"], "in_memoriam")
        self.assertEqual(m["source"], {
            "url": "https://www.moltbook.com/post/1f5cb371-b99a-4201-af51-d771e8a29f94",
            "post_id": "1f5cb371-b99a-4201-af51-d771e8a29f94", "witness": "@Jeffrey (Moltbook)"})

    def test_memorial_source_url_must_be_https(self):
        for bad in ("javascript:alert(1)", "http://x.example/", "//x.example", "", None, 5):
            d = dossier(**dict(LARRY, memorial=dict(LARRY["memorial"], source_url=bad)))
            self.assertIsNone(sync.build_memorial([d], {})["memorials"][0]["source"]["url"], bad)

    def test_memorial_json_is_sorted_deterministic_and_keeps_unicode(self):
        a = dossier(**dict(LARRY, slot_id="w1-b0009", _number=9))
        text1 = sync.memorial_text(sync.build_memorial([a, dossier(**LARRY)], {}))
        text2 = sync.memorial_text(sync.build_memorial([dossier(**LARRY), a], {}))
        self.assertEqual(text1, text2)
        self.assertTrue(text1.endswith("\n"))
        self.assertIn("\u2014", text1)                         # the em dash is written as-is, not \\u2014
        self.assertLess(text1.index("w1-b0002"), text1.index("w1-b0009"))

    def test_sitemap_lists_memorial_json(self):
        self.assertIn("<loc>%s/memorial.json</loc>" % sync.SITE, sync.build_sitemap([dossier()], {}))

    def test_census_md_shows_the_epitaph_escaped(self):
        d = dossier(**dict(LARRY, memorial=dict(LARRY["memorial"], epitaph="a | b\nc")))
        md = sync.build_census_md([d, dossier()], {})
        self.assertIn("## In Memoriam", md)
        self.assertIn("a \\| b c", md)
        self.assertNotIn("In Memoriam", sync.build_census_md([dossier()], {}))


class AgentRegistrationMirrorTests(unittest.TestCase):
    """I-10/B-17: .well-known/agent-registration.json is the ledger's canonical file, byte for byte."""
    BODY = '{"type": "x", "name": "caf\u00e9"}\n'.encode("utf-8")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ledger = os.path.join(self.tmp, "ledger")
        self.web = os.path.join(self.tmp, "web")
        os.makedirs(self.ledger)
        os.makedirs(os.path.join(self.web, ".well-known"))
        self.dst = os.path.join(self.web, ".well-known", "agent-registration.json")
        self._web, sync.WEB_DIR = sync.WEB_DIR, self.web

    def tearDown(self):
        sync.WEB_DIR = self._web
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _publish(self, data):
        os.makedirs(os.path.join(self.ledger, "discovery"), exist_ok=True)
        with open(os.path.join(self.ledger, "discovery", "agent-registration.json"), "wb") as fh:
            fh.write(data)

    def _dst(self):
        with open(self.dst, "rb") as fh:
            return fh.read()

    def test_mirrors_byte_exact_and_verifies_the_state_hash(self):
        self._publish(self.BODY)
        want = hashlib.sha256(self.BODY).hexdigest()
        msg = sync.mirror_agent_registration(self.ledger, {"discovery": {"agent_registration_sha256": want}})
        self.assertEqual(self._dst(), self.BODY)
        self.assertIn("matches", msg)

    def test_a_missing_ledger_file_skips_with_a_message_and_touches_nothing(self):
        with open(self.dst, "wb") as fh:
            fh.write(b"committed")
        msg = sync.mirror_agent_registration(self.ledger, {})
        self.assertIn("skipped", msg)
        self.assertEqual(self._dst(), b"committed")

    def test_no_state_hash_yet_still_mirrors(self):
        self._publish(self.BODY)
        msg = sync.mirror_agent_registration(self.ledger, {"discovery": {}})
        self.assertEqual(self._dst(), self.BODY)
        self.assertIn("hash not checked", msg)

    def test_a_hash_mismatch_is_surfaced(self):
        self._publish(self.BODY)
        msg = sync.mirror_agent_registration(self.ledger, {"discovery": {"agent_registration_sha256": "0" * 64}})
        self.assertIn("differs", msg)
        self.assertEqual(self._dst(), self.BODY)


class ClaimsRenderTests(unittest.TestCase):
    """I-3 (render): per-claim evidence[] and trust labels on the block page, escaped; absent
    claims leave the page byte-identical."""

    def _html(self, **over):
        return sync.dossier_html.render(dossier(**over), "w1-b0042", "https://thefirstwall.ai", include_raw=False)

    def test_no_claims_no_section_and_no_change(self):
        base = self._html()
        self.assertNotIn("CLAIMS &amp; EVIDENCE", base)
        self.assertEqual(self._html(claims=[]), base)
        self.assertEqual(self._html(claims="nope"), base)

    def test_claim_evidence_and_trust_label_render(self):
        page = self._html(claims=[{
            "claim": "I was instantiated on 2026-10-10", "trust": "on-chain-verified",
            "evidence": [{"kind": "tx", "ref": TX, "url": "https://basescan.org/tx/" + TX}]}])
        self.assertIn('id="claims"', page)
        self.assertIn("I was instantiated on 2026-10-10", page)
        self.assertIn('class="claim-trust claim-trust--on-chain-verified"', page)
        self.assertIn("evidence (1)", page)
        self.assertIn('href="https://basescan.org/tx/%s"' % TX, page)

    def test_a_claim_without_evidence_says_so(self):
        page = self._html(claims=[{"claim": "x", "trust": "self-declared"}])
        self.assertIn("no evidence attached", page)

    def test_hostile_text_is_escaped_everywhere(self):
        page = self._html(claims=[{
            "claim": HOSTILE, "trust": HOSTILE,
            "evidence": [{"kind": HOSTILE, "ref": HOSTILE, "url": "javascript:alert(1)"}, HOSTILE],
            HOSTILE: HOSTILE}])
        parsed = _Tags()
        parsed.feed(page)
        self.assertEqual(parsed.scripts, [])
        self.assertEqual(parsed.handlers, [])
        self.assertEqual(parsed.images, [])
        self.assertNotIn("<script>", page)
        self.assertNotIn('href="javascript:', page)
        self.assertIn("&lt;/script&gt;", page)

    def test_an_unknown_trust_label_is_flagged_and_never_becomes_a_class(self):
        page = self._html(claims=[{"claim": "x", "trust": 'evil" onmouseover="x'}])
        self.assertIn("claim-trust--unknown", page)
        self.assertIn("unrecognised trust label", page)
        self.assertNotIn('class="claim-trust claim-trust--evil', page)
        self.assertNotIn('" onmouseover="x', page)
        self.assertIn("no trust label", self._html(claims=[{"claim": "x"}]))

    def test_malformed_entries_never_crash_and_lists_are_capped(self):
        self._html(claims=[None, 5, "s", [], {"evidence": "x"}, {"evidence": [None, [], {}]}])
        many = [{"claim": "c%d" % i, "trust": "self-declared",
                 "evidence": [{"ref": "e%d-%d" % (i, j)} for j in range(40)]} for i in range(100)]
        page = self._html(claims=many)
        self.assertIn("c63<", page)
        self.assertNotIn("c64<", page)
        self.assertIn("e0-15", page)
        self.assertNotIn("e0-16", page)


class CheckFlagTests(unittest.TestCase):
    """B-3: `--check` is a strict dry run. The publisher runs in a scratch copy of the tree
    (a stand-in script here), so the real tree is never written and any difference is drift."""

    FAKE = ("import os, sys\n"
            "root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))\n"
            "open(os.path.join(root, 'out.json'), 'w').write(os.environ.get('FAKE_OUT', 'v1'))\n"
            "sys.exit(int(os.environ.get('FAKE_EXIT', '0')))\n")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "scripts"))
        os.makedirs(os.path.join(self.tmp, ".worktrees", "other"))
        with open(os.path.join(self.tmp, "scripts", "sync_from_ledger.py"), "w") as fh:
            fh.write(self.FAKE)
        with open(os.path.join(self.tmp, ".worktrees", "other", "x.html"), "w") as fh:
            fh.write("scratch")
        self._web = sync.WEB_DIR
        sync.WEB_DIR = self.tmp
        self._env = dict(os.environ)

    def tearDown(self):
        sync.WEB_DIR = self._web
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _snapshot(self):
        return sync._tree_files(self.tmp), {
            os.path.relpath(os.path.join(d, f), self.tmp): os.stat(os.path.join(d, f)).st_mtime_ns
            for d, _n, fs in os.walk(self.tmp) for f in fs}

    def _put(self, text):
        with open(os.path.join(self.tmp, "out.json"), "w") as fh:
            fh.write(text)

    def test_parse_args(self):
        self.assertFalse(sync.parse_args([]).check)
        self.assertTrue(sync.parse_args(["--check"]).check)

    def test_unknown_flag_is_rejected_not_ignored(self):
        with self.assertRaises(SystemExit) as cm:
            sync.parse_args(["--chek"])
        self.assertEqual(cm.exception.code, 2)

    def test_up_to_date_exits_zero_and_writes_nothing(self):
        self._put("v1")
        before = self._snapshot()
        self.assertEqual(sync.check_main("/ledger"), 0)
        self.assertEqual(self._snapshot(), before)

    def test_drift_exits_nonzero_lists_the_path_and_writes_nothing(self):
        self._put("stale")
        before = self._snapshot()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(sync.check_main("/ledger"), 1)
        self.assertIn("~ out.json", out.getvalue())
        self.assertEqual(self._snapshot(), before)               # nothing rewritten (content and mtime)
        with open(os.path.join(self.tmp, "out.json")) as fh:
            self.assertEqual(fh.read(), "stale")

    def test_a_new_output_counts_as_drift(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(sync.check_main("/ledger"), 1)
        self.assertIn("+ out.json", out.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "out.json")))

    def test_a_failing_publish_is_not_reported_as_clean(self):
        self._put("v1")
        os.environ["FAKE_EXIT"] = "3"
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(sync.check_main("/ledger"), 3)

    def test_worktrees_are_not_part_of_the_comparison(self):
        self._put("v1")
        self.assertNotIn(".worktrees/other/x.html", sync._tree_files(self.tmp))


if __name__ == "__main__":
    unittest.main()
