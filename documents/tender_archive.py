"""Единый архив исходных документов и метаданных отдельного тендера."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
TENDERS_ROOT = ROOT / "data/tenders"
LEGACY_CONTRACTS_ROOT = ROOT / "data/contracts"


def safe_identifier(value: Any) -> str:
    """Безопасное имя папки, сохраняющее исходный идентификатор."""
    cleaned = re.sub(r"[^0-9A-Za-zА-Яа-яЁё._-]+", "_", str(value or "unknown"))
    return cleaned.strip("._")[:180] or "unknown"


def safe_filename(value: Any) -> str:
    cleaned = re.sub(r"[^0-9A-Za-zА-Яа-яЁё._() -]+", "_", str(value or "document"))
    return cleaned.strip(" .")[:180] or "document"


def tender_folder(purchase_id: Any, *, tender_number: Any = None,
                  root: Path = TENDERS_ROOT) -> Path:
    """Старый архив остаётся на месте; новый получает номер закупки ЕАТ.

    UUID нужен для API и хранится в manifest, но не служит именем новой папки.
    Без подтверждённого номера новый архив не создаём.
    """
    root = Path(root)
    existing = root / safe_identifier(purchase_id)
    if existing.is_dir():
        return existing
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            if not folder.is_dir() or folder.name.startswith("_"):
                continue
            try:
                metadata = json.loads((folder / "tender.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(metadata, dict) and str(metadata.get("purchase_id")) == str(purchase_id):
                return folder
    number = str(tender_number if tender_number is not None else purchase_id).strip()
    if not re.fullmatch(r"[0-9]+", number):
        raise ValueError("Для нового архива нужен номер закупки ЕАТ (tradeNumber)")
    folder = root / number
    if folder.exists():
        try:
            metadata = json.loads((folder / "tender.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            metadata = {}
        owner = metadata.get("purchase_id") if isinstance(metadata, dict) else None
        if not folder.is_dir() or owner not in (None, number, str(purchase_id)):
            raise ValueError(f"Папка закупки {number} принадлежит другому purchase_id")
    return folder


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _next_path(folder: Path, filename: str, digest: str) -> Path:
    candidate = folder / safe_filename(filename)
    if not candidate.exists() or sha256_file(candidate) == digest:
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    index = 2
    while True:
        candidate = folder / f"{stem}_{index}{suffix}"
        if not candidate.exists() or sha256_file(candidate) == digest:
            return candidate
        index += 1


def save_document(body: bytes, filename: str, purchase_id: Any, *,
                  tender_number: Any = None,
                  root: Path = TENDERS_ROOT) -> tuple[Path, str, bool]:
    """Сохраняет оригинал, не затирая другой файл с тем же именем.

    Возвращает путь, SHA-256 и признак фактической записи нового содержимого.
    """
    folder = tender_folder(purchase_id, tender_number=tender_number, root=root)
    folder.mkdir(parents=True, exist_ok=True)
    digest = sha256_bytes(body)
    path = _next_path(folder, filename, digest)
    if path.exists():
        return path, digest, False
    path.write_bytes(body)
    return path, digest, True


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def migrate_legacy_tender(purchase_id: Any, *, root: Path = TENDERS_ROOT,
                          legacy_root: Path = LEGACY_CONTRACTS_ROOT) -> list[Path]:
    """Копирует старые документы из data/contracts в единый архив.

    Копирование намеренно не удаляет старые файлы: существующие fixtures и отчёты
    должны продолжать работать, а переход на новый архив остаётся обратимым.
    """
    source = legacy_root / str(purchase_id)
    if not source.is_dir():
        return []
    # Историческая миграция сохраняет прежнее имя, а не применяет правило новых закупок.
    (root / safe_identifier(purchase_id)).mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for path in sorted(source.iterdir()):
        if not path.is_file():
            continue
        target, _, created = save_document(path.read_bytes(), path.name, purchase_id, root=root)
        if created:
            copied.append(target)
    return copied


def archive_metadata(purchase_id: Any, *, tender_number: Any = None,
                     title: str | None = None, card_url: str | None = None,
                     documents: list[dict[str, Any]] | None = None,
                     raw: dict[str, Any] | None = None,
                     root: Path = TENDERS_ROOT) -> dict[str, Any]:
    """Создаёт/обновляет manifest тендера и возвращает его содержимое."""
    tender_number = tender_number if tender_number is not None else (raw or {}).get("tradeNumber")
    folder = tender_folder(purchase_id, tender_number=tender_number, root=root)
    path = folder / "tender.json"
    current: dict[str, Any] = {}
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = {}
    metadata = {
        **current,
        "archive_version": 1,
        "purchase_id": str(purchase_id),
        "tender_number": tender_number if tender_number is not None else current.get("tender_number"),
        "title": title if title is not None else current.get("title"),
        "card_url": card_url if card_url is not None else current.get("card_url"),
        "documents": documents if documents is not None else current.get("documents", []),
    }
    if raw is not None:
        metadata["raw"] = raw
    write_json(path, metadata)
    return metadata
