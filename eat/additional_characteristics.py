"""Официальный popup характеристик зелёного наименования позиции ЕАТ."""
from datetime import datetime, timezone


def read_additional_characteristics(page, items, card_url):
    results = []
    for index, item in enumerate(items):
        result = {'source': 'EAT_ADDITIONAL_CHARACTERISTICS', 'source_url': card_url,
                  'position_number': index + 1, 'checked_at': datetime.now(timezone.utc).isoformat(),
                  'status': 'not_available', 'text': '', 'rows': []}
        popup = page.locator('.modal-window-lot-item-info-content')
        try:
            name = page.locator(f'#lotItemName-{index}')
            if name.count() == 0:
                result['reason'] = 'Зелёное наименование позиции не найдено'
            else:
                name.click(timeout=5000)
                popup.wait_for(state='visible', timeout=5000)
                # Собирать только строки после официального заголовка.
                rows = popup.evaluate('''root => {
                    let additional = false; const rows = [];
                    for (const element of root.querySelectorAll('h3,.lot-item-window-info')) {
                        if (element.tagName === 'H3') {
                            additional = element.innerText.trim() === 'Дополнительные характеристики';
                        } else if (additional) {
                            const label = element.querySelector('.lot-item-window-info__label');
                            const value = element.querySelector('.lot-item-window-info__value');
                            if (label && value) rows.push({parameter: label.innerText.trim().replace(/:$/, ''),
                                                         value: value.innerText.trim()});
                        }
                    }
                    return rows;
                }''')
                result.update(status='read', rows=rows,
                              text='\n'.join(f"{r['parameter']}: {r['value']}" for r in rows))
        except Exception as exc:
            result.update(status='error', reason=f'Popup не прочитан ({type(exc).__name__})')
        finally:
            try:
                if popup.is_visible():
                    # Штатный крестик модального окна; без чтения соседнего текста.
                    close = page.locator('.swipeable-modal').get_by_role('button', name='Закрыть')
                    if close.count():
                        close.first.click(timeout=1000)
                    else:
                        page.keyboard.press('Escape')
            except Exception:
                pass
        item['eat_additional_characteristics'] = result
        results.append(result)
    return results
