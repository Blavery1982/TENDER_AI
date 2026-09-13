"""Локальное извлечение текста: PDF text layer или Tesseract OCR постранично."""
from __future__ import annotations

import csv
import io
import re
import subprocess
import tempfile
from pathlib import Path
from statistics import mean
from typing import Any

import pymupdf
from pypdf import PdfReader

TESSERACT = Path("/opt/homebrew/bin/tesseract")
LOW_CONFIDENCE = 70.0
AMBIGUOUS = {"0": "O", "O": "0", "1": "Il", "I": "1l", "l": "1I",
             "4": "A", "A": "4", "5": "S", "S": "5", "8": "B", "B": "8"}
CRITICAL_PATTERNS = {
    "comparison": re.compile(r"(?:≥|≤|(?<![\w])>|(?<![\w])<)"),
    "percentage": re.compile(r"\b\d+(?:[,.]\d+)?\s*%"),
    "technology": re.compile(r"\b(?:4K|UHD|OLED|QLED|LED|Full\s+HD|LHD)\b", re.I),
    "number_or_unit": re.compile(r"\b\d+(?:[\s.,]\d+)*(?:\s*(?:₽|руб\.?|шт\.?|кг|г|мм|см|мкм|дюйм\w*))?\b", re.I),
    "model_or_article": re.compile(r"\b(?=[A-ZА-Я0-9-]{5,}\b)(?=[A-ZА-Я0-9-]*[A-ZА-Я])(?=[A-ZА-Я0-9-]*\d)[A-ZА-Я0-9-]+\b"),
}


def text_layer_quality(text: str) -> tuple[bool, dict[str, Any]]:
    compact = re.sub(r"\s+", "", text)
    ratio = sum(ch.isalnum() for ch in compact) / max(1, len(compact))
    quality = {"non_whitespace_characters": len(compact), "alphanumeric_ratio": round(ratio, 4),
               "replacement_characters": text.count("\ufffd")}
    return len(compact) >= 80 and ratio >= .45 and not quality["replacement_characters"], quality


def ambiguity_variants(value: str, allowed: set[str] | None = None) -> list[str]:
    """Только предлагает варианты; никогда не заменяет исходное значение."""
    variants = set()
    for index, char in enumerate(value):
        for replacement in AMBIGUOUS.get(char, ""):
            variants.add(value[:index] + replacement + value[index + 1:])
    if value in {">", "<"}:
        variants.add("≥" if value == ">" else "≤")
    return sorted(variants & allowed if allowed is not None else variants)


def _tsv_words(tsv: str) -> list[dict[str, Any]]:
    words = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        token = (row.get("text") or "").strip()
        if not token:
            continue
        try: confidence = float(row.get("conf", -1))
        except ValueError: confidence = -1
        if confidence >= 0:
            words.append({"text": token, "confidence": round(confidence, 2)})
    return words


def _critical_values(text: str, words: list[dict[str, Any]], page: int) -> list[dict[str, Any]]:
    confidence_by_token = {}
    for word in words:
        confidence_by_token.setdefault(word["text"].casefold(), []).append(word["confidence"])
    found = []
    for kind, pattern in CRITICAL_PATTERNS.items():
        for match in pattern.finditer(text):
            value = match.group(0).strip()
            token_conf = confidence_by_token.get(value.casefold(), [])
            confidence = round(mean(token_conf), 2) if token_conf else None
            variants = ambiguity_variants(value)
            if value.upper() == "LHD":
                variants.append("UHD")
            symbol_ambiguous = value in {">", "<"}
            low = confidence is None or confidence < LOW_CONFIDENCE
            inherently_ambiguous = symbol_ambiguous or kind == "model_or_article" or value.upper() == "LHD"
            found.append({
                "page_number": page, "value_type": kind, "source_fragment": value,
                "recognized_value": value, "confidence": confidence,
                "confirmed": not low and not inherently_ambiguous,
                "possible_recognition_variants": variants,
                "external_confirmation_source": None,
                "identification_method": "ocr" if not low else "ocr_requires_review",
            })
    return found


def _ocr(page: pymupdf.Page, work: Path) -> tuple[str, list[dict[str, Any]]]:
    image = work / f"page_{page.number + 1}.png"; base = work / f"page_{page.number + 1}"
    page.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY, alpha=False).save(image)
    subprocess.run([str(TESSERACT), str(image), str(base), "-l", "rus+eng", "--psm", "3", "txt", "tsv"],
                   check=True, capture_output=True, text=True)
    return base.with_suffix(".txt").read_text(encoding="utf-8"), _tsv_words(base.with_suffix(".tsv").read_text(encoding="utf-8"))


def extract_pdf(path: Path) -> dict[str, Any]:
    reader, pdf = PdfReader(path), pymupdf.open(path)
    pages=[]; all_critical=[]; ocr_pages=[]
    with tempfile.TemporaryDirectory(prefix="tender_ai_ocr_") as tmp:
        for index, page in enumerate(pdf):
            layer = reader.pages[index].extract_text() or ""
            usable, quality = text_layer_quality(layer)
            if usable:
                text, words, method = layer, [], "text_layer"
            else:
                try:
                    text, words = _ocr(page, Path(tmp))
                    method = "ocr"
                    ocr_pages.append(index + 1)
                except Exception as exc:
                    text, words, method = "", [], "failed"
                    pages.append({"page_number": index+1,"read_method":method,"text":"",
                                  "text_layer_quality":quality,"average_confidence":None,
                                  "minimum_confidence":None,"low_confidence_word_count":0,
                                  "word_count":0,"error":f"{type(exc).__name__}: OCR page failed"})
                    continue
            confidences=[x["confidence"] for x in words]
            critical=_critical_values(text, words, index + 1) if method == "ocr" else []
            all_critical.extend(critical)
            pages.append({"page_number": index+1,"read_method":method,"text":text,
                          "text_layer_quality":quality,"average_confidence":round(mean(confidences),2) if confidences else None,
                          "minimum_confidence":min(confidences) if confidences else None,
                          "low_confidence_word_count":sum(x < LOW_CONFIDENCE for x in confidences),
                          "word_count":len(words) if words else None})
    methods={x["read_method"] for x in pages if x["read_method"] != "failed"}
    read_method=methods.pop() if len(methods)==1 else "mixed" if methods else "ocr"
    warnings=[]
    failed_pages=[x["page_number"] for x in pages if x["read_method"]=="failed"]
    if failed_pages: warnings.append("Не удалось распознать страницы: " + ", ".join(map(str,failed_pages)))
    doubtful=[x for x in all_critical if not x["confirmed"]]
    if doubtful: warnings.append("Требуется проверка распознавания документа")
    return {"text":"\n".join(x["text"] for x in pages),"read_method":read_method,
            "ocr_engine":"Tesseract 5 rus+eng" if ocr_pages else None,"ocr_pages":ocr_pages,
            "pages":pages,"raw_ocr_text":"\n".join(x["text"] for x in pages if x["read_method"]=="ocr"),
            "critical_values":all_critical,"doubtful_critical_values":doubtful,"warnings":warnings}


def confirm_model_identity(extraction: dict[str, Any], ocr_value: str, official_model: str, source: str) -> dict[str, Any] | None:
    """Идентификация модели допустима лишь при однозначной подмене одного похожего знака."""
    if official_model not in ambiguity_variants(ocr_value, {official_model}): return None
    matches=[x for x in extraction["critical_values"] if x["recognized_value"].casefold()==ocr_value.casefold()]
    confidence=matches[0]["confidence"] if matches else None
    return {"source_ocr_value":ocr_value,"identified_model":official_model,"source":source,
            "ocr_confidence":confidence,"identification_method":"Модель идентифицирована по внешнему источнику"}


def identify_model_from_catalog(extraction: dict[str, Any], official_model: str, source: str) -> dict[str, Any] | None:
    """Ищет точное OCR-значение или однозначный односимвольный вариант модели."""
    values = [x for x in extraction.get("critical_values", []) if x.get("value_type") == "model_or_article"]
    exact = next((x for x in values if x["recognized_value"].casefold() == official_model.casefold()), None)
    if exact:
        return {"source_ocr_value": exact["recognized_value"], "identified_model": official_model,
                "source": source, "ocr_confidence": exact.get("confidence"),
                "identification_method": "Модель точно распознана OCR и подтверждена внешним источником"}
    for value in values:
        result = confirm_model_identity(extraction, value["recognized_value"], official_model, source)
        if result:
            return result
    return None
