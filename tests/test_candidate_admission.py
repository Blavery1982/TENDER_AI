import unittest

from model_search.candidate_admission import (
    CONFIRMED_COMPLIANT, LIKELY_COMPLIANT, REJECTED, NOT_ENOUGH_EVIDENCE,
    classify_candidate, classify_requirement_criticality,
)


def requirement(name, value='да', **extra):
    return {'parameter':name,'value':value,**extra}


def candidate(results):
    return {'sku':'TEST-100','page_matches':True,
            'requirements_check':[{'result':result,'found_value':'x','source':'https://shop.ru/x'}
                                  for result in results]}


class CandidateAdmissionTests(unittest.TestCase):
    def test_all_customer_requirements_confirmed(self):
        requirements=[requirement('Тип товара'),requirement('Цвет')]
        self.assertEqual(classify_candidate(candidate(['corresponds']*2),requirements)
                         ['candidate_admission_status'],CONFIRMED_COMPLIANT)

    def test_all_critical_confirmed_secondary_unknown_is_likely(self):
        requirements=[requirement('Тип товара'),requirement('Мощность','не менее 2 кВт'),
                      requirement('Комплектация',criticality='secondary'),
                      requirement('Гарантия',criticality='secondary')]
        result=classify_candidate(candidate(['corresponds','corresponds',
                                             'could_not_confirm','could_not_confirm']),requirements)
        self.assertEqual(result['candidate_admission_status'],LIKELY_COMPLIANT)

    def test_critical_mismatch_is_rejected(self):
        requirements=[requirement('Инверторный тип кондиционера','Нет')]
        result=classify_candidate(candidate(['does_not_comply']),requirements)
        self.assertEqual(result['candidate_admission_status'],REJECTED)
        self.assertEqual(result['critical_mismatches'][0]['requirement'],
                         'Инверторный тип кондиционера')

    def test_ninety_percent_cannot_hide_critical_violation(self):
        requirements=[requirement('Инверторный тип кондиционера','Нет')]+[
            requirement(f'Свойство {n}',criticality='secondary') for n in range(9)]
        result=classify_candidate(candidate(['does_not_comply']+['corresponds']*9),requirements)
        self.assertEqual(result['candidate_admission_status'],REJECTED)

    def test_half_confirmed_can_be_likely_with_all_critical_proven(self):
        requirements=[requirement('Тип товара'),requirement('Мощность','не менее 2 кВт')]+[
            requirement('Гарантия',criticality='secondary'),
            requirement('Комплектация',criticality='secondary')]
        result=classify_candidate(candidate(['corresponds','corresponds',
                                             'could_not_confirm','could_not_confirm']),requirements)
        self.assertEqual(result['candidate_admission_status'],LIKELY_COMPLIANT)

    def test_five_confirmed_eight_unknown_is_likely(self):
        requirements=[requirement('Вид товара')]+[
            requirement(f'Параметр {n}',criticality='secondary') for n in range(12)]
        checks=['corresponds']*5+['could_not_confirm']*8
        result=classify_candidate(candidate(checks),requirements)
        self.assertEqual(result['candidate_admission_status'],LIKELY_COMPLIANT)
        self.assertEqual(result['requirements_unknown'],8)

    def test_twelve_confirmed_one_mismatch_is_rejected(self):
        requirements=[requirement('Вид товара')]+[
            requirement(f'Параметр {n}',criticality='secondary') for n in range(12)]
        result=classify_candidate(candidate(['corresponds']*12+['does_not_comply']),requirements)
        self.assertEqual(result['candidate_admission_status'],REJECTED)

    def test_one_fact_is_not_automatically_likely(self):
        requirements=[requirement('Вид товара')]+[
            requirement(f'Параметр {n}',criticality='secondary') for n in range(4)]
        result=classify_candidate(candidate(['corresponds']+['could_not_confirm']*4),requirements)
        self.assertEqual(result['candidate_admission_status'],NOT_ENOUGH_EVIDENCE)

    def test_unknown_is_not_mismatch(self):
        requirements=[requirement('Вид товара'),requirement('Мощность')]
        result=classify_candidate(candidate(['corresponds','could_not_confirm']),requirements)
        self.assertEqual(result['requirements_mismatched'],0)
        self.assertNotEqual(result['candidate_admission_status'],REJECTED)

    def test_bound_from_customer_tz_is_critical(self):
        result=classify_requirement_criticality(requirement('Высота','не более 190 см'))
        self.assertTrue(result['critical'])


if __name__=='__main__':unittest.main()
