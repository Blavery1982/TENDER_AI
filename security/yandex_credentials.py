"""Yandex Search API credentials in a separate macOS Keychain entry.

Reuse only the project's native Keychain transport; never read the EAT entry.
No credentials in argv, files, environment variables, repr or diagnostics.
"""
from __future__ import annotations
import argparse
import ctypes as C
import getpass
import re
import struct
import sys
import warnings
from dataclasses import dataclass, field
from security.eat_credentials import MacOSKeychain

SERVICE='TENDER_AI_YANDEX_SEARCH'
ACCOUNT='search_api'
NOT_CONFIGURED='Доступ к Yandex Search API не настроен'


class YandexKeychain(MacOSKeychain):
    def _find(self, read=False):
        length=C.c_uint32();data=C.c_void_p();item=C.c_void_p()
        status=self.api.SecKeychainFindGenericPassword(None,len(SERVICE),SERVICE.encode(),len(ACCOUNT),ACCOUNT.encode(),
            C.byref(length) if read else None,C.byref(data) if read else None,C.byref(item))
        if status==-25300:return None,None
        self._check(status)
        try:payload=C.string_at(data,length.value) if read else None
        finally:
            if data:
                C.memset(data,0,length.value);self.api.SecKeychainItemFreeContent(None,data)
        return item,payload

    def write(self,payload):
        def operation():
            item,_=self._find();buffer=C.create_string_buffer(payload)
            try:
                status=(self.api.SecKeychainItemModifyAttributesAndData(item,None,len(payload),buffer) if item else
                    self.api.SecKeychainAddGenericPassword(None,len(SERVICE),SERVICE.encode(),len(ACCOUNT),ACCOUNT.encode(),len(payload),buffer,None))
                self._check(status)
            finally:
                C.memset(buffer,0,C.sizeof(buffer))
                if item:self.cf.CFRelease(item)
        self._run(operation)


@dataclass(frozen=True,repr=False)
class YandexCredentials:
    folder_id:str
    api_key:str
    def __repr__(self):return '<YandexCredentials: hidden>'


@dataclass(frozen=True)
class CredentialsResult:
    status:str
    message:str
    credentials:YandexCredentials|None=field(default=None,repr=False)


def valid(folder_id,api_key):
    return bool(re.fullmatch(r'[A-Za-z0-9_-]{1,50}',folder_id) and api_key and len(api_key)<=4096
                and not any(c.isspace() or ord(c)<33 or ord(c)>126 for c in api_key))


def get_yandex_credentials(*,keychain=None):
    try:
        payload=(keychain if keychain is not None else YandexKeychain()).read()
        if payload is None:return CredentialsResult('not_configured',NOT_CONFIGURED)
        if len(payload)<4:raise ValueError()
        size=struct.unpack('>I',payload[:4])[0]
        if not 0<size<len(payload)-4:raise ValueError()
        folder=payload[4:4+size].decode();key=payload[4+size:].decode()
        if not valid(folder,key):raise ValueError()
        return CredentialsResult('ready','Доступ к Yandex Search API настроен',YandexCredentials(folder,key))
    except (ValueError,UnicodeError,TypeError):return CredentialsResult('invalid','Некорректная запись Yandex Search API; повторите setup')
    except Exception:return CredentialsResult('unavailable','Keychain недоступен или доступ запрещён')


def main(argv=None,*,keychain=None):
    parser=argparse.ArgumentParser(description='Локальная настройка Yandex Search API в Keychain')
    parser.add_argument('command',choices=('setup','status','delete'))
    command=parser.parse_args(argv).command
    try:
        backend=keychain if keychain is not None else YandexKeychain()
        if command=='status':print('Запись Yandex Search API существует' if backend.exists() else NOT_CONFIGURED)
        elif command=='delete':print('Запись Yandex Search API удалена' if backend.delete() else NOT_CONFIGURED)
        else:
            if not sys.stdin.isatty():
                print('Запустите setup вручную в локальном терминале');return 1
            folder=input('Yandex Cloud folder ID: ').strip()
            with warnings.catch_warnings():
                warnings.simplefilter('error',getpass.GetPassWarning)
                key=getpass.getpass('API key (скрытый ввод): ')
            try:
                if not valid(folder,key):print('Некорректный формат folder ID или API key');return 1
                encoded=folder.encode();backend.write(struct.pack('>I',len(encoded))+encoded+key.encode())
            finally:key=None;folder=None
            print('Данные сохранены в macOS Keychain: '+SERVICE)
        return 0
    except (KeyboardInterrupt,EOFError):print('\nНастройка отменена');return 1
    except getpass.GetPassWarning:print('Скрытый ввод недоступен; используйте локальный терминал');return 1
    except Exception:print('Операция Keychain не выполнена');return 1


if __name__=='__main__':raise SystemExit(main())
