import json
import tempfile
import unittest
from pathlib import Path

from documents.tender_passport import build_all_passports, build_passport


class TenderPassportTests(unittest.TestCase):
    def test_passport_contains_every_source_file_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "p1"
            folder.mkdir()
            (folder / "tender.json").write_text(json.dumps({"purchase_id": "p1", "tender_number": "123", "documents": [{"file_name": "ТЗ.pdf", "download_status": "downloaded"}]}), encoding="utf-8")
            (folder / "ТЗ.pdf").write_bytes(b"pdf")
            passport = build_passport(folder)
            self.assertEqual(passport["status"], "complete")
            self.assertEqual(passport["documents_count"], 1)
            self.assertEqual(len(passport["files"][0]["sha256"]), 64)
            self.assertTrue((folder / "passport.md").exists())
            content = (folder / "passport.md").read_text(encoding="utf-8")
            self.assertIn("## 2. Состав скачанных файлов", content)
            self.assertIn("ТЗ.pdf", content)

    def test_all_passports_summary_counts_no_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "p1"
            folder.mkdir()
            (folder / "tender.json").write_text(json.dumps({"purchase_id": "p1", "documents": []}), encoding="utf-8")
            result = build_all_passports(root=Path(tmp))
            self.assertEqual(result["tenders"], 1)
            self.assertEqual(result["no_documents"], 1)


if __name__ == "__main__":
    unittest.main()
