import tempfile
import unittest
import json
from pathlib import Path

from documents.brand_detector import build_brand_lists, detect_brands, save_brand_audit
from documents.tender_archive import archive_metadata, migrate_legacy_tender, save_document, tender_folder


class TenderArchiveTests(unittest.TestCase):
    def test_each_tender_has_own_folder_and_duplicate_names_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, digest, created = save_document(b"one", "ТЗ.pdf", "tender-1", tender_number="123", root=root)
            second, _, created_second = save_document(b"two", "ТЗ.pdf", "tender-1", tender_number="123", root=root)
            other, _, _ = save_document(b"one", "ТЗ.pdf", "tender-2", tender_number="456", root=root)
            self.assertTrue(created)
            self.assertTrue(created_second)
            self.assertEqual(first.parent, second.parent)
            self.assertNotEqual(first, second)
            self.assertNotEqual(first.parent, other.parent)
            self.assertEqual(len(digest), 64)
            self.assertEqual(first.parent.name, "123")
            self.assertEqual(other.parent.name, "456")

    def test_new_archive_uses_number_and_retains_uuid_in_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata = archive_metadata("uuid-1", raw={"tradeNumber": "200908050126100191"}, root=root)
            folder = root / "200908050126100191"
            self.assertEqual(metadata["purchase_id"], "uuid-1")
            self.assertEqual(tender_folder("uuid-1", root=root), folder)
            path, _, _ = save_document(b"original", "ТЗ.pdf", "uuid-1", root=root)
            audit_path = save_brand_audit({"purchase_id": "uuid-1"}, {"status": "review_required"}, root=root)
            self.assertEqual(path.parent, folder)
            self.assertEqual(audit_path.parent, folder)
            self.assertFalse((root / "uuid-1").exists())

    def test_existing_uuid_archive_has_priority_over_numbered_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "uuid-1"
            old.mkdir()
            original = old / "original.pdf"
            original.write_bytes(b"old")
            numbered = root / "123"
            numbered.mkdir()
            (numbered / "tender.json").write_text(json.dumps({"purchase_id": "uuid-1"}))
            self.assertEqual(tender_folder("uuid-1", tender_number="123", root=root), old)
            self.assertEqual(tender_folder("uuid-1", root=root), old)
            archive_metadata("uuid-1", tender_number="123", root=root)
            audit_path = save_brand_audit({"purchase_id": "uuid-1", "raw": {"tradeNumber": "123"}}, {}, root=root)
            self.assertEqual(audit_path.parent, old)
            self.assertEqual(original.read_bytes(), b"old")
            self.assertTrue((old / "tender.json").exists())
            self.assertEqual(json.loads((numbered / "tender.json").read_text()), {"purchase_id": "uuid-1"})

    def test_new_archive_without_valid_number_fails_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "tenders"
            for number in (None, "", "../123", "uuid-1", "12/34"):
                with self.subTest(number=number), self.assertRaises(ValueError):
                    save_document(b"original", "ТЗ.pdf", "uuid-1", tender_number=number, root=root)
                with self.assertRaises(ValueError):
                    archive_metadata("uuid-1", tender_number=number, root=root)
            self.assertFalse(root.exists())

    def test_number_collision_with_other_uuid_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_metadata("uuid-1", tender_number="123", root=root)
            before = (root / "123" / "tender.json").read_bytes()
            with self.assertRaises(ValueError):
                archive_metadata("uuid-2", tender_number="123", root=root)
            self.assertEqual((root / "123" / "tender.json").read_bytes(), before)

    def test_existing_numeric_legacy_archive_is_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "123"
            old.mkdir()
            (old / "original.pdf").write_bytes(b"old")
            archive_metadata("123", root=root)
            self.assertEqual(tender_folder("uuid-1", tender_number="123", root=root), old)
            self.assertEqual(tender_folder("123", root=root), old)

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
