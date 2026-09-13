"""CORE-first admission of Queue-2 model candidates to rough price ranking."""
from __future__ import annotations

import re
from typing import Any

from model_search.compliance import CORRESPONDS, MISMATCH, parameter_key

CONFIRMED_COMPLIANT='CONFIRMED_COMPLIANT'
LIKELY_COMPLIANT='LIKELY_COMPLIANT'
REJECTED='REJECTED'
NOT_ENOUGH_EVIDENCE='NOT_ENOUGH_EVIDENCE'

_CRITICAL_KEYS={
    'product_type','inverter','mounting','outdoor_unit','cooling_capacity',
    'heating_capacity','room_area','functions','цвет','material','volume',
    'dimensions','size','capacity','duplex',
}
_CRITICAL_WORDS=re.compile(
    r'тип товара|вид товара|конструкц|исполнени|формат|размер|габарит|мощност|'
    r'объ[её]м|вместимост|производительност|материал|цвет|обязательн|наличие|'
    r'не менее|не более|не ниже|не выше|диапазон|предельн',re.I)
_BOUND_OPERATORS={'minimum','maximum','range','greater_than','less_than','>','<','≥','≤','>=','<='}


def classify_requirement_criticality(requirement: dict[str,Any]) -> dict[str,Any]:
    """Classify the customer's own requirement, without adding a new one."""
    explicit=str(requirement.get('criticality') or '').casefold()
    if requirement.get('mandatory',True) is False or explicit=='optional':
        return {'critical':False,'reason':'Требование отмечено как необязательное'}
    if requirement.get('is_critical') is True or explicit in {'critical','high','критическое'}:
        return {'critical':True,'reason':'Критичность явно указана в данных ТЗ'}
    if explicit in {'secondary','low','noncritical','второстепенное'}:
        return {'critical':False,'reason':'Требование явно отмечено как второстепенное'}
    parameter=str(requirement.get('parameter') or requirement.get('requirement_name') or '')
    value=str(requirement.get('required_value',requirement.get('value','')))
    operator=requirement.get('operator') or requirement.get('comparison_type')
    key=parameter_key(parameter)
    if operator in _BOUND_OPERATORS or re.search(r'не менее|не более|не ниже|не выше|≥|≤|>|<',value,re.I):
        return {'critical':True,'reason':'Заказчик задал обязательную границу/диапазон'}
    if key in _CRITICAL_KEYS or _CRITICAL_WORDS.search(parameter):
        return {'critical':True,'reason':'Параметр определяет назначение или обязательное исполнение товара'}
    return {'critical':False,'reason':'CORE не обнаружил признаков критического ограничения'}


def classify_candidate(candidate: dict[str,Any],
                       requirements: list[dict[str,Any]]) -> dict[str,Any]:
    """Return one of three business statuses for a single exact SKU."""
    mandatory=[(index,row) for index,row in enumerate(requirements)
               if row.get('mandatory',True) is not False and row.get('criticality')!='optional']
    checks=candidate.get('requirements_check') or []
    rows=[]
    for index,requirement in mandatory:
        criticality=classify_requirement_criticality(requirement)
        check=checks[index] if index<len(checks) else {'result':'could_not_confirm'}
        rows.append({'requirement':requirement.get('parameter') or requirement.get('requirement_name'),
                     'critical':criticality['critical'],'criticality_reason':criticality['reason'],
                     'result':check.get('result'),'found_value':check.get('found_value'),
                     'source':check.get('source') or check.get('source_url')})
    confirmed=sum(row['result']==CORRESPONDS for row in rows)
    unknown=sum(row['result'] not in {CORRESPONDS,MISMATCH} for row in rows)
    mismatches=[row for row in rows if row['result']==MISMATCH]
    critical=[row for row in rows if row['critical']]
    critical_mismatches=[row for row in mismatches if row['critical']]
    critical_unknown=[row for row in critical if row['result']!=CORRESPONDS]
    exact_sku=bool(str(candidate.get('exact_model') or candidate.get('model_name')
                       or candidate.get('sku') or '').strip())
    exact_identity=exact_sku and candidate.get('page_matches') is not False
    type_confirmed=any(row['result']==CORRESPONDS and
                       (parameter_key(row['requirement'] or '')=='product_type'
                        or bool(re.search(r'тип товара|вид товара|вид кондиционера|'
                                          r'тип устройства|категория товара',
                                          str(row['requirement'] or ''),re.I)))
                       for row in rows)
    if rows and confirmed==len(rows):
        status=CONFIRMED_COMPLIANT
        reason='Подтверждены все обязательные требования заказчика'
    elif critical_mismatches:
        status=REJECTED
        reason='Доказано несоответствие критическому требованию заказчика'
    elif mismatches:
        status=REJECTED
        reason='Доказано несоответствие обязательному требованию заказчика'
    else:
        # UNKNOWN is never a contradiction.  "Several" means more than one
        # real confirmed fact, not a fixed share of the specification.
        if exact_identity and type_confirmed and confirmed>1:
            status=LIKELY_COMPLIANT
            reason=('Точная модель и нужный тип товара подтверждены несколькими фактами; '
                    'доказанных противоречий нет, остальные параметры не подтверждены')
        else:
            status=NOT_ENOUGH_EVIDENCE
            reason=('Недостаточно evidence для уверенного определения точной модели и типа товара')
    return {'candidate_admission_status':status,'candidate_admission_reason':reason,
            'requirements_total':len(rows),'requirements_confirmed':confirmed,
            'requirements_unknown':unknown,'requirements_mismatched':len(mismatches),
            'exact_sku_confirmed':exact_identity,'product_type_confirmed':type_confirmed,
            'critical_requirements':[row['requirement'] for row in critical],
            'critical_unknown':[row['requirement'] for row in critical_unknown],
            'critical_mismatches':critical_mismatches,'requirement_classification':rows}
