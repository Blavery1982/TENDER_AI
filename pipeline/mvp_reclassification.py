"""Offline reclassification of the last saved MVP items; performs no network I/O."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from documents.pipeline import audit_from_extraction, process_procurement_documents
from model_search.price_readiness import classify_price_search_readiness

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data" / "mvp_exact_batch_latest.json"
OUTPUT = ROOT / "data" / "mvp_exact_reclassification.json"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _card_by_trade_number(trade_numbers: set[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in (ROOT / "data").glob("eat_single_*.json"):
        card = _load(path)
        trade = str((card.get("raw") or {}).get("tradeNumber") or "")
        if trade in trade_numbers:
            result[trade] = card
    return result


def run() -> dict[str, Any]:
    saved = _load(SOURCE)
    old_items = {(str(row.get("trade_number")), int(row.get("item_number") or 0)): row
                 for row in saved.get("items") or []}
    cards = _card_by_trade_number({key[0] for key in old_items})
    rows: list[dict[str, Any]] = []
    for trade, card in sorted(cards.items()):
        raw = card.get("raw") or {}
        lot = raw.get("lot") or raw
        extraction = process_procurement_documents(card, card.get("documents") or [])
        audit = audit_from_extraction(card, extraction)
        items = lot.get("lotItems") or []
        for resolved in audit.get("items") or []:
            number = int(resolved.get("item_number") or 0)
            item = items[number - 1] if 0 < number <= len(items) else {}
            old = old_items.get((trade, number)) or {}
            previous = ("MODEL_MODE_REVIEW_REQUIRED" if (old.get("model") or {}).get("route") == "manual_review"
                        else "MODEL_DISCOVERY_REQUIRED" if (old.get("model") or {}).get("route") == "discovery"
                        else "PRICE_SEARCH_READY")
            current = classify_price_search_readiness(item, resolved)
            rows.append({
                "trade_number": trade,
                "purchase_id": raw.get("id") or card.get("purchase_id"),
                "item_number": number,
                "position_name": item.get("name") or item.get("eatTitle") or item.get("description"),
                "item_name": item.get("description") or item.get("name") or item.get("eatTitle"),
                "found_identifier": current.get("identifier"),
                "customer_required_model": current.get("customer_required_model"),
                "price_justification_model": current.get("price_justification_model"),
                "model_source": current.get("model_source"),
                "evidence": current.get("evidence") or [],
                "old_classification": previous,
                "new_classification": current["classification"],
                "reason": current["reason"],
                "compliance_before_price_search": current["compliance_before_price_search"],
            })
    counts = Counter(row["new_classification"] for row in rows)
    old_manual_to_ready = sum(row["old_classification"] == "MODEL_MODE_REVIEW_REQUIRED"
                              and row["new_classification"] == "PRICE_SEARCH_READY" for row in rows)
    result = {
        "source": str(SOURCE),
        "dataset_role": "regression_only",
        "network_used": False,
        "supplier_search_performed": False,
        "items_total": len(rows),
        "counts": dict(counts),
        "old_manual_to_price_search_ready": old_manual_to_ready,
        "items": rows,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
