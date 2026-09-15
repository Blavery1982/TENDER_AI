"""Построение локального Markdown-отчёта из сохранённого price-search JSON."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from calculator.business_decision import decision_markdown


def _value(value: Any, empty: str = "не указано") -> str:
    return empty if value is None or value == "" else str(value)


def render_price_search_markdown(result: dict[str, Any]) -> str:
    """Сформировать отчёт только по данным результата, без сетевых обращений."""
    candidates = result.get("price_candidates")
    if candidates is None:
        candidates = [x for x in result.get("offers", []) if x.get("confirmed_price") is not None
                      or x.get("price") is not None]
    lines = [
        f"# Поиск публичных цен — {_value(result.get('target_model'))}",
        "",
        f"- Режим: `{_value(result.get('model_search_mode'))}`.",
        f"- Запросов: {len(result.get('queries_used') or [])}.",
        f"- Уникальных URL: {_value(result.get('unique_urls'))}; доменов: {_value(result.get('unique_domains'))}.",
        f"- HTTP: {_value(result.get('http_pages_read'))}; Playwright: {_value(result.get('playwright_pages_read'))}.",
        f"- Карточек: {_value(result.get('product_cards_confirmed'))}; подтверждённых цен: {_value(result.get('confirmed_prices'))}.",
        f"- Причина остановки: {_value(result.get('stop_reason'))}.",
        "",
        "## Подтверждённые цены",
        "",
        "| Цена | Домен | Наличие | Метод | Прямая карточка |",
        "|---:|---|---|---|---|",
    ]
    for candidate in sorted(candidates, key=lambda x: (x.get("confirmed_price", x.get("price")) is None,
                                                         x.get("confirmed_price", x.get("price")) or float("inf"))):
        price = candidate.get("confirmed_price", candidate.get("price"))
        lines.append("| {price} | {domain} | {availability} | `{method}` | {url} |".format(
            price=_value(price), domain=_value(candidate.get("domain") or candidate.get("source")),
            availability=_value(candidate.get("availability_normalized") or candidate.get("availability")),
            method=_value(candidate.get("extraction_method")),
            url=_value(candidate.get("source_url") or candidate.get("url"))))
    lines.extend(["", "## Поисковые запросы", ""])
    lines.extend(f"- `{query}`" for query in result.get("queries_used") or [])
    prefix = decision_markdown(result["business_decision"]) if result.get("business_decision") else ""
    return prefix + "\n".join(lines) + "\n"


def write_price_search_markdown(json_path: Path) -> Path:
    """Перечитать JSON и записать рядом одноимённый Markdown-файл."""
    path = Path(json_path)
    result = json.loads(path.read_text(encoding="utf-8"))
    report_path = path.with_suffix(".md")
    report_path.write_text(render_price_search_markdown(result), encoding="utf-8")
    return report_path
