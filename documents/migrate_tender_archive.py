"""Одноразовая миграция старого data/contracts в data/tenders."""
from __future__ import annotations

import argparse
from pathlib import Path

from documents.tender_archive import (LEGACY_CONTRACTS_ROOT, TENDERS_ROOT,
                                       archive_metadata, migrate_legacy_tender,
                                       sha256_file, tender_folder)


def migrate_all(*, legacy_root: Path = LEGACY_CONTRACTS_ROOT,
                root: Path = TENDERS_ROOT) -> dict[str, int]:
    copied = tenders = 0
    if not legacy_root.is_dir():
        return {"tenders": 0, "documents_copied": 0}
    for source in sorted(legacy_root.iterdir()):
        if not source.is_dir():
            continue
        purchase_id = source.name
        copied_paths = migrate_legacy_tender(purchase_id, root=root, legacy_root=legacy_root)
        target = tender_folder(purchase_id, root=root)
        documents = []
        for path in sorted(target.iterdir()):
            if path.name in {"tender.json", "brand_audit.json", "document_extraction.json"} or not path.is_file():
                continue
            documents.append({"file_name": path.name, "local_path": str(path),
                              "sha256": sha256_file(path), "size_bytes": path.stat().st_size,
                              "download_status": "migrated"})
        archive_metadata(purchase_id, documents=documents, root=root)
        copied += len(copied_paths)
        tenders += 1
    return {"tenders": tenders, "documents_copied": copied}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-root", type=Path, default=LEGACY_CONTRACTS_ROOT)
    parser.add_argument("--root", type=Path, default=TENDERS_ROOT)
    args = parser.parse_args()
    result = migrate_all(legacy_root=args.legacy_root, root=args.root)
    print(f"Тендеров: {result['tenders']}; документов скопировано: {result['documents_copied']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
