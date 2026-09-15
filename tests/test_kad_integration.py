"""Минимальная production-интеграция КАД: синтетические страницы, без LIVE."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from suppliers.arbitration import RESULT_FIELDS, defendant_kad_result, unavailable_kad_result
from suppliers.kad_client import KADClient, parse_defendant_search_page
from suppliers.live_verification import verify_live_supplier
from suppliers.verification import MANUAL, verify_supplier

INN = "5904993922"
OTHER = "7701097787"
CARD_URL = "https://kad.arbitr.ru/Card/12345678-1234-1234-1234-123456789abc"
# Старые подробные fixtures нужны только тестам инструмента разработки kad_diagnostic.
SEARCH = f'''<input id="documentsTotalCount" value="1"><table id="b-cases"><tr><td>
<time>28.08.2026</time><a href="{CARD_URL}">А40-123/2026</a><span title="Гражданские"></span>
</td><td>Другие участники</td></tr></table>'''
CARD = f'''<dl><dt>Истцы</dt><dd>ООО «Другая компания», ИНН {OTHER}</dd>
<dt>Ответчики</dt><dd>ООО «Поставщик», ИНН {INN}</dd>
<dt>Статус дела</dt><dd>Рассматривается</dd><dt>Решение</dt><dd>Производство прекращено</dd></dl>'''
EMPTY = '<input id="documentsTotalCount" value="0">'


def verified(kad=None, domain="shop.example"):
    return verify_supplier({"product_url": f"https://{domain}/product", "verification_checks": {
        "domain_age_years": 8, "requisites_consistent": True, "current_seller_determined": True,
        "company": {"inn": INN, "active": True, "director_changed_within_6_months": False},
        **({"kad_check": kad} if kad is not None else {})}})


def row(index=1, role="Ответчик", inn=INN):
    url = f"https://kad.arbitr.ru/Card/{index:08x}-1234-1234-1234-123456789abc"
    return f'<tr><td><a href="{url}">А40-{index}/2026</a></td><td data-role="{role}">ИНН {inn}</td></tr>'


def page(rows, total=None, pages=1):
    total = len(rows) if total is None else total
    return f'<input id="documentsTotalCount" value="{total}"><input id="documentsPagesCount" value="{pages}"><table>{"".join(rows)}</table>'


class KADIntegrationTests(unittest.TestCase):
    def test_role_matching_counts_defendants_only_and_preserves_multiple_roles(self):
        html = page([row(1), row(2, "Истец"), row(3, "Третье лицо"), row(4, "Иная роль"),
                     row(5).replace('</tr>', f'<td data-role="Истец">ИНН {INN}</td></tr>')])
        with tempfile.TemporaryDirectory() as directory:
            client = KADClient(cache_dir=directory, http_reader=lambda url, payload: (html if payload else "КАД", 200))
            result = client.check(INN)
        self.assertEqual(result["defendant_cases_count"], 2)
        self.assertEqual(result["kad_status"], "YELLOW")

    def test_header_column_binds_role_to_our_inn_not_other_defendant(self):
        html = page([f'<tr><td><a href="{CARD_URL}">А40-1/2026</a></td><td>ИНН {INN}</td><td>ИНН {OTHER}</td></tr>'])
        html = html.replace('<table>', '<table><tr><th>Дело</th><th>Истцы</th><th>Ответчики</th></tr>')
        parsed = parse_defendant_search_page(html, INN)
        self.assertFalse(parsed["ambiguous"])
        self.assertEqual(list(parsed["participants"].values()), [False])
        complex_header = html.replace('<th>Истцы</th>', '<th colspan="2">Истцы</th>')
        self.assertTrue(parse_defendant_search_page(complex_header, INN)["ambiguous"])

    def test_unmatched_inn_or_role_returns_yellow_without_cards(self):
        for html in (page([row(1, inn=OTHER)]), SEARCH, page([row(1, "Решение")])):
            with self.subTest(html=html), tempfile.TemporaryDirectory() as directory:
                reader = Mock(side_effect=lambda url, payload: (html if payload else "КАД", 200))
                result = KADClient(cache_dir=directory, http_reader=reader).check(INN)
                self.assertEqual(result["technical_status"], "KAD_REQUIRES_MANUAL_CHECK")
                self.assertIsNone(result["defendant_cases_count"])
                self.assertEqual(reader.call_count, 2)

    def test_http_search_has_current_inn_and_persists_only_minimal_result(self):
        requests = []
        def read(url, payload):
            requests.append((url, payload))
            return (page([row()]) if payload else "КАД"), 200
        with tempfile.TemporaryDirectory() as directory:
            client = KADClient(cache_dir=directory, http_reader=read)
            result = client.check(INN)
            saved = json.loads(next((Path(directory) / INN).glob('*.json')).read_text())
            self.assertEqual(set(saved["result"]), set(RESULT_FIELDS))
            self.assertEqual(saved["result"], result)
            self.assertEqual(saved["schema_version"], 2)
            self.assertIsNone(client.collection)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[1][1]["Sides"], [{"Name": INN, "Type": -1, "ExactMatch": True}])
        self.assertNotIn("/Card/", json.dumps(saved))
        self.assertNotIn("А40-", json.dumps(saved))

    def test_pagination_deduplicates_and_pauses_without_fetching_cards(self):
        calls = []
        def read(url, payload):
            calls.append(payload)
            return (page([row(1)], total=2, pages=2) if payload and payload["Page"] == 1 else
                    page([row(1), row(2)], total=2, pages=2) if payload else "КАД"), 200
        with tempfile.TemporaryDirectory() as directory:
            client = KADClient(cache_dir=directory, http_reader=read)
            client._pause = Mock()
            result = client.check(INN)
            client._pause.assert_called_once()
        self.assertEqual(result["defendant_cases_count"], 2)
        self.assertEqual(len(calls), 3)

    def test_incomplete_changed_or_unconfirmed_results_are_not_zero(self):
        for html in ("<body>Дел нет</body>", page([], total=1), page([row()], total=2, pages=2),
                     EMPTY + '<input id="totalCount" value="1">'):
            with self.subTest(html=html), tempfile.TemporaryDirectory() as directory:
                client = KADClient(cache_dir=directory, http_reader=lambda url, payload: (html if payload else "КАД", 200))
                client._pause = Mock()
                with patch.dict('suppliers.kad_client.CONFIG', {"max_search_pages": 1}):
                    result = client.check(INN)
                self.assertEqual(result["kad_status"], "YELLOW")
                self.assertIsNone(result["defendant_cases_count"])
        with tempfile.TemporaryDirectory() as directory:
            client = KADClient(cache_dir=directory)
            with self.assertRaisesRegex(ValueError, "изменился"):
                client._pause = Mock()
                client._collect_pages(INN, lambda n: page([row()], total=n, pages=2))

    def test_confirmed_zero_needs_no_card_or_role_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            client = KADClient(cache_dir=directory, http_reader=lambda url, payload: (EMPTY if payload else "КАД", 200))
            result = client.check(INN)
        self.assertEqual(result["technical_status"], "KAD_CHECKED")
        self.assertEqual(result["defendant_cases_count"], 0)

    def test_block_or_timeout_stops_without_fallback_and_is_cached_with_reason(self):
        for value in (("Доступ заблокирован", 451), ("CAPTCHA", 200), TimeoutError("timeout")):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                reader = Mock(side_effect=value if isinstance(value, Exception) else None,
                              return_value=value if isinstance(value, tuple) else None)
                browser = Mock(side_effect=AssertionError("Повтор запрещён"))
                client = KADClient(cache_dir=directory, http_reader=reader, browser_collector=browser)
                result = client.check(INN)
                self.assertEqual(result, client.check(INN))
                self.assertEqual(result["technical_status"], "KAD_REQUIRES_MANUAL_CHECK")
                self.assertIsNone(result["defendant_cases_count"])
                if isinstance(value, tuple) and value[0] == "CAPTCHA":
                    self.assertIn("captcha", result["reason"].casefold())
                reader.assert_called_once()
                browser.assert_not_called()

    def test_cache_default_ttl_explicit_ttl_and_legacy_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = Mock(return_value=(EMPTY, 200))
            first = KADClient(cache_dir=directory, run_id="one", http_reader=reader).check(INN)
            cached = KADClient(cache_dir=directory, run_id="two", cache_max_age_hours=24,
                               http_reader=Mock(side_effect=AssertionError("Запрос лишний"))).check(INN)
            self.assertEqual(cached, first)
            fresh = Mock(return_value=(EMPTY, 200))
            KADClient(cache_dir=directory, run_id="two", http_reader=fresh).check(INN)
            self.assertEqual(fresh.call_count, 2)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / INN
            folder.mkdir()
            (folder / "old.json").write_text(json.dumps({"schema_version": 1, "run_id": "one", "result": first}))
            reader = Mock(return_value=(EMPTY, 200))
            KADClient(cache_dir=directory, run_id="one", http_reader=reader).check(INN)
            self.assertEqual(reader.call_count, 2)

    def test_live_current_seller_company_before_kad_and_minimal_evidence(self):
        order = []
        def company(inn):
            order.append(("company", inn))
            return {"inn": inn, "active": True, "source": "ФНС"}
        client = Mock()
        client.check.side_effect = lambda inn: order.append(("kad", inn)) or defendant_kad_result(inn, 1)
        def reader(url):
            return f"<body>Реквизиты ИНН {INN}</body>", url, 200
        with tempfile.TemporaryDirectory() as directory, patch('suppliers.live_verification.rdap_check', return_value={}), \
                patch('suppliers.live_verification.wayback_check', return_value={}):
            result = verify_live_supplier({"product_url": "https://shop.example/product"}, reader=reader,
                                         kad_client=client, company_checker=company, evidence_directory=directory)
            evidence = json.loads(Path(result["kad_evidence_path"]).read_text())
            self.assertEqual(set(evidence), set(RESULT_FIELDS))
            self.assertEqual(order, [("company", INN), ("kad", INN)])
            self.assertEqual(result["verification_status"], MANUAL)
            self.assertEqual(set(result["verification_checks"]["kad_check"]), set(RESULT_FIELDS))

    def test_ambiguous_seller_skips_kad_and_known_seller_unavailable_is_yellow(self):
        client = Mock()
        with tempfile.TemporaryDirectory() as directory, patch('suppliers.live_verification.rdap_check', return_value={}), \
                patch('suppliers.live_verification.wayback_check', return_value={}):
            result = verify_live_supplier({"product_url": "https://shop.example/product"},
                reader=lambda url: (f"ИНН {INN} ИНН {OTHER}", url, 200),
                kad_client=client, evidence_directory=directory)
            self.assertEqual(result["verification_status"], MANUAL)
            client.check.assert_not_called()
        for domain in ("shop.example", "citilink.ru"):
            for kad in (None, unavailable_kad_result(INN, "HTTP 451"), defendant_kad_result(OTHER, 0)):
                self.assertEqual(verified(kad, domain)["verification_status"], MANUAL)

    def test_production_browser_never_opens_card_and_checks_sent_inn(self):
        from tests.test_kad_diagnostic import context
        for inn, status, filters in ((INN, "KAD_CHECKED", {}), (OTHER, "KAD_REQUIRES_MANUAL_CHECK", {}),
                                     (INN, "KAD_REQUIRES_MANUAL_CHECK", {"DateFrom": "2026-01-01"})):
            ctx, root, card = context(search_html=page([row()]), sent_inn=inn, payload_extra=filters)
            with tempfile.TemporaryDirectory() as directory:
                result = KADClient(context=ctx, cache_dir=directory).check(INN)
            self.assertEqual(result["technical_status"], status)
            self.assertEqual(ctx.new_page.call_count, 1)
            card.goto.assert_not_called()
            root.get_by_role.return_value.click.assert_called_once()
