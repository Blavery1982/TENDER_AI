import contextlib
import ctypes as C
import getpass
import io
import struct
import unittest
from unittest.mock import Mock, patch

from security.eat_credentials import (EatCredentials, CredentialResult, MacOSKeychain,
                                      get_eat_credentials, main)


class CredentialsTests(unittest.TestCase):
    def setUp(self):
        self.backend = Mock()
        self.login = 'mock-user'
        self.secret = 'mock-secret-never-real'
        self.backend.read.return_value = struct.pack('>I',len(self.login.encode())) + self.login.encode() + self.secret.encode()

    def command(self, command, **kwargs):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch('sys.stdin.isatty', return_value=True), \
             patch('builtins.input', return_value=self.login), \
             patch('getpass.getpass', **({'return_value': self.secret} | kwargs)):
            code = main([command], keychain=self.backend)
        self.assertNotIn(self.secret, out.getvalue())
        return code, out.getvalue()

    def test_read(self):
        r=get_eat_credentials(keychain=self.backend)
        self.assertEqual(r.status,'ready')
        self.assertEqual((r.credentials.login,r.credentials.password),(self.login,self.secret))

    def test_missing(self):
        self.backend.read.return_value=None
        r=get_eat_credentials(keychain=self.backend)
        self.assertEqual(r.status,'not_configured')
        self.assertEqual(r.message,'Учётные данные ЕАТ не настроены')

    def test_error_sanitized(self):
        self.backend.read.side_effect=RuntimeError(self.secret)
        r=get_eat_credentials(keychain=self.backend)
        self.assertEqual(r.status,'unavailable');self.assertNotIn(self.secret,repr(r))

    def test_corrupt(self):
        for value in (b'bad', b'[]', b'{}', b'{"login":1,"password":"x"}', b'\xff'):
            with self.subTest(value=value):
                self.backend.read.return_value=value
                self.assertEqual(get_eat_credentials(keychain=self.backend).status,'invalid')

    def test_repr_redacted(self):
        c=EatCredentials(self.login,self.secret)
        for value in (c,CredentialResult('ready','ok',c)):
            self.assertNotIn(self.secret,repr(value));self.assertNotIn(self.login,repr(value))

    def test_setup(self):
        self.assertEqual(self.command('setup')[0],0)
        self.backend.read.return_value=self.backend.write.call_args.args[0]
        saved=get_eat_credentials(keychain=self.backend).credentials
        self.assertEqual((saved.login,saved.password),(self.login,self.secret))

    def test_status_does_not_read_secret(self):
        self.backend.exists.return_value=True
        self.assertEqual(self.command('status')[0],0)
        self.backend.read.assert_not_called()

    def test_status_missing(self):
        self.backend.exists.return_value=False
        self.assertIn('не настроены',self.command('status')[1])

    def test_delete(self):
        self.backend.delete.return_value=True
        self.assertIn('удалены',self.command('delete')[1])
        self.backend.read.assert_not_called()

    def test_delete_missing(self):
        self.backend.delete.return_value=False
        self.assertIn('не настроены',self.command('delete')[1])

    def test_setup_error_never_exposes_secret(self):
        self.backend.write.side_effect=RuntimeError(self.secret)
        self.assertEqual(self.command('setup')[0],1)

    def test_hidden_input_failure(self):
        self.assertEqual(self.command('setup',side_effect=getpass.GetPassWarning())[0],1)
        self.backend.write.assert_not_called()

    def test_cancel(self):
        self.assertEqual(self.command('setup',side_effect=KeyboardInterrupt())[0],1)
        self.backend.write.assert_not_called()

    def test_empty_password(self):
        self.assertEqual(self.command('setup',return_value='')[0],1)
        self.backend.write.assert_not_called()

    def test_noninteractive_setup(self):
        with patch('sys.stdin.isatty',return_value=False),patch('getpass.getpass') as prompt,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['setup'],keychain=self.backend),1)
        prompt.assert_not_called();self.backend.write.assert_not_called()

    def test_no_interactive_retrieval(self):
        with patch('builtins.input',side_effect=AssertionError),patch('getpass.getpass',side_effect=AssertionError):
            self.assertEqual(get_eat_credentials(keychain=self.backend).status,'ready')

    def test_unsupported_platform(self):
        with patch('sys.platform','linux'):
            self.assertEqual(get_eat_credentials().status,'unavailable')

    def native(self):
        native=object.__new__(MacOSKeychain)
        native.api=Mock();native.cf=Mock()
        native.api.SecKeychainSetUserInteractionAllowed.return_value=0
        def previous(pointer):
            C.cast(pointer,C.POINTER(C.c_ubyte))[0]=1
            return 0
        native.api.SecKeychainGetUserInteractionAllowed.side_effect=previous
        return native

    def test_native_interaction_restored_after_failure(self):
        native=self.native()
        with self.assertRaises(RuntimeError):
            native._run(Mock(side_effect=RuntimeError()))
        self.assertEqual([x.args[0] for x in native.api.SecKeychainSetUserInteractionAllowed.call_args_list],[0,1])

    def test_native_status_requests_no_password(self):
        native=self.native();native.api.SecKeychainFindGenericPassword.return_value=-25300
        self.assertFalse(native.exists())
        args=native.api.SecKeychainFindGenericPassword.call_args.args
        self.assertIsNone(args[5]);self.assertIsNone(args[6])
        self.assertEqual(args[2],b'TENDER_AI_EAT')

    def test_native_write_add_and_update(self):
        for existing in (None,C.c_void_p(123)):
            native=self.native();native._find=Mock(return_value=(existing,None))
            native.api.SecKeychainAddGenericPassword.return_value=0
            native.api.SecKeychainItemModifyAttributesAndData.return_value=0
            native.write(b'mock')
            if existing:
                native.api.SecKeychainItemModifyAttributesAndData.assert_called_once()
                native.cf.CFRelease.assert_called_once()
            else:
                native.api.SecKeychainAddGenericPassword.assert_called_once()


if __name__=='__main__': unittest.main()
