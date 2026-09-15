"""Offline only. All tokens are artificial; Keychain and HTTP are mocked."""
import base64
import contextlib
import ctypes as C
import io
import json
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch
from security.yandex_credentials import (YandexKeychain,YandexCredentials,CredentialsResult,get_yandex_credentials,main,SERVICE,ACCOUNT)
from model_search.yandex_provider import YandexSearchProvider,SearchAPIError,parse_response,NoRedirect,post_json,ENDPOINT
from model_search.yandex_control import preview,run_control

XML='<yandexsearch><response><results><grouping><group><doc><url>https://shop.ru/model</url><title>Принтер <hlword>M283fdn</hlword></title><passages><passage>Цвет: белый</passage></passages></doc></group></grouping></results></response></yandexsearch>'

def envelope(xml=XML):return {'rawData':base64.b64encode(xml.encode()).decode()}
def credentials():return CredentialsResult('ready','ok',YandexCredentials('mock-folder','mock-key-not-a-real-secret'))


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.sender=Mock(return_value=envelope());self.keys=Mock(side_effect=credentials);self.fetcher=Mock()
        self.p=YandexSearchProvider(allow_paid=True,sender=self.sender,credentials_provider=self.keys,fetcher=self.fetcher,sleep=Mock())

    def test_disabled_before_keychain(self):
        self.p.allow_paid=False
        with self.assertRaises(SearchAPIError):self.p.search('кондиционер')
        self.keys.assert_not_called();self.sender.assert_not_called()

    def test_request_settings(self):
        self.p.search('кондиционер',6)
        payload,key=self.sender.call_args.args
        self.assertEqual(payload['query']['searchType'],'SEARCH_TYPE_RU')
        self.assertEqual(payload['query']['fixTypoMode'],'FIX_TYPO_MODE_OFF')
        self.assertEqual(payload['region'],'225');self.assertEqual(payload['responseFormat'],'FORMAT_XML')
        self.assertEqual(payload['folderId'],'mock-folder');self.assertEqual(payload['groupSpec']['groupsOnPage'],'6')

    def test_xml_highlight_and_snippet(self):
        row=self.p.search('принтер')[0]
        self.assertEqual(row['title'],'Принтер M283fdn');self.assertEqual(row['snippet'],'Цвет: белый')
        self.assertFalse(row['snippet_is_evidence'])

    def test_three_call_ceiling(self):
        for _ in range(3):self.p.search('кондиционер')
        with self.assertRaises(SearchAPIError):self.p.search('кондиционер')
        self.assertEqual(self.sender.call_count,3)

    def test_deep_search_can_use_eight_explicitly_configured_queries(self):
        provider = YandexSearchProvider(allow_paid=True, max_requests=8,
                                        sender=self.sender, credentials_provider=self.keys,
                                        fetcher=self.fetcher, sleep=Mock())
        for _ in range(8):
            provider.search('Pantum M6607NW')
        self.assertEqual(self.sender.call_count, 8)

    def test_no_retry_after_error(self):
        self.sender.side_effect=RuntimeError('mock-key-not-a-real-secret')
        for _ in range(2):
            with self.assertRaises(SearchAPIError) as exc:self.p.search('кондиционер')
            self.assertNotIn('mock-key',str(exc.exception))
        self.sender.assert_called_once()

    def test_missing_credentials_no_http(self):
        self.keys.side_effect=None;self.keys.return_value=CredentialsResult('not_configured','missing')
        with self.assertRaises(SearchAPIError):self.p.search('кондиционер')
        self.sender.assert_not_called()

    def test_query_limits_before_keychain(self):
        for q in ('','a'*401,'word '*41):
            with self.assertRaises(SearchAPIError):self.p.search(q)
        self.keys.assert_not_called()

    def test_fetch_never_receives_key(self):
        self.p.fetch('https://shop.ru/model')
        self.fetcher.fetch.assert_called_once_with('https://shop.ru/model');self.keys.assert_not_called()

    def test_report_has_no_key_or_folder(self):
        self.p.search('кондиционер')
        report=json.dumps(self.p.search_log)
        self.assertNotIn('mock-key',report);self.assertNotIn('mock-folder',report)

    def test_malformed_xml(self):
        for e in ({'rawData':'??'},envelope('<bad'),envelope('<!DOCTYPE x><x/>'),envelope('<response><error code="15">secret</error></response>')):
            with self.assertRaises(SearchAPIError):parse_response(e,6)

    def test_empty_response(self):self.assertEqual(parse_response({},6),[])

    def test_redirect_never_followed(self):self.assertIsNone(NoRedirect().redirect_request(None,None,None,None,None,None))

    def test_transport_uses_official_endpoint(self):
        response=Mock();response.read.return_value=json.dumps(envelope()).encode()
        opener=Mock();opener.open.return_value.__enter__=Mock(return_value=response)
        opener.open.return_value.__exit__=Mock(return_value=False)
        with patch('model_search.yandex_provider.build_opener',return_value=opener):post_json({'query':{}},'mock-key')
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,ENDPOINT);self.assertEqual(request.get_method(),'POST')
        self.assertEqual(request.get_header('Authorization'),'Api-Key mock-key')
        self.assertIsNone(request.get_header('Cookie'))


class CredentialTests(unittest.TestCase):
    def test_roundtrip(self):
        folder=b'mock-folder';key=b'mock-key'
        r=get_yandex_credentials(keychain=Mock(read=Mock(return_value=struct.pack('>I',len(folder))+folder+key)))
        self.assertEqual(r.credentials.api_key,'mock-key')
        self.assertNotIn('mock-key',repr(r));self.assertNotIn('mock-key',repr(r.credentials))

    def test_missing_and_invalid(self):
        self.assertEqual(get_yandex_credentials(keychain=Mock(read=Mock(return_value=None))).status,'not_configured')
        self.assertEqual(get_yandex_credentials(keychain=Mock(read=Mock(return_value=b'bad'))).status,'invalid')

    def test_setup_hidden_input_and_status_no_read(self):
        backend=Mock();output=io.StringIO()
        with patch('sys.stdin.isatty',return_value=True),patch('builtins.input',return_value='mock-folder'),patch('getpass.getpass',return_value='mock-key') as password,contextlib.redirect_stdout(output):
            self.assertEqual(main(['setup'],keychain=backend),0)
            self.assertEqual(main(['status'],keychain=backend),0)
        password.assert_called_once();backend.write.assert_called_once();backend.read.assert_not_called()
        self.assertNotIn('mock-key',output.getvalue())

    def test_no_hidden_input_fallback(self):
        import getpass
        backend=Mock()
        with patch('sys.stdin.isatty',return_value=True),patch('builtins.input',return_value='mock-folder'),patch('getpass.getpass',side_effect=getpass.GetPassWarning),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['setup'],keychain=backend),1)
        backend.write.assert_not_called()

    def test_native_service_isolated_from_eat(self):
        backend=object.__new__(YandexKeychain);backend.api=Mock()
        backend.api.SecKeychainFindGenericPassword.return_value=-25300
        self.assertEqual(backend._find(),(None,None))
        args=backend.api.SecKeychainFindGenericPassword.call_args.args
        self.assertEqual(args[2],SERVICE.encode());self.assertEqual(args[4],ACCOUNT.encode())

    def test_header_injection_rejected(self):
        folder=b'mock-folder';key=b'mock\r\nCookie:bad'
        r=get_yandex_credentials(keychain=Mock(read=Mock(return_value=struct.pack('>I',len(folder))+folder+key)))
        self.assertEqual(r.status,'invalid')

    def test_delete(self):
        backend=Mock()
        with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(main(['delete'],keychain=backend),0)
        backend.delete.assert_called_once();backend.read.assert_not_called()


class ControlTests(unittest.TestCase):
    def test_preview_no_credentials(self):
        with patch('security.yandex_credentials.YandexKeychain',side_effect=AssertionError('No Keychain')):
            p=preview()
        self.assertEqual(p['requirements_count'],23);self.assertEqual(len(p['query_plan']),3)
        self.assertNotIn('RC-TWN28HN',' '.join(p['query_plan']))

    def test_control_requires_approval(self):
        with self.assertRaises(ValueError):run_control()

    def test_mock_control_report(self):
        p=YandexSearchProvider(allow_paid=True,sender=Mock(return_value=envelope()),credentials_provider=credentials,
                              fetcher=Mock(fetch=Mock(return_value='')),sleep=Mock())
        with tempfile.TemporaryDirectory() as d:
            path,r=run_control(allow_paid=True,provider=p,output_dir=d)
            saved=path.read_text()
        self.assertEqual(r['api_calls'],3)
        self.assertLessEqual(r['result']['candidates_found'],8)
        self.assertEqual(len(r['result']['candidates'][0]['requirements_check']),23)
        self.assertIsNone(r['result']['selected_model'])
        self.assertNotIn('mock-key',saved);self.assertNotIn('mock-folder',saved)
