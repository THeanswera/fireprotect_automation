from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

Confidence = Literal["confirmed", "probable", "unknown"]


class WritePolicy(str, Enum):
    SAFE_DIRECT = "SAFE_DIRECT"
    SAFE_WITH_COMPATIBILITY_CHECK = "SAFE_WITH_COMPATIBILITY_CHECK"
    RESULT_ONLY = "RESULT_ONLY"
    READ_ONLY = "READ_ONLY"
    EXPERIMENTAL = "EXPERIMENTAL"
    FORBIDDEN = "FORBIDDEN"


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
    write_policy: WritePolicy = WritePolicy.FORBIDDEN
    evidence_type: str = "UNSPECIFIED"
    evidence_sources: tuple[str, ...] = ()
    controlled_experiment_ids: tuple[str, ...] = ()
    corpus_count: int | None = None
    exact_identity: bool = False
    report_correlation: bool = False
    database_match: bool = False
    help_match: bool = False
    reviewer_note: str = ""


def _write_policy(index: int, direction: str, confidence: Confidence) -> WritePolicy:
    if confidence != "confirmed":
        return WritePolicy.FORBIDDEN
    if direction == "output":
        return WritePolicy.RESULT_ONLY
    if index == 0:
        return WritePolicy.READ_ONLY
    if index in {1, 3}:
        return WritePolicy.SAFE_DIRECT
    return WritePolicy.SAFE_WITH_COMPATIBILITY_CHECK


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
    *,
    evidence_type: str = "CORPUS_AND_DOCUMENTATION",
    evidence_sources: tuple[str, ...] = (),
    controlled_experiment_ids: tuple[str, ...] = (),
    reviewer_note: str = "",
    write_policy: WritePolicy | None = None,
) -> FieldSpec:
    return FieldSpec(
        index,
        name,
        purpose,
        data_type,
        units,
        direction,
        required,
        source,
        confidence,
        comment,
        write_policy or _write_policy(index, direction, confidence),
        evidence_type,
        evidence_sources or (source,),
        controlled_experiment_ids,
        46 if "46" in source or "corpus" in source.casefold() else None,
        "Exact identity" in source or "exact match" in source.casefold(),
        "report" in source.casefold(),
        "rx3.rxdb" in source.casefold() or "rx3.xml" in source.casefold(),
        "help" in source.casefold(),
        reviewer_note or comment,
    )


# Only positions supported by admissible evidence are named here. All other
# positions remain unknown and are deliberately absent from this mapping.
FIELD_SPECS: dict[int, FieldSpec] = {
    0: _c(0, "record_type", "Record discriminator", "literal[Tconstr]", None, "service", "yes", "All RX38 files"),
    1: _c(1, "mark", "Construction mark/name", "string", None, "input", "yes", "RX38 files + RX3 report"),
    3: _c(3, "mark_copy", "Second stored copy of construction mark", "string", None, "service", "yes", "Exact equality with field 1 in all 46 corpus records", "confirmed", "The consumer role is undocumented; synchronizing it with field 1 is confirmed."),
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
    44: _c(44, "critical_temperature_c", "Calculated critical steel temperature", units="°C", direction="output", source="RX38 ↔ EN 1993-1-2 load-level relation + RX3 report", controlled_experiment_ids=("RX3-EXP-01", "RX3-EXP-01C", "RX3-EXP-01D", "RX3-EXP-02B"), reviewer_note="Bidirectional axial perturbations and the controlled X-X bending perturbation reproduce the governing GUI critical temperature; normative equivalence remains outside this evidence."),
    45: _c(45, "stress_state", "Member loading/stress-state description", "string", None, source="RX38 values + RX3 help/report"),
    46: _c(46, "stress_state_code", "Numeric loading/stress-state code", "integer", None, "service", "yes", "RX38 cross-comparison", "probable"),
    47: _c(47, "support_condition_code", "Numeric support-condition code", "integer", None, "service", "conditional", "RX38 cross-comparison", "probable"),
    48: _c(48, "support_condition", "Support condition", "string", None, source="RX38 values + RX3 help/report", required="conditional"),
    49: _c(49, "axial_force_kn", "Axial force N", units="kN", source="RX38 values + RX3 help/report", required="conditional", controlled_experiment_ids=("RX3-EXP-01", "RX3-EXP-01C", "RX3-EXP-01D"), reviewer_note="Field 49 is reproduced bidirectionally through the controlled axial GUI path; this does not verify the LIRA-to-RX3 sign convention."),
    50: _c(50, "major_axis_moment_knm", "Maximum bending-moment magnitude about the selected major axis", units="kN·m", source="5-record bending corpus + RX3 help + controlled RX3/LIRA evidence", required="conditional", comment="Confirmed as a non-negative maximum Mx input for one-plane X-X bending in the verified Б1 family and the exact Б2 / 22П / ГОСТ 8240-97 / zero-rotation scope; production writing remains disabled.", evidence_type="CONTROLLED_SINGLE_VARIABLE_GUI_CAUSALITY", evidence_sources=("RX3-EXP-02 five-record one-plane bending corpus", "RX3-EXP-02 Phase A GUI observation", "RX3-EXP-02B controlled field50 Mx=10 GUI/calculation/save", "LIRA-RX3-22P-XX-MAGNITUDE negative-Mx rejection, positive acceptance and target-aware persistence"), controlled_experiment_ids=("RX3-EXP-02", "RX3-EXP-02B", "LIRA-RX3-22P-XX-MAGNITUDE"), reviewer_note="LIRA My -> field50 with MAGNITUDE is VALIDATED only for Б2 / 22П / ГОСТ 8240-97 / L=3.00 m / zero rotation / one-plane X-X and an explicitly selected source observation. Governing-result selection, other profiles, rotations and stress states remain unresolved.", write_policy=WritePolicy.EXPERIMENTAL),
    51: _c(51, "effective_length_m", "Effective/calculated member length", units="m", direction="calculated", source="Exact identity field14×field141 for supported compression records + RX3 report"),
    52: _c(52, "load_level_mu0", "Load level used to determine critical temperature", units="1", direction="calculated", source="Exact EN 1993-1-2 critical-temperature relation with field44 + RX3 help", controlled_experiment_ids=("RX3-EXP-01", "RX3-EXP-01C", "RX3-EXP-01D", "RX3-EXP-02B"), reviewer_note="Three controlled axial states and the controlled X-X bending state equal the GUI gamma_tem values after display rounding."),
    53: _c(53, "beta_tem_modulus_reduction", "RX3 beta_tem / elastic-modulus reduction coefficient at critical temperature", units="1", direction="calculated", source="RX3-EXP-01/01C/01D/02B GUI equality + RX3 EN 1993-1-2 help", confidence="probable", comment="Three controlled axial states and the one-plane X-X bending state equal GUI beta_tem after display rounding; broader stress-state applicability and a direct exported-report binding remain unverified.", evidence_type="CONTROLLED_GUI_CORRELATION_AND_HELP", evidence_sources=("RX3-EXP-01 baseline GUI observation", "RX3-EXP-01C controlled N30 GUI observation", "RX3-EXP-01D controlled N25 GUI observation", "RX3-EXP-02B controlled Mx10 GUI observation", "rx3/doc/pages/EN1993_1_2.html"), controlled_experiment_ids=("RX3-EXP-01", "RX3-EXP-01C", "RX3-EXP-01D", "RX3-EXP-02B"), reviewer_note="Retained as PROBABLE; the bending value is 0 and correlates with GUI beta_tem=0.000, while the separate 1200 C beta-related temperature is not assigned to an RX38 field."),
    54: _c(54, "unprotected_fire_resistance_min", "Calculated unprotected fire resistance", units="min", direction="output", source="RX38 values + RX3 report", controlled_experiment_ids=("RX3-EXP-01", "RX3-EXP-01C", "RX3-EXP-01D", "RX3-EXP-02B"), reviewer_note="Bidirectional axial perturbations and the controlled X-X bending perturbation reproduce the GUI unprotected fire resistance; normative equivalence remains outside this evidence."),
    55: _c(55, "required_fire_resistance_min", "Required fire resistance R", units="min", source="RX38 values + RX3 report/help"),
    59: _c(59, "quantity_method", "Method used for quantity/mass/area accounting", "string", None, source="RX38 text value", confidence="probable"),
    61: _c(61, "loading_axis", "Selected reference loading axis", "string", None, source="RX38 text value + RX3 help", confidence="probable"),
    66: _c(66, "mass_one_kg", "Mass of one construction", units="kg", direction="calculated", source="Exact identity area×length×density; RX3 report"),
    67: _c(67, "mass_total_kg", "Total mass of constructions", units="kg", direction="calculated", source="Exact identity field66×field15; RX3 report"),
    72: _c(72, "fireproofing_material", "Fireproofing material name", "string", None, source="RX38 values + fpm.rxdb + RX3 report/help"),
    76: _c(76, "normal_stress_mpa", "Calculated normal stress", units="MPa", direction="calculated", source="RX38 values + RX3 report", confidence="probable", comment="Three controlled axial states and one one-plane X-X bending state match the GUI stress/load value, but broader combined-stress semantics are not uniquely attributable.", evidence_type="CONTROLLED_GUI_CORRELATION_AND_REPORT", evidence_sources=("RX3 report", "RX3-EXP-01 baseline GUI observation", "RX3-EXP-01C controlled N30 GUI observation", "RX3-EXP-01D controlled N25 GUI observation", "RX3-EXP-02B controlled Mx10 GUI observation"), controlled_experiment_ids=("RX3-EXP-01", "RX3-EXP-01C", "RX3-EXP-01D", "RX3-EXP-02B"), reviewer_note="Retained as PROBABLE because the displayed quantity is stress/load and broader combined-stress states are unverified."),
    78: _c(78, "persisted_major_axis_moment_copy_knm", "RX3-persisted copy of the active major-axis moment", units="kN·m", direction="service", required="conditional", source="RX3-EXP-02 corpus + controlled post-calc differentials", confidence="probable", comment="Synchronized from the active Mx only after RX3 calculation/save in the controlled one-plane cases; it is not a direct writer target.", evidence_type="CONTROLLED_POST_CALC_PERSISTENCE_CORRELATION", evidence_sources=("RX3-EXP-02 five-record one-plane bending corpus", "RX3-EXP-02B generated-to-calculated diff", "LIRA-RX3-22P-XX-MAGNITUDE persisted target-aware diff"), controlled_experiment_ids=("RX3-EXP-02", "RX3-EXP-02B", "LIRA-RX3-22P-XX-MAGNITUDE"), reviewer_note="Derived/persisted-copy role remains PROBABLE and writing remains forbidden."),
    79: _c(
        79,
        "rx3_gui_minor_axis_moment_input_knm",
        "RX3 GUI My bending-moment input for the verified biaxial Кс1 / 20П family",
        data_type="decimal",
        units="kN·m",
        direction="input",
        required="conditional",
        source="RX3-EXP-04/04B controlled GUI and persisted RX38 evidence",
        confidence="confirmed",
        comment=(
            "Confirmed only for the verified Кс1 / 20П biaxial RX3 GUI My path; "
            "production writing remains disabled."
        ),
        evidence_type="CONTROLLED_SINGLE_VARIABLE_GUI_CAUSALITY",
        evidence_sources=(
            "RX3-EXP-04 exact Phase A GUI correlation",
            "RX3-EXP-04B field79-only My perturbation with Mx fixed at 0.51",
            "RX3-EXP-04B manual Calculate / Save to table / Save As persistence",
            "RX3-EXP-04B target-aware diff with six invariant non-target records",
        ),
        controlled_experiment_ids=("RX3-EXP-04", "RX3-EXP-04B"),
        reviewer_note=(
            "Excludes LIRA My mapping, LIRA local-axis mapping, sign convention, "
            "arbitrary X/Y correspondence, other profiles or stress states, "
            "general combined-stress semantics, and production compatibility."
        ),
        write_policy=WritePolicy.EXPERIMENTAL,
    ),
    82: _c(82, "convection_coefficient", "Convective heat-transfer coefficient", units="W/(m²·K)", source="RX38 value + rx3.xml ac + RX3 help"),
    83: _c(83, "flame_emissivity", "Flame emissivity", units="1", source="RX38 value + rx3.xml blackflame + RX3 help"),
    84: _c(84, "view_factor", "Radiation view/configuration factor", units="1", source="RX38 value + rx3.xml ff + RX3 help"),
    85: _c(85, "shadow_effect_factor", "Shadow-effect correction selector/factor", units="1", source="RX38 values + rx3.xml kf + RX3 help", confidence="probable"),
    86: _c(86, "box_heated_perimeter_mm", "Heated perimeter for box protection", units="mm", direction="calculated", source="Geometry identity + RX3 report"),
    87: _c(87, "box_area_one_m2", "Box-protection area of one construction", units="m²", direction="calculated", source="Exact identity field86×field14/1000; RX3 report"),
    88: _c(88, "box_area_total_m2", "Box-protection area of all constructions", units="m²", direction="calculated", source="Exact identity field87×field15; RX3 report"),
    90: _c(90, "static_moment_half_section_x_m3", "Static moment of the half-section Sx", units="m³", direction="calculated", source="RX3 main-window table label + RX38 value match: table Sx=410,683 cm3, file 0,00041068340928512 m3; Wpl,x=782,527 cm3 is field 99", confidence="probable"),
    91: _c(91, "static_moment_half_section_y_m3", "Static moment of the half-section Sy", units="m³", direction="calculated", source="RX3 main-window table label + RX38 value match: table Sy=188,611 cm3, file 0,00018861123871488 m3; Wpl,y=364,854 cm3 is field 100", confidence="probable"),
    92: _c(92, "rx3_gui_q_input_kn", "RX3 GUI maximum shear-force Q magnitude for the verified one-plane X-X bending family", units="kN", required="conditional", source="Five-record bending correlation + controlled RX3/LIRA evidence", comment="Confirmed as a non-negative maximum Q input for the verified Б1 / 14Б2 family and the exact Б2 / 22П / ГОСТ 8240-97 / zero-rotation scope; production writing remains disabled.", evidence_type="CONTROLLED_SINGLE_VARIABLE_GUI_CAUSALITY", evidence_sources=("RX3-EXP-02 five-record bending correlation and GUI observation", "RX3-EXP-02B fixed-Q Mx perturbation: GUI Q remained 2.32 while Mx changed", "RX3-EXP-03 field92-only perturbation: GUI Q 2.32 -> 3.00 and Q utilisation 0.028 -> 0.037", "RX3-EXP-03 persisted field92 numeric 3.00 with target-aware non-target invariance", "LIRA-RX3-22P-XX-MAGNITUDE negative-Q rejection, positive acceptance and exact field92 persistence"), controlled_experiment_ids=("RX3-EXP-02", "RX3-EXP-02B", "RX3-EXP-03", "LIRA-RX3-22P-XX-MAGNITUDE"), reviewer_note="LIRA Qz -> field92 with MAGNITUDE is VALIDATED only for Б2 / 22П / ГОСТ 8240-97 / L=3.00 m / zero rotation / one-plane X-X and an explicitly selected source observation. Governing-result selection, other profiles, rotations and stress states remain unresolved.", write_policy=WritePolicy.EXPERIMENTAL),
    104: _c(104, "fire_regime", "Fire temperature regime", "string", None, source="RX38 values + rx3.xml + RX3 report/help"),
    113: _c(113, "box_ptm_mm", "Reduced thickness A/P for box protection", units="mm", direction="calculated", source="Exact identity field20/field86 in all RX38 records"),
    114: _c(114, "box_section_factor_per_m", "Section factor P/A for box protection", units="m⁻¹", direction="calculated", source="Exact identity 1000/field113 in all RX38 records"),
    141: _c(141, "effective_length_factor", "Effective-length factor", units="1", source="Exact identity field51/field14 + support descriptions"),
    188: _c(188, "steel_temperature_model", "Steel strength/stiffness temperature-dependence model", "string", None, source="Exact match with rx3.xml rx_steel_prop_text + RX3 report/help"),
    189: _c(189, "steel_temperature_model_code", "Numeric code of steel temperature model", "integer", None, "service", "yes", "Exact match with rx3.xml rx_steel_prop_index", "confirmed"),
    99: _c(99, "plastic_modulus_x_m3", "Plastic section modulus Wpl,x", units="m³", direction="calculated", source="RX3 main-window table label + RX38 value match: table Wpl,x=782,527 cm3, file 0,000782527351351351 m3", confidence="probable"),
    100: _c(100, "plastic_modulus_y_m3", "Plastic section modulus Wpl,y", units="m³", direction="calculated", source="RX3 main-window table label + RX38 value match: table Wpl,y=364,854 cm3, file 0,000364854 m3", confidence="probable"),
}

TCONSTR_FIELD_COUNT = 200
MY_BIAXIAL_PATH_MVP_STATUS = "VALIDATED"
CONFIRMED_INDICES = frozenset(i for i, spec in FIELD_SPECS.items() if spec.confidence == "confirmed")
WRITABLE_CONFIRMED_INDICES = frozenset(
    i
    for i in CONFIRMED_INDICES
    if FIELD_SPECS[i].write_policy
    in {WritePolicy.SAFE_DIRECT, WritePolicy.SAFE_WITH_COMPATIBILITY_CHECK}
)


def field_spec(index: int) -> FieldSpec:
    return FIELD_SPECS.get(
        index,
        FieldSpec(index, f"unknown_{index:03d}", "Not established", "unknown", None, "unknown", "unknown", "No admissible evidence", "unknown", "Preserved verbatim; do not write through the typed API."),
    )
