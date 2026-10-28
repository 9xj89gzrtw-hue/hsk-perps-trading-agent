"""HSK Chain adapter: encoding, the deployment gate, and one live read.

The live test is opt-in (HSK_LIVE=1) so the suite stays hermetic; run it once before
a submission to prove the chain ids below were read, not remembered.
"""

from __future__ import annotations

import os
import unittest
from decimal import Decimal

from agent import hsk
from agent.hsk import (
    HSK_MAINNET,
    HSK_TESTNET,
    Rpc,
    RpcError,
    decode_bool,
    decode_signal,
    deployment_gate,
    enc_address,
    enc_bytes32,
    enc_int,
    enc_uint,
    encode_is_actionable,
    encode_publish,
    encode_revoke,
    signal_key,
)


class ChainConfigTest(unittest.TestCase):
    def test_mainnet_is_177(self):
        self.assertEqual(HSK_MAINNET.chain_id, 177)

    def test_testnet_is_133(self):
        self.assertEqual(HSK_TESTNET.chain_id, 133)

    def test_registry(self):
        self.assertIs(hsk.CHAINS[177], HSK_MAINNET)
        self.assertIs(hsk.CHAINS[133], HSK_TESTNET)

    def test_rpc_urls_are_https(self):
        for chain in (HSK_MAINNET, HSK_TESTNET):
            self.assertTrue(chain.rpc.startswith("https://"), chain.rpc)

    def test_env_selects_by_id(self):
        old = os.environ.get("HSK_CHAIN")
        try:
            os.environ["HSK_CHAIN"] = "133"
            self.assertIs(hsk.chain_from_env(), HSK_TESTNET)
            os.environ["HSK_CHAIN"] = "mainnet"
            self.assertIs(hsk.chain_from_env(), HSK_MAINNET)
            os.environ["HSK_CHAIN"] = "TestNet"
            self.assertIs(hsk.chain_from_env(), HSK_TESTNET)
        finally:
            if old is None:
                os.environ.pop("HSK_CHAIN", None)
            else:
                os.environ["HSK_CHAIN"] = old

    def test_env_unknown_is_an_error_not_a_fallback(self):
        old = os.environ.get("HSK_CHAIN")
        try:
            os.environ["HSK_CHAIN"] = "1"
            with self.assertRaises(ValueError):
                hsk.chain_from_env()
        finally:
            if old is None:
                os.environ.pop("HSK_CHAIN", None)
            else:
                os.environ["HSK_CHAIN"] = old


class EncodingTest(unittest.TestCase):
    def test_uint_word(self):
        self.assertEqual(enc_uint(1).hex(), "00" * 31 + "01")
        self.assertEqual(len(enc_uint(2**256 - 1)), 32)

    def test_uint_rejects_negative(self):
        with self.assertRaises(ValueError):
            enc_uint(-1)

    def test_int_two_complement(self):
        self.assertEqual(enc_int(0), b"\x00" * 32)
        self.assertEqual(enc_int(1), b"\x00" * 31 + b"\x01")
        # -1 is all ones in two's complement, and it is what an ABI int256 looks like.
        self.assertEqual(enc_int(-1), b"\xff" * 32)
        self.assertEqual(enc_int(-2), b"\xff" * 31 + b"\xfe")

    def test_int_roundtrip_through_decode(self):
        for value in (0, 1, -1, 2**200, -(2**200), 1560, -35):
            self.assertEqual(hsk.dec_int(enc_int(value).hex()), value)

    def test_address_encoding(self):
        word = enc_address("0x72Aa6edDE71B94f6b99c8e52EdC02B5C75F74d1a")
        self.assertEqual(len(word), 32)
        self.assertEqual(word[:12], b"\x00" * 12)
        self.assertEqual(
            word[12:].hex(), "72aa6edde71b94f6b99c8e52edc02b5c75f74d1a"
        )

    def test_address_rejects_wrong_length(self):
        with self.assertRaises(ValueError):
            enc_address("0x72Aa6edD")
        with self.assertRaises(ValueError):
            enc_address("0x" + "zz" * 20)

    def test_bytes32_is_strict(self):
        self.assertEqual(enc_bytes32(b"\x00" * 32), b"\x00" * 32)
        with self.assertRaises(ValueError):
            enc_bytes32(b"\x00" * 31)


class SignalTest(unittest.TestCase):
    def test_key_is_deterministic_and_32_bytes(self):
        a, b = signal_key("BTC-PERP:funding-short"), signal_key("BTC-PERP:funding-short")
        self.assertEqual(a, b)
        self.assertEqual(len(a), 32)
        self.assertNotEqual(a, signal_key("ETH-PERP:funding-short"))

    def test_publish_calldata_shape(self):
        key = signal_key("BTC-PERP")
        data = encode_publish(key, 1560, 1_793_200_000)
        self.assertTrue(data.startswith("0x"))
        body = data[2:]
        # selector + key word + edge word + expiry word
        self.assertEqual(len(body), 8 + 64 * 3)
        self.assertEqual(body[:8], hsk.PUBLISH_SELECTOR.hex())
        self.assertEqual(body[8:72], key.hex())
        self.assertEqual(int(body[72:136], 16), 1560)
        self.assertEqual(int(body[136:200], 16), 1_793_200_000)

    def test_publish_encodes_a_negative_edge(self):
        data = encode_publish(signal_key("ETH-PERP"), -420, 1_793_200_000)
        body = data[2:]
        # The edge is the second word; the first is the key. Sign-extended to 32
        # bytes, not rejected and not wrapped into a huge positive.
        self.assertEqual(body[72:136], hsk.enc_int(-420).hex())
        self.assertEqual(hsk.dec_int(body[72:136]), -420)

    def test_revoke_and_is_actionable_shapes(self):
        key = signal_key("BTC-PERP")
        self.assertEqual(len(encode_revoke(key)), 2 + 8 + 64)
        self.assertEqual(len(encode_is_actionable(key)), 2 + 8 + 64)

    def test_selectors_come_from_the_solidity_source(self):
        """The ABI the agent sends must be the ABI the contract declares.

        Read the .sol file, normalise each signature the way an ABI does (drop
        names, keep types), and check that the selector computed in Python is the
        selector for that text. A drift here is a call to a function that does not
        exist, which the node answers with 0x -- and 0x decodes as False.
        """
        import re
        from pathlib import Path

        source = (Path(__file__).resolve().parent.parent / "contracts" / "SignalAdapter.sol").read_text()
        declared = set(re.findall(r"function\s+(\w+)\s*\(([^)]*)\)", source))
        by_name = {}
        for name, args in declared:
            types = []
            for arg in args.split(","):
                parts = arg.split()
                if parts:
                    types.append(parts[0])
            by_name[name] = f"{name}({','.join(types)})"

        self.assertEqual(by_name["publish"], "publish(bytes32,int256,uint64)")
        self.assertEqual(by_name["revoke"], "revoke(bytes32)")
        self.assertEqual(by_name["get"], "get(bytes32)")
        self.assertEqual(by_name["isActionable"], "isActionable(bytes32)")

        self.assertEqual(hsk.PUBLISH_SELECTOR, hsk.selector(by_name["publish"]))
        self.assertEqual(hsk.REVOKE_SELECTOR, hsk.selector(by_name["revoke"]))
        self.assertEqual(hsk.GET_SELECTOR, hsk.selector(by_name["get"]))
        self.assertEqual(hsk.IS_ACTIONABLE_SELECTOR, hsk.selector(by_name["isActionable"]))

    def test_event_topic_matches_the_solidity_event(self):
        import re
        from pathlib import Path

        source = (Path(__file__).resolve().parent.parent / "contracts" / "SignalAdapter.sol").read_text()
        match = re.search(r"event\s+SignalPublished\s*\(([^)]*)\)", source)
        self.assertIsNotNone(match)
        types = [a.split()[0] for a in match.group(1).split(",") if a.strip()]
        signature = f"SignalPublished({','.join(types)})"
        self.assertEqual(signature, "SignalPublished(bytes32,int256,uint64,uint256)")
        self.assertEqual(hsk.SIGNAL_PUBLISHED_TOPIC, hsk.topic(signature))

    def test_decode_signal(self):
        row = "0x" + (
            f"{1560:064x}"
            + f"{1_793_150_000:064x}"
            + f"{1_793_200_000:064x}"
            + f"{35_133_148:064x}"
        )
        parsed = decode_signal(row)
        self.assertEqual(parsed.edge_bps, 1560)
        self.assertEqual(parsed.published_at, 1_793_150_000)
        self.assertEqual(parsed.expires_at, 1_793_200_000)
        self.assertEqual(parsed.block_number, 35_133_148)
        self.assertTrue(parsed.live)

    def test_decode_signal_negative_edge(self):
        row = "0x" + (
            hsk.enc_int(-250).hex()
            + f"{1:064x}" + f"{2:064x}" + f"{3:064x}"
        )
        self.assertEqual(decode_signal(row).edge_bps, -250)

    def test_decode_signal_rejects_short(self):
        with self.assertRaises(ValueError):
            decode_signal("0x" + "00" * 64)

    def test_decode_bool(self):
        self.assertFalse(decode_bool("0x" + "00" * 32))
        self.assertTrue(decode_bool("0x" + "00" * 31 + "01"))


class DeployGateTest(unittest.TestCase):
    def test_empty_account_is_blocked(self):
        gate = hsk.DeployGate(False, "empty", 0, 10**18)
        self.assertFalse(gate.ready)
        self.assertEqual(gate.balance_native, Decimal(0))
        self.assertEqual(gate.shortfall_native, Decimal(1))

    def test_funded_account_is_ready(self):
        gate = hsk.DeployGate(True, "funded", 3 * 10**18, 10**18)
        self.assertTrue(gate.ready)
        self.assertEqual(gate.shortfall_native, Decimal(0))

    def test_line_names_the_chain_and_the_shortfall(self):
        gate = hsk.DeployGate(False, "x", 0, 10**18)
        line = gate.line(HSK_MAINNET)
        self.assertIn("hsk", line)
        self.assertIn("177", line)
        self.assertIn("short 1.000000 HSK", line)

    def test_ready_line_has_no_shortfall(self):
        gate = hsk.DeployGate(True, "funded", 3 * 10**18, 10**18)
        self.assertTrue(gate.line(HSK_MAINNET).startswith("ready:"))


class FakeRpc:
    def __init__(self, balance_wei, gas_price_wei):
        self._balance = balance_wei
        self._price = gas_price_wei

    def gas_price(self):
        return self._price

    def balance(self, address):
        return self._balance


class DeploymentGateLiveLogicTest(unittest.TestCase):
    def test_prices_the_fee_at_twice_the_base_fee(self):
        rpc = FakeRpc(balance_wei=1000, gas_price_wei=2)
        gate = deployment_gate(rpc, "0x" + "00" * 20, gas_estimate=100)
        self.assertEqual(gate.needed_wei, 2 * 2 * 100)
        self.assertTrue(gate.ready)

    def test_the_real_account_with_zero_gas_is_blocked(self):
        rpc = FakeRpc(balance_wei=0, gas_price_wei=10**9)
        gate = deployment_gate(rpc, "0x72Aa6edDE71B94f6b99c8e52EdC02B5C75F74d1a")
        self.assertFalse(gate.ready)
        self.assertIn("empty account", gate.reason)


class RpcErrorTest(unittest.TestCase):
    def test_message_carries_the_node_text(self):
        err = RpcError("eth_call", {"error": {"message": "execution reverted"}})
        self.assertIn("execution reverted", str(err))
        self.assertEqual(err.method, "eth_call")

    def test_revert_is_not_retried(self):
        # A revert must surface on the first call. A retry loop around a revert
        # is how a bot sends the same failing transaction three times.
        rpc = Rpc("https://mainnet.hsk.xyz", retries=2)
        calls = []

        def boom(method, params):
            calls.append(method)
            raise RpcError(method, {"error": {"message": "execution reverted"}})

        rpc._post = boom
        with self.assertRaises(RpcError):
            rpc.call("0x" + "00" * 20, "0x")
        self.assertEqual(calls, ["eth_call"])


@unittest.skipUnless(os.environ.get("HSK_LIVE") == "1", "set HSK_LIVE=1 to hit the public RPC")
class LiveChainTest(unittest.TestCase):
    def test_mainnet_answers_177(self):
        rpc = Rpc(HSK_MAINNET.rpc)
        self.assertEqual(rpc.chain_id(), 177)

    def test_testnet_answers_133(self):
        rpc = Rpc(HSK_TESTNET.rpc)
        self.assertEqual(rpc.chain_id(), 133)

    def test_mainnet_has_a_head(self):
        self.assertGreater(Rpc(HSK_MAINNET.rpc).block_number(), 0)


if __name__ == "__main__":
    unittest.main()


class DeployScriptTest(unittest.TestCase):
    """The plan the script hands to a signer has to be well formed."""

    def _load(self):
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / "scripts" / "hsk_deploy.py"
        spec = importlib.util.spec_from_file_location("hsk_deploy", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_constructor_args_is_one_abi_word(self):
        module = self._load()
        word = module.constructor_args("0x72Aa6edDE71B94f6b99c8e52EdC02B5C75F74d1a")
        self.assertEqual(len(word), 64)
        self.assertEqual(word[:24], "00" * 12)
        self.assertEqual(word[24:], "72aa6edde71b94f6b99c8e52edc02b5c75f74d1a")

    def test_artifact_is_committed_and_compiles_to_bytecode(self):
        from pathlib import Path

        artifact = Path(__file__).resolve().parent.parent / "build" / "contracts_SignalAdapter_sol_SignalAdapter.bin"
        self.assertTrue(artifact.exists(), "compile before running the suite")
        hexcode = artifact.read_text().strip()
        self.assertTrue(hexcode.startswith("6080") or hexcode.startswith("60"), hexcode[:8])
        self.assertEqual(len(hexcode) % 2, 0)
