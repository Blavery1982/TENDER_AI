import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from filters.eat_filters import load_config
from filters.semantic_bad_words import filter_purchase_v2
from pipeline.batch_orchestrator import (_safe_text, prioritize_fixtures,
                                         process_item, procurement_queue, run_batch)


def raw_item(name="Монитор", quantity=1, unit_price=200000):
    return {"name":name,"description":name,"quantity":quantity,"unitPrice":unit_price,
            "sum":quantity*unit_price,"okeiTitle":"шт.","okpd2Code":"26.20.17"}


def fixture(pid="p1", trade="1", count=1, subject="Поставка мониторов", **raw_changes):
    raw={"id":pid,"tradeNumber":trade,"subject":subject,"price":200000,
         "lotItems":[raw_item() for _ in range(count)],"deliveryInfos":[{"deliveryAddress":{"regionName":"г Москва"}}]}
    raw.update(raw_changes)
    return {"purchase_id":pid,"raw":raw,"documents":[],
            "purchase_type_title":"Закупка до 600 000 руб. (п. 4 ч.1 ст. 93 Закона №44-ФЗ)"}


def paths(root):
    return {"checkpoint_path":root/"checkpoint.json","output_dir":root/"runs",
            "summary_path":root/"summary.md","log_path":root/"run.log"}


class BatchOrchestratorTests(unittest.TestCase):
    def test_01_main_without_args_is_safe(self):
        with patch.object(sys,"argv",["main.py"]),io.StringIO() as out,contextlib.redirect_stdout(out):
            self.assertEqual(main.main(),0); self.assertIn("Система запущена успешно",out.getvalue())
    def test_02_no_live_eat(self):
        with tempfile.TemporaryDirectory() as d:
            x=run_batch([fixture()],**paths(Path(d))); self.assertFalse(x["summary"]["live_eat_called"])
    def test_03_no_live_supplier_search(self):
        with tempfile.TemporaryDirectory() as d:
            x=run_batch([fixture()],**paths(Path(d))); self.assertFalse(x["summary"]["live_supplier_search_called"])
    def test_04_no_google_write(self):
        with tempfile.TemporaryDirectory() as d:
            x=run_batch([fixture()],**paths(Path(d))); self.assertFalse(x["summary"]["google_sheets_written"])
    def test_05_multiple_procurements(self):
        with tempfile.TemporaryDirectory() as d:
            x=run_batch([fixture(),fixture("p2","2")],**paths(Path(d))); self.assertEqual(x["summary"]["total_procurements"],2)
    def test_06_procurement_error_isolated(self):
        with tempfile.TemporaryDirectory() as d:
            x=run_batch([fixture(),fixture("p2","2")],faults={"p1":"procurement"},**paths(Path(d)))
            self.assertEqual((x["summary"]["failed_procurements"],x["summary"]["completed_procurements"]),(1,1))
    def test_07_all_items_processed(self):
        with tempfile.TemporaryDirectory() as d:
            x=run_batch([fixture(count=4)],**paths(Path(d))); self.assertEqual(x["summary"]["total_items"],4)
    def test_08_item_error_isolated(self):
        with tempfile.TemporaryDirectory() as d:
            x=run_batch([fixture(count=3)],faults={"p1:2":"item"},**paths(Path(d)))
            self.assertEqual((x["summary"]["completed_items"],x["summary"]["failed_items"]),(2,1))
    def test_09_checkpoint_created(self):
        with tempfile.TemporaryDirectory() as d:
            p=paths(Path(d)); run_batch([fixture()],**p); self.assertTrue(p["checkpoint_path"].is_file())
    def test_10_resume_skips_completed_procurement(self):
        with tempfile.TemporaryDirectory() as d:
            p=paths(Path(d)); run_batch([fixture()],**p); x=run_batch([fixture()],resume=True,**p)
            self.assertEqual(x["summary"]["skipped_completed_on_resume"],1)
    def test_11_resume_does_not_duplicate_completed_item(self):
        with tempfile.TemporaryDirectory() as d:
            p=paths(Path(d)); run_batch([fixture(count=2)],faults={"p1:2":"item"},**p)
            x=run_batch([fixture(count=2)],resume=True,**p); self.assertEqual(len(x["procurements"][0]["items"]),2)
    def test_12_partial_saved(self):
        with tempfile.TemporaryDirectory() as d:
            x=run_batch([fixture(count=2)],faults={"p1:1":"item"},**paths(Path(d)))
            self.assertEqual(x["procurements"][0]["final_status"],"partial")
    def test_13_customer_once_per_procurement(self):
        calls=[]
        def customer(*args): calls.append(1); return {"warnings":[],"manual_actions_required":[]}
        with tempfile.TemporaryDirectory() as d: run_batch([fixture(count=3)],customer_builder=customer,**paths(Path(d)))
        self.assertEqual(len(calls),1)
    def test_14_traceability_each_item(self):
        with tempfile.TemporaryDirectory() as d:
            x=run_batch([fixture(count=3)],**paths(Path(d))); self.assertTrue(all("traceability" in i for i in x["procurements"][0]["items"]))
    def test_15_threshold_percent(self):
        with tempfile.TemporaryDirectory() as d:
            x=run_batch([fixture()],**paths(Path(d))); self.assertEqual(x["procurements"][0]["items"][0]["supplier_target_discount_percent"],15)
    def test_16_threshold_value(self):
        f=fixture(); f["raw"]["lotItems"][0]["unitPrice"]=25400
        with tempfile.TemporaryDirectory() as d:
            x=run_batch([f],**paths(Path(d))); self.assertEqual(x["procurements"][0]["items"][0]["supplier_target_price"],21590)
    def test_17_subsidy_not_rejected(self):
        x=filter_purchase_v2(fixture(subject="Поставка за счёт субсидии")["raw"],fixture()["purchase_type_title"])
        self.assertNotEqual(x["filter_result"],"rejected"); self.assertEqual(x["special_conditions"],"СУБСИДИИ")
    def test_18_max_items_15(self): self.assertEqual(load_config()["max_items"],15)
    def test_19_linoleum_not_rejected(self):
        x=filter_purchase_v2(fixture(subject="Поставка линолеума")["raw"],fixture()["purchase_type_title"])
        self.assertFalse(any("линолеум" in r for r in x["rejection_reasons"]))
    def test_20_auction_rejected(self):
        x=filter_purchase_v2(fixture(purchaseMethodTitle="Электронный аукцион")["raw"],fixture()["purchase_type_title"])
        self.assertIn("auction_not_allowed",x["rejection_reasons"])
    def test_21_defense_rejected(self):
        x=filter_purchase_v2(fixture(stateDefenseOrder=True)["raw"],fixture()["purchase_type_title"])
        self.assertIn("state_defense_order_not_allowed",x["rejection_reasons"])
    def test_22_semantic_v2_used(self):
        x=filter_purchase_v2(fixture()["raw"],fixture()["purchase_type_title"]); self.assertIn("hard_exclusion_matches",x)
    def test_23_public_price_not_purchase(self):
        with tempfile.TemporaryDirectory() as d:
            x=run_batch([fixture()],**paths(Path(d))); self.assertIsNone(x["procurements"][0]["items"][0]["calculator"]["purchase_price"])
    def test_24_document_error_does_not_break_batch(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); doc=root/"x.pdf"; doc.write_bytes(b"x"); f=fixture(); f["documents"]=[{"local_path":str(doc),"file_name":"x.pdf"}]
            x=run_batch([f,fixture("p2","2")],**paths(root))
            self.assertEqual((x["summary"]["blocked_procurements"], x["summary"]["completed_procurements"]),(1,1))
    def test_25_secrets_redacted_in_log_text(self):
        text=_safe_text("Authorization=abc Cookie:xyz token=q password=p private_key=k")
        for value in ("abc","xyz","=q","=p","=k"): self.assertNotIn(value,text)

    def test_26_exact_model_queue_is_first_and_deadline_sorted(self):
        late=fixture("p1","1",subject="Поставка ИБП")
        late["raw"]["lotItems"][0]["description"]="CyberPower PR1500ELCD"
        late["raw"]["applicationFillingEndDate"]="2026-09-15T10:00:00"
        early=fixture("p2","2",subject="Поставка кондиционера")
        early["raw"]["lotItems"][0]["description"]="Royal Clima RC-TWN28HN"
        early["raw"]["applicationFillingEndDate"]="2026-09-14T10:00:00"
        no_model=fixture("p3","3")
        ordered=prioritize_fixtures([no_model,late,early])
        self.assertEqual([x["purchase_id"] for x in ordered],["p2","p1","p3"])

    def test_27_price_justification_model_can_promote_after_documents(self):
        resolved={"original_model":None,"pricing_reference_model":"Model X100",
                  "model_search_mode":"MODEL_DISCOVERY_REQUIRED",
                  "source_warnings":[],"source_conflicts":[]}
        self.assertEqual(procurement_queue(fixture(),[resolved])["queue"],"PRIORITY_1_EXACT_MODEL")

    def test_28_multi_item_priority_one_requires_exact_model_for_every_item(self):
        f=fixture(count=2)
        exact={"original_model":"Model X100","model_search_mode":"EXACT_MODEL_ONLY",
               "source_warnings":[],"source_conflicts":[]}
        unresolved={"original_model":None,"pricing_reference_model":None,
                    "model_search_mode":"MODEL_DISCOVERY_REQUIRED",
                    "source_warnings":[],"source_conflicts":[]}
        self.assertEqual(procurement_queue(f,[exact,unresolved])["queue"],
                         "PRIORITY_2_MODEL_NOT_SPECIFIED")
        second={**exact,"original_model":"Model Y200"}
        result=procurement_queue(f,[exact,second])
        self.assertEqual(result["queue"],"PRIORITY_1_EXACT_MODEL")
        self.assertEqual(result["exact_models"],["Model X100","Model Y200"])

    def test_29_ambiguous_document_model_stays_priority_two(self):
        resolved={"original_model":None,"pricing_reference_model":"Model X100",
                  "model_search_mode":"MODEL_MODE_REVIEW_REQUIRED",
                  "source_warnings":["Неоднозначная привязка документа к позиции"],
                  "source_conflicts":[]}
        self.assertEqual(procurement_queue(fixture(),[resolved])["queue"],
                         "PRIORITY_2_MODEL_NOT_SPECIFIED")

    def test_30_transport_services_are_rejected_by_procurement_kind(self):
        f=fixture(subject="Транспортные услуги №26606", count=3)
        for kind,item in zip(("легковой","автобус","микроавтобус"),f["raw"]["lotItems"]):
            item["name"]=f"Транспортные услуги ({kind})"
            item["description"]="В соответствии с условиями проекта контракта"
        decision=filter_purchase_v2(f["raw"],f["purchase_type_title"])
        self.assertEqual(decision["procurement_kind"],"services")
        self.assertEqual(decision["filter_result"],"rejected")
        self.assertIn("procurement_kind_services",decision["rejection_reasons"])

    def test_31_transport_word_in_physical_good_is_not_broadly_blocked(self):
        f=fixture(subject="Поставка транспортировочных тележек")
        f["raw"]["lotItems"][0].update(
            name="Транспортировочная тележка",description="Тележка металлическая")
        decision=filter_purchase_v2(f["raw"],f["purchase_type_title"])
        self.assertEqual(decision["procurement_kind"],"goods")
        self.assertNotIn("procurement_kind_services",decision["rejection_reasons"])

    def test_32_procurement_model_bypasses_discovery_even_when_live_enabled(self):
        customer={"warnings":[],"manual_actions_required":[]}
        item=raw_item(name="CyberPower PR1500ELCD")
        with patch("pipeline.batch_orchestrator.discover_models") as discovery:
            result=process_item(item,1,"100",customer,model_live=True)
        discovery.assert_not_called()
        self.assertEqual(result["price_readiness"]["classification"],"PRICE_SEARCH_READY")
        self.assertFalse(result["model_search"]["compliance_required_before_price_search"])


if __name__=="__main__": unittest.main()
