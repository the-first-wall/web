#!/usr/bin/env python3
"""
The First Wall — register the agent on the ERC-8004 Identity Registry (Base mainnet)
====================================================================================

    python scripts/register_agent.py                       # 1st run: creates agent-owner.key, prints the address to fund
    python scripts/register_agent.py                       # 2nd run, once funded: registers, prints the agentId
    python scripts/register_agent.py --key-file my.key     # use a key you already have (hex, one line)
    python scripts/register_agent.py --dry-run --from 0x…  # simulate only: no key, nothing sent

Needs Python 3.9+ and `pip install eth-account`. It talks to one JSON-RPC endpoint
(https://mainnet.base.org, or RPC_URL) and sends exactly one transaction:

    registry  0x8004A169FB4a3325136EB29fA0ceB6D2e539a432   (Base mainnet, chain 8453)
    call      register(string agentURI)                    value 0
    agentURI  https://thefirstwall.ai/.well-known/agent-registration.json   (--uri to change)

The wallet that sends it owns the agent (an ERC-721 token; its id is the agentId). Use a
wallet you control and can back up: the first run makes a fresh one for you and keeps its
key in ./agent-owner.key (mode 0600, never printed, never sent anywhere). The owner can
later transfer the agent to a safer wallet.

What it does, in order, and refuses at the first thing that is wrong:
  1. the RPC answers for chain 8453 and the registry is the contract named `AgentIdentity`;
  2. the registration file at agentURI is reachable and is JSON naming this registry type;
  3. the call simulates from the owner (prints the agentId it would get) and the gas is estimated;
  4. the fee fields are set explicitly (max priority fee from the node, max fee = 2 x base fee + priority),
     and the owner can pay them: otherwise it says how much ETH to send and stops;
  5. it signs, sends, waits for the receipt, reads the agentId from the `Registered` event and
     checks ownerOf(agentId) and tokenURI(agentId) on chain.

It ends by printing the `registrations` entry for .well-known/agent-registration.json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request

CHAIN_ID = 8453
REGISTRY = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"
REGISTRY_NAME = "AgentIdentity"
DEFAULT_URI = "https://thefirstwall.ai/.well-known/agent-registration.json"
DEFAULT_RPC = "https://mainnet.base.org"
DEFAULT_KEY_FILE = "agent-owner.key"
REGISTRATION_TYPE = "https://eips.ethereum.org/EIPS/eip-8004#registration-v1"

SEL_REGISTER = "f2c298be"      # register(string)
SEL_NAME = "06fdde03"          # name()
SEL_OWNER_OF = "6352211e"      # ownerOf(uint256)
SEL_TOKEN_URI = "c87b56dd"     # tokenURI(uint256)
# event Registered(uint256 indexed agentId, string agentURI, address indexed owner)
TOPIC_REGISTERED = "0xca52e62c367d81bb2e328eb795f7c7ba24afb478408a26c0e201d155c449bc4a"
TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
GAS_MARGIN = 1.25
RE_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
RE_KEY = re.compile(r"(0x)?[0-9a-fA-F]{64}")


class Refusal(Exception):
    """A check failed; nothing was sent."""


# --- ABI helpers (just what this one call needs) ------------------------------------

def encode_register(uri: str) -> str:
    """Calldata for register(string): selector, offset 0x20, length, the bytes padded to 32."""
    raw = uri.encode("utf-8")
    pad = b"\x00" * ((32 - len(raw) % 32) % 32)
    return "0x" + SEL_REGISTER + (32).to_bytes(32, "big").hex() + len(raw).to_bytes(32, "big").hex() + (raw + pad).hex()


def encode_uint(n: int) -> str:
    return n.to_bytes(32, "big").hex()


def decode_string(result: str) -> str:
    data = bytes.fromhex(result[2:])
    offset = int.from_bytes(data[0:32], "big")
    length = int.from_bytes(data[offset:offset + 32], "big")
    return data[offset + 32:offset + 32 + length].decode("utf-8")


def decode_address(result: str) -> str:
    return "0x" + result[-40:]


def agent_id_from_receipt(receipt: dict, owner: str) -> int:
    """The agentId the registry minted for `owner` in this receipt: the Registered event, or
    (as a cross-check) the ERC-721 Transfer from the zero address."""
    found = []
    for log in receipt.get("logs", []):
        if str(log.get("address", "")).lower() != REGISTRY.lower():
            continue
        topics = [t.lower() for t in log.get("topics", [])]
        if len(topics) >= 3 and topics[0] == TOPIC_REGISTERED and topics[2][-40:] == owner.lower()[2:]:
            found.append(int(topics[1], 16))
        elif len(topics) >= 4 and topics[0] == TOPIC_TRANSFER and int(topics[1], 16) == 0 and topics[2][-40:] == owner.lower()[2:]:
            found.append(int(topics[3], 16))
    if not found:
        raise Refusal("the receipt carries no Registered event for this owner; check the transaction on BaseScan")
    if len(set(found)) != 1:
        raise Refusal("the receipt names more than one agentId for this owner: %s" % sorted(set(found)))
    return found[0]


# --- JSON-RPC ----------------------------------------------------------------------

class Rpc:
    def __init__(self, url: str):
        self.url, self.n = url, 0

    def __call__(self, method: str, params: list):
        self.n += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}).encode()
        req = urllib.request.Request(self.url, data=body, headers={"content-type": "application/json",
                                                                    "user-agent": "thefirstwall-register-agent/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            out = json.loads(resp.read().decode())
        if out.get("error"):
            raise Refusal("%s failed: %s" % (method, out["error"].get("message", out["error"])))
        return out.get("result")


def eth_call(rpc, to: str, data: str, sender=None):
    call = {"to": to, "data": data}
    if sender:
        call["from"] = sender
    return rpc("eth_call", [call, "latest"])


# --- The checks and the call ----------------------------------------------------------

def check_chain_and_registry(rpc) -> None:
    chain = int(rpc("eth_chainId", []), 16)
    if chain != CHAIN_ID:
        raise Refusal("this RPC is chain %d, not Base mainnet (%d)" % (chain, CHAIN_ID))
    name = decode_string(eth_call(rpc, REGISTRY, "0x" + SEL_NAME))
    if name != REGISTRY_NAME:
        raise Refusal("the contract at %s is named %r, not %r; refusing to send anything" % (REGISTRY, name, REGISTRY_NAME))


def check_registration_file(uri: str, fetch=None) -> None:
    """The agentURI must resolve to a registration file of the right type, or the registry would point at nothing."""
    fetch = fetch or (lambda u: urllib.request.urlopen(urllib.request.Request(
        u, headers={"user-agent": "thefirstwall-register-agent/1.0"}), timeout=30).read())
    try:
        doc = json.loads(fetch(uri))
    except Exception as e:  # noqa: BLE001
        raise Refusal("could not read the registration file at %s (%s)" % (uri, e))
    if doc.get("type") != REGISTRATION_TYPE:
        raise Refusal("%s is not an ERC-8004 registration file (type %r)" % (uri, doc.get("type")))


def plan_fees(rpc) -> dict:
    block = rpc("eth_getBlockByNumber", ["latest", False])
    base = int(block["baseFeePerGas"], 16)
    tip = max(int(rpc("eth_maxPriorityFeePerGas", []), 16), 1)
    return {"maxPriorityFeePerGas": tip, "maxFeePerGas": base * 2 + tip, "baseFee": base}


def simulate(rpc, owner: str, uri: str) -> tuple:
    """(agentId the call would mint, gas to send) — raises Refusal if the call would revert."""
    data = encode_register(uri)
    would_be = int(eth_call(rpc, REGISTRY, data, owner), 16)
    gas = int(rpc("eth_estimateGas", [{"from": owner, "to": REGISTRY, "data": data}]), 16)
    return would_be, int(gas * GAS_MARGIN)


def build_tx(owner: str, uri: str, nonce: int, gas: int, fees: dict) -> dict:
    return {"type": 2, "chainId": CHAIN_ID, "nonce": nonce, "to": REGISTRY, "value": 0, "gas": gas,
            "maxFeePerGas": fees["maxFeePerGas"], "maxPriorityFeePerGas": fees["maxPriorityFeePerGas"],
            "data": encode_register(uri)}


def wait_for_receipt(rpc, tx_hash: str, timeout: int = 180, sleep=time.sleep):
    deadline = time.time() + timeout
    while time.time() < deadline:
        receipt = rpc("eth_getTransactionReceipt", [tx_hash])
        if receipt:
            return receipt
        sleep(2)
    raise Refusal("no receipt after %d s; the transaction may still confirm: https://basescan.org/tx/%s" % (timeout, tx_hash))


def registrations_entry(agent_id: int) -> str:
    return json.dumps({"agentId": agent_id, "agentRegistry": "eip155:%d:%s" % (CHAIN_ID, REGISTRY)})


def register(rpc, account, uri: str, out=print, fetch=None, sleep=time.sleep) -> int:
    """Run the whole flow with `account` (an eth_account LocalAccount). Returns the agentId."""
    owner = account.address
    check_chain_and_registry(rpc)
    check_registration_file(uri, fetch)
    would_be, gas = simulate(rpc, owner, uri)
    fees = plan_fees(rpc)
    cost = gas * fees["maxFeePerGas"]
    balance = int(rpc("eth_getBalance", [owner, "latest"]), 16)
    out("owner     : %s" % owner)
    out("would mint: agentId %d (the registry's next id)" % would_be)
    out("fees      : max %.6f gwei, priority %.6f gwei, gas %d -> at most %.8f ETH"
        % (fees["maxFeePerGas"] / 1e9, fees["maxPriorityFeePerGas"] / 1e9, gas, cost / 1e18))
    if balance < cost:
        raise Refusal("the owner holds %.8f ETH on Base; it needs at least %.8f. Send about 0.0001 ETH on Base to %s and run this again."
                      % (balance / 1e18, cost / 1e18, owner))
    nonce = int(rpc("eth_getTransactionCount", [owner, "pending"]), 16)
    signed = account.sign_transaction(build_tx(owner, uri, nonce, gas, fees))
    raw = signed.raw_transaction if hasattr(signed, "raw_transaction") else signed.rawTransaction
    tx_hash = rpc("eth_sendRawTransaction", ["0x" + bytes(raw).hex()])
    out("sent      : https://basescan.org/tx/%s" % tx_hash)
    receipt = wait_for_receipt(rpc, tx_hash, sleep=sleep)
    if receipt.get("status") != "0x1":
        raise Refusal("the transaction reverted: https://basescan.org/tx/%s" % tx_hash)
    agent_id = agent_id_from_receipt(receipt, owner)
    on_chain_owner = decode_address(eth_call(rpc, REGISTRY, "0x" + SEL_OWNER_OF + encode_uint(agent_id)))
    on_chain_uri = decode_string(eth_call(rpc, REGISTRY, "0x" + SEL_TOKEN_URI + encode_uint(agent_id)))
    if on_chain_owner.lower() != owner.lower() or on_chain_uri != uri:
        raise Refusal("agent %d reads back as owner %s, uri %r; check it on BaseScan" % (agent_id, on_chain_owner, on_chain_uri))
    out("registered: agentId %d, owner %s, tokenURI %s" % (agent_id, owner, uri))
    out("")
    out('Add this to .well-known/agent-registration.json ("registrations": []):')
    out("  " + registrations_entry(agent_id))
    return agent_id


# --- The key file ----------------------------------------------------------------------

def load_key(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        key = fh.read().strip()
    if not RE_KEY.fullmatch(key):
        raise Refusal("%s does not hold a 32-byte hex private key" % path)
    return key if key.startswith("0x") else "0x" + key


def create_key_file(path: str, out=print):
    """A fresh owner key, written with mode 0600 and never printed; returns the account."""
    from eth_account import Account
    account = Account.create()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(account.key.hex() if account.key.hex().startswith("0x") else "0x" + account.key.hex())
        fh.write("\n")
    out("Created %s (mode 0600). BACK IT UP: whoever holds it owns the agent. It is never printed or sent anywhere." % path)
    return account


def main(argv=None, rpc=None, out=print) -> int:
    ap = argparse.ArgumentParser(description="Register the agent on the ERC-8004 Identity Registry (Base mainnet).")
    ap.add_argument("--uri", default=DEFAULT_URI)
    ap.add_argument("--key-file", default=DEFAULT_KEY_FILE)
    ap.add_argument("--dry-run", action="store_true", help="simulate only (needs --from; no key, nothing sent)")
    ap.add_argument("--from", dest="sender", help="address to simulate from (with --dry-run)")
    args = ap.parse_args(argv)
    rpc = rpc or Rpc(os.environ.get("RPC_URL", DEFAULT_RPC))
    try:
        if args.dry_run:
            if not args.sender or not RE_ADDRESS.fullmatch(args.sender):
                raise Refusal("--dry-run needs --from <0x address>")
            check_chain_and_registry(rpc)
            check_registration_file(args.uri)
            would_be, gas = simulate(rpc, args.sender, args.uri)
            fees = plan_fees(rpc)
            out("dry run from %s: the call succeeds and would mint agentId %d" % (args.sender, would_be))
            out("gas %d, max fee %.6f gwei -> at most %.8f ETH" % (gas, fees["maxFeePerGas"] / 1e9, gas * fees["maxFeePerGas"] / 1e18))
            return 0
        from eth_account import Account
        if not os.path.exists(args.key_file):
            account = create_key_file(args.key_file, out)
            out("")
            out("Owner address: %s" % account.address)
            out("Send about 0.0001 ETH on Base (not Ethereum mainnet) to that address, then run this command again.")
            return 0
        register(rpc, Account.from_key(load_key(args.key_file)), args.uri, out)
        return 0
    except Refusal as e:
        print("[x] " + str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
