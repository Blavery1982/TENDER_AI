import io
import unittest
from unittest.mock import Mock, patch
from security.eat_credentials import CredentialResult, EatCredentials
from eat.browser_auth import (BatchBrowserAuth, BrowserAuthError, CAPTCHA, MANUAL,
                              PlaywrightLoginUI, install_auth_guard, response_requires_auth)


class BrowserAuthTests(unittest.TestCase):
    def setUp(self):
        self.ui=Mock();self.ui.navigate_login_link.return_value=True
        self.credentials=Mock(return_value=CredentialResult('ready','ok',EatCredentials('mock-user','mock-secret')))
        self.messages=[]
        self.auth=BatchBrowserAuth(ui=self.ui,credentials_provider=self.credentials,notify=self.messages.append)

    def test_keychain_form_login(self):
        self.ui.state.side_effect=['form','signed_in']
        self.auth.authenticate()
        self.credentials.assert_called_once();self.ui.submit.assert_called_once()
        self.assertTrue(self.auth.authenticated);self.ui.finish.assert_called_once()

    def test_reuse_across_procurements(self):
        self.ui.state.side_effect=['form','signed_in']
        for _ in range(5):self.auth.authenticate()
        self.ui.open_login.assert_called_once();self.credentials.assert_called_once()

    def test_already_authenticated_no_keychain(self):
        self.ui.state.return_value='signed_in'
        self.auth.authenticate();self.credentials.assert_not_called()

    def test_hidden_logout_control_still_confirms_session(self):
        page=Mock(url='https://agregatoreat.ru/')
        logout=page.get_by_role.return_value.or_.return_value
        logout.count.return_value=1
        self.assertTrue(PlaywrightLoginUI(page).signed_in())

    def test_auth_cookie_name_confirms_without_exposing_value(self):
        page=Mock(url='https://agregatoreat.ru/')
        page.get_by_role.return_value.or_.return_value.count.return_value=0
        page.context.cookies.return_value=[{'name':'idsrv.session','value':'must-not-be-read'}]
        self.assertTrue(PlaywrightLoginUI(page).signed_in())

    def test_one_reauthentication(self):
        self.ui.state.side_effect=['form','signed_in','form','signed_in']
        self.auth.authenticate();self.auth.authenticate(expired=True)
        with self.assertRaises(BrowserAuthError):self.auth.authenticate(expired=True)
        self.assertEqual(self.credentials.call_count,2)

    def test_captcha_keeps_same_browser(self):
        self.ui.state.side_effect=['captcha','captcha','form','signed_in']
        self.auth.authenticate()
        self.assertEqual(self.messages,[CAPTCHA]);self.ui.open_login.assert_called_once()
        self.ui.submit.assert_called_once()

    def test_manual_after_submission(self):
        self.ui.state.side_effect=['form','manual','manual','signed_in']
        self.auth.authenticate()
        self.assertEqual(self.messages,[MANUAL]);self.ui.submit.assert_called_once()

    def test_captcha_does_not_resubmit_password(self):
        self.ui.state.side_effect=['form','captcha','form','signed_in']
        self.auth.authenticate();self.assertEqual(self.ui.submit.call_count,1)

    def test_bad_credentials_no_loop(self):
        self.ui.state.side_effect=['form','failed']
        with self.assertRaises(BrowserAuthError):self.auth.authenticate()
        with self.assertRaises(BrowserAuthError):self.auth.authenticate()
        self.credentials.assert_called_once()

    def test_missing_keychain(self):
        self.ui.state.return_value='form';self.credentials.return_value=CredentialResult('not_configured','missing')
        with self.assertRaisesRegex(BrowserAuthError,'не настроены'):self.auth.authenticate()
        self.ui.submit.assert_not_called()

    def test_error_contains_no_secret(self):
        self.ui.state.return_value='form';self.ui.submit.side_effect=RuntimeError('mock-secret')
        with self.assertRaises(BrowserAuthError) as caught:self.auth.authenticate()
        self.assertNotIn('mock-secret',str(caught.exception));self.assertEqual(self.messages,[])

    def test_confidentiality_blocks_login(self):
        self.ui.state.return_value='restricted'
        with self.assertRaises(BrowserAuthError):self.auth.authenticate()
        self.credentials.assert_not_called();self.ui.submit.assert_not_called()

    def test_login_navigation(self):
        self.ui.state.side_effect=['loading','form','signed_in']
        self.auth.authenticate();self.ui.navigate_login_link.assert_called_once()

    def test_expiry_refreshes_before_retry(self):
        self.ui.state.side_effect=['form','signed_in','form','signed_in']
        self.auth.authenticate()
        order=[]
        responses=[Mock(status=401),Mock(status=200,url='https://tender-api.agregatoreat.ru/api/TradeLot/list-published-trade-lots',headers={})]
        def request():order.append('request');return responses.pop(0)
        self.auth.fetch(request,refresh=lambda:order.append('refresh'))
        self.assertEqual(order,['request','refresh','request'])
        self.assertEqual(self.credentials.call_count,2)

    def test_no_infinite_request_retries(self):
        self.ui.state.side_effect=['signed_in','signed_in'];self.auth.authenticate()
        request=Mock(return_value=Mock(status=401))
        with self.assertRaises(BrowserAuthError):self.auth.fetch(request,refresh=Mock())
        self.assertEqual(request.call_count,2)

    def test_success_page_no_reauthentication(self):
        response=Mock(status=200,url='https://tender-api.agregatoreat.ru/api/TradeLot/list-published-trade-lots',headers={})
        self.auth.fetch(Mock(return_value=response),refresh=Mock());self.ui.open_login.assert_not_called()

    def test_timeout_requires_success_evidence(self):
        self.ui.state.return_value='loading';self.auth.clock=Mock(side_effect=[0,100])
        with self.assertRaises(BrowserAuthError):self.auth.authenticate()
        self.assertFalse(self.auth.authenticated)

    def test_guard_rejects_external_post(self):
        context=Mock();guard=install_auth_guard(context);route=Mock()
        route.request.method='POST';route.request.url='https://other.example/'
        guard(route);route.abort.assert_called_once();route.continue_.assert_not_called()

    def test_guard_allows_eat_form_post(self):
        guard=install_auth_guard(Mock());route=Mock()
        route.request.method='POST';route.request.url='https://login.agregatoreat.ru/Account/Login'
        guard(route);route.continue_.assert_called_once()

    def test_login_response_is_expired(self):
        self.assertTrue(response_requires_auth(Mock(status=200,url='https://login.agregatoreat.ru/Account/Login',headers={})))

    def test_main_batch_dispatch(self):
        import main
        with patch('sys.argv',['main.py','--batch-procurement-search']),patch('main.run_eat_live_collection_audit',return_value=0) as run:
            self.assertEqual(main.main(),0)
        run.assert_called_once()

    def test_login_check_dispatch_no_batch(self):
        import main
        with patch('sys.argv',['main.py','--eat-login-check']),patch('eat.browser_auth.run_login_check',return_value=0) as login,patch('main.run_eat_filter_test') as batch:
            self.assertEqual(main.main(),0)
        login.assert_called_once();batch.assert_not_called()

class BatchIntegrationTests(unittest.TestCase):
    def test_filter_uses_authenticated_context_without_saving_headers(self):
        from unittest.mock import MagicMock
        from eat.filter_pipeline import run_filter_test
        pw=MagicMock();browser=pw.chromium.launch.return_value
        context=browser.new_context.return_value;page=context.new_page.return_value
        api='https://tender-api.agregatoreat.ru/api/TradeLot/list-published-trade-lots'
        payload={'items':[{'id':'mock-id','subject':'Поставка бумаги','tradeNumber':'1','price':200000,'lotItems':[]}]}
        resp=Mock(status=200,url=api,headers={'content-type':'application/json'},ok=True)
        resp.json.return_value=payload
        resp.request=Mock(method='POST',headers={'Authorization':'mock-token'},post_data='present',post_data_json={'page':1,'size':10})
        def navigate(url,**kwargs):
            page.on.call_args.args[1](resp)
        page.goto.side_effect=navigate
        context.request.fetch.return_value=resp
        auth=Mock();auth.fetch.side_effect=lambda request,refresh:request()
        session=Mock(context=context,page=page)
        output=io.StringIO()
        with patch('eat.filter_pipeline.sync_playwright') as factory,patch('eat.filter_pipeline.open_authorized_eat_browser',return_value=session),patch('eat.filter_pipeline.BatchBrowserAuth',return_value=auth),patch('eat.filter_pipeline.save_eat_session') as persist,patch('eat.filter_pipeline._save') as save,patch('sys.stdout',output):
            factory.return_value.__enter__.return_value=pw
            run_filter_test(limit=1,pause_seconds=0)
        auth.authenticate.assert_called_once()
        persist.assert_called_once_with(context)
        auth.fetch.assert_called()
        context.request.fetch.assert_called_once()
        self.assertEqual(context.request.fetch.call_args.kwargs['headers']['Authorization'],'mock-token')
        self.assertNotIn('mock-token',repr(save.call_args_list))
        self.assertNotIn('mock-token',output.getvalue())
        self.assertTrue(all('/announcement/' not in c.args[0] for c in page.goto.call_args_list))
        session.close.assert_called_once()

class PasswordFormTests(unittest.TestCase):
    def setup_form(self,method='post',action='/Account/Login'):
        page=Mock(url='https://login.agregatoreat.ru/Account/Login')
        form=Mock();form.is_visible.return_value=True
        forms=page.locator.return_value.filter.return_value
        forms.count.return_value=1;forms.nth.return_value=form
        form.get_attribute.side_effect=lambda key:{'action':action,'method':method}.get(key)
        fields={}
        def field(selector):
            if selector not in fields:
                fields[selector]=Mock()
                fields[selector].count.return_value=0 if 'checkbox' in selector else 1
            return fields[selector]
        form.locator.side_effect=field
        form.get_by_role.return_value.count.return_value=1
        return PlaywrightLoginUI(page),form,fields

    def test_actual_ui_fills_password_form_only(self):
        ui,form,fields=self.setup_form()
        ui.submit(EatCredentials('mock-user','mock-secret'))
        fields['input[type="password"]'].fill.assert_called_once_with('mock-secret',timeout=10000)
        form.get_by_role.return_value.click.assert_called_once()

    def test_get_form_never_receives_credentials(self):
        ui,form,fields=self.setup_form(method='get')
        with self.assertRaises(BrowserAuthError):ui.submit(EatCredentials('mock-user','mock-secret'))
        self.assertEqual(fields,{})

    def test_external_action_never_receives_credentials(self):
        ui,form,fields=self.setup_form(action='https://other.example/login')
        with self.assertRaises(BrowserAuthError):ui.submit(EatCredentials('mock-user','mock-secret'))
        self.assertEqual(fields,{})
