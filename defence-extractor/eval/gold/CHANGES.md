
## 2026-10-09 — alternatives for catalogue v1.1 (A14 promotions)
Catalogue v1.1 added canonical homes for parameters that the benchmark keys named with free ids. Where the new id
means the same thing, it was added to `alt` (values and owners unchanged):
- E1043, E1050 `modes_of_operation` += `operating_modes`
- E126 `launch_type` += `launch_method`
- E648 `shell_material` += `material` (label kept as "Material of Empty Shell"); `weight` (Mass of Round) += `cartridge_weight`
- E661 `nato_qualification` (AC/116-32A) += `standard_compliance`
- E662 `standard` (STANAG 2310) += `standard_compliance`
- E879 `tyres` (335/85/R20) += `tyre_size`
Not added (judged real mapping errors): navigation modes -> operating_modes, calibre -> diameter, armour -> material,
finish (antenna material) -> colour, powder property -> propellant_type.
