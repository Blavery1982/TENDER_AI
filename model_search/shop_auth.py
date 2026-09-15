"""Одна попытка штатного входа в разрешённый магазин; CAPTCHA не решаем."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin

from security.shop_credentials import approved_site, get_shop_credentials

CAPTCHA = re.compile(
    r'капч|captcha|не.{0,15}бот|не.{0,15}робот|пройдите проверку|'
    r'разверните картинку|verify you are human|unusual traffic', re.I)
MANUAL = re.compile(r'одноразов\w* код|код подтверждения|код из (?:смс|sms)|подтвердите вход', re.I)


@dataclass(frozen=True)
class LoginResult:
    status: str
    site: str | None = None


def authenticate_shop(page, *, credentials_provider=get_shop_credentials) -> LoginResult:
    """Только текущая штатная POST-форма; неоднозначность требует человека.

    Вызывать без tracing, HAR, screenshots и слушателей сетевых payload.
    Сессию в файлы не сохраняем. При CAPTCHA оставляем страницу открытой.
    """
    try:
        site = approved_site(page.url)
    except Exception:
        return LoginResult('unsupported_site')

    def guard(route):
        try:
            allowed = approved_site(route.request.url) == site
        except Exception:
            allowed = False
        route.continue_() if allowed else route.abort()

    guarded = False
    user = password = credentials = None
    user_filled = password_filled = False
    submitted = False
    try:
        page.context.route('**/*', guard)
        guarded = True
        def state():
            if approved_site(page.url) != site:
                return 'unsupported_site'
            text = page.locator('body').inner_text(timeout=5000)
            widgets = page.locator('iframe[src*="captcha"], .g-recaptcha, .h-captcha')
            if any(widgets.nth(i).is_visible() for i in range(widgets.count())):
                return 'manual_captcha'
            if CAPTCHA.search(text):
                return 'manual_captcha'
            if MANUAL.search(text):
                return 'manual_confirmation'
            logout = page.get_by_role('link', name=re.compile(r'^(Выйти|Выход)$', re.I)).or_(
                page.get_by_role('button', name=re.compile(r'^(Выйти|Выход)$', re.I)))
            if any(logout.nth(i).is_visible() for i in range(logout.count())):
                return 'signed_in'
            return 'form'
        current = state()
        if current != 'form':
            return LoginResult(current, site)
        forms = page.locator('form').filter(has=page.locator('input[type="password"]'))
        visible = [forms.nth(i) for i in range(forms.count()) if forms.nth(i).is_visible()]
        if len(visible) != 1:
            return LoginResult('manual_login', site)
        form = visible[0]
        action = urljoin(page.url, form.get_attribute('action') or page.url)
        if approved_site(action) != site or (form.get_attribute('method') or '').lower() != 'post':
            return LoginResult('unsafe_form', site)
        if form.locator('input[type="checkbox"]:required').count():
            return LoginResult('manual_confirmation', site)
        user = form.locator('input[autocomplete="username"], input[type="email"], input[name="login"], input[name="email"]')
        password = form.locator('input[type="password"]')
        button = form.get_by_role('button', name=re.compile(r'^(Войти|Вход|Авторизоваться)$', re.I))
        if user.count() != 1 or password.count() != 1 or button.count() != 1:
            return LoginResult('manual_login', site)
        credentials = credentials_provider(page.url)
        if credentials.status != 'ready' or credentials.credentials is None:
            return LoginResult(credentials.status, site)
        # Повторная проверка URL и challenge непосредственно перед вводом секрета.
        current = state()
        if current != 'form':
            return LoginResult(current, site)
        user_filled = True
        user.fill(credentials.credentials.login, timeout=10000)
        password_filled = True
        password.fill(credentials.credentials.password, timeout=10000)
        button.click(timeout=15000)
        submitted = True
        page.wait_for_timeout(1500)
        current = state()
        return LoginResult('login_not_confirmed' if current == 'form' else current, site)
    except Exception:
        # Ошибки Playwright могут содержать введённые значения: не возвращаем их текст.
        return LoginResult('login_not_confirmed' if submitted else 'login_error', site)
    finally:
        credentials = None
        for field, filled in ((password, password_filled), (user, user_filled)):
            if field is not None and filled:
                try:
                    field.fill('', timeout=1000)
                except Exception:
                    pass
        if guarded:
            try:
                page.context.unroute('**/*', guard)
            except Exception:
                pass
