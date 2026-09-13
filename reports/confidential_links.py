"""Очередь ссылок на закрытые закупки для ручной проверки."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_PATH = PROJECT_ROOT / "data" / "eat_filtered_test.json"
JSON_PATH = PROJECT_ROOT / "data" / "eat_confidential_links.json"
CSV_PATH = PROJECT_ROOT / "data" / "eat_confidential_links.csv"
MOSCOW = ZoneInfo("Europe/Moscow")


def _parse_eat_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        # Перехваченные наивные timestamps совпадают с UTC, не с МСК.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(MOSCOW)


def _deadline_values(deadline: datetime | None, now: datetime) -> tuple[int | None, float | None, str]:
    if deadline is None:
        return None, None, "unknown"
    minutes = int((deadline - now).total_seconds() // 60)
    hours = round(minutes / 60, 2)
    if minutes < 0:
        status = "expired"
    elif minutes < 120:
        status = "urgent"
    elif minutes <= 720:
        status = "today"
    else:
        status = "normal"
    return minutes, hours, status


def _time_left(minutes: int | None) -> str:
    if minutes is None:
        return "не определено"
    prefix = "просрочено на " if minutes < 0 else "осталось "
    absolute = abs(minutes)
    days, remainder = divmod(absolute, 1440)
    hours, mins = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    parts.append(f"{mins} мин")
    return prefix + " ".join(parts)


def build_confidential_links(now: datetime | None = None) -> dict[str, Any]:
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    now_moscow = (now or datetime.now(MOSCOW)).astimezone(MOSCOW)
    items: list[dict[str, Any]] = []
    for record in source["purchases"]:
        if record.get("filter_result") != "confidential_locked":
            continue
        raw = record["raw"]
        deadline = _parse_eat_datetime(raw.get("applicationFillingEndDate"))
        publish_date = _parse_eat_datetime(raw.get("publishDate"))
        contract_start = _parse_eat_datetime(
            raw.get("contractStartDate") or raw.get("contractSignDate")
        )
        contract_end = _parse_eat_datetime(
            raw.get("contractEndDate") or raw.get("contractExpirationDate")
        )
        minutes, hours, status = _deadline_values(deadline, now_moscow)
        item_id = raw.get("id")
        items.append({
            "id": item_id,
            "publishDate": publish_date.isoformat() if publish_date else None,
            "applicationFillingEndDate": deadline.isoformat() if deadline else None,
            "lotState": raw.get("lotState"),
            "type": raw.get("type"),
            "purchaseTypeId": raw.get("purchaseTypeId"),
            "purchaseMethod": raw.get("purchaseMethod"),
            "deliveryType": raw.get("deliveryType"),
            "contractStartDate": contract_start.isoformat() if contract_start else None,
            "contractEndDate": contract_end.isoformat() if contract_end else None,
            "url": f"https://agregatoreat.ru/purchases/announcement/{item_id}/info",
            "minutes_until_deadline": minutes,
            "hours_until_deadline": hours,
            "time_left": _time_left(minutes),
            "deadline_status": status,
            "filter_result": "confidential_locked",
        })

    # Сначала будущие сроки от ближайшего, затем недавно истёкшие.
    items.sort(key=lambda item: (
        item["deadline_status"] == "expired",
        item["applicationFillingEndDate"] or "9999",
    ))
    counts = {status: sum(item["deadline_status"] == status for item in items) for status in ("expired", "urgent", "today", "normal")}
    result = {
        "metadata": {
            "generated_at_moscow": now_moscow.isoformat(),
            "comparison_timezone": "Europe/Moscow",
            "eat_api_timestamp_interpretation": "UTC timestamps without an explicit offset; converted to Europe/Moscow",
            "timezone_evidence": (
                "During capture, newest publishDate values matched current UTC; "
                "Europe/Moscow clock was three hours ahead."
            ),
            "total": len(items),
            "counts": counts,
        },
        "purchases": items,
    }
    JSON_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=[
            "deadline", "time_left", "deadline_status", "purchaseTypeId", "lotState", "id", "url"
        ])
        writer.writeheader()
        for item in items:
            writer.writerow({
                "deadline": item["applicationFillingEndDate"],
                "time_left": item["time_left"],
                "deadline_status": item["deadline_status"],
                "purchaseTypeId": item["purchaseTypeId"],
                "lotState": item["lotState"],
                "id": item["id"],
                "url": item["url"],
            })
    return result


if __name__ == "__main__":
    built = build_confidential_links()
    print(json.dumps(built["metadata"], ensure_ascii=False, indent=2))
