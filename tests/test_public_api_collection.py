from __future__ import annotations

import unittest
from unittest.mock import patch

from eat.public_api_collection import (
    _apply_program_api_filters,
    _fetch_json,
    _pagination_paths,
    _program_request_body,
    _request_body,
    classify_response,
)


class FakeResponse:
    def __init__(self, status, content_type, text="", url="https://tender-cache-api.agregatoreat.ru/api/TradeLot/list-published-trade-lots", location="", payload=None):
        self.status = status
        self.ok = 200 <= status < 300
        self.url = url
        self.headers = {"content-type": content_type}
        if location:
            self.headers["location"] = location
        self._text = text
        self._payload = payload

    def text(self):
        return self._text

    def json(self):
        return self._payload


class FlakyRequestContext:
    def __init__(self, failures: int, response: FakeResponse):
        self.request = self
        self.failures = failures
        self.response = response
        self.calls = 0

    def fetch(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise TimeoutError("temporary timeout")
        return self.response


class PublicApiDiagnosticsTests(unittest.TestCase):
    def test_json_api_response(self):
        result = classify_response(FakeResponse(200, "application/json; charset=utf-8"))
        self.assertEqual(result["classification"], "json_api")
        self.assertEqual(result["http_status"], 200)

    def test_login_redirect_is_identified_without_body_output(self):
        result = classify_response(FakeResponse(
            302, "text/html", location="https://login.agregatoreat.ru/Account/Login"
        ))
        self.assertEqual(result["classification"], "login_page")
        self.assertTrue(result["redirect_to_login"])
        self.assertNotIn("body", result)

    def test_captcha_html_is_identified(self):
        result = classify_response(FakeResponse(
            403, "text/html", text="<html>Подтвердите, что вы не робот</html>"
        ))
        self.assertEqual(result["classification"], "captcha")
        self.assertTrue(result["captcha_detected"])

    def test_plain_http_failure_is_api_error(self):
        result = classify_response(FakeResponse(500, "application/json", text='{"error":"x"}'))
        self.assertEqual(result["classification"], "api_error")


class PublicApiPaginationTests(unittest.TestCase):
    @patch("eat.public_api_collection.time.sleep")
    def test_temporary_transport_error_is_retried(self, _sleep):
        context = FlakyRequestContext(
            2, FakeResponse(200, "application/json", payload={"items": []})
        )
        result = _fetch_json(
            context,
            {"url": "https://example.test/api", "method": "POST", "headers": {}},
            {"page": 1},
        )
        self.assertEqual(result, {"items": []})
        self.assertEqual(context.calls, 3)

    def test_program_template_contains_price_filters_and_pagination(self):
        result = _program_request_body({"price": {"min": 150000, "max": 400000}})
        self.assertEqual(result["page"], 1)
        self.assertEqual(result["size"], 100)
        self.assertEqual(result["priceStart"], 150000)
        self.assertEqual(result["priceEnd"], 400000)
        self.assertEqual(result["sort"], [{"fieldName": "publishDate", "direction": 2}])

    def test_program_price_filters_are_applied_without_losing_site_filters(self):
        captured = {
            "page": 1,
            "size": 10,
            "purchaseTypeIds": ["1", "2"],
            "priceStart": None,
            "priceEnd": None,
        }
        result = _apply_program_api_filters(
            captured, {"price": {"min": 150000, "max": 400000}}
        )
        self.assertEqual(result["purchaseTypeIds"], ["1", "2"])
        self.assertEqual(result["priceStart"], 150000)
        self.assertEqual(result["priceEnd"], 400000)
        self.assertIsNone(captured["priceStart"])

    def test_only_page_and_size_change_in_captured_filter_body(self):
        captured = {
            "pagination": {"page": 0, "size": 10},
            "filters": {"priceFrom": 150000, "regions": ["Москва"]},
        }
        page_path, initial, size_path = _pagination_paths(captured)
        result = _request_body(
            captured, page_path, initial, size_path, logical_page=3, page_size=100
        )
        self.assertEqual(result["pagination"], {"page": 2, "size": 100})
        self.assertEqual(result["filters"], captured["filters"])
        self.assertEqual(captured["pagination"], {"page": 0, "size": 10})


if __name__ == "__main__":
    unittest.main()
