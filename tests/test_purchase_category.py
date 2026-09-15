"""Профильные проверки постоянной категории закупки."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from google_sheets.production_upsert import EXTRA_HEADERS, row_values
from google_sheets.workbook import ACTIVE_HEADERS
from model_search.purchase_category import (
    ALLOWED_CATEGORIES,
    CATEGORY_EXACT_MODEL,
    CATEGORY_MODEL_DISCOVERY,
    CATEGORY_NO_MODEL,
    classify_purchase_category,
)
from pipeline.single_purchase_test import run_check
from documents.item_sources import resolve_item_sources
from tests.test_google_sheets_formulas import _payload


class PurchaseCategoryTests(unittest.TestCase):
    def test_explicit_pump_is_category_one(self):
        item = {"name": "Помпа", "model": "ПР-1", "position_kind": "goods"}
        self.assertEqual(classify_purchase_category(item, {"original_model": "Помпа ПР-1"}),
                         CATEGORY_EXACT_MODEL)

    def test_modelled_goods_without_model_are_category_two(self):
        for name in ("Телевизор", "Кондиционер", "Принтер"):
            self.assertEqual(classify_purchase_category({"name": name, "position_kind": "goods"}),
                             CATEGORY_MODEL_DISCOVERY)

    def test_model_in_contract_or_price_justification_is_category_one(self):
        item = {"name": "Принтер", "position_kind": "goods"}
        doc = {"document_name": "Обоснование цены.txt", "document_type": ["price_justification"],
               "text": "Принтер Pantum M6607NW", "status": "analyzed", "text_available": True,
               "pages": []}
        resolved = resolve_item_sources(item, [doc])
        self.assertEqual(classify_purchase_category(item, resolved), CATEGORY_EXACT_MODEL)

    def test_blanket_pillow_and_brick_are_category_three(self):
        for name in ("Одеяло 140х205, хлопок, плотность 300 г/м2",
                     "Подушка 50х70, синтетический наполнитель",
                     "Кирпич керамический по ГОСТ, 250х120х65"):
            self.assertEqual(classify_purchase_category({"name": name, "position_kind": "goods"}),
                             CATEGORY_NO_MODEL)

    def test_category_three_does_not_start_price_search(self):
        purchase_id = "93ffd454-9d9e-49f5-a4d7-acbcf8cdbe8b"
        card = {"raw": {"id": purchase_id, "tradeNumber": "cat-3",
                         "lot": {"price": 1000, "commissionFee": 0,
                                  "lotItems": [{"name": "Одеяло", "quantity": 1,
                                                "unitPrice": 1000, "position_kind": "goods"}]}},
                "documents": []}
        calls = []
        def forbidden(*args, **kwargs):
            calls.append("called")
            raise AssertionError("поиск модели/цены для категории 3 не должен запускаться")
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), \
             patch("pipeline.single_purchase_test.tender_folder", return_value=Path(directory)):
            result = run_check(purchase_id, journal=_Journal(), card_loader=lambda _: card,
                               model_discovery=forbidden, price_search=forbidden,
                               document_loader=lambda _card, purpose: [], output_dir=Path(directory))
        self.assertEqual(calls, [])
        self.assertEqual(result["positions"][0]["purchase_category"], CATEGORY_NO_MODEL)
        self.assertFalse(result["positions"][0]["price_search_performed"])
        self.assertEqual(result["current_analysis_result"], "Алгоритм просчета не доработан")

    def test_sheets_mapping_keeps_exact_category_and_empty_model(self):
        payload = _payload()
        payload["purchase_category"] = CATEGORY_NO_MODEL
        payload["current_analysis_result"] = "Алгоритм просчета не доработан"
        payload["manual_stop_comment"] = "Алгоритм просчета не доработан"
        headers = list(ACTIVE_HEADERS) + EXTRA_HEADERS
        mapped = dict(zip(headers, row_values(payload, headers)))
        self.assertEqual(mapped["Категория закупки"], CATEGORY_NO_MODEL)
        self.assertEqual(mapped["ВЫБРАННАЯ МОДЕЛЬ"], "")
        self.assertEqual(mapped["Текущий итог просчета и анализа"], "Алгоритм просчета не доработан")

    def test_only_three_category_values_are_allowed(self):
        self.assertEqual(len(ALLOWED_CATEGORIES), 3)
        self.assertEqual(set(ALLOWED_CATEGORIES), {
            CATEGORY_EXACT_MODEL, CATEGORY_MODEL_DISCOVERY, CATEGORY_NO_MODEL})


class _Journal:
    def write_many(self, result):
        self.result = result

    def verify(self, result):
        return {"status": "LOCAL_ONLY"}


if __name__ == "__main__":
    unittest.main()
