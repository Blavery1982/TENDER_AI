"""Обычная выдача Google в текущем Playwright-контексте, без обхода защиты."""
import re
from urllib.parse import quote, urlsplit, parse_qs

from model_search.diagnostics import (BlockedSourceError, block_markers,
                                      make_block_diagnostic, safe_page_url,
                                      save_snapshot)


class GoogleBrowserSearch:
    name = 'google_browser'

    def __init__(self, research, max_requests=24, diagnostic_dir=None):
        self.research = research
        self.page = research.context.new_page()
        self.max_requests = max_requests
        self.requests = 0
        self.failed = False
        self.search_log = []
        self.diagnostic_dir = diagnostic_dir
        self.diagnostics = []
        self.block_diagnostic = None

    @property
    def manual_captcha_available(self):
        return bool(self.block_diagnostic
                    and self.block_diagnostic.get("classification") == "captcha_or_robot_check")

    def _raise_block(self, query, response, text, reason, *, primary=True):
        status = getattr(response, "status", None) if response is not None else None
        markers = block_markers(text)
        snapshot = save_snapshot(self.page, self.diagnostic_dir, "google_block") if primary else None
        diagnostic = make_block_diagnostic(
            stage="google_search" if primary else "google_search_deferred",
            reason=reason, status=status, url=self.page.url,
            snapshot=snapshot, markers=markers, primary=primary)
        if primary:
            diagnostic["query"] = query
            self.block_diagnostic = diagnostic
        else:
            diagnostic["primary_reason"] = (self.block_diagnostic or {}).get("reason")
            diagnostic["primary_url"] = (self.block_diagnostic or {}).get("url")
        self.diagnostics.append(diagnostic)
        message = ("Google требует ручного подтверждения; поиск отложен"
                   if reason == "captcha_or_robot_check"
                   else f"Google заблокировал выдачу ({reason})")
        raise BlockedSourceError(message, diagnostic)

    def continue_after_manual_check(self):
        """Продолжить после ручного прохождения CAPTCHA в этом же page/context."""
        if not self.block_diagnostic:
            return {"status": "not_blocked"}
        if not self.manual_captcha_available:
            raise BlockedSourceError(
                "Автоматическое продолжение разрешено только после CAPTCHA",
                make_block_diagnostic(stage="google_manual_continue",
                                      reason="manual_continue_not_allowed",
                                      status=None, url=self.page.url,
                                      primary=False))
        text = self.page.locator("body").inner_text(timeout=5000)
        markers = block_markers(text)
        if "/sorry/" in self.page.url or markers:
            diagnostic = make_block_diagnostic(
                stage="google_manual_continue", reason="captcha_still_present",
                status=None, url=self.page.url, markers=markers, primary=False)
            self.diagnostics.append(diagnostic)
            raise BlockedSourceError("CAPTCHA ещё не пройдена", diagnostic)
        self.failed = False
        cleared = make_block_diagnostic(
            stage="google_manual_continue", reason="captcha_cleared",
            status=None, url=self.page.url, primary=False)
        self.diagnostics.append(cleared)
        return cleared

    def search(self, query, limit=20):
        if self.failed:
            self._raise_block(query, None, "", "deferred_due_to_primary_block", primary=False)
        if self.requests >= self.max_requests:
            raise RuntimeError('Достигнут лимит поисковых запросов')
        self.requests += 1
        entry = {'query': query, 'status': 'started', 'snippet_is_evidence': False}
        self.search_log.append(entry)
        request_url = 'https://www.google.com/search?q=' + quote(query)
        response = self.page.goto(request_url,
                                  wait_until='domcontentloaded', timeout=25000)
        text = self.page.locator('body').inner_text(timeout=5000)
        status = getattr(response, "status", None) if response is not None else None
        if status == 403:
            self.failed = True
            entry['status'] = 'requires_manual_check'
            self._raise_block(query, response, text, "http_403")
        if status == 429:
            self.failed = True
            entry['status'] = 'requires_manual_check'
            self._raise_block(query, response, text, "http_429")
        markers = block_markers(text)
        if '/sorry/' in self.page.url or markers:
            self.failed = True
            entry['status'] = 'requires_manual_check'
            self._raise_block(query, response, text, "captcha_or_robot_check")
        if response is None or response.status >= 400:
            entry['status'] = 'network_error'
            raise ConnectionError('Выдача Google недоступна')
        links = self.page.locator('a:has(h3)').evaluate_all(
            '(links)=>links.map(a=>({url:a.href,title:a.innerText}))')
        rows, seen = [], set()
        for row in links:
            url = row['url']
            parsed = urlsplit(url)
            if parsed.hostname in ('google.com', 'www.google.com') and parsed.path == '/url':
                url = parse_qs(parsed.query).get('q', [''])[0]
                parsed = urlsplit(url)
            host = parsed.hostname or ''
            if (parsed.scheme not in ('http', 'https') or not host or url in seen
                    or any(host == d or host.endswith('.' + d) for d in
                           ('google.com', 'google.ru', 'bing.com', 'yandex.ru'))):
                continue
            seen.add(url)
            rows.append({**row, 'url': url, 'discovery_provider': self.name,
                         'snippet_is_evidence': False})
            if len(rows) >= limit:
                break
        entry.update(status='completed', results_count=len(rows))
        return rows

    def fetch(self, url):
        return self.research.fetch(url)
