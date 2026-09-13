"""Bounded read-only EAT access, with lazy Keychain login and memory-only cookies.

No endpoint is guessed: login uses a POST form returned by EAT, including its
hidden fields. Unsupported/interactive login flows require manual confirmation.
Do not attach HTTP debug handlers or persist response bodies/cookie jars.
"""
from __future__ import annotations
import http.cookiejar
import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, build_opener, HTTPCookieProcessor, HTTPRedirectHandler
from security.eat_credentials import get_eat_credentials

FAILED = 'Не удалось авторизоваться в ЕАТ'
MANUAL = 'Требуется ручное подтверждение входа в ЕАТ'
CLOSED = 'Закрытый доступ или соглашение: автоматическое открытие запрещено'
HOSTS = {'agregatoreat.ru', 'login.agregatoreat.ru'}


def trusted(url):
    try:
        p=urlsplit(url)
        return p.scheme=='https' and p.hostname in HOSTS and p.port in (None,443) and not p.username and not p.password
    except ValueError:
        return False


def restricted(text):
    return bool(re.search(r'соглашени\w*\s+о\s+конфиденциальности|закрытая закупка|подписать соглашение|non.disclosure',text,re.I))


def challenge(text):
    return bool(re.search(r'captcha|капч|вы не робот|код из смс|код из sms|одноразов\w* код|электронн\w* подпис|подтвердите вход|one.time.code|two.factor',text,re.I))


@dataclass(repr=False)
class Response:
    status: int
    url: str
    body: bytes = field(repr=False)
    headers: dict = field(default_factory=dict,repr=False)

    def __repr__(self): return f'<EatResponse status={self.status}>'

    @property
    def text(self): return self.body[:2_000_000].decode('utf-8','replace')


@dataclass
class AccessResult:
    status: str
    message: str
    response: Response | None = field(default=None,repr=False)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


class MemoryTransport:
    def __init__(self):
        self.cookies=http.cookiejar.CookieJar()
        self.opener=build_opener(HTTPCookieProcessor(self.cookies),_NoRedirect())

    def request(self, method, url, data=None):
        # Redirects are checked before following; credentials never go off-site.
        for _ in range(6):
            if not trusted(url) or restricted(url): raise ValueError('Forbidden destination')
            req=Request(url,data=data,method=method,headers={'User-Agent':'TENDER_AI/1.0',
                **({'Content-Type':'application/x-www-form-urlencoded'} if data is not None else {})})
            try: response=self.opener.open(req,timeout=20)
            except HTTPError as exc: response=exc
            with response:
                result=Response(response.code,response.geturl(),response.read(2_000_000),dict(response.headers))
            location=result.headers.get('Location') or result.headers.get('location')
            if result.status in (301,302,303,307,308) and location:
                # Never replay a password POST on 307/308 or another endpoint.
                if method=='POST' and result.status in (307,308): return result
                url=urljoin(url,location);method='GET';data=None
                continue
            return result
        raise ValueError('Redirect limit')

    def close(self): self.cookies.clear()


class LoginForm(HTMLParser):
    def __init__(self):
        super().__init__();self.forms=[];self.current=None
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='form': self.current={'action':a.get('action',''),'method':a.get('method','get').lower(),'inputs':[]}
        elif tag=='input' and self.current is not None: self.current['inputs'].append(a)
    def handle_endtag(self,tag):
        if tag=='form' and self.current is not None:
            self.forms.append(self.current);self.current=None


def login_form(response):
    parser=LoginForm();parser.feed(response.text)
    forms=[f for f in parser.forms if any(i.get('type','').lower()=='password' for i in f['inputs'])]
    if len(forms)!=1: return None
    form=forms[0]
    if any(i.get('type','').lower() in ('checkbox','radio') for i in form['inputs']): return None
    passwords=[i for i in form['inputs'] if i.get('type','').lower()=='password' and i.get('name')]
    users=[i for i in form['inputs'] if i.get('type','text').lower() in ('text','email') and i.get('name')]
    action=urljoin(response.url,form['action'])
    if form['method']!='post' or len(passwords)!=1 or len(users)!=1 or not trusted(action) or restricted(action): return None
    return action,form,users[0]['name'],passwords[0]['name']


def needs_login(response):
    p=urlsplit(response.url)
    return (response.status in (401,403) or p.hostname=='login.agregatoreat.ru'
            or '/account/login' in p.path.casefold() or login_form(response) is not None
            or bool(re.search(r'"(?:error|code)"\s*:\s*"(?:unauthorized|login_required)"',response.text,re.I)))


class EatClient:
    """Reuse one instance per run. One login attempt per instance, no secret logs."""
    def __init__(self, *, transport=None, credentials_provider=None, logger=None):
        self.transport=transport if transport is not None else MemoryTransport()
        self.credentials_provider=credentials_provider or get_eat_credentials
        self.logger=logger or logging.getLogger('tender_ai.eat.auth')
        self.login_attempted=False;self.auth_failure=None

    def _result(self,status,message,response=None):
        # Only fixed status identifiers are logged, never HTTP bodies, URLs,
        # exceptions, account names, form values, cookies or tokens.
        self.logger.info('EAT access status=%s',status)
        return AccessResult(status,message,response)

    def get(self,url, *, closed=False):
        if closed or not trusted(url) or restricted(url): return self._result('restricted',CLOSED)
        try:
            response=self.transport.request('GET',url)
            if restricted(response.text): return self._result('restricted',CLOSED)
            if challenge(response.text): return self._result('manual_required',MANUAL)
            if not needs_login(response):
                return self._result('ok' if 200<=response.status<300 else 'http_error',
                                    'Запрос выполнен' if 200<=response.status<300 else 'Ошибка доступа к ЕАТ',response)
            if self.login_attempted:
                return self._result(*(self.auth_failure or ('auth_failed',FAILED)))
            self.login_attempted=True
            form=login_form(response)
            if form is None:
                # A bare 401/403 is not a verified password-login endpoint.
                self.auth_failure=('manual_required',MANUAL)
                return self._result(*self.auth_failure)
            credentials=self.credentials_provider()
            if credentials.status!='ready' or credentials.credentials is None:
                self.auth_failure=('not_configured','Учётные данные ЕАТ не настроены') if credentials.status=='not_configured' else ('auth_failed',FAILED)
                return self._result(*self.auth_failure)
            action,parsed,user_key,password_key=form
            fields={i['name']:i.get('value','') for i in parsed['inputs'] if i.get('type','').lower()=='hidden' and i.get('name')}
            fields[user_key]=credentials.credentials.login;fields[password_key]=credentials.credentials.password
            try:
                payload=urlencode(fields).encode()
                logged=self.transport.request('POST',action,payload)
            finally:
                fields.clear();credentials=None;payload=None
            if restricted(logged.text): self.auth_failure=('restricted',CLOSED)
            elif challenge(logged.text): self.auth_failure=('manual_required',MANUAL)
            elif needs_login(logged) or not 200<=logged.status<300: self.auth_failure=('auth_failed',FAILED)
            if self.auth_failure: return self._result(*self.auth_failure)
            response=self.transport.request('GET',url)  # Exactly one retry.
            if restricted(response.text): return self._result('restricted',CLOSED)
            if challenge(response.text):
                self.auth_failure=('manual_required',MANUAL);return self._result(*self.auth_failure)
            if needs_login(response):
                self.auth_failure=('auth_failed',FAILED);return self._result(*self.auth_failure)
            return self._result('ok' if 200<=response.status<300 else 'http_error','Запрос выполнен' if 200<=response.status<300 else 'Ошибка доступа к ЕАТ',response)
        except Exception:
            if self.login_attempted:
                self.auth_failure=('auth_failed',FAILED);return self._result(*self.auth_failure)
            return self._result('unavailable','Не удалось выполнить запрос к ЕАТ')

    def close(self): self.transport.close()
    def __enter__(self): return self
    def __exit__(self,*args): self.close()
