import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from documents.pipeline import (PDF_UNREADABLE_COMMENT, audit_from_extraction,
                                document_processing_stop_reason, document_sha256,
                                process_procurement_documents)
from documents.text_extraction import OCR_TIMEOUT_SECONDS, extract_pdf
from documents import text_extraction
from pipeline.batch_orchestrator import run_batch
from tests.test_batch_orchestrator import fixture, paths


def extracted(text="Техническое задание", method="text_layer", pages=None, warnings=None):
    return {"text":text,"document_parse_status":"analyzed","read_method":method,
            "ocr_engine":"Tesseract 5 rus+eng" if method in {"ocr","mixed"} else None,
            "ocr_pages":[1] if method in {"ocr","mixed"} else [],
            "pages":pages or [{"page_number":1,"read_method":method,"text":text,"average_confidence":90 if method=="ocr" else None}],
            "raw_ocr_text":text if method=="ocr" else "","critical_values":[],
            "doubtful_critical_values":[],"warnings":warnings or []}


def doc_fixture(count=2, structured=False):
    raw={"id":"p","tradeNumber":"1","subject":"Поставка товаров","price":200000,
         "lotItems":[{"name":f"Товар {i}","description":f"Параметр {i}: значение {i}","quantity":1,"unitPrice":100000,"okeiTitle":"шт."} for i in range(1,count+1)]}
    if structured:
        for i, item in enumerate(raw["lotItems"], 1):
            item["structured_requirements"] = [{"parameter": f"Параметр {i}", "value": f"значение {i}"}]
    return {"purchase_id":"p","raw":raw,"documents":[]}


class DocumentPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); self.cache=self.root/"cache"
    def tearDown(self): self.tmp.cleanup()
    def file(self,name="a.pdf",content=b"one"):
        p=self.root/name;p.write_bytes(content);return p
    def process(self,docs,extractor=lambda p:extracted()):
        return process_procurement_documents(doc_fixture(),docs,cache_dir=self.cache,extractor=extractor)

    def test_01_multiple_documents(self):
        x=self.process([{"local_path":str(self.file("a.pdf"))},{"local_path":str(self.file("b.pdf",b"b"))}]); self.assertEqual(x["documents_processed"],2)
    def test_02_text_layer_pdf(self): self.assertEqual(self.process([{"local_path":str(self.file())}])["document_results"][0]["extraction_method"],"text_layer")
    def test_03_scan_ocr_fallback(self): self.assertTrue(self.process([{"local_path":str(self.file())}],lambda p:extracted(method="ocr"))["document_results"][0]["ocr_used"])
    def test_04_mixed_pdf(self): self.assertEqual(self.process([{"local_path":str(self.file())}],lambda p:extracted(method="mixed"))["extraction_summary"]["mixed_documents"],1)
    def test_05_ocr_page_error_partial(self):
        pages=[{"page_number":1,"read_method":"ocr","text":"ok","average_confidence":90},{"page_number":2,"read_method":"failed","text":"","error":"OCR failed"},{"page_number":3,"read_method":"ocr","text":"ok","average_confidence":90}]
        x=self.process([{"local_path":str(self.file())}],lambda p:extracted("ok",method="ocr",pages=pages,warnings=["page 2"])); self.assertEqual(x["document_results"][0]["status"],"partial")
    def test_06_one_document_error(self):
        good=self.file("good.pdf"); bad=self.file("bad.pdf",b"bad")
        def reader(p):
            if p.name=="bad.pdf": raise RuntimeError("bad")
            return extracted("Техническое задание")
        x=self.process([{"local_path":str(good)},{"local_path":str(bad)}],reader); self.assertEqual((x["documents_processed"],x["documents_failed"]),(1,1))
    def test_07_audit_gets_extracted_text(self):
        x=self.process([{"local_path":str(self.file())}],lambda p:extracted("Техническое задание Параметр 1: значение 1")); a=audit_from_extraction(doc_fixture(1),x); self.assertGreater(a["input_evidence"]["combined_text_length"],0)
    def test_08_saved_audit_does_not_override(self):
        f=doc_fixture(1);f["saved_audit"]={"special_conditions":"НЕВЕРНО"};x=self.process([{"local_path":str(self.file())}],lambda p:extracted("Техническое задание"));self.assertNotEqual(audit_from_extraction(f,x)["special_conditions"],"НЕВЕРНО")
    def test_09_missing_document(self): self.assertEqual(self.process([{"local_path":str(self.root/"none.pdf")}])["document_results"][0]["status"],"missing")
    def test_10_all_positions_requirements(self):
        a=audit_from_extraction(doc_fixture(3, structured=True),self.process([]));self.assertTrue(all(x["requirements"] for x in a["items"]))
    def test_11_two_positions(self): self.assertEqual(len(audit_from_extraction(doc_fixture(2),self.process([]))["items"]),2)
    def test_12_four_positions(self): self.assertEqual(len(audit_from_extraction(doc_fixture(4),self.process([]))["items"]),4)
    def test_13_requirements_not_mixed(self):
        items=audit_from_extraction(doc_fixture(2, structured=True),self.process([]))["items"];self.assertIn("1",items[0]["requirements"][0]["requirement_name"]);self.assertIn("2",items[1]["requirements"][0]["requirement_name"])
    def test_14_source_evidence(self): self.assertTrue(audit_from_extraction(doc_fixture(1, structured=True),self.process([]))["items"][0]["requirements"][0]["evidence"])
    def test_15_special_conditions(self):
        x=self.process([{"local_path":str(self.file())}],lambda p:extracted("Проект контракта. Поставщик обязан осуществить разгрузку своими силами."));self.assertIn("разгруз",audit_from_extraction(doc_fixture(1),x)["special_conditions"].lower())
    def test_16_subsidy_warning_not_reject(self):
        x=self.process([{"local_path":str(self.file())}],lambda p:extracted("Проект контракта. Финансирование за счет субсидии."));self.assertIn("СУБСИДИИ",audit_from_extraction(doc_fixture(1),x)["special_conditions"])
    def test_17_price_justification_not_blocking(self):
        x=self.process([{"file_name":"Обоснование НМЦК.pdf","local_path":str(self.file())}],lambda p:extracted("Обоснование НМЦК цена 100"));a=audit_from_extraction(doc_fixture(1),x);self.assertTrue(a["readiness_for_item_analysis"])
    def test_18_model_justification_is_candidate(self):
        x=self.process([{"file_name":"Обоснование НМЦК.pdf","local_path":str(self.file())}],lambda p:extracted("Обоснование НМЦК модель GE32LFN0"));a=audit_from_extraction(doc_fixture(1),x);self.assertEqual(a["model_from_justification"]["status"],"Только кандидат для проверки")
    def test_19_unreadable_fragment_manual_warning(self):
        f=doc_fixture(1);f["raw"]["lotItems"][0]["description"]="";f["raw"]["lotItems"][0]["name"]=""
        a=audit_from_extraction(f,self.process([]));self.assertIn("Требуется ручная проверка ТЗ",a["document_warnings"])
    def test_20_traceability_every_item_in_batch(self):
        with tempfile.TemporaryDirectory() as d:
            x=run_batch([fixture(count=4)],**paths(Path(d)));self.assertTrue(all("traceability" in i for i in x["procurements"][0]["items"]))
    def test_21_checkpoint_document_audit_stages(self):
        with tempfile.TemporaryDirectory() as d:
            p=paths(Path(d));run_batch([fixture()],**p);state=json.loads(p["checkpoint_path"].read_text())["procurements"]["p1"];self.assertEqual(state["stage_statuses"]["procurement_audit"],"completed")
    def test_22_resume_uses_completed_extraction(self):
        with tempfile.TemporaryDirectory() as d:
            p=paths(Path(d));run_batch([fixture(count=2)],faults={"p1:2":"item"},**p)
            with patch("pipeline.batch_orchestrator.process_procurement_documents",side_effect=AssertionError("must not run")):
                x=run_batch([fixture(count=2)],resume=True,**p)
            self.assertEqual(x["procurements"][0]["final_status"],"completed")
    def test_23_ocr_cache_no_repeat(self):
        calls=[];p=self.file();docs=[{"local_path":str(p)}]
        reader=lambda path:(calls.append(1) or extracted(method="ocr"))
        self.process(docs,reader);self.process(docs,reader);self.assertEqual(len(calls),1)
    def test_24_changed_hash_reprocesses(self):
        calls=[];p=self.file();docs=[{"local_path":str(p)}];reader=lambda path:(calls.append(1) or extracted())
        self.process(docs,reader);p.write_bytes(b"two");self.process(docs,reader);self.assertEqual(len(calls),2)
    def test_25_ocr_failure_does_not_stop_next_procurement(self):
        with tempfile.TemporaryDirectory() as d:
            x=run_batch([fixture(),fixture("p2","2")],**paths(Path(d)));self.assertEqual(x["summary"]["completed_procurements"],2)

    def test_26_unreadable_document_blocks_only_current_procurement(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); doc=root/"x.pdf"; doc.write_bytes(b"x")
            first=fixture(); first["documents"]=[{"local_path":str(doc),"file_name":"x.pdf"}]
            x=run_batch([first,fixture("p2","2")],**paths(root))
            self.assertEqual(x["summary"]["blocked_procurements"],1)
            self.assertEqual(x["summary"]["completed_procurements"],1)
            self.assertEqual(x["procurements"][0]["manual_stop_reason"],PDF_UNREADABLE_COMMENT)

    def test_27_partial_document_has_explicit_stop_reason(self):
        value={"document_results":[{"status":"partial"}]}
        self.assertEqual(document_processing_stop_reason(value),PDF_UNREADABLE_COMMENT)
    def test_26_log_excludes_raw_document(self):
        secret="VERY_LONG_RAW_DOCUMENT_SECRET";p=self.file();self.process([{"local_path":str(p)}],lambda path:extracted(secret));self.assertFalse((self.root/"run.log").exists())

    def test_extract_pdf_survives_one_ocr_page_error(self):
        class Layer: 
            def extract_text(self): return ""
        class Reader: pages=[Layer(),Layer(),Layer()]
        pages=[object(),object(),object()]
        with patch("documents.text_extraction.PdfReader",return_value=Reader()),patch("documents.text_extraction.pymupdf.open",return_value=pages),patch("documents.text_extraction._ocr",side_effect=[("one",[]),RuntimeError("bad"),("three",[])]):
            x=extract_pdf(self.file())
        self.assertEqual([p["read_method"] for p in x["pages"]],["ocr","failed","ocr"])

    def test_ocr_subprocess_has_finite_timeout(self):
        class Pixmap:
            def save(self, path): Path(path).write_bytes(b"image")
        class Page:
            number = 0
            def get_pixmap(self, **kwargs): return Pixmap()
        work = self.root / "ocr"
        work.mkdir()
        (work / "page_1.txt").write_text("text", encoding="utf-8")
        (work / "page_1.tsv").write_text("text\n", encoding="utf-8")
        with patch.object(text_extraction.subprocess, "run") as run:
            text_extraction._ocr(Page(), work)
        self.assertEqual(run.call_args.kwargs["timeout"], OCR_TIMEOUT_SECONDS)


if __name__=="__main__": unittest.main()
