"""Повторная обработка локального архива тендеров: текст/OCR и бренды."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from documents.brand_detector import build_brand_lists, detect_brands, save_brand_audit
from documents.pipeline import process_procurement_documents
from documents.tender_archive import TENDERS_ROOT, tender_folder, write_json


def _procurement_from_manifest(manifest: dict[str, Any], folder: Path) -> dict[str, Any]:
    raw = manifest.get("raw") or {
        "id": manifest.get("purchase_id"),
        "tradeNumber": manifest.get("tender_number"),
        "subject": manifest.get("title"),
        "lotItems": [],
    }
    documents = []
    for item in manifest.get("documents") or []:
        document = dict(item)
        document["local_path"] = str(folder / Path(str(document.get("local_path") or document.get("file_name"))).name)
        documents.append(document)
    return {"purchase_id": manifest.get("purchase_id"), "raw": raw, "documents": documents}


def audit_archive(*, root: Path = TENDERS_ROOT, output_dir: Path | None = None) -> dict[str, Any]:
    output_dir = output_dir or root / "_brand_lists"
    audits = []
    for folder in sorted(path for path in root.iterdir() if path.is_dir() and not path.name.startswith("_")) if root.is_dir() else []:
        manifest_path = folder / "tender.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        procurement = _procurement_from_manifest(manifest, folder)
        extraction = process_procurement_documents(procurement, procurement["documents"], cache_dir=folder / ".extraction_cache")
        write_json(folder / "document_extraction.json", extraction)
        audit = {"purchase_id": procurement["purchase_id"], "tender_number": manifest.get("tender_number"),
                 "title": manifest.get("title"), "checked_at": datetime.now(timezone.utc).isoformat(),
                 **detect_brands(procurement, extraction)}
        save_brand_audit(procurement, audit, root=root)
        audits.append(audit)
    lists = build_brand_lists(audits, output_dir=output_dir)
    summary = {"generated_at": datetime.now(timezone.utc).isoformat(), "tenders": len(audits),
               "brands_found": len(lists["brands_found"]), "brands_not_found": len(lists["brands_not_found"]),
               "review_required": len(lists["review_required"]), "output_dir": str(output_dir)}
    write_json(output_dir / "summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=TENDERS_ROOT)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    print(json.dumps(audit_archive(root=args.root, output_dir=args.output_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
