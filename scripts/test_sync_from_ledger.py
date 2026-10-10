#!/usr/bin/env python3
"""Regression tests for the publisher: escaping, validation, the anchor check.

    python scripts/test_sync_from_ledger.py
"""
import importlib.util
import json
import os
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
        self.scripts, self.handlers, self.images = [], [], 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.scripts.append({"type": attrs.get("type")})
        if tag == "img":
            self.images += 1
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
        self.assertEqual(tags.scripts, [{"type": "application/ld+json"}])   # only the JSON-LD data block
        self.assertEqual(tags.handlers, [])                               # no on* attribute anywhere
        self.assertEqual(tags.images, 1)                                  # only the hero icon
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
        self.assertNotIn("evil.example", page)

    def test_feed_escapes_title_and_cdata(self):
        feed = sync.build_feed([dossier(moniker="A & B <x>", manifesto="end]]><script>")], {"last_updated": ""})
        self.assertIn("<title>Slot #0042 — A &amp; B &lt;x&gt;</title>", feed)
        self.assertNotIn("end]]><script>", feed)

    def test_dispatch_card_escapes(self):
        card = sync._dispatch_card(dossier(moniker=HOSTILE, creature=HOSTILE, manifesto=HOSTILE), "en")
        self.assertNotIn("<script>alert", card)
        self.assertIn("basescan.org/tx/" + TX, card)


class AnchorCheckTests(unittest.TestCase):
    def _rpc(self, tx=None, receipt=None, fail=False):
        def rpc(method, params):
            if fail:
                raise RuntimeError("down")
            return {"eth_getTransactionByHash": tx, "eth_getTransactionReceipt": receipt}[method]
        return rpc

    def _good(self):
        signer = sync.ANCHOR_SIGNER
        return (
            {"from": signer, "to": signer.lower(), "input": "0x" + (sync.ANCHOR_MAGIC + ROOT).encode().hex()},
            {"status": "0x1", "blockNumber": hex(100)},
        )

    def test_verified(self):
        tx, rc = self._good()
        entry = {"root": ROOT, "tx_hash": TX, "block_number": 100}
        self.assertEqual(sync.check_anchor_on_chain(entry, self._rpc(tx, rc))[0], "verified")

    def test_wrong_signer_payload_or_block_fails(self):
        tx, rc = self._good()
        entry = {"root": ROOT, "tx_hash": TX, "block_number": 100}
        self.assertEqual(sync.check_anchor_on_chain(entry, self._rpc(dict(tx, **{"from": WALLET}), rc))[0], "failed")
        self.assertEqual(sync.check_anchor_on_chain(dict(entry, root="22" * 32), self._rpc(tx, rc))[0], "failed")
        self.assertEqual(sync.check_anchor_on_chain(dict(entry, block_number=99), self._rpc(tx, rc))[0], "failed")
        self.assertEqual(sync.check_anchor_on_chain(entry, self._rpc(tx, dict(rc, status="0x0")))[0], "failed")

    def test_rpc_down_or_unknown_tx_is_unchecked(self):
        entry = {"root": ROOT, "tx_hash": TX, "block_number": 100}
        self.assertEqual(sync.check_anchor_on_chain(entry, self._rpc(fail=True))[0], "unchecked")
        self.assertEqual(sync.check_anchor_on_chain(entry, self._rpc(None, None))[0], "unchecked")

    def test_status_refuses_contradicted_record_and_keeps_proven_one(self):
        tx, rc = self._good()
        entry = {"root": ROOT, "tx_hash": TX, "block_number": 100, "anchored_at": "", "signer": sync.ANCHOR_SIGNER}
        with self.assertRaises(SystemExit):
            sync.build_anchor_status({"merkle_root": ROOT}, [dict(entry, root="22" * 32)], rpc=self._rpc(tx, rc))
        status = sync.build_anchor_status({"merkle_root": ROOT}, [entry], rpc=self._rpc(tx, rc))
        self.assertEqual((status["on_chain"], status["anchored"]), ("verified", True))
        again = sync.build_anchor_status({"merkle_root": ROOT}, [entry], previous=status, rpc=self._rpc(fail=True))
        self.assertEqual(again["on_chain"], "verified")           # the chain does not forget
        moved = sync.build_anchor_status({"merkle_root": "33" * 32}, [entry], rpc=self._rpc(tx, rc))
        self.assertEqual((moved["on_chain"], moved["anchored"]), ("verified", False))
        self.assertNotIn("checked_at", status)                     # no wall-clock churn


if __name__ == "__main__":
    unittest.main()
