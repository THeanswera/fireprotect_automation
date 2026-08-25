from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Confidence = Literal["confirmed", "probable", "unknown"]


@dataclass(frozen=True)
class FieldSpec:
    index: int
    name: str
    purpose: str
    data_type: str
    units: str | None
    direction: str
    required: str
    source: str
    confidence: Confidence
    comment: str = ""


def _c(
    index: int,
    name: str,
    purpose: str,
    data_type: str = "decimal",
    units: str | None = None,
    direction: str = "input",
    required: str = "yes for steel Tconstr",
    source: str = "RX38 differential analysis",
    confidence: Confidence = "confirmed",
    comment: str = "",
) -> FieldSpec:
    return FieldSpec(index, name, purpose, data_type, units, direction, required, source, confidence, comment)


# Only positions supported by admissible evidence are named here. All other
# positions remain unknown and are deliberately absent from this mapping.
FIELD_SPECS: dict[int, FieldSpec] = {
    0: _c(0, "record_type", "Record discriminator", "literal[Tconstr]", None, "service", "yes", "All RX38 files"),
    1: _c(1, "mark", "Construction mark/name", "string", None, "input", "yes", "RX38 files + RX3 report"),
    3: _c(3, "mark_copy", "Second stored copy of construction mark", "string", None, "service", "yes", "All RX38 files", "probable", "Always equals field 1 in the available corpus; internal use is not documented."),
    4: _c(4, "section_type_code", "Numeric section-family code", "integer", None, "service", "yes", "RX38 cross-comparison", "probable", "One-to-one mapping with field 5 in the available corpus."),
    5: _c(5, "section_type", "Section family", "string", None, "input", "yes", "RX38 files + RX3 report + help"),
    8: _c(8, "height_mm", "Overall section height", units="mm", source="RX38 ↔ rx3.rxdb exact match"),
    9: _c(9, "width_mm", "Overall section width", units="mm", source="RX38 ↔ rx3.rxdb exact match"),
    11: _c(11, "web_thickness_mm", "Web/wall thickness stored in tw position", units="mm", source="RX38 ↔ rx3.rxdb exact match"),
    13: _c(13, "flange_thickness_mm", "Flange/wall thickness stored in tf position", units="mm", source="RX38 ↔ rx3.rxdb exact match"),
    14: _c(14, "length_m", "Length of one construction", units="m", source="RX38 identities: field24=field21×field14/1000; RX3 report"),
    15: _c(15, "quantity", "Number of constructions", units="pcs", source="RX38 identities: field25=field24×field15; RX3 report"),
    17: _c(17, "profile_standard", "Profile assortment standard", "string", None, source="RX38 ↔ rx3.rxdb exact match"),
    18: _c(18, "profile_row_code", "RX3 assortment row code", "integer", None, "service", "yes", "RX38 cross-comparison", "probable", "Not the SQLite primary key; exact internal meaning is unproved."),
    19: _c(19, "profile_name", "Profile designation", "string", None, source="RX38 ↔ rx3.rxdb exact match"),
    20: _c(20, "area_mm2", "Cross-sectional area", units="mm²", direction="calculated", source="RX38 ↔ rx3.rxdb s×100 exact match; RX3 report"),
    21: _c(21, "heated_perimeter_mm", "Heated perimeter along section contour", units="mm", direction="calculated", source="RX38 A/P identities + RX3 report/help"),
    22: _c(22, "ptm_mm", "Reduced thickness A/P along contour", units="mm", direction="calculated", source="Exact identity field20/field21 in all RX38 records"),
    23: _c(23, "section_factor_per_m", "Section factor P/A along contour", units="m⁻¹", direction="calculated", source="Exact identity 1000/field22 in all RX38 records"),
    24: _c(24, "contour_area_one_m2", "Contour treatment area of one construction", units="m²", direction="calculated", source="Exact identity field21×field14/1000; RX3 report"),
    25: _c(25, "contour_area_total_m2", "Contour treatment area of all constructions", units="m²", direction="calculated", source="Exact identity field24×field15; RX3 report"),
    26: _c(26, "ix_m4", "Second moment of area Ix", units="m⁴", direction="calculated", source="RX38 ↔ rx3.rxdb Ix×10⁻⁸ exact match"),
    27: _c(27, "iy_m4", "Second moment of area Iy", units="m⁴", direction="calculated", source="RX38 ↔ rx3.rxdb Iy×10⁻⁸ exact match"),
    28: _c(28, "imin_m4", "Minimum second moment of area", units="m⁴", direction="calculated", source="RX38 values + RX3 report", confidence="probable", comment="Symmetric cases are exact; asymmetric cases need UI-controlled differential samples."),
    29: _c(29, "wx_m3", "Elastic section modulus Wx", units="m³", direction="calculated", source="RX38 ↔ rx3.rxdb wx×10⁻⁶ exact match"),
    30: _c(30, "wy_m3", "Elastic section modulus Wy", units="m³", direction="calculated", source="RX38 ↔ rx3.rxdb wy×10⁻⁶ exact match"),
    31: _c(31, "governing_steel_thickness_mm", "Thickness used for steel strength selection", units="mm", source="RX38 cross-comparison", confidence="probable", comment="Often equals tf; needs a controlled nonstandard-section sample."),
    32: _c(32, "steel_density_kg_m3", "Steel density", units="kg/m³", source="RX3 report/help + rx3.xml default"),
    33: _c(33, "steel_yield_strength_mpa", "Stored steel yield strength", units="MPa", source="RX38 values + RX3 report/help"),
    34: _c(34, "steel_elastic_modulus_mpa", "Steel elastic modulus", units="MPa", source="RX38 values + RX3 report/help + rx3.xml"),
    35: _c(35, "steel_conductivity_d", "Coefficient D of steel thermal-conductivity model", units=None, source="Exact match with rx3.xml teploD"),
    36: _c(36, "steel_conductivity_e", "Coefficient E of steel thermal-conductivity model", units=None, source="Exact match with rx3.xml teploE"),
    37: _c(37, "steel_conductivity_f", "Coefficient F of steel thermal-conductivity model", units=None, source="Exact match with rx3.xml teploF"),
    38: _c(38, "steel_heat_capacity_d", "Coefficient D of steel heat-capacity model", units=None, source="Exact match with rx3.xml emkostD"),
    39: _c(39, "steel_heat_capacity_e", "Coefficient E of steel heat-capacity model", units=None, source="Exact match with rx3.xml emkostE"),
    40: _c(40, "steel_heat_capacity_f", "Coefficient F of steel heat-capacity model", units=None, source="Exact match with rx3.xml emcostF"),
    41: _c(41, "steel_emissivity", "Steel surface emissivity", units="1", source="Exact match with rx3.xml blacksteel + RX3 help"),
    42: _c(42, "steel_grade", "Steel grade", "string", None, source="RX38 files + RX3 report/help"),
    44: _c(44, "critical_temperature_c", "Calculated critical steel temperature", units="°C", direction="output", source="RX38 ↔ EN 1993-1-2 load-level relation + RX3 report"),
    45: _c(45, "stress_state", "Member loading/stress-state description", "string", None, source="RX38 values + RX3 help/report"),
    46: _c(46, "stress_state_code", "Numeric loading/stress-state code", "integer", None, "service", "yes", "RX38 cross-comparison", "probable"),
    47: _c(47, "support_condition_code", "Numeric support-condition code", "integer", None, "service", "conditional", "RX38 cross-comparison", "probable"),
    48: _c(48, "support_condition", "Support condition", "string", None, source="RX38 values + RX3 help/report", required="conditional"),
    49: _c(49, "axial_force_kn", "Axial force N", units="kN", source="RX38 values + RX3 help/report", required="conditional"),
    50: _c(50, "major_axis_moment_knm", "Bending moment about selected major axis", units="kN·m", source="RX38 differential values + RX3 help", confidence="probable", required="conditional"),
    51: _c(51, "effective_length_m", "Effective/calculated member length", units="m", direction="calculated", source="Exact identity field14×field141 for supported compression records + RX3 report"),
    52: _c(52, "load_level_mu0", "Load level used to determine critical temperature", units="1", direction="calculated", source="Exact EN 1993-1-2 critical-temperature relation with field44 + RX3 help"),
    54: _c(54, "unprotected_fire_resistance_min", "Calculated unprotected fire resistance", units="min", direction="output", source="RX38 values + RX3 report"),
    55: _c(55, "required_fire_resistance_min", "Required fire resistance R", units="min", source="RX38 values + RX3 report/help"),
    59: _c(59, "quantity_method", "Method used for quantity/mass/area accounting", "string", None, source="RX38 text value", confidence="probable"),
    61: _c(61, "loading_axis", "Selected reference loading axis", "string", None, source="RX38 text value + RX3 help", confidence="probable"),
    66: _c(66, "mass_one_kg", "Mass of one construction", units="kg", direction="calculated", source="Exact identity area×length×density; RX3 report"),
    67: _c(67, "mass_total_kg", "Total mass of constructions", units="kg", direction="calculated", source="Exact identity field66×field15; RX3 report"),
    72: _c(72, "fireproofing_material", "Fireproofing material name", "string", None, source="RX38 values + fpm.rxdb + RX3 report/help"),
    76: _c(76, "normal_stress_mpa", "Calculated normal stress", units="MPa", direction="calculated", source="RX38 values + RX3 report", confidence="probable", comment="Position needs a controlled UI export because combined-stress records are not uniquely attributable."),
    82: _c(82, "convection_coefficient", "Convective heat-transfer coefficient", units="W/(m²·K)", source="RX38 value + rx3.xml ac + RX3 help"),
    83: _c(83, "flame_emissivity", "Flame emissivity", units="1", source="RX38 value + rx3.xml blackflame + RX3 help"),
    84: _c(84, "view_factor", "Radiation view/configuration factor", units="1", source="RX38 value + rx3.xml ff + RX3 help"),
    85: _c(85, "shadow_effect_factor", "Shadow-effect correction selector/factor", units="1", source="RX38 values + rx3.xml kf + RX3 help", confidence="probable"),
    86: _c(86, "box_heated_perimeter_mm", "Heated perimeter for box protection", units="mm", direction="calculated", source="Geometry identity + RX3 report"),
    87: _c(87, "box_area_one_m2", "Box-protection area of one construction", units="m²", direction="calculated", source="Exact identity field86×field14/1000; RX3 report"),
    88: _c(88, "box_area_total_m2", "Box-protection area of all constructions", units="m²", direction="calculated", source="Exact identity field87×field15; RX3 report"),
    90: _c(90, "plastic_modulus_x_m3", "Plastic section modulus Wpl,x", units="m³", direction="calculated", source="RX38 values + RX3 report", confidence="probable"),
    91: _c(91, "plastic_modulus_y_m3", "Plastic section modulus Wpl,y", units="m³", direction="calculated", source="RX38 values + RX3 report", confidence="probable"),
    104: _c(104, "fire_regime", "Fire temperature regime", "string", None, source="RX38 values + rx3.xml + RX3 report/help"),
    113: _c(113, "box_ptm_mm", "Reduced thickness A/P for box protection", units="mm", direction="calculated", source="Exact identity field20/field86 in all RX38 records"),
    114: _c(114, "box_section_factor_per_m", "Section factor P/A for box protection", units="m⁻¹", direction="calculated", source="Exact identity 1000/field113 in all RX38 records"),
    141: _c(141, "effective_length_factor", "Effective-length factor", units="1", source="Exact identity field51/field14 + support descriptions"),
    188: _c(188, "steel_temperature_model", "Steel strength/stiffness temperature-dependence model", "string", None, source="Exact match with rx3.xml rx_steel_prop_text + RX3 report/help"),
    189: _c(189, "steel_temperature_model_code", "Numeric code of steel temperature model", "integer", None, "service", "yes", "Exact match with rx3.xml rx_steel_prop_index", "confirmed"),
}

TCONSTR_FIELD_COUNT = 200
CONFIRMED_INDICES = frozenset(i for i, spec in FIELD_SPECS.items() if spec.confidence == "confirmed")


def field_spec(index: int) -> FieldSpec:
    return FIELD_SPECS.get(
        index,
        FieldSpec(index, f"unknown_{index:03d}", "Not established", "unknown", None, "unknown", "unknown", "No admissible evidence", "unknown", "Preserved verbatim; do not write through the typed API."),
    )
