"""Strict replay with only accepted evidence; unverified mirrored manual stays separate."""
import json,csv
from pathlib import Path
from collections import Counter
from model_search.product_evidence import html_page
from model_search.live_discovery import normalize_requirements
from model_search.compliance import assess_model
root=Path(__file__).resolve().parent
old=root.parent/'yandex_model_search_control/20260911T110342708671Z'
req=normalize_requirements(json.loads((old/'report.json').read_text())['requirements'])
prior=json.loads((old/'supplemental_compliance.json').read_text())['candidates'][0]
p=html_page((old/'saved_html/0.html').read_text(),prior['identity_source'])
p.update(source_type='professional_store',source_verified=True)
result=assess_model(req,[p],'EACS-09HP/N3')
for x in result['requirements_check']:
 x.setdefault('source_type','OTHER');x.setdefault('source_url',x.get('source'));x.setdefault('normalized_value',None)
result.update(api_requests=2,counts=dict(Counter(x['result'] for x in result['requirements_check'])),exact_sku_confirmed=True,official_evidence_verified=False,selected_model=None,
 document_review={'url':'https://ecoclima.ru/erc/2022/08/16/896f51281d6a75266ad0c0193165542ad952c913.pdf','source_type':'OTHER','page':12,'exact_model_column':'EACS-09 HP/N3','visual_check':'table_page12.png','authenticity':'Manufacturer-styled licensed manual mirrored by third parties; official origin not independently verified','not_used_as_official_evidence':True,'observations':[
 {'parameter':'energy_efficiency','value':'A','limitation':'No separate heating/cooling designation'},
 {'parameter':'capacity_cooling_heating','value':'9000/9400','limitation':'Unit absent; no BTU/h assumption or conversion'},
 {'parameter':'input_power','value':'821/763 W','limitation':'Electrical consumption, not delivered capacity'},
 {'parameter':'outdoor_unit','value':'720x428x310 mm','limitation':'Exact model column; document provenance unverified'},
 {'parameter':'antibacterial_filter','value':'3 ступень - антибактериальный фильтр','limitation':'Series-wide statement, not a separate exact-model cell'},
 {'parameter':'wall_mounting','value':'Крепления для монтажа на стену (только для внутреннего блока)','limitation':'Series-wide statement; not accepted as verified official evidence'}]},
 provenance_checks=[{'url':'https://electrolux-home.ru/info-about-sellers/','finding':'Retail sellers; manufacturer status not established'},{'url':'https://home-comfort.ru/','finding':'Website referenced by manual, climate product support available'},{'url':'https://home-comfort.ru/support/manual/','finding':'HTTP 403; no bypass or retry'}])
(root/'compliance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
with (root/'compliance_matrix.csv').open('w',encoding='utf-8-sig',newline='') as f:
 w=csv.writer(f);w.writerow(['requirement','status','source_type','source_url','exact_evidence','normalized_value'])
 for x in result['requirements_check']:w.writerow([x['requirement'],x['result'],x['source_type'],x['source_url'],json.dumps(x['evidence'],ensure_ascii=False),json.dumps(x['normalized_value'],ensure_ascii=False)])
lines=['# Electrolux EACS-09HP/N3: поиск официального evidence','','2 synchronous Yandex-запроса, по 8 результатов. Другие модели не исследовались. Ревизии _23Y и инверторная серия EACS/I исключены.','','Официальный технический источник с проверенным происхождением не получен. Найдены две сторонние копии руководства EACS-HP/N3 с точной колонкой EACS-09 HP/N3. Страница 12 первого PDF просмотрена визуально. Значения соседних колонок не использовались.','','Не считать electrolux-home.ru производителем по названию домена: страница продавцов описывает розничных контрагентов. В руководстве указан home-comfort.ru; главная доступна, раздел инструкций отвечает 403. Обход не выполнялся.','','В таблице класс A без привязки к режиму, 9000/9400 без единиц; 821/763 Вт — потребляемая мощность. Эти данные не закрывают два класса эффективности и две мощности. Признаки антибактериального фильтра/настенного и наружного блока вынесены в наблюдения; официальными доказательствами не объявлены.','','Итог принятого compliance: 5 corresponds / 0 does_not_comply / 8 could_not_confirm. Все 5 — ранее сохранённая карточка магазина. Официальных подтверждений: 0.','','| Требование | Статус |','|---|---|']
lines += [f"| {x['requirement']} | {x['result']} |" for x in result['requirements_check']]
lines += ['','Модель не выбрана. Пока не доказано несоответствие, но для продолжения нужны проверенный паспорт/инструкция точной ревизии с единицами мощности и классами по режимам. Повторять такие же поисковые запросы нецелесообразно; без более качественного источника разумнее перейти к следующему кандидату после отдельного разрешения.']
(root/'summary.md').write_text('\n'.join(lines)+'\n')
print(result['counts'])
