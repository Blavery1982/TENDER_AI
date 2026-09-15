"""Регрессии требований заказчика, коммерческого имени, XLS и текущих цен."""
import contextlib
import copy
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from documents.item_sources import resolve_item_sources
from documents.pipeline import document_processing_stop_reason
from documents.procurement_audit import extract_document_with_evidence
from model_search.compliance import assess_model
from model_search.live_discovery import select_cheapest_compliant_model
from model_search.live_price_search import inspect_product_url
from model_search.product_evidence import html_page, public_offer
from pipeline.single_purchase_test import run_check
from tests.test_business_order import card, Journal, MODEL, PID, offer


def popup(name, text):
    return {'name':name, 'eat_additional_characteristics':{'status':'read','text':text}}


def doc(text, **kwargs):
    return {'document_name':'ТЗ', 'document_type':['technical_specification'],
            'status':'analyzed','text_available':True,'text':text, **kwargs}


class RequirementsFirstTests(unittest.TestCase):
    def test_pump_from_green_field(self):
        result=resolve_item_sources(popup('Помпа','ПР-1'))
        self.assertEqual(result['original_model'],'Помпа ПР-1')
        self.assertFalse(result['model_discovery_allowed'])
        self.assertEqual(result['customer_product']['product_name'],'Помпа')

    def test_shared_raw_policy_uses_customer_field_context(self):
        from model_search.search_mode import determine_model_search_mode
        result=determine_model_search_mode({'name':'Помпа','model':'ПР-1'})
        self.assertEqual(result['original_model'],'Помпа ПР-1')
        self.assertFalse(result['model_discovery_allowed'])

    def test_collector_from_label_in_green_field(self):
        result=resolve_item_sources(popup('Коллектор','Модель КСТ-6'))
        self.assertEqual(result['original_model'],'Коллектор КСТ-6')
        result=resolve_item_sources(popup('Поставка аппарата воздушно-дыхательного АВМ-12-К','3. Модель\tАВМ-12-К\n12. Наличие баллонов БА-1\tДа\n14. Наличие редуктора ВР-12\tДа'))
        self.assertEqual(result['original_model'],'Аппарат воздушно-дыхательный АВМ-12-К')
        self.assertEqual(result['source_conflicts'],[])
        self.assertEqual(len(result['requirements']),2)

    def test_bb_battery_spaced_series_without_analog_selection(self):
        result=resolve_item_sources({'name':'Батарея BB Battery BP 5-12 (или аналог)'})
        self.assertEqual(result['original_model'],'BB Battery BP 5-12')
        self.assertTrue(result['replacement_allowed'])
        self.assertFalse(result['model_discovery_allowed'])

    def test_explicit_field_accepts_designation_without_model_format(self):
        result=resolve_item_sources({'name':'Помпа','model':'Морская серия «Тихая»'})
        self.assertEqual(result['original_model'],'Помпа Морская серия «Тихая»')

    def test_slash_modification_preserved_from_label(self):
        result=resolve_item_sources(popup('Кондиционер','Модель: HAC-09/S-PRO'))
        self.assertEqual(result['original_model'],'Кондиционер HAC-09/S-PRO')

    def test_requirements_collected_from_card_and_contract_before_branch(self):
        item=popup('Помпа','Модель: ПР-1\nДавление: не менее 100\nЦвет: белый')
        result=resolve_item_sources(item,[doc('Спецификация\nМодель: ПР-1\nМасса: 2 кг\nПорядок оплаты\nСрок оплаты: 5 дней',document_type=['contract_draft'])])
        self.assertEqual({r['requirement_name'] for r in result['requirements']},{'Давление','Цвет','Масса'})
        self.assertEqual(result['original_model'],'Помпа ПР-1')
        self.assertTrue(result['compliance_required'])
        items=[{'name':'Помпа ПР-1 для пресса'},{'name':'Коллектор КСТ-6 для пресса'}]
        text='Мощность: 10 Вт\nТекст без однозначной привязки к позиции'
        result=resolve_item_sources(items[0],[doc(text)],items=items)
        self.assertFalse(result['requirements_complete'])
        self.assertEqual(result['customer_product']['unbound_technical_sources'][0]['text'],text)

    def test_references_are_not_requirements_or_models(self):
        from documents.item_sources import parse_requirements
        text='99650,0: 199300,00\nПланируемые сроки закупки: сентябрь\n«Согласовано»: ЛБО\nСпособ определения поставщика: единственный\nУФСИН России: начальник\nАгрегат ЭЦВ 6-16-110 (7,5 кВт): 2\nКоличество патронов: не менее 6'
        self.assertEqual([r['requirement_name'] for r in parse_requirements(text,'PRICE_JUSTIFICATION')],['Количество патронов'])
        for text in ['характеристики в соответствии с ООЗ','согласно техническому заданию','в соответствии со спецификацией','см. приложение','согласно условиям договора']:
            with self.subTest(text=text):
                result=resolve_item_sources(popup('Помпа','Описание предложения: '+text))
                self.assertEqual(result['requirements'],[])
                self.assertIsNone(result['original_model'])
                self.assertTrue(result['customer_product']['source_references'])

    def test_popup_wrapper_does_not_swallow_voltage(self):
        result=resolve_item_sources(popup('Батарея','Описание предложения: Напряжение, В:12\nЕмкость, Ач:5'))
        self.assertEqual([(r['requirement_name'],r['value'],r['unit']) for r in result['requirements']], [('Напряжение, В','12','В'),('Емкость, Ач','5','Ач')])
        self.assertIsNone(result['original_model'])

    def test_position_blocks_keep_following_parameters_without_leakage(self):
        items=[{'name':'Помпа'},{'name':'Коллектор'}]
        source=doc('Помпа\nМодель: ПР-1\nМасса: 2 кг\nКоллектор\nМодель: КСТ-6\nМасса: 3 кг')
        pump=resolve_item_sources(items[0],[source],item_number=1,items=items)
        collector=resolve_item_sources(items[1],[source],item_number=2,items=items)
        self.assertEqual(pump['original_model'],'Помпа ПР-1')
        self.assertEqual(collector['original_model'],'Коллектор КСТ-6')
        self.assertEqual(pump['requirements'][0]['value'],'2 кг')
        self.assertEqual(collector['requirements'][0]['value'],'3 кг')

    def test_xls_reads_real_biff_model_and_tables(self):
        result=extract_document_with_evidence(Path(__file__).parent/'fixtures/customer_requirements.xls')
        self.assertEqual(result['document_parse_status'],'analyzed')
        self.assertEqual(len(result['rows']),3)
        resolved=resolve_item_sources({'name':'Помпа'},[doc(result['text'])])
        self.assertEqual(resolved['original_model'],'Помпа ПР-1')
        self.assertEqual([r['requirement_name'] for r in resolved['requirements']],['Рабочее давление'])

    def test_unparsed_explicit_execution_is_preserved_as_unknown(self):
        result=resolve_item_sources({'name':'Помпа ПР-1 для прессов серии ПУМ (отдельно на ножке)'})
        self.assertEqual(result['original_model'],'Помпа ПР-1')
        self.assertFalse(result['requirements_complete'])
        self.assertIn('на ножке',result['customer_product']['unparsed_technical_requirements'][0]['value'])
        result=resolve_item_sources({'name':'Одеяло','description':'Тип: всесезонное. Наполнитель: холлофайбер. Плотность (гр/м²): 300. Длина (см): 205. Масса: 1.8 кг.\n92.24.119: служебный код'})
        self.assertEqual(len(result['requirements']),5)
        self.assertEqual(result['requirements'][2]['unit'],'гр/м²')
        self.assertEqual(result['requirements'][4]['value'],'1.8 кг.')

    def test_unsupported_or_empty_read_is_incomplete(self):
        for status,text_available in [('unsupported',False),('analyzed',False),('parse_failed',False)]:
            self.assertIsNotNone(document_processing_stop_reason({'document_results':[{'status':status,'text_available':text_available}]}))

    def test_cheapest_model_must_pass_every_customer_parameter(self):
        from model_search.live_discovery import _candidate_models
        candidates=_candidate_models([{'url':'https://shop.ru/product/pillow','title':'Подушка выдуманная','verified_product_heading':'Подушка стеганая Белый пух'}, {'url':'https://shop.ru/other','title':'Подушка из сниппета'}],product_name='Подушка')
        self.assertEqual([c['model_name'] for c in candidates],['Подушка стеганая Белый пух'])
        requirements=[{'requirement_name':'Мощность','value':'10 Вт','operator':'minimum'}, {'requirement_name':'Цвет','value':'белый'}]
        candidates=[]
        for name,power,color,price in [('Pantum M6500W',5,'белый',1),('Pantum M6607NW',12,'белый',20),('Pantum M6700DW',15,'белый',30),('Pantum M6800DW',15,'черный',2)]:
            page=html_page(f'<h1>{name}</h1><p>Мощность: {power} Вт</p><p>Цвет: {color}</p>', 'https://shop.ru/product/printer')
            candidates.append({'exact_model':name,**assess_model(requirements,[page],name),'public_price':price,'russia_availability':'available'})
        self.assertEqual(select_cheapest_compliant_model(candidates)['exact_model'],'Pantum M6607NW')


class CurrentPriceTests(unittest.TestCase):
    def test_search_engine_links_are_rejected_before_fetch(self):
        from unittest.mock import MagicMock
        fetcher=MagicMock()
        for url in ['https://www.google.com/search?q=Помпа','https://google.ru/search?q=Помпа','https://bing.com/search?q=Помпа','https://yandex.ru/search/?text=Помпа']:
            self.assertIsNone(inspect_product_url({'product_url':url},'Помпа ПР-1',fetcher=fetcher))
        fetcher.assert_not_called()

    def test_browser_search_filters_footer_links_and_stops_at_captcha(self):
        from unittest.mock import MagicMock
        from model_search.playwright_provider import YandexBrowserSearch
        research=MagicMock();page=research.context.new_page.return_value
        page.url='https://yandex.ru/search/'
        page.locator.return_value.inner_text.return_value='Результаты поиска'
        page.locator.return_value.evaluate_all.return_value=[{'url':'https://google.com/search?q=Помпа','title':'Google'}, {'url':'https://bing.com/search?q=Помпа','title':'Bing'}, {'url':'https://shop.ru/product/pump','title':'Помпа ПР-1'}]
        provider=YandexBrowserSearch(research)
        self.assertEqual(provider.search('Помпа ПР-1'),[{'url':'https://shop.ru/product/pump','title':'Помпа ПР-1','discovery_provider':'yandex_browser','snippet_is_evidence':False}])
        page.url='https://yandex.ru/showcaptcha'
        with self.assertRaises(PermissionError):provider.search('Помпа ПР-1 купить')
        with self.assertRaises(RuntimeError):provider.search('Повтор')
        self.assertEqual(page.goto.call_count,2)

    def inspect(self, body, model='Bergauf Keramik PLUS 25 кг'):
        return inspect_product_url({'product_url':'https://shop.ru/product/glue'},model,fetcher=lambda url:(body,url,200))

    def test_woocommerce_current_price_not_related_prices(self):
        body='<h1>Bergauf Keramik Plus 25 кг</h1><div class="summary entry-summary"><p class="price"><bdi>490.00&nbsp;₽</bdi></p>В наличии</div><div class="related products"><p class="price">10 ₽</p></div>'
        result=self.inspect(body)
        self.assertEqual(result['price'],490)
        self.assertTrue(result['price_confirmed_on_product_page'])

    def test_microdata_current_price_not_old_price_reaches_ranking(self):
        from suppliers.price_search_flow import confirmed_price_ranking
        body='<div itemscope itemtype="https://schema.org/Product"><h1>Bergauf Keramik Plus 25 кг</h1><div class="price__new"><span class="price__new-val">498.99 ₽/шт</span><meta itemprop="price" content="498.99"><meta itemprop="priceCurrency" content="RUB"></div><div class="price__old">566 ₽</div>В наличии</div>'
        result=self.inspect(body)
        self.assertEqual(result['price'],498.99)
        rows=[{**result,'url':f'https://shop{i}.ru/product/glue','seller':f'shop{i}.ru'} for i in range(3)]
        self.assertEqual([r['public_price'] for r in confirmed_price_ranking(rows)], [498.99]*3)
        result=inspect_product_url({'product_url':'https://market.yandex.ru/card/glue/123'},'Bergauf Keramik PLUS 25 кг',fetcher=lambda url:(body,url,200))
        self.assertIsNone(result)

    def test_related_only_or_ambiguous_current_prices_not_accepted(self):
        for content in ['<div class="related products"><p>Цена: 10 руб.</p></div>', '<div class="summary"><p class="price">100 ₽</p><p class="price">200 ₽</p></div>']:
            result=self.inspect('<h1>Bergauf Keramik Plus 25 кг</h1>'+content)
            self.assertIsNone(result['price'])

    def test_exact_identity_rejects_neighbor_and_preserves_formatting(self):
        for model in ('ПР-2','ПР-1.1','ПР-1-F2'):
            self.assertIsNone(self.inspect(f'<h1>Помпа {model}</h1><p>Цена: 100 руб.</p>','Помпа ПР-1'))
        self.assertIsNone(self.inspect('<h1>Bergauf Keramik Plus 50 кг</h1><p>Цена: 100 руб.</p>'))
        self.assertIsNone(self.inspect('<h1>Кондиционер HAC-09-S-PRO</h1><p>Цена: 100 руб.</p>','Кондиционер HAC-09/S-PRO'))
        self.assertIsNotNone(self.inspect('<h1>BB Battery BP5-12</h1><p>Цена: 100 руб.</p>','BB Battery BP 5-12'))
        for title,accepted in [('Воздушно-дыхательный аппарат АВМ-12-К',True),('Воздушно-дыхательный насос АВМ-12-К',False),('Воздушно-дыхательный аппарат АВМ-12-К.1',False)]:
            result=inspect_product_url({'product_url':'https://shop.ru/product/avm'},'Аппарат воздушно-дыхательный АВМ-12-К',product_name='Аппарат воздушно-дыхательный',fetcher=lambda url:(f'<h1>{title}</h1><p>Цена: 100 руб.</p>',url,200))
            self.assertEqual(result is not None,accepted)
        body='<h1>Насос ЭЦВ 6-16-110</h1><p>Электронасосный агрегат для скважин</p><p>Цена: 100 руб.</p>'
        result=inspect_product_url({'product_url':'https://shop.ru/product/ecv'},'Агрегат ЭЦВ 6-16-110',product_name='Агрегат',fetcher=lambda url:(body,url,200))
        self.assertEqual(result['price'],100)

    def test_explicit_voltage_contradiction_rejects_exact_model(self):
        req=[{'requirement_name':'Напряжение, В','value':'12','unit':'В'}]
        result=inspect_product_url({'product_url':'https://shop.ru/product/battery'},'BB Battery BP 5-12',requirements=req,fetcher=lambda url:('<h1>BB Battery BP5-12</h1><p>Напряжение, В: 6</p><p>Цена: 100 руб.</p>',url,200))
        self.assertIsNone(result)
        result=inspect_product_url({'product_url':'https://shop.ru/product/battery'},'BB Battery BP 5-12',requirements=[{'requirement_name':'Срок службы АКБ, лет','value':'10','unit':'лет'}],fetcher=lambda url:('<h1>BB Battery BP5-12</h1><p>Срок службы, лет: 5</p><p>Цена: 100 руб.</p>',url,200))
        self.assertIsNone(result)
        from unittest.mock import MagicMock
        from model_search.playwright_provider import PlaywrightResearch
        research=PlaywrightResearch(MagicMock());provider=MagicMock();provider.name='yandex_browser'
        provider.search.return_value=[{'url':'https://shop.ru/product/battery','title':'BB Battery BP 5-12'}]
        research.read_http=lambda url:('<h1>BB Battery BP5-12</h1><p>Цена: 100 руб.</p>',url,200)
        for voltage,expected in [(6,[]),(12,[100])]:
            research.read=lambda url:(f'<h1>BB Battery BP5-12</h1><p>Напряжение, В: {voltage}</p>',url,200)
            with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
                result=research.prices_exact('BB Battery BP 5-12',provider,Path(directory)/'price.json',requirements=req,max_queries=1,max_sources=1)
            self.assertEqual([o['price'] for o in result['offers']],expected)


class SemanticStatusTests(unittest.TestCase):
    def route(self, source, offers):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), patch('pipeline.single_purchase_test.tender_folder',return_value=Path(directory)):
            return run_check(PID,journal=Journal(),card_loader=lambda _:source,price_search=lambda *a,**kw:{'offers':offers},output_dir=Path(directory))

    def test_unresolved_model_never_claims_economic_signal(self):
        source=card(None);source['documents']=[]
        result=self.route(source,[])
        self.assertEqual(result['status'],'MODEL_NOT_RESOLVED')
        self.assertFalse(result['positions'][0]['price_search_performed'])
        self.assertEqual(result['price_gate']['status'],'NOT_EVALUATED')

    def test_no_prices_is_search_failure_not_economic_rejection(self):
        source=card();source['documents']=[]
        result=self.route(source,[])
        self.assertEqual(result['status'],'PRICE_SEARCH_FAILED')
        self.assertTrue(result['positions'][0]['price_search_performed'])

    def test_actual_prices_above_gate_are_economic_rejection(self):
        source=card();source['documents']=[]
        result=self.route(source,[offer('one',450),offer('two',500),offer('three',550)])
        self.assertEqual(result['status'],'NO_ECONOMIC_SIGNAL')

    def test_two_economically_good_prices_do_not_trigger_supplier_checks(self):
        source=card();source['documents']=[]
        for offers in ([offer('one',380),offer('two',390)],[offer('one',550)]):
            with self.subTest(prices=len(offers)):
                result=self.route(source,offers)
                self.assertFalse(result['price_gate']['passes'])
                self.assertEqual(result['price_gate']['status'],'NOT_EVALUATED')
                self.assertEqual(result['economic_precheck']['status'],'insufficient_data')
                self.assertEqual(result['status'],'PRICE_SEARCH_FAILED')
                self.assertEqual(result['exact_supplier_flow']['antifraud_history'],[])
