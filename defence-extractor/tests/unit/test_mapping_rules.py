from defence_extractor.agents.mapping import _generic_label, _label_agrees, _refines
from defence_extractor.ontology import load_ontology


def test_generic_and_composite_labels():
    for lab in ["Dimensions", "Dimensions (LxWxH)", "Performance", "Crew and Capacity", "Range & Altitude", "Length & Dia",
                "Weight / Dimensions", None, ""]:
        assert _generic_label(lab), lab
    for lab in ["Max. speed (road/water)", "Width", "Safety temperature", "Rate of fire", "Combat weight"]:
        assert not _generic_label(lab), lab


def test_refines_and_agrees():
    onto = load_ontology()
    assert _refines("Speed", onto.get("cruise_speed"))
    assert _refines("Capacity", onto.get("troop_capacity"))
    assert not _refines("Width", onto.get("height"))
    assert _label_agrees(onto, "Max speed", onto.get("max_speed"))
    assert not _label_agrees(onto, "Safety temperature", onto.get("operating_temperature"))


def test_unit_in_label_makes_bare_cell_compatible():
    from defence_extractor.agents.mapping import dims_compatible, label_unit_dimension

    onto = load_ontology()
    assert label_unit_dimension("Weight (approx. Kg) (weapon only)") == "mass"
    assert label_unit_dimension("Barrel length (mm)") == "length"
    assert dims_compatible(onto.get("weight"), "8", "en", "Weight (approx. Kg) (weapon only)")
    assert not dims_compatible(onto.get("weight"), "8", "en", "Barrel length (mm)")
