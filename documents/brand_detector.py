"""Проверка тендера на упоминания марок и товарных знаков."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from documents.tender_archive import TENDERS_ROOT, tender_folder, write_json


BRAND_LABEL_RE = re.compile(
    r"(?im)\b(?:бренд|марка|товарн(?:ый|ого)\s+знак|производител\w*)\s*[:—-]\s*([^\n;|]{2,100})"
)
MODEL_LABEL_RE = re.compile(r"(?im)\b(?:модель|артикул|sku)\s*[:—-]\s*([^\n;|]{2,100})")
BRAND_MODEL_RE = re.compile(r"\b([A-Z][A-Za-z]{2,}(?:\s+[A-Z][A-Za-z]{2,})?)\s+([A-Z0-9][A-Z0-9._/-]*(?:\d|-)[A-Z0-9._/-]*)\b")
NEGATED_RE = re.compile(r"\b(?:не\s+указан(?:а|о)?|не\s+требу\w*|не\s+имеет)\b", re.I)
GENERIC_MARK_RE = re.compile(r"^марка\s+(?:стали|бетона|цемента|топлива|кабеля)\b", re.I)
NON_BRAND_MARK_RE = re.compile(r"^марка\s+(?:автомобил|машин|транспорт|бетон|стал|кабел|цемент|топлив)", re.I)
NON_BRAND_MODEL_PREFIXES = {"iso", "iec", "din", "astm", "en", "tuv", "usp", "гост"}


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" .,:;()[]")


def _snippet(text: str, start: int, end: int, radius: int = 130) -> str:
    return _compact(text[max(0, start - radius):min(len(text), end + radius)])


def _text_with_pages(document: dict[str, Any]) -> Iterable[tuple[str, int | None, str]]:
    name = str(document.get("document_name") or document.get("file_name") or "document")
    pages = document.get("pages") or []
    if pages:
        for page in pages:
            text = str(page.get("text") or "")
            if text:
                yield text, page.get("page_number"), name
        return
    text = str(document.get("text") or "")
    if text:
        yield text, None, name


def _add_hit(hits: list[dict[str, Any]], *, brand: str, matched_text: str,
             source: str, page: int | None, snippet: str, method: str,
             confidence: str) -> None:
    brand = _compact(brand)
    if (not brand or len(brand) < 2 or GENERIC_MARK_RE.search(matched_text)
            or NON_BRAND_MARK_RE.search(matched_text)
            or (method == "brand_model_match" and brand.casefold() in NON_BRAND_MODEL_PREFIXES)):
        return
    key = (brand.casefold(), source, page, snippet.casefold())
    if any((x["brand"].casefold(), x["source_document"], x.get("page"), x["snippet"].casefold()) == key for x in hits):
        return
    hits.append({"brand": brand, "matched_text": _compact(matched_text),
                 "source_document": source, "page": page, "snippet": snippet,
                 "detection_method": method, "confidence": confidence})


def detect_brands(procurement: dict[str, Any], extraction: dict[str, Any] | None = None) -> dict[str, Any]:
    """Возвращает доказательства, статус и список найденных брендов.

    Отсутствие совпадения считается доказанным только при полной доступности
    документов и успешном чтении всех доступных файлов.
    """
    raw = procurement.get("raw") or procurement
    hits: list[dict[str, Any]] = []
    title_parts = [raw.get("subject"), raw.get("title"), raw.get("description")]
    for item in (raw.get("lotItems") or (raw.get("lot") or {}).get("lotItems") or []):
        title_parts.extend([item.get("name"), item.get("description"), item.get("brand"), item.get("model")])
    title_text = "\n".join(str(x) for x in title_parts if x)
    for item in (raw.get("lotItems") or (raw.get("lot") or {}).get("lotItems") or []):
        for field in ("brand", "manufacturer", "model"):
            value = _compact(str(item.get(field) or ""))
            if value:
                _add_hit(hits, brand=value, matched_text=f"{field}: {value}", source="tender_title_or_item",
                         page=None, snippet=title_text, method="structured_fields",
                         confidence="high" if field in {"brand", "manufacturer"} else "medium")
    for pattern in (BRAND_LABEL_RE, MODEL_LABEL_RE):
        for match in pattern.finditer(title_text):
            value = _compact(match.group(1))
            _add_hit(hits, brand=value, matched_text=match.group(0), source="tender_title_or_item",
                     page=None, snippet=_snippet(title_text, match.start(), match.end()),
                     method="structured_fields", confidence="high" if pattern is BRAND_LABEL_RE else "medium")
    for match in BRAND_MODEL_RE.finditer(title_text):
        _add_hit(hits, brand=match.group(1), matched_text=match.group(0), source="tender_title_or_item",
                 page=None, snippet=_snippet(title_text, match.start(), match.end()),
                 method="brand_model_match", confidence="high")
    documents = (extraction or {}).get("document_results") or procurement.get("documents") or []
    for document in documents:
        file_name = str(document.get("document_name") or document.get("file_name") or "document")
        for pattern in (BRAND_LABEL_RE, MODEL_LABEL_RE):
            match = pattern.search(file_name)
            if match:
                _add_hit(hits, brand=match.group(1), matched_text=match.group(0), source=file_name,
                         page=None, snippet=file_name, method="filename", confidence="medium")
        for text, page, source in _text_with_pages(document):
            for pattern, method, confidence in ((BRAND_LABEL_RE, "label_match", "high"),
                                                (MODEL_LABEL_RE, "model_label_candidate", "medium")):
                for match in pattern.finditer(text):
                    context = _snippet(text, match.start(), match.end())
                    if NEGATED_RE.search(context):
                        continue
                    _add_hit(hits, brand=match.group(1), matched_text=match.group(0), source=source,
                             page=page, snippet=context, method=method, confidence=confidence)
            for match in BRAND_MODEL_RE.finditer(text):
                _add_hit(hits, brand=match.group(1), matched_text=match.group(0), source=source,
                         page=page, snippet=_snippet(text, match.start(), match.end()),
                         method="brand_model_match", confidence="high")
    failed = int((extraction or {}).get("documents_failed", 0))
    partial = int(((extraction or {}).get("extraction_summary") or {}).get("partial_documents", 0))
    missing = any(x.get("status") in {"missing", "failed"} or x.get("download_status", "").startswith(("http_", "no_")) for x in documents)
    confirmed_hits = [x for x in hits if x.get("confidence") == "high"]
    candidates = [x for x in hits if x.get("confidence") != "high"]
    if not documents:
        status = "review_required"
    elif failed or partial or missing:
        status = "review_required"
    elif confirmed_hits:
        status = "brand_found"
    elif candidates:
        status = "review_required"
    elif documents or extraction is not None:
        status = "brand_not_found"
    else:
        status = "review_required"
    if status == "review_required":
        review_reason = ("Не все документы доступны или распознаны" if (not documents or failed or partial or missing)
                         else "Найден неоднозначный кандидат марки или модели")
    else:
        review_reason = None
    return {"status": status, "brands": sorted({x["brand"] for x in confirmed_hits}, key=str.casefold),
            "brand_candidates": sorted({x["brand"] for x in candidates}, key=str.casefold),
            "hits": hits, "documents_checked": len(documents),
            "coverage": {"documents_failed": failed, "partial_documents": partial, "title_checked": True},
            "review_reason": review_reason}


def save_brand_audit(procurement: dict[str, Any], audit: dict[str, Any], *, root: Path = TENDERS_ROOT) -> Path:
    raw = procurement.get("raw") or {}
    path = tender_folder(procurement.get("purchase_id") or raw.get("id"),
                         tender_number=procurement.get("tender_number") or raw.get("tradeNumber"),
                         root=root) / "brand_audit.json"
    write_json(path, audit)
    return path


def build_brand_lists(audits: Iterable[dict[str, Any]], *, output_dir: Path) -> dict[str, list[dict[str, Any]]]:
    lists = {"brands_found": [], "brands_not_found": [], "review_required": []}
    status_to_list = {"brand_found": "brands_found", "brand_not_found": "brands_not_found",
                      "review_required": "review_required"}
    for audit in audits:
        status = status_to_list.get(audit.get("status"), "review_required")
        lists[status].append(audit)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in lists.items():
        write_json(output_dir / f"{name}.json", {"tenders": rows})
    return lists
