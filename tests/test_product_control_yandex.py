"""Изолированные проверки перехода контрольного запуска на Search API."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from pipeline import product_price_control as control
from model_search.yandex_provider import YandexSearchProvider, SearchAPIError, post_json
from model_search.deferred_search import run_deferred
from security.yandex_credentials import CredentialsResult
from tests.test_yandex_search import credentials, envelope


class YandexControlTests(unittest.TestCase):
    def test_missing_credentials_stops_before_browser_and_intake(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'jobs.json'
            source.write_text(json.dumps({'jobs': [{'id': '1', 'item': {'name': 'МФУ'}}]}))
            with patch.object(control, 'get_yandex_credentials', return_value=CredentialsResult('unavailable', '')), \
                 patch.object(control, 'prepare_product') as intake, \
                 patch('playwright.sync_api.sync_playwright') as browser, \
                 patch.object(control, 'Path', side_effect=lambda value: root / value if value == 'data/product_price_control' else Path(value)):
                result = control.run(source)
            self.assertEqual(result['status'], 'SEARCH_API_NOT_CONFIGURED')
            self.assertEqual(result['api_calls'], 0)
            self.assertTrue(Path(result['path']).exists())
            intake.assert_not_called()
            browser.assert_not_called()

    def test_control_wires_yandex_to_shop_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'jobs.json'
            source.write_text(json.dumps({'jobs': [{'id': '1', 'item': {'name': 'МФУ'}}]}))
            provider = YandexSearchProvider(allow_paid=True, credentials_provider=credentials,
                                            sender=Mock(return_value=envelope()), max_requests=30)
            with patch.object(control, 'get_yandex_credentials', side_effect=credentials), \
                 patch.object(control, 'prepare_product', return_value={'original_model': 'МФУ'}), \
                 patch('playwright.sync_api.sync_playwright'), \
                 patch.object(control, 'PlaywrightResearch') as reader, \
                 patch.object(control, 'YandexSearchProvider', return_value=provider) as factory, \
                 patch.object(control, 'Path', side_effect=lambda value: root / value if value == 'data/product_price_control' else Path(value)):
                reader.return_value.prices_exact.return_value = {'search_status': 'completed'}
                result = control.run(source)
            factory.assert_called_once_with(allow_paid=True, fetcher=reader.return_value, max_requests=30)
            args, kwargs = reader.return_value.prices_exact.call_args
            self.assertIs(args[1], provider)
            self.assertEqual(kwargs['target_offers'], 6)
            self.assertTrue(kwargs['require_complete_offers'])
            self.assertEqual(result['search_provider'], 'yandex_search_api')

    def test_http_status_preserved_without_response_body(self):
        opener = Mock()
        opener.open.side_effect = HTTPError('https://example.org', 403, 'secret-body', {}, None)
        with patch('model_search.yandex_provider.build_opener', return_value=opener):
            with self.assertRaises(SearchAPIError) as raised:
                post_json({}, 'test-key')
        self.assertEqual(raised.exception.diagnostic['http_status'], 403)
        self.assertNotIn('secret-body', str(raised.exception))

    def test_api_error_is_not_captcha_and_is_not_retried(self):
        sender = Mock(side_effect=SearchAPIError('Ошибка', reason_code='api_http_429', http_status=429))
        provider = YandexSearchProvider(allow_paid=True, credentials_provider=credentials, sender=sender)
        def search(job):
            provider.search('МФУ')
        with tempfile.TemporaryDirectory() as directory:
            result = run_deferred([{'id': 1}, {'id': 2}], search, Path(directory) / 'result.json')
        self.assertEqual([r['status'] for r in result['jobs']], ['failed', 'failed'])
        self.assertEqual(result['jobs'][0]['diagnostic']['http_status'], 429)
        self.assertEqual(provider.search_log[0]['diagnostic']['reason'], 'api_http_429')
        sender.assert_called_once()

    def test_global_request_budget(self):
        sender = Mock(return_value=envelope())
        provider = YandexSearchProvider(allow_paid=True, credentials_provider=credentials,
                                        sender=sender, max_requests=30, sleep=Mock())
        for _ in range(30):
            provider.search('МФУ')
        with self.assertRaises(SearchAPIError) as raised:
            provider.search('МФУ')
        self.assertEqual(raised.exception.reason_code, 'api_request_limit')
        self.assertEqual(sender.call_count, 30)
