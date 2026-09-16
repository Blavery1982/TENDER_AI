"""Изолированный контроль Codex → Yandex Search API → карточки, без записи в Sheets."""
import json
from datetime import datetime, timezone
from pathlib import Path

from ai.codex_intake import extract_product
from documents.item_sources import resolve_item_sources, parse_requirements
from documents.tender_archive import write_json
from model_search.deferred_search import run_deferred
from model_search.yandex_provider import YandexSearchProvider, SearchAPIError
from security.yandex_credentials import get_yandex_credentials
from model_search.live_discovery import discover_models, select_cheapest_compliant_model
from model_search.playwright_provider import PlaywrightResearch


def prepare_product(job, *, intake=extract_product):
    item = job['item']
    resolved = resolve_item_sources(item, job.get('documents', []))
    if resolved.get('position_kind') != 'goods':
        return resolved
    sources = resolved['customer_product']['sources']
    text = '\n\n'.join(s['text'] for s in sources)
    analysis = intake(text)
    resolved['ai_intake'] = analysis
    if analysis['uncertainties'] or resolved.get('source_conflicts'):
        resolved.update(original_model=None, model_search_mode='MODEL_MODE_REVIEW_REQUIRED',
                        model_discovery_allowed=False)
        return resolved
    requirements = list(resolved.get('requirements', []))
    for row in analysis['requirements']:
        # Python нормализует AI-параметры, сохраняя цитату отдельно.
        parsed = parse_requirements(f"{row['parameter']}: {row['value']}", 'CODEX_CUSTOMER_SOURCE')
        for requirement in parsed:
            requirement['evidence'] = row['evidence']
        requirements.extend(parsed)
    resolved['requirements'] = requirements
    model = analysis['selected_model']
    resolved.update(original_model=model, customer_required_model=model,
                    customer_model_raw=model,
                    model_search_mode='EXACT_MODEL' if model else 'MODEL_DISCOVERY_REQUIRED',
                    model_discovery_allowed=not model and bool(requirements)
                    and resolved.get('requirements_complete', False))
    return resolved


def run(input_path):
    from playwright.sync_api import sync_playwright
    payload = json.loads(Path(input_path).read_text(encoding='utf-8'))
    jobs = payload['jobs']
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= 5:
        raise ValueError('Контроль ограничен 1–5 товарными позициями')
    root = Path('data/product_price_control') / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    root.mkdir(parents=True, exist_ok=False)
    credentials = get_yandex_credentials()
    if credentials.status != 'ready' or credentials.credentials is None:
        result = {'search_provider': 'yandex_search_api', 'api_calls': 0,
                  'status': 'SEARCH_API_NOT_CONFIGURED',
                  'reason': 'Нужны folder_id и API-ключ в macOS Keychain TENDER_AI_YANDEX_SEARCH',
                  'credentials_status': credentials.status, 'jobs': []}
        write_json(root / 'result.json', result)
        return {'path': str(root / 'result.json'), **result}
    credentials = None
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(locale='ru-RU')
        diagnostics_dir = root / 'diagnostics'
        research = PlaywrightResearch(context, diagnostic_dir=diagnostics_dir)
        provider = YandexSearchProvider(allow_paid=True, fetcher=research, max_requests=30)
        prepared = {}

        def search(job):
            if provider.failed:
                raise SearchAPIError('Провайдер API остановлен', reason_code='api_provider_stopped')
            key = str(job['id'])
            if key not in prepared:
                prepared[key] = prepare_product(job)
                write_json(root / f'intake_{len(prepared)}.json', prepared[key])
            resolved = prepared[key]
            model = resolved.get('original_model')
            if not model and resolved.get('model_discovery_allowed'):
                discovered = discover_models({**resolved, 'item_name': job['item'].get('name')},
                                             provider=provider, use_cache=False)
                selected = select_cheapest_compliant_model(discovered.get('candidates', []))
                if selected:
                    model = selected.get('exact_model') or selected.get('model_name')
                if provider.failed:
                    raise SearchAPIError('Поиск API не выполнен', reason_code='api_search_failed')
            if not model:
                return {'search_status': 'MODEL_NOT_RESOLVED', 'offers': [],
                        'intake': resolved}
            result = research.prices_exact(model, provider, root / f'prices_{list(prepared).index(key)}.json',
                                           requirements=resolved.get('requirements', []),
                                           product_name=resolved.get('product_name'), target_offers=6,
                                           require_complete_offers=True)
            if provider.failed and result.get('search_status') != 'completed':
                result['search_status'] = 'SEARCH_API_FAILED'
            return {**result, 'intake': resolved, 'selected_model': model}

        try:
            result = run_deferred(jobs, search, root / 'result.json')
            result.update(search_provider=provider.name, api_calls=provider.api_calls,
                          max_api_requests=provider.max_requests, search_log=provider.search_log)
            write_json(root / 'result.json', result)
            return {'path': str(root / 'result.json'),
                    'jobs': [{'id': r['id'], 'status': r['status']} for r in result['jobs']],
                    'search_provider': provider.name, 'api_calls': provider.api_calls}
        finally:
            context.close()
            browser.close()
