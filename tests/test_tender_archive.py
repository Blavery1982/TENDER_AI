import tempfile
import unittest
from pathlib import Path

from documents.brand_detector import build_brand_lists, detect_brands
from documents.tender_archive import archive_metadata, migrate_legacy_tender, save_document


class TenderArchiveTests(unittest.TestCase):
    def test_each_tender_has_own_folder_and_duplicate_names_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, digest, created = save_document(b"one", "ТЗ.pdf", "tender-1", root=root)
            second, _, created_second = save_document(b"two", "ТЗ.pdf", "tender-1", root=root)
            other, _, _ = save_document(b"one", "ТЗ.pdf", "tender-2", root=root)
            self.assertTrue(created)
            self.assertTrue(created_second)
            self.assertEqual(first.parent, second.parent)
            self.assertNotEqual(first, second)
            self.assertNotEqual(first.parent, other.parent)
            self.assertEqual(len(digest), 64)

    def test_manifest_and_legacy_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, legacy = Path(tmp) / "tenders", Path(tmp) / "contracts"
            old = legacy / "p1"
            old.mkdir(parents=True)
            (old / "ТЗ.pdf").write_bytes(b"old")
            copied = migrate_legacy_tender("p1", root=root, legacy_root=legacy)
            self.assertEqual(len(copied), 1)
            metadata = archive_metadata("p1", tender_number="123", title="Поставка", root=root)
            self.assertEqual(metadata["tender_number"], "123")
            self.assertTrue((root / "p1" / "tender.json").exists())

    def test_brand_statuses_and_evidence(self):
        procurement = {"purchase_id": "p", "raw": {"subject": "Поставка оборудования", "lotItems": []}}
        extraction = {"documents_found": 1, "documents_failed": 0,
                      "extraction_summary": {"partial_documents": 0},
                      "document_results": [{"document_name": "ТЗ.pdf", "text": "Марка: Samsung", "pages": [{"page_number": 2, "text": "Марка: Samsung"}]}]}
        found = detect_brands(procurement, extraction)
        self.assertEqual(found["status"], "brand_found")
        self.assertEqual(found["brands"], ["Samsung"])
        self.assertEqual(found["hits"][0]["page"], 2)
        not_found = detect_brands(procurement, {**extraction, "document_results": [{"document_name": "ТЗ.pdf", "text": "Поставка оборудования"}]})
        self.assertEqual(not_found["status"], "brand_not_found")
        review = detect_brands(procurement, {**extraction, "documents_failed": 1})
        self.assertEqual(review["status"], "review_required")
        quoted = detect_brands(procurement, {**extraction, "document_results": [{"document_name": "Договор.docx", "text": "Федеральный закон «О бухгалтерском учете»"}]})
        self.assertEqual(quoted["status"], "brand_not_found")
        standard = detect_brands(procurement, {**extraction, "document_results": [{"document_name": "ТЗ.docx", "text": "Соответствие ISO 9001 и USP Class 6"}]})
        self.assertEqual(standard["status"], "brand_not_found")

    def test_brand_lists_are_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = build_brand_lists([{"status": "brand_found", "purchase_id": "p1"}, {"status": "brand_not_found", "purchase_id": "p2"}], output_dir=Path(tmp))
            self.assertEqual(len(result["brands_found"]), 1)
            self.assertTrue((Path(tmp) / "brands_not_found.json").exists())


if __name__ == "__main__":
    unittest.main()
