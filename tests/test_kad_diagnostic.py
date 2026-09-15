"""Локальная диагностика: браузер заменён mock; сеть и LIVE не запускаются."""
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from suppliers.arbitration import unavailable_kad_result
from suppliers.kad_diagnostic import DiagnosticClient, DiagnosticLog, run, summarize
from suppliers.verification import MANUAL
from tests.test_kad_integration import INN, OTHER, SEARCH, CARD, CARD_URL, EMPTY, verified


def context(*, root_status=200, root_html="<body>КАД</body>", search_html=SEARCH,
            search_status=200, send=True, sent_inn=INN, search_error=None, card_status=200, payload_extra=None):
    page, card = Mock(), Mock()
    page.url = "https://kad.arbitr.ru/"
    page.goto.return_value = SimpleNamespace(status=root_status)
    page.content.return_value = root_html
    body, close = Mock(), Mock()
    body.inner_text.return_value = root_html
    close.count.return_value = 0
    page.locator.side_effect = lambda selector: body if selector == "body" else close
    field = Mock()
    field.input_value.return_value = INN
    page.get_by_placeholder.return_value = field
    request = SimpleNamespace(url="https://kad.arbitr.ru/Kad/SearchInstances", method="POST",
                              post_data_json={"Sides": [{"Name": sent_inn, "Type": -1}], **(payload_extra or {})})
    response = SimpleNamespace(url=request.url, request=request, status=search_status,
                               text=lambda: search_html)
    @contextmanager
    def capture(predicate, **kwargs):
        assert predicate(response)
        yield SimpleNamespace(value=response)
    page.expect_response.side_effect = capture
    def click(**kwargs):
        if send and page.on.call_args:
            page.on.call_args.args[1](request)
        if search_error:
            raise search_error
    page.get_by_role.return_value.click.side_effect = click
    card.url = CARD_URL
    card.goto.return_value = SimpleNamespace(status=card_status)
    card.content.return_value = CARD
    ctx = Mock()
    ctx.new_page.side_effect = [page, card]
    return ctx, page, card


class KADDiagnosticTests(unittest.TestCase):
    def check(self, **options):
        ctx, page, card = context(**options)
        with tempfile.TemporaryDirectory() as directory, patch("builtins.print"):
            log = DiagnosticLog(directory)
            client = DiagnosticClient(context=ctx, cache_dir=Path(directory) / "cache", diagnostic_callback=log)
            result = client.check(INN)
            saved = [json.loads(line) for line in (Path(directory) / "stages.jsonl").read_text().splitlines()]
            self.assertEqual(saved, log.events)
            return result, summarize(log.events), page, card

    def test_success_uses_real_request_event_and_exact_role_on_sample(self):
        result, summary, page, card = self.check()
        self.assertTrue(summary["diagnostic_success"])
        self.assertTrue(summary["search_sent"])
        self.assertTrue(summary["inn_filled"])
        self.assertEqual(summary["cases_found"], 1)
        self.assertTrue(summary["role_matches_target_inn"])
        self.assertEqual(summary["card_fields_extracted"]["role"], "Ответчик")
        self.assertEqual(summary["card_fields_extracted"]["target_inn"], INN)
        self.assertEqual(summary["card_fields_extracted"]["status"], "Рассматривается")
        self.assertTrue(result["checked_in_kad"])
        page.get_by_role.return_value.click.assert_called_once()
        card.goto.assert_called_once()

    def test_click_or_wrong_inn_does_not_confirm_search_or_open_card(self):
        for options in ({"send": False}, {"sent_inn": OTHER}):
            with self.subTest(options=options):
                result, summary, _, card = self.check(**options)
                self.assertTrue(summary["search_clicked"])
                self.assertFalse(summary["diagnostic_success"])
                self.assertFalse(result["checked_in_kad"])
                card.goto.assert_not_called()

    def test_root_451_stops_before_input_and_search(self):
        result, summary, page, card = self.check(root_status=451)
        self.assertEqual(result["status"], "KAD_REQUIRES_MANUAL_CHECK")
        self.assertFalse(summary["site_opened"])
        self.assertTrue(summary["site_reached"])
        self.assertTrue(summary["access_blocked"])
        self.assertIn("451", summary["failure"]["reason"])
        page.get_by_placeholder.assert_not_called()
        page.get_by_role.assert_not_called()
        card.goto.assert_not_called()
        page.close.assert_called_once()

    def test_captcha_in_search_stops_without_opening_card_or_retry(self):
        result, summary, page, card = self.check(search_html="<body>CAPTCHA</body>")
        self.assertEqual(result["status"], "KAD_REQUIRES_MANUAL_CHECK")
        self.assertTrue(summary["challenge_detected"])
        self.assertTrue(summary["search_sent"])
        self.assertIsNone(summary["cases_found"])
        self.assertFalse(summary["diagnostic_success"])
        card.goto.assert_not_called()
        page.get_by_role.return_value.click.assert_called_once()

    def test_many_cases_open_only_one_card_and_remain_incomplete(self):
        html = SEARCH.replace('value="1"', 'value="26"')
        html += SEARCH.replace(CARD_URL, CARD_URL.replace("12345678", "abcdefab")).replace("А40-123/", "А40-124/")
        # Один общий подтверждённый счётчик, а не второй счётчик из искусственной строки.
        html = html.replace('<input id="documentsTotalCount" value="1">', '')
        result, summary, page, card = self.check(search_html=html)
        self.assertEqual(summary["cases_found"], 26)
        self.assertTrue(summary["diagnostic_success"])
        self.assertFalse(result["checked_in_kad"])
        self.assertEqual(result["status"], "KAD_INCOMPLETE")
        card.goto.assert_called_once()
        page.get_by_role.return_value.click.assert_called_once()

    def test_confirmed_zero_is_not_confused_with_missing_results(self):
        for html, success, count in ((EMPTY, True, 0), ("<body>Нет дел</body>", False, None)):
            with self.subTest(html=html):
                result, summary, _, card = self.check(search_html=html)
                self.assertEqual(summary["diagnostic_success"], success)
                self.assertEqual(summary["cases_found"], count)
                self.assertEqual(result["checked_in_kad"], success)
                card.goto.assert_not_called()

    def test_search_timeout_saves_sent_stage_and_does_not_retry(self):
        result, summary, page, card = self.check(search_error=TimeoutError("Поиск не ответил за 25 секунд"))
        self.assertFalse(result["checked_in_kad"])
        self.assertTrue(summary["search_sent"])
        self.assertIsNone(summary["cases_found"])
        self.assertEqual(summary["failure"]["error_type"], "TimeoutError")
        self.assertIn("25 секунд", summary["failure"]["reason"])
        page.get_by_role.return_value.click.assert_called_once()
        card.goto.assert_not_called()

    def test_card_block_preserves_discovered_case_but_not_role(self):
        result, summary, _, card = self.check(card_status=451)
        self.assertEqual(summary["cases_found"], 1)
        self.assertFalse(summary["card_opened"])
        self.assertIsNone(summary["role_matches_target_inn"])
        self.assertEqual(result["status"], "KAD_REQUIRES_MANUAL_CHECK")
        self.assertEqual(result["case_urls"], [CARD_URL])
        self.assertFalse(result["checked_in_kad"])
        card.goto.assert_called_once()

    def test_runner_saves_summary_and_sample_and_launches_visible_browser(self):
        ctx, _, _ = context()
        browser = Mock()
        browser.new_context.return_value = ctx
        pw = Mock()
        pw.chromium.launch.return_value = browser
        manager = Mock()
        manager.__enter__ = Mock(return_value=pw)
        manager.__exit__ = Mock(return_value=False)
        with tempfile.TemporaryDirectory() as directory, patch("builtins.print"), \
                patch("playwright.sync_api.sync_playwright", return_value=manager):
            self.assertEqual(run(INN, directory), 0)
            report = json.loads((Path(directory) / "summary.json").read_text())
            sample = json.loads((Path(directory) / "sample_case.json").read_text())
            self.assertTrue(report["diagnostic_success"])
            self.assertEqual(sample["fields"]["target_inn"], INN)
            self.assertIn(INN, sample["card_text"])
            browser.close.assert_called_once()
            pw.chromium.launch.assert_called_once_with(headless=False)

    def test_startup_failure_still_saves_report_with_unknown_stages(self):
        with tempfile.TemporaryDirectory() as directory, patch("builtins.print"), \
                patch("playwright.sync_api.sync_playwright", side_effect=RuntimeError("Chromium недоступен")):
            code = run(INN, directory)
            report = json.loads((Path(directory) / "summary.json").read_text())
            self.assertEqual(code, 2)
            self.assertIsNone(report["site_opened"])
            self.assertIsNone(report["challenge_detected"])
            self.assertFalse(report["kad_check"]["checked_in_kad"])
            self.assertIn("Chromium недоступен", report["failure"]["reason"])

    def test_known_seller_technical_kad_failure_is_yellow_with_visible_reason(self):
        for domain in ("shop.example", "citilink.ru"):
            for reason in ("HTTP 451", "CAPTCHA", "challenge", "timeout"):
                with self.subTest(domain=domain, reason=reason):
                    kad = unavailable_kad_result(INN, reason)
                    result = verified(kad, domain=domain)
                    self.assertEqual(result["verification_status"], MANUAL)
                    self.assertIn(reason, result["verification_comment"])
                    self.assertEqual(result["arbitration_cases"]["technical_status"], "KAD_REQUIRES_MANUAL_CHECK")


if __name__ == "__main__":
    unittest.main()
