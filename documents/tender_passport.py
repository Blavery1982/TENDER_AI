"""Формирование паспорта состава файлов каждого тендера."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from documents.tender_archive import TENDERS_ROOT, sha256_file, write_json


PASSPORT_MD = "passport.md"
META_FILES = {"tender.json", PASSPORT_MD, "document_extraction.json", "brand_audit.json"}


def _manifest(folder: Path) -> dict[str, Any]:
    path = folder / "tender.json"
    if not path.is_file():
        return {"purchase_id": folder.name, "documents": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"purchase_id": folder.name, "documents": []}


def _source_files(folder: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    listed = manifest.get("documents") or []
    listed_names = {Path(str(item.get("file_name") or item.get("local_path") or "document")).name for item in listed}
    extras = [{"file_name": path.name} for path in sorted(folder.iterdir())
              if path.is_file() and path.name not in META_FILES and not path.name.startswith(".")
              and path.name not in listed_names]
    listed = list(listed) + extras
    files: list[dict[str, Any]] = []
    for item in listed:
        name = Path(str(item.get("file_name") or item.get("local_path") or "document")).name
        path = folder / name
        row = {"file_name": name, "relative_path": name,
               "download_url": item.get("download_url"),
               "file_id": item.get("file_id"), "document_type": item.get("document_type"),
               "download_status": item.get("download_status") or "unknown",
               "exists": path.is_file()}
        if path.is_file():
            row.update({"size_bytes": path.stat().st_size, "sha256": sha256_file(path),
                        "format": path.suffix.casefold().lstrip(".") or None})
        else:
            row.update({"size_bytes": None, "sha256": None,
                        "format": path.suffix.casefold().lstrip(".") or None})
        files.append(row)
    return files


def _md_value(value: Any) -> str:
    if value in (None, ""):
        return "не указано"
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def _markdown(passport: dict[str, Any]) -> str:
    lines = ["---", f"passport_version: {passport['passport_version']}",
             f"purchase_id: {passport['purchase_id']}",
             f"tender_number: {_md_value(passport.get('tender_number'))}",
             f"status: {passport['status']}",
             f"documents_count: {passport['documents_count']}",
             f"generated_at: {passport['generated_at']}", "---", "",
             f"# Паспорт тендера {passport['purchase_id']}", "",
             "## 1. Идентификация тендера", "",
             "| Поле | Значение |", "|---|---|",
             f"| ID закупки | `{_md_value(passport['purchase_id'])}` |",
             f"| Номер тендера | `{_md_value(passport.get('tender_number'))}` |",
             f"| Название | {_md_value(passport.get('title'))} |",
             f"| Статус состава | `{passport['status']}` |",
             f"| Количество исходных файлов | `{passport['documents_count']}` |",
             f"| Файлы отсутствуют | `{len(passport['missing_files'])}` |",
             f"| Страница тендера | [{_md_value(passport.get('card_url'))}]({_md_value(passport.get('card_url'))}) |",
             f"| Паспорт сформирован | `{passport['generated_at']}` |", ""]
    lines.extend(["## 2. Состав скачанных файлов", "",
                  "Каждая строка соответствует одному исходному файлу. Поле `relative_path` можно использовать для открытия файла из этой папки.", "",
                  "| № | Файл | Путь | Формат | Размер, байт | SHA-256 | Статус | Источник |", 
                  "|---:|---|---|---|---:|---|---|---|"])
    for index, item in enumerate(passport["files"], 1):
        link = f"[{item['file_name']}](./{item['file_name']})"
        state = "OK" if item["exists"] else "ОТСУТСТВУЕТ"
        source = item.get("download_url") or "не указано"
        lines.append(f"| {index} | {link} | `{_md_value(item['relative_path'])}` | `{_md_value(item.get('format'))}` | `{item.get('size_bytes') or 0}` | `{item.get('sha256') or 'нет'}` | `{state}` | [{source}]({source}) |")
    lines.extend(["", "## 3. Производные материалы", "",
                  "| Материал | Назначение |", "|---|---|"])
    if (Path(passport["folder"]) / "tender.json").is_file():
        lines.append("| [tender.json](./tender.json) | Исходный manifest карточки и источников |")
    for name, label in (("document_extraction.json", "Извлечённый текст и OCR по страницам"),
                        ("brand_audit.json", "Проверка марок/товарных знаков")):
        if (Path(passport["folder"]) / name).is_file():
            lines.append(f"| [{name}](./{name}) | {label} |")
    lines.extend(["", "## 4. Проблемы состава", ""])
    if passport.get("missing_files"):
        lines.extend([f"- Отсутствует файл: `{name}`" for name in passport["missing_files"]])
    else:
        lines.append("- Нет: все файлы из manifest присутствуют в папке")
    raw = passport.get("raw")
    if raw:
        lines.extend(["", "## 5. Исходные данные карточки", "", "```json",
                      json.dumps(raw, ensure_ascii=False, indent=2), "```"])
    return "\n".join(lines) + "\n"


def build_passport(folder: Path) -> dict[str, Any]:
    manifest = _manifest(folder)
    files = _source_files(folder, manifest)
    missing = [item["file_name"] for item in files if not item["exists"]]
    status = "no_documents" if not files else "partial" if missing else "complete"
    passport = {
        "passport_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purchase_id": str(manifest.get("purchase_id") or folder.name),
        "tender_number": manifest.get("tender_number") or (manifest.get("raw") or {}).get("tradeNumber"),
        "title": manifest.get("title") or (manifest.get("raw") or {}).get("subject") or (manifest.get("raw") or {}).get("title"),
        "card_url": manifest.get("card_url"),
        "raw": manifest.get("raw"),
        "folder": str(folder),
        "status": status,
        "documents_count": len(files),
        "missing_files": missing,
        "files": files,
    }
    (folder / PASSPORT_MD).write_text(_markdown(passport), encoding="utf-8")
    return passport


def build_all_passports(*, root: Path = TENDERS_ROOT) -> dict[str, Any]:
    passports = []
    folders = sorted(path for path in root.iterdir() if path.is_dir() and not path.name.startswith("_")) if root.is_dir() else []
    for folder in folders:
        passports.append(build_passport(folder))
    result = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "tenders": len(passports),
              "complete": sum(x["status"] == "complete" for x in passports),
              "partial": sum(x["status"] == "partial" for x in passports),
              "no_documents": sum(x["status"] == "no_documents" for x in passports),
              "archive_root": str(root)}
    write_json(root / "_passport_summary.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=TENDERS_ROOT)
    args = parser.parse_args()
    print(json.dumps(build_all_passports(root=args.root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
