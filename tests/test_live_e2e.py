import unittest

from google_sheets.production_upsert import EXTRA_HEADERS, _enum_text, _has_manual_value, row_values
from pipeline.live_e2e import contains_secrets
from google_sheets.workbook import (ACTIVE_HEADERS, FREE_TEXT_HEADERS,
                                    clear_data_validation_requests)


class LiveE2ETest(unittest.TestCase):
    def test_secret_names_are_rejected(self):
        self.assertTrue(contains_secrets({"authorization": "x"}))
        self.assertTrue(contains_secrets({"nested": {"client_secret": "x"}}))
        self.assertFalse(contains_secrets({"supplier": "Магазин", "url": "https://example.ru"}))

    def test_row_uses_existing_key_and_russian_fields(self):
        payload = {"procurement": {"deadline": "2026-09-14T08:52:00", "trade_number": "1001", "id": "uuid",
                    "url": "https://agregatoreat.ru/x", "subject": "ИБП", "delivery_address": "Москва",
                    "delivery_period": 10, "delivery_working_days": True, "payment_type": 2, "nmck": 200000,
                    "commission_fee": 6000, "contact": {"phone": "+7(495)1", "email": "a@b.ru"}},
                   "item": {"position_number": 1, "display_name": "ИБП", "okpd2_code": "26.20", "eat_code": "E1",
                            "quantity": 4, "unit": "шт", "customer_unit_price": 50000, "sum": 200000},
                   "documents": {"processed": 1}, "audit": {"requirements_count": 2, "special_conditions": "Пропуск",
                   "contract_analysis": "Документы проанализированы", "customer_check": {}},
                   "model": {"selected_model": "PR1500ELCD", "compliance_status": "Соответствие не подтверждено",
                             "search_mode": "EXACT_MODEL_ONLY", "customer_model": "PR1500ELCD",
                             "equivalent_allowed": False, "full_analogs_note": "НЕ ИСПОЛЬЗОВАТЬ ДЛЯ ПОДАЧИ",
                             "requirements_confirmed": 1, "requirements_unconfirmed": 1, "requirements_mismatched": 0},
                   "traceability": {"traceability_status": "Не подлежит по имеющимся данным"},
                   "supplier_search": {"confirmed_offers": [], "minimum_confirmed_price": None, "suppliers_for_call": []},
                   "economics": {"nmck_after_eat_commission": 194000,
                                  "preliminary_margin_before_logistics": 54000},
                   "warnings": ["НАЙДЕНО МЕНЕЕ 3"]}
        headers = ACTIVE_HEADERS + EXTRA_HEADERS
        row = row_values(payload, headers)
        self.assertEqual(row[headers.index("ID закупки")], "uuid")
        self.assertEqual(row[headers.index("№ позиции")], 1)
        self.assertIn("+7", row[headers.index("Контакты заказчика")])
        self.assertEqual(row[headers.index("ПРЕДУПРЕЖДЕНИЯ")], "НАЙДЕНО МЕНЕЕ 3")
        self.assertEqual(row[headers.index("РЕЖИМ ПОИСКА МОДЕЛИ")], "EXACT MODEL")
        self.assertEqual(row[headers.index("ЭКВИВАЛЕНТ РАЗРЕШЁН")], "Нет")
        self.assertEqual(row[headers.index("НМЦК МИНУС КОМИССИЯ ЕАТ, ₽")], 194000)

    def test_current_analysis_result_has_own_user_column(self):
        payload = {"procurement": {"id": "uuid", "contact": {}}, "item": {},
                   "documents": {"processed": 0}, "audit": {"customer_check": {}},
                   "model": {}, "traceability": {},
                   "supplier_search": {"confirmed_offers": [], "suppliers_for_call": []},
                   "current_analysis_result": "📞 НУЖНА ЦЕНА. Запросить цену.", "warnings": []}
        headers = ACTIVE_HEADERS + EXTRA_HEADERS
        row = row_values(payload, headers)
        self.assertEqual(row[headers.index("Текущий итог просчета и анализа")],
                         "📞 НУЖНА ЦЕНА. Запросить цену.")

    def test_missing_sheet_fields_are_blank(self):
        payload = {"procurement": {"id": "uuid", "contact": {}},
                   "item": {}, "documents": {"processed": 0},
                   "audit": {"customer_check": {}}, "model": {},
                   "traceability": {},
                   "supplier_search": {"confirmed_offers": [], "suppliers_for_call": []},
                   "warnings": []}
        headers = ACTIVE_HEADERS + EXTRA_HEADERS
        row = row_values(payload, headers)
        for header in ("Заказчик", "ИНН заказчика", "Контакты заказчика",
                       "Цена заказчика за единицу, ₽", "ПОСТАВЩИК №1"):
            self.assertEqual(row[headers.index(header)], "")

    def test_old_no_data_placeholder_is_not_preserved_as_manual_input(self):
        self.assertFalse(_has_manual_value("Нет данных"))
        self.assertFalse(_has_manual_value(""))
        self.assertTrue(_has_manual_value(0))
        self.assertTrue(_has_manual_value("Просчёт в работе"))

    def test_internal_enum_code_is_never_shown_to_user(self):
        self.assertEqual(_enum_text(2), "")
        self.assertEqual(_enum_text("2"), "")
        self.assertEqual(_enum_text("По счету"), "По счету")

    def test_selected_model_is_permanent_free_text_column(self):
        headers = ["Статус закупки", "ВЫБРАННАЯ МОДЕЛЬ", "ПРЕДУПРЕЖДЕНИЯ"]
        requests = clear_data_validation_requests(321, headers)
        self.assertEqual(FREE_TEXT_HEADERS, {"ВЫБРАННАЯ МОДЕЛЬ"})
        self.assertEqual(requests, [{
            "setDataValidation": {
                "range": {
                    "sheetId": 321,
                    "startRowIndex": 1,
                    "startColumnIndex": 1,
                    "endColumnIndex": 2,
                },
                "rule": None,
            }
        }])

    def test_selected_model_accepts_arbitrary_text(self):
        payload = {"procurement": {"id": "uuid", "contact": {}}, "item": {},
                   "documents": {"processed": 0}, "audit": {"customer_check": {}},
                   "model": {"selected_model": "CyberPower PR1500ELCD / произвольный текст"},
                   "traceability": {},
                   "supplier_search": {"confirmed_offers": [], "suppliers_for_call": []},
                   "warnings": []}
        headers = ACTIVE_HEADERS + EXTRA_HEADERS
        row = row_values(payload, headers)
        self.assertEqual(row[headers.index("ВЫБРАННАЯ МОДЕЛЬ")],
                         "CyberPower PR1500ELCD / произвольный текст")


if __name__ == "__main__":
    unittest.main()
