"""Явные границы разрешённых источников характеристик закупки."""
import re

TECH_HEADING = re.compile(
    r"(?im)^\s*(?:\d+[.)]\s*)?(?:спецификаци[яи]|техническ\w*\s+задани\w*|"
    r"описани\w*\s+(?:объекта\s+закупки|ОЗ)|техническ\w*\s+характеристик\w*|"
    r"таблица\s+характеристик\w*)[^\n]*$"
)
END_HEADING = re.compile(
    r"(?im)^\s*(?:\d+(?:\.\d+)*[.)]?\s*)?(?:ответственность\s+сторон|"
    r"порядок\s+расч[её]тов|условия\s+оплаты|порядок\s+оплаты|форс.?мажор|"
    r"штраф\w*|расторжен\w*|обязанности\s+сторон|права\s+и\s+обязанности|"
    r"реквизиты|подписи|заключительные\s+положения|приложение\s*№|"
    r"предмет\s+контракта|порядок\s+(?:поставки|при[её]мки)|условия\s+поставки|"
    r"срок\s+действия\s+контракта|обеспечение\s+исполнения|прочие\s+условия|"
    r"обоснование\s+включения)[^\n]*$"
)


def technical_sections(text):
    """Без явного раздела договор не является источником характеристик."""
    sections = []
    headings = list(TECH_HEADING.finditer(text))
    for index, heading in enumerate(headings):
        end = headings[index+1].start() if index+1 < len(headings) else len(text)
        stop = END_HEADING.search(text, heading.end(), end)
        if stop:
            end = stop.start()
        sections.append(text[heading.end():end].strip())
    return "\n".join(filter(None, sections))


def allowed_requirement_text(document, text=None):
    text = str(document.get("text") or "") if text is None else text
    sections = technical_sections(text)
    if sections:
        return sections
    kinds = document.get("document_type") or []
    if not isinstance(kinds, list):
        kinds = [kinds]
    # Проект контракта, даже автоматически классифицированный ещё и как
    # ТЗ по упоминанию, допускает только явно выделенный раздел.
    if "contract_draft" in kinds or document.get("source_document_type") == 15:
        return ""
    standalone = (document.get("source_document_type") == 14 or
                  any(k in kinds for k in ("technical_specification", "specification",
                                           "price_justification", "commercial_offer")))
    if not standalone:
        return ""
    stop = END_HEADING.search(text)
    return text[:stop.start()].strip() if stop else text.strip()


def popup_requirement_text(item):
    popup = item.get("eat_additional_characteristics") or {}
    return str(popup.get("text") or "") if popup.get("status") == "read" else ""
