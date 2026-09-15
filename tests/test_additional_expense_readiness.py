"""Краткий анализ контракта и сохранение проверенных ручных расходов."""
import unittest
from unittest.mock import MagicMock, patch

from documents.pipeline import audit_from_extraction
from google_sheets.client import active_row_updates, _upsert, PRESERVED_ACTIVE_HEADERS
from google_sheets.client import GOOGLE_REQUEST_TIMEOUT_SECONDS, _install_request_timeout
from gspread.utils import a1_to_rowcol
from google_sheets.production_upsert import row_values, upsert_live_payload
from google_sheets.workbook import ACTIVE, quote_formulas, _column_letter
from pipeline.mvp_exact_batch import _sheet_payload
from tests.test_kp_mapping import headers, payload, offer


def audit(text):
    doc = {'document_name': 'Контракт.docx', 'document_type': ['contract_draft'], 'text': text}
    ex = {'procurement_id': 'uuid', 'documents_found': 1, 'documents_processed': 1,
          'documents_failed': 0, 'document_results': [doc], 'combined_text': text,
          'warnings': [], 'extraction_summary': {'partial_documents': 0}}
    return audit_from_extraction({'lotItems': []}, ex)


def writes(ws):
    entries = [entry for call in ws.batch_update.call_args_list for entry in call.args[0]]
    entries.extend({'range': call.args[1], 'values': call.args[0]} for call in ws.update.call_args_list)
    return entries


def simulate_updates(h, old, updates):
    result = dict(old)
    for entry in updates:
        start = a1_to_rowcol(entry['range'].split(':')[0])[1] - 1
        for i, value in enumerate(entry['values'][0], start):
            result[h[i]] = value
    return result


class ManualExpensesContractTests(unittest.TestCase):
    def test_google_request_timeout_replaces_none(self):
        session = MagicMock()
        session._tender_ai_timeout_wrapped = False
        client = MagicMock()
        client.http_client.session = session
        original = session.request
        _install_request_timeout(client)
        session.request("get", "https://example.test", timeout=None)
        self.assertEqual(original.call_args.kwargs["timeout"], GOOGLE_REQUEST_TIMEOUT_SECONDS)

    def test_standard_price_inclusion_clause_is_ignored(self):
        a = audit('Цена контракта включает транспортные расходы, расходы на страхование, налоги, сборы и иные расходы поставщика.')
        self.assertEqual(a['contract_analysis'], 'Нет специальных условий')
        self.assertEqual(a['special_conditions'], '')
        self.assertIsNone(a['additional_expenses'])
        self.assertNotIn('additional_expense_state', a)

    def test_lifting_retains_floor_in_contract_summary(self):
        self.assertIn('Подъём на 3 этаж', audit('Поставщик обязан выполнить подъём на 3 этаж.')['contract_analysis'])

    def test_assembly_enters_contract_summary(self):
        self.assertIn('Сборка на месте', audit('Поставщик обязан выполнить сборку на месте.')['contract_analysis'])

    def test_pass_and_vehicle_data_enter_contract_summary(self):
        summary = audit('Нужен пропуск. Поставщик обязан заранее передать данные автомобиля и госномер.')['contract_analysis']
        self.assertIn('Нужен пропуск', summary); self.assertIn('Данные автомобиля', summary)

    def test_marking_heading_is_not_vehicle_data(self):
        self.assertEqual(audit('Маркировка, упаковка и транспортировка. Маркировка товара должна соответствовать стандартам.')['contract_analysis'], 'Нет специальных условий')

    def test_ordinary_delivery_is_not_a_special_obligation(self):
        self.assertEqual(audit('Поставщик обязан осуществить доставку. Транспортировка Товара должна осуществляться до склада.')['contract_analysis'], 'Нет специальных условий')

    def test_technical_features_and_damage_are_not_supplier_work(self):
        text = ('Тип подключения проводной. USB кабель для подключения. Краткое руководство по установке. '
                'Товар, получивший при погрузке (разгрузке) и транспортировке повреждения, считается не поставленным.')
        self.assertEqual(audit(text)['contract_analysis'], 'Нет специальных условий')

    def test_explicit_negative_condition_is_ignored(self):
        self.assertEqual(audit('Разгрузка не требуется.')['contract_analysis'], 'Нет специальных условий')

    def test_unloading_and_installation_training_enter_summary(self):
        summary = audit('Поставщик обязан выполнить разгрузку, монтаж и обучение.')['contract_analysis']
        for term in ['Разгрузка', 'Монтаж', 'Обучение']: self.assertIn(term, summary)

    def test_ambiguous_unloading_does_not_invent_executor(self):
        summary = audit('Транспортировка Товара должна осуществляться до места назначения и разгрузки, на складе Заказчика.')['contract_analysis']
        self.assertIn('кто выполняет — не уточнено', summary)

    def test_no_automatic_expense_mapping_even_from_legacy_states(self):
        for state in [{'ready': True, 'amount': 0}, {'ready': True, 'amount': 1500},
                      {'ready': False, 'reason': 'ТРЕБУЕТ РАСЧЁТА: монтаж'}]:
            p = payload([]); p['audit'].update(audit('Поставщик обязан выполнить монтаж.'), additional_expense_state=state)
            row = dict(zip(headers(), row_values(p, headers())))
            self.assertEqual(row['Дополнительные расходы, ₽'], '')
            self.assertIn('Монтаж', row['РЕЗУЛЬТАТ АНАЛИЗА КОНТРАКТА'])
            self.assertNotIn('ТРЕБУЕТ РАСЧЁТА', row['Текущий итог просчета и анализа'])

    def production_upsert_with_manual_value(self, value, existing=True):
        h = headers(); old = {name: '' for name in h}
        old.update({'ID закупки': 'uuid', '№ позиции': 1, 'Дополнительные расходы, ₽': value})
        ws, book, client = MagicMock(), MagicMock(), MagicMock()
        ws.row_values.return_value = h
        ws.get_all_values.return_value = [h, [old[name] for name in h]] if existing else [h]
        ws.acell.return_value.value = value if existing else None
        ws.col_count = len(h); book.worksheet.return_value = ws; client.open_by_key.return_value = book
        p = payload([offer('a', 24554), offer('b', 25635)])
        p['audit'].update(audit('Поставщик обязан выполнить подъём на 3 этаж.'),
                          additional_expense_state={'ready': True, 'amount': 9999})
        with patch('google_sheets.production_upsert.authorize_service_account', return_value=(client, 'local-test')):
            upsert_live_payload(p)
        for entry in [entry for call in ws.batch_update.call_args_list for entry in call.args[0]]:
            start = a1_to_rowcol(entry['range'].split(':')[0])[1]-1
            self.assertNotIn(h.index('Дополнительные расходы, ₽'), range(start, start+len(entry['values'][0])))
        self.assertFalse(ws.update.called)
        return simulate_updates(h, old, writes(ws))

    def test_production_upsert_preserves_manual_number(self):
        row = self.production_upsert_with_manual_value(1234.56)
        self.assertEqual(row['Дополнительные расходы, ₽'], 1234.56)
        self.assertIn('Подъём на 3 этаж', row['РЕЗУЛЬТАТ АНАЛИЗА КОНТРАКТА'])

    def test_production_upsert_preserves_manual_zero(self):
        self.assertEqual(self.production_upsert_with_manual_value(0)['Дополнительные расходы, ₽'], 0)

    def test_production_upsert_keeps_unknown_expenses_blank(self):
        self.assertEqual(self.production_upsert_with_manual_value('')['Дополнительные расходы, ₽'], '')

    def test_production_upsert_preserves_any_existing_text(self):
        self.assertEqual(self.production_upsert_with_manual_value('Ручной комментарий')['Дополнительные расходы, ₽'], 'Ручной комментарий')

    def test_new_row_keeps_unknown_expenses_blank(self):
        self.assertEqual(self.production_upsert_with_manual_value('', existing=False)['Дополнительные расходы, ₽'], '')

    def test_collection_upsert_also_skips_manual_cell(self):
        h = headers(); old = {name: '' for name in h}
        old.update({'ID закупки': 'uuid', '№ позиции': 1, 'Дополнительные расходы, ₽': 321})
        ws = MagicMock(); ws.title = ACTIVE
        ws.get_all_values.return_value = [h, [old[name] for name in h]]
        incoming = dict(old, **{'Дополнительные расходы, ₽': 9999})
        _upsert(ws, [h, [incoming[name] for name in h]], ('ID закупки', '№ позиции'), PRESERVED_ACTIVE_HEADERS)
        self.assertEqual(simulate_updates(h, old, writes(ws))['Дополнительные расходы, ₽'], 321)

    def test_skip_manual_cell_when_headers_reordered(self):
        for h in [['Дополнительные расходы, ₽', 'A'], ['A', 'Дополнительные расходы, ₽'], ['A', 'Дополнительные расходы, ₽', 'B']]:
            for entry in active_row_updates(2, h, [999]*len(h)):
                start = a1_to_rowcol(entry['range'].split(':')[0])[1]-1
                self.assertNotIn(h.index('Дополнительные расходы, ₽'), range(start, start+len(entry['values'][0])))

    def test_adapter_propagates_summary_only(self):
        p = _sheet_payload({'contract_analysis': 'Сборка на месте'})
        self.assertEqual(p['audit']['contract_analysis'], 'Сборка на месте')
        self.assertNotIn('additional_expense_state', p['audit'])

    def test_blocked_procurement_comment_overrides_business_decision_text(self):
        p = _sheet_payload({
            'purchase_id': 'uuid', 'trade_number': '1001', 'purchase_url': 'https://example.test',
            'subject': 'Поставка товара', 'item_number': 1, 'item_name': 'Товар',
            'quantity': 400, 'unit': 'шт.', 'customer_unit_price': 100,
            'customer_sum': 40000, 'nmck': 40000, 'commission_fee': 6000,
            'processing_blocked': True,
            'processing_stop_reason': 'работа с закупкой НЕ автоматизирована - изза НЕЧИТАЕМОСТИ ФАЙЛОВ PDF в закупке - просчет делать в ручную!',
            'model': {'route': 'manual_review', 'status_ru': 'Остановлено', 'mode': 'MANUAL_REVIEW'},
            'supplier_search': None, 'customer_check': {}, 'traceability': {},
            'documents_found': 1, 'documents_processed': 0, 'audit_status': 'blocked',
            'requirements_count': 0, 'special_conditions': '',
            'contract_analysis': 'Анализ документов остановлен',
        })
        row = dict(zip(headers(), row_values(p, headers())))
        self.assertEqual(row['Текущий итог просчета и анализа'], p['manual_stop_comment'])

    def test_formulas_depend_on_existing_numeric_manual_input(self):
        h = headers()
        for f in quote_formulas(2, h).values():
            self.assertIn(f'N(ISNUMBER(${_column_letter(h.index("Дополнительные расходы, ₽"))}$2:', f)
            self.assertNotIn('additional_expense_state', f)
            self.assertNotIn('ТРЕБУЕТ РАСЧЁТА', f)
