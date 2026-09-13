"""Скачать документы всех актуальных строк из листа ACTIVE Google Sheets."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from eat.browser_policy import open_authorized_eat_browser
from eat.single_purchase import fetch_purchase_card
from google_sheets.client import _env, authorize_service_account
from google_sheets.workbook import ACTIVE
from documents.tender_archive import TENDERS_ROOT, write_json


ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = TENDERS_ROOT / "_batch_download.json"


def active_purchase_ids() -> list[str]:
    client, _ = authorize_service_account()
    book = client.open_by_key(_env("GOOGLE_SPREADSHEET_ID"))
    rows = book.worksheet(ACTIVE).get_all_records()
    result = []
    seen = set()
    for row in rows:
        value = str(row.get("ID закупки") or "").strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def download_active_tenders(*, wait_ms: int = 1500) -> dict[str, Any]:
    purchase_ids = active_purchase_ids()
    started = datetime.now(timezone.utc).isoformat()
    results: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        session = open_authorized_eat_browser(playwright)
        try:
            for index, purchase_id in enumerate(purchase_ids, 1):
                try:
                    card = fetch_purchase_card(session.context, session.page, purchase_id,
                                               wait_ms=wait_ms, analyze_documents=False)
                    results.append({"purchase_id": purchase_id, "status": "downloaded",
                                    "documents": len(card.get("documents") or [])})
                    print(f"[{index}/{len(purchase_ids)}] {purchase_id}: документов {len(card.get('documents') or [])}", flush=True)
                except Exception as exc:
                    result = {"purchase_id": purchase_id, "status": "failed",
                              "error": f"{type(exc).__name__}: {exc}"}
                    results.append(result)
                    print(f"[{index}/{len(purchase_ids)}] {purchase_id}: ошибка {result['error']}", flush=True)
        finally:
            session.close()
    report = {"started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
              "source": "Google Sheets ACTIVE", "requested": len(purchase_ids),
              "downloaded": sum(x["status"] == "downloaded" for x in results),
              "failed": sum(x["status"] == "failed" for x in results), "results": results,
              "archive_root": str(TENDERS_ROOT)}
    write_json(REPORT_PATH, report)
    return report


if __name__ == "__main__":
    print(json.dumps(download_active_tenders(), ensure_ascii=False, indent=2))
