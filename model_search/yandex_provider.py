"""Synchronous Yandex Web Search v2 adapter; no business-logic changes.

API calls require explicit enablement. No retries, redirect following, cookies,
SDK background calls, environment credentials, or logging of response error bodies.
"""
from __future__ import annotations
import base64
import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request,build_opener,HTTPRedirectHandler
from security.yandex_credentials import get_yandex_credentials
from model_search.live_discovery import WebProvider

ENDPOINT='https://searchapi.api.cloud.yandex.net/v2/web/search'


class SearchAPIError(RuntimeError):pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def post_json(payload,api_key):
    request=Request(ENDPOINT,data=json.dumps(payload,ensure_ascii=False).encode(),method='POST',
        headers={'Authorization':'Api-Key '+api_key,'Content-Type':'application/json','Accept':'application/json'})
    try:
        with build_opener(NoRedirect()).open(request,timeout=45) as response:
            raw=response.read(8_000_001)
            if len(raw)>8_000_000:raise SearchAPIError('Ответ Search API превышает лимит размера')
            return json.loads(raw)
    except HTTPError as exc:
        code=exc.code;exc.close()
        raise SearchAPIError(f'Yandex Search API: HTTP {code}; повторы отключены') from None
    except Exception:
        raise SearchAPIError('Yandex Search API: запрос не выполнен или ответ некорректен; повторы отключены') from None
    finally:request=None;api_key=None


def parse_response(envelope,limit):
    if not isinstance(envelope,dict):raise SearchAPIError('Некорректный формат ответа Search API')
    raw=envelope.get('rawData')
    if raw is None:return []
    try:
        decoded=base64.b64decode(raw,validate=True)
        if b'<!DOCTYPE' in decoded.upper() or b'<!ENTITY' in decoded.upper():raise ValueError()
        root=ET.fromstring(decoded)
    except Exception:raise SearchAPIError('Не удалось декодировать XML Search API') from None
    if root.find('.//error') is not None:raise SearchAPIError('Search API вернул ошибку поиска')
    rows=[];seen=set()
    for doc in root.findall('.//doc'):
        url=(doc.findtext('url') or '').strip()
        if urlsplit(url).scheme not in ('https','http') or not urlsplit(url).hostname or url in seen:continue
        title=doc.find('title')
        rows.append({'url':url,'title':''.join(title.itertext()) if title is not None else '',
            'snippet':' '.join(''.join(p.itertext()) for p in doc.findall('.//passage')),
            'discovery_provider':'yandex_search_api','snippet_is_evidence':False})
        seen.add(url)
        if len(rows)>=limit:break
    return rows


class YandexSearchProvider:
    def __init__(self,*,allow_paid=False,credentials_provider=None,sender=None,fetcher=None,
                 max_requests=3,clock=time.monotonic,sleep=time.sleep):
        if not 1<=max_requests<=3:raise ValueError('Лимит контрольного теста: 1–3 запроса')
        self.allow_paid=allow_paid;self.credentials_provider=credentials_provider or get_yandex_credentials
        self.sender=sender or post_json;self.fetcher=fetcher or WebProvider(retries=0)
        self.max_requests=max_requests;self.api_calls=0;self.search_log=[];self.clock=clock;self.sleep=sleep
        self.last_call=None;self.failed=False;self._lock=threading.Lock()

    def search(self,query,limit=6):
        with self._lock:return self._search(query,limit)

    def _search(self,query,limit):
        if not self.allow_paid:raise SearchAPIError('Платные запросы не разрешены')
        if self.failed:raise SearchAPIError('Провайдер остановлен после ошибки; повторов нет')
        if self.api_calls>=self.max_requests:raise SearchAPIError('Лимит запросов исчерпан')
        if not isinstance(query,str) or not query.strip() or len(query)>400 or len(query.split())>40:
            raise SearchAPIError('Запрос должен содержать не более 400 символов и 40 слов')
        limit=max(1,min(int(limit),100))
        credentials=self.credentials_provider()
        if credentials.status!='ready' or credentials.credentials is None:
            self.failed=True
            raise SearchAPIError('Yandex Search API: credentials не настроены или недоступны')
        payload={'query':{'searchType':'SEARCH_TYPE_RU','queryText':query,'page':'0',
            'familyMode':'FAMILY_MODE_MODERATE','fixTypoMode':'FIX_TYPO_MODE_OFF'},
            'sortSpec':{'sortMode':'SORT_MODE_BY_RELEVANCE'},
            'groupSpec':{'groupMode':'GROUP_MODE_FLAT','groupsOnPage':str(limit),'docsInGroup':'1'},
            'maxPassages':'3','region':'225','l10n':'LOCALIZATION_RU',
            'folderId':credentials.credentials.folder_id,'responseFormat':'FORMAT_XML'}
        entry={'query':query,'requested_results':limit,'results_count':0,'status':'started'}
        self.search_log.append(entry)
        if self.last_call is not None:self.sleep(max(0,.2-(self.clock()-self.last_call)))
        self.last_call=self.clock();self.api_calls+=1
        try:
            response=self.sender(payload,credentials.credentials.api_key)
            results=parse_response(response,limit)
            entry.update(status='completed',results_count=len(results),results=results)
            return results
        except Exception:
            self.failed=True;entry['status']='failed'
            raise SearchAPIError('Yandex Search API: поиск не выполнен; провайдер остановлен без повторов') from None
        finally:credentials=None;payload=None

    def fetch(self,url):
        # API authorization is never passed to manufacturer/shop requests.
        return self.fetcher.fetch(url)
