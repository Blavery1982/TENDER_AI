"""Авторизация ЕАТ с повторным использованием безопасно сохранённой сессии.

Пароль используется только если восстановленная сессия уже недействительна.
Никаких logout, tracing, screenshots или credential logging.
"""
from __future__ import annotations
import re
import time
from urllib.parse import urljoin, urlsplit
from security.eat_credentials import get_eat_credentials
from eat.auth_client import trusted, restricted

ENTRY_URL = 'https://agregatoreat.ru/'
PURCHASES_URL = 'https://agregatoreat.ru/purchases'
FAILED = 'Не удалось авторизоваться в ЕАТ'
CAPTCHA = 'ЕАТ требует CAPTCHA. Пройдите её в открытом окне браузера.'
MANUAL = 'ЕАТ требует ручного подтверждения. Выполните его в открытом окне браузера.'


class BrowserAuthError(RuntimeError): pass


class PlaywrightLoginUI:
    def __init__(self, page): self.page=page;self.guard=None

    def open_login(self):
        self.guard=install_auth_guard(self.page.context)
        self.page.goto(ENTRY_URL,wait_until='domcontentloaded',timeout=60_000)

    def finish(self):
        if self.guard is not None:
            self.page.context.unroute('**/*',self.guard)
            self.guard=None

    def _visible(self, locator):
        return any(locator.nth(i).is_visible() for i in range(locator.count()))

    def signed_in(self):
        if urlsplit(self.page.url).hostname!='agregatoreat.ru': return False
        logout=self.page.get_by_role('button',name=re.compile(r'^(Выйти|Выход)$',re.I)).or_(
            self.page.get_by_role('link',name=re.compile(r'^(Выйти|Выход)$',re.I)))
        if logout.count()>0: return True
        # Меню выхода может рендериться только после открытия профиля. Проверяем
        # лишь наличие признаков auth storage, никогда не читая и не выводя значения.
        try:
            cookie_names=[str(item.get('name','')).casefold() for item in self.page.context.cookies()]
            if any(re.search(r'auth|identity|session|idsrv',name) for name in cookie_names): return True
            storage_names=self.page.evaluate("""() => [
                ...Object.keys(localStorage), ...Object.keys(sessionStorage)
            ]""")
            return any(re.search(r'auth|token|identity|session',str(name),re.I) for name in storage_names)
        except Exception:
            return False

    def state(self):
        if not trusted(self.page.url): return 'unsupported'
        text=self.page.locator('body').inner_text(timeout=3000)
        if restricted(text): return 'restricted'
        if self.signed_in(): return 'signed_in'
        if self._visible(self.page.locator('iframe[src*="captcha"], .g-recaptcha, .h-captcha')) or re.search(r'вы не робот|пройдите.{0,20}captcha|введите.{0,20}код с картинки',text,re.I): return 'captcha'
        if re.search(r'введите.{0,30}(?:код из|одноразовый код|код подтверждения)|подтвердите вход|подтверждение входа',text,re.I): return 'manual'
        if re.search(r'неверн\w*.{0,30}(?:пароль|логин)|неправильн\w*.{0,30}пароль',text,re.I): return 'failed'
        if self._visible(self.page.locator('input[type="password"]')): return 'form'
        return 'loading'

    def navigate_login_link(self):
        controls=self.page.get_by_role('link',name=re.compile(r'^(Войти|Вход|Личный кабинет)$',re.I)).or_(
            self.page.get_by_role('button',name=re.compile(r'^(Войти|Вход|Личный кабинет)$',re.I)))
        visible=[controls.nth(i) for i in range(controls.count()) if controls.nth(i).is_visible()]
        if len(visible)!=1: return False
        href=visible[0].get_attribute('href')
        if href and not trusted(urljoin(self.page.url,href)): raise BrowserAuthError(FAILED)
        visible[0].click(timeout=10000)
        return True

    def submit(self, credentials):
        # Only one ordinary password form, never ECP/consent/registration buttons.
        if not trusted(self.page.url): raise BrowserAuthError(FAILED)
        forms=self.page.locator('form').filter(has=self.page.locator('input[type="password"]'))
        visible=[forms.nth(i) for i in range(forms.count()) if forms.nth(i).is_visible()]
        if len(visible)!=1: raise BrowserAuthError('Не удалось однозначно определить штатную форму входа ЕАТ')
        form=visible[0]
        action=urljoin(self.page.url,form.get_attribute('action') or self.page.url)
        if not trusted(action) or restricted(action): raise BrowserAuthError(FAILED)
        method=(form.get_attribute('method') or '').lower()
        # HTML GET forms could place the password in a URL. Fail closed.
        if method!='post': raise BrowserAuthError('Штатная POST-форма входа ЕАТ не подтверждена')
        if self._visible(form.locator('input[type="checkbox"]:required')):
            raise BrowserAuthError('Согласия автоматически не принимаются')
        user=form.locator('input[autocomplete="username"], input[type="email"], input[name="Username"], input[name="username"], input[name="login"], input[name="Login"]')
        password=form.locator('input[type="password"]')
        button=form.get_by_role('button',name=re.compile(r'^(Войти|Вход|Войти в систему)$',re.I))
        if user.count()!=1 or password.count()!=1 or button.count()!=1: raise BrowserAuthError('Не удалось однозначно определить поля входа ЕАТ')
        user.fill(credentials.login,timeout=10000)
        password.fill(credentials.password,timeout=10000)
        button.click(timeout=15000)

    def pause(self): self.page.wait_for_timeout(500)


class BatchBrowserAuth:
    def __init__(self, page=None, *, ui=None, credentials_provider=None, notify=print, clock=time.monotonic, timeout=45):
        self.ui=ui if ui is not None else PlaywrightLoginUI(page)
        self.credentials_provider=credentials_provider or get_eat_credentials
        self.notify=notify;self.clock=clock;self.timeout=timeout
        self.authenticated=False;self.initial_attempted=False;self.reauth_used=False

    def authenticate(self, *, expired=False):
        if self.authenticated and not expired: return
        if expired:
            if self.reauth_used: raise BrowserAuthError('Сессия ЕАТ истекла; повторный вход уже использован')
            self.reauth_used=True
        elif self.initial_attempted: raise BrowserAuthError(FAILED)
        self.initial_attempted=True;self.authenticated=False
        try:
            self.ui.open_login()
            submitted=False;submissions=0;navigated=False;announced=None;deadline=self.clock()+self.timeout
            while True:
                state=self.ui.state()
                if state=='signed_in':
                    self.authenticated=True
                    return
                if state in ('failed','unsupported','restricted'): raise BrowserAuthError(FAILED)
                if state in ('captcha','manual'):
                    if announced!=state: self.notify(CAPTCHA if state=='captcha' else MANUAL)
                    announced=state
                    # The browser stays open; manual confirmation has no automatic deadline.
                    deadline=self.clock()+self.timeout
                    self.ui.pause();continue
                if announced is not None:
                    # Не повторяем отправку логина циклически в одном запуске.
                    if state=='form' and submissions<1: submitted=False
                    announced=None;deadline=self.clock()+self.timeout
                if state=='form' and not submitted:
                    credentials=self.credentials_provider()
                    if credentials.status!='ready' or credentials.credentials is None:
                        raise BrowserAuthError('Учётные данные ЕАТ не настроены' if credentials.status=='not_configured' else FAILED)
                    try: self.ui.submit(credentials.credentials)
                    finally: credentials=None
                    submitted=True;submissions+=1;deadline=self.clock()+self.timeout
                elif state=='loading' and not submitted and not navigated:
                    navigated=self.ui.navigate_login_link()
                if self.clock()>deadline: raise BrowserAuthError(FAILED)
                self.ui.pause()
        except BrowserAuthError:
            raise
        except Exception:
            raise BrowserAuthError(FAILED) from None
        finally:
            try: self.ui.finish()
            except Exception: pass  # Closed browser; do not expose Playwright diagnostics.

    def fetch(self, request, *, refresh):
        """Retry one page once after one session renewal per batch run."""
        response=request()
        if not response_requires_auth(response): return response
        self.authenticate(expired=True)
        refresh()  # Recapture current bearer/CSRF headers after login.
        response=request()
        if response_requires_auth(response): raise BrowserAuthError(FAILED)
        return response


def response_requires_auth(response):
    if response.status in (401,403): return True
    location=response.headers.get('location','')
    if response.status in (301,302,303,307,308) and location:
        target=urlsplit(urljoin(response.url,location))
        if target.hostname=='login.agregatoreat.ru' or '/account/login' in target.path.lower(): return True
    url=urlsplit(response.url)
    if url.hostname=='login.agregatoreat.ru' or '/account/login' in url.path.lower(): return True
    if 'text/html' in response.headers.get('content-type','').lower():
        text=response.text()
        return bool(re.search(r'type=["\']password|/account/login',text,re.I))
    return False


def install_auth_guard(context):
    """Never send a password form/POST to a third-party origin."""
    def guard(route):
        request=route.request
        if request.method not in ('GET','HEAD','OPTIONS') and not trusted(request.url):
            route.abort();return
        if restricted(request.url): route.abort();return
        route.continue_()
    context.route('**/*',guard)
    return guard


def run_login_check():
    """Explicit manual command, login only: no purchase API capture or pagination."""
    from playwright.sync_api import sync_playwright
    from eat.browser_policy import open_authorized_eat_browser
    with sync_playwright() as pw:
        session=open_authorized_eat_browser(pw)
        try:
            print('Авторизация ЕАТ подтверждена сохранённой сессией')
            return 0
        except BrowserAuthError as exc:
            print(str(exc));return 1
        finally:
            session.close()


if __name__=='__main__':
    raise SystemExit(run_login_check())
