import copy
import json
import unittest

from pipeline.orchestrator import (AUDIT_MODEL, CARD, SUPPLIERS, VERIFICATION,
                                   build_pipeline, load_json)
from suppliers.verification import HIGH_RISK, MANUAL


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.card=load_json(CARD); cls.audit=load_json(AUDIT_MODEL)
        cls.supplier=load_json(SUPPLIERS); cls.deep=load_json(VERIFICATION)

    def result(self, **changes):
        values={"card":copy.deepcopy(self.card),"audit_model":copy.deepcopy(self.audit),
                "supplier":copy.deepcopy(self.supplier),"deep":copy.deepcopy(self.deep)}
        values.update(changes); return build_pipeline(**values)

    def test_01_procurement_flows_to_audit(self):
        x=self.result()
        self.assertEqual(x["procurement"]["procurement_number"],x["technical_audit"] and "100205573126100053")

    def test_02_audit_flows_to_model_search(self):
        x=self.result(); self.assertEqual(len(x["model_search"]["model_evidence"]),13)

    def test_03_model_flows_to_supplier_search(self):
        x=self.result(); self.assertEqual(x["model_search"]["selected_model"],"Royal Clima RC-TWN28HN")
        self.assertEqual(x["supplier_search"]["supplier_price_threshold"],20320)

    def test_04_supplier_flows_to_verification(self):
        x=self.result(); self.assertGreater(x["supplier_verification"]["checked"],0)

    def test_05_verification_flows_to_calculator(self):
        x=self.result(); self.assertEqual(x["calculator"]["status"],x["final_decision"]["final_status"])

    def test_06_public_price_is_not_purchase_price(self):
        x=self.result(); self.assertIsNotNone(x["model_search"]["public_price"])
        self.assertIsNone(x["calculator"]["purchase_price"])

    def test_07_missing_purchase_price_reaches_final(self):
        self.assertEqual(self.result()["final_decision"]["final_status"], "🟡 РУЧНАЯ ПРОВЕРКА")

    def test_08_unknown_delivery_is_not_zero(self):
        x=self.result(); self.assertIn("delivery_cost",x["calculator"]["unknown_costs"])
        self.assertNotIn("delivery_cost",x["calculator"]["known_costs"])

    def test_09_manual_supplier_does_not_block_procurement(self):
        x=self.result(); self.assertGreaterEqual(x["supplier_verification"]["manual_review"],1)
        self.assertNotIn("НЕ УЧАСТВОВАТЬ",x["final_decision"]["final_status"])

    def test_10_red_supplier_is_not_admitted_to_quote(self):
        supplier=copy.deepcopy(self.supplier); deep=copy.deepcopy(self.deep)
        candidate=supplier["supplier_candidates"][0]
        supplier["quotes"]["kp2"]={"supplier_name":candidate["supplier_name"],"product_url":candidate["product_url"],"public_price":1,"purchase_price":None}
        domain=next((x["domain"] for x in deep["sites"] if x["domain"] in candidate["product_url"]),None)
        if domain: next(x for x in deep["sites"] if x["domain"]==domain)["verification_status"]=HIGH_RISK
        self.assertIsNone(self.result(supplier=supplier,deep=deep)["supplier_search"]["kp2"])

    def test_11_empty_quotes_are_not_automatic_rejection(self):
        x=self.result(); self.assertFalse(any(x["supplier_search"][k] for k in ("kp1","kp2","kp3")))
        self.assertEqual(x["final_decision"]["final_status"], "🟡 РУЧНАЯ ПРОВЕРКА")

    def test_12_warnings_are_collected(self):
        self.assertIn("Стоимость доставки неизвестна",self.result()["warnings"])

    def test_13_manual_actions_are_collected(self):
        self.assertGreaterEqual(len(self.result()["manual_actions_required"]),6)

    def test_14_pipeline_survives_incomplete_stage(self):
        x=build_pipeline(copy.deepcopy(self.card),copy.deepcopy(self.audit),copy.deepcopy(self.supplier),copy.deepcopy(self.deep),{"audit"})
        self.assertEqual(x["technical_audit"]["technical_spec_status"],"Этап не завершён")
        self.assertIn("final_status",x["final_decision"])

    def test_15_user_statuses_are_russian(self):
        x=self.result(); self.assertEqual(x["user_summary"]["status"], "🟡 РУЧНАЯ ПРОВЕРКА")

    def test_16_user_output_has_no_null_none_nan(self):
        text=json.dumps(self.result()["user_summary"],ensure_ascii=False)
        self.assertNotIn("null",text); self.assertNotIn("None",text); self.assertNotIn("NaN",text)
        self.assertIn("Нет данных",text)

    def test_17_scenarios_never_become_real_price(self):
        x=self.result(); self.assertIsNone(x["calculator"]["purchase_price"])
        self.assertIsInstance(x["scenario_results_reference"],str)

    def test_18_final_status_matches_missing_purchase_price(self):
        self.assertEqual(self.result()["final_decision"]["final_status"], "🟡 РУЧНАЯ ПРОВЕРКА")

    def test_19_procurement_model_bypasses_compliance_in_legacy_orchestrator(self):
        card=copy.deepcopy(self.card)
        lot=(card.get("raw") or {}).get("lot") or card.get("raw") or {}
        lot["lotItems"][0]["model"]="CyberPower PR1500ELCD"
        audit=copy.deepcopy(self.audit)
        audit["models_for_supplier_search"]=[]
        result=self.result(card=card,audit_model=audit)
        self.assertEqual(result["model_search"]["selected_model"],"CyberPower PR1500ELCD")
        self.assertEqual(result["model_search"]["model_status"],"PRICE_SEARCH_READY")
        self.assertFalse(result["model_search"]["compliance_required_before_price_search"])


if __name__=="__main__": unittest.main()
