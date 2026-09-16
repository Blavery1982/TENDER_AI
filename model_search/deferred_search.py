"""Два прохода поиска с сохранением результатов и явных ручных блокировок."""
import json
import time
from pathlib import Path
from documents.tender_archive import write_json


def run_deferred(jobs, search, output_path, *, pause_seconds=10, sleep=time.sleep):
    """Повторяем только временные ошибки после обработки всех товаров.

    search(job) возвращает сохранённый результат поиска. Защита не обходится;
    повтор CAPTCHA возможен только в новом явно запущенном проходе после
    ручного восстановления доступа. Старые цены не подменяют новые.
    """
    path = Path(output_path)
    state = {'version': 1, 'jobs': []}
    if len({str(j['id']) for j in jobs}) != len(jobs):
        raise ValueError('Идентификаторы заданий должны быть уникальными')

    def attempt(row):
        row['attempts'] += 1
        try:
            result = search(row['input'])
            row['result'] = result
            errors = [str(e.get('error', '')) for e in result.get('source_errors', [])]
            errors += [str(e.get('error', '')) for e in result.get('query_log', [])]
            diagnostics = list(result.get('diagnostics', []))
            for entry in result.get('source_errors', []) + result.get('query_log', []):
                if entry.get('diagnostic'):
                    diagnostics.append(entry['diagnostic'])
            if diagnostics:
                row['diagnostics'] = diagnostics
                primary = [d for d in diagnostics if d.get('primary')]
                row['primary_google_block'] = any(
                    d.get('stage') == 'google_search' and d.get('classification') in
                    {'captcha_or_robot_check', 'http_403', 'http_429'} for d in primary)
                row['search_deferred_due_to_primary_block'] = any(
                    d.get('classification') == 'deferred_due_to_primary_block' for d in diagnostics)
            has_block = any(d.get('classification') in {
                'captcha_or_robot_check', 'http_403', 'http_429',
                'deferred_due_to_primary_block'} for d in diagnostics)
            if result.get('search_status') == 'completed':
                # Блокировка лишнего источника не отменяет уже собранный
                # проверяемый TOP-3 из других доступных карточек.
                row['status'] = 'completed'
            elif has_block or any('PermissionError' in e or 'BlockedSourceError' in e for e in errors):
                row['status'] = 'requires_manual_check'
            elif any(any(t in e for t in ('Timeout', 'Connection', 'URLError')) for e in errors):
                row['status'] = 'retry_pending'
            else:
                row['status'] = result.get('search_status', 'incomplete')
        except PermissionError as exc:
            row['status'] = 'requires_manual_check'
            row['error'] = type(exc).__name__
            if getattr(exc, 'reason_code', None):
                row['error_reason'] = exc.reason_code
            elif getattr(exc, 'diagnostic', None):
                row['error_reason'] = exc.diagnostic.get('reason')
            if getattr(exc, 'diagnostic', None):
                row['diagnostic'] = exc.diagnostic
        except (TimeoutError, ConnectionError):
            row['status'] = 'retry_pending'
        except Exception as exc:
            row.update(status='failed', error=type(exc).__name__)
            if getattr(exc, 'reason_code', None):
                row['error_reason'] = exc.reason_code
            elif getattr(exc, 'reason', None):
                row['error_reason'] = exc.reason
            if getattr(exc, 'diagnostic', None):
                row['diagnostic'] = exc.diagnostic
        write_json(path, state)

    for job in jobs:
        row = {'id': str(job['id']), 'input': job, 'attempts': 0,
               'status': 'pending', 'result': None}
        state['jobs'].append(row)
        attempt(row)
    pending = [r for r in state['jobs'] if r['status'] == 'retry_pending']
    if pending:
        sleep(pause_seconds)
    for row in pending:
        previous = row.get('result')
        attempt(row)
        if previous:
            row['previous_result'] = previous
        if row['status'] == 'retry_pending':
            row['status'] = 'incomplete'
    write_json(path, state)
    return state


def retry_deferred(state, search, output_path, *, predicate):
    """Один явно запрошенный повтор выбранных отложенных заданий."""
    path = Path(output_path)
    retried = []
    for row in state.get('jobs', []):
        if row.get('attempts', 0) >= 2 or not predicate(row):
            continue
        row['attempts'] = row.get('attempts', 0) + 1
        retried.append(row['id'])
        try:
            result = search(row['input'])
            row['result'] = result
            row['status'] = result.get('search_status', 'incomplete')
            diagnostics = list(result.get('diagnostics', []))
            if diagnostics:
                row['diagnostics'] = diagnostics
            row['search_deferred_due_to_primary_block'] = False
        except PermissionError as exc:
            row['status'] = 'requires_manual_check'
            row['error'] = type(exc).__name__
            if getattr(exc, 'diagnostic', None):
                row['diagnostic'] = exc.diagnostic
                row['error_reason'] = exc.diagnostic.get('reason')
        except Exception as exc:
            row['status'] = 'failed'
            row['error'] = type(exc).__name__
        write_json(path, state)
    return retried
