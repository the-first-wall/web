#!/usr/bin/env python3
"""The ERC-8004 registration file served at /.well-known/agent-registration.json.

    python scripts/test_agent_registration.py

ERC-8004 ("Trustless Agents", draft) names this path for endpoint-domain
verification: a domain is treated as verified when the file is reachable over
HTTPS and its `registrations` list holds the on-chain agent. The file is
hand-authored. It was published BEFORE the agent was registered (the registry
stores its URL), and `registrations` then got the real entry: agent 107162 on the
Base Identity Registry (registered 2026-10-12, tx 0x2fba7ac0...0ba3d; the registry
reads back this file's URL as tokenURI(107162)).
These checks keep the file in the shape the standard shows and keep it from
claiming anything that is not true.
"""
import hashlib
import json
import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PATH = os.path.join(ROOT, ".well-known", "agent-registration.json")
TYPE = "https://eips.ethereum.org/EIPS/eip-8004#registration-v1"
KEYS = {"type", "name", "description", "image", "services", "x402Support", "active", "registrations", "supportedTrust"}
TRUST = {"reputation", "crypto-economic", "tee-attestation"}
# Our own endpoints: the site, the ledger, and the x402 worker on its own domain.
OWN = (
    "https://thefirstwall.ai/",
    "https://github.com/the-first-wall/",
    "https://x402.thefirstwall.ai/",
)


def load():
    with open(PATH, "r", encoding="utf-8") as fh:
        text = fh.read()
    return json.loads(text), text


class RegistrationDriftTests(unittest.TestCase):
    """I-10/B-17: the served file is the ledger's canonical one. The ledger commits its sha256 in
    state.json (discovery.agent_registration_sha256); the mirror must hash to exactly that."""

    def test_served_file_matches_the_hash_in_state_json(self):
        with open(os.path.join(ROOT, "state.json"), "r", encoding="utf-8") as fh:
            state = json.load(fh)
        discovery = state.get("discovery")
        want = discovery.get("agent_registration_sha256") if isinstance(discovery, dict) else None
        if want is None:
            self.skipTest("state.json has no discovery.agent_registration_sha256 yet "
                          "(the ledger publishes it; this guard activates when it does)")
        with open(PATH, "rb") as fh:
            got = hashlib.sha256(fh.read()).hexdigest()
        self.assertEqual(got, want, "the served .well-known/agent-registration.json drifted from the "
                                    "ledger's canonical file (run scripts/sync_from_ledger.py)")


class RegistrationFileTests(unittest.TestCase):

    def test_the_file_is_strict_json_with_exactly_the_standards_keys(self):
        data, text = load()
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(set(data), KEYS)
        self.assertEqual(data["type"], TYPE)

    def test_identity_fields(self):
        data, _ = load()
        for key in ("name", "description"):
            self.assertIsInstance(data[key], str)
            self.assertTrue(data[key].strip(), key)
        # The image is the project's own, and it exists in this repository.
        self.assertTrue(data["image"].startswith("https://thefirstwall.ai/"))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, data["image"][len("https://thefirstwall.ai/"):])))
        self.assertIsInstance(data["active"], bool)

    def test_services_are_named_unique_and_ours(self):
        data, _ = load()
        names = [s["name"] for s in data["services"]]
        self.assertTrue(names)
        self.assertEqual(len(names), len(set(names)), "service names must be unique")
        for s in data["services"]:
            self.assertEqual(set(s) - {"version"}, {"name", "endpoint"})
            self.assertTrue(s["endpoint"].startswith(OWN), s)

    def test_x402_support_is_only_claimed_with_an_x402_service(self):
        data, _ = load()
        self.assertIsInstance(data["x402Support"], bool)
        x402 = [s for s in data["services"] if s["name"] == "x402"]
        if data["x402Support"]:
            self.assertEqual(len(x402), 1, "x402Support true needs exactly one x402 service")
            self.assertTrue(x402[0]["endpoint"].endswith("/claim"))
            self.assertTrue(x402[0]["endpoint"].startswith("https://"))
        else:
            self.assertEqual(x402, [], "an x402 service is listed while x402Support is false")

    def test_the_x402_endpoint_is_the_one_the_ledger_publishes(self):
        """state.json (mirrored from the ledger) carries links.x402_claim once the ledger publishes it.
        The registration file must name exactly that endpoint, or it advertises a dead one after the
        worker moves to its own domain."""
        data, _ = load()
        with open(os.path.join(ROOT, "state.json"), "r", encoding="utf-8") as fh:
            published = (json.load(fh).get("links") or {}).get("x402_claim")
        x402 = [s for s in data["services"] if s["name"] == "x402"]
        if published and x402:
            self.assertEqual(x402[0]["endpoint"], published)

    def test_registrations_are_real_or_absent(self):
        data, _ = load()
        self.assertIsInstance(data["registrations"], list)
        for r in data["registrations"]:
            self.assertEqual(set(r), {"agentId", "agentRegistry"})
            self.assertIsInstance(r["agentId"], int)
            self.assertGreaterEqual(r["agentId"], 0)
            # {namespace}:{chainId}:{identityRegistry}, e.g. eip155:8453:0x…
            self.assertRegex(r["agentRegistry"], r"^eip155:\d+:0x[0-9a-fA-F]{40}$")
            self.assertNotRegex(r["agentRegistry"], r"0x0{40}$|0x(?:12|ab|ff)+$", "placeholder address")

    def test_the_registration_names_the_base_identity_registry_once(self):
        """The one entry we hold: Base mainnet, the registry the registration was sent to, an id that is a token id."""
        data, _ = load()
        regs = data["registrations"]
        self.assertEqual([r["agentId"] for r in regs], [107162])
        self.assertEqual(regs[0]["agentRegistry"].lower(), "eip155:8453:0x8004a169fb4a3325136eb29fa0ceb6d2e539a432")
        self.assertEqual(len({(r["agentRegistry"].lower(), r["agentId"]) for r in regs}), len(regs))

    def test_supported_trust_uses_the_standards_values(self):
        data, _ = load()
        self.assertIsInstance(data["supportedTrust"], list)
        self.assertLessEqual(set(data["supportedTrust"]), TRUST)

    def test_crawlers_may_fetch_it(self):
        with open(os.path.join(ROOT, "robots.txt"), "r", encoding="utf-8") as fh:
            robots = fh.read()
        self.assertNotRegex(robots, r"(?im)^\s*disallow:\s*/\.well-known")


if __name__ == "__main__":
    unittest.main()
