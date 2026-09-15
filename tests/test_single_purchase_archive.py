"""Локальные проверки выбора папки карточки, без сетевых запросов."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from documents.tender_archive import archive_metadata, tender_folder
from documents.brand_detector import save_brand_audit
from eat.single_purchase import fetch_purchase_card


class SinglePurchaseArchiveTests(unittest.TestCase):
    def fetch(self, root, *, existing=False, number="200908050126100191", deferred=False):
        purchase_id = "test-uuid"
        archive_root = root / "data/tenders"
        if existing:
            (archive_root / purchase_id).mkdir(parents=True)
            (archive_root / purchase_id / "original.txt").write_bytes(b"keep")
        context, page = MagicMock(), MagicMock()
        page.url = "https://example.test/card"
        anchor = MagicMock()
        anchor.get_attribute.side_effect = lambda name: "https://example.test/specification.txt" if name == "href" else None
        anchor.inner_text.return_value = "ТЗ.txt"
        page.locator.return_value.all.return_value = [anchor]
        downloaded = context.request.get.return_value
        downloaded.ok = True
        downloaded.body.return_value = b"specification"
        response = MagicMock()
        response.headers = {"content-type": "application/json"}
        response.url = "https://example.test/card"
        response.json.return_value = {"id": purchase_id, "tradeNumber": number}
        page.goto.side_effect = lambda *a, **k: page.on.call_args.args[1](response)
        def folder(pid, **kwargs):
            return tender_folder(pid, root=archive_root, **kwargs)
        def metadata_writer(pid, **kwargs):
            return archive_metadata(pid, root=archive_root, **kwargs)
        def audit_writer(procurement, audit):
            return save_brand_audit(procurement, audit, root=archive_root)
        with patch("eat.single_purchase.ROOT", root), patch("eat.single_purchase.tender_folder", side_effect=folder), \
             patch("eat.single_purchase.archive_metadata", side_effect=metadata_writer) as metadata, \
             patch("eat.single_purchase.process_procurement_documents", return_value={"combined_text": "", "documents_found": 0}), \
             patch("eat.single_purchase.detect_brands", return_value={"status": "review_required"}), \
             patch("eat.single_purchase.save_brand_audit", side_effect=audit_writer):
            result = fetch_purchase_card(context, page, purchase_id, wait_ms=0, download_documents=not deferred)
        metadata.assert_called_once()
        self.assertEqual(result["purchase_id"], purchase_id)
        self.assertEqual(metadata.call_args.kwargs["tender_number"], number)
        if deferred:
            context.request.get.assert_not_called()
            self.assertEqual(result["documents"][0]["download_status"], "deferred")
            self.assertIsNone(result["documents"][0]["local_path"])
            return archive_root
        expected = archive_root / (purchase_id if existing else number)
        self.assertEqual(Path(result["documents"][0]["local_path"]), expected / "ТЗ.txt")
        self.assertEqual((expected / "ТЗ.txt").read_bytes(), b"specification")
        self.assertTrue((expected / "brand_audit.json").is_file())
        page.remove_listener.assert_called_once()
        return archive_root

    def test_new_card_and_extraction_use_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            archive = self.fetch(root)
            self.assertTrue((archive / "200908050126100191" / "document_extraction.json").is_file())
            self.assertFalse((archive / "test-uuid").exists())
            self.assertEqual(json.loads((root / "data/eat_single_test-uuid.json").read_text())["raw"]["tradeNumber"], "200908050126100191")

    def test_existing_card_keeps_uuid_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = self.fetch(Path(tmp), existing=True)
            self.assertEqual((archive / "test-uuid/original.txt").read_bytes(), b"keep")
            self.assertTrue((archive / "test-uuid/document_extraction.json").is_file())
            self.assertFalse((archive / "200908050126100191").exists())

    def test_repeat_new_card_reuses_numbered_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            self.fetch(root)
            archive = self.fetch(root)
            self.assertEqual([p.name for p in archive.iterdir()], ["200908050126100191"])

    def test_missing_number_does_not_create_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                self.fetch(root, number=None)
            self.assertFalse((root / "data/tenders").exists())

    def test_card_only_does_not_download_documents_even_when_analysis_default_is_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            archive = self.fetch(root, deferred=True)
            self.assertFalse((archive / "200908050126100191" / "ТЗ.txt").exists())
            self.assertFalse((archive / "200908050126100191" / "document_extraction.json").exists())
