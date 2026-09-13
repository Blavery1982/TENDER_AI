import io
import logging
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs
from eat.client import EatClient
from eat.auth_client import Response, trusted, login_form
from security.eat_credentials import CredentialResult, EatCredentials

URL='https://agregatoreat.ru/purchases/announcement/31caee8a-cca2-4e2b-b773-42229d413d03/info'
LOGIN='https://login.agregatoreat.ru/Account/Login'
FORM=b'<form method="post" action="/Account/Login"><input type="hidden" name="csrf" value="mock-token"><input name="Username"><input type="password" name="Password"></form>'


def response(status=200,body=b'public',url=URL): return Response(status,url,body)


class EatAuthTests(unittest.TestCase):
    def setUp(self):
        self.transport=Mock();self.credentials=Mock(return_value=CredentialResult('ready','ok',EatCredentials('mock-login','mock-password')))
        self.output=io.StringIO();self.logger=logging.Logger('auth-test');self.logger.addHandler(logging.StreamHandler(self.output))
        self.client=EatClient(transport=self.transport,credentials_provider=self.credentials,logger=self.logger)
    def login(self): return response(401,FORM,LOGIN)

    def test_public_no_keychain(self):
        self.transport.request.return_value=response()
        self.assertEqual(self.client.get(URL).status,'ok');self.credentials.assert_not_called()

    def test_401_login_retry_once(self):
        self.transport.request.side_effect=[self.login(),response(),response()]
        self.assertEqual(self.client.get(URL).status,'ok')
        self.credentials.assert_called_once()
        self.assertEqual([c.args[0] for c in self.transport.request.call_args_list],['GET','POST','GET'])
        fields=parse_qs(self.transport.request.call_args_list[1].args[2].decode())
        self.assertEqual(fields['Password'],['mock-password']);self.assertEqual(fields['csrf'],['mock-token'])

    def test_session_reused(self):
        self.transport.request.side_effect=[self.login(),response(),response(),response()]
        self.client.get(URL);self.client.get(URL)
        self.credentials.assert_called_once();self.assertEqual(self.transport.request.call_count,4)

    def test_wrong_credentials(self):
        self.transport.request.side_effect=[self.login(),self.login()]
        self.assertEqual(self.client.get(URL).message,'Не удалось авторизоваться в ЕАТ')
        self.assertEqual(self.transport.request.call_count,2)

    def test_not_configured(self):
        self.credentials.return_value=CredentialResult('not_configured','missing')
        self.transport.request.return_value=self.login()
        self.assertEqual(self.client.get(URL).status,'not_configured')
        self.assertEqual(self.transport.request.call_count,1)

    def test_captcha_before_secrets(self):
        self.transport.request.return_value=response(403,b'captcha')
        self.assertEqual(self.client.get(URL).status,'manual_required');self.credentials.assert_not_called()

    def test_sms_after_login(self):
        self.transport.request.side_effect=[self.login(),response(body='Введите код из СМС'.encode())]
        self.assertEqual(self.client.get(URL).message,'Требуется ручное подтверждение входа в ЕАТ')
        self.assertEqual(self.transport.request.call_count,2)

    def test_no_repeated_login_after_failure(self):
        self.transport.request.return_value=self.login()
        for _ in range(3): self.client.get(URL)
        self.credentials.assert_called_once();self.assertEqual(self.transport.request.call_count,4)

    def test_retry_401_no_loop(self):
        self.transport.request.side_effect=[self.login(),response(),self.login()]
        self.assertEqual(self.client.get(URL).status,'auth_failed');self.assertEqual(self.transport.request.call_count,3)

    def test_continue_public_after_failure(self):
        self.transport.request.side_effect=[self.login(),self.login(),response()]
        self.client.get(URL);self.assertEqual(self.client.get(URL).status,'ok')

    def test_closed_skipped(self):
        self.assertEqual(self.client.get(URL,closed=True).status,'restricted')
        self.transport.request.assert_not_called();self.credentials.assert_not_called()

    def test_confidentiality_never_signed(self):
        self.transport.request.return_value=response(body='Соглашение о конфиденциальности'.encode())
        self.assertEqual(self.client.get(URL).status,'restricted');self.credentials.assert_not_called()

    def test_unknown_auth_flow_not_guessed(self):
        self.transport.request.return_value=response(401)
        self.assertEqual(self.client.get(URL).status,'manual_required');self.credentials.assert_not_called()

    def test_external_form_not_submitted(self):
        self.transport.request.return_value=response(401,FORM.replace(b'/Account/Login',b'https://evil.example/login'),LOGIN)
        self.assertEqual(self.client.get(URL).status,'manual_required');self.credentials.assert_not_called()

    def test_logs_and_repr_no_secrets(self):
        self.transport.request.side_effect=[self.login(),RuntimeError('mock-password mock-login mock-token Cookie: secret')]
        result=self.client.get(URL)
        for secret in ('mock-password','mock-login','mock-token','Cookie'):
            self.assertNotIn(secret,self.output.getvalue());self.assertNotIn(secret,repr(result))
        self.assertNotIn('mock-token',repr(self.login()))

    def test_redirect_login_response(self):
        self.transport.request.side_effect=[response(200,FORM,LOGIN),response(),response()]
        self.assertEqual(self.client.get(URL).status,'ok');self.credentials.assert_called_once()

    def test_context_clears_session(self):
        with self.client: pass
        self.transport.close.assert_called_once()

    def test_expired_session_no_login_loop(self):
        self.transport.request.side_effect=[self.login(),response(),response(),self.login()]
        self.client.get(URL);self.assertEqual(self.client.get(URL).status,'auth_failed');self.credentials.assert_called_once()

    def test_no_consent_checkbox(self):
        f=FORM.replace(b'</form>',b'<input type="checkbox" name="agree"></form>')
        self.assertIsNone(login_form(response(401,f,LOGIN)))

    def test_trust_boundaries(self):
        for url in ('http://agregatoreat.ru','https://agregatoreat.ru.evil.example','https://u:p@agregatoreat.ru','https://agregatoreat.ru:bad'):
            self.assertFalse(trusted(url))

class MemoryTransportTests(unittest.TestCase):
    def fake(self,code,url,body=b'ok',headers=None):
        from unittest.mock import MagicMock
        r=MagicMock();r.code=code;r.geturl.return_value=url;r.read.return_value=body;r.headers=headers or {}
        r.__enter__.return_value=r
        return r

    def test_external_redirect_blocked_before_request(self):
        from eat.auth_client import MemoryTransport
        t=MemoryTransport();t.opener=Mock()
        t.opener.open.return_value=self.fake(302,URL,headers={'Location':'https://evil.example/'})
        with self.assertRaises(ValueError):t.request('GET',URL)
        self.assertEqual(t.opener.open.call_count,1)

    def test_post_never_replayed_on_307(self):
        from eat.auth_client import MemoryTransport
        t=MemoryTransport();t.opener=Mock()
        t.opener.open.return_value=self.fake(307,LOGIN,headers={'Location':'/another'})
        self.assertEqual(t.request('POST',LOGIN,b'mock').status,307)
        self.assertEqual(t.opener.open.call_count,1)

    def test_login_redirect_followed_without_post_data(self):
        from eat.auth_client import MemoryTransport
        t=MemoryTransport();t.opener=Mock()
        t.opener.open.side_effect=[self.fake(302,LOGIN,headers={'Location':URL}),self.fake(200,URL)]
        t.request('POST',LOGIN,b'mock')
        retry=t.opener.open.call_args.args[0]
        self.assertEqual(retry.get_method(),'GET');self.assertIsNone(retry.data)

    def test_cookiejar_is_memory_only(self):
        from eat.auth_client import MemoryTransport
        from http.cookiejar import FileCookieJar
        t=MemoryTransport()
        self.assertNotIsInstance(t.cookies,FileCookieJar)
        t.close()
