"""Профильные проверки бизнес-решения без сети."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from calculator.business_decision import (calculate_bid_economics,
                                          calculate_bid_economics_from_offers,
                                          decide_business, decision_text)
from calculator.result_decision import attach_business_decision
from filters.eat_filters import load_config
from google_sheets.production_upsert import row_values, upsert_live_payload, publish_business_decision
from google_sheets.test_journal import row_values as journal_row
from pipeline.single_purchase_test import TestRun
from reports.price_search_report import render_price_search_markdown, write_price_search_markdown
from tests.test_kp_mapping import headers, payload, offer as kp_offer
from gspread.utils import a1_to_rowcol


def offer(domain, price=100, profitability=25, **fields):
    return {"domain": domain, "source_url": f"https://{domain}/product/model",
            "confirmed_price": price, "price_confirmed": True,
            "exact_model_confirmed": True, "profitability": profitability,
            "economics_complete": True, "mandatory_expenses_included": True,
            "availability_normalized": "in_stock", "antifraud_status": "green", **fields}


def three(**first):
    return [offer("a.ru", **first), offer("b.ru", price=110), offer("c.ru", price=120)]


class BusinessDecisionTests(unittest.TestCase):
    def test_average_uses_full_quote_cost_for_required_quantity(self):
        economics = calculate_bid_economics_from_offers(
            [offer("a.ru", price=100), offer("b.ru", price=110), offer("c.ru", price=120)],
            additional_expenses=0, eat_commission=0, nmck=500, quantity=3)
        self.assertAlmostEqual(economics["average_purchase_price"], 330)
        self.assertAlmostEqual(economics["bid_price"], 389.4)

    def test_three_full_quotes_average_to_105000(self):
        economics = calculate_bid_economics_from_offers(
            [{"full_quote_price": value} for value in (100_000, 105_000, 110_000)],
            additional_expenses=0, eat_commission=0, nmck=130_000)
        self.assertAlmostEqual(economics["average_purchase_price"], 105_000)
        self.assertAlmostEqual(economics["bid_price"], 123_900)

    def test_three_valid_offers_and_aggregate_economics_can_be_green_without_row_profitability(self):
        rows = [offer(f"{name}.ru", profitability=None, economics_complete=False) for name in "abc"]
        final = calculate_bid_economics(330, 0, 0, 500)
        decision = decide_business(rows, final_economics=final,
                                   calculation_complete=True,
                                   mandatory_expenses_included=True)
        self.assertEqual(decision["status"], "bid")

    def test_explicit_zero_additional_expenses_is_complete(self):
        final = calculate_bid_economics(330, 0, 2531.5, 5000)
        self.assertTrue(final["complete"])
        self.assertEqual(final["additional_expenses"], 0)

    def test_missing_commission_cannot_produce_green(self):
        rows = three()
        final = calculate_bid_economics(330, 0, 2531.5, 5000)
        result = {"procurement": {"commission_fee": None},
                  "supplier_search": {"ranked_offers": rows},
                  "additional_expenses": 0,
                  "calculation_complete": True,
                  "final_economics": final}
        self.assertEqual(attach_business_decision(result)["status"], "manual_review")

    def test_stale_final_economics_with_other_commission_is_ignored(self):
        result = {"procurement": {"commission_fee": 2531.5},
                  "supplier_search": {"ranked_offers": three()},
                  "additional_expenses": 0,
                  "final_economics": {"net_profit_percent": 30, "eat_commission": 1000,
                                      "commission_source": "calculator.formulas.platform_commission",
                                      "commission_verified": False, "complete": True}}
        decision = attach_business_decision(result)
        self.assertEqual(decision["status"], "manual_review")
    def test_green_three_suppliers(self):
        self.assertEqual(decide_business(three())["status"], "bid")

    def test_one_supplier_manual(self):
        self.assertEqual(decide_business(three()[:1])["status"], "manual_review")

    def test_two_suppliers_manual(self):
        self.assertEqual(decide_business(three()[:2])["status"], "manual_review")

    def test_order_manual(self):
        self.assertEqual(decide_business(three(availability_normalized="order"))["status"], "manual_review")

    def test_yellow_manual(self):
        self.assertEqual(decide_business(three(antifraud_status="yellow"))["status"], "manual_review")

    def test_below_threshold_red(self):
        rows = [offer("a.ru", profitability=10.2), offer("b.ru", profitability=9)]
        decision = decide_business(rows)
        self.assertEqual(decision["status"], "do_not_bid")
        self.assertIn("10,2%", decision_text(decision))
        self.assertIn("в наличии", decision["decision_reasons"][2])
        self.assertIn("зелёный статус", decision["decision_reasons"][3])

    def test_all_out_of_stock_red(self):
        decision = decide_business([offer("a.ru", availability_normalized="out_of_stock")])
        self.assertEqual(decision["status"], "do_not_bid")
        self.assertIn("отсутствуют", decision_text(decision))

    def test_all_red_antifraud(self):
        self.assertEqual(decide_business([offer("a.ru", antifraud_status="red")])["status"], "do_not_bid")

    def test_domain_duplicates_do_not_create_reserve(self):
        rows = [offer("a.ru"), offer("www.a.ru", price=110), offer("b.ru", price=120)]
        decision = decide_business(rows)
        self.assertEqual(decision["eligible_supplier_count"], 2)
        self.assertEqual(decision["status"], "manual_review")

    def test_pantum_known_case(self):
        rows = [offer("first.ru", price=21087, profitability=25.33,
                      availability_normalized="order", antifraud_status="yellow"),
                offer("second.ru", price=23690, profitability=16.12),
                offer("third.ru", price=24554, profitability=13.06)]
        decision = decide_business(rows)
        self.assertEqual(decision["status"], "manual_review")
        self.assertEqual(decision["eligible_supplier_count"], 3)
        self.assertEqual(decision["confirmed_supplier_count"], 3)
        self.assertEqual(decision["recommended_supplier"]["confirmed_price"], 21087)
        self.assertIn("25,33%", decision_text(decision))
        self.assertIn("под заказ", decision_text(decision))
        self.assertIn("жёлтый", decision_text(decision))
        self.assertEqual(len(decision["decision_reasons"]), 5)

    def test_missing_price_is_manual_confirmed_model_rejection_is_red(self):
        for fields in [{"price_confirmed": False}, {"exact_model_confirmed": False},
                       {"confirmed_price": float("nan")}, {"confirmed_price": 0},
                       {"stale_price": True}, {"price_confirmed_on_product_page": False}]:
            with self.subTest(fields=fields):
                expected = "do_not_bid" if fields.get("exact_model_confirmed") is False else "manual_review"
                self.assertEqual(decide_business([offer("a.ru", **fields)])["status"], expected)

    def test_missing_economics_unknown_stock_and_antifraud_manual(self):
        for fields in [{"profitability": None}, {"availability_normalized": "unknown"},
                       {"antifraud_status": "unknown"}]:
            with self.subTest(fields=fields):
                self.assertEqual(decide_business(three(**fields))["status"], "manual_review")

    def test_net_profit_threshold_is_fixed_at_12_percent(self):
        config = load_config()
        config["calculator"]["economic_precheck_margin_percent"] = 99
        self.assertEqual(decide_business(three(profitability=12), config=config)["status"], "bid")
        below = [offer(f"{domain}.ru", profitability=11.99) for domain in ("a", "b", "c")]
        self.assertEqual(decide_business(below, config=config)["status"], "do_not_bid")

    def test_best_eligible_retains_ranking_and_input(self):
        rows = [offer("red.ru", price=1, antifraud_status="red"), *three(price=150)]
        snapshot = copy.deepcopy(rows)
        self.assertEqual(decide_business(rows)["recommended_supplier"]["domain"], "a.ru")
        self.assertEqual(rows, snapshot)

    def test_warnings_prevent_green_and_remain_visible(self):
        decision = decide_business(three(), manual_review_reasons=["Уточнить расходы", "Проверить заказчика"])
        self.assertEqual(decision["status"], "manual_review")
        self.assertIn("Проверить заказчика", decision_text(decision))

    def test_saved_basket_does_not_assign_its_metric_to_other_prices(self):
        rows = [{"domain": "a.ru", "seller": "a.ru", "url": "https://a.ru/product/model", "price": 21087,
                 "price_confirmed_on_product_page": True, "exact_model_match": True, "availability": "Под заказ"},
                {"domain": "b.ru", "url": "https://b.ru/product/model", "price": 23690,
                 "price_confirmed_on_product_page": True, "exact_model_match": True}]
        result = {"exact_supplier_flow": {
            "candidates": [{"position_number": 1, "ranked_offers": rows}],
            "antifraud_history": [{**rows[0], "verification_status": "🟡 РУЧНАЯ ПРОВЕРКА"}],
            "public_economics_before_supplier_approval": {"basket": [{"supplier_name": "a.ru", "unit_price": 21087}],
                                                        "preliminary_reserve_percent": 25.33}}}
        original = copy.deepcopy(rows)
        decision = attach_business_decision(result)
        self.assertEqual(decision["status"], "manual_review")
        self.assertEqual(decision["eligible_supplier_count"], 0)
        self.assertEqual(decision["profitability_basis"], "preliminary_reserve_percent")
        self.assertIn("Предварительный запас", decision_text(decision))
        self.assertFalse(decision["economics_complete"])
        self.assertEqual(decision["rule_candidates"], [])
        self.assertEqual(rows, original)

    def test_markdown_from_json_and_sheet_use_same_decision(self):
        result = payload([])
        result["business_decision"] = decide_business(three(availability_normalized="order"))
        h = headers()
        row = dict(zip(h, row_values(result, h)))
        self.assertEqual(row["Текущий итог просчета и анализа"], decision_text(result["business_decision"]))
        self.assertEqual(h, headers())
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "result.json"
            path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            md = write_price_search_markdown(path).read_text(encoding="utf-8")
        self.assertTrue(md.startswith("# Бизнес-решение"))
        self.assertIn(result["business_decision"]["label"], md)
        self.assertEqual(md, render_price_search_markdown(result))

    def test_control_run_records_decision_in_journal(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = MagicMock()
            journal.verify.return_value = {"rows_verified": 1}
            run = TestRun("1219e37d-4823-499b-97bc-006e9c338180", journal, Path(folder))
            run.data["exact_supplier_flow"] = {"candidates": [{"position_number": 1, "ranked_offers": three()}]}
            result = run.finish()
            event = result["events"][-1]
            row = journal_row(result, event)
            self.assertEqual(row[5], "Бизнес-решение")
            self.assertEqual(row[6], "🟡 РУЧНАЯ ПРОВЕРКА")
            self.assertIn("Подходящих уникальных поставщиков: 3", row[8])
            self.assertTrue(run.path.with_suffix(".md").exists())

    def test_repeat_upsert_replaces_old_decision(self):
        h = headers()
        old = {name: "" for name in h}
        old.update({"ID закупки": "uuid", "№ позиции": 1,
                    "Текущий итог просчета и анализа": "🟢 СТАРОЕ РЕШЕНИЕ"})
        ws, book, client = MagicMock(), MagicMock(), MagicMock()
        ws.row_values.return_value = h
        ws.get_all_values.return_value = [h, [old[name] for name in h]]
        ws.col_count = len(h)
        book.worksheet.return_value = ws
        client.open_by_key.return_value = book
        result = payload([])
        result["business_decision"] = decide_business(three()[:1])
        with patch("google_sheets.production_upsert.authorize_service_account", return_value=(client, "test")):
            upsert_live_payload(result)
        written = {}
        for call in ws.batch_update.call_args_list:
            for entry in call.args[0]:
                start = a1_to_rowcol(entry["range"].split(":")[0])[1] - 1
                written.update(zip(h[start:], entry["values"][0]))
        self.assertEqual(written["Текущий итог просчета и анализа"], decision_text(result["business_decision"]))

    def test_multiple_positions_cannot_green_without_complete_basket(self):
        result = {"exact_supplier_flow": {"candidates": [
            {"position_number": 1, "ranked_offers": three()},
            {"position_number": 2, "ranked_offers": []}]}}
        decision = attach_business_decision(result)
        self.assertEqual(decision["status"], "manual_review")
        self.assertEqual(len(decision["position_decisions"]), 2)
        self.assertIsNone(decision["recommended_supplier"])
        for final, expected in [(None, "manual_review"), (25, "manual_review"), (10, "manual_review")]:
            with self.subTest(final=final):
                result = {"exact_supplier_flow": {"candidates": [
                    {"position_number": n, "ranked_offers": three()} for n in (1, 2)]},
                          "procurement": {"commission_fee": 6_000}}
                if final is not None:
                    result["final_economics"] = {"net_profit_percent": final, "eat_commission": 6_000,
                                                "commission_source": "procurement.commission_fee",
                                                "commission_verified": True, "bid_price": 130_000,
                                                "nmck": 200_000, "complete": True,
                                                "mandatory_expenses_included": True}
                decision = attach_business_decision(result)
                self.assertEqual(decision["status"], expected)
                self.assertEqual(len(decision["decision_reasons"]), 5)
        result["final_economics"]["profitability_percent"] = 25
        for group in result["exact_supplier_flow"]["candidates"]:
            group["ranked_offers"] = [offer(f"{n}.ru", profitability=10) for n in range(3)]
        self.assertEqual(attach_business_decision(result)["status"], "do_not_bid")

    def test_publish_only_decision_and_verify_readback(self):
        h = headers()
        result = payload([kp_offer('a', 24000)])
        result['business_decision'] = attach_business_decision(result)
        row = row_values(result, h)
        ws, book = MagicMock(), MagicMock()
        book.worksheet.return_value = ws
        ws.id = 1
        ws.get_all_values.return_value = [h, row]
        ws.acell.return_value.value = decision_text(result['business_decision'])
        receipt = publish_business_decision(result, book=book)
        self.assertTrue(receipt['readback_verified'])
        ws.update.assert_called_once_with([[decision_text(result['business_decision'])]],
                                          receipt['cell'], value_input_option='RAW')
        book.batch_update.assert_not_called()

    def test_publish_refuses_stale_kp_without_writing(self):
        h = headers()
        result = payload([kp_offer('a', 24000)])
        row = row_values(result, h)
        row[h.index('Цена КП 1')] = 999
        ws, book = MagicMock(), MagicMock()
        book.worksheet.return_value = ws
        ws.get_all_values.return_value = [h, row]
        with self.assertRaises(ValueError):
            publish_business_decision(result, book=book)
        ws.update.assert_not_called()

    def test_batch_mapping_keeps_saved_flow_and_decision(self):
        from pipeline.mvp_exact_batch import _sheet_payload
        item = {'model': {'mode': 'EXACT_MODEL', 'route': 'exact'}, 'item_number': 1,
                'supplier_search': {'exact_supplier_flow': {'candidates': [
                    {'position_number': 1, 'ranked_offers': three()}]}}}
        result = _sheet_payload(item)
        self.assertEqual(result['business_decision']['status'], 'manual_review')
        self.assertEqual(item['business_decision'], result['business_decision'])
        self.assertIs(result['exact_supplier_flow'], item['supplier_search']['exact_supplier_flow'])

    def test_incomplete_data_never_implies_economic_rejection(self):
        scenarios = [
            (three(), {"calculation_complete": False}),
            (three(), {"mandatory_expenses_included": False}),
            ([], {}),
            ([offer("a.ru", profitability=10)], {"calculation_complete": False}),
            (three(profitability=25, economics_complete=False), {}),
            (three(profitability_basis="preliminary_reserve_percent"), {}),
            (three(), {"manual_review_reasons": ["Влияние особых условий ещё не учтено"]}),
            (three(), {"final_economics": {"profitability_percent": 10, "complete": False}}),
        ]
        for rows, kwargs in scenarios:
            with self.subTest(kwargs=kwargs, rows=rows):
                decision = decide_business(rows, **kwargs)
                self.assertEqual(decision["status"], "manual_review")
                self.assertEqual(decision["rule_candidates"], [])
                self.assertEqual(len(decision["decision_reasons"]), 5)
        self.assertEqual(decide_business([], calculation_complete=True)["status"], "do_not_bid")

    def test_final_profitability_precedes_preliminary_reserve(self):
        for final, expected in [(25, "bid"), (10, "do_not_bid")]:
            with self.subTest(final=final):
                rows = [offer(f"{n}.ru", price=100+n,
                              profitability=1, profitability_basis="preliminary_reserve_percent",
                              economics={"profitability_percent": final}) for n in range(3)]
                original = copy.deepcopy(rows)
                decision = decide_business(rows)
                self.assertEqual(decision["status"], expected)
                self.assertEqual(decision["final_profitability"], final)
                self.assertEqual(decision["profitability_basis"], "profitability_percent")
                self.assertEqual(rows, original)
        # Полный итог всей закупки имеет приоритет и над показателями кандидатов.
        final = {"profitability_percent": 10, "complete": True, "mandatory_expenses_included": True}
        self.assertEqual(decide_business(three(), final_economics=final)["status"], "do_not_bid")
        final["profitability_percent"] = 25
        rows = [offer(f"{n}.ru", profitability=10) for n in range(3)]
        decision = decide_business(rows, final_economics=final)
        self.assertEqual(decision["status"], "manual_review")
        self.assertEqual(decision["final_profitability"], 25)
        self.assertIn("25%", decision["decision_reasons"][1])

    def test_critical_supplier_reserve_requires_confirmed_stock_and_clean_checks(self):
        for fields in [{"availability_normalized": "unknown"}, {"antifraud_status": "yellow"},
                       {"antifraud_status": "green", "verification_status": "🟡 РУЧНАЯ ПРОВЕРКА"},
                       {"risk_flags": ["Требуется подтвердить получателя счёта"]}]:
            with self.subTest(fields=fields):
                rows = three()
                rows[1].update(fields)
                self.assertEqual(decide_business(rows)["status"], "manual_review")
        rows = three() + [offer("reserve.ru", availability_normalized="unknown", critical_supplier=True)]
        self.assertEqual(decide_business(rows)["status"], "manual_review")

    def test_result_adapter_preserves_final_economy_and_expense_uncertainty(self):
        for final, expected in [(25, "manual_review"), (10, "manual_review")]:
            rows = [offer(f"{n}.ru", price=100+n, profitability=1,
                          profitability_basis="preliminary_reserve_percent",
                          economics={"profitability_percent": final}) for n in range(3)]
            result = {"supplier_search": {"ranked_offers": rows},
                      "procurement": {"commission_fee": 3},
                      "final_economics": {"net_profit_percent": final, "eat_commission": 3,
                                          "commission_source": "procurement.commission_fee",
                                          "commission_verified": True, "bid_price": 130,
                                          "nmck": 200, "complete": True,
                                          "mandatory_expenses_included": True}}
            original = copy.deepcopy(rows)
            self.assertEqual(attach_business_decision(result)["status"], expected)
            self.assertEqual(rows, original)
        for state in [{"ready": False, "reason": "Доставка не оценена"},
                      {"ready": True, "amount": 500}]:
            with self.subTest(state=state):
                result = {"supplier_search": {"ranked_offers": three()},
                          "audit": {"additional_expense_state": state},
                          "economics": {"profitability_percent": 25, "complete": True,
                                        "operating_costs": 100, "mandatory_expenses_included": True}}
                self.assertEqual(attach_business_decision(result)["status"], "manual_review")
        result = {"supplier_search": {"ranked_offers": three()},
                  "audit": {"special_conditions": "Обязателен монтаж"}}
        self.assertEqual(attach_business_decision(result)["status"], "manual_review")
        final = {"profitability_percent": 25, "commission": 3,
                 "commission_source": "procurement.commission_fee", "commission_verified": True,
                 "complete": True, "mandatory_expenses_included": True,
                 "basket": [{"supplier_name": "a.ru", "unit_price": 100}]}
        result = {"supplier_search": {"ranked_offers": three()}, "procurement": {"commission_fee": 3},
                  "final_economics": final,
                  "economic_precheck": {"preliminary_reserve_percent": 1,
                    "basket": [{"supplier_name": "a.ru", "unit_price": 100}]}}
        self.assertEqual(attach_business_decision(result)["status"], "manual_review")
        self.assertEqual(result["business_decision"]["recommended_supplier"]["profitability"], 25)
        result["audit"] = {"additional_expense_state": {"ready": True, "amount": 500}}
        result["final_economics"]["operating_costs"] = 500
        result["mandatory_expenses_included"] = False
        self.assertEqual(attach_business_decision(result)["status"], "manual_review")
        result.pop("mandatory_expenses_included")
        result["costs"] = {"all_costs_known": False}
        self.assertEqual(attach_business_decision(result)["status"], "manual_review")

        derived = {"procurement": {"nmck": 200, "commission_fee": 3},
                   "audit": {"additional_expense_state": {"ready": True, "amount": 1}},
                   "supplier_search": {"ranked_offers": three()}}
        self.assertEqual(attach_business_decision(derived)["status"], "bid")
        self.assertAlmostEqual(derived["final_economics"]["average_purchase_price"], 110)
        self.assertAlmostEqual(derived["final_economics"]["bid_price"], 129.8)
        derived["procurement"]["nmck"] = 120
        derived.pop("final_economics")
        derived.pop("calculation_complete")
        self.assertEqual(attach_business_decision(derived)["status"], "do_not_bid")

    def test_bid_formula_uses_markup_tax_and_net_profit(self):
        economics = calculate_bid_economics(100, 1, 1, 130)
        self.assertAlmostEqual(economics["bid_price"], 118)
        self.assertAlmostEqual(economics["total_expenses"], 102)
        self.assertAlmostEqual(economics["tax_base"], 16)
        self.assertAlmostEqual(economics["usn_tax"], 2.4)
        self.assertAlmostEqual(economics["net_profit"], 13.6)
        self.assertAlmostEqual(economics["net_profit_percent"], 13.6)
        self.assertTrue(economics["passes"])

    def test_bid_formula_rejects_price_above_nmck(self):
        economics = calculate_bid_economics(100, 1, 1, 117)
        self.assertFalse(economics["passes_nmck"])
        self.assertTrue(economics["passes_net_profit"])
        self.assertFalse(economics["passes"])
        decision = decide_business(three(), final_economics=economics)
        self.assertEqual(decision["status"], "do_not_bid")
        self.assertIn("выше НМЦК", decision_text(decision))

        saved = {"net_profit_percent": 20, "bid_price": 130, "nmck": 120,
                 "complete": True, "mandatory_expenses_included": True}
        self.assertEqual(decide_business(three(), final_economics=saved)["status"], "do_not_bid")

    def test_bid_formula_rejects_net_profit_below_twelve(self):
        economics = calculate_bid_economics(100, 5, 3, 130)
        self.assertAlmostEqual(economics["net_profit_percent"], 8.5)
        self.assertFalse(economics["passes_net_profit"])
        decision = decide_business(three(), final_economics=economics)
        self.assertEqual(decision["status"], "do_not_bid")

    def test_average_purchase_price_comes_from_first_three_offers(self):
        economics = calculate_bid_economics_from_offers(
            [{"confirmed_price": 100}, {"confirmed_price": 110},
             {"confirmed_price": 120}, {"confirmed_price": 999}], 0, 0, 200)
        self.assertAlmostEqual(economics["average_purchase_price"], 110)
        self.assertAlmostEqual(economics["bid_price"], 129.8)
        self.assertTrue(economics["passes"])

    def test_bid_formula_missing_input_is_incomplete(self):
        result = calculate_bid_economics(100, None, 2, 130)
        self.assertFalse(result["complete"])
        self.assertIn("additional_expenses", result["missing_inputs"])

        payload = {"procurement": {"nmck": 200, "commission_fee": 2},
                   "supplier_search": {"ranked_offers": three()}}
        self.assertEqual(attach_business_decision(payload)["status"], "manual_review")


if __name__ == "__main__":
    unittest.main()
