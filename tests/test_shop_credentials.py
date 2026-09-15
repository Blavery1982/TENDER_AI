"""Проверки скрытого хранилища; реальные секреты и Keychain не используются."""
import contextlib
import ctypes as C
import getpass
import io
import struct
import unittest
from unittest.mock import Mock, patch

from security.shop_credentials import (CredentialsResult, ShopCredentials,
    ShopKeychain, approved_site, get_shop_credentials, main)


class ShopCredentialsTests(unittest.TestCase):
    def setUp(self):
        self.backend = Mock()
        self.payload = struct.pack('>I', 9) + b'mock-user' + b'fake-secret'
        self.backend.read.return_value = self.payload

    def test_domains_are_exact_and_https_only(self):
        for url in ('onlinetrade.ru', 'https://www.onlinetrade.ru/account/', 'https://onlinetrade.ru:443/'):
            self.assertEqual(approved_site(url), 'onlinetrade.ru')
        for url in ('http://onlinetrade.ru', 'https://onlinetrade.ru.evil.test',
                    'https://evil.test/onlinetrade.ru', 'https://onlinetrade.ru@evil.test',
                    'https://evil@onlinetrade.ru', 'https://onlinetrade.ru:8443',
                    'https://login.onlinetrade.ru', 'https://nix.ru'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                approved_site(url)

    def test_unsupported_site_never_reads_keychain(self):
        result = get_shop_credentials('https://evil.test', keychain=self.backend)
        self.assertEqual(result.status, 'unsupported_site')
        self.backend.read.assert_not_called()

    def test_read_and_redacted_repr(self):
        result = get_shop_credentials('https://www.onlinetrade.ru', keychain=self.backend)
        self.assertEqual(result.status, 'ready')
        self.assertEqual(result.credentials.password, 'fake-secret')
        for obj in (result, result.credentials):
            self.assertNotIn('fake-secret', repr(obj))
            self.assertNotIn('mock-user', repr(obj))

    def test_missing_invalid_and_sanitized_error(self):
        self.backend.read.return_value = None
        self.assertEqual(get_shop_credentials('onlinetrade.ru', keychain=self.backend).status, 'not_configured')
        for payload in (b'bad', b'\xff'*8, self.payload + b'\n', struct.pack('>I', 0)+b'userpw'):
            self.backend.read.return_value = payload
            self.assertEqual(get_shop_credentials('onlinetrade.ru', keychain=self.backend).status, 'invalid')
        self.backend.read.side_effect = RuntimeError('fake-secret')
        result = get_shop_credentials('onlinetrade.ru', keychain=self.backend)
        self.assertEqual(result.status, 'unavailable')
        self.assertNotIn('fake-secret', repr(result))

    def command(self, args, **kwargs):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch('sys.stdin.isatty', return_value=True), \
             patch('getpass.getpass', **({'side_effect': ['mock-user', 'fake-secret']} | kwargs)):
            code = main(args, keychain_factory=lambda site: self.backend)
        self.assertNotIn('fake-secret', output.getvalue())
        self.assertNotIn('mock-user', output.getvalue())
        return code, output.getvalue()

    def test_hidden_setup_and_roundtrip(self):
        self.assertEqual(self.command(['setup', 'onlinetrade.ru'])[0], 0)
        self.backend.write.assert_called_once_with(self.payload)

    def test_list_and_status_never_read_secrets(self):
        self.assertEqual(self.command(['list'])[0], 0)
        self.assertEqual(self.command(['status', 'onlinetrade.ru'])[0], 0)
        self.backend.read.assert_not_called()
        self.backend.write.assert_not_called()

    def test_setup_failure_or_hidden_input_failure_never_writes(self):
        for error in (getpass.GetPassWarning(), KeyboardInterrupt(), EOFError()):
            self.assertEqual(self.command(['setup', 'onlinetrade.ru'], side_effect=error)[0], 1)
        self.backend.write.assert_not_called()

    def test_noninteractive_setup_never_prompts(self):
        with patch('sys.stdin.isatty', return_value=False), patch('getpass.getpass') as prompt, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['setup', 'onlinetrade.ru'], keychain_factory=lambda site: self.backend), 1)
        prompt.assert_not_called()
        self.backend.write.assert_not_called()

    def test_native_record_is_separate_and_status_does_not_read(self):
        native = object.__new__(ShopKeychain)
        native.site = 'onlinetrade.ru'
        native.api = Mock()
        native.api.SecKeychainFindGenericPassword.return_value = -25300
        self.assertEqual(native._find(), (None, None))
        args = native.api.SecKeychainFindGenericPassword.call_args.args
        self.assertEqual(args[2], b'TENDER_AI_SHOPS')
        self.assertEqual(args[4], b'onlinetrade.ru')
        self.assertIsNone(args[5])
        self.assertIsNone(args[6])

    def test_native_write_uses_shop_service(self):
        native = object.__new__(ShopKeychain)
        native.site = 'onlinetrade.ru'
        native.api, native.cf = Mock(), Mock()
        native._run = lambda f: f()
        native._find = Mock(return_value=(None, None))
        native.api.SecKeychainAddGenericPassword.return_value = 0
        native.write(self.payload)
        args = native.api.SecKeychainAddGenericPassword.call_args.args
        self.assertEqual(args[2], b'TENDER_AI_SHOPS')
        self.assertEqual(args[4], b'onlinetrade.ru')
