import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from model_search.live_discovery import (discover_models, generate_queries,
    normalize_requirements, same_exact_model)
from pipeline.batch_orchestrator import run_batch


REQ=[{"requirement_name":"Мощность охлаждения","value":"2.8 кВт"},
     {"requirement_name":"Цвет","value":"белый"}]
ITEM={"item_name":"Кондиционер","structured_requirements":REQ,"customer_unit_price":25400}


class Provider:
    def __init__(self, fail=None): self.search_calls=0; self.fetch_calls=0; self.fail=fail
    def search(self, query, limit):
        self.search_calls+=1
        if self.fail=="search": raise RuntimeError("429")
        return [{"title":"Royal Clima RC-TWN28HN характеристики","url":"https://royal.example/RC-TWN28HN"},
                {"title":"RC-TWN28HN цена","url":"https://shop.ru/RC-TWN28HN"}]
    def fetch(self,url):
        self.fetch_calls+=1
        if self.fail=="official" and "royal" in url: raise TimeoutError()
        text="RC-TWN28HN\nМощность охлаждения: 2.8 кВт\nЦвет: белый"
        if "royal" in url: return {"url":url,"text":text,"source_type":"manufacturer","source_verified":True}
        return text+"\nВ наличии\nЦена: 21 000 руб"


class LiveDiscoveryTests(unittest.TestCase):
    def discover(self, **kwargs):
        with tempfile.TemporaryDirectory() as d:
            return discover_models(ITEM,provider=kwargs.pop("provider",Provider()),cache_dir=Path(d),**kwargs)

    def test_01_normalizes_pipeline_requirements(self): self.assertEqual(normalize_requirements(REQ)[0]["parameter"],"Мощность охлаждения")
    def test_02_generates_multiple_queries(self): self.assertGreaterEqual(len(generate_queries(ITEM)),2)
    def test_03_query_contains_item(self): self.assertTrue(all("Кондиционер" in x for x in generate_queries(ITEM)))
    def test_04_query_limit(self): self.assertLessEqual(len(generate_queries(ITEM,2)),2)
    def test_05_exact_dedupe(self): self.assertTrue(same_exact_model("RC-TWN28HN","rc-twn28hn"))
    def test_06_similar_sku_not_merged(self): self.assertFalse(same_exact_model("RC-TWN28HN","RC-TWN28HN/IN"))
    def test_07_all_confirmed_compliant(self): self.assertEqual(self.discover()["fully_compliant_count"],1)
    def test_08_unconfirmed_not_compliant(self):
        x=dict(ITEM,structured_requirements=[*REQ,{"requirement_name":"Шум","value":"20 дБ"}]);
        with tempfile.TemporaryDirectory() as d: self.assertEqual(discover_models(x,provider=Provider(),cache_dir=Path(d))["fully_compliant_count"],0)
    def test_09_selected_has_official_priority(self): self.assertTrue(self.discover()["selected_model"]["official_sources"])
    def test_10_russia_availability_separate(self): self.assertEqual(self.discover()["selected_model"]["russia_availability"],"available")
    def test_11_public_price_saved(self): self.assertEqual(self.discover()["selected_model_public_price"],21000)
    def test_12_public_not_purchase(self): self.assertIsNone(self.discover()["purchase_price"])
    def test_13_checked_at(self): self.assertIn("checked_at",self.discover())
    def test_14_source_error_isolated(self): self.assertIn("Источник", " ".join(self.discover(provider=Provider("official"))["warnings"]))
    def test_15_search_error_partial(self): self.assertEqual(self.discover(provider=Provider("search"))["search_status"],"partial")
    def test_16_cache_hit(self):
        with tempfile.TemporaryDirectory() as d:
            p=Provider(); discover_models(ITEM,provider=p,cache_dir=Path(d)); x=discover_models(ITEM,provider=p,cache_dir=Path(d)); self.assertEqual(x["cache_status"],"fresh_hit"); self.assertEqual(p.search_calls,3)
    def test_17_stale_cache_refreshes(self):
        with tempfile.TemporaryDirectory() as d:
            p=Provider(); x=discover_models(ITEM,provider=p,cache_dir=Path(d)); f=next(Path(d).glob("*.json")); x["checked_at"]="2000-01-01T00:00:00+00:00"; f.write_text(json.dumps(x)); discover_models(ITEM,provider=p,cache_dir=Path(d)); self.assertEqual(p.search_calls,6)
    def test_18_justification_first(self):
        x=dict(ITEM,model_from_justification="TEST-123")
        with tempfile.TemporaryDirectory() as d: self.assertEqual(discover_models(x,provider=Provider(),cache_dir=Path(d))["candidates"][0]["exact_model"],"TEST-123")
    def test_19_bad_justification_does_not_stop(self):
        x=dict(ITEM,model_from_justification="BAD-999")
        with tempfile.TemporaryDirectory() as d: self.assertGreaterEqual(discover_models(x,provider=Provider(),cache_dir=Path(d))["candidates_found"],2)
    def test_20_price_not_compliance(self): self.assertEqual(self.discover()["selected_model"]["status"],"fully_compliant")
    def test_21_cache_has_split_freshness(self):
        x=self.discover(); self.assertIn("technical_fresh_until",x); self.assertIn("market_fresh_until",x)
    def test_22_candidate_limit(self): self.assertLessEqual(self.discover(candidate_limit=1)["candidates_found"],1)


class BatchModelTests(unittest.TestCase):
    def fixture(self):
        return {"purchase_id":"p","raw":{"id":"p","tradeNumber":"1","subject":"Кондиционер","price":200000,
          "lotItems":[{"name":"Кондиционер","description":"Мощность: 2.8 кВт","quantity":1,"unitPrice":200000}],"deliveryInfos":[]},"documents":[]}
    def paths(self,d):
        r=Path(d); return {"checkpoint_path":r/"c.json","output_dir":r/"runs","summary_path":r/"s.md","log_path":r/"l.log"}
    @patch("pipeline.batch_orchestrator.discover_models")
    def test_23_dry_run_default_no_web(self,discovery):
        with tempfile.TemporaryDirectory() as d: x=run_batch([self.fixture()],**self.paths(d))
        discovery.assert_not_called(); self.assertFalse(x["summary"]["live_model_search_called"])
    @patch("pipeline.batch_orchestrator.discover_models")
    def test_24_live_flag_calls_model_only(self,discovery):
        discovery.return_value={"search_status":"completed","live_queries_count":2,"selected_model":None}
        with tempfile.TemporaryDirectory() as d: x=run_batch([self.fixture()],model_live_test=True,**self.paths(d))
        discovery.assert_called_once(); self.assertTrue(x["summary"]["live_model_search_called"]); self.assertFalse(x["summary"]["live_supplier_search_called"])
    @patch("pipeline.batch_orchestrator.discover_models")
    def test_25_item_error_isolated(self,discovery):
        discovery.side_effect=[RuntimeError("site"),{"search_status":"completed","live_queries_count":1}]
        f=self.fixture(); f["raw"]["lotItems"]*=2
        with tempfile.TemporaryDirectory() as d: x=run_batch([f],model_live_test=True,**self.paths(d))
        self.assertEqual(x["summary"]["failed_items"],1)
    @patch("pipeline.batch_orchestrator.discover_models")
    def test_26_checkpoint_model_stage(self,discovery):
        discovery.return_value={"search_status":"completed","live_queries_count":1}
        with tempfile.TemporaryDirectory() as d:
            p=self.paths(d); run_batch([self.fixture()],model_live_test=True,**p); c=json.loads(p["checkpoint_path"].read_text())
        self.assertEqual(c["procurements"]["p"]["stage_statuses"]["model_discovery"],"completed")
    @patch("pipeline.batch_orchestrator.discover_models")
    def test_27_resume_skips_fresh_completed(self,discovery):
        discovery.return_value={"search_status":"completed","live_queries_count":1}
        with tempfile.TemporaryDirectory() as d:
            p=self.paths(d); run_batch([self.fixture()],model_live_test=True,**p); run_batch([self.fixture()],model_live_test=True,resume=True,**p)
        self.assertEqual(discovery.call_count,1)
    @patch("pipeline.batch_orchestrator._saved_supplier_result")
    def test_28_supplier_never_invoked(self,supplier):
        with tempfile.TemporaryDirectory() as d: run_batch([self.fixture()],**self.paths(d))
        supplier.assert_not_called()

if __name__=="__main__": unittest.main()
