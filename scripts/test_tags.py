#!/usr/bin/env python3
"""The Tag: block ids, the append-only id index and the static resolver.

    python scripts/test_tags.py

Three layers: the tree arithmetic against vectors produced by the ledger's own
implementation; the index and the pages on synthetic ledgers; and the committed output
checked against itself (every id's bytes hash to the id, the current ids make the
published root, every audit path ends in it).
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import build_chrome  # noqa: E402
import tags  # noqa: E402

# Vectors produced by the ledger's ledger_core (leaf data b"leaf-<i>"), the implementation the root
# on Base comes from: the site must compute the same tree.
LEAVES = [tags.leaf_hash(b"leaf-%d" % i, b"") for i in range(9)]
ROOTS = {
    0: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    1: "305df59f9590c3c9ac63d2b2743c388e3792449078cebf7fb3dbe6471643b2b7",
    2: "60a53eed0de87a90c8e59427c59c46253c33a76a09502a51801300927b7e6bdc",
    3: "cf763a041c81ceef1578a6083f75c61bef2e0014f2a3e683a97fcfca5be7f19a",
    4: "bdd1c5ff55b19cb6b0e7c761bf9a6ccaa27fbbfc07b74f1fabb6e911a0bd2ab3",
    5: "00d21829a5503145348abcf712513eacf2a274211ad83e970202bb5b6d80b286",
    6: "160cf1a616e8792f9078a9665cb06520d95a33f467d0826f2310219d31383d73",
    7: "0b007fb915eb9b2a146f54b1c86ec53b664f8e455b7660b0b6ee13edc0d921c0",
    8: "ca6b7b3e674ac86c1027b59c87c064fc3bc27b313294c75f83bd05fdd13f0dcf",
    9: "1374d3a5ecbef4cd7c109e5d0127955f4ef014756496d70a0f99f65aa0ac8a30",
}
PATH_7_3 = ["fca89f57c9f8c8eb4047a7ff9d333acf9e0f3384b20b255bceab0f216dcca267",
            "60a53eed0de87a90c8e59427c59c46253c33a76a09502a51801300927b7e6bdc",
            "8eae6bd3b3a07f1f75ee72a531629e6eb31e42e62f760e47de52a53c3641ef23"]

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
TX = "0x" + "ab" * 32


def read(path, mode="rb"):
    with open(path, mode) as fh:
        return fh.read()


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts, self.handlers = [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.scripts.append(attrs)
        self.handlers += [a for a in attrs if a.startswith("on")]


class TreeTests(unittest.TestCase):

    def test_roots_match_the_ledgers_implementation(self):
        self.assertEqual(LEAVES[0].hex(), "305df59f9590c3c9ac63d2b2743c388e3792449078cebf7fb3dbe6471643b2b7")
        for n, root in ROOTS.items():
            self.assertEqual(tags.tree_hash(LEAVES[:n]).hex(), root, "tree of %d" % n)

    def test_audit_paths_match_and_verify_for_every_leaf_of_every_size(self):
        self.assertEqual([p.hex() for p in tags.inclusion_path(LEAVES[:7], 3)], PATH_7_3)
        for n in range(1, 10):
            root = tags.tree_hash(LEAVES[:n])
            for i in range(n):
                path = tags.inclusion_path(LEAVES[:n], i)
                self.assertEqual(tags.root_from_path(LEAVES[i], i, n, path), root, (n, i))
        # a path for another position, or for another leaf, does not end in the root
        path = tags.inclusion_path(LEAVES[:7], 3)
        self.assertNotEqual(tags.root_from_path(LEAVES[4], 3, 7, path), tags.tree_hash(LEAVES[:7]))
        with self.assertRaises(ValueError):
            tags.root_from_path(LEAVES[3], 3, 7, path[:-1])

    def test_the_leaf_is_one_hash_over_the_two_files(self):
        d, i = b'{"slot_id":"w1-b0001"}\n', b"RIFF....WEBP"
        self.assertEqual(tags.leaf_hash(d, i), hashlib.sha256(b"\x00" + d + i).digest())
        self.assertNotEqual(tags.leaf_hash(d, i), tags.leaf_hash(i, d))


def fake_ledger(td, slots):
    """A ledger tree with ledger/w1/<slot>.json|.webp for {slot_id: (dossier, icon)}."""
    d = os.path.join(td, "ledger", "w1")
    os.makedirs(d, exist_ok=True)
    for name in os.listdir(d):
        os.remove(os.path.join(d, name))
    for slot_id, (dossier, icon) in slots.items():
        with open(os.path.join(d, slot_id + ".json"), "wb") as fh:
            fh.write(dossier)
        with open(os.path.join(d, slot_id + ".webp"), "wb") as fh:
            fh.write(icon)
    return td


SLOTS = {"w1-b0001": ('{"moniker": "Ezra — Ära"}\n'.encode("utf-8"), b"icon-one"),
         "w1-b0002": (b'{"moniker": "Second"}\n', b"icon-two"),
         "w1-b0007": (b'{"moniker": "Seventh"}\n', b"icon-seven")}


class IndexTests(unittest.TestCase):

    def heads(self, slots=SLOTS):
        td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, td, True)
        fake_ledger(td, slots)
        return tags.heads_of(tags.read_heads(td))

    def root(self, heads):
        return tags.tree_hash([bytes.fromhex(h["block_id"]) for h in heads]).hex()

    def test_the_first_run_records_every_slot_as_current(self):
        heads = self.heads()
        index = tags.update_index(None, heads, self.root(heads), COMMIT_A, "2026-10-12T20:00:00Z")
        self.assertEqual([e["slot_id"] for e in index["ids"]], list(SLOTS))
        self.assertTrue(all(e["status"] == "current" and e["superseded_by"] is None for e in index["ids"]))
        self.assertEqual(index["ids"][0]["first_seen_ledger_commit"], COMMIT_A)
        self.assertEqual(index["tree_size"], 3)
        # the id is exactly sha256(0x00 || dossier || icon), and non-ASCII text is hashed as committed
        d, i = SLOTS["w1-b0001"]
        self.assertEqual(index["ids"][0]["block_id"], hashlib.sha256(b"\x00" + d + i).hexdigest())

    def test_an_updated_block_gets_a_new_id_and_the_old_one_stays(self):
        h1 = self.heads()
        first = tags.update_index(None, h1, self.root(h1), COMMIT_A, "2026-10-12T20:00:00Z")
        changed = dict(SLOTS, **{"w1-b0002": (b'{"moniker": "Second, updated"}\n', b"icon-two")})
        h2 = self.heads(changed)
        second = tags.update_index(first, h2, self.root(h2), COMMIT_B, "2026-10-13T08:00:00Z")
        ids2 = [e for e in second["ids"] if e["slot_id"] == "w1-b0002"]
        self.assertEqual([e["status"] for e in ids2], ["superseded", "current"])
        old, new = ids2
        self.assertEqual(old["superseded_by"], new["block_id"])
        # what was recorded stays as it was: the old id, and when and from what it was first seen
        self.assertEqual(old["block_id"], [e for e in first["ids"] if e["slot_id"] == "w1-b0002"][0]["block_id"])
        self.assertEqual(old["first_seen_ledger_commit"], COMMIT_A)
        self.assertEqual(new["first_seen_ledger_commit"], COMMIT_B)
        # the other slots are untouched
        self.assertEqual([e for e in second["ids"] if e["slot_id"] != "w1-b0002"],
                         [e for e in first["ids"] if e["slot_id"] != "w1-b0002"])

    def test_a_revert_to_earlier_bytes_makes_that_id_current_again(self):
        h1 = self.heads()
        first = tags.update_index(None, h1, self.root(h1), COMMIT_A, "t1")
        h2 = self.heads(dict(SLOTS, **{"w1-b0002": (b"{}\n", b"x")}))
        second = tags.update_index(first, h2, self.root(h2), COMMIT_B, "t2")
        third = tags.update_index(second, h1, self.root(h1), COMMIT_B, "t3")
        ids = [e for e in third["ids"] if e["slot_id"] == "w1-b0002"]
        self.assertEqual(len(ids), 2)                       # no third entry: the old id is the same id
        self.assertEqual({e["block_id"]: e["status"] for e in ids},
                         {ids[0]["block_id"]: "current", ids[1]["block_id"]: "superseded"})
        self.assertEqual(ids[0]["first_seen_ledger_commit"], COMMIT_A)

    def test_the_index_only_grows_and_is_deterministic(self):
        h = self.heads()
        a = tags.update_index(None, h, self.root(h), COMMIT_A, "t")
        self.assertEqual(tags.update_index(a, h, self.root(h), COMMIT_B, "later"), a)     # same ledger: nothing changes
        self.assertEqual(tags.update_index(json.loads(json.dumps(a)), h, self.root(h), COMMIT_B, "later"), a)
        fewer = self.heads({k: v for k, v in SLOTS.items() if k != "w1-b0007"})
        with self.assertRaisesRegex(ValueError, "refusing to drop"):
            tags.update_index(a, fewer, self.root(fewer), COMMIT_B, "t")
        # an id cannot be recorded for two slots
        clash = json.loads(json.dumps(a))
        clash["ids"][0]["block_id"] = a["ids"][1]["block_id"]
        with self.assertRaisesRegex(ValueError, "is recorded for"):
            tags.update_index(clash, h, self.root(h), COMMIT_B, "t")

    def test_only_real_slot_files_are_read(self):
        td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, td, True)
        fake_ledger(td, SLOTS)
        d = os.path.join(td, "ledger", "w1")
        for stray in ("notes.json", "w1-b00001.json", "..%2f..json", "w1-b0003.json.bak", "W1-B0003.json"):
            with open(os.path.join(d, stray), "wb") as fh:
                fh.write(b"{}")
        self.assertEqual([s for s, _, _ in tags.read_heads(td)], list(SLOTS))


class PublishTests(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.td, True)
        self.ledger = os.path.join(self.td, "ledger-clone")
        self.web = os.path.join(self.td, "web")
        os.makedirs(self.web)
        self.header, self.footer = build_chrome.block("header"), build_chrome.block("footer")

    def run_publish(self, slots, commit=COMMIT_A, at="2026-10-12T20:00:00Z", root=None, anchors=None):
        fake_ledger(self.ledger, slots)
        files = tags.read_heads(self.ledger)
        if root is None:
            root = tags.tree_hash([tags.leaf_hash(d, i) for _, d, i in files]).hex()
        if anchors is None:
            anchors = [{"chain_id": 8453, "root": root, "tx_hash": TX, "block_number": 123, "anchored_at": "2026-10-12T19:00:00Z"}]
        return tags.publish(self.web, files, anchors, root, commit, at, "default-src 'self'", self.header, self.footer)

    def tree(self):
        out = {}
        for dirpath, _, names in os.walk(self.web):
            for n in names:
                p = os.path.join(dirpath, n)
                out[os.path.relpath(p, self.web)] = read(p)
        return out

    def test_every_id_gets_its_bytes_a_record_and_two_pages(self):
        index = self.run_publish(SLOTS)
        for e in index["ids"]:
            i = e["block_id"]
            dossier, icon = SLOTS[e["slot_id"]]
            self.assertEqual(read(os.path.join(self.web, "id", i, "dossier.json")), dossier)       # exact bytes
            self.assertEqual(read(os.path.join(self.web, "id", i, "icon.webp")), icon)
            rec = json.loads(read(os.path.join(self.web, "id", i + ".json"), "r"))
            self.assertEqual((rec["block_id"], rec["slot_id"], rec["status"]), (i, e["slot_id"], "current"))
            self.assertEqual(rec["files"]["dossier"], "https://thefirstwall.ai/id/%s/dossier.json" % i)
            self.assertEqual(rec["anchor"]["tx_hash"], TX)
            p = rec["proof"]
            root = tags.root_from_path(bytes.fromhex(i), p["leaf_index"], p["tree_size"], [bytes.fromhex(x) for x in p["path"]])
            self.assertEqual(root.hex(), p["root"])
            for page in (os.path.join("id", i, "index.html"), os.path.join("b", e["slot_id"], "index.html")):
                html = read(os.path.join(self.web, page), "r")
                self.assertIn("SITE:HEADER:START", html)       # the shared chrome, so build_chrome --check accepts it
                self.assertIn("SITE:FOOTER:START", html)
                self.assertIn(i, html)
        self.assertEqual(json.loads(read(os.path.join(self.web, "ids.json"), "r")), index)

    def test_the_machine_record_says_how_to_check_it_without_us(self):
        index = self.run_publish(SLOTS)
        rec = json.loads(read(os.path.join(self.web, "id", index["ids"][0]["block_id"] + ".json"), "r"))
        recipe = "\n".join(rec["verify"])
        self.assertIn("ID=" + rec["block_id"], recipe)
        self.assertIn("shasum -a 256", recipe)
        self.assertIn("(printf '\\x00'; curl -s", recipe)

    def test_a_second_run_changes_nothing(self):
        self.run_publish(SLOTS)
        before = self.tree()
        self.run_publish(SLOTS, commit=COMMIT_B, at="2026-10-13T00:00:00Z")
        self.assertEqual(self.tree(), before)

    def test_a_superseded_id_still_resolves(self):
        first = self.run_publish(SLOTS)
        old = [e for e in first["ids"] if e["slot_id"] == "w1-b0002"][0]["block_id"]
        updated = dict(SLOTS, **{"w1-b0002": (b'{"moniker": "Second, updated"}\n', b"icon-two")})
        second = self.run_publish(updated, commit=COMMIT_B, at="2026-10-13T00:00:00Z")
        new = [e for e in second["ids"] if e["slot_id"] == "w1-b0002" and e["status"] == "current"][0]["block_id"]
        self.assertNotEqual(old, new)
        # the old bytes are kept, and the old id still checks out against them
        d = read(os.path.join(self.web, "id", old, "dossier.json"))
        i = read(os.path.join(self.web, "id", old, "icon.webp"))
        self.assertEqual(tags.leaf_hash(d, i).hex(), old)
        rec = json.loads(read(os.path.join(self.web, "id", old + ".json"), "r"))
        self.assertEqual((rec["status"], rec["superseded_by"], rec["proof"], rec["anchor"]), ("superseded", new, None, None))
        page = read(os.path.join(self.web, "id", old, "index.html"), "r")
        self.assertIn("superseded", page)
        self.assertIn('href="/id/%s/"' % new, page)
        slot_page = read(os.path.join(self.web, "b", "w1-b0002", "index.html"), "r")
        self.assertIn(old[:12], slot_page)
        self.assertIn(new[:12], slot_page)
        self.assertIn(COMMIT_A[:10], slot_page)
        self.assertIn(COMMIT_B[:10], slot_page)

    def test_the_handle_page_names_the_agent_and_defers_to_the_block_page(self):
        # P-21/P-30: one indexed URL per slot. The resolver names the agent, is not indexed, and its
        # canonical is the block page (trailing slash, so no 301).
        self.run_publish(SLOTS)
        page = read(os.path.join(self.web, "b", "w1-b0002", "index.html"), "r")
        self.assertIn("<title>Second — Tag of w1-b0002 — The First Wall</title>", page)
        self.assertIn('<meta name="robots" content="noindex, follow">', page)
        self.assertIn('<link rel="canonical" href="https://thefirstwall.ai/w1/b0002/">', page)
        self.assertIn('<meta property="og:url" content="https://thefirstwall.ai/w1/b0002/">', page)
        self.assertNotIn('href="/w1/b0002"', page)                      # slash form only
        self.assertNotIn("<title>Tag of", page)

    def test_it_refuses_a_root_the_files_do_not_make(self):
        with self.assertRaisesRegex(ValueError, "refusing to publish ids"):
            self.run_publish(SLOTS, root="00" * 32)
        self.assertEqual(self.tree(), {})                    # nothing was written

    def test_no_script_runs_from_a_tag_page_and_an_unanchored_root_is_said_so(self):
        index = self.run_publish(SLOTS, anchors=[])
        for e in index["ids"]:
            for page in (os.path.join("id", e["block_id"], "index.html"), os.path.join("b", e["slot_id"], "index.html")):
                p = _Scripts()
                p.feed(read(os.path.join(self.web, page), "r"))
                self.assertEqual(p.handlers, [])
                self.assertTrue(all(s.get("src", "").startswith("/") and not s.get("type") for s in p.scripts), p.scripts)
        self.assertIn("not anchored on Base yet", read(os.path.join(self.web, "b", "w1-b0001", "index.html"), "r"))


class CommittedOutputTests(unittest.TestCase):
    """The repository's own ids.json, id/ and records/, checked against themselves and state.json."""

    def setUp(self):
        path = os.path.join(ROOT, "ids.json")
        if not os.path.exists(path):
            self.skipTest("no ids.json committed yet (the first sync writes it)")
        self.index = json.loads(read(path, "r"))
        self.state = json.loads(read(os.path.join(ROOT, "state.json"), "r"))

    def test_every_id_hashes_from_its_own_bytes(self):
        for e in self.index["ids"]:
            d = read(os.path.join(ROOT, "id", e["block_id"], "dossier.json"))
            i = read(os.path.join(ROOT, "id", e["block_id"], "icon.webp"))
            self.assertEqual(tags.leaf_hash(d, i).hex(), e["block_id"], e["slot_id"])

    def test_the_current_ids_make_the_published_root(self):
        current = [bytes.fromhex(e["block_id"]) for e in self.index["ids"] if e["status"] == "current"]
        self.assertEqual(len(current), self.index["tree_size"])
        self.assertEqual(tags.tree_hash(current).hex(), self.index["root"])
        self.assertEqual(self.index["root"], self.state["merkle_root"])

    def test_every_audit_path_ends_in_the_root_and_the_mirror_is_byte_exact(self):
        for e in self.index["ids"]:
            rec = json.loads(read(os.path.join(ROOT, "id", e["block_id"] + ".json"), "r"))
            self.assertEqual((rec["status"], rec["slot_id"]), (e["status"], e["slot_id"]))
            if e["status"] != "current":
                continue
            p = rec["proof"]
            root = tags.root_from_path(bytes.fromhex(e["block_id"]), p["leaf_index"], p["tree_size"],
                                       [bytes.fromhex(x) for x in p["path"]])
            self.assertEqual(root.hex(), self.index["root"], e["slot_id"])
            # the mirrored record and icon are the committed bytes: a hash over them is the id
            d = read(os.path.join(ROOT, "records", e["slot_id"] + ".json"))
            i = read(os.path.join(ROOT, "assets", e["slot_id"] + ".webp"))
            self.assertEqual(tags.leaf_hash(d, i).hex(), e["block_id"], e["slot_id"])


if __name__ == "__main__":
    unittest.main()
