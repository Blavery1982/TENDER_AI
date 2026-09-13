"""Local EAT credentials in macOS Keychain. No network or pipeline prompts.

Python cannot guarantee erasure of immutable strings from process memory. Callers
must acquire credentials just before login and release references afterwards.
Never serialize credentials or pass them to logging, exceptions or checkpoints.
"""
from __future__ import annotations

import argparse
import ctypes as C
import getpass
import struct
import sys
import threading
import warnings
from dataclasses import dataclass, field
from typing import Protocol

SERVICE = 'TENDER_AI_EAT'
ACCOUNT = 'eat_credentials'
NOT_CONFIGURED = 'Учётные данные ЕАТ не настроены'
_LOCK = threading.RLock()


@dataclass(frozen=True, repr=False)
class EatCredentials:
    login: str
    password: str

    def __repr__(self) -> str:
        return '<EatCredentials: hidden>'


@dataclass(frozen=True)
class CredentialResult:
    status: str
    message: str
    credentials: EatCredentials | None = field(default=None, repr=False)


class KeychainError(Exception):
    """Sanitized error, never containing credential values."""


class Keychain(Protocol):
    def read(self) -> bytes | None: ...
    def exists(self) -> bool: ...
    def write(self, payload: bytes) -> None: ...
    def delete(self) -> bool: ...


class MacOSKeychain:
    """Security.framework API; secrets never enter argv, stdout or files.

    Interaction is disabled during each operation and restored afterwards. A
    locked/denied Keychain returns an error instead of prompting the pipeline.
    """
    def __init__(self):
        if sys.platform != 'darwin':
            raise KeychainError('macOS required')
        self.api = C.CDLL('/System/Library/Frameworks/Security.framework/Security')
        signatures = {
            'SecKeychainFindGenericPassword': [C.c_void_p, C.c_uint32, C.c_char_p, C.c_uint32, C.c_char_p, C.POINTER(C.c_uint32), C.POINTER(C.c_void_p), C.POINTER(C.c_void_p)],
            'SecKeychainAddGenericPassword': [C.c_void_p, C.c_uint32, C.c_char_p, C.c_uint32, C.c_char_p, C.c_uint32, C.c_void_p, C.POINTER(C.c_void_p)],
            'SecKeychainItemModifyAttributesAndData': [C.c_void_p, C.c_void_p, C.c_uint32, C.c_void_p],
            'SecKeychainItemFreeContent': [C.c_void_p, C.c_void_p],
            'SecKeychainItemDelete': [C.c_void_p],
            'SecKeychainGetUserInteractionAllowed': [C.POINTER(C.c_ubyte)],
            'SecKeychainSetUserInteractionAllowed': [C.c_ubyte],
        }
        for name, args in signatures.items():
            fn = getattr(self.api, name); fn.argtypes = args; fn.restype = C.c_int32
        self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.cf.CFRelease.argtypes = [C.c_void_p]; self.cf.CFRelease.restype = None

    @staticmethod
    def _check(status):
        if status != 0: raise KeychainError('Keychain operation unavailable')

    def _run(self, operation):
        with _LOCK:
            previous = C.c_ubyte()
            self._check(self.api.SecKeychainGetUserInteractionAllowed(C.byref(previous)))
            self._check(self.api.SecKeychainSetUserInteractionAllowed(0))
            try:
                return operation()
            finally:
                self._check(self.api.SecKeychainSetUserInteractionAllowed(previous.value))

    def _find(self, read=False):
        length = C.c_uint32(); data = C.c_void_p(); item = C.c_void_p()
        status = self.api.SecKeychainFindGenericPassword(
            None, len(SERVICE.encode()), SERVICE.encode(), len(ACCOUNT.encode()), ACCOUNT.encode(),
            C.byref(length) if read else None, C.byref(data) if read else None, C.byref(item))
        if status == -25300: return None, None
        self._check(status)
        try:
            payload = C.string_at(data, length.value) if read else None
        finally:
            if data:
                C.memset(data, 0, length.value)
                self.api.SecKeychainItemFreeContent(None, data)
        return item, payload

    def read(self):
        def operation():
            item, payload = self._find(read=True)
            if item: self.cf.CFRelease(item)
            return payload
        return self._run(operation)

    def exists(self):
        def operation():
            item, _ = self._find()
            if item: self.cf.CFRelease(item)
            return bool(item)
        return self._run(operation)

    def write(self, payload):
        def operation():
            item, _ = self._find()
            buffer = C.create_string_buffer(payload)
            try:
                if item:
                    status = self.api.SecKeychainItemModifyAttributesAndData(item, None, len(payload), buffer)
                else:
                    status = self.api.SecKeychainAddGenericPassword(None, len(SERVICE.encode()), SERVICE.encode(),
                        len(ACCOUNT.encode()), ACCOUNT.encode(), len(payload), buffer, None)
                self._check(status)
            finally:
                C.memset(buffer, 0, C.sizeof(buffer))
                if item: self.cf.CFRelease(item)
        self._run(operation)

    def delete(self):
        def operation():
            item, _ = self._find()
            if not item: return False
            try: self._check(self.api.SecKeychainItemDelete(item))
            finally: self.cf.CFRelease(item)
            return True
        return self._run(operation)


def get_eat_credentials(*, keychain: Keychain | None = None) -> CredentialResult:
    """Call immediately before authorization; never prompts or caches secrets."""
    try:
        payload = (keychain if keychain is not None else MacOSKeychain()).read()
        if payload is None: return CredentialResult('not_configured', NOT_CONFIGURED)
        if len(payload) < 4:
            raise ValueError('invalid record')
        size = struct.unpack('>I', payload[:4])[0]
        if not 0 < size < len(payload) - 4:
            raise ValueError('invalid record')
        login = payload[4:4+size].decode('utf-8')
        password = payload[4+size:].decode('utf-8')
        return CredentialResult('ready', 'Учётные данные ЕАТ получены', EatCredentials(login, password))
    except (ValueError, UnicodeError, TypeError):
        return CredentialResult('invalid', 'Запись ЕАТ повреждена; повторите setup')
    except Exception:
        return CredentialResult('unavailable', 'Keychain недоступен или доступ запрещён')


def main(argv=None, *, keychain: Keychain | None = None) -> int:
    parser = argparse.ArgumentParser(description='Учётные данные ЕАТ в macOS Keychain')
    parser.add_argument('command', choices=('setup', 'status', 'delete'))
    command = parser.parse_args(argv).command
    try:
        backend = keychain if keychain is not None else MacOSKeychain()
        if command == 'status':
            print('Учётные данные ЕАТ настроены' if backend.exists() else NOT_CONFIGURED)
        elif command == 'delete':
            print('Учётные данные ЕАТ удалены' if backend.delete() else NOT_CONFIGURED)
        else:
            if not sys.stdin.isatty():
                print('Запустите setup вручную в локальном интерактивном терминале')
                return 1
            login = input('Логин ЕАТ: ').strip()
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                password = getpass.getpass('Пароль ЕАТ: ')
            if not login or not password:
                print('Логин и пароль не должны быть пустыми')
                return 1
            try:
                encoded_login = login.encode('utf-8')
                backend.write(struct.pack('>I', len(encoded_login)) + encoded_login + password.encode('utf-8'))
            finally:
                del password, login
            print('Учётные данные ЕАТ сохранены в macOS Keychain')
        return 0
    except (KeyboardInterrupt, EOFError):
        print('\nНастройка отменена')
        return 1
    except getpass.GetPassWarning:
        print('Скрытый ввод недоступен; используйте локальный терминал')
        return 1
    except Exception:
        print('Операция не выполнена: Keychain недоступен или доступ запрещён')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
