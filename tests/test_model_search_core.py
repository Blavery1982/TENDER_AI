"""Offline regressions; product evidence below is a controlled test fixture, not a live check."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from model_search.query_planning import generate_queries
from model_search.product_evidence import (extract_skus,candidates_from_results,html_page,document_pages,
    public_offer,page_matches,source_priority,same_exact_model)
from model_search.compliance import (assess_model,check_requirement,compare,CORRESPONDS,MISMATCH,UNKNOWN)
from model_search.live_discovery import discover_models

ROOT=Path(__file__).resolve().parent.parent
CONTROL_SKU='RC-TWN28HN'
CONTROL_TEXT='''Royal Clima RC-TWN28HN
Инверторный тип: on-off
Энергоэффективность при нагреве: A (COP 3,74)
Энергоэффективность при охлаждении: A (EER 3,21)
Фильтр тонкой очистки: Да
Дополнительные функции: Самоочистка, AUTO, ночной, TURBO, самодиагностика
Тип внутреннего блока: Настенная установка
Вид кондиционера: Сплит-система
Наружный блок: Да
Антибактериальный фильтр: Да
Мощность охлаждения: 2.85 кВт
Мощность нагрева: 2.92 кВт
Площадь помещения: 28.5 м²
Цвет: белый'''


def requirements():
    d=json.loads((ROOT/'data/production_dry_run/run_20260909_162735_772653.json').read_text())
    return d['procurements'][0]['items'][0]['requirements']


def item():return {'item_name':'Кондиционер бытовой','structured_requirements':requirements()}

def page(text=CONTROL_TEXT,**kw):
    return {'url':'https://manufacturer.example/product','text':text,'source_type':'manufacturer','source_verified':True,**kw}

def req(p,v,**kw):return {'parameter':p,'required_value':v,**kw}


class StrictControlTests(unittest.TestCase):
    def test_13_requirements_all_confirmed(self):
        result=assess_model(requirements(),[page()],CONTROL_SKU)
        self.assertEqual(len(result['requirements_check']),13)
        self.assertTrue(all(c['result']==CORRESPONDS for c in result['requirements_check']))
        self.assertEqual(result['status_ru'],'Полностью соответствует ТЗ')
        self.assertTrue(all(c['evidence'] and c['source'] and c['found_value'] for c in result['requirements_check']))

    def test_missing_one_parameter(self):
        r=assess_model(requirements(),[page(CONTROL_TEXT.replace('\nЦвет: белый',''))],CONTROL_SKU)
        self.assertEqual(r['status_ru'],'Соответствие не подтверждено')
        self.assertEqual(sum(c['result']==CORRESPONDS for c in r['requirements_check']),12)

    def test_incompatible_inverter(self):
        r=assess_model(requirements(),[page(CONTROL_TEXT.replace('on-off','инверторный'))],CONTROL_SKU)
        self.assertEqual(r['status_ru'],'Не соответствует ТЗ')
        self.assertEqual(r['requirements_check'][0]['result'],MISMATCH)

    def test_low_power_mismatch(self):
        r=assess_model(requirements(),[page(CONTROL_TEXT.replace('2.85 кВт','2.5 кВт'))],CONTROL_SKU)
        self.assertEqual(r['requirements_check'][9]['result'],MISMATCH)

    def test_missing_function_not_confirmed(self):
        r=assess_model(requirements(),[page(CONTROL_TEXT.replace(', самодиагностика',''))],CONTROL_SKU)
        self.assertEqual(r['requirements_check'][4]['result'],UNKNOWN)

    def test_regression_not_seeded_in_discovery(self):
        provider=Mock();provider.search.return_value=[]
        with tempfile.TemporaryDirectory() as d:
            r=discover_models(item(),provider=provider,cache_dir=Path(d))
        self.assertEqual(r['candidates_found'],0);self.assertIsNone(r['selected_model'])
        self.assertTrue(all(CONTROL_SKU not in q for q in r['queries_used']))


class NumericTests(unittest.TestCase):
    def test_operators(self):
        for operator,target,actual,expected in (
            ('minimum','2.6 кВт','2.64 кВт',CORRESPONDS),('minimum','2.6 кВт','2.5 кВт',MISMATCH),
            ('maximum','5 г','4 г',CORRESPONDS),('maximum','5 г','6 г',MISMATCH),
            ('greater_than','5 г','5 г',MISMATCH),('less_than','5 г','5 г',MISMATCH),
            ('greater_than','5 г','6 г',CORRESPONDS),('less_than','5 г','4 г',CORRESPONDS),
            ('equals','5 г','5 г',CORRESPONDS),('equals','5 г','4 г',MISMATCH),
            ('range','1-5 г','3 г',CORRESPONDS),('range','от 1 до 5 г','6 г',MISMATCH),
            ('range','1–5 г','2–4 г',CORRESPONDS),('range','1–5 г','4–6 г',UNKNOWN)):
            with self.subTest(operator=operator,target=target,actual=actual):
                self.assertEqual(compare(target,actual,'generic',operator),expected)

    def test_safe_unit_conversion(self):
        self.assertEqual(compare('2,6 кВт','2640 Вт','power','minimum'),CORRESPONDS)
        self.assertEqual(compare('0.01 г','10 мг','mass'),CORRESPONDS)
        self.assertEqual(compare('20 мм','2 см','length'),CORRESPONDS)

    def test_units_missing_or_incompatible(self):
        for actual in ('2.64','2.64 кг','2.64 неизвестная_единица'):
            self.assertEqual(compare('2.6 кВт',actual,'power','minimum'),UNKNOWN)

    def test_separate_units(self):
        self.assertEqual(compare('2.6','2640','power','minimum','кВт','Вт'),CORRESPONDS)

    def test_same_page_number_is_not_proof(self):
        p=page('M283fdn\nМощность: 5 Вт\nЦена: 2640 руб\nРекомендация: 2.64 кВт')
        r=check_requirement(req('Мощность','2.6 кВт',operator='minimum'),[p],'M283fdn')
        self.assertEqual(r['result'],MISMATCH)
        self.assertEqual(r['found_value'],'5 Вт')

    def test_ambiguous_value_not_guessed(self):
        self.assertEqual(compare('2.6 кВт','до 2.8 кВт','power','minimum'),UNKNOWN)
        self.assertEqual(compare('2.6 кВт','2.6 ± 0.5 кВт','power','minimum'),UNKNOWN)


class SKUTests(unittest.TestCase):
    def test_supported_skus(self):
        for sku in ('RC-TWN28HN','UE32T5300','M283fdn','4303dw','HCB 123'):
            with self.subTest(sku=sku):self.assertIn(sku,extract_skus('Модель: '+sku))

    def test_exact_skus_not_merged(self):
        text='RC-TWN28HN RC-TWN28HN/IN RC-TWN28HN/OUT'
        self.assertEqual(len(extract_skus(text)),3)
        self.assertFalse(same_exact_model('RC-TWN28HN','RC-TWN28HN/IN'))

    def test_noise_excluded(self):
        text='Закупка 100205573126100053 ОКПД2 28.25.12.130 ИНН 1234567890 2026-09-10 21 000 руб 2.64 кВт 2600W IP20 USB3.0 HDMI2.1 артикул закупки AB-123'
        self.assertEqual(extract_skus(text),[])

    def test_all_discovery_channels(self):
        for field,value in (('title','Samsung UE32T5300'),('snippet','HP M283fdn'),('url','https://shop.ru/4303dw'),('page_title','HCB 123'),('page_text','Royal Clima RC-TWN28HN')):
            with self.subTest(field=field):
                r=candidates_from_results([{'url':'https://shop.ru/product',field:value}])
                self.assertTrue(r)
                self.assertTrue(all(k in r[0] for k in ('brand','model','sku','source_url','source_title')))

    def test_url_suffix_preserved(self):
        rows=candidates_from_results([{'url':'https://shop.ru/RC-TWN28HN/OUT'}])
        self.assertEqual([c['sku'] for c in rows],['RC-TWN28HN/OUT'])

    def test_snippet_not_evidence(self):
        r=assess_model([req('Цвет','белый')],[page('UE32T5300\nЦвет: белый',content_kind='search_snippet')],'UE32T5300')
        self.assertEqual(r['status'],'not_confirmed')

    def test_multimodel_page_rejected(self):
        self.assertFalse(page_matches(page('UE32T5300 M283fdn\nЦвет: белый'),'UE32T5300'))


class CategoryTests(unittest.TestCase):
    def test_television(self):
        r=assess_model([req('Диагональ','32 дюйм'),req('Интерфейс','HDMI')],
            [page('Samsung UE32T5300\nДиагональ: 32 дюйм\nИнтерфейс: HDMI')],'UE32T5300')
        self.assertEqual(r['status'],'fully_compliant')

    def test_printer(self):
        for sku in ('M283fdn','4303dw'):
            r=assess_model([req('Тип печати','лазерный'),req('Цветность','цветной'),req('Автоматическая двусторонняя печать','да')],
                [page(f'HP {sku}\nТип печати: лазерная печать\nЦветность: цветная\nДуплекс: да')],sku)
            self.assertEqual(r['status'],'fully_compliant')

    def test_laboratory_scales(self):
        r=assess_model([req('Наибольший предел взвешивания','120 г',operator='minimum'),req('Дискретность','0.01 г',operator='maximum')],
            [page('Kern HCB 123\nНаибольший предел взвешивания: 0.12 кг\nДискретность: 10 мг')],'HCB 123')
        self.assertEqual(r['status'],'fully_compliant')

    def test_explicit_inverter_negation(self):
        self.assertEqual(compare('не требуется','инверторный','inverter'),MISMATCH)
        self.assertEqual(compare('не требуется','on-off','inverter'),CORRESPONDS)

    def test_textual_mismatch(self):
        self.assertEqual(compare('настенный','напольный','mounting'),MISMATCH)
        self.assertEqual(compare('настенный','настенная установка','mounting'),CORRESPONDS)


class QueryTests(unittest.TestCase):
    def test_control_semantic_queries(self):
        q=generate_queries(item())
        self.assertEqual(len(q),3)
        self.assertIn('неинверторный',q[0]);self.assertIn('Сплит-система',q[0])
        self.assertIn('антибактериальный фильтр',q[1]);self.assertIn('самоочистка',q[1])
        self.assertFalse(any('Требуется' in x or 'datasheet' in x for x in q))

    def test_exact_separate_from_equivalents(self):
        q=generate_queries({'name':'Принтер HP M283fdn или эквивалент','requirements':[req('Тип печати','лазерный'),req('Дуплекс','да')]})
        self.assertIn('M283fdn',q[0]);self.assertNotIn('M283fdn',' '.join(q[1:]));self.assertNotIn('HP',' '.join(q[1:]))

    def test_other_categories_semantics(self):
        for name,requirement,phrase in (('Принтер',req('Тип печати','лазерный'),'лазерный'),
            ('Весы лабораторные',req('Тип конструкции','настольные'),'настольные'),('Телевизор',req('Интерфейс','HDMI'),'HDMI')):
            self.assertIn(phrase,' '.join(generate_queries({'name':name,'requirements':[requirement]})))

    def test_two_queries_when_candidates_sufficient(self):
        provider=Mock();provider.search.return_value=[{'url':'https://shop.ru/'+sku,'title':'Кондиционер '+sku} for sku in ('M283fdn','4303dw','UE32T5300')]
        provider.fetch.return_value=''
        with tempfile.TemporaryDirectory() as d:
            r=discover_models(item(),provider=provider,cache_dir=Path(d))
        self.assertEqual(r['live_queries_count'],2);self.assertEqual(provider.search.call_count,2)

    def test_reserve_when_insufficient(self):
        provider=Mock();provider.search.return_value=[]
        with tempfile.TemporaryDirectory() as d:r=discover_models(item(),provider=provider,cache_dir=Path(d),query_limit=100)
        self.assertEqual(r['live_queries_count'],3)


class EvidenceTests(unittest.TestCase):
    def test_html_table_extracts_rows(self):
        p=html_page('<h1>Samsung UE32T5300</h1><table><tr><td>Цвет</td><td>белый</td></tr></table>','https://shop.ru/tv')
        self.assertEqual(assess_model([req('Цвет','белый')],[p],'UE32T5300')['status'],'fully_compliant')

    def test_source_priority_not_inferred_from_marketing_words(self):
        self.assertEqual(source_priority(page('official manufacturer каталог',source_verified=False)),4)
        self.assertEqual(source_priority(page(source_type='official_document')),1)

    def test_conflicting_sources_not_confirmed(self):
        p=page('UE32T5300\nЦвет: белый');other=page('UE32T5300\nЦвет: черный',url='https://shop.ru/item')
        r=assess_model([req('Цвет','белый')],[p,other],'UE32T5300')
        self.assertEqual(r['status'],'not_confirmed')

    def test_pdf_bridge_uses_existing_extractor(self):
        extractor=Mock(return_value={'pages':[{'text':'UE32T5300\nЦвет: белый','page_number':2,'read_method':'text_layer'}]})
        pages=document_pages(Path('mock.pdf'),'https://maker.example/manual.pdf',extractor=extractor)
        r=assess_model([req('Цвет','белый')],pages,'UE32T5300')
        self.assertEqual(r['status'],'fully_compliant');self.assertEqual(r['requirements_check'][0]['source_page'],2)
        extractor.assert_called_once()

    def test_low_confidence_ocr_not_confirmed(self):
        extractor=Mock(return_value={'pages':[{'text':'UE32T5300\nЦвет: белый','read_method':'ocr','average_confidence':30}]})
        p=document_pages(Path('mock.pdf'),'https://maker.example/manual.pdf',extractor=extractor)
        self.assertEqual(assess_model([req('Цвет','белый')],p,'UE32T5300')['status'],'not_confirmed')

    def test_optional_unknown_does_not_block(self):
        r=assess_model([req('Цвет','белый'),req('Wi-Fi','да',mandatory=False)],[page('UE32T5300\nЦвет: белый')],'UE32T5300')
        self.assertEqual(r['status'],'fully_compliant')


class PriceTests(unittest.TestCase):
    def test_exact_labeled_price(self):
        p=page('M283fdn\nЦена: 21 000 руб\nВ наличии',url='https://shop.ru/item')
        self.assertEqual(public_offer(p,'M283fdn')['price'],21000)

    def test_unrelated_amount_not_price(self):
        self.assertIsNone(public_offer(page('M283fdn\nДоставка: 500 руб\nРассрочка 2000 руб'),'M283fdn'))

    def test_other_sku_price_not_reused(self):
        self.assertIsNone(public_offer(page('RC-TWN28HN/OUT\nЦена: 10000 руб'),'RC-TWN28HN'))

    def test_jsonld_product_offer(self):
        raw='<h1>HP M283fdn</h1><script type="application/ld+json">'+json.dumps({'@type':'Product','sku':'M283fdn','offers':{'@type':'Offer','price':21000,'priceCurrency':'RUB','availability':'https://schema.org/InStock'}})+'</script>'
        p=html_page(raw,'https://shop.ru/item')
        self.assertEqual(public_offer(p,'M283fdn')['price'],21000)
        self.assertTrue(public_offer(p,'M283fdn')['available'])

    def test_ambiguous_prices_return_null(self):
        self.assertIsNone(public_offer(page('M283fdn\nЦена: 100 руб\nЦена: 200 руб'),'M283fdn'))

    def test_fully_confirmed_discovery_selection(self):
        provider=Mock();provider.search.return_value=[{'title':CONTROL_SKU,'url':'https://shop.ru/item'}]
        provider.fetch.return_value=page(CONTROL_TEXT+'\nЦена: 21000 руб\nВ наличии',url='https://shop.ru/item')
        with tempfile.TemporaryDirectory() as d:r=discover_models(item(),provider=provider,cache_dir=Path(d))
        self.assertIsNotNone(r['selected_model'])
        self.assertIsNone(r['selected_model']['purchase_price'])
        self.assertEqual(r['selected_model_public_price'],21000)

class AdditionalSafetyTests(unittest.TestCase):
    def test_2640_watts_control_threshold(self):
        r=assess_model(requirements(),[page(CONTROL_TEXT.replace('2.85 кВт','2640 Вт'))],CONTROL_SKU)
        self.assertEqual(r['requirements_check'][9]['result'],CORRESPONDS)

    def test_numeric_unit_table_column(self):
        p=html_page('<h1>M283fdn</h1><table><tr><td>Мощность</td><td>2640</td><td>Вт</td></tr></table>','https://shop.ru/item')
        c=check_requirement(req('Мощность','2.6 кВт',operator='minimum'),[p],'M283fdn')
        self.assertEqual(c['result'],CORRESPONDS)

    def test_unknown_enum_not_invented_mismatch(self):
        self.assertEqual(compare('настенный','универсальное крепление','mounting'),UNKNOWN)

    def test_three_unrelated_skus_do_not_skip_reserve(self):
        provider=Mock();provider.search.return_value=[{'url':'https://shop.ru/'+sku,'title':'Принтер '+sku} for sku in ('M283fdn','4303dw','PR-777')]
        provider.fetch.return_value=''
        with tempfile.TemporaryDirectory() as d:r=discover_models(item(),provider=provider,cache_dir=Path(d))
        self.assertEqual(r['live_queries_count'],3)

    def test_single_page_multiple_prices_not_model_specific(self):
        self.assertIsNone(public_offer(page('M283fdn 4303dw\nЦена: 100 руб'),'M283fdn'))

    def test_dimensions_not_models(self):
        self.assertEqual(extract_skus('1920x1080 802.11n 21000 2026-09-10'),[])

    def test_empty_requirements_not_fully_compliant(self):
        self.assertEqual(assess_model([],[page()],CONTROL_SKU)['status'],'not_confirmed')

    def test_min_price_among_confirmed_candidates(self):
        provider=Mock()
        provider.search.return_value=[{'title':'Принтер '+sku,'url':'https://shop.ru/'+sku} for sku in ('M283fdn','4303dw')]
        def fetch(url):
            sku=url.rsplit('/',1)[-1]
            return {'url':url,'text':f'{sku}\nЦвет: белый\nЦена: '+('20000' if sku=='M283fdn' else '15000')+' руб\nВ наличии'}
        provider.fetch.side_effect=fetch
        with tempfile.TemporaryDirectory() as d:
            r=discover_models({'name':'Принтер','requirements':[req('Цвет','белый')]},provider=provider,cache_dir=Path(d))
        self.assertEqual(r['selected_model']['sku'],'4303dw')
        self.assertIsNone(r['selected_model']['purchase_price'])

    def test_cheaper_unconfirmed_candidate_not_selected(self):
        provider=Mock();provider.search.return_value=[{'title':'Принтер '+sku,'url':'https://shop.ru/'+sku} for sku in ('M283fdn','4303dw')]
        def fetch(url):
            sku=url.rsplit('/',1)[-1]
            return {'url':url,'text':f'{sku}\n'+('Цвет: белый\nЦена: 20000' if sku=='M283fdn' else 'Цена: 1000')+' руб\nВ наличии'}
        provider.fetch.side_effect=fetch
        with tempfile.TemporaryDirectory() as d:r=discover_models({'name':'Принтер','requirements':[req('Цвет','белый')]},provider=provider,cache_dir=Path(d))
        self.assertEqual(r['selected_model']['sku'],'M283fdn')

    def test_no_price_no_budget_selection(self):
        provider=Mock();provider.search.return_value=[{'title':CONTROL_SKU,'url':'https://shop.ru/item'}];provider.fetch.return_value=page()
        with tempfile.TemporaryDirectory() as d:r=discover_models(item(),provider=provider,cache_dir=Path(d))
        self.assertEqual(r['fully_compliant_count'],1);self.assertIsNone(r['selected_model'])

    def test_malformed_structured_offer_ignored(self):
        p=page('M283fdn',structured_products=[{'@type':'Product','sku':'M283fdn','offers':'not an offer'}])
        self.assertIsNone(public_offer(p,'M283fdn'))

    def test_non_finite_price_ignored(self):
        p=page('M283fdn',structured_products=[{'@type':'Product','sku':'M283fdn','offers':{'price':'Infinity','priceCurrency':'RUB'}}])
        self.assertIsNone(public_offer(p,'M283fdn'))
