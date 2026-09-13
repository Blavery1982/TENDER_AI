"""Изолированный OCR-тест двух PDF закупки 100250237126100180.

Скрипт не изменяет PDF и не связан с основным procurement_audit.
"""
from __future__ import annotations

import csv
import io
import json
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

import pymupdf
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "data/contracts/31f18b8a-d3b9-4cd8-890c-5d11824fc635"
OUTPUT = ROOT / "data/ocr_tv_test.json"
TESSERACT = Path("/opt/homebrew/bin/tesseract")
LOW_CONFIDENCE = 70.0

EXPECTED = {
    "рап.pdf": {
        "OLED": r"\bOLED\b",
        "4K UHD": r"\b4K\s+UHD\b",
        "количество 2 шт": r"\b2\s*шт\b",
    },
    "кп.pdf": {
        "GE32LFN0": r"\bGE32LFN0\b",
        "DEXP 43UCY3": r"\bDEXP\s+43UCY3\b",
        "LED": r"\bLED\b",
        "Full HD": r"\bFull\s+HD\b",
        "13 750": r"\b13\s*750(?:[,.]00)?\b",
        "26 600": r"\b26\s*600(?:[,.]00)?\b",
        "54 100": r"\b54\s*100(?:[,.]00)?\b",
        "количество 2": r"(?:\b2\b|\b2\s*шт\b)",
        "количество 1": r"(?:\b1\b|\b1\s*шт\b)",
    },
}


def text_layer_is_usable(text: str) -> tuple[bool, dict[str, float | int]]:
    """Консервативная оценка: пустой или повреждённый слой отправляется в OCR."""
    compact = re.sub(r"\s+", "", text)
    printable = sum(ch.isprintable() for ch in compact)
    letters_digits = sum(ch.isalnum() for ch in compact)
    replacement = text.count("\ufffd")
    ratio = letters_digits / max(1, len(compact))
    usable = len(compact) >= 80 and ratio >= 0.45 and replacement == 0
    return usable, {
        "non_whitespace_characters": len(compact),
        "printable_characters": printable,
        "alphanumeric_ratio": round(ratio, 4),
        "replacement_characters": replacement,
    }


def _words_from_tsv(tsv: str) -> list[dict]:
    words = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        word = (row.get("text") or "").strip()
        if not word:
            continue
        try:
            confidence = float(row.get("conf", -1))
        except ValueError:
            confidence = -1
        if confidence < 0:
            continue
        words.append({"text": word, "confidence": round(confidence, 2)})
    return words


def _uncertain_fragments(words: list[dict], radius: int = 2) -> list[dict]:
    fragments = []
    for index, word in enumerate(words):
        if word["confidence"] >= LOW_CONFIDENCE:
            continue
        start, end = max(0, index - radius), min(len(words), index + radius + 1)
        fragment = " ".join(item["text"] for item in words[start:end])
        entry = {
            "word": word["text"],
            "confidence": word["confidence"],
            "text_fragment": fragment,
        }
        if not fragments or fragments[-1]["text_fragment"] != fragment:
            fragments.append(entry)
    return fragments


def _ocr_page(page: pymupdf.Page, work: Path) -> tuple[str, list[dict]]:
    image = work / f"page_{page.number + 1}.png"
    output_base = work / f"page_{page.number + 1}_ocr"
    pixmap = page.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY, alpha=False)
    pixmap.save(image)
    subprocess.run(
        [str(TESSERACT), str(image), str(output_base), "-l", "rus+eng", "--psm", "3", "txt", "tsv"],
        check=True, capture_output=True, text=True,
    )
    text = output_base.with_suffix(".txt").read_text(encoding="utf-8")
    tsv = output_base.with_suffix(".tsv").read_text(encoding="utf-8")
    return text, _words_from_tsv(tsv)


def analyze_pdf(path: Path) -> dict:
    reader = PdfReader(path)
    document = pymupdf.open(path)
    pages = []
    with tempfile.TemporaryDirectory(prefix="tender_ai_ocr_") as directory:
        work = Path(directory)
        for index, page in enumerate(document):
            layer_text = reader.pages[index].extract_text() or ""
            usable, quality = text_layer_is_usable(layer_text)
            if usable:
                text, words, method = layer_text, [], "text_layer"
            else:
                text, words = _ocr_page(page, work)
                method = "ocr"
            confidences = [word["confidence"] for word in words]
            pages.append({
                "document_name": path.name,
                "page_number": index + 1,
                "read_method": method,
                "text_layer_quality": quality,
                "recognized_text": text,
                "average_confidence": round(mean(confidences), 2) if confidences else None,
                "minimum_confidence": min(confidences) if confidences else None,
                "recognized_word_count": len(words) if words else None,
                "low_confidence_word_count": sum(value < LOW_CONFIDENCE for value in confidences),
                "uncertain_fragments": _uncertain_fragments(words),
            })
    full_text = "\n".join(page["recognized_text"] for page in pages)
    checks = {
        name: {
            "recognized": bool(re.search(pattern, full_text, re.I)),
            "pattern": pattern,
        }
        for name, pattern in EXPECTED.get(path.name, {}).items()
    }
    return {"document_name": path.name, "page_count": len(pages), "pages": pages, "key_value_checks": checks}


def run_test() -> dict:
    if not TESSERACT.is_file():
        raise FileNotFoundError(f"Tesseract не найден: {TESSERACT}")
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    result = {
        "test": "local_pdf_ocr",
        "procurement_number": "100250237126100180",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "engine": "Tesseract rus+eng",
        "rendering": "PyMuPDF 300 DPI grayscale",
        "low_confidence_threshold": LOW_CONFIDENCE,
        "original_pdf_modified": False,
        "documents": [analyze_pdf(path) for path in pdfs],
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(run_test(), ensure_ascii=False, indent=2))
