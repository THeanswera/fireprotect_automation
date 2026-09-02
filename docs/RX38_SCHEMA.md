# RX38 Tconstr schema

Версия карты: 2026-09-02. Корпус: 4 уникальных RX38-проекта, 46 конструкций; каждый `Tconstr` содержит ровно 200 полей (0–199). Индексы `unknown` не интерпретируются и сохраняются verbatim.

## Уровень доказанности

- `confirmed`: 56
- `probable`: 14
- `unknown`: 130

`probable` не входит в безопасный writable-набор. Semantic confidence теперь
отделён от write safety. `WritePolicy` имеет значения `SAFE_DIRECT`,
`SAFE_WITH_COMPATIBILITY_CHECK`, `RESULT_ONLY`, `READ_ONLY`, `EXPERIMENTAL` и
`FORBIDDEN`. UNKNOWN/PROBABLE запрещены; confirmed output fields 44/54 имеют
`RESULT_ONLY`; field 0 имеет `READ_ONLY`; остальные расчётные inputs требуют
явной compatibility-проверки. Field 53 переведён из `unknown` в
`probable` после двух двунаправленных осевых возмущений, трёх GUI-
совпадений и сверки со встроенной справкой RX3; запись по-прежнему
запрещена.

`FieldSpec` также хранит evidence type/sources, controlled experiment ids,
corpus count, признаки exact identity/report/database/help match и reviewer
note. Наличие CONFIRMED-семантики само по себе не даёт права записи.

## Карта всех полей

| index | рабочее имя | исходное назначение | тип | единицы | направление | обязательность | источник | уверенность | комментарий |
|---:|---|---|---|---|---|---|---|---|---|
| 0 | record_type | Record discriminator | literal[Tconstr] | — | service | yes | All RX38 files | confirmed | — |
| 1 | mark | Construction mark/name | string | — | input | yes | RX38 files + RX3 report | confirmed | — |
| 2 | unknown_002 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 3 | mark_copy | Second stored copy of construction mark | string | — | service | yes | Exact equality with field 1 in all 46 corpus records | confirmed | The consumer role is undocumented; synchronizing it with field 1 is confirmed. |
| 4 | section_type_code | Numeric section-family code | integer | — | service | yes | RX38 cross-comparison | probable | One-to-one mapping with field 5 in the available corpus. |
| 5 | section_type | Section family | string | — | input | yes | RX38 files + RX3 report + help | confirmed | — |
| 6 | unknown_006 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 7 | unknown_007 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 8 | height_mm | Overall section height | decimal | mm | input | yes for steel Tconstr | RX38 ↔ rx3.rxdb exact match | confirmed | — |
| 9 | width_mm | Overall section width | decimal | mm | input | yes for steel Tconstr | RX38 ↔ rx3.rxdb exact match | confirmed | — |
| 10 | unknown_010 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 11 | web_thickness_mm | Web/wall thickness stored in tw position | decimal | mm | input | yes for steel Tconstr | RX38 ↔ rx3.rxdb exact match | confirmed | — |
| 12 | unknown_012 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 13 | flange_thickness_mm | Flange/wall thickness stored in tf position | decimal | mm | input | yes for steel Tconstr | RX38 ↔ rx3.rxdb exact match | confirmed | — |
| 14 | length_m | Length of one construction | decimal | m | input | yes for steel Tconstr | RX38 identities: field24=field21×field14/1000; RX3 report | confirmed | — |
| 15 | quantity | Number of constructions | decimal | pcs | input | yes for steel Tconstr | RX38 identities: field25=field24×field15; RX3 report | confirmed | — |
| 16 | unknown_016 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 17 | profile_standard | Profile assortment standard | string | — | input | yes for steel Tconstr | RX38 ↔ rx3.rxdb exact match | confirmed | — |
| 18 | profile_row_code | RX3 assortment row code | integer | — | service | yes | RX38 cross-comparison | probable | Not the SQLite primary key; exact internal meaning is unproved. |
| 19 | profile_name | Profile designation | string | — | input | yes for steel Tconstr | RX38 ↔ rx3.rxdb exact match | confirmed | — |
| 20 | area_mm2 | Cross-sectional area | decimal | mm² | calculated | yes for steel Tconstr | RX38 ↔ rx3.rxdb s×100 exact match; RX3 report | confirmed | — |
| 21 | heated_perimeter_mm | Heated perimeter along section contour | decimal | mm | calculated | yes for steel Tconstr | RX38 A/P identities + RX3 report/help | confirmed | — |
| 22 | ptm_mm | Reduced thickness A/P along contour | decimal | mm | calculated | yes for steel Tconstr | Exact identity field20/field21 in all RX38 records | confirmed | — |
| 23 | section_factor_per_m | Section factor P/A along contour | decimal | m⁻¹ | calculated | yes for steel Tconstr | Exact identity 1000/field22 in all RX38 records | confirmed | — |
| 24 | contour_area_one_m2 | Contour treatment area of one construction | decimal | m² | calculated | yes for steel Tconstr | Exact identity field21×field14/1000; RX3 report | confirmed | — |
| 25 | contour_area_total_m2 | Contour treatment area of all constructions | decimal | m² | calculated | yes for steel Tconstr | Exact identity field24×field15; RX3 report | confirmed | — |
| 26 | ix_m4 | Second moment of area Ix | decimal | m⁴ | calculated | yes for steel Tconstr | RX38 ↔ rx3.rxdb Ix×10⁻⁸ exact match | confirmed | — |
| 27 | iy_m4 | Second moment of area Iy | decimal | m⁴ | calculated | yes for steel Tconstr | RX38 ↔ rx3.rxdb Iy×10⁻⁸ exact match | confirmed | — |
| 28 | imin_m4 | Minimum second moment of area | decimal | m⁴ | calculated | yes for steel Tconstr | RX38 values + RX3 report | probable | Symmetric cases are exact; asymmetric cases need UI-controlled differential samples. |
| 29 | wx_m3 | Elastic section modulus Wx | decimal | m³ | calculated | yes for steel Tconstr | RX38 ↔ rx3.rxdb wx×10⁻⁶ exact match | confirmed | — |
| 30 | wy_m3 | Elastic section modulus Wy | decimal | m³ | calculated | yes for steel Tconstr | RX38 ↔ rx3.rxdb wy×10⁻⁶ exact match | confirmed | — |
| 31 | governing_steel_thickness_mm | Thickness used for steel strength selection | decimal | mm | input | yes for steel Tconstr | RX38 cross-comparison | probable | Often equals tf; needs a controlled nonstandard-section sample. |
| 32 | steel_density_kg_m3 | Steel density | decimal | kg/m³ | input | yes for steel Tconstr | RX3 report/help + rx3.xml default | confirmed | — |
| 33 | steel_yield_strength_mpa | Stored steel yield strength | decimal | MPa | input | yes for steel Tconstr | RX38 values + RX3 report/help | confirmed | — |
| 34 | steel_elastic_modulus_mpa | Steel elastic modulus | decimal | MPa | input | yes for steel Tconstr | RX38 values + RX3 report/help + rx3.xml | confirmed | — |
| 35 | steel_conductivity_d | Coefficient D of steel thermal-conductivity model | decimal | — | input | yes for steel Tconstr | Exact match with rx3.xml teploD | confirmed | — |
| 36 | steel_conductivity_e | Coefficient E of steel thermal-conductivity model | decimal | — | input | yes for steel Tconstr | Exact match with rx3.xml teploE | confirmed | — |
| 37 | steel_conductivity_f | Coefficient F of steel thermal-conductivity model | decimal | — | input | yes for steel Tconstr | Exact match with rx3.xml teploF | confirmed | — |
| 38 | steel_heat_capacity_d | Coefficient D of steel heat-capacity model | decimal | — | input | yes for steel Tconstr | Exact match with rx3.xml emkostD | confirmed | — |
| 39 | steel_heat_capacity_e | Coefficient E of steel heat-capacity model | decimal | — | input | yes for steel Tconstr | Exact match with rx3.xml emkostE | confirmed | — |
| 40 | steel_heat_capacity_f | Coefficient F of steel heat-capacity model | decimal | — | input | yes for steel Tconstr | Exact match with rx3.xml emcostF | confirmed | — |
| 41 | steel_emissivity | Steel surface emissivity | decimal | 1 | input | yes for steel Tconstr | Exact match with rx3.xml blacksteel + RX3 help | confirmed | — |
| 42 | steel_grade | Steel grade | string | — | input | yes for steel Tconstr | RX38 files + RX3 report/help | confirmed | — |
| 43 | unknown_043 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 44 | critical_temperature_c | Calculated critical steel temperature | decimal | °C | output | yes for steel Tconstr | RX38 ↔ EN 1993-1-2 load-level relation + RX3 report | confirmed | — |
| 45 | stress_state | Member loading/stress-state description | string | — | input | yes for steel Tconstr | RX38 values + RX3 help/report | confirmed | — |
| 46 | stress_state_code | Numeric loading/stress-state code | integer | — | service | yes | RX38 cross-comparison | probable | — |
| 47 | support_condition_code | Numeric support-condition code | integer | — | service | conditional | RX38 cross-comparison | probable | — |
| 48 | support_condition | Support condition | string | — | input | conditional | RX38 values + RX3 help/report | confirmed | — |
| 49 | axial_force_kn | Axial force N | decimal | kN | input | conditional | RX38 values + RX3 help/report | confirmed | — |
| 50 | major_axis_moment_knm | Bending moment about selected major axis | decimal | kN·m | input | conditional | RX38 differential values + RX3 help | probable | — |
| 51 | effective_length_m | Effective/calculated member length | decimal | m | calculated | yes for steel Tconstr | Exact identity field14×field141 for supported compression records + RX3 report | confirmed | — |
| 52 | load_level_mu0 | Load level used to determine critical temperature | decimal | 1 | calculated | yes for steel Tconstr | Exact EN 1993-1-2 critical-temperature relation with field44 + RX3 help | confirmed | — |
| 53 | beta_tem_modulus_reduction | RX3 beta_tem / elastic-modulus reduction coefficient at critical temperature | decimal | 1 | calculated | yes for steel Tconstr | RX3-EXP-01/01C/01D GUI equality + RX3 EN 1993-1-2 help | probable | Three controlled axial states equal GUI beta_tem after display rounding; broader stress-state applicability and a direct exported-report binding remain unverified. |
| 54 | unprotected_fire_resistance_min | Calculated unprotected fire resistance | decimal | min | output | yes for steel Tconstr | RX38 values + RX3 report | confirmed | — |
| 55 | required_fire_resistance_min | Required fire resistance R | decimal | min | input | yes for steel Tconstr | RX38 values + RX3 report/help | confirmed | — |
| 56 | unknown_056 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 57 | unknown_057 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 58 | unknown_058 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 59 | quantity_method | Method used for quantity/mass/area accounting | string | — | input | yes for steel Tconstr | RX38 text value | probable | — |
| 60 | unknown_060 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 61 | loading_axis | Selected reference loading axis | string | — | input | yes for steel Tconstr | RX38 text value + RX3 help | probable | — |
| 62 | unknown_062 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 63 | unknown_063 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 64 | unknown_064 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 65 | unknown_065 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 66 | mass_one_kg | Mass of one construction | decimal | kg | calculated | yes for steel Tconstr | Exact identity area×length×density; RX3 report | confirmed | — |
| 67 | mass_total_kg | Total mass of constructions | decimal | kg | calculated | yes for steel Tconstr | Exact identity field66×field15; RX3 report | confirmed | — |
| 68 | unknown_068 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 69 | unknown_069 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 70 | unknown_070 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 71 | unknown_071 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 72 | fireproofing_material | Fireproofing material name | string | — | input | yes for steel Tconstr | RX38 values + fpm.rxdb + RX3 report/help | confirmed | — |
| 73 | unknown_073 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 74 | unknown_074 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 75 | unknown_075 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 76 | normal_stress_mpa | Calculated normal stress | decimal | MPa | calculated | yes for steel Tconstr | RX38 values + RX3 report | probable | Three controlled axial states match the GUI stress value, but combined-stress records are not uniquely attributable. |
| 77 | unknown_077 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 78 | unknown_078 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 79 | unknown_079 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 80 | unknown_080 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 81 | unknown_081 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 82 | convection_coefficient | Convective heat-transfer coefficient | decimal | W/(m²·K) | input | yes for steel Tconstr | RX38 value + rx3.xml ac + RX3 help | confirmed | — |
| 83 | flame_emissivity | Flame emissivity | decimal | 1 | input | yes for steel Tconstr | RX38 value + rx3.xml blackflame + RX3 help | confirmed | — |
| 84 | view_factor | Radiation view/configuration factor | decimal | 1 | input | yes for steel Tconstr | RX38 value + rx3.xml ff + RX3 help | confirmed | — |
| 85 | shadow_effect_factor | Shadow-effect correction selector/factor | decimal | 1 | input | yes for steel Tconstr | RX38 values + rx3.xml kf + RX3 help | probable | — |
| 86 | box_heated_perimeter_mm | Heated perimeter for box protection | decimal | mm | calculated | yes for steel Tconstr | Geometry identity + RX3 report | confirmed | — |
| 87 | box_area_one_m2 | Box-protection area of one construction | decimal | m² | calculated | yes for steel Tconstr | Exact identity field86×field14/1000; RX3 report | confirmed | — |
| 88 | box_area_total_m2 | Box-protection area of all constructions | decimal | m² | calculated | yes for steel Tconstr | Exact identity field87×field15; RX3 report | confirmed | — |
| 89 | unknown_089 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 90 | plastic_modulus_x_m3 | Plastic section modulus Wpl,x | decimal | m³ | calculated | yes for steel Tconstr | RX38 values + RX3 report | probable | — |
| 91 | plastic_modulus_y_m3 | Plastic section modulus Wpl,y | decimal | m³ | calculated | yes for steel Tconstr | RX38 values + RX3 report | probable | — |
| 92 | unknown_092 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 93 | unknown_093 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 94 | unknown_094 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 95 | unknown_095 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 96 | unknown_096 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 97 | unknown_097 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 98 | unknown_098 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 99 | unknown_099 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 100 | unknown_100 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 101 | unknown_101 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 102 | unknown_102 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 103 | unknown_103 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 104 | fire_regime | Fire temperature regime | string | — | input | yes for steel Tconstr | RX38 values + rx3.xml + RX3 report/help | confirmed | — |
| 105 | unknown_105 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 106 | unknown_106 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 107 | unknown_107 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 108 | unknown_108 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 109 | unknown_109 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 110 | unknown_110 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 111 | unknown_111 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 112 | unknown_112 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 113 | box_ptm_mm | Reduced thickness A/P for box protection | decimal | mm | calculated | yes for steel Tconstr | Exact identity field20/field86 in all RX38 records | confirmed | — |
| 114 | box_section_factor_per_m | Section factor P/A for box protection | decimal | m⁻¹ | calculated | yes for steel Tconstr | Exact identity 1000/field113 in all RX38 records | confirmed | — |
| 115 | unknown_115 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 116 | unknown_116 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 117 | unknown_117 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 118 | unknown_118 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 119 | unknown_119 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 120 | unknown_120 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 121 | unknown_121 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 122 | unknown_122 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 123 | unknown_123 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 124 | unknown_124 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 125 | unknown_125 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 126 | unknown_126 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 127 | unknown_127 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 128 | unknown_128 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 129 | unknown_129 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 130 | unknown_130 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 131 | unknown_131 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 132 | unknown_132 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 133 | unknown_133 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 134 | unknown_134 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 135 | unknown_135 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 136 | unknown_136 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 137 | unknown_137 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 138 | unknown_138 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 139 | unknown_139 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 140 | unknown_140 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 141 | effective_length_factor | Effective-length factor | decimal | 1 | input | yes for steel Tconstr | Exact identity field51/field14 + support descriptions | confirmed | — |
| 142 | unknown_142 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 143 | unknown_143 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 144 | unknown_144 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 145 | unknown_145 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 146 | unknown_146 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 147 | unknown_147 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 148 | unknown_148 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 149 | unknown_149 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 150 | unknown_150 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 151 | unknown_151 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 152 | unknown_152 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 153 | unknown_153 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 154 | unknown_154 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 155 | unknown_155 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 156 | unknown_156 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 157 | unknown_157 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 158 | unknown_158 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 159 | unknown_159 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 160 | unknown_160 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 161 | unknown_161 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 162 | unknown_162 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 163 | unknown_163 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 164 | unknown_164 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 165 | unknown_165 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 166 | unknown_166 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 167 | unknown_167 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 168 | unknown_168 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 169 | unknown_169 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 170 | unknown_170 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 171 | unknown_171 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 172 | unknown_172 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 173 | unknown_173 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 174 | unknown_174 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 175 | unknown_175 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 176 | unknown_176 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 177 | unknown_177 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 178 | unknown_178 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 179 | unknown_179 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 180 | unknown_180 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 181 | unknown_181 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 182 | unknown_182 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 183 | unknown_183 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 184 | unknown_184 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 185 | unknown_185 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 186 | unknown_186 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 187 | unknown_187 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 188 | steel_temperature_model | Steel strength/stiffness temperature-dependence model | string | — | input | yes for steel Tconstr | Exact match with rx3.xml rx_steel_prop_text + RX3 report/help | confirmed | — |
| 189 | steel_temperature_model_code | Numeric code of steel temperature model | integer | — | service | yes | Exact match with rx3.xml rx_steel_prop_index | confirmed | — |
| 190 | unknown_190 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 191 | unknown_191 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 192 | unknown_192 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 193 | unknown_193 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 194 | unknown_194 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 195 | unknown_195 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 196 | unknown_196 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 197 | unknown_197 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 198 | unknown_198 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |
| 199 | unknown_199 | Not established | unknown | — | unknown | unknown | No admissible evidence | unknown | Preserved verbatim; do not write through the typed API. |

## Воспроизводимые дифференциальные факты

- `field20 / field21 = field22`; `1000 / field22 = field23` для всех 46 конструкций.
- `field21 × field14 / 1000 = field24`; `field24 × field15 = field25`.
- `field20 / field86 = field113`; `1000 / field113 = field114`.
- `field86 × field14 / 1000 = field87`; `field87 × field15 = field88`.
- `field20 × 10⁻⁶ × field14 × field32 = field66`; `field66 × field15 = field67`.
- Геометрия и Ix/Iy/Wx/Wy сопоставлены с `rx3.rxdb` на совпадающих профилях, включая `30 К1`, `18 Б2`, `160x160x8`.
- Позиции 35–41, 82–85, 104, 188–189 сопоставлены с именованными атрибутами `rx3.xml` и справкой.
- Для одного осевого шаблона три состояния N=`25.00/27.85/30.00 kN`
  дают двунаправленный GUI-отклик fields 44, 52, 53, 54, 76 и 77.
  Fields 44/52/54 сохраняют `confirmed`; field53 и field76 остаются
  `probable`; field77 остаётся `unknown`.

## Что пока не доказано

Большая часть строки относится к внутренним флагам, промежуточным характеристикам, комбинированным усилиям и моделям древесины. Одинаковое значение во всех проектах не доказывает назначения. Нужны контролируемые пары RX38, где в интерфейсе меняется ровно один параметр.

Mappings Mx/My/Qx/Qy остаются неподтверждёнными. Field 50 остаётся
`probable`; индексы My/Qx/Qy не назначаются по догадке. Поэтому ненулевое
значение любого из четырёх компонентов блокирует генерацию до записи файла.
Field 50=0 не доказывает, что шаблон осевой: нужен отдельный `AXIAL_ONLY`
evidence для всего calculation profile.

Field77 в трёх осевых состояниях точно пропорционален N с постоянным
коэффициентом около `3609.86830159046` для этого профиля/длины/
закрепления. Это только математическая гипотеза: физическое назначение и
единицы не доказаны.

Fields 44 и 54 могут физически сохранять значения старого шаблона, но они
помечаются `STALE_TEMPLATE_RESULT`, исключены из `Rx3Input` и принимаются
только из отдельного calculated-файла после GUI validation. UNKNOWN tokens
сохраняются verbatim и получают opaque fingerprint без интерпретации.
