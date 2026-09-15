"""Разрешённые источники ТЗ и выбор одной модели, без сети и Sheets."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from documents.item_sources import resolve_item_sources
from documents.pipeline import audit_from_extraction
from eat.additional_characteristics import read_additional_characteristics
from model_search.live_discovery import select_cheapest_compliant_model
from model_search.price_readiness import classify_price_search_readiness
from pipeline.single_purchase_test import run_check
from tests.test_exact_model_branch import extraction, PID


def document(text, kind='contract_draft'):
    return {'document_name': 'Официальный документ', 'document_type': [kind],
            'text': text, 'status': 'analyzed', 'pages': []}


class RequirementSourceTests(unittest.TestCase):
    def test_specification_reads_only_explicit_section(self):
        result = resolve_item_sources({'name': 'Измельчитель'}, [document(
            'Условия оплаты\nСрок оплаты: 30 дней\nСпецификация\nТип резки: перекрестный\nОтветственность сторон\nШтраф: 10 %')])
        self.assertEqual([r['requirement_name'] for r in result['requirements']], ['Тип резки'])

    def test_technical_assignment_reads_only_explicit_section(self):
        result = resolve_item_sources({'name': 'Измельчитель'}, [document(
            'Техническое задание\nРазмер частицы: 4x12 мм\nПорядок расчётов\nОплата: 30 дней')])
        self.assertEqual([r['value'] for r in result['requirements']], ['4x12 мм'])

    def test_allowed_attached_technical_and_commercial_documents(self):
        for kind in ('technical_specification', 'specification', 'price_justification', 'commercial_offer'):
            with self.subTest(kind=kind):
                result = resolve_item_sources({'name': 'Измельчитель'}, [document('Уровень секретности: P-4', kind)])
                self.assertEqual(result['requirements'][0]['value'], 'P-4')

    def test_popup_is_separate_official_source(self):
        item = {'name': 'Измельчитель'}
        page = MagicMock()
        popup = MagicMock()
        name = MagicMock()
        page.locator.side_effect = lambda selector: popup if selector == '.modal-window-lot-item-info-content' else name
        popup.evaluate.return_value = [{'parameter': 'Ёмкость корзины', 'value': '25 л'},
                                       {'parameter': 'Мощность', 'value': '300 Вт'}]
        read_additional_characteristics(page, [item], 'https://eat.example/card')
        name.click.assert_called_once()
        popup.evaluate.assert_called_once()
        result = resolve_item_sources(item)
        self.assertEqual(len(result['requirements']), 2)
        self.assertTrue(all(r['requirement_source'] == 'EAT_ADDITIONAL_CHARACTERISTICS' for r in result['requirements']))

    def test_legal_text_without_technical_section_is_never_requirements(self):
        for text in ('Ответственность сторон\nШтраф: 10 %', 'Срок оплаты: 30 дней\nСрок расторжения: 5 дней',
                     'Обязанности сторон\nГарантия исполнения: 2 года'):
            with self.subTest(text=text):
                self.assertEqual(resolve_item_sources({'name': 'Измельчитель'}, [document(text)])['requirements'], [])

    def test_position_scope_inside_shared_specification(self):
        items = [{'name': 'Измельчитель'}, {'name': 'Принтер'}]
        result = resolve_item_sources(items[1], [document(
            'Спецификация\nПозиция 1\nМощность: 300 Вт\nПозиция 2\nСкорость: 40 стр/мин\nОтветственность сторон\nШтраф: 10 %')], item_number=2, items=items)
        self.assertEqual([r['requirement_name'] for r in result['requirements']], ['Скорость'])

    def test_cheapest_fully_compliant_available_model_wins(self):
        candidates = [{'exact_model': name, 'public_price': price, 'status': status,
                       'russia_availability': available, 'production_status': 'production_not_confirmed'}
                      for name, price, status, available in (
                          ('CheapWrong', 100, 'non_compliant', 'available'),
                          ('CheapUnknown', 200, 'unconfirmed', 'available'),
                          ('CheapUnavailable', 300, 'fully_compliant', 'not_confirmed'),
                          ('Valid B', 600, 'fully_compliant', 'available'),
                          ('Valid A', 500, 'fully_compliant', 'available'))]
        self.assertEqual(select_cheapest_compliant_model(candidates)['exact_model'], 'Valid A')

    def test_no_named_model_collects_requirements_and_deep_searches_one_selected_model(self):
        item = {'name': 'Измельчитель', 'quantity': 1, 'unitPrice': 50000}
        card = {'raw': {'tradeNumber': '200909955126100205', 'lot': {'price': 50000, 'lotItems': [item]}},
                'documents': [{'download_status': 'downloaded'}]}
        ex = extraction([document('Техническое задание\nМощность: 300 Вт')])
        resolved = audit_from_extraction(card, ex)['items'][0]
        self.assertEqual(len(resolved['requirements']), 1)
        self.assertEqual(classify_price_search_readiness(item, resolved)['classification'], 'MODEL_DISCOVERY_REQUIRED')
        discovery = MagicMock(return_value={'selected_model': {'exact_model': 'Valid A'},
                                           'candidates': [{'exact_model': 'Valid A'}, {'exact_model': 'Valid B'}]})
        prices = MagicMock(return_value={'offers': []})
        journal = MagicMock()
        journal.verify.side_effect = lambda run: {'rows_verified': len(run['events'])}
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), \
             patch('pipeline.single_purchase_test.tender_folder', return_value=Path(directory)), \
             patch('pipeline.single_purchase_test.process_procurement_documents', return_value=ex), \
             patch('pipeline.single_purchase_test.build_passport'), \
             patch('pipeline.single_purchase_test.detect_brands', return_value={'status': 'not_found'}), \
             patch('pipeline.single_purchase_test.save_brand_audit'):
            run_check(PID, journal=journal, card_loader=lambda _: card, price_search=prices,
                      model_discovery=discovery, output_dir=Path(directory))
        discovery.assert_called_once()
        prices.assert_called_once()
        self.assertEqual(prices.call_args.args[0], 'Valid A')
