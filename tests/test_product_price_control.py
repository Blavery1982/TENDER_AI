"""Профильные регрессии полного имени, источника модели и отложенного поиска."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import json

from ai.codex_intake import validate_intake
from documents.item_sources import resolve_item_sources
from model_search.price_readiness import classify_price_search_readiness, extract_direct_identifier
from model_search.deferred_search import run_deferred
from model_search.google_provider import GoogleBrowserSearch
from model_search.diagnostics import BlockedSourceError

CABLE = 'Коммутационный кабель микрофонных консолей 13Р-Т3 (DIN 13-PIN) Gonsin'


class ProductControlTests(unittest.TestCase):
    def test_full_cable_name(self):
        result = resolve_item_sources({'name': CABLE})
        self.assertEqual(result['original_model'], CABLE)
        self.assertEqual(result['model_search_mode'], 'EXACT_MODEL')

    def test_full_delegate_name(self):
        name = 'Пульт делегата Gonsin TL-VDC4200'
        self.assertEqual(resolve_item_sources({'name': name})['original_model'], name)

    def test_full_chairman_name(self):
        name = 'Пульт председателя Gonsin TL-VXC4200'
        self.assertEqual(resolve_item_sources({'name': name})['original_model'], name)

    def test_connector_is_not_model(self):
        self.assertIsNone(extract_direct_identifier({'name': 'Кабель DIN 13-PIN'}))

    def test_review_not_overridden_by_export_classifier(self):
        result = classify_price_search_readiness({'name': CABLE}, {
            'source_resolution_version': 6, 'position_kind': 'goods',
            'model_search_mode': 'MODEL_MODE_REVIEW_REQUIRED'})
        self.assertIsNone(result['identifier'])
        self.assertFalse(result['price_search_ready'])

    def test_sync_does_not_restore_old_guessed_model(self):
        from pipeline.mvp_exact_batch import _sheet_payload
        payload = _sheet_payload({'item_number': 3, 'name': CABLE,
            'model': {'model': 'DIN 13', 'route': 'exact'},
            'business_order_result': {'positions': [{'position_number': 3, 'selected_model': None}]}})
        self.assertIsNone(payload['model']['selected_model'])
        self.assertEqual(payload['item']['display_name'], CABLE)

    def test_generic_printer_has_no_selected_model(self):
        self.assertIsNone(resolve_item_sources({'name': 'Принтер лазерный'})['original_model'])

    def test_ai_must_quote_customer_model(self):
        data = dict(selected_model=CABLE, evidence=CABLE, requirements=[], uncertainties=[])
        self.assertEqual(validate_intake(data, CABLE)['selected_model'], CABLE)
        with self.assertRaises(ValueError):
            validate_intake({**data, 'selected_model': 'Gonsin выдуманный'}, CABLE)

    def test_ai_requirement_requires_source(self):
        data = dict(selected_model=None, evidence='', requirements=[{
            'parameter': 'Длина', 'value': '9 м', 'evidence': 'Длина: 9 м'}], uncertainties=[])
        with self.assertRaises(ValueError):
            validate_intake(data, 'Длина: 2.7 м')

    def test_explicit_customer_designation_survives_missing_unspecified_characteristic(self):
        from pipeline.product_price_control import prepare_product
        analysis = dict(selected_model=CABLE, evidence=CABLE, requirements=[{
            'parameter': 'Разъём', 'value': 'DIN 13-PIN',
            'evidence': '(DIN 13-PIN)'}],
            uncertainties=['Не указана длина кабеля'])
        result = prepare_product(
            {'item': {'name': CABLE, 'position_kind': 'goods'}, 'documents': []},
            intake=lambda _: analysis)
        self.assertEqual(result['original_model'], CABLE)
        self.assertEqual(result['model_search_mode'], 'EXACT_MODEL')
        self.assertFalse(result['model_discovery_allowed'])
        self.assertEqual(result['ai_uncertainties'], ['Не указана длина кабеля'])

    def test_retry_only_after_other_jobs(self):
        calls = []
        def search(job):
            calls.append(job['id'])
            if calls == ['a']:
                raise TimeoutError()
            return {'search_status': 'completed'}
        with tempfile.TemporaryDirectory() as directory:
            state = run_deferred([{'id': 'a'}, {'id': 'b'}], search,
                                 Path(directory) / 'queue.json', sleep=lambda _: None)
        self.assertEqual(calls, ['a', 'b', 'a'])
        self.assertEqual(state['jobs'][0]['status'], 'completed')

    def test_captcha_not_automatically_retried(self):
        search = MagicMock(side_effect=PermissionError())
        with tempfile.TemporaryDirectory() as directory:
            state = run_deferred([{'id': 'a'}], search, Path(directory) / 'queue.json')
        self.assertEqual(search.call_count, 1)
        self.assertEqual(state['jobs'][0]['status'], 'requires_manual_check')

    def test_retry_preserves_first_result(self):
        initial = {'offers': [{'price': 123}], 'source_errors': [{'error': 'TimeoutError'}]}
        search = MagicMock(side_effect=[initial, {'search_status': 'incomplete'}])
        with tempfile.TemporaryDirectory() as directory:
            state = run_deferred([{'id': 'a'}], search, Path(directory) / 'queue.json', sleep=lambda _: None)
        self.assertEqual(state['jobs'][0]['previous_result'], initial)

    def test_google_protection_stops_all_further_queries(self):
        research = MagicMock()
        page = research.context.new_page.return_value
        page.url = 'https://www.google.com/sorry/index'
        page.goto.return_value.status = 200
        page.locator.return_value.inner_text.return_value = 'not a robot'
        provider = GoogleBrowserSearch(research)
        for _ in range(2):
            with self.assertRaises(PermissionError):
                provider.search('test')
        self.assertEqual(page.goto.call_count, 1)

    def test_google_keeps_only_external_results(self):
        research = MagicMock()
        page = research.context.new_page.return_value
        page.url = 'https://www.google.com/search?q=test'
        page.goto.return_value.status = 200
        page.locator.return_value.inner_text.return_value = 'Results'
        page.locator.return_value.evaluate_all.return_value = [
            {'url': 'https://shop.example/product/1', 'title': 'Товар'},
            {'url': 'https://google.com/search?q=other', 'title': 'Ещё'}]
        result = GoogleBrowserSearch(research).search('test')
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]['snippet_is_evidence'])

    def test_google_http_403_is_classified_with_safe_url(self):
        research = MagicMock()
        page = research.context.new_page.return_value
        page.url = 'https://www.google.com/search?q=secret-token'
        page.goto.return_value.status = 403
        page.locator.return_value.inner_text.return_value = 'Access denied'
        provider = GoogleBrowserSearch(research)
        with self.assertRaises(BlockedSourceError) as caught:
            provider.search('test')
        diagnostic = caught.exception.diagnostic
        self.assertEqual(diagnostic['classification'], 'http_403')
        self.assertEqual(diagnostic['http_status'], 403)
        self.assertNotIn('secret-token', diagnostic['url'])

    def test_google_manual_captcha_continuation_uses_same_page(self):
        research = MagicMock()
        page = research.context.new_page.return_value
        page.url = 'https://www.google.com/sorry/index'
        page.goto.return_value.status = 200
        page.locator.return_value.inner_text.side_effect = ['not a robot', 'Results', 'Results']
        page.locator.return_value.evaluate_all.return_value = []
        provider = GoogleBrowserSearch(research)
        with self.assertRaises(PermissionError):
            provider.search('test')
        page.url = 'https://www.google.com/search'
        cleared = provider.continue_after_manual_check()
        self.assertEqual(cleared['reason'], 'captcha_cleared')
        self.assertFalse(provider.failed)
        provider.search('test')
        self.assertEqual(page.goto.call_count, 2)

    def test_deferred_rows_distinguish_primary_and_deferred_google_block(self):
        result = {'search_status': 'incomplete', 'query_log': [
            {'error': 'PermissionError', 'diagnostic': {
                'stage': 'google_search', 'classification': 'captcha_or_robot_check',
                'primary': True}},
            {'error': 'PermissionError', 'diagnostic': {
                'stage': 'google_search_deferred', 'classification': 'deferred_due_to_primary_block',
                'primary': False}}]}
        def search(job):
            return result
        with tempfile.TemporaryDirectory() as directory:
            state = run_deferred([{'id': 'a'}], search, Path(directory) / 'queue.json')
        row = state['jobs'][0]
        self.assertTrue(row['primary_google_block'])
        self.assertTrue(row['search_deferred_due_to_primary_block'])

    def test_completed_top3_is_not_downgraded_by_unrelated_blocked_source(self):
        result = {'search_status': 'completed', 'eligible_offers': [{}, {}, {}],
                  'source_errors': [{'error': 'HTTP BlockedSourceError', 'diagnostic': {
                      'stage': 'product_page_http', 'classification': 'http_403',
                      'primary': True}}]}
        with tempfile.TemporaryDirectory() as directory:
            state = run_deferred([{'id': 'a'}], lambda _: result,
                                 Path(directory) / 'queue.json')
        self.assertEqual(state['jobs'][0]['status'], 'completed')

    def test_cable_formatting_difference_is_accepted(self):
        data = dict(selected_model='Gonsin 13Р–Т3', evidence='13Р-Т3 Gonsin',
                    requirements=[], uncertainties=[])
        self.assertEqual(validate_intake(data, CABLE)['selected_model'], 'Gonsin 13Р–Т3')

    def test_search_reaches_six_and_selects_cheapest_three(self):
        from model_search.playwright_provider import PlaywrightResearch
        from tests.test_playwright_provider import PAGE
        research = PlaywrightResearch(MagicMock())
        prices = [32000, 31000, 30000, 29000, 28000, 27000, 26000]
        provider = MagicMock()
        provider.name = 'google_browser'
        provider.search.return_value = [{'url': f'https://shop{i}.example/product/ma3500x'} for i in range(7)]
        research.read_http = lambda url: (PAGE.replace('32891', str(prices[int(url.split('shop')[1].split('.')[0])])), url, 200)
        with tempfile.TemporaryDirectory() as directory:
            result = research.prices_exact('Kyocera Ecosys MA3500X', provider, Path(directory)/'prices.json')
        self.assertEqual(result['sources_checked'], 6)
        self.assertEqual([r['price'] for r in result['top3_confirmed_prices']], [27000, 28000, 29000])

    def test_codex_adapter_uses_schema_and_no_api_key(self):
        from ai.codex_intake import extract_product
        data = dict(selected_model=CABLE, evidence=CABLE, requirements=[], uncertainties=[])
        def runner(args, **kwargs):
            self.assertIn('--output-schema', args)
            self.assertIn('read-only', args)
            self.assertIn('gpt-5.6-luna', args)
            Path(args[args.index('-o')+1]).write_text(json.dumps(data), encoding='utf-8')
            return MagicMock(returncode=0)
        with patch('ai.codex_intake.shutil.which', return_value='codex'):
            self.assertEqual(extract_product(CABLE, runner=runner), data)

    def test_ai_cannot_promote_connector(self):
        with self.assertRaises(ValueError):
            validate_intake(dict(selected_model='DIN 13', evidence=CABLE,
                                 requirements=[], uncertainties=[]), CABLE)

    def test_unknown_availability_is_reserve_in_control(self):
        from model_search.playwright_provider import PlaywrightResearch
        from tests.test_playwright_provider import PAGE
        research = PlaywrightResearch(MagicMock())
        research.read_http = lambda url: (PAGE.replace(',"availability":"https://schema.org/InStock"', ''), url, 200)
        provider = MagicMock()
        provider.search.return_value = [{'url': 'https://shop.example/product/ma3500x'}]
        with tempfile.TemporaryDirectory() as directory:
            result = research.prices_exact('Kyocera Ecosys MA3500X', provider,
                                           Path(directory)/'prices.json', require_complete_offers=True)
        self.assertEqual(len(result['price_candidates']), 1)
        self.assertEqual(result['eligible_offers'], [])
        self.assertEqual(result['search_status'], 'incomplete')

    def test_continuation_reuses_successful_card_and_reads_only_new_url(self):
        from model_search.playwright_provider import PlaywrightResearch
        from tests.test_playwright_provider import PAGE
        research = PlaywrightResearch(MagicMock())
        reads = []
        def read(url):
            reads.append(url)
            price = '32000' if 'old.example' in url else '31000'
            return PAGE.replace('32891', price), url, 200
        research.read_http = read
        first_provider = MagicMock(name='first_provider')
        first_provider.name = 'yandex_search_api'
        first_provider.search.return_value = [
            {'url': 'https://old.example/product/ma3500x'}]
        with tempfile.TemporaryDirectory() as directory:
            first = research.prices_exact(
                'Kyocera Ecosys MA3500X', first_provider,
                Path(directory) / 'first.json', query_variants=['старый запрос'],
                target_offers=3)
            reads.clear()
            second_provider = MagicMock(name='second_provider')
            second_provider.name = 'yandex_search_api'
            second_provider.search.return_value = [
                {'url': 'https://old.example/product/ma3500x'},
                {'url': 'https://new.example/product/ma3500x'}]
            second = research.prices_exact(
                'Kyocera Ecosys MA3500X', second_provider,
                Path(directory) / 'second.json', query_variants=['новый запрос'],
                prior_result=first, target_offers=3)
        self.assertEqual(reads, ['https://new.example/product/ma3500x'])
        self.assertEqual(len(second['offers']), 2)
        self.assertEqual(second['continuation']['reused_offers'], 1)

    def test_direct_continuation_uses_short_page_identity_but_keeps_customer_name(self):
        from model_search.playwright_provider import PlaywrightResearch
        page = '''<html><head><title>BB Battery BP5-12</title>
        <script type="application/ld+json">{"@type":"Product","name":"BB Battery BP5-12",
        "offers":{"@type":"Offer","price":"2957","priceCurrency":"RUB",
        "availability":"https://schema.org/InStock"}}</script></head>
        <body><h1>BB Battery BP5-12</h1><div>В наличии</div></body></html>'''
        research = PlaywrightResearch(MagicMock())
        research.read_http = lambda url: (page, url, 200)
        provider = MagicMock()
        provider.name = 'yandex_search_api'
        full_name = 'Батарея BB Battery BP 5-12'
        prior = {'source_errors': [{
            'url': 'https://shop.example/product/bp5-12',
            'error': 'Страница не подтверждает точную модель и марку'}]}
        with tempfile.TemporaryDirectory() as directory:
            result = research.prices_exact(
                full_name, provider, Path(directory) / 'prices.json',
                query_variants=[], candidate_urls=['https://shop.example/product/bp5-12'],
                page_match_model='BB Battery BP5-12', target_offers=1,
                require_complete_offers=True, prior_result=prior)
        provider.search.assert_not_called()
        self.assertEqual(result['target_model'], full_name)
        self.assertEqual(result['page_match_model'], 'BB Battery BP5-12')
        self.assertEqual(result['eligible_offers'][0]['price'], 2957.0)
        self.assertEqual(result['source_errors'], [])
        self.assertEqual(len(result['resolved_source_errors']), 1)

    def test_connector_plural_confirms_din_requirement(self):
        from model_search.compliance import assess_model
        page = {'heading': 'Gonsin 13P-T3', 'title': 'Gonsin 13P-T3',
                'url': 'https://shop.example/product/13p-t3',
                'text': 'Gonsin 13P-T3\nРазъемы : DIN 13-pin'}
        result = assess_model(
            [{'requirement_name': 'Разъём', 'value': 'DIN 13-PIN'}],
            [page], 'Gonsin 13P-T3')
        self.assertEqual(result['status'], 'fully_compliant')


if __name__ == '__main__':
    unittest.main()
