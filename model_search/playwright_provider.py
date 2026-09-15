"""Чтение публичных товарных страниц через изолированный Playwright-контекст."""
from __future__ import annotations

import re
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit
from urllib.parse import quote
from datetime import datetime, timezone

from documents.tender_archive import write_json
from model_search.live_price_search import inspect_product_url
from model_search.product_evidence import document_pages, html_page
from suppliers.market_search import normalize_product_url
from suppliers.price_search_flow import confirmed_price_ranking
from reports.price_search_report import write_price_search_markdown

MAX_UNIQUE_CANDIDATES = 100
DEFAULT_RESULTS_PER_QUERY = 20
DEFAULT_SATURATION_WINDOW = 20
DEFAULT_MIN_CANDIDATES_FOR_SATURATION = 40
DEFAULT_EXACT_QUERIES = (
    '"{model}"', '"{model}" купить', '"{model}" цена',
    '"{model}" в наличии', '"{model}" купить цена',
    '"{model}" поставщик', '"{model}" интернет магазин',
    '"{model}" недорого',
)


class YandexBrowserSearch:
    """Обычная публичная выдача Яндекса; защита останавливает поисковые запросы."""
    name = 'yandex_browser'

    def __init__(self, research, max_requests=24):
        self.research = research
        self.page = research.context.new_page()
        self.max_requests, self.requests, self.failed = max_requests, 0, False
        self.search_log = []

    def search(self, query, limit=20):
        if self.failed or self.requests>=self.max_requests:
            raise RuntimeError('Поиск Яндекса остановлен или достигнут лимит запросов')
        self.requests += 1
        entry = {'query':query, 'status':'started', 'snippet_is_evidence':False}
        self.search_log.append(entry)
        self.page.goto('https://yandex.ru/search/?text='+quote(query), wait_until='domcontentloaded', timeout=25000)
        self.page.wait_for_timeout(1500)
        text = self.page.locator('body').inner_text(timeout=5000)
        if ('showcaptcha' in self.page.url or re.search(r'(?i)подтвердите.{0,35}(?:не робот|вы человек)|проверка.{0,15}(?:робот|браузер)|пройдите.{0,15}captcha|доступ ограничен',text)):
            self.failed = True
            entry['status'] = 'requires_manual_check'
            raise PermissionError('Яндекс требует ручное подтверждение; обход и повторные запросы запрещены')
        links = self.page.locator('a[href]').evaluate_all('(links)=>links.map(a=>({url:a.href,title:a.innerText}))')
        rows, seen = [], set()
        for row in links:
            host = urlsplit(row['url']).hostname or ''
            if (urlsplit(row['url']).scheme not in {'http','https'} or not host
                    or host=='yandex.ru' or host.endswith('.yandex.ru') and host!='market.yandex.ru' or host=='ya.ru'
                    or any(host==d or host.endswith('.'+d) for d in ('google.com','google.ru','bing.com','bing.ru','yahoo.com','duckduckgo.com'))
                    or row['url'] in seen or not row['title'].strip()):
                continue
            rows.append({**row,'discovery_provider':self.name,'snippet_is_evidence':False})
            seen.add(row['url'])
            if len(rows)>=limit:
                break
        entry.update(status='completed',results_count=len(rows))
        return rows

    def fetch(self, url):
        return self.research.fetch(url)


class PlaywrightResearch:
    def __init__(self, context):
        self.context = context
        self.page = context.new_page()
        self.cache = {}
        self.http_cache = {}

    def read(self, url):
        if urlsplit(url).scheme not in {"https", "http"}:
            raise ValueError("Неподдерживаемая ссылка источника")
        if url in self.cache:
            return self.cache[url]
        response = self.page.goto(url, wait_until="domcontentloaded", timeout=25000)
        self.page.wait_for_timeout(1500)
        text = self.page.locator("body").inner_text(timeout=5000)
        if re.search(r"подтвердите.{0,30}(?:не робот|вы человек)|пройдите.{0,15}captcha|доступ ограничен", text, re.I):
            raise PermissionError("Источник требует ручного подтверждения; обход не выполняется")
        if response is None or response.status >= 400:
            raise ConnectionError("Товарная страница недоступна")
        result = (self.page.content(), self.page.url, response.status)
        self.cache[url] = result
        return result

    def read_http(self, url):
        """Дешёвый уровень чтения страницы без запуска браузера."""
        if urlsplit(url).scheme not in {"https", "http"}:
            raise ValueError("Неподдерживаемая ссылка источника")
        if url in self.http_cache:
            return self.http_cache[url]
        request = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; TENDER_AI price reader)",
            "Accept-Language": "ru-RU,ru;q=.9,en;q=.5",
        })
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read(5_000_001)
            if len(body) > 5_000_000:
                raise ValueError("Страница превышает лимит размера")
            result = (body.decode(response.headers.get_content_charset() or "utf-8", "replace"),
                      response.geturl(), response.status)
        self.http_cache[url] = result
        return result

    def fetch(self, url):
        suffix = Path(urlsplit(url).path).suffix.casefold()
        if suffix in {".pdf", ".doc", ".docx", ".xlsx"}:
            response = self.context.request.get(url, timeout=25000)
            if not response.ok:
                raise ConnectionError("Документ источника недоступен")
            with tempfile.TemporaryDirectory(prefix="tender_source_") as directory:
                path = Path(directory) / ("source" + suffix)
                path.write_bytes(response.body())
                return document_pages(path, response.url, source_type="other", source_verified=False)
        body, final_url, _ = self.read(url)
        return {**html_page(body, final_url), "source_type": "other", "source_verified": False}

    def prices(self, model, provider, output_path):
        return self.prices_exact(model, provider, output_path)

    def prices_exact(self, model, provider, output_path, *,
                     max_sources=MAX_UNIQUE_CANDIDATES,
                     results_per_query=DEFAULT_RESULTS_PER_QUERY,
                     saturation_window=DEFAULT_SATURATION_WINDOW,
                     min_candidates_for_saturation=DEFAULT_MIN_CANDIDATES_FOR_SATURATION,
                     target_max_unit_price=None,
                     price_gate_source_limit=40, requirements=(), product_name=None,
                     stop_after_top3=True, max_queries=3):
        """Настойчивый поиск трёх минимальных цен точной модели.

        Минимум определяется по прочитанным страницам, а не по сниппетам.
        Лимит и ошибки сохраняются: глобальный минимум всего интернета
        конечным числом запросов доказать нельзя.
        """
        queries = [template.format(model=model) for template in DEFAULT_EXACT_QUERIES[:max_queries]]
        price_run_id = datetime.now(timezone.utc).isoformat()
        # Кэши живут только в текущем поиске: повторный запуск читает страницы заново.
        self.cache.clear()
        self.http_cache.clear()
        rows, errors, query_log = {}, [], []
        for query in queries:
            try:
                found = provider.search(query, limit=results_per_query)
                query_log.append({"query": query, "status": "ВЫПОЛНЕНО", "links_found": len(found)})
                for row in found:
                    if row.get("url"):
                        rows.setdefault(normalize_product_url(row["url"]), row)
            except Exception as exc:
                query_log.append({"query": query, "status": "ОШИБКА", "error": type(exc).__name__})
        offers = []
        # price_gate_source_limit — отдельная жёсткая граница карточек,
        # читаемых для текущего ценового gate; max_sources ограничивает общий
        # пул кандидатов и остаётся параметром глубокого поиска.
        effective_source_limit = (min(max_sources, price_gate_source_limit)
                                  if price_gate_source_limit and stop_after_top3
                                  else max_sources)
        selected = list(rows.items())[:effective_source_limit]
        http_pages_read = playwright_pages_read = product_cards_confirmed = 0
        pages_without_price = 0
        top3 = []
        last_improvement = 0
        for index, (url, row) in enumerate(selected, 1):
            try:
                offer = http_offer = None
                try:
                    http_offer = inspect_product_url({"product_url": url}, model,
                                                     fetcher=self.read_http, transport="http",
                                                     requirements=requirements, product_name=product_name)
                    http_pages_read += 1
                    offer = http_offer
                except Exception as exc:
                    errors.append({"url": url, "error": f"HTTP {type(exc).__name__}"})
                if (offer is None or offer.get("price") is None or requirements
                        and offer.get('customer_requirements_status') != 'fully_compliant'):
                    try:
                        browser_offer = inspect_product_url({"product_url": url}, model,
                                                           fetcher=self.read, transport="playwright",
                                                           requirements=requirements, product_name=product_name)
                        playwright_pages_read += 1
                        offer = browser_offer
                        if browser_offer and browser_offer.get('price') is None and http_offer and http_offer.get('price') is not None:
                            # Браузер проверяет параметры, подтверждённая HTTP-цена не теряется.
                            offer = {**browser_offer, **{key:http_offer.get(key) for key in (
                                'price','confirmed_price','currency','price_confirmed_on_product_page','extraction_method')}}
                    except Exception as exc:
                        errors.append({"url": url, "error": f"Playwright {type(exc).__name__}"})
                # Полная марка дополнительно защищает от совпавших SKU других производителей.
                brand = model.split()[0] if len(model.split()) > 1 else None
                if offer and brand and brand.casefold() not in str(offer.get("exact_product_name") or "").casefold():
                    offer = None
                if offer:
                    offer['price_run_id'] = price_run_id
                    product_cards_confirmed += 1
                    if offer.get("price") is None:
                        pages_without_price += 1
                        errors.append({"url": url, "error": "price_not_confirmed"})
                    elif target_max_unit_price is not None:
                        offer["target_max_unit_price"] = target_max_unit_price
                        offer["price_classification"] = (
                            "profitable_candidate" if offer["price"] <= target_max_unit_price
                            else "near_target" if offer["price"] <= target_max_unit_price * 1.20
                            else "unprofitable")
                    offers.append(offer)
                elif not any(e.get("url") == url for e in errors):
                    errors.append({"url": url, "error": "Страница не подтверждает точную модель и марку"})
                current = confirmed_price_ranking(offers)
                current_top = [(x.get("url"), x.get("public_price", x.get("price"))) for x in current[:3]]
                if current_top != top3:
                    top3 = current_top
                    last_improvement = index
                if stop_after_top3 and len(current)>=3:
                    selected = selected[:index]
                    break
                if (len(selected) >= min_candidates_for_saturation and len(current) >= 3
                        and index - last_improvement >= saturation_window):
                    selected = selected[:index]
                    break
            except Exception as exc:
                errors.append({"url": url, "error": type(exc).__name__})
            print(f"EXACT_MODEL: проверен источник {index}/{len(selected)}; цен найдено: "
                  f"{sum(o.get('price') is not None for o in offers)}", flush=True)
        offers = sorted({o["url"]: o for o in offers}.values(),
                        key=lambda o: (o["price"] is None, o["price"] or float("inf")))
        priced = [o for o in offers if o["price"] is not None]
        confirmed = confirmed_price_ranking(offers)
        unique_domains = {urlsplit(url).hostname for url, _ in selected if urlsplit(url).hostname}
        unique_product_pages = {(urlsplit(x.get("url") or "").hostname or "", urlsplit(x.get("url") or "").path.rstrip("/")) for x in offers}
        result = {"target_model": model, "model_search_mode": "EXACT_MODEL",
                  "price_run_id": price_run_id,
                  "checked_at": datetime.now(timezone.utc).isoformat(), "queries_used": queries,
                  "query_log": query_log, "candidate_urls_found": len(rows),
                  "unique_urls": len(selected), "unique_product_pages": len(unique_product_pages),
                  "unique_domains": len(unique_domains), "sources_checked": len(selected), "source_limit": max_sources,
                  "search_truncated": len(rows) > max_sources, "offers": offers,
                  "price_candidates": priced,
                  "product_cards_confirmed": product_cards_confirmed,
                  "confirmed_prices": len(priced), "http_pages_read": http_pages_read,
                  "playwright_pages_read": playwright_pages_read, "pages_without_price": pages_without_price,
                  "offers_found": len(offers), "minimum_price": priced[0]["price"] if priced else None,
                  "top3_confirmed_prices": confirmed[:3],
                  "source_errors": errors, "discovery_provider": provider.name if isinstance(getattr(provider,'name',None),str) else 'yandex_search_api',
                  "search_status": 'completed' if confirmed else 'failed',
                  "page_verification": "playwright",
                  "target_max_unit_price": target_max_unit_price,
                  "stop_reason": ("Собраны три подтверждённых предложения разных продавцов" if stop_after_top3 and len(confirmed)>=3
                                  else "Достигнут лимит уникальных кандидатов" if len(selected) >= max_sources
                                  else "Насыщение: TOP-3 не улучшался заданное окно" if len(selected) < len(rows)
                                  else "Все уникальные кандидаты из поисковой выдачи проверены"),
                  "minimum_scope": "Минимум среди подтверждённых цен проверенных источников"}
        write_json(output_path, result)
        # Markdown перечитывает сохранённый JSON и не запускает повторное чтение страниц.
        write_price_search_markdown(output_path)
        return result
