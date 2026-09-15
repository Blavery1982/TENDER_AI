"""Один ручной локальный запуск КАД: один поиск, максимум одна карточка, без повторов."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from suppliers.kad_client import KADClient, ROOT, TreeParser, parse_card, parse_search_page


class DiagnosticLog:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.events = []

    def __call__(self, stage, **details):
        if stage == "failure":
            details["after_stage"] = self.events[-1]["stage"] if self.events else None
        event = {"at": datetime.now(timezone.utc).isoformat(), "stage": stage, **details}
        line = json.dumps(event, ensure_ascii=False)
        # Снимок этапа не должен измениться после дополнения карточки в исходном списке.
        self.events.append(json.loads(line))
        with (self.directory / "stages.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
        print(line, flush=True)

    def save(self, name, value):
        (self.directory / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class DiagnosticClient(KADClient):
    """Диагностический режим переиспользует штатный браузер и парсеры КАД."""
    search_inn_confirmed = None
    development_diagnostic = True

    def check(self, inn, *, force_refresh=False):
        # Подробности разрешены только в этом инструменте разработки.
        # Production-assessor и production-кэш не получают списков судебных дел.
        collection = None
        warning = None
        status = "KAD_INCOMPLETE"
        try:
            collection = self.collect(inn)
            if collection.get("complete"):
                status = "KAD_CHECKED"
        except PermissionError as exc:
            status = "KAD_REQUIRES_MANUAL_CHECK"
            collection = getattr(exc, "collection", None)
            warning = str(exc)
        except Exception as exc:
            status = "KAD_UNAVAILABLE"
            warning = f"{type(exc).__name__}: {exc}"
        cases = (collection or {}).get("cases", [])
        result = {"searched_inn": inn, "checked_at": datetime.now(timezone.utc).isoformat(),
                  "checked_in_kad": status == "KAD_CHECKED", "complete": status == "KAD_CHECKED",
                  "status": status, "warnings": [warning] if warning else (collection or {}).get("errors", []),
                  "case_urls": [case.get("url") for case in cases], "collection": collection}
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "development_result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result

    def _diagnostic(self, stage, **details):
        if stage == "search_sent":
            self.search_inn_confirmed = details.get("target_inn_matches")
        super()._diagnostic(stage, **details)

    def collect(self, inn):
        # Здесь только Playwright. HTTP, fallback и повторный поиск не запускаются.
        try:
            result = self._browser_collect(inn)
            result["transport"] = "playwright_local_diagnostic"
            return result
        except Exception as exc:
            self._diagnostic("failure", error_type=type(exc).__name__, reason=str(exc)[:2000])
            if isinstance(exc, PermissionError):
                exc.collection = self.collection
                raise
            if self.collection:
                self.collection["errors"].append(f"{type(exc).__name__}: {str(exc)[:2000]}")
                self.collection["complete"] = False
                return self.collection
            raise ConnectionError("Локальная диагностика КАД не завершена") from exc

    def _collect_pages(self, inn, search, read_card):
        result = {"searched_inn": inn, "cases": [], "complete": False,
                  "expected_count": None, "errors": []}
        self.collection = result
        parsed = parse_search_page(search(1))
        result["expected_count"] = parsed["total"]
        result["cases"] = parsed["cases"]
        self._diagnostic("results", count=parsed["total"], pages=parsed["pages"],
                         cases_on_first_page=len(parsed["cases"]), cases=parsed["cases"])
        if self.search_inn_confirmed is not True:
            result["errors"].append("ИНН в реально отправленном поисковом запросе не подтверждён")
            return result
        if parsed["cases"]:
            first = parsed["cases"][0]
            html = read_card(first["url"])
            checked = parse_card(html, first, inn)
            result["cases"][0] = checked
            # Только текст публичной карточки: без HTML, скриптов, cookies и заголовков сессии.
            text = re.sub(r"\s+", " ", TreeParser(html).root.text()).strip()
            self._diagnostic("card_fields", fields=checked,
                             extracted_fields=[key for key, value in checked.items()
                                               if value is not None and value != [] and value != ""],
                             target_inn=inn, role_matches=checked.get("target_inn") == inn
                             and bool(checked.get("role")), card_text=text[:30000],
                             field_sources={"number": "search", "date": "search",
                                            "role": "card: labelled participant group + exact INN",
                                            "status": "card", "decision": "card",
                                            "category": "card or search", "is_bankruptcy": "category"})
        if parsed["total"] is None:
            result["errors"].append("Нет подтверждённого счётчика результатов")
        elif parsed["total"] == 0 and not parsed["cases"]:
            result["complete"] = True
        elif parsed["total"] == 1 and len(result["cases"]) == 1:
            case = result["cases"][0]
            result["complete"] = bool(case.get("target_inn") == inn and case.get("role")
                                      and case.get("date") and case.get("is_bankruptcy") is not None)
            if not result["complete"]:
                result["errors"].append("Не все обязательные поля/роль нашего ИНН подтверждены")
        else:
            result["errors"].append("Диагностика ограничена одной страницей и одной карточкой; полный КАД не проверен")
        return result


def summarize(events):
    def last(stage):
        return next((event for event in reversed(events) if event["stage"] == stage), {})
    accesses = [event for event in events if event["stage"] == "access"]
    root = last("site_opened")
    results = last("results")
    card = last("card_opened")
    fields = last("card_fields")
    def opened(event):
        if not event:
            return None
        return bool(event.get("http_status") and event["http_status"] < 400
                    and event.get("url", "").startswith("https://kad.arbitr.ru/"))
    summary = {
        "site_reached": bool(root), "site_opened": opened(root), "site_http_status": root.get("http_status"),
        "challenge_detected": any(e["challenge_detected"] for e in accesses) if accesses else None,
        "access_blocked": any(e["access_blocked"] for e in accesses) if accesses else None,
        "inn_filled": last("inn_filled").get("matches"),
        "search_clicked": bool(last("search_clicked")),
        "search_sent": bool(last("search_sent")),
        "search_inn_matches": last("search_sent").get("target_inn_matches"),
        "search_http_status": last("search_response").get("http_status"),
        "results_appeared": bool(results.get("cases_on_first_page")) if results else None,
        "results_count_confirmed": bool(results and results.get("count") is not None),
        "cases_found": results.get("count"),
        "card_opened": opened(card), "card_http_status": card.get("http_status"),
        "card_fields_extracted": fields.get("fields"),
        "role_matches_target_inn": fields.get("role_matches"),
        "role_check_not_applicable": bool(results and results.get("count") == 0),
        "failure": last("failure") or None,
        "note": "null означает: этап не выполнен или не подтверждён; это не успешная проверка",
    }
    summary["diagnostic_success"] = bool(
        summary["site_opened"] and summary["inn_filled"] and summary["search_sent"]
        and summary["search_inn_matches"] is True and summary["results_count_confirmed"]
        and not summary["challenge_detected"] and not summary["access_blocked"]
        and (summary["role_check_not_applicable"] or (summary["card_opened"]
                                                     and summary["role_matches_target_inn"])))
    return summary


def run(inn, directory):
    log = DiagnosticLog(directory)
    log("started", searched_inn=inn, mode="one search / one card / no retries", browser="visible Chromium")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            try:
                context = browser.new_context(locale="ru-RU")
                client = DiagnosticClient(context=context, cache_dir=Path(directory) / "client_result",
                                          diagnostic_callback=log)
                result = client.check(inn)
            finally:
                browser.close()
    except Exception as exc:
        log("failure", error_type=type(exc).__name__, reason=str(exc)[:2000])
        result = {"searched_inn": inn, "checked_in_kad": False, "complete": False,
                  "status": "KAD_UNAVAILABLE", "warnings": [f"{type(exc).__name__}: {str(exc)[:2000]}"]}
    summary = summarize(log.events)
    if summary["failure"]:
        result["warnings"].append(summary["failure"]["error_type"] + ": " + summary["failure"]["reason"])
    # Наличие ответа без подтверждённого ИНН в отправленном запросе не даёт уверенности.
    if summary["search_inn_matches"] is not True and result.get("checked_in_kad"):
        result.update(checked_in_kad=False, complete=False, status="KAD_INCOMPLETE")
        result["warnings"].append("ИНН в реально отправленном поисковом запросе не подтверждён")
    log.save("summary.json", {"searched_inn": inn, **summary, "kad_check": result})
    fields = next((event for event in reversed(log.events) if event["stage"] == "card_fields"), None)
    if fields:
        log.save("sample_case.json", fields)
    log("finished", diagnostic_success=summary["diagnostic_success"], kad_status=result["status"],
        complete_kad_check=result["checked_in_kad"], report=str(Path(directory) / "summary.json"))
    return 0 if summary["diagnostic_success"] else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inn", help="Один ИНН актуального продавца (10 или 12 цифр)")
    args = parser.parse_args()
    if not re.fullmatch(r"\d{10}|\d{12}", args.inn):
        parser.error("ИНН должен содержать 10 или 12 цифр")
    directory = ROOT / "data/kad_diagnostics" / args.inn / datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%f")
    print(f"Отчёт: {directory}", flush=True)
    return run(args.inn, directory)


if __name__ == "__main__":
    raise SystemExit(main())
