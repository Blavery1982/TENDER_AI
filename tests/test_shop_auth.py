"""Проверки одной попытки входа; браузер и учётные данные фиктивные."""
import unittest
from unittest.mock import MagicMock, Mock

from model_search.shop_auth import authenticate_shop
from security.shop_credentials import CredentialsResult, ShopCredentials


class ShopAuthTests(unittest.TestCase):
    def setUp(self):
        self.page = MagicMock()
        self.page.url = 'https://www.onlinetrade.ru/login/'
        self.body, self.forms, self.form = MagicMock(), MagicMock(), MagicMock()
        self.user, self.password, self.button = MagicMock(), MagicMock(), MagicMock()
        self.widgets = MagicMock()
        self.widgets.count.return_value = 0
        self.body.inner_text.return_value = 'Вход'
        self.forms.filter.return_value = self.forms
        self.forms.count.return_value = 1
        self.forms.nth.return_value = self.form
        self.form.is_visible.return_value = True
        self.form.get_attribute.side_effect = lambda name: 'post' if name == 'method' else '/login/'
        self.checkbox = MagicMock()
        self.checkbox.count.return_value = 0
        self.form.locator.side_effect = lambda sel: self.password if sel == 'input[type="password"]' else (self.checkbox if 'checkbox' in sel else self.user)
        for field in (self.user, self.password, self.button):
            field.count.return_value = 1
        self.form.get_by_role.return_value = self.button
        self.page.locator.side_effect = lambda sel: self.body if sel == 'body' else (self.forms if sel == 'form' else self.widgets)
        self.logout = MagicMock()
        self.logout.or_.return_value = self.logout
        self.logout.count.return_value = 0
        self.page.get_by_role.return_value = self.logout
        self.credentials = Mock(return_value=CredentialsResult('ready', 'onlinetrade.ru', ShopCredentials('fake-user', 'fake-password')))

    def run_login(self):
        return authenticate_shop(self.page, credentials_provider=self.credentials)

    def test_captcha_stops_before_secret(self):
        self.body.inner_text.return_value = 'Разверните картинку правильно'
        self.assertEqual(self.run_login().status, 'manual_captcha')
        self.credentials.assert_not_called()
        self.button.click.assert_not_called()
        self.page.close.assert_not_called()
        self.page.context.unroute.assert_called_once()

    def test_visible_captcha_widget_stops(self):
        self.widgets.count.return_value = 1
        self.widgets.nth.return_value.is_visible.return_value = True
        self.assertEqual(self.run_login().status, 'manual_captcha')
        self.credentials.assert_not_called()

    def test_manual_code_stops(self):
        self.body.inner_text.return_value = 'Введите код подтверждения'
        self.assertEqual(self.run_login().status, 'manual_confirmation')
        self.credentials.assert_not_called()

    def test_foreign_domain_never_reads(self):
        self.page.url = 'https://onlinetrade.ru.evil.test/'
        self.assertEqual(self.run_login().status, 'unsupported_site')
        self.credentials.assert_not_called()
        self.page.context.route.assert_not_called()

    def test_get_form_and_foreign_action_never_read(self):
        for action, method in (('/login/', 'get'), ('https://evil.test/', 'post')):
            self.form.get_attribute.side_effect = lambda name: method if name == 'method' else action
            self.assertIn(self.run_login().status, ('unsafe_form', 'login_error'))
        self.credentials.assert_not_called()

    def test_ambiguous_form_and_required_consent_stop(self):
        self.forms.count.return_value = 2
        self.assertEqual(self.run_login().status, 'manual_login')
        self.forms.count.return_value = 1
        self.checkbox.count.return_value = 1
        self.assertEqual(self.run_login().status, 'manual_confirmation')
        self.credentials.assert_not_called()

    def test_one_submission_success_and_fields_cleared(self):
        self.button.click.side_effect = lambda **kw: setattr(self.logout.count, 'return_value', 1)
        self.logout.nth.return_value.is_visible.return_value = True
        self.assertEqual(self.run_login().status, 'signed_in')
        self.credentials.assert_called_once()
        self.button.click.assert_called_once()
        self.assertEqual([c.args[0] for c in self.password.fill.call_args_list], ['fake-password', ''])
        self.assertEqual([c.args[0] for c in self.user.fill.call_args_list], ['fake-user', ''])

    def test_post_submission_captcha_no_retry(self):
        self.button.click.side_effect = lambda **kw: setattr(self.body.inner_text, 'return_value', 'Пройдите проверку')
        self.assertEqual(self.run_login().status, 'manual_captcha')
        self.button.click.assert_called_once()

    def test_unconfirmed_submission_is_not_success(self):
        self.assertEqual(self.run_login().status, 'login_not_confirmed')
        self.button.click.assert_called_once()

    def test_secret_error_never_exposed_and_fields_cleared(self):
        self.password.fill.side_effect = [RuntimeError('fake-password'), None]
        result = self.run_login()
        self.assertEqual(result.status, 'login_error')
        self.assertNotIn('fake-password', repr(result))
        self.button.click.assert_not_called()
        self.page.context.unroute.assert_called_once()

    def test_missing_credentials_preserves_user_fields(self):
        self.credentials.return_value = CredentialsResult('not_configured', 'onlinetrade.ru')
        self.assertEqual(self.run_login().status, 'not_configured')
        self.user.fill.assert_not_called()
        self.password.fill.assert_not_called()

    def test_guard_blocks_other_domains(self):
        self.run_login()
        guard = self.page.context.route.call_args.args[1]
        for url, allowed in (('https://www.onlinetrade.ru/login/', True), ('http://onlinetrade.ru/', False), ('https://evil.test/', False)):
            route = Mock()
            route.request.url = url
            guard(route)
            (route.continue_ if allowed else route.abort).assert_called_once()
