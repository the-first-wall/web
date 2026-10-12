#!/usr/bin/env python3
"""register_agent.py: the ERC-8004 registration, tested against a fake Base node.

    python scripts/test_register_agent.py        (needs: pip install eth-account)

Nothing here touches the network: a fake JSON-RPC node answers the calls, a real key signs,
and the signed transaction is decoded again to prove what would have been sent.
"""
import io
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import register_agent as ra  # noqa: E402

try:
    from eth_account import Account
    from eth_account.typed_transactions import TypedTransaction
    from hexbytes import HexBytes
except ImportError:                                   # pragma: no cover
    raise SystemExit("test_register_agent.py needs eth-account: pip install eth-account")

KEY = "0x" + "11" * 32
ACCOUNT = Account.from_key(KEY)
AGENT_ID = 107147
TX_HASH = "0x" + "ab" * 32
URI = ra.DEFAULT_URI
# What the registry's register(string) call is for DEFAULT_URI: selector, offset, length 59, the URL padded.
KNOWN_CALLDATA = (
    "0xf2c298be"
    "0000000000000000000000000000000000000000000000000000000000000020"
    "000000000000000000000000000000000000000000000000000000000000003b"
    "68747470733a2f2f746865666972737477616c6c2e61692f2e77656c6c2d6b6e6f776e2f6167656e742d726567697374726174696f6e2e6a736f6e0000000000"
)


def abi_string(text):
    raw = text.encode()
    return "0x" + (32).to_bytes(32, "big").hex() + len(raw).to_bytes(32, "big").hex() + (raw + b"\x00" * ((32 - len(raw) % 32) % 32)).hex()


def topic_addr(addr):
    return "0x" + "00" * 12 + addr.lower()[2:]


class FakeNode:
    """Answers the handful of JSON-RPC methods the script uses; records what was sent."""

    def __init__(self, chain=ra.CHAIN_ID, name="AgentIdentity", balance=10 ** 16, simulate_ok=True,
                 receipt_status="0x1", token_uri=URI, owner=None):
        self.chain, self.name, self.balance, self.simulate_ok = chain, name, balance, simulate_ok
        self.receipt_status, self.token_uri, self.owner = receipt_status, token_uri, owner or ACCOUNT.address
        self.sent = []

    def __call__(self, method, params):
        if method == "eth_chainId":
            return hex(self.chain)
        if method == "eth_call":
            call = params[0]
            data = call["data"]
            if data.startswith("0x" + ra.SEL_NAME):
                return abi_string(self.name)
            if data.startswith("0x" + ra.SEL_REGISTER):
                if not self.simulate_ok:
                    raise ra.Refusal("eth_call failed: execution reverted")
                return hex(AGENT_ID)
            if data.startswith("0x" + ra.SEL_OWNER_OF):
                return "0x" + "00" * 12 + self.owner.lower()[2:]
            if data.startswith("0x" + ra.SEL_TOKEN_URI):
                return abi_string(self.token_uri)
        if method == "eth_estimateGas":
            return hex(189193)
        if method == "eth_getBlockByNumber":
            return {"baseFeePerGas": hex(5_000_000)}
        if method == "eth_maxPriorityFeePerGas":
            return hex(1_000_000)
        if method == "eth_getBalance":
            return hex(self.balance)
        if method == "eth_getTransactionCount":
            return hex(7)
        if method == "eth_sendRawTransaction":
            self.sent.append(params[0])
            return TX_HASH
        if method == "eth_getTransactionReceipt":
            return {"status": self.receipt_status, "logs": [
                {"address": ra.REGISTRY, "topics": [ra.TOPIC_TRANSFER, topic_addr("0x" + "00" * 20), topic_addr(ACCOUNT.address), hex(AGENT_ID)]},
                {"address": ra.REGISTRY, "topics": [ra.TOPIC_REGISTERED, hex(AGENT_ID), topic_addr(ACCOUNT.address)], "data": "0x"},
            ]}
        raise AssertionError("unexpected RPC call %s" % method)


def run(node, account=ACCOUNT, uri=URI):
    out = []
    agent_id = ra.register(node, account, uri, out=out.append, fetch=lambda u: b'{"type": "%s"}' % ra.REGISTRATION_TYPE.encode(),
                           sleep=lambda s: None)
    return agent_id, "\n".join(out)


class EncodingTests(unittest.TestCase):

    def test_calldata_is_the_known_register_call(self):
        self.assertEqual(ra.encode_register(URI), KNOWN_CALLDATA)
        self.assertEqual(len(bytes.fromhex(KNOWN_CALLDATA[2:])), 4 + 32 + 32 + 64)      # URL is 59 bytes, padded to 64

    def test_strings_round_trip_including_non_ascii_and_empty(self):
        for text in ("AgentIdentity", "", "https://thefirstwall.ai/xä", "a" * 100):
            self.assertEqual(ra.decode_string(abi_string(text)), text)

    def test_the_agent_id_comes_from_the_receipt_for_this_owner_only(self):
        mint = {"address": ra.REGISTRY, "topics": [ra.TOPIC_REGISTERED, hex(5), topic_addr(ACCOUNT.address)]}
        other = {"address": ra.REGISTRY, "topics": [ra.TOPIC_REGISTERED, hex(6), topic_addr("0x" + "22" * 20)]}
        foreign = {"address": "0x" + "33" * 20, "topics": [ra.TOPIC_REGISTERED, hex(7), topic_addr(ACCOUNT.address)]}
        self.assertEqual(ra.agent_id_from_receipt({"logs": [other, foreign, mint]}, ACCOUNT.address), 5)
        with self.assertRaisesRegex(ra.Refusal, "no Registered event"):
            ra.agent_id_from_receipt({"logs": [other, foreign]}, ACCOUNT.address)
        two = {"address": ra.REGISTRY, "topics": [ra.TOPIC_REGISTERED, hex(9), topic_addr(ACCOUNT.address)]}
        with self.assertRaisesRegex(ra.Refusal, "more than one"):
            ra.agent_id_from_receipt({"logs": [mint, two]}, ACCOUNT.address)


class FlowTests(unittest.TestCase):

    def test_it_signs_the_exact_transaction_sends_it_once_and_reads_the_agent_back(self):
        node = FakeNode()
        agent_id, text = run(node)
        self.assertEqual(agent_id, AGENT_ID)
        self.assertEqual(len(node.sent), 1)
        raw = bytes.fromhex(node.sent[0][2:])
        self.assertEqual(Account.recover_transaction(raw), ACCOUNT.address)              # signed by the owner
        tx = TypedTransaction.from_bytes(HexBytes(raw)).transaction.dictionary
        self.assertEqual((tx["chainId"], tx["nonce"], tx["value"]), (ra.CHAIN_ID, 7, 0))
        to = tx["to"].hex() if isinstance(tx["to"], (bytes, bytearray)) else tx["to"][2:]
        self.assertEqual(to.lower().replace("0x", ""), ra.REGISTRY.lower()[2:])
        self.assertEqual("0x" + tx["data"].hex(), KNOWN_CALLDATA)
        # the fee fields are explicit and non-null: the failure that sent us here was a missing priority fee
        self.assertEqual(tx["maxPriorityFeePerGas"], 1_000_000)
        self.assertEqual(tx["maxFeePerGas"], 5_000_000 * 2 + 1_000_000)
        self.assertEqual(tx["gas"], int(189193 * ra.GAS_MARGIN))
        self.assertIn('{"agentId": %d, "agentRegistry": "eip155:8453:%s"}' % (AGENT_ID, ra.REGISTRY), text)
        self.assertIn("https://basescan.org/tx/" + TX_HASH, text)

    def test_it_stops_before_sending_when_the_owner_cannot_pay(self):
        node = FakeNode(balance=0)
        with self.assertRaisesRegex(ra.Refusal, r"Send about 0\.0001 ETH on Base to " + ACCOUNT.address):
            run(node)
        self.assertEqual(node.sent, [])

    def test_it_refuses_the_wrong_chain_the_wrong_contract_and_a_call_that_would_revert(self):
        for node, message in ((FakeNode(chain=1), "not Base mainnet"),
                              (FakeNode(name="SomethingElse"), "refusing to send anything"),
                              (FakeNode(simulate_ok=False), "execution reverted")):
            with self.assertRaisesRegex(ra.Refusal, message):
                run(node)
            self.assertEqual(node.sent, [], message)

    def test_it_refuses_a_uri_that_is_not_a_registration_file(self):
        node = FakeNode()
        with self.assertRaisesRegex(ra.Refusal, "not an ERC-8004 registration file"):
            ra.register(node, ACCOUNT, URI, out=lambda s: None, fetch=lambda u: b'{"type": "something else"}')
        with self.assertRaisesRegex(ra.Refusal, "could not read the registration file"):
            ra.register(node, ACCOUNT, URI, out=lambda s: None, fetch=lambda u: b"<html>")
        self.assertEqual(node.sent, [])

    def test_a_reverted_transaction_and_a_wrong_read_back_are_reported_not_hidden(self):
        with self.assertRaisesRegex(ra.Refusal, "reverted"):
            run(FakeNode(receipt_status="0x0"))
        with self.assertRaisesRegex(ra.Refusal, "reads back as owner"):
            run(FakeNode(owner="0x" + "44" * 20))
        with self.assertRaisesRegex(ra.Refusal, "reads back as owner"):
            run(FakeNode(token_uri="https://example.com/other.json"))


class KeyFileTests(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.path = os.path.join(self.td, "agent-owner.key")
        ra.check_registration_file = lambda uri, fetch=None: None        # no network in the tests

    def main(self, *argv, node=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = ra.main(list(argv), rpc=node or FakeNode(), out=lambda s: print(s))
        return code, out.getvalue(), err.getvalue()

    def test_the_first_run_creates_a_private_key_file_and_prints_only_the_address(self):
        code, out, err = self.main("--key-file", self.path)
        self.assertEqual(code, 0)
        mode = stat.S_IMODE(os.stat(self.path).st_mode)
        self.assertEqual(mode, 0o600)
        key = ra.load_key(self.path)
        address = Account.from_key(key).address
        self.assertIn(address, out)
        self.assertNotIn(key[2:], out + err)                              # the key itself is never printed
        self.assertIn("Send about 0.0001 ETH on Base", out)
        # a second creation never overwrites a key
        with self.assertRaises(FileExistsError):
            ra.create_key_file(self.path, out=lambda s: None)

    def test_the_second_run_registers_with_that_key(self):
        self.main("--key-file", self.path)
        key = ra.load_key(self.path)
        node = FakeNode(owner=Account.from_key(key).address)
        # the fake node's receipt names ACCOUNT; give it this key's address instead
        real = node.__call__
        owner = Account.from_key(key).address

        def patched(method, params):
            result = real(method, params)
            if method == "eth_getTransactionReceipt":
                for log in result["logs"]:
                    log["topics"] = [t.replace(topic_addr(ACCOUNT.address)[2:], topic_addr(owner)[2:]) for t in log["topics"]]
            return result
        code, out, err = self.main("--key-file", self.path, node=patched)
        self.assertEqual((code, err), (0, ""), out)
        self.assertIn("registered: agentId %d" % AGENT_ID, out)

    def test_a_garbage_key_file_and_a_dry_run_without_an_address_are_refused(self):
        with open(self.path, "w") as fh:
            fh.write("not a key\n")
        code, _, err = self.main("--key-file", self.path)
        self.assertEqual(code, 1)
        self.assertIn("32-byte hex private key", err)
        code, _, err = self.main("--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("--dry-run needs --from", err)

    def test_a_dry_run_needs_no_key_and_sends_nothing(self):
        node = FakeNode()
        code, out, err = self.main("--dry-run", "--from", ACCOUNT.address, node=node)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("would mint agentId %d" % AGENT_ID, out)
        self.assertEqual(node.sent, [])
        self.assertFalse(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main()
