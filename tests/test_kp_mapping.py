"""Профильные проверки КП без сети и записи UNIT в Google Sheets."""
import re
import unittest
from unittest.mock import MagicMock, patch

from google_sheets.kp_schema import kp_headers, plan_kp_schema
from google_sheets.production_upsert import EXTRA_HEADERS, row_values, upsert_live_payload
from google_sheets.workbook import ACTIVE_HEADERS, _column_letter, quote_formulas
from google_sheets.client import PRESERVED_ACTIVE_HEADERS, _rewrite_text_as_raw, _upsert
from gspread.utils import a1_to_rowcol
from model_search.live_price_search import inspect_product_url
from suppliers.exact_model_flow import exact_supplier_flow
from suppliers.price_search_flow import confirmed_price_ranking, select_verified_top3
from suppliers.verification import HIGH_RISK, MANUAL, PASSED
from tests.test_google_sheets_formulas import _payload

RUN = '2026-09-15T12:00:00+00:00'


def offer(seller, unit_price, **fields):
    return {'seller': seller, 'supplier_name': seller,
            'url': f'https://{seller}.example/product/pantum-m6607nw',
            'product_url': f'https://{seller}.example/product/pantum-m6607nw',
            'price': unit_price, 'public_price': unit_price, 'exact_model_match': True,
            'product_page_available': True, 'price_confirmed_on_product_page': True,
            'availability': 'В наличии', 'checked_at': RUN, 'price_run_id': RUN,
            'verification_status': PASSED, **fields}


def headers():
    old = list(ACTIVE_HEADERS) + EXTRA_HEADERS
    return plan_kp_schema(old, 1)[0]


def payload(offers):
    data = _payload()
    data['supplier_search'].update(confirmed_offers=offers, price_run_id=RUN)
    return data


def mapped(offers):
    h = headers()
    return dict(zip(h, row_values(payload(offers), h)))


def written_fields(ws, h, existing):
    result = dict(existing)
    for call in ws.batch_update.call_args_list:
        for entry in call.args[0]:
            start = a1_to_rowcol(entry['range'].split(':')[0])[1] - 1
            for index, value in enumerate(entry['values'][0], start):
                result[h[index]] = value
    return result


def evaluate_quote(formula, h, price, qty=3, nmck=87390, fee=2665.4, extras=1000):
    """Вычислить арифметическую ветку реальной формулы для одной строки."""
    expression = formula.split(';"";IF(', 1)[1][:-2]
    expression = 'IF(' + expression + ')'
    expression = re.sub(r'SUMPRODUCT\([^)]*\)\*[^)]*\)', str(price * qty), expression)
    expression = re.sub(r'SUMIF\([^)]*\)', str(extras), expression)
    for name, value in [('НМЦК, ₽', nmck), ('Комиссия площадки, ₽', fee)]:
        expression = expression.replace(f'{_column_letter(h.index(name))}2', str(value))
    expression = re.sub(r'(\d),(\d)', r'\1.\2', expression)
    expression = expression.replace(';', ',').replace('=0', '==0')
    return eval(expression, {'__builtins__': {}},
                {'IF': lambda condition, yes, no: yes if condition else no,
                 'MIN': min, 'MAX': max})


class KPMappingTests(unittest.TestCase):
    def test_sort_and_price_url_supplier_pair_are_preserved(self):
        offers = [offer('c', 27000), offer('a', 24000), offer('b', 26000)]
        row = mapped(offers)
        for n, source in enumerate(sorted(offers, key=lambda o: o['price']), 1):
            self.assertEqual(row[f'Цена КП {n}'], source['price'])
            self.assertEqual(row[f'Ссылка на товар КП {n}'], source['url'])
            self.assertEqual(row[f'Поставщик КП {n}'], source['seller'])

    def test_each_check_follows_its_supplier(self):
        row = mapped([offer('manual', 25000, verification_status=MANUAL, risk_flags=['На сайте не найден ИНН']),
                      offer('passed', 24000)])
        self.assertEqual(row['Проверка поставщика КП 1'], '🟢 ПРОШЁЛ')
        self.assertEqual(row['Поставщик КП 2'], 'manual')
        self.assertEqual(row['Проверка поставщика КП 2'], '🟡 РУЧНАЯ ПРОВЕРКА — не найден ИНН на сайте')

    def test_red_replaced_after_economic_gate_and_kept_in_history(self):
        calls = []
        def verify(source):
            calls.append(source['supplier_name'])
            return {**source, 'verification_status': HIGH_RISK if source['seller'] == 'red' else PASSED}
        flow = exact_supplier_flow({'price': 200000, 'commissionFee': 6000}, [{'position_number': 1, 'quantity': 2,
            'source_offers': [offer('d', 27000), offer('red', 20000), offer('b', 25000), offer('c', 26000)]}], verifier=verify)
        self.assertEqual(calls, ['red', 'b', 'c', 'd'])
        row = mapped(flow['positions'][0]['offers'])
        self.assertEqual([row[f'Поставщик КП {n}'] for n in range(1, 4)], ['b', 'c', 'd'])
        self.assertEqual(flow['antifraud_history'][0]['verification_status'], HIGH_RISK)

    def test_unknown_stock_only_after_confirmed_candidates(self):
        ranked = confirmed_price_ranking([offer('unknown', 100), offer('a', 200), offer('b', 300), offer('c', 400)])
        ranked[0]['availability'] = None
        top, history = select_verified_top3(ranked, verifier=lambda row: row)
        self.assertEqual([r['seller'] for r in top], ['a', 'b', 'c'])
        self.assertEqual(len(history), 3)

    def test_unknown_stock_can_fill_gap_and_final_prices_sorted(self):
        ranked = confirmed_price_ranking([offer('unknown', 100, availability=None), offer('a', 200), offer('b', 300, availability='Под заказ')])
        top, _ = select_verified_top3(ranked, verifier=lambda row: row)
        self.assertEqual([r['price'] for r in top], [100, 200, 300])

    def test_two_offers_leave_entire_third_block_empty(self):
        row = mapped([offer('a', 24000), offer('b', 25000)])
        for name in kp_headers(3):
            self.assertEqual(row[name], '')
        self.assertIn('Найдено только 2 актуальных подтверждённых предложения', row['Текущий итог просчета и анализа'])

    def test_invoice_never_uses_product_link(self):
        row = mapped([offer('a', 24000), offer('b', 25000, invoice_url='https://b.example/invoice/123.pdf')])
        self.assertEqual(row['Ссылка на счёт КП 1'], '')
        self.assertEqual(row['Ссылка на счёт КП 2'], 'https://b.example/invoice/123.pdf')

    def test_unconfirmed_old_red_and_non_product_pages_rejected(self):
        for changes in [{'availability': 'Архив'}, {'availability': 'Снят с продажи'},
                        {'availability': 'Нет в наличии'}, {'exact_model_match': False},
                        {'price_confirmed_on_product_page': False}, {'product_page_available': False},
                        {'price_run_id': 'previous-run'}, {'stale_price': True}, {'is_accessory': True},
                        {'verification_status': HIGH_RISK}, {'verification_skipped': True},
                        {'url': 'https://yandex.ru/search/?text=Pantum'},
                        {'url': 'https://shop.example/'}, {'url': 'https://shop.example/category/mfu'},
                        {'price': float('nan'), 'public_price': float('nan')}]:
            with self.subTest(changes=changes):
                self.assertEqual(mapped([offer('bad', 24000, **changes)])['Цена КП 1'], '')

    def test_duplicate_supplier_uses_one_card_with_its_price(self):
        first = offer('a', 25000)
        cheaper = offer('a', 24000, url='https://a.example/product/another-card')
        row = mapped([first, cheaper, offer('b', 26000)])
        self.assertEqual(row['Цена КП 1'], 24000)
        self.assertEqual(row['Ссылка на товар КП 1'], cheaper['url'])
        self.assertEqual(row['Поставщик КП 2'], 'b')

    def test_verifier_cannot_replace_price_or_product_link(self):
        top, _ = select_verified_top3(confirmed_price_ranking([offer('a', 24000)]),
            verifier=lambda row: {'verification_status': PASSED, 'public_price': 1, 'product_url': 'https://other.example/x'})
        self.assertEqual(top[0]['public_price'], 24000)
        self.assertEqual(top[0]['product_url'], offer('a', 24000)['url'])

    def test_schema_reuses_legacy_columns_and_is_idempotent(self):
        old = list(ACTIVE_HEADERS)
        for n in range(1, 4):
            old.extend([f'ПОСТАВЩИК №{n}', f'ПРОВЕРКА ПОСТАВЩИКА №{n}'])
        new, requests = plan_kp_schema(old, 1)
        self.assertEqual(len(new), len(old))
        self.assertFalse(any('insertDimension' in r for r in requests))
        for n in range(1, 4):
            start = new.index(f'Поставщик КП {n}')
            self.assertEqual(new[start:start+6], kp_headers(n))
        self.assertEqual(plan_kp_schema(new, 1), (new, []))

    def test_schema_adds_only_genuinely_missing_columns(self):
        new, requests = plan_kp_schema(list(ACTIVE_HEADERS), 1)
        self.assertEqual(len(new), len(ACTIVE_HEADERS) + 6)
        self.assertEqual(sum('insertDimension' in r for r in requests), 6)
        self.assertEqual(len(new), len(set(new)))

    def test_quote_profitability_evaluates_own_price_expenses_fee_and_reserve(self):
        h = headers()
        fs = quote_formulas(2, h)
        values = []
        for n, price in enumerate([24554, 25635, 27500], 1):
            f = fs[h.index(f'Рентабельность КП {n}, %')]
            self.assertIn(f'${_column_letter(h.index(f"Цена КП {n}"))}$2:', f)
            for other in {1, 2, 3} - {n}:
                self.assertNotIn(f'${_column_letter(h.index(f"Цена КП {other}"))}$2:', f)
            actual = evaluate_quote(f, h, price)
            cost = price * 3 + 2665.4 + 1000
            bid = cost * 1.18
            tax = max(0, (bid-cost)*.15)
            total = cost + tax
            expected = (bid-total)/cost
            self.assertAlmostEqual(actual, expected)
            values.append(actual)
        self.assertAlmostEqual(values[0], values[1])
        self.assertAlmostEqual(values[1], values[2])

    def test_quote_guards_missing_inputs_and_follows_reordered_headers(self):
        h = list(reversed(headers()))
        for n in range(1, 4):
            f = quote_formulas(2, h)[h.index(f'Рентабельность КП {n}, %')]
            for name in ['Количество', f'Цена КП {n}', 'Дополнительные расходы, ₽']:
                self.assertIn(f'N(ISNUMBER(${_column_letter(h.index(name))}$2:', f)
            self.assertIn(';"";', f)

    def test_repeat_upsert_updates_complete_blocks_and_preserves_manual_expenses(self):
        h = headers()
        old = {name: '' for name in h}
        old.update({'ID закупки': 'uuid', '№ позиции': 1, 'Дополнительные расходы, ₽': 1000,
                    'Цена КП 1': 999, 'Ссылка на товар КП 1': 'https://old.example/product/old',
                    'Цена КП 3': 999, 'Ссылка на счёт КП 3': 'https://old.example/invoice/old'})
        ws, book, client = MagicMock(), MagicMock(), MagicMock()
        ws.row_values.return_value = h
        ws.get_all_values.return_value = [h, [old[name] for name in h]]
        ws.col_count = len(h)
        book.worksheet.return_value = ws
        client.open_by_key.return_value = book
        with patch('google_sheets.production_upsert.authorize_service_account', return_value=(client, 'local-test')):
            upsert_live_payload(payload([offer('a', 24000), offer('b', 25000)]))
        written = written_fields(ws, h, old)
        self.assertEqual(written['Цена КП 1'], 24000)
        self.assertEqual(written['Ссылка на товар КП 1'], offer('a', 24000)['url'])
        self.assertEqual(written['Цена КП 3'], '')
        self.assertEqual(written['Ссылка на счёт КП 3'], '')
        self.assertEqual(written['Дополнительные расходы, ₽'], 1000)
        self.assertTrue(written['Рентабельность КП 1, %'].startswith('='))

    def test_current_page_negative_availability_overrides_jsonld(self):
        for marker, expected in [('Архивный товар', 'Архив'), ('Снят с продажи', 'Снят с продажи'), ('Нет в наличии', 'Нет в наличии')]:
            html = '<h1>Pantum M6607NW</h1><p>' + marker + '</p><script type="application/ld+json">{"@type":"Product","name":"Pantum M6607NW","offers":{"price":24000,"priceCurrency":"RUB","availability":"https://schema.org/InStock"}}</script>'
            row = inspect_product_url({'product_url': offer('a', 1)['url']}, 'Pantum M6607NW', fetcher=lambda url: (html, url, 200))
            self.assertEqual(row['availability'], expected)
            self.assertEqual(confirmed_price_ranking([row]), [])

    def test_general_collection_upsert_preserves_existing_kp_block(self):
        h = headers()
        data = mapped([offer('a', 24000)])
        data.update({'ID закупки': 'uuid', '№ позиции': 1})
        ws = MagicMock()
        from google_sheets.workbook import ACTIVE
        ws.title = ACTIVE
        ws.get_all_values.return_value = [h, [data[name] for name in h]]
        incoming = [list(ACTIVE_HEADERS), row_values(payload([]), list(ACTIVE_HEADERS))]
        _upsert(ws, incoming, ('ID закупки', '№ позиции'), PRESERVED_ACTIVE_HEADERS)
        written = written_fields(ws, h, data)
        self.assertEqual(written['Поставщик КП 1'], 'a')
        self.assertEqual(written['Цена КП 1'], 24000)
        _rewrite_text_as_raw(ws, incoming, ('ID закупки', '№ позиции'))
        cells = {entry['range']: entry['values'][0][0] for entry in ws.batch_update.call_args.args[0]}
        self.assertEqual(cells[f'{_column_letter(h.index("Ссылка на товар КП 1"))}2'], data['Ссылка на товар КП 1'])

    def test_structured_discontinued_is_rejected_without_visible_warning(self):
        for state in ['Discontinued', 'OutOfStock', 'SoldOut']:
            html = '<h1>Pantum M6607NW</h1><script type="application/ld+json">{"@type":"Product","name":"Pantum M6607NW","offers":{"price":24000,"priceCurrency":"RUB","availability":"https://schema.org/' + state + '"}}</script>'
            row = inspect_product_url({'product_url': offer('a', 1)['url']}, 'Pantum M6607NW', fetcher=lambda url: (html, url, 200))
            self.assertEqual(confirmed_price_ranking([row]), [])

    def test_known_availability_wins_within_same_supplier(self):
        first = offer('a', 23000, availability=None)
        second = offer('a', 24000, url='https://a.example/product/second-card')
        top, _ = select_verified_top3(confirmed_price_ranking([first, second, offer('b', 25000)]), verifier=lambda row: row)
        self.assertEqual(top[0]['public_price'], 24000)
        self.assertEqual(top[0]['product_url'], second['url'])

    def test_unknown_stock_not_final_if_three_reliable_cards_exist_before_economic_gate(self):
        sources = [offer('a', 24000), offer('b', 25000),
                   offer('unknown', 24500, availability=None), offer('expensive', 40000)]
        flow = exact_supplier_flow({'price': 87390, 'commissionFee': 2665.4},
            [{'position_number': 1, 'quantity': 3, 'source_offers': sources}], verifier=lambda row: row)
        self.assertEqual([o['seller'] for o in flow['positions'][0]['offers']], ['a', 'b'])
        self.assertEqual(len(flow['antifraud_history']), 2)
        unknown = next(o for o in flow['candidates'][0]['ranked_offers'] if o['seller']=='unknown')
        self.assertIn('три более надёжные', unknown['final_offer_exclusion_reason'])


if __name__ == '__main__':
    unittest.main()
