from defence_extractor.ontology import load_ontology, normalize_label
from defence_extractor.ontology.values import dimension_of, parse_value


def test_ontology_loads_and_aliases():
    o = load_ontology()
    assert len(o.params) >= 80
    assert o.lookup_label("Max. Speed (km/h)")[0].id == "max_speed"
    assert o.lookup_label("maximum_range_km")[0].id == "max_range"
    assert o.lookup_label("HOGE")[0].id == "hover_ceiling_oge"
    assert o.lookup_label("vehicle_weight_t")[0].id == "combat_weight"
    assert "calibre" in o.core_for(["ammunition"])
    assert normalize_label("Length (m):") == "length"


def test_values():
    v = parse_value("2,893 m (9,490 ft)")
    assert v.kind == "scalar" and v.nominal.value == 2893 and v.alternates[0].unit == "ft"
    v = parse_value("up to 70km")
    assert v.kind == "bound" and v.comparator.value == "up_to" and v.nominal.normalized_value == 70000
    v = parse_value("3.0 m to 4.5 m")
    assert v.kind == "range" and v.min.value == 3.0 and v.max.value == 4.5
    v = parse_value("5945 x 2340 x 2450 mm")
    assert v.kind == "composite" and len(v.components) == 3 and v.components[0].unit == "mm"
    v = parse_value("2 x 30 mm cannon")
    assert v.kind == "count" and v.multiplier == 2
    v = parse_value("Cummins 6BT 5.91-litre turbo diesel")
    assert v.kind in ("text", "scalar")
    v = parse_value("> 35,000 ft")
    assert v.kind == "bound" and v.comparator.value == "gt"
    assert dimension_of("96 km/h") == "speed"
    assert dimension_of("Fire-on-the-move capability") is None


def test_a14_extension_is_merged_into_the_catalogue():
    from defence_extractor.ontology import load_ontology

    onto = load_ontology()
    assert onto.version == "1.1.0"
    assert len(onto.params) >= 170
    assert [p.id for p in onto.lookup_label("Shelf life")] == ["shelf_life"]
    assert [p.id for p in onto.lookup_label("Material")] == ["material"]
    assert [p.id for p in onto.lookup_label("Cartridge weight")] == ["cartridge_weight"]  # no longer projectile_weight
    assert "length_overall" in [p.id for p in onto.lookup_label("Total length extended (mm)")]
    assert [p.id for p in onto.lookup_label("Total length retracted (mm)")] == ["length_folded"]
    assert onto.get("thrust").dimension == "force" and onto.get("thrust").status == "candidate"
