"""The conversational desk: deterministic routing, sizing, and cards."""

from __future__ import annotations

import io
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from decimal import Decimal
from pathlib import Path

from agent import chatcard
from agent.chat import ChatEngine, Reply, fixture_loader
from agent.cli import build_parser, main


def engine(**kw) -> ChatEngine:
    return ChatEngine(fixture_loader(), deploy_status=lambda: {
        "chain": 177,
        "publisher": "0x72Aa6edDE71B94f6b99c8e52EdC02B5C75F74d1a",
        "gas_estimate": 348_527,
        "price_gwei": "501",
        "cost_native": "0.218265",
        "balance_native": "0",
        "shortfall_native": "0.218265",
        "ready": False,
    }, **kw)


class IntentTest(unittest.TestCase):
    def setUp(self):
        self.desk = engine()

    def test_help_is_the_empty_turn(self):
        self.assertEqual(self.desk.reply("").intent, "help")

    def test_russian_help(self):
        self.assertEqual(self.desk.reply("что ты умеешь?").intent, "help")

    def test_scan(self):
        reply = self.desk.reply("scan")
        self.assertEqual(reply.intent, "scan")
        self.assertIn("perps", reply.text)
        self.assertEqual(len(reply.metrics), 4)

    def test_scan_is_cached_until_refresh(self):
        self.desk.reply("scan")
        first = self.desk._snapshot
        self.desk.reply("scan")
        self.assertIs(self.desk._snapshot, first)

    def test_funding_one_market(self):
        reply = self.desk.reply("funding BTC")
        self.assertEqual(reply.intent, "funding")
        self.assertIn("BTC-PERP", reply.text)
        self.assertIn("APR", reply.text)

    def test_funding_unknown_market_lists_names(self):
        reply = self.desk.reply("funding NOPE")
        self.assertEqual(reply.intent, "funding")
        self.assertIn("do not have", reply.text)
        self.assertIn("LIT-PERP", reply.text)

    def test_plan_uses_dollar_amount(self):
        reply = self.desk.reply("plan $2000 at 3x")
        self.assertEqual(reply.intent, "plan")
        self.assertEqual(Decimal(reply.data["capital"]), Decimal("2000"))
        self.assertEqual(Decimal(reply.data["leverage"]), Decimal("3"))

    def test_plan_uses_a_bare_number_but_not_the_leverage(self):
        reply = self.desk.reply("plan 1000 5x")
        self.assertEqual(Decimal(reply.data["capital"]), Decimal("1000"))
        self.assertEqual(Decimal(reply.data["leverage"]), Decimal("5"))

    def test_plan_accepts_russian_rubric(self):
        reply = self.desk.reply("план 500 usd плечо 2")
        self.assertEqual(reply.intent, "plan")
        self.assertEqual(Decimal(reply.data["capital"]), Decimal("500"))

    def test_plan_has_a_why(self):
        self.desk.reply("plan 1000 3x")
        why = self.desk.reply("why")
        self.assertEqual(why.intent, "why")
        self.assertIn("plan", why.text)

    def test_check_is_placeable(self):
        reply = self.desk.reply("check BTC 1000 3x")
        self.assertEqual(reply.intent, "check")
        self.assertIn("BTC-PERP", reply.text)
        self.assertIn("placeable=", "placeable=" + " ".join(a for a, _, _ in reply.metrics))

    def test_deploy_reports_the_gate(self):
        reply = self.desk.reply("deploy")
        self.assertEqual(reply.intent, "deploy")
        self.assertIn("0.218265", reply.text)
        self.assertIn("CLOSED", reply.text)
        self.assertEqual(reply.metrics[-1], ("gate", "closed", "red"))

    def test_deploy_rpc_error_is_a_reply_not_a_crash(self):
        desk = ChatEngine(fixture_loader(), deploy_status=lambda: {"error": "boom"})
        reply = desk.reply("gas")
        self.assertEqual(reply.intent, "deploy")
        self.assertIn("boom", reply.text)

    def test_unknown_is_honest(self):
        reply = self.desk.reply("make me a sandwich")
        self.assertEqual(reply.intent, "unknown")
        self.assertIn("did not map", reply.text)

    def test_card_without_a_turn(self):
        reply = self.desk.reply("card")
        self.assertEqual(reply.intent, "card")
        self.assertIn("Nothing to draw", reply.text)

    def test_card_after_a_turn(self):
        self.desk.reply("scan")
        reply = self.desk.reply("card")
        self.assertEqual(reply.data["source_intent"], "scan")

    def test_loader_failure_becomes_an_error_reply(self):
        def boom():
            raise RuntimeError("indexer down")

        desk = ChatEngine(boom)
        reply = desk.reply("scan")
        self.assertEqual(reply.intent, "error")
        self.assertIn("indexer down", reply.text)

    def test_reply_serialises_metrics(self):
        payload = self.desk.reply("scan").as_dict()
        self.assertIn("metrics", payload)
        self.assertEqual(payload["metrics"][0]["label"], "perps")


class SizingTest(unittest.TestCase):
    def test_leverage_forms(self):
        desk = engine()
        for text, want in (("plan 3x", "3"), ("plan x4", "4"), ("plan плечо 5", "5")):
            reply = desk.reply(text)
            self.assertEqual(reply.data["leverage"], want, text)

    def test_leverage_is_capped_to_the_venue_range(self):
        reply = engine().reply("plan 100 99x")
        self.assertEqual(reply.data["leverage"], "3")

    def test_commas_in_capital(self):
        reply = engine().reply("plan $1,500 2x")
        self.assertEqual(Decimal(reply.data["capital"]), Decimal("1500"))


class CardTest(unittest.TestCase):
    def setUp(self):
        self.reply = engine().reply("plan 1000 3x")

    def test_svg_is_wellformed_and_selfcontained(self):
        svg = chatcard.render(self.reply, block=1, source="fixture")
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("</svg>", svg)
        self.assertNotIn("<script", svg)
        self.assertNotIn('href="http', svg)
        self.assertIn("CONVERSATIONAL", svg.upper())

    def test_svg_escapes_user_text(self):
        reply = Reply(text="<script>alert(1)</script>", intent="scan", question="<b>hi</b>")
        svg = chatcard.render(reply)
        self.assertNotIn("<script>alert(1)</script>", svg)
        self.assertIn("&lt;script&gt;", svg)

    def test_write_creates_parents(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a" / "b.svg"
            chatcard.write(self.reply, path)
            self.assertTrue(path.exists())

    def test_png_has_the_signature_and_the_right_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "card.png"
            used = chatcard.write_png(self.reply, path)
            self.assertIn(used, ("pillow", "minimal"))
            data = path.read_bytes()
            self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(int.from_bytes(data[16:20], "big"), 1200)
            self.assertEqual(int.from_bytes(data[20:24], "big"), 760)

    def test_minimal_png_is_a_valid_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "min.png"
            chatcard._minimal_png(path, 120, 80)
            data = path.read_bytes()
            self.assertTrue(data.startswith(b"\x89PNG"))
            self.assertEqual(int.from_bytes(data[16:20], "big"), 120)

    def test_question_is_wrapped(self):
        reply = Reply(text="ok", intent="scan", question="x " * 200)
        svg = chatcard.render(reply)
        self.assertLessEqual(len(svg.splitlines()), 200)


class ChatCliTest(unittest.TestCase):
    def test_parser_has_chat(self):
        args = build_parser().parse_args(["chat", "--once", "scan", "--fixture"])
        self.assertEqual(args.command, "chat")
        self.assertEqual(args.once, "scan")
        self.assertEqual(args.func.__name__, "cmd_chat")

    def test_web_is_off_by_default(self):
        args = build_parser().parse_args(["chat"])
        self.assertFalse(args.web)
        self.assertEqual(args.host, "127.0.0.1")

    def test_once_prints_the_answer(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["chat", "--fixture", "--once", "scan"])
        self.assertEqual(code, 0)
        self.assertIn("perps", buf.getvalue())

    def test_once_card_writes_svg_and_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "card.svg"
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["chat", "--fixture", "--once", "scan", "--card", str(target)])
            self.assertEqual(code, 0)
            self.assertTrue(target.exists())
            self.assertTrue(target.with_suffix(".png").exists())


@unittest.skipUnless(shutil.which("node"), "node is optional")
class NoNodeNeededTest(unittest.TestCase):
    def test_chat_module_does_not_shell_out(self):
        import agent.chat as chat

        source = Path(chat.__file__).read_text()
        self.assertNotIn("subprocess", source)


if __name__ == "__main__":
    unittest.main()
