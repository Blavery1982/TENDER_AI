"""Проверки подтверждения карточек браузером, без обращения к магазинам."""
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import MagicMock

from model_search.playwright_provider import PlaywrightResearch
from reports.price_search_report import write_price_search_markdown

PAGE = '''<html><head><title>Kyocera Ecosys MA3500X</title>
<script type="application/ld+json">{"@type":"Product","name":"Kyocera Ecosys MA3500X",
"offers":{"price":"32891","priceCurrency":"RUB","availability":"https://schema.org/InStock"}}</script>
</head><body><h1>Kyocera Ecosys MA3500X</h1></body></html>'''


class PlaywrightProviderTests(unittest.TestCase):
    def research(self, body="Kyocera Ecosys MA3500X"):
        context = MagicMock()
        page = context.new_page.return_value
        page.url = "https://shop.example/product/ma3500x"
        page.goto.return_value.status = 200
        page.content.return_value = PAGE
        page.locator.return_value.inner_text.return_value = body
        return PlaywrightResearch(context)

    def test_product_page_price_wins_over_search_snippet(self):
        research = self.research()
        research.read_http = lambda url: (PAGE, url, 200)
        provider = MagicMock()
        provider.search.return_value = [{"url": "https://shop.example/product/ma3500x", "snippet": "Цена 100 ₽"}]
        with tempfile.TemporaryDirectory() as directory:
            result = research.prices("MA3500X", provider, Path(directory) / "prices.json")
        self.assertEqual(result["minimum_price"], 32891)
        self.assertEqual(result["page_verification"], "playwright")
        self.assertTrue(result["offers"][0]["exact_model_match"])

    def test_deep_search_keeps_three_lowest_prices_and_uses_http_first(self):
        research = self.research()
        prices = [32000, 28000, 25000, 31000, 27000]
        rows = [{"url": f"https://shop{i}.example/product/ma3500x"} for i in range(len(prices))]
        provider = MagicMock()
        provider.search.return_value = rows
        def http(url):
            index = int(url.split("shop")[1].split(".")[0])
            return (PAGE.replace("32891", str(prices[index])), url, 200)
        research.read_http = http
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "prices.json"
            result = research.prices_exact("Kyocera Ecosys MA3500X", provider, output)
            saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(provider.search.call_count, 8)
        self.assertEqual(result["unique_urls"], 5)
        self.assertEqual(result["confirmed_prices"], 5)
        self.assertEqual([x["price"] for x in result["top3_confirmed_prices"]], [25000, 27000, 28000])
        self.assertEqual(result["playwright_pages_read"], 0)
        self.assertEqual(result["http_pages_read"], 5)
        self.assertEqual(saved["price_candidates"][0]["confirmed_price"], 25000)
        self.assertEqual(saved["price_candidates"][0]["extraction_method"], "json_ld")

    def test_deep_search_uses_playwright_only_when_http_has_no_price(self):
        research = self.research()
        provider = MagicMock()
        provider.search.return_value = [{"url": "https://shop.example/product/ma3500x"}]
        no_price = PAGE.replace('"price":"32891",', '')
        research.read_http = lambda url: (no_price, url, 200)
        with tempfile.TemporaryDirectory() as directory:
            result = research.prices_exact("Kyocera Ecosys MA3500X", provider,
                                           Path(directory) / "prices.json")
        self.assertEqual(result["http_pages_read"], 1)
        self.assertEqual(result["playwright_pages_read"], 1)
        self.assertEqual(result["minimum_price"], 32891)
        self.assertEqual(result["offers"][0]["extraction_method"], "playwright")
        self.assertEqual(result["price_candidates"][0]["confirmed_price"], 32891)

    def test_markdown_report_reads_saved_json_without_fetching(self):
        result = {"target_model": "Kyocera Ecosys MA3500X", "model_search_mode": "EXACT_MODEL",
                  "queries_used": ["q"], "unique_urls": 1, "unique_domains": 1,
                  "http_pages_read": 1, "playwright_pages_read": 0, "product_cards_confirmed": 1,
                  "confirmed_prices": 1, "stop_reason": "Все кандидаты проверены",
                  "price_candidates": [{"confirmed_price": 32891, "domain": "shop.ru",
                                        "availability_normalized": "in_stock",
                                        "extraction_method": "json_ld",
                                        "source_url": "https://shop.ru/product/ma3500x"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prices.json"
            path.write_text(json.dumps(result), encoding="utf-8")
            report = write_price_search_markdown(path)
            text = report.read_text(encoding="utf-8")
        self.assertIn("32891", text)
        self.assertIn("json_ld", text)
        self.assertIn("https://shop.ru/product/ma3500x", text)

    def test_captcha_is_not_accepted_as_product_evidence(self):
        with self.assertRaises(PermissionError):
            self.research("Подтвердите, что вы не робот").read("https://shop.example/captcha")

    def test_official_source_status_is_not_invented(self):
        result = self.research().fetch("https://shop.example/product/ma3500x")
        self.assertFalse(result["source_verified"])
        self.assertEqual(result["source_type"], "other")
