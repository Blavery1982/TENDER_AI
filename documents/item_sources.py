"""Position-local source resolution over already extracted text; no I/O."""
from __future__ import annotations
import re
from model_search.search_mode import determine_model_search_mode, DENIAL
from model_search.price_readiness import extract_document_identifiers

VERSION = 3
PRICE = 'PRICE_JUSTIFICATION'
CONTRACT = 'CONTRACT_DOCUMENT'
EAT = 'EAT_SPECIFICATION'
FIELDS = ('item_name', 'name', 'eatTitle', 'description', 'offerDescription', 'offer_description',
          'additionalCharacteristics', 'additional_characteristics', 'specification', 'technical_description')
PLACEHOLDER = re.compile(r'^(?:в соответствии? с техническим заданием|товар|требуется)\.?$', re.I)


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
    for line in re.split(r'[\n;]+', text):
        line = line.strip(' \t|')
        if not line or PLACEHOLDER.fullmatch(line): continue
        match = re.match(r'^(.+?)\s*(≥|≤|>=|<=|>|<|:|\s[-–—]\s|\|)\s*(.+)$', line)
        if not match:
            match = re.match(r'^([^\d:]+?)\s+(\d[\d.,]*\s*(?:кВт|Вт|мм|см|дБ|кг|Гц|м²))$', line)
            if not match: continue
            parameter, value = match.groups(); separator = ':'
        else:
            parameter, separator, value = match.groups()
        if re.search(r'^(?:модель|марка|бренд|артикул|цена|стоимость|количество|№)\b', parameter, re.I): continue
        operator = {'≥':'minimum','>=':'minimum','≤':'maximum','<=':'maximum','>':'greater_than','<':'less_than'}.get(separator, 'equals')
        if re.search(r'не менее|не ниже', value, re.I): operator = 'minimum'
        if re.search(r'не более|не выше', value, re.I): operator = 'maximum'
        if re.search(r'не требуется|не требуются', value, re.I): operator = 'not_required'
        unit = re.search(r'(?<!\w)(кВт|Вт|мм|см|м²|м\^2|дБ|Гц|кг|В|%)(?!\w)', value)
        rows.append({'requirement_name': parameter.strip(), 'value': value.strip(), 'operator': operator,
                     'unit': unit.group(1) if unit else None, 'requirement_source': source,
                     **_evidence(doc or {}, line), 'confidence': 'high'})
    return rows


def _technical_text(text):
    """Exclude contract payment/party clauses from product identity detection."""
    heading = re.search(r'(?im)^\s*(?:техническое задание|спецификация)\s*:?[ \t]*$', text)
    if heading:
        text = text[heading.end():]
        end = re.search(r'(?im)^.*(?:Приложение\s*№\s*\d+|ПОДПИСИ|Обоснование включения).*$', text)
        if end: text = text[:end.start()]
    return re.sub(r'(?im)^\s*(?:КТРУ|ОКПД2|ИКЗ)\s*:.*$', '', text)


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
        lines = [line for line in text.splitlines() if name.casefold() in line.casefold()
                 and not any(n and n != name and n.casefold() in line.casefold() for n in names)]
        if lines: return '\n'.join(lines), False
    return '', bool(text.strip())


def resolve_item_sources(item, documents=(), *, item_number=1, items=None):
    items = items or [item]
    texts = [str(item[k]) for k in FIELDS if isinstance(item.get(k), str) and item[k].strip()]
    for field, label in (('brand','Марка'), ('model','Модель'), ('customer_required_model','Модель'), ('article','Артикул товара')):
        if item.get(field): texts.append(f'{label}: {item[field]}')
    eat_text = '\n'.join(dict.fromkeys(texts))
    customer_rows = parse_requirements(eat_text, EAT)
    for row in item.get('structured_requirements') or item.get('requirements') or item.get('characteristics') or []:
        if not isinstance(row, dict): continue
        parameter = row.get('parameter') or row.get('requirement_name') or row.get('name')
        value = row.get('required_value', row.get('value'))
        if parameter and value is not None:
            customer_rows.append({**row, 'requirement_name': parameter, 'value': value,
                                  'requirement_source': EAT, **_evidence({}, f'{parameter}: {value}')})
            eat_text += f'\n{parameter}: {value}'
    customer_texts = [eat_text]; price_texts = []; price_rows = []; other_rows = []; warnings = []
    model_evidence = []; price_evidence = []; document_model_candidates = []
    initial = determine_model_search_mode({'description': eat_text})
    if initial['original_model']: model_evidence.append({**_evidence({}, eat_text), 'model_source':'CUSTOMER_SPECIFICATION'})
    for doc in sorted(documents, key=lambda d: {CONTRACT:0, PRICE:1}.get(source_kind(d), 2)):
        source = source_kind(doc)
        text, ambiguous = _scoped_text(doc, item, item_number, items)
        if ambiguous and (not customer_rows or not initial['original_model']): warnings.append('Неоднозначная привязка документа к позиции')
        if doc.get('status') in ('failed', 'missing', 'partial') and source == CONTRACT:
            warnings.append('Документ требований прочитан не полностью')
        if not text: continue
        if source == CONTRACT: text = _technical_text(text)
        for identifier in extract_document_identifiers(text):
            fragment = next((line.strip() for line in text.splitlines()
                             if identifier.get('identifier') and
                             str(identifier['identifier']).casefold() in line.casefold()),
                            str(identifier.get('identifier') or '')[:300])
            evidence = _evidence(doc, fragment)
            document_model_candidates.append({
                **identifier,
                'model_source': source,
                'evidence': evidence,
            })
        rows = parse_requirements(text, source, doc)
        if source == CONTRACT:
            customer_texts.append(text)
            customer_rows.extend(rows)
            if determine_model_search_mode({'description': text})['original_model']:
                model_evidence.append({**_evidence(doc, text), 'model_source': CONTRACT})
        elif source == PRICE:
            price_texts.append(text); price_rows.extend(rows); price_evidence.append(_evidence(doc, text))
        else: other_rows.extend(rows)
        if doc.get('status') in ('failed', 'missing', 'partial') and source == CONTRACT:
            warnings.append('Документ требований прочитан не полностью')
    # Customer conflicts are explicit review; commercial rows never overwrite customer rows.
    merged = {}; conflicts = []
    for row in customer_rows:
        key = row['requirement_name'].casefold().strip()
        old = merged.get(key)
        if old and any(str(old.get(k)).casefold() != str(row.get(k)).casefold() for k in ('value', 'operator', 'unit')): conflicts.append(key)
        else: merged.setdefault(key, row)
    customer = determine_model_search_mode({'description': '\n'.join(customer_texts)})
    price = determine_model_search_mode({'description': '\n'.join(price_texts)})
    price_model = price['original_model'] or item.get('price_justification_model') or item.get('model_from_justification')
    review = bool(warnings or conflicts or customer['model_search_mode'] == 'MODEL_MODE_REVIEW_REQUIRED'
                  or (price['model_search_mode'] == 'MODEL_MODE_REVIEW_REQUIRED' and not customer['original_model'] and not merged))
    if not customer['original_model'] and not merged and not price_model: review = True
    if review: customer.update(model_search_mode='MODEL_MODE_REVIEW_REQUIRED', model_discovery_allowed=False,
                               model_mode_reason='Недостаточные, противоречивые или неоднозначные данные источников')
    baseline = bool(price_model and not customer['original_model'] and not merged and not review)
    model_source = model_evidence[0]['model_source'] if model_evidence else PRICE if price_model else 'NOT_FOUND'
    identities = []
    for source_text, origin in [(eat_text, 'CUSTOMER_SPECIFICATION'),
                                 *[(t, CONTRACT) for t in customer_texts[1:]],
                                 *[(t, PRICE) for t in price_texts]]:
        for match in re.finditer(r'(?im)^(марка|бренд|артикул(?: товара)?)\s*[:—-]\s*([^\n]+)', source_text):
            identities.append({'field': 'article' if match.group(1).lower().startswith('артикул') else 'brand',
                               'value': match.group(2).strip(), 'model_source': origin, 'evidence': match.group(0)})
    return {**customer, 'source_resolution_version': VERSION, 'product_identifiers': identities,
            'customer_required_model': customer['original_model'], 'price_justification_model': price_model,
            'model_source': model_source, 'model_evidence': model_evidence, 'price_model_evidence': price_evidence,
            'document_model_candidates': document_model_candidates,
            'requirements': list(merged.values()), 'price_justification_requirements': price_rows,
            'other_document_requirements': other_rows, 'source_warnings': warnings, 'source_conflicts': conflicts,
            'pricing_reference_model': customer['original_model'] or price_model,
            'supplier_baseline_model': price_model if baseline else None,
            'supplier_baseline_status': 'reference_only_not_compliance' if baseline else None,
            'supplier_prices_required': 3 if baseline else None, 'distinct_suppliers_required': 3 if baseline else None,
            'alternative_policy': {'label': 'ВОЗМОЖНА БОЛЕЕ ВЫГОДНАЯ АЛЬТЕРНАТИВА',
                'requires_confirmed_equivalent_characteristics': True, 'silent_replacement_allowed': False,
                'submission_allowed': customer['model_search_mode'] in ('MODEL_DISCOVERY_REQUIRED','EXACT_MODEL_OR_EQUIVALENT')
                    and not DENIAL.search('\n'.join(customer_texts))},
            'customer_specification_text': '\n'.join(customer_texts)}
