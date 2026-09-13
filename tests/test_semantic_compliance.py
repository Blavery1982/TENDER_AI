import unittest
from model_search.compliance import compare,assess_model,parameter_key
from model_search.product_evidence import html_page

def check(parameter,value,text,**kw):
 return assess_model([{'parameter':parameter,'value':value,**kw}],[{'heading':'LG DC07RH','url':'https://shop.ru/product','text':text}],'DC07RH')['requirements_check'][0]

class SemanticTests(unittest.TestCase):
 def test_units(self):
  for v in ('2.6 кВт','2,6 кВт','2600 Вт','2 600 W'):
   self.assertEqual(compare('2.6 кВт',v,'cooling_capacity'),'corresponds')
 def test_capacity_alias(self):
  self.assertEqual(check('Мощность охлаждения','2.6 кВт','Cooling capacity 2640 W',operator='>=')['result'],'corresponds')
 def test_consumption_not_capacity(self):
  self.assertEqual(check('Мощность охлаждения','2.6 кВт','Потребляемая мощность охлаждения: 0.8 кВт')['result'],'could_not_confirm')
 def test_functions(self):
  for f in ('ночной режим','турбо','самоочистка'):
   r=check(f,'да','Режимы: Sleep, Turbo, Self Clean');self.assertEqual(r['result'],'corresponds');self.assertTrue(r['evidence'])
 def test_composite_functions(self):
  r=check('Дополнительные функции','ночной режим, турбо, самоочистка','Режимы: Sleep, Turbo, Self Clean')
  self.assertEqual(r['result'],'corresponds');self.assertEqual(len(r['function_checks']),3)
  self.assertEqual(len(set(x['evidence'] for x in r['function_checks'])),3)
 def test_missing_function(self):
  self.assertEqual(check('Дополнительные функции','ночной режим, турбо','Режимы: Sleep')['result'],'could_not_confirm')
 def test_negative_function(self):
  self.assertEqual(check('турбо','да','Режимы: Sleep, Turbo нет')['result'],'does_not_comply')
 def test_optional_function(self):
  self.assertEqual(check('турбо','да','Режимы: Turbo опционально')['result'],'could_not_confirm')
 def test_generic_energy(self):
  for mode in ('нагрева','охлаждения'):
   self.assertEqual(check('Класс энергоэффективности в режиме '+mode,'не ниже А','Класс энергоэффективности: A')['result'],'could_not_confirm')
 def test_different_sku(self):
  p=html_page('<h1>LG DC07RH</h1><div class="related"><h2>LG DC09RH</h2><p>Cooling capacity: 3000 W</p></div>','https://shop.ru/product')
  r=assess_model([{'parameter':'Мощность охлаждения','value':'2.6 кВт'}],[p],'DC07RH')
  self.assertEqual(r['requirements_check'][0]['result'],'could_not_confirm')
 def test_kit_not_component(self):
  p={'heading':'SAS09BN3-AI','url':'https://shop.ru/product','text':'Мощность охлаждения: 3000 W'}
  r=assess_model([{'parameter':'Мощность охлаждения','value':'2.6 кВт'}],[p],'SAS09BN3-AI/SAU09BN3-AI-LE')
  self.assertEqual(r['requirements_check'][0]['result'],'could_not_confirm')
 def test_official_priority(self):
  pages=[{'heading':'DC07RH','url':'https://example.ru/'+str(i),'source_verified':True,'source_type':typ,'text':'Цвет: '+color} for i,(typ,color) in enumerate((('manufacturer','белый'),('professional_store','черный')))]
  r=assess_model([{'parameter':'Цвет','value':'белый'}],pages,'DC07RH')['requirements_check'][0]
  self.assertEqual(r['result'],'corresponds');self.assertEqual(r['source_type'],'OFFICIAL_MANUFACTURER');self.assertTrue(r['lower_priority_observations'])
 def test_equal_priority_conflict(self):
  pages=[{'heading':'DC07RH','url':'https://example.ru','text':'Цвет: '+color} for color in ('белый','черный')]
  self.assertEqual(assess_model([{'parameter':'Цвет','value':'белый'}],pages,'DC07RH')['requirements_check'][0]['result'],'could_not_confirm')
 def test_btu_conversion(self):
  self.assertEqual(compare('2.6 кВт','9000 BTU/h','cooling_capacity','minimum'),'corresponds')
  self.assertEqual(compare('2.6 кВт','9000 BTU','cooling_capacity','minimum'),'could_not_confirm')
 def test_ranges_bounds(self):
  self.assertEqual(compare('25 м²','до 20 м²','room_area','minimum'),'does_not_comply')
  self.assertEqual(compare('25 м²','до 30 м²','room_area','minimum'),'could_not_confirm')
  self.assertEqual(compare('2-3 кВт','2600 W','cooling_capacity','range'),'corresponds')
  self.assertEqual(compare('3 кВт','2600 W','cooling_capacity','maximum'),'corresponds')
 def test_explicit_aliases(self):
  for word in ('нагрев','теплопроизводительность','мощность при обогреве'):self.assertEqual(parameter_key(word),'heating_capacity')
  self.assertEqual(parameter_key('ночной режим'),'sleep_mode')
