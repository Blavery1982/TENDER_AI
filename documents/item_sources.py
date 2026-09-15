"""Position-local source resolution over already extracted text; no I/O."""
from __future__ import annotations
import re
from filters.position_kind import goods_position, blocked_position, classify_position
from model_search.search_mode import DENIAL
from documents.requirement_sources import allowed_requirement_text, popup_requirement_text
from model_search.customer_model import (MODEL_FIELD, REFERENCE, clean_designation,
                                         commercial_name, named_designation, product_name)
from model_search.compliance import UNIT_PATTERN

VERSION = 6
PRICE = 'PRICE_JUSTIFICATION'
CONTRACT = 'CONTRACT_DOCUMENT'
EAT = 'EAT_SPECIFICATION'
FIELDS = ('item_name', 'name', 'eatTitle', 'description', 'offerDescription', 'offer_description',
          'additionalCharacteristics', 'additional_characteristics', 'specification', 'technical_description')
PLACEHOLDER = re.compile(r'^(?:в соответствии? с техническим заданием|товар|требуется)\.?$', re.I)
CUSTOMER_UNIT = re.compile(r'(?<!\w)(кг/см²|см³|'+UNIT_PATTERN+r')(?![а-яa-z²])',re.I)
ADMIN_FIELD = re.compile(r'(?i)^(?:основные(?=:|$)|основные:?\s+требования\b|планируемые\s+сроки\b|(?:\d+\s+)?способ\s+определения\s+поставщика\b|согласовано\b|УФСИН\b|ФСИН\b)')


def source_kind(doc):
    section = str(doc.get('source_section') or '').casefold()
    kinds = doc.get('document_type') or []
    if not isinstance(kinds, list): kinds = [kinds]
    if 'обоснован' in section or doc.get('source_document_type') == 14 or any(k in kinds for k in ('price_justification', 'commercial_offer', 'price_list')):
        return PRICE
    if 'контракт' in section or doc.get('source_document_type') == 15 or any(k in kinds for k in ('contract_draft', 'contract_appendix', 'technical_specification', 'specification')):
        return CONTRACT
    return 'OTHER_DOCUMENT'


def _evidence(doc, fragment):
    page = next((p.get('page_number') for p in doc.get('pages', []) if fragment in (p.get('text') or '')), None)
    return {'source_document': doc.get('document_name', 'Спецификация ЕАТ'),
            'source_page': page, 'evidence': fragment}


def parse_requirements(text, source, doc=None):
    rows = []
    # «Описание предложения» — оболочка popup, а не название характеристики.
    text = re.sub(r'(?im)^\s*Описание предложения\s*:\s*', '', str(text or ''))
    # Делим явно именованные параметры одного абзаца, сохраняя десятичные точки.
    text = re.sub(r'(?<=[.!?])\s+(?=[^\n:.]{1,100}:)', '\n', text)
    for line in re.split(r'[\n;]+', text):
        line = line.strip(' \t|')
        if not line or PLACEHOLDER.fullmatch(line) or REFERENCE.search(line): continue
        match = re.match(r'^(.+?)\s*(≥|≤|>=|<=|>|<|:|\s[-–—]\s|\||\t+)\s*(.+)$', line)
        if not match:
            if named_designation(line, product_name({'name':line}), field='name'):
                continue
            match = re.match(r'^([^\d:]+?)\s+(\d[\d.,]*(?:\s*(?:кВт|Вт|мм|см|дБ|кг|Гц|м²))?)$', line)
            if not match: continue
            parameter, value = match.groups(); separator = ':'
        else:
            parameter, separator, value = match.groups()
        parameter = re.sub(r'^\d+[.)]\s*', '', parameter).strip()
        if MODEL_FIELD.match(parameter + ': ' + value) or REFERENCE.search(value): continue
        if re.fullmatch(r'\d{2}(?:\.\d{2,})+',parameter):continue
        if re.search(r'^(?:цена|стоимость|кол-во|№|описание предложения|наименование|единица измерения|ед\.\s*изм)\b', parameter, re.I): continue
        if re.fullmatch(r'(?i)количество(?: товаров| единиц)?',parameter):continue
        if re.fullmatch(r'[\d\s,.]+',parameter):continue
        if ADMIN_FIELD.search(parameter.strip('«»"')):continue
        if named_designation(parameter,product_name({'name':parameter}),field='name'):continue
        operator = {'≥':'minimum','>=':'minimum','≤':'maximum','<=':'maximum','>':'greater_than','<':'less_than'}.get(separator, 'equals')
        if re.search(r'не менее|не ниже', value, re.I): operator = 'minimum'
        if re.search(r'не более|не выше', value, re.I): operator = 'maximum'
        if re.search(r'не требуется|не требуются', value, re.I): operator = 'not_required'
        unit = CUSTOMER_UNIT.search(value + ' ' + parameter)
        rows.append({'requirement_name': parameter.strip(), 'value': value.strip(), 'operator': operator,
                     'unit': unit.group(1) if unit else None, 'requirement_source': source,
                     **_evidence(doc or {}, line), 'confidence': 'high'})
    return rows


def _normalize_structured_requirement(row, parameter, value):
    """Привести popup/fixture-строку к той же форме, что и текстовую строку."""
    value_text = str(value).strip()
    operator = row.get('operator')
    if not operator:
        if re.search(r'не требуется|не требуются', value_text, re.I):
            operator = 'not_required'
        elif re.search(r'не менее|не ниже|≥|>=', value_text, re.I):
            operator = 'minimum'
        elif re.search(r'не более|не выше|≤|<=', value_text, re.I):
            operator = 'maximum'
        else:
            operator = 'equals'
    unit = row.get('unit')
    if unit is None:
        match = CUSTOMER_UNIT.search(value_text+' '+str(parameter))
        unit = match.group(1) if match else None
    return {**row, 'requirement_name': parameter, 'value': value,
            'operator': operator, 'unit': unit, 'requirement_source': EAT,
            **_evidence({}, f'{parameter}: {value}')}


def _technical_text(text):
    """Совместимость: использовать только обозначенные разделы договора."""
    return allowed_requirement_text({'document_type': ['contract_draft']}, text)


def _scoped_text(doc, item, number, items):
    text = doc.get('text') or ''
    assigned = doc.get('item_number') or doc.get('position_number')
    if assigned is not None: return (text if str(assigned) == str(number) else ''), False
    # Explicit position headings delimit appendices and extracted table rows.
    headings = list(re.finditer(r'(?im)^\s*(?:позиция|товар)\s*№?\s*(\d+)\b[^\n]*', text))
    if headings:
        return '\n'.join(text[m.start():headings[i+1].start() if i+1<len(headings) else len(text)]
                         for i,m in enumerate(headings) if int(m.group(1)) == number), False
    if len(items) == 1: return text, False
    names = [str(x.get('name') or x.get('eatTitle') or '').strip() for x in items]
    name = names[number-1]
    if name and names.count(name) == 1:
        lines = text.splitlines()
        starts = [i for i,line in enumerate(lines) if name.casefold() in line.casefold()
                  and not any(n and n != name and n.casefold() in line.casefold() for n in names)]
        if starts:
            blocks = []
            for start in starts:
                end = next((i for i in range(start+1,len(lines))
                            if any(n and n != name and n.casefold() in lines[i].casefold() for n in names)),len(lines))
                blocks.append('\n'.join(lines[start:end]))
            return '\n'.join(blocks), False
    return '', bool(text.strip())


def scoped_requirement_text(doc, item, number, items):
    # Сначала границы официального раздела, затем привязка к позиции.
    # Иначе при выделении позиции теряется заголовок общей спецификации.
    allowed = allowed_requirement_text(doc)
    return _scoped_text({**doc, 'text': allowed}, item, number, items)


def resolve_item_sources(item, documents=(), *, item_number=1, items=None):
    """Сначала собрать требования позиции, затем установить обозначение заказчика."""
    if not goods_position(item):
        return blocked_position(item)
    items = items or [item]
    noun = product_name(item)
    rows, references, sources, designations, warnings, unparsed, unbound = [], [], [], [], [], [], []

    def consume(text, origin, doc=None, field='document_name'):
        if not text:
            return
        doc = doc or {}
        sources.append({'source': origin, 'field': field, **_evidence(doc, text), 'text': text})
        technical = re.sub(r'(?i)^\s*'+re.escape(noun)+r'\s*\(', '', text).rstrip(')')
        parsed = parse_requirements(technical, origin, doc)
        rows.extend(parsed)
        clean = re.sub(r'(?im)^\s*Описание предложения\s*:\s*', '', text)
        for line in clean.splitlines():
            if REFERENCE.search(line.strip()):
                references.append({**_evidence(doc, line), 'source': origin})
                continue
            # Обозначение комплектующей внутри параметра не становится моделью товара.
            is_parameter = any(r['evidence']==line.strip() for r in parsed)
            value = named_designation(line, noun, field=field) if not is_parameter or MODEL_FIELD.match(line.strip()) else None
            if value:
                value = value.split('|')[0].strip()
                designations.append((value, {'model_source': origin, 'source_field': field,
                                              **_evidence(doc, line)}))
            qualifier = bool(re.search(r'(?i)\b(?:для|должен|должна|требуется|необходимо|отдельно|совместим\w*)\b',line))
            if (qualifier and not any(r['evidence'] in line for r in parsed)
                    and not ADMIN_FIELD.search(line.strip('«»"'))
                    and not MODEL_FIELD.match(line.strip())):
                unparsed.append({'value':line,'requirement_source':origin,**_evidence(doc,line)})

    for field in FIELDS:
        if isinstance(item.get(field), str):
            consume(item[field], 'CUSTOMER_SPECIFICATION', field=field)
    consume(popup_requirement_text(item), 'EAT_ADDITIONAL_CHARACTERISTICS',
            {'document_name': 'ЕАТ: popup «Дополнительные характеристики»'}, 'popup')
    explicit = item.get('customer_required_model') or item.get('model') or item.get('article') or item.get('trademark') or item.get('commercial_designation')
    brand = item.get('brand')
    if explicit or brand:
        value = clean_designation(explicit or brand)
        if brand and str(brand).casefold() not in value.casefold():
            value = f'{brand} {value}'
        designations.append((value, {**_evidence({}, value), 'model_source': 'CUSTOMER_SPECIFICATION',
                                     'source_field': 'model/brand/article'}))
    for row in item.get('structured_requirements') or item.get('requirements') or item.get('characteristics') or []:
        if not isinstance(row, dict):
            continue
        parameter = row.get('parameter') or row.get('requirement_name') or row.get('name')
        value = row.get('required_value', row.get('value'))
        if parameter and value is not None:
            if MODEL_FIELD.match(f'{parameter}: {value}') or parameter.casefold() == 'описание предложения':
                consume(f'{parameter}: {value}', 'EAT_SPECIFICATION', field='popup')
            elif REFERENCE.search(str(value)):
                references.append({**_evidence({}, str(value)), 'source': EAT})
            else:
                rows.append(_normalize_structured_requirement(row, parameter, value))
    price_rows = []
    for doc in documents:
        origin = source_kind(doc)
        text, ambiguous = scoped_requirement_text(doc, item, item_number, items)
        if ambiguous:
            warnings.append('Неоднозначная привязка технического документа к позиции')
            # Не теряем прочитанные требования, но не приписываем их чужой позиции.
            technical = allowed_requirement_text(doc)
            unbound.append({'requirement_source':origin, 'text':technical,
                            **_evidence(doc,technical), 'binding_status':'requires_manual_check'})
        if doc.get('status') not in (None, 'analyzed') or doc.get('text_available') is False:
            warnings.append('Документ требований прочитан не полностью')
        before = len(rows)
        consume(text, origin, doc)
        if origin == PRICE:
            price_rows.extend(rows[before:])
    # Ценовое evidence не заменяет уже указанную заказчиком модель.
    customer_designations = [d for d in designations if d[1]['model_source'] != PRICE]
    chosen = customer_designations or designations
    names = [(commercial_name(noun, value), evidence) for value,evidence in chosen]
    names = [(name,e) for name,e in names if name]
    distinct = []
    for name,e in names:
        key = ' '.join(re.findall(r'[0-9A-Za-zА-Яа-яЁё]+', name.casefold()))
        if not any(key == k or (' '+key+' ') in (' '+k+' ') or (' '+k+' ') in (' '+key+' ') for k in distinct):
            distinct.append(key)
    model = max((name for name,e in names),key=len,default=None) if len(distinct)<=1 else None
    merged, conflicts = {}, []
    for row in rows:
        key = row['requirement_name'].casefold().strip()
        old = merged.get(key)
        if old and any(' '.join(str(old.get(k)).casefold().split()).rstrip(' .;)') != ' '.join(str(row.get(k)).casefold().split()).rstrip(' .;)') for k in ('value','operator','unit')):
            conflicts.append(key)
        else:
            merged.setdefault(key,row)
    if len(distinct)>1:
        conflicts.append('Несколько разных обозначений товара в источниках заказчика')
    all_text = '\n'.join(source['text'] for source in sources)
    replacement = bool(re.search(r'(?i)(?:или\s+)?(?:аналог|эквивалент)\w*', DENIAL.sub('', all_text)))
    evidence = [e for name,e in names] if model else []
    requirements = list(merged.values())
    if unparsed:
        warnings.append('Часть явного технического текста требует ручной расшифровки')
    complete = not warnings and not conflicts
    mode = 'EXACT_MODEL' if model and not conflicts else 'MODEL_DISCOVERY_REQUIRED' if requirements and complete else 'MODEL_MODE_REVIEW_REQUIRED'
    customer_product = {'product_name': noun, 'requirements': requirements,
                        'customer_designation': model, 'selected_model': model,
                        'model_evidence': evidence, 'sources': sources,
                        'source_references': references, 'unparsed_technical_requirements':unparsed,
                        'unbound_technical_sources':unbound,
                        'requirements_complete': complete}
    return {**classify_position(item), 'price_search_allowed': True,
            'source_resolution_version': VERSION, 'customer_product': customer_product,
            'product_name': noun, 'model_search_mode': mode, 'original_model': model,
            'customer_required_model': model, 'customer_model_raw': model,
            'customer_model_evidence': evidence[0] if evidence else None,
            'model_mode_reason': 'Заказчик указал коммерческое обозначение' if model else 'Требуется подбор по явным характеристикам' if requirements else 'Модель и реальные характеристики не установлены',
            'model_discovery_allowed': mode == 'MODEL_DISCOVERY_REQUIRED',
            'requirements': requirements, 'requirements_complete': complete,
            'compliance_required': True, 'requirements_required': True,
            'replacement_allowed': replacement,
            'model_source': evidence[0]['model_source'] if evidence else 'NOT_FOUND',
            'model_evidence': evidence, 'source_warnings': list(dict.fromkeys(warnings)),
            'source_conflicts': list(dict.fromkeys(conflicts)),
            'product_identifiers': [], 'document_model_candidates': [],
            'price_justification_model': model if evidence and evidence[0]['model_source']==PRICE else None,
            'price_justification_requirements': price_rows, 'price_model_evidence': [],
            'other_document_requirements': [], 'supplier_baseline_model': None,
            'supplier_baseline_status': None, 'supplier_prices_required': None,
            'distinct_suppliers_required': None, 'pricing_reference_model': model,
            'customer_specification_text': all_text,
            'alternative_policy': {'submission_allowed': False, 'silent_replacement_allowed': False}}
