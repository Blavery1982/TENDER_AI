"""CORE-first production flow for MODEL_DISCOVERY_REQUIRED positions."""
from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any, Callable

from model_search.live_discovery import discover_models
from model_search.live_price_search import search_exact_model_prices
from model_search.candidate_admission import (CONFIRMED_COMPLIANT, LIKELY_COMPLIANT,
                                               classify_candidate)
from model_search.price_readiness import MODEL_DISCOVERY_REQUIRED, classify_price_search_readiness
from model_search.yandex_control import CARD, control_item
from model_search.yandex_provider import YandexSearchProvider
from suppliers.price_search_flow import build_price_search_result, confirmed_price_ranking
from suppliers.verification import verify_supplier
from calculator.result_decision import attach_business_decision

ROOT=Path(__file__).resolve().parent.parent
OUTPUT=ROOT/'data/second_queue_control_test.json'
CACHE=ROOT/'data/cache/second_queue_control'


def _model_name(candidate: dict[str,Any]) -> str:
    return str(candidate.get('exact_model') or candidate.get('model_name') or candidate.get('sku') or '').strip()


def _rejected(candidates: list[dict[str,Any]]) -> list[dict[str,Any]]:
    result=[]
    for candidate in candidates:
        mismatches=[row for row in candidate.get('requirements_check') or []
                    if row.get('result')=='does_not_comply']
        if mismatches or candidate.get('candidate_admission_status')=='REJECTED':
            result.append({'model':_model_name(candidate),
                           'reason':candidate.get('candidate_admission_reason') or
                                    'Не соответствует обязательному параметру',
                           'critical_unknown':candidate.get('critical_unknown') or [],
                           'mismatches':[{'requirement':row.get('requirement'),
                                          'required_value':row.get('required_value'),
                                          'found_value':row.get('found_value'),
                                          'source':row.get('source')} for row in mismatches]})
    return result


def _classify_models(candidates: list[dict[str,Any]],
                     requirements: list[dict[str,Any]]) -> list[dict[str,Any]]:
    return [{**row,**classify_candidate(row,requirements)} for row in candidates
            if _model_name(row)]


def _rough_signal(candidate: dict[str,Any], price_result: dict[str,Any]) -> dict[str,Any]:
    """Build a cheap market-comparison signal without supplier verification.

    A lone low card is deliberately penalised: it remains evidence, but cannot
    automatically beat a model supported by several available sellers.
    """
    ranked=confirmed_price_ranking(price_result.get('offers') or [])
    prices=[float(row['public_price']) for row in ranked]
    minimum=min(prices) if prices else None
    typical=statistics.median(prices[:3]) if prices else None
    count=len(ranked)
    quality='stable' if count>=2 else 'single_offer' if count==1 else 'not_confirmed'
    score=(float(typical)*(1.25 if count==1 else 1.0)) if typical is not None else None
    return {'model':_model_name(candidate),'cheapest_confirmed_public_price':minimum,
            'rough_market_price':typical,'rough_ranking_score':score,
            'confirmed_supplier_count':count,'market_signal_quality':quality,
            'candidate_admission_status':candidate.get('candidate_admission_status'),
            'availability_confirmed':bool(ranked),
            'source_urls':[row.get('product_url') for row in ranked if row.get('product_url')]}


def _rank_rough_signals(signals: list[dict[str,Any]]) -> list[dict[str,Any]]:
    return sorted(signals,key=lambda row:(row['rough_ranking_score'] is None,
                  row['rough_ranking_score'] or float('inf'),
                  row.get('candidate_admission_status')!=CONFIRMED_COMPLIANT,
                  -row['confirmed_supplier_count'],row['model']))


def run_second_queue_position(item: dict[str,Any], *, procurement_id: str,
                              discoverer: Callable[...,dict[str,Any]]=discover_models,
                              provider: Any=None,
                              rough_price_search: Callable[...,dict[str,Any]] | None=None,
                              price_search: Callable[...,dict[str,Any]]=search_exact_model_prices,
                              verifier: Callable[[dict[str,Any]],dict[str,Any]]=verify_supplier,
                              output_path: Path=OUTPUT, cache_dir: Path=CACHE) -> dict[str,Any]:
    readiness=classify_price_search_readiness({},item)
    requirements=item.get('structured_requirements') or item.get('requirements') or []
    if readiness['classification']!=MODEL_DISCOVERY_REQUIRED or not requirements:
        raise ValueError('Позиция не относится ко второй очереди')
    discovery=discoverer(item,provider=provider,query_limit=3,results_per_query=6,
                         candidate_limit=10,cache_dir=cache_dir,use_cache=False)
    candidates=discovery.get('candidates') or []
    classified=_classify_models(candidates,requirements)
    fully=[row for row in classified
           if row['candidate_admission_status']==CONFIRMED_COMPLIANT]
    likely=[row for row in classified
            if row['candidate_admission_status']==LIKELY_COMPLIANT]
    admitted=fully+likely
    # The default reuses the common exact-model market search as a price-only
    # signal.  No supplier verification runs for losing model candidates.
    rough_search=rough_price_search or price_search
    price_results={};signals=[]
    for number,candidate in enumerate(admitted,1):
        model=_model_name(candidate)
        path=ROOT/'data/second_queue_rough_prices'/(
            f"{procurement_id}_{item.get('item_number',1)}_{number}.json")
        try:
            price_results[model]=rough_search(model,output_path=path)
        except Exception as exc:
            price_results[model]={'offers':[],'candidate_discovery':{'candidate_count':0},
                                  'source_errors':[{'error':f'{type(exc).__name__}: {exc}'}]}
        signals.append(_rough_signal(candidate,price_results[model]))
    ranked_models=_rank_rough_signals(signals)
    selected_signal=next((row for row in ranked_models
                          if row['rough_ranking_score'] is not None),None)
    # With exactly one admitted exact SKU there is nothing to compare.  It may
    # proceed to the common Price Search even when the quick signal found no
    # price; the final result remains explicitly unconfirmed/review-required.
    if selected_signal is None and len(ranked_models)==1:
        selected_signal=ranked_models[0]
    selected_model=selected_signal['model'] if selected_signal else None
    selected_candidate=next((row for row in admitted
                             if selected_model and _model_name(row)==selected_model),None)
    selected_admission=(selected_candidate or {}).get('candidate_admission_status')
    procurement = item.get('procurement') if isinstance(item.get('procurement'), dict) else {}
    # После нормализации канонический fee закупки имеет приоритет. Если ключ
    # присутствует со значением None, это подтверждённое отсутствие данных и
    # старое поле позиции не должно его заменять.
    commission = (procurement['commission_fee'] if 'commission_fee' in procurement
                  else item.get('commission_fee'))
    commission_source = (procurement.get('commission_source')
                         if 'commission_fee' in procurement
                         else item.get('commission_source', 'raw.lot.commissionFee'))
    downstream=None;prices=None;ranked=[]
    if selected_model:
        path=ROOT/'data/second_queue_prices'/(
            f"{procurement_id}_{item.get('item_number',1)}.json")
        # If the rough stage used the common search function, reuse its fresh
        # result rather than repeating network work.  An injected lightweight
        # rough provider still hands the winner to the shared Queue-1 search.
        if rough_search is price_search:
            prices=price_results[selected_model]
        else:
            try:
                prices=price_search(selected_model,output_path=path)
            except Exception as exc:
                prices={'offers':[],'candidate_discovery':{'candidate_count':0},
                        'source_errors':[{'error':f'{type(exc).__name__}: {exc}'}]}
        ranked=confirmed_price_ranking(prices.get('offers') or [])
        position={'procurement_id':procurement_id,'item_number':item.get('item_number',1),
                  'item_name':item.get('item_name') or item.get('name'),'quantity':item.get('quantity'),
                  'customer_unit_price':item.get('customer_unit_price'),'exact_model':selected_model,
                  'model_source':'MODEL_DISCOVERY_FULLY_COMPLIANT',
                  'nmck':item.get('nmck') or procurement.get('nmck'),
                  'commission_fee':commission,
                  'commission_source':commission_source}
        downstream=build_price_search_result(position,prices,
                                              verifier=verifier,
                                              eat_commission=commission)
        downstream['procurement'] = {
            'nmck': position.get('nmck'), 'commission_fee': commission,
            'commission_source': commission_source}
        downstream['item'] = {'position_number': position.get('item_number', 1),
                              'quantity': position.get('quantity')}
        downstream['supplier_search'] = {
            'ranked_offers': downstream.get('ranked_offers') or [],
            'all_verified_offers': downstream.get('antifraud_history') or []}
        downstream['audit'] = {'additional_expense_state': {
            'ready': False, 'amount': None,
            'reason': 'Специальные расходы во второй очереди ещё не подтверждены'}}
        downstream['calculation_complete'] = False
        attach_business_decision(downstream)
    manual=bool(item.get('source_conflicts'))
    status=('MANUAL_MODEL_REVIEW_REQUIRED' if manual else
            'MODEL_NOT_CONFIRMED' if not admitted else
            'MODEL_PRICE_SIGNAL_NOT_CONFIRMED' if not selected_model else
            'MODEL_SELECTION_REQUIRES_REVIEW' if selected_admission==LIKELY_COMPLIANT else
            'SELECTED_MODEL_PRICE_SEARCH_COMPLETED')
    result={'procurement_id':procurement_id,'item_number':item.get('item_number',1),
            'item_name':item.get('item_name') or item.get('name'),'requirements':requirements,
            'discovery_queries':discovery.get('queries_used') or [],
            'yandex_api_calls':getattr(provider,'api_calls',discovery.get('live_queries_count',0)),
            'candidate_models':classified,'rejected_models':_rejected(classified),
            'fully_compliant_models':fully,'likely_compliant_models':likely,
            'rough_ranking_admitted_models':admitted,'rough_price_per_model':ranked_models,
            'selected_model':selected_model,
            'selected_model_admission_status':selected_admission,
            'selected_model_evidence':selected_candidate.get('requirements_check') if selected_candidate else [],
            'selection_reason':(('Единственная допущенная exact model передана в общий Price Search; '
                                 'rough market price не подтверждён'
                                 if selected_model and selected_signal.get('rough_ranking_score') is None else
                                 'Модель имеет лучший текущий rough price signal с учётом числа '
                                 'доступных предложений')
                                if selected_model else
                                ('Для полностью соответствующих моделей не подтверждён текущий '
                                 'ценовой ориентир' if fully else
                                 'Нет модели с подтверждением всех обязательных требований ТЗ')),
            'price_search_model':selected_model,
            'price_search_offer_count':len(ranked),
            'price_search_performed':bool(selected_model),
            'compliance_repeated_during_price_search':False,
            'downstream_price_search_result':downstream,'status':status,
            'ai_provider_used':False,'third_queue_classification_performed':False}
    result['procurement'] = {
        'nmck': item.get('nmck') or procurement.get('nmck'),
        'commission_fee': commission, 'commission_source': commission_source}
    result['item'] = {'position_number': item.get('item_number', 1),
                      'quantity': item.get('quantity')}
    selected_offers = (downstream or {}).get('ranked_offers') if downstream else []
    verified_offers = (downstream or {}).get('antifraud_history') if downstream else []
    result['supplier_search'] = {'ranked_offers': selected_offers or [],
                                 'all_verified_offers': verified_offers or []}
    result['audit'] = {'additional_expense_state': {
        'ready': False, 'amount': None,
        'reason': 'Специальные расходы во второй очереди ещё не подтверждены'}}
    result['calculation_complete'] = False
    attach_business_decision(result)
    output_path.parent.mkdir(parents=True,exist_ok=True)
    output_path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def control_position() -> tuple[str,dict[str,Any]]:
    item=control_item()
    card=json.loads(CARD.read_text(encoding='utf-8'));raw=card.get('raw') or card
    lot=raw.get('lot') or raw;raw_item=(lot.get('lotItems') or [{}])[0]
    quantity=raw_item.get('quantity');unit_price=raw_item.get('unitPrice')
    if unit_price is None and quantity not in (None,0) and raw_item.get('sum') is not None:
        unit_price=float(raw_item['sum'])/float(quantity)
    item.update(quantity=quantity,customer_unit_price=unit_price,item_number=1)
    return str(raw.get('id') or lot.get('id') or card.get('purchase_id')),item


def run_control_live() -> dict[str,Any]:
    procurement_id,item=control_position()
    provider=YandexSearchProvider(allow_paid=True,max_requests=3)
    return run_second_queue_position(item,procurement_id=procurement_id,provider=provider)


if __name__=='__main__':
    print(json.dumps(run_control_live(),ensure_ascii=False,indent=2))
