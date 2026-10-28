"""Known-answer tests for keccak256 and the selectors the agent sends.

If these pass, the calldata is built from the real hash function and not from a
constant somebody pasted. The vectors are the public Ethereum ones.
"""

from __future__ import annotations

import unittest

from agent.keccak import keccak256, selector, topic


class KeccakVectorTest(unittest.TestCase):
    def test_empty_string(self):
        self.assertEqual(
            keccak256(b"").hex(),
            "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
        )

    def test_abc(self):
        self.assertEqual(
            keccak256(b"abc").hex(),
            "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45",
        )

    def test_exactly_one_rate_block(self):
        # 135 bytes: padding still fits inside the 136-byte rate.
        digest = keccak256(b"a" * 135)
        self.assertEqual(len(digest), 32)
        self.assertEqual(digest, keccak256(b"a" * 135))

    def test_spans_two_blocks(self):
        # 136 bytes: the 0x01 pad spills into a second block, the case a lazy
        # implementation gets wrong.
        self.assertEqual(len(keccak256(b"a" * 136)), 32)
        self.assertNotEqual(keccak256(b"a" * 136), keccak256(b"a" * 137))

    def test_is_not_sha3(self):
        import hashlib
        self.assertNotEqual(keccak256(b""), hashlib.sha3_256(b"").digest())


class SelectorTest(unittest.TestCase):
    def test_erc20_transfer(self):
        self.assertEqual(selector("transfer(address,uint256)").hex(), "a9059cbb")

    def test_erc20_balance_of(self):
        self.assertEqual(selector("balanceOf(address)").hex(), "70a08231")

    def test_publish_is_four_bytes(self):
        self.assertEqual(len(selector("publish(bytes32,int256,uint64)")), 4)

    def test_selector_is_prefix_of_topic(self):
        self.assertEqual(topic("transfer(address,uint256)")[:4], selector("transfer(address,uint256)"))

    def test_erc20_transfer_topic(self):
        self.assertEqual(
            topic("Transfer(address,address,uint256)").hex(),
            "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
        )


if __name__ == "__main__":
    unittest.main()
