"""Explicit technical vocabulary; no fuzzy matching or inferred capabilities."""
FIELDS={
 'heating_capacity':('режим обогрева','нагрев','теплопроизводительность','мощность при обогреве','мощность в режиме нагрева','мощность обогрева','мощность нагрева','heating capacity','производительность тепло'),
 'cooling_capacity':('охлаждение','холодопроизводительность','мощность охлаждения','производительность по холоду','мощность в режиме охлаждения','cooling capacity','производительность холод'),
 'room_area':('рекомендуемая площадь','площадь помещения','обслуживаемая площадь','площадь охлаждения','площадь','площадь м²','площадь помещения (м2)','площадь помещения (м²)','площадь обслуживания'),
 'antibacterial_filter':('антибактериальный фильтр','фильтр с антибактериальным эффектом','antibacterial filter'),
 'fine_filter':('фильтр тонкой очистки','fine filter','фильтр тонкой очистки воздуха','фильтры тонкой очистки воздуха'),
 'outdoor_unit':('наружный блок','внешний блок','outdoor unit','вид блока кондиционера'),
}
FUNCTIONS={
 'self_clean':('самоочистка','автоматическая очистка','автоочистка','self clean','self-clean','самоочистка внутреннего блока'),
 'turbo_mode':('турбо','turbo','режим турбо','интенсивный режим','режим повышенной мощности'),
 'sleep_mode':('ночной режим','sleep','sleep mode','ночной'),
 'self_diagnosis':('самодиагностика','self-diagnosis','self diagnosis','автодиагностика'),
 'auto_mode':('auto','автоматический режим','автоматический выбор режима'),
}
FIELD_ALIASES={v:k for k,values in {**FIELDS,**FUNCTIONS}.items() for v in (k,*values)}
SOURCE_TYPES={'manufacturer':'OFFICIAL_MANUFACTURER','official_document':'OFFICIAL_MANUAL','official_manual':'OFFICIAL_MANUAL','official_datasheet':'OFFICIAL_DATASHEET','official_distributor':'OFFICIAL_DISTRIBUTOR','professional_store':'PROFESSIONAL_STORE','other':'OTHER'}

FIELD_ALIASES.update({'energy efficiency cooling':'energy_efficiency_cooling','energy efficiency heating':'energy_efficiency_heating'})
