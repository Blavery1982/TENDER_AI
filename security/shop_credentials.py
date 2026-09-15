"""Учётные данные разрешённых магазинов в отдельных записях macOS Keychain."""
from __future__ import annotations

import argparse
import ctypes as C
import getpass
import struct
import sys
import warnings
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from security.eat_credentials import MacOSKeychain

SERVICE = 'TENDER_AI_SHOPS'
# Добавлять сайты только после разрешения владельца; это список доменов, не паролей.
SITES = {'onlinetrade.ru': 'ОНЛАЙН ТРЕЙД'}


def approved_site(value: str) -> str:
    """Не выдаём секрет по HTTP, чужому домену, порту или URL с userinfo."""
    url = urlsplit(value if '://' in value else 'https://' + value)
    host = (url.hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    if (url.scheme != 'https' or url.username is not None or url.password is not None
            or url.port not in (None, 443) or host not in SITES):
        raise ValueError('Сайт не разрешён для использования учётных данных')
    return host


class ShopKeychain(MacOSKeychain):
    def __init__(self, site: str):
        self.site = approved_site(site)
        super().__init__()

    def _find(self, read=False):
        service, account = SERVICE.encode(), self.site.encode()
        length, data, item = C.c_uint32(), C.c_void_p(), C.c_void_p()
        status = self.api.SecKeychainFindGenericPassword(
            None, len(service), service, len(account), account,
            C.byref(length) if read else None, C.byref(data) if read else None, C.byref(item))
        if status == -25300:
            return None, None
        self._check(status)
        try:
            payload = C.string_at(data, length.value) if read else None
        finally:
            if data:
                C.memset(data, 0, length.value)
                self.api.SecKeychainItemFreeContent(None, data)
        return item, payload

    def write(self, payload):
        def operation():
            item, _ = self._find()
            buffer = C.create_string_buffer(payload)
            service, account = SERVICE.encode(), self.site.encode()
            try:
                if item:
                    status = self.api.SecKeychainItemModifyAttributesAndData(item, None, len(payload), buffer)
                else:
                    status = self.api.SecKeychainAddGenericPassword(
                        None, len(service), service, len(account), account, len(payload), buffer, None)
                self._check(status)
            finally:
                C.memset(buffer, 0, C.sizeof(buffer))
                if item:
                    self.cf.CFRelease(item)
        self._run(operation)


@dataclass(frozen=True, repr=False)
class ShopCredentials:
    login: str
    password: str

    def __repr__(self):
        return '<ShopCredentials: hidden>'


@dataclass(frozen=True)
class CredentialsResult:
    status: str
    site: str | None
    credentials: ShopCredentials | None = field(default=None, repr=False)


def valid(login, password):
    return (isinstance(login, str) and isinstance(password, str) and
            0 < len(login) <= 1024 and 0 < len(password) <= 4096 and
            not any(ord(c) < 32 or ord(c) == 127 for c in login + password))


def get_shop_credentials(url: str, *, keychain=None) -> CredentialsResult:
    """Получать непосредственно перед входом; не кэшировать и не сериализовать."""
    try:
        site = approved_site(url)
    except (TypeError, ValueError, AttributeError):
        return CredentialsResult('unsupported_site', None)
    try:
        payload = (keychain if keychain is not None else ShopKeychain(site)).read()
        if payload is None:
            return CredentialsResult('not_configured', site)
        if not isinstance(payload, bytes) or not 4 < len(payload) <= 24580:
            raise ValueError()
        size = struct.unpack('>I', payload[:4])[0]
        if not 0 < size < len(payload) - 4:
            raise ValueError()
        login, password = payload[4:4+size].decode(), payload[4+size:].decode()
        if not valid(login, password):
            raise ValueError()
        return CredentialsResult('ready', site, ShopCredentials(login, password))
    except (ValueError, UnicodeError, TypeError):
        return CredentialsResult('invalid', site)
    except Exception:
        return CredentialsResult('unavailable', site)


def main(argv=None, *, keychain_factory=ShopKeychain):
    parser = argparse.ArgumentParser(description='Скрытые учётные данные магазинов в macOS Keychain')
    parser.add_argument('command', choices=('list', 'status', 'setup'))
    parser.add_argument('site', nargs='?')
    args = parser.parse_args(argv)
    if args.command != 'list' and not args.site:
        parser.error('Для status/setup укажите сайт, например onlinetrade.ru')
    try:
        sites = list(SITES) if args.command == 'list' else [approved_site(args.site)]
        if args.command in ('list', 'status'):
            code = 0
            for site in sites:
                try:
                    exists = keychain_factory(site).exists()
                    status = 'настроено' if exists else 'не настроено'
                except Exception:
                    status, code = 'Keychain недоступен', 1
                print(f'{site} — {status}; логин и пароль скрыты')
            return code
        if not sys.stdin.isatty():
            print('Запустите setup вручную в локальном интерактивном терминале')
            return 1
        login = password = None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                login = getpass.getpass('Логин (скрытый ввод): ').strip()
                password = getpass.getpass('Пароль (скрытый ввод): ')
            if not valid(login, password):
                print('Пустые, слишком длинные данные или управляющие символы не допускаются')
                return 1
            encoded = login.encode()
            keychain_factory(sites[0]).write(struct.pack('>I', len(encoded)) + encoded + password.encode())
        finally:
            login = password = None
        print(f'{sites[0]} — учётные данные сохранены в macOS Keychain')
        return 0
    except (KeyboardInterrupt, EOFError):
        print('\nНастройка отменена')
    except getpass.GetPassWarning:
        print('Скрытый ввод недоступен; используйте локальный терминал')
    except Exception:
        print('Операция не выполнена: сайт не разрешён или Keychain недоступен')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
