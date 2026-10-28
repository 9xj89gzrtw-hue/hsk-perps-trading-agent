"""The local web desk: routes, body limits, cards, and escaping."""

from __future__ import annotations

import http.client
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

from agent.chat import ChatEngine, fixture_loader
from agent.webchat import MAX_CARDS, Desk, make_server


def make_engine() -> ChatEngine:
    return ChatEngine(fixture_loader(), deploy_status=lambda: {"ready": False, "cost_native": "0.2"})


class WebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(make_engine(), port=0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def get(self, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as r:
                return r.status, r.headers.get("content-type", ""), r.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("content-type", ""), exc.read()

    def post(self, path, payload, headers=None):
        body = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=body,
            headers={"content-type": "application/json", **(headers or {})},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_page_is_self_contained(self):
        status, ctype, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        text = body.decode()
        self.assertIn("Trading Desk", text)
        self.assertIn("MARKET TAPE", text)
        self.assertIn("/api/markets", text)
        self.assertIn("URLSearchParams(location.search)", text)
        self.assertNotIn("<script src=", text)
        self.assertNotIn("cdn.", text)

    def test_health(self):
        status, _, body = self.get("/api/health")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["deployment"]["chainId"], 133)

    def test_markets_route_serves_the_tape(self):
        status, ctype, body = self.get("/api/markets")
        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["counts"], {"perps": 21, "spots": 3})
        self.assertEqual(len(payload["markets"]), 21)
        self.assertFalse(payload["truncated"])
        self.assertEqual(payload["source"], "fixture")
        self.assertGreater(payload["block"], 0)
        for key in ("base", "name", "mark", "index", "basisBps", "fundingApr", "openInterest", "volume"):
            self.assertIn(key, payload["markets"][0])

    def test_markets_are_sorted_by_absolute_funding(self):
        payload = json.loads(self.get("/api/markets")[2])
        aprs = [abs(row["fundingApr"]) for row in payload["markets"]]
        self.assertEqual(aprs, sorted(aprs, reverse=True))
        self.assertEqual(payload["markets"][0]["name"], "BTC-PERP")
        self.assertAlmostEqual(payload["markets"][0]["fundingApr"], 157.20, places=2)

    def test_markets_force_refresh_is_answerable(self):
        status, _, body = self.get("/api/markets?force=1")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_deployment_is_exposed_to_the_page(self):
        payload = json.loads(self.get("/api/markets")[2])
        dep = payload["deployment"]
        self.assertEqual(dep["chainId"], 133)
        self.assertEqual(dep["network"], "HSK Testnet")
        self.assertEqual(dep["address"], "0x6595d7079cbdc72d52086ef7381364e23c5f00a9")
        self.assertEqual(len(dep["deployTx"]), 66)
        self.assertEqual(len(dep["publishTx"]), 66)

    def test_chat_returns_a_reply_and_a_card(self):
        status, body = self.post("/api/chat", {"message": "scan"})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["intent"], "scan")
        self.assertIn("perps", payload["reply"])
        self.assertTrue(payload["card_url"].endswith(".svg"))
        self.assertTrue(payload["png_url"].endswith(".png"))

    def test_card_svg_is_served(self):
        _, body = self.post("/api/chat", {"message": "plan 1000 3x"})
        card_url = json.loads(body)["card_url"]
        status, ctype, svg = self.get(card_url)
        self.assertEqual(status, 200)
        self.assertIn("image/svg+xml", ctype)
        self.assertTrue(svg.startswith(b"<svg"))

    def test_card_png_is_served(self):
        _, body = self.post("/api/chat", {"message": "scan"})
        png_url = json.loads(body)["png_url"]
        status, ctype, png = self.get(png_url)
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "image/png")
        self.assertTrue(png.startswith(b"\x89PNG"))

    def test_unknown_card_is_404(self):
        status, _, _ = self.get("/api/card/nope.svg")
        self.assertEqual(status, 404)

    def test_unknown_route_is_404(self):
        status, _, _ = self.get("/nope")
        self.assertEqual(status, 404)

    def test_get_on_chat_is_404(self):
        status, _, _ = self.get("/api/chat")
        self.assertEqual(status, 404)

    def test_invalid_json_is_400(self):
        status, _ = self.post("/api/chat", b"{not json")
        self.assertEqual(status, 400)

    def test_empty_message_is_400(self):
        status, _ = self.post("/api/chat", {"message": "   "})
        self.assertEqual(status, 400)

    def test_missing_message_is_400(self):
        status, _ = self.post("/api/chat", {"q": "scan"})
        self.assertEqual(status, 400)

    def test_long_message_is_400(self):
        status, _ = self.post("/api/chat", {"message": "x" * 2001})
        self.assertEqual(status, 400)

    def test_body_over_the_cap_is_413(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.putrequest("POST", "/api/chat")
        conn.putheader("content-type", "application/json")
        conn.putheader("content-length", str(70_000))
        conn.endheaders()
        resp = conn.getresponse()
        self.assertEqual(resp.status, 413)
        conn.close()

    def test_deploy_is_answerable_over_http(self):
        status, body = self.post("/api/chat", {"message": "deploy"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["intent"], "deploy")

    def test_user_text_is_escaped_in_the_card(self):
        status, body = self.post("/api/chat", {"message": "<script>alert(1)</script>"})
        self.assertEqual(status, 200)
        card_url = json.loads(body)["card_url"]
        _, _, svg = self.get(card_url)
        self.assertNotIn(b"<script>alert(1)</script>", svg)
        self.assertIn(b"&lt;script&gt;", svg)


class DeskCacheTest(unittest.TestCase):
    def test_cards_are_bounded(self):
        desk = Desk(make_engine())
        for i in range(MAX_CARDS + 3):
            desk.ask(f"scan {i}")
        self.assertEqual(len(desk.cards), MAX_CARDS)

    def test_png_for_unknown_card_is_none(self):
        self.assertIsNone(Desk(make_engine()).png("missing"))


class DeploymentRecordTest(unittest.TestCase):
    """The JSON is a claim with hashes; these tests keep the claim well-formed."""

    @classmethod
    def setUpClass(cls):
        import json as _json
        from pathlib import Path

        cls.record = _json.loads((Path(__file__).resolve().parents[1] / "deployments" / "testnet.json").read_text())

    def test_chain_and_publisher(self):
        self.assertEqual(self.record["chainId"], 133)
        self.assertRegex(self.record["address"], r"^0x[0-9a-f]{40}$")
        self.assertRegex(self.record["publisher"], r"^0x[0-9a-fA-F]{40}$")

    def test_transactions_succeeded(self):
        for key in ("deploy", "publish"):
            entry = self.record[key]
            self.assertEqual(entry["status"], 1)
            self.assertGreater(entry["gasUsed"], 21_000)
            self.assertRegex(entry["tx"], r"^0x[0-9a-f]{64}$")

    def test_publish_follows_deploy(self):
        self.assertGreater(self.record["publish"]["block"], self.record["deploy"]["block"])
        self.assertLess(self.record["publish"]["publishedAt"], self.record["publish"]["expiresAt"])
        self.assertLess(self.record["publish"]["expiresAt"] - self.record["publish"]["publishedAt"], 3600)

    def test_the_page_loader_agrees(self):
        from agent.webchat import load_deployment

        dep = load_deployment()
        self.assertEqual(dep["address"], self.record["address"])
        self.assertEqual(dep["deployTx"], self.record["deploy"]["tx"])
        self.assertEqual(dep["publishTx"], self.record["publish"]["tx"])


class NoExternalAssetsTest(unittest.TestCase):
    def test_page_has_no_remote_fetch(self):
        from agent import webchat

        self.assertNotIn("http://", webchat.PAGE)
        self.assertNotIn("https://", webchat.PAGE)


if __name__ == "__main__":
    unittest.main()
