"""Коммерческое имя из контекста полей заказчика, без поиска и догадок о свойствах."""
import json
import re
from pathlib import Path

from model_search.product_evidence import BRANDS, normalized_identifier_phrase

CONTEXT = json.loads((Path(__file__).resolve().parent.parent / 'config/customer_product_context.json').read_text())
BRAND_NAMES = (*BRANDS, *CONTEXT['commercial_names'])
MODEL_FIELD = re.compile(r'^(?:\d+[.)]\s*)?(?:модель|марка|бренд|артикул(?: товара)?|товарный знак|коммерческое обозначение)\s*[:|\t—-]?\s*(.+)$', re.I)
REFERENCE = re.compile(r'(?i)^(?:характеристики\s+)?(?:в соответствии\s+со?|в соответствие\s+со?|согласно|см\.?\s+приложение|см\.?\s+тз)\b|^[-—.]$')


def clean_designation(value):
    value = ' '.join(str(value or '').strip(' \t|').split())
    if (value.startswith('«') and value.endswith('»')) or (value.startswith('"') and value.endswith('"')):
        value = value[1:-1]
    value = re.split(r'(?i)\s*\(?\s*(?:или\s+)?(?:аналог\w*|эквивалент\w*)\b', value)[0].strip(' (,;')
    return '' if REFERENCE.search(value) else value


def product_name(item):
    name = str(item.get('product_name') or item.get('item_name') or item.get('name') or '').strip()
    for form,noun in CONTEXT.get('product_name_forms',{}).items():
        if re.search(r'(?i)(?<!\w)'+re.escape(form)+r'(?!\w)',name):
            return noun
    for noun in sorted(CONTEXT['product_names'], key=len, reverse=True):
        match = re.search(r'(?i)(?<!\w)' + re.escape(noun).replace('е', '[её]') + r'(?!\w)', name)
        if match:
            return match.group(0).capitalize()
    return name or 'Товар'


def has_brand(value):
    return any(re.search(r'(?i)(?<!\w)' + re.escape(brand) + r'(?!\w)', value) for brand in BRAND_NAMES)


def commercial_name(noun, designation):
    value = clean_designation(designation)
    if not value:
        return None
    if has_brand(value) or normalized_identifier_phrase(value).startswith(normalized_identifier_phrase(noun)):
        return value
    return f'{noun} {value}'.strip()


def named_designation(text, noun, *, field='name'):
    """Форма кода — лишь ограничитель внутри явно именованного товара/поля."""
    line = clean_designation(text)
    if not line:
        return None
    labelled = MODEL_FIELD.match(line)
    if labelled:
        return clean_designation(labelled.group(1))
    if field=='popup' and (re.match(r'^\d+[.)]',line) or re.search(r'[:|\t≥≤<>]',line)):
        return None
    if has_brand(line):
        # В характеристиках название разъёма/материала не является моделью товара.
        if field not in {'name', 'description', 'popup', 'document_name'}:
            return None
        start = min(m.start() for brand in BRAND_NAMES
                    if (m := re.search(r'(?i)(?<!\w)' + re.escape(brand) + r'(?!\w)', line)))
        return re.split(r'(?i)\s+(?:для|совместим\w*|характеристики)\b', line[start:])[0].strip()
    named_line = line
    for form,canonical in CONTEXT.get('product_name_forms',{}).items():
        named_line = re.sub(r'(?i)(?<!\w)'+re.escape(form)+r'(?!\w)',canonical,named_line)
    match = re.match(r'(?i)^(?:поставка\s+)?' + re.escape(noun) + r'\s+(.+)$', named_line)
    tail = match.group(1) if match else line if field == 'popup' and ':' not in line else ''
    tail = re.split(r'(?i)\s+(?:для|с использованием|совместно|отдельно)\b', tail)[0].strip(' ()')
    # Здесь уже есть контекст наименования конкретного товара либо зелёного поля.
    # Размер, стандарт и технологическая характеристика не дают модели.
    if (tail and re.search(r'[A-ZА-ЯЁ]{2}', tail) and re.search(r'\d', tail)
            and not re.match(r'(?i)^(?:ГОСТ|ISO|DIN|IP\d|USB|HDMI|DDR|RJ[- ]?45)\b', tail)
            and not re.fullmatch(r'(?i)[\d., хx×()]+\s*(?:кг|л|мм|см|м|вт|в|тб|гб|дюйм\w*)?', tail)):
        return tail
    return None
