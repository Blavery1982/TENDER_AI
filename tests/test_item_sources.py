import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from documents.item_sources import resolve_item_sources
from documents.pipeline import audit_from_extraction
from model_search.price_readiness import (
    MODEL_IDENTIFIER_REVIEW_REQUIRED,
    PRICE_SEARCH_READY,
    classify_price_search_readiness,
)
from model_search.live_discovery import discover_models, generate_queries
from model_search.search_mode import determine_model_search_mode
from pipeline.batch_orchestrator import process_item


def doc(text, kind='contract_appendix', **kw):
    return {'document_name':'Приложение.docx', 'document_type':[kind], 'text':text,
            'pages':[{'page_number':2,'text':text}], **kw}


class ItemSourcesTests(unittest.TestCase):
    def resolve(self, text='', docs=(), **kw):
        return resolve_item_sources({'name':'Кондиционер','description':text, **kw}, docs)

    def test_01_exact_specification(self):
        r=self.resolve('Модель: Royal Clima RC-TWN28HN')
        self.assertEqual(r['model_search_mode'],'EXACT_MODEL_ONLY')
        self.assertEqual(r['model_source'],'CUSTOMER_SPECIFICATION')

    def test_02_equivalent(self):
        r=self.resolve('Royal Clima RC-TWN28HN или эквивалент')
        self.assertEqual(r['model_search_mode'],'EXACT_MODEL_OR_EQUIVALENT')
        self.assertIn(r['customer_required_model'],generate_queries(r)[0])

    def test_03_characteristics_and_negation(self):
        r=self.resolve('', structured_requirements=[{'parameter':'Мощность','value':'≥ 2.6 кВт'}, {'parameter':'Wi-Fi','value':'не требуется'}])
        self.assertEqual(r['model_search_mode'],'MODEL_DISCOVERY_REQUIRED')
        self.assertEqual(r['requirements'][0]['unit'],'кВт')
        self.assertEqual(r['requirements'][0]['operator'],'minimum')
        self.assertEqual(r['requirements'][1]['operator'],'not_required')
        self.assertTrue(all(x['requirement_source']=='EAT_SPECIFICATION' for x in r['requirements']))

    def test_04_contract_model(self):
        r=self.resolve('В соответствии с техническим заданием', [doc('Спецификация\nМодель: Royal Clima RC-TWN28HN\nМощность: 2.8 кВт')])
        self.assertEqual(r['model_search_mode'],'EXACT_MODEL_ONLY')
        self.assertEqual(r['model_source'],'CONTRACT_DOCUMENT')
        self.assertEqual(r['model_evidence'][0]['source_page'],2)

    def test_05_price_only_baseline(self):
        r=self.resolve('', [doc('Модель: Royal Clima RC-TWN28HN','commercial_offer')])
        self.assertIsNone(r['customer_required_model'])
        self.assertEqual(r['model_search_mode'],'MODEL_DISCOVERY_REQUIRED')
        self.assertEqual(r['supplier_baseline_model'],'Royal Clima RC-TWN28HN')
        self.assertEqual(r['distinct_suppliers_required'],3)
        self.assertFalse(r['alternative_policy']['silent_replacement_allowed'])
        self.assertIn('Royal Clima RC-TWN28HN', generate_queries(r)[0])

    def test_06_price_conflict_never_compliant(self):
        r=self.resolve('', [doc('Модель: Royal Clima RC-TWN28HN\nМощность: 2.8 кВт','commercial_offer')], structured_requirements=[{'parameter':'Мощность','value':'5 кВт'}])
        self.assertEqual(r['requirements'][0]['value'],'5 кВт')
        self.assertEqual(r['price_justification_requirements'][0]['value'],'2.8 кВт')
        self.assertIsNone(r['supplier_baseline_model'])
        provider=Mock();provider.search.return_value=[]
        with tempfile.TemporaryDirectory() as d, patch('model_search.live_discovery.time.sleep'):
            result=discover_models(r,provider=provider,cache_dir=Path(d))
        self.assertIsNone(result['selected_model'])
        self.assertEqual(result['fully_compliant_count'],0)

    def test_07_no_model_contract_characteristics(self):
        r=self.resolve('',[doc('Спецификация\nМощность: 2.8 кВт\nЦвет: белый')])
        self.assertEqual(r['model_search_mode'],'MODEL_DISCOVERY_REQUIRED')
        self.assertEqual(r['model_source'],'NOT_FOUND')

    def test_08_ambiguous(self):
        self.assertEqual(self.resolve('Возможно Royal Clima RC-TWN28HN')['model_search_mode'],'MODEL_MODE_REVIEW_REQUIRED')
        self.assertEqual(self.resolve()['model_search_mode'],'MODEL_MODE_REVIEW_REQUIRED')

    def test_09_expanded_offer(self):
        r=self.resolve('', structured_requirements=[{'parameter':'Мощность','value':'2.8 кВт'}], offerDescription='Модель: Royal Clima RC-TWN28HN')
        self.assertEqual(r['model_search_mode'],'EXACT_MODEL_ONLY')
        self.assertEqual(len(r['requirements']),1)

    def test_10_position_isolation(self):
        items=[{'name':'Кондиционер'},{'name':'Телевизор'}]
        documents=[doc('Спецификация\nПозиция 1\nМодель: Royal Clima RC-TWN28HN\nПозиция 2\nМодель: Samsung UE32T5300')]
        a=resolve_item_sources(items[0],documents,item_number=1,items=items)
        b=resolve_item_sources(items[1],documents,item_number=2,items=items)
        self.assertEqual(a['customer_required_model'],'Royal Clima RC-TWN28HN')
        self.assertEqual(b['customer_required_model'],'Samsung UE32T5300')

    def test_11_unassigned_document_review(self):
        items=[{'name':'Кондиционер'},{'name':'Кондиционер'}]
        r=resolve_item_sources(items[0],[doc('Спецификация\nМодель: Royal Clima RC-TWN28HN')],items=items)
        self.assertEqual(r['model_search_mode'],'MODEL_MODE_REVIEW_REQUIRED')
        self.assertIsNone(r['customer_required_model'])

    def test_12_customer_conflict_review(self):
        r=self.resolve('',[doc('Спецификация\nМощность: 2.8 кВт')], structured_requirements=[{'parameter':'Мощность','value':'5 кВт'}])
        self.assertEqual(r['model_search_mode'],'MODEL_MODE_REVIEW_REQUIRED')
        self.assertEqual(r['requirements'][0]['value'],'5 кВт')

    def test_13_price_section_overrides_filename_classification(self):
        r=self.resolve('',[doc('Модель: Royal Clima RC-TWN28HN',source_document_type=14)])
        self.assertIsNone(r['customer_required_model'])
        self.assertEqual(r['model_source'],'PRICE_JUSTIFICATION')

    def test_14_audit_to_orchestrator(self):
        item={'name':'Кондиционер'}
        docs=[doc('Спецификация\nМодель: Royal Clima RC-TWN28HN')]
        extraction={'document_results':docs,'combined_text':docs[0]['text'],'warnings':[],
            'documents_found':1,'documents_processed':1,'documents_failed':0,
            'extraction_summary':{'partial_documents':0},'procurement_id':'p'}
        audit=audit_from_extraction({'raw':{'lotItems':[item]}},extraction)
        with patch('pipeline.batch_orchestrator.discover_models') as discovery:
            r=process_item(item,1,'p',{},source_resolution=audit['items'][0],model_live=True)
        discovery.assert_not_called()
        self.assertEqual(r['model_search']['customer_required_model'],'Royal Clima RC-TWN28HN')

    def test_15_control_procurement(self):
        import json
        root=Path(__file__).resolve().parent.parent
        card=json.loads((root/'data/eat_single_31caee8a-cca2-4e2b-b773-42229d413d03.json').read_text())
        saved=json.loads((root/'data/production_dry_run/run_20260909_162735_772653.json').read_text())
        r=resolve_item_sources(card['raw']['lot']['lotItems'][0],saved['procurements'][0]['document_processing']['document_results'])
        self.assertEqual(r['model_search_mode'],'MODEL_DISCOVERY_REQUIRED')
        self.assertEqual(len(r['requirements']),23)
        self.assertIsNone(r['customer_required_model'])
        self.assertTrue({x['requirement_source'] for x in r['requirements']} <= {'CONTRACT_DOCUMENT', 'PRICE_JUSTIFICATION'})

    def test_16_unreadable_contract_review(self):
        r=self.resolve('',[doc('',status='missing')])
        self.assertEqual(r['model_search_mode'],'MODEL_MODE_REVIEW_REQUIRED')

    def test_17_baseline_reaches_supplier_stage_without_search(self):
        resolved=self.resolve('',[doc('Модель: Royal Clima RC-TWN28HN','commercial_offer')])
        r=process_item({'name':'Кондиционер'},1,'p',{},source_resolution=resolved)
        self.assertEqual(r['supplier_market_search']['baseline_model'],'Royal Clima RC-TWN28HN')
        self.assertEqual(r['supplier_market_search']['minimum_distinct_suppliers'],3)
        self.assertFalse(r['supplier_market_search']['live_search_performed'])

    def test_18_explicit_ban_prohibits_alternative_submission(self):
        r=self.resolve('Мощность: 2.8 кВт\nНе допускаются аналоги',
                       [doc('Модель: Royal Clima RC-TWN28HN','commercial_offer')])
        self.assertFalse(r['alternative_policy']['submission_allowed'])

    def test_19_model_in_other_document_is_price_ready_without_customer_claim(self):
        other = doc('Сведения о товаре\nМодель: RC-TWN28HN', 'other',
                    document_name='Информационный лист.pdf')
        resolved = self.resolve('', [other], name='Кондиционер')
        readiness = classify_price_search_readiness({'name': 'Кондиционер'}, resolved)
        self.assertEqual(readiness['classification'], PRICE_SEARCH_READY)
        self.assertEqual(readiness['model_value'], 'RC-TWN28HN')
        self.assertEqual(readiness['model_source'], 'OTHER_DOCUMENT')
        self.assertIsNone(readiness['customer_required_model'])
        self.assertIsNone(readiness['price_justification_model'])
        self.assertEqual(readiness['evidence'][0]['source_document'], 'Информационный лист.pdf')
        self.assertEqual(readiness['evidence'][0]['source_page'], 2)
        self.assertFalse(readiness['compliance_before_price_search'])

    def test_20_other_document_formats_are_source_agnostic(self):
        for suffix in ('pdf', 'docx', 'xlsx'):
            with self.subTest(suffix=suffix):
                other = doc('Артикул: UE32T5300', 'other',
                            document_name=f'Материалы.{suffix}')
                resolved = self.resolve('', [other])
                readiness = classify_price_search_readiness({'name': 'Телевизор'}, resolved)
                self.assertEqual(readiness['classification'], PRICE_SEARCH_READY)
                self.assertEqual(readiness['identifier'], 'UE32T5300')

    def test_21_multiple_models_in_other_document_require_review(self):
        other = doc('Модель: RC-TWN28HN\nМодель: UE32T5300', 'other')
        resolved = self.resolve('', [other])
        readiness = classify_price_search_readiness({'name': 'Кондиционер'}, resolved)
        self.assertEqual(readiness['classification'], MODEL_IDENTIFIER_REVIEW_REQUIRED)
        self.assertFalse(readiness['price_search_ready'])
        self.assertIn('разные модели', readiness['reason'])

    def test_22_current_source_version_is_preserved_by_search_mode(self):
        resolved = self.resolve('Модель: RC-TWN28HN')
        decision = determine_model_search_mode(resolved)
        self.assertEqual(decision['source_resolution_version'], resolved['source_resolution_version'])
        self.assertEqual(decision['customer_required_model'], 'RC-TWN28HN')
        self.assertEqual(decision['model_search_mode'], 'EXACT_MODEL_ONLY')
