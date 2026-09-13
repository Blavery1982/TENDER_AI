"""One-position Yandex discovery check. Default command is offline plan only."""
from __future__ import annotations
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from documents.item_sources import resolve_item_sources
from model_search.live_discovery import discover_models, generate_queries
from model_search.yandex_provider import YandexSearchProvider, ENDPOINT

ROOT=Path(__file__).resolve().parent.parent
TRADE='100205573126100053'
CARD=ROOT/'data/eat_single_31caee8a-cca2-4e2b-b773-42229d413d03.json'
SAVED=ROOT/'data/production_dry_run/run_20260909_162735_772653.json'
OUTPUT=ROOT/'data/yandex_model_search_control'


def control_item():
    card=json.loads(CARD.read_text(encoding='utf-8'))
    saved=json.loads(SAVED.read_text(encoding='utf-8'))
    procurement=next(p for p in saved['procurements'] if str(p['procurement_number'])==TRADE)
    raw=card['raw']['lot']['lotItems']
    if len(raw)!=1:raise ValueError('Ожидалась ровно одна контрольная позиция')
    resolved=resolve_item_sources(raw[0],procurement['document_processing']['document_results'])
    if not resolved['requirements'] or resolved['model_search_mode']!='MODEL_DISCOVERY_REQUIRED':
        raise ValueError('Контрольные требования или режим изменились')
    if resolved.get('customer_required_model') or resolved.get('price_justification_model'):
        raise ValueError('Предварительная подстановка модели запрещена')
    return {**resolved,'item_name':raw[0]['name'],'procurement_number':TRADE,'item_number':1,
            'structured_requirements':resolved['requirements']}


def preview():
    item=control_item()
    return {'mode':'offline_plan','endpoint':ENDPOINT,'procurement_number':TRADE,
            'requirements_count':len(item['requirements']),'query_plan':generate_queries(item),
            'max_api_requests':3,'max_candidates':8,'paid_requests_performed':0}


def run_control(*,allow_paid=False,provider=None,output_dir=OUTPUT):
    if not allow_paid:raise ValueError('Требуется отдельное разрешение на платный тест')
    item=control_item()
    provider=provider if provider is not None else YandexSearchProvider(allow_paid=True,max_requests=3)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory=Path(output_dir)/stamp
    # Provider-specific cache is isolated from previous Bing and fixture results.
    result=discover_models(item,provider=provider,query_limit=3,results_per_query=6,
                           candidate_limit=8,cache_dir=directory/'cache',use_cache=False)
    report={'provider':'Yandex Search API v2','mode':'synchronous','procurement_number':TRADE,
            'requirements':item['requirements'],'api_calls':provider.api_calls,
            'searches':provider.search_log,'result':result,
            'supplier_search_performed':False,'calculator_performed':False,'google_sheets_written':False}
    directory.mkdir(parents=True,exist_ok=True)
    path=directory/'report.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return path,report


def main(argv=None):
    p=argparse.ArgumentParser(description='Одна контрольная позиция; по умолчанию только локальный план')
    p.add_argument('--allow-paid-live-test',action='store_true',help='только после отдельного разрешения пользователя')
    args=p.parse_args(argv)
    if not args.allow_paid_live_test:
        print(json.dumps(preview(),ensure_ascii=False,indent=2));return 0
    path,report=run_control(allow_paid=True)
    print(f"Отчёт: {path}; вызовов API: {report['api_calls']}")
    return 1 if report['api_calls']==0 or any(x['status']=='failed' for x in report['searches']) else 0


if __name__=='__main__':raise SystemExit(main())
