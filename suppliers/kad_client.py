"""Получение публичных дел КАД; защита и неполнота никогда не означают успех."""
from __future__ import annotations

import hashlib
import http.cookiejar
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config/kad.json").read_text(encoding="utf-8"))
RUN_ID = uuid.uuid4().hex


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    parent: Node | None = None

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def text(self):
        if self.tag in {"script", "style", "noscript"}:
            return ""
        return " ".join(child.text() if isinstance(child, Node) else child
                        for child in self.children)


class TreeParser(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), parent=self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in {"input", "br", "img", "meta", "link", "hr", "source", "wbr", "area", "base"}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, dict(attrs), parent=self.stack[-1]))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _role(value):
    value = _clean(value).casefold().rstrip(":")
    return next((name for name, labels in CONFIG["role_labels"].items()
                 if value == name.casefold() or value in labels), None)


def _has_inn(node, inn):
    for child in node.walk():
        if child.attrs.get("data-inn") == inn:
            return True
    # ИНН должен быть обозначен как реквизит, а не совпасть с номером дела/документа.
    return bool(re.search(r"\bИНН\s*[:№]?\s*" + re.escape(inn) + r"(?!\d)",
                          _clean(node.text()), re.I))


def participant_roles(root, inn):
    """Ищет наш ИНН только внутри явно обозначенной группы участников."""
    found = set()
    for node in root.walk():
        role = _role(node.attrs.get("data-role"))
        if role and _has_inn(node, inn):
            found.add(role)
        if node.tag not in {"dt", "h2", "h3", "h4", "th", "strong", "b"}:
            continue
        role = _role(node.text())
        if not role or node.parent is None:
            continue
        siblings = node.parent.children
        for sibling in siblings[siblings.index(node) + 1:]:
            if isinstance(sibling, Node):
                if _role(sibling.text()) or sibling.tag == node.tag:
                    break
                if _has_inn(sibling, inn):
                    found.add(role)
        if node.tag == "th" and node.parent.tag == "tr":
            headers = [x for x in siblings if isinstance(x, Node) and x.tag == "th"]
            column = headers.index(node)
            table = node.parent
            while table.parent and table.tag != "table":
                table = table.parent
            for row in table.walk():
                if row.tag != "tr":
                    continue
                cells = [x for x in row.children if isinstance(x, Node) and x.tag == "td"]
                if len(cells) > column and _has_inn(cells[column], inn):
                    found.add(role)
    return [name for name in CONFIG["role_labels"] if name in found]


def _labelled(root, labels):
    labels = {value.casefold() for value in labels}
    for node in root.walk():
        if node.tag not in {"dt", "th", "b", "strong", "label"} or not node.parent:
            continue
        if _clean(node.text()).casefold().rstrip(":") not in labels:
            continue
        for sibling in node.parent.children[node.parent.children.index(node) + 1:]:
            text = _clean(sibling.text() if isinstance(sibling, Node) else sibling)
            if text:
                return text
    return None


def _date(text):
    match = re.search(r"\b(\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})\b", text)
    if not match:
        return None
    try:
        return datetime.strptime(match[0], "%d.%m.%Y" if "." in match[0] else "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _category(root):
    value = _labelled(root, ("категория", "категория дела", "вид дела", "предмет спора"))
    if value:
        return value
    for node in root.walk():
        code = node.attrs.get("data-case-type")
        if code in CONFIG["category_codes"]:
            return CONFIG["category_codes"][code]
        title = node.attrs.get("title") or ""
        if _clean(title) in CONFIG["category_codes"].values():
            return _clean(title)
    return None


def parse_card(html, case, inn):
    """Только инструмент разработки kad_diagnostic; production карточки не читает."""
    root = TreeParser(html).root
    roles = participant_roles(root, inn)
    category = _category(root) or case.get("category")
    bankruptcy = ("банкрот" in category.casefold()) if category else case.get("is_bankruptcy")
    return {**case, "role": "; ".join(roles) or None, "roles": roles,
            "target_inn": inn if roles else None, "category": category,
            "is_bankruptcy": bankruptcy,
            "status": _labelled(root, ("статус", "статус дела", "состояние дела")),
            "decision": _labelled(root, ("решение", "результат", "результат рассмотрения"))}


def _search_counts(root, html):
    total = pages = None
    totals, page_counts = set(), set()
    for node in root.walk():
        key = (node.attrs.get("id") or node.attrs.get("name") or "").casefold()
        value = node.attrs.get("value")
        if value is not None and str(value).isdigit():
            if key in {"documentstotalcount", "totalcount"}:
                total = int(value)
                totals.add(total)
            elif key in {"documentspagescount", "pagescount"}:
                pages = int(value)
                page_counts.add(pages)
    # Читается только конкретный вызов построения страниц, без выполнения JavaScript.
    pagination = re.search(r"reDrawPages\s*\(\s*\{([^{}]*)\}\s*\)", html)
    if pagination:
        values = dict(re.findall(r"[\"']?(totalCount|pagesCount)[\"']?\s*:\s*(\d+)", pagination[1]))
        if "totalCount" in values:
            total = int(values["totalCount"])
            totals.add(total)
        if "pagesCount" in values:
            pages = int(values["pagesCount"])
            page_counts.add(pages)
    if len(totals) > 1 or len(page_counts) > 1:
        raise ValueError("Счётчики выдачи КАД противоречат друг другу")
    if pages is None and total is not None:
        pages = (total + CONFIG["page_size"] - 1) // CONFIG["page_size"]
    return total, pages


def parse_defendant_search_page(html, inn):
    """Сопоставляет ИНН с ролью в строке выдачи; карточки не запрашивает."""
    root = TreeParser(html).root
    total, pages = _search_counts(root, html)
    participants = {}
    ambiguous = False
    for table in root.walk():
        if table.tag != "table":
            continue
        # Заголовок таблицы определяет роль конкретного столбца, а не всей строки.
        columns = {}
        for row in table.walk():
            headers = [node for node in row.children if isinstance(node, Node) and node.tag == "th"]
            if headers:
                if all(node.attrs.get("colspan", "1") == "1" and node.attrs.get("rowspan", "1") == "1"
                       for node in headers):
                    columns = {index: _role(node.text()) for index, node in enumerate(headers)}
                break
        for row in table.walk():
            if row.tag != "tr":
                continue
            identities = set()
            for node in row.walk():
                href = urlsplit(urljoin(CONFIG["base_url"], node.attrs.get("href", "")))
                if (node.tag == "a" and href.hostname == "kad.arbitr.ru"
                        and re.fullmatch(r"/Card/[0-9a-fA-F-]{36}", href.path)):
                    identities.add(href.path)
            if not identities:
                continue
            if len(identities) != 1:
                ambiguous = True
                continue
            roles = set(participant_roles(row, inn))
            cells = [node for node in row.children if isinstance(node, Node) and node.tag == "td"]
            for index, role in columns.items():
                if role and index < len(cells) and _has_inn(cells[index], inn):
                    roles.add(role)
            if not roles:
                ambiguous = True
                continue
            identity = next(iter(identities))
            defendant = "Ответчик" in roles
            if identity in participants and participants[identity] != defendant:
                ambiguous = True
            participants[identity] = defendant
    return {"total": total, "pages": pages, "participants": participants, "ambiguous": ambiguous}


def parse_search_page(html):
    """Подробная выдача только для kad_diagnostic; не используется production-счётчиком."""
    root = TreeParser(html).root
    total, pages = _search_counts(root, html)
    cases = {}
    for node in root.walk():
        href = node.attrs.get("href") or ""
        url = urljoin(CONFIG["base_url"], href)
        if node.tag != "a" or not re.fullmatch(r"/Card/[0-9a-fA-F-]{36}", urlsplit(url).path):
            continue
        if urlsplit(url).hostname != "kad.arbitr.ru":
            continue
        number = _clean(node.text())
        if not re.fullmatch(r"[АA]\d+[\w-]*/\d{4}", number):
            continue
        row = node
        while row.parent and row.tag != "tr":
            row = row.parent
        category = _category(row)
        cases[url] = {"number": number, "date": _date(_clean(row.text())),
                      "url": url, "category": category,
                      "is_bankruptcy": "банкрот" in category.casefold() if category else None}
    return {"cases": list(cases.values()), "total": total, "pages": pages}


def check_access(status, html):
    markers = [marker for marker in CONFIG["manual_markers"] if marker in html.casefold()]
    if status in {403, 429, 451} or markers:
        reason = "; признаки защиты: " + ", ".join(markers[:4]) if markers else ""
        raise PermissionError(f"HTTP {status}: КАД требует ручного доступа{reason}; защита не обходилась")
    if status >= 400:
        raise ConnectionError(f"HTTP {status} КАД")


class KADClient:
    development_diagnostic = False

    def __init__(self, *, context=None, cache_dir=None, cache_max_age_hours=None,
                 run_id=RUN_ID, http_reader=None, browser_collector=None, diagnostic_callback=None):
        self.context = context
        self.cache_dir = Path(cache_dir) if cache_dir is not None else ROOT / "data/cache/kad"
        self.cache_max_age_hours = (cache_max_age_hours if cache_max_age_hours is not None
                                    else CONFIG["cache_max_age_hours"])
        if self.cache_max_age_hours is not None and self.cache_max_age_hours < 0:
            raise ValueError("Срок кэша не может быть отрицательным")
        self.run_id = run_id
        self.http_reader = http_reader
        self.browser_collector = browser_collector
        self.diagnostic_callback = diagnostic_callback
        self.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.collection = None

    def _diagnostic(self, stage, **details):
        if self.diagnostic_callback:
            self.diagnostic_callback(stage, **details)

    def _browser_access(self, stage, status, html):
        markers = [marker for marker in CONFIG["manual_markers"] if marker in html.casefold()]
        self._diagnostic("access", location=stage, http_status=status,
                         challenge_detected=bool(markers), markers=markers,
                         access_blocked=status in {403, 429, 451})
        check_access(status, html)

    def _read_http(self, url, payload=None):
        if self.http_reader:
            html, status = self.http_reader(url, payload)
        else:
            request = Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                              headers={"User-Agent": "TENDER_AI/1.0 supplier verification",
                                       "Content-Type": "application/json", "Referer": CONFIG["base_url"] + "/"})
            try:
                with self.opener.open(request, timeout=20) as response:
                    if urlsplit(response.geturl()).hostname != "kad.arbitr.ru":
                        raise ConnectionError("КАД перенаправил на другой домен")
                    data = response.read(5_000_001)
                    if len(data) > 5_000_000:
                        raise ValueError("Ответ КАД превышает лимит размера")
                    html, status = data.decode("utf-8", "replace"), response.status
            except HTTPError as exc:
                html, status = exc.read(100_000).decode("utf-8", "replace"), exc.code
            except URLError as exc:
                raise ConnectionError(f"HTTP КАД недоступен: {str(exc.reason)[:300]}") from exc
        check_access(status, html)
        return html

    def _collect_pages(self, inn, search, read_card=None):
        # Идентификаторы нужны только для устранения дублей внутри текущего поиска.
        seen = {}
        count = 0
        expected = None
        try:
            for number in range(1, CONFIG["max_search_pages"] + 1):
                parsed = parse_defendant_search_page(search(number), inn)
                if parsed["total"] is None or parsed["pages"] is None:
                    raise ValueError("Нет подтверждённого счётчика результатов КАД")
                if parsed["total"] > 0 and parsed["pages"] < 1:
                    raise ValueError("Некорректный счётчик страниц КАД")
                if expected is not None and expected != parsed["total"]:
                    raise ValueError("Состав выдачи КАД изменился во время проверки")
                expected = parsed["total"]
                if parsed["ambiguous"]:
                    raise ValueError("В выдаче КАД нельзя однозначно сопоставить роль участника с нашим ИНН")
                for identity, defendant in parsed["participants"].items():
                    if identity in seen and seen[identity] != defendant:
                        raise ValueError("Роль участника изменилась между страницами КАД")
                    if identity not in seen:
                        seen[identity] = defendant
                        count += int(defendant)
                if number >= parsed["pages"]:
                    if len(seen) != expected:
                        raise ValueError("Получена неполная выдача КАД")
                    from suppliers.arbitration import defendant_kad_result
                    return defendant_kad_result(inn, count)
                # Только необходимые страницы; без частых повторов и запросов к карточкам.
                self._pause()
            raise ValueError("Достигнут технический лимит страниц: КАД не проверен полностью")
        finally:
            seen.clear()

    def _pause(self):
        import time
        time.sleep(1)

    def _http_collect(self, inn):
        self._read_http(CONFIG["base_url"] + "/")
        def search(page):
            return self._read_http(CONFIG["base_url"] + CONFIG["search_path"], {
                "Page": page, "Count": CONFIG["page_size"], "Courts": [],
                "DateFrom": None, "DateTo": None, "Sides": [{"Name": inn, "Type": -1, "ExactMatch": True}],
                "Judges": [], "CaseNumbers": [], "WithVKSInstances": False})
        return self._collect_pages(inn, search, self._read_http)

    def _browser_collect(self, inn):
        if self.browser_collector:
            return self.browser_collector(inn)
        if self.context is None:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                try:
                    context = browser.new_context(locale="ru-RU")
                    return self._browser_pages(inn, context)
                finally:
                    browser.close()
        return self._browser_pages(inn, self.context)

    def _browser_pages(self, inn, context):
        page = context.new_page()
        card_page = None
        try:
            if self.diagnostic_callback:
                def sent(request):
                    if (urlsplit(request.url).hostname == "kad.arbitr.ru"
                            and urlsplit(request.url).path == CONFIG["search_path"]
                            and request.method == "POST"):
                        try:
                            sides = request.post_data_json.get("Sides", [])
                            matches = any(str(side.get("Name")) == inn for side in sides)
                        except (ValueError, TypeError, AttributeError):
                            matches = None
                        self._diagnostic("search_sent", target_inn_matches=matches,
                                         url=CONFIG["base_url"] + CONFIG["search_path"], method="POST")
                page.on("request", sent)
            self._diagnostic("site_open_started")
            response = page.goto(CONFIG["base_url"] + "/", wait_until="domcontentloaded", timeout=25000)
            self._diagnostic("site_opened", url=page.url, http_status=response.status if response else None)
            if urlsplit(page.url).hostname != "kad.arbitr.ru":
                raise ConnectionError("КАД перенаправил на другой домен")
            self._browser_access("site", response.status if response else 500, page.content())
            page.wait_for_timeout(1500)
            self._browser_access("site_visible", 200, page.locator("body").inner_text())
            close = page.locator(CONFIG["notification_close_selector"])
            if close.count() and close.first.is_visible():
                close.first.click(timeout=3000)
            field = page.get_by_placeholder(CONFIG["participant_placeholder"])
            self._diagnostic("inn_fill_started", placeholder=CONFIG["participant_placeholder"])
            field.click(timeout=5000)
            field.press_sequentially(inn, delay=20)
            if self.diagnostic_callback:
                value = field.input_value()
                self._diagnostic("inn_filled", value=value, matches=value == inn)
            field.press("Tab")
            def search(number):
                try:
                    with page.expect_response(lambda r: urlsplit(r.url).path == CONFIG["search_path"]
                                               and r.request.method == "POST", timeout=25000) as captured:
                        if number == 1:
                            self._diagnostic("search_click_started")
                            page.get_by_role("button", name="Найти", exact=True).click(timeout=5000)
                            self._diagnostic("search_clicked")
                        else:
                            page.locator(f'#b-footer-pages a[href="#page{number}"]').click(timeout=5000)
                    response = captured.value
                    html = response.text()
                    self._diagnostic("search_response", http_status=response.status)
                    self._browser_access("search", response.status, html)
                    if not self.development_diagnostic:
                        payload = response.request.post_data_json
                        sides = payload.get("Sides", []) if isinstance(payload, dict) else []
                        if (len(sides) != 1 or not isinstance(sides[0], dict)
                                or str(sides[0].get("Name")) != inn or sides[0].get("Type") != -1):
                            raise ValueError("Поисковый запрос КАД по нашему ИНН не подтверждён")
                        if any(payload.get(key) for key in ("Courts", "Judges", "CaseNumbers", "DateFrom", "DateTo", "WithVKSInstances")):
                            raise ValueError("Поиск КАД ограничен дополнительными фильтрами; полный счётчик не подтверждён")
                    return html
                except Exception:
                    self._browser_access("search_failure_page", 200, page.content())
                    raise
            def read_card(url):
                nonlocal card_page
                if card_page is None:
                    card_page = context.new_page()
                self._diagnostic("card_open_started", url=url)
                response = card_page.goto(url, wait_until="domcontentloaded", timeout=25000)
                self._diagnostic("card_opened", url=card_page.url,
                                 http_status=response.status if response else None)
                if urlsplit(card_page.url).hostname != "kad.arbitr.ru":
                    raise ConnectionError("Карточка КАД перенаправила на другой домен")
                self._browser_access("card", response.status if response else 500, card_page.content())
                card_page.wait_for_timeout(1000)
                self._browser_access("card_visible", 200, card_page.content())
                return card_page.content()
            return self._collect_pages(inn, search, read_card)
        finally:
            if card_page is not None:
                card_page.close()
            page.close()

    def collect(self, inn):
        # Единственный транспорт за попытку, без повторного поиска после ошибки/защиты.
        return self._browser_collect(inn) if self.context is not None else self._http_collect(inn)

    def _cached(self, inn, now):
        from suppliers.arbitration import minimal_kad_result
        directory = self.cache_dir / inn
        for path in sorted(directory.glob("*.json"), reverse=True):
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                original = saved["result"]
                if saved.get("schema_version") != 2 or original.get("searched_inn") != inn:
                    continue
                result = minimal_kad_result(original)
                checked = datetime.fromisoformat(result["checked_at"])
                if checked > now:
                    continue
                same_run = saved.get("run_id") == self.run_id
                allowed_age = (self.cache_max_age_hours is not None
                               and result["technical_status"] == "KAD_CHECKED"
                               and now - checked <= timedelta(hours=self.cache_max_age_hours))
                if same_run or allowed_age:
                    return result
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return None

    def check(self, inn, *, force_refresh=False):
        from suppliers.arbitration import check_kad, unavailable_kad_result
        if not re.fullmatch(r"\d{10}|\d{12}", str(inn or "")):
            return unavailable_kad_result(inn, "ИНН не имеет подтверждённого формата",
                                          status="KAD_CURRENT_SELLER_UNDETERMINED")
        now = datetime.now(timezone.utc)
        if not force_refresh:
            cached = self._cached(inn, now)
            if cached:
                return cached
        result = check_kad(inn, self.collect)
        directory = self.cache_dir / inn
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (now.strftime("%Y-%m-%dT%H-%M-%S-%f") + ".json")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"schema_version": 2, "run_id": self.run_id,
                                          "result": result}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        return result


def save_supplier_kad_evidence(domain, seller, kad, *, directory=None):
    """В отдельном файле сохраняется только краткий результат КАД."""
    from suppliers.arbitration import minimal_kad_result
    directory = Path(directory) if directory is not None else ROOT / "data/suppliers/_kad"
    folder = directory / hashlib.sha256(domain.encode()).hexdigest()[:20]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%f") + ".json")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(minimal_kad_result(kad, seller.get("inn")),
                                     ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return str(path)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Количество дел ответчика по текущему ИНН поставщика")
    parser.add_argument("inn", help="ИНН актуального продавца, установленный до запуска")
    parser.add_argument("--cache-max-age-hours", type=float, default=None,
                        help="Разрешённый срок повторного использования успешного результата между запусками")
    parser.add_argument("--force-refresh", action="store_true", help="Явно повторить проверку, игнорируя кэш")
    args = parser.parse_args()
    result = KADClient(cache_max_age_hours=args.cache_max_age_hours).check(args.inn, force_refresh=args.force_refresh)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
