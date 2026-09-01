# RX3 validation evidence

`VALIDATION` and `PRODUCTION` are fail-closed before an RX38 file is generated.
For every `ProjectElement`, `rx3_safety.heating_exposure` must be typed evidence
that binds all of the following:

- the exact `project_element_id`;
- the engineering value `heating_sides`;
- the SHA-256 fingerprint of the exact 200-field template `Tconstr` record;
- a reviewed source, reviewer, review date and evidence version.

`heated_perimeter` remains an explicit engineering value, but it does not prove
which RX3 heating-side flags apply. No RX38 heating-side indices are inferred.
Missing or mismatched evidence raises `HEATING_EXPOSURE_UNVERIFIED`. `DRAFT` may
preserve the template flags only with that warning in the generation audit.

Steel production evidence must also bind the template thermal profile: fields
82, 83, 84, 188 and 189 must match `SteelCalculationProperties`. A strength
match by itself is not production evidence.

Do not relax the protected RX3 GUI fields 44/54 or the unexpected-change check.
Those controls remain in force until a separate controlled experiment provides
evidence.
