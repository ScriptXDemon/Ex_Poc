from defence_extractor.contracts.inventory import Comparator
from defence_extractor.ingest.measurements import find_measurements, parse_number


def kinds(text, lang="en"):
    return [(m.kind, m.text) for m in find_measurements(text, lang)]


def test_parse_number_locales():
    assert parse_number("11,660") == 11660
    assert parse_number("4,69", "de") == 4.69
    assert parse_number("11.660", "de") == 11660
    assert parse_number("4.69") == 4.69
    assert parse_number("2 893".replace(" ", " ")) == 2893
    assert parse_number("1.000,5", "de") == 1000.5
    assert parse_number(".50") == 0.5


def test_scalar_and_units():
    ms = find_measurements("Length: 5.17 m and weight 11,660 kg (combat loaded)")
    assert [m.text for m in ms] == ["5.17 m", "11,660 kg"]
    assert ms[0].dimension == "length" and ms[1].dimension == "mass"


def test_bound_and_plus():
    ms = find_measurements("up to 70km, range 30+ nm, ceiling > 35,000 ft")
    texts = [m.text for m in ms]
    assert "up to 70km" in texts
    assert any(t.startswith("30+") for t in texts)
    assert "> 35,000 ft" in texts
    assert ms[0].comparator == Comparator.up_to


def test_range_and_dual():
    k = kinds("length 3.0 m to 4.5 m; band 2.0 – 18.0 GHz; HOGE 2,893 m (9,490 ft); temps -32°C to +49°C")
    assert ("range", "3.0 m to 4.5 m") in k
    assert ("range", "2.0 – 18.0 GHz") in k
    assert ("dual", "2,893 m (9,490 ft)") in k
    assert ("range", "-32°C to +49°C") in k


def test_composite_calibre_multiplier():
    k = dict((t, kd) for kd, t in kinds("Dimensions 5945 x 2340 x 2450 mm, 2 x 30 mm cannon, 7.62x51mm NATO"))
    assert k.get("5945 x 2340 x 2450 mm") == "composite"
    assert k.get("2 x 30 mm") == "multiplier"
    assert any(kd == "calibre" for t, kd in k.items() if t.startswith("7.62x51"))


def test_counts_ratio_percent_config():
    k = kinds("3-man crew, 8 rounds, VSWR 3.5:1, 100 percent lead free, 8x8 chassis")
    assert ("count", "3-man") in k or ("count", "3-man crew") in k
    assert ("count", "8 rounds") in k
    assert ("ratio", "3.5:1") in k
    assert ("percent", "100 percent") in k
    assert ("config", "8x8") in k


def test_no_false_positives():
    assert find_measurements("Founded in 2019 in Paris, the company") == []
    assert find_measurements("the 1990s were different") == []
    assert find_measurements("Leopard 2A7 and K9A1 and AW149") == []


def test_inches_with_bracket():
    k = kinds("Overall length 26.25 in [667 mm] - 31 in [787 mm]")
    assert any(kd in ("dual", "range") and "26.25 in" in t for kd, t in k)


def test_case_sensitive_units_do_not_collide():
    from defence_extractor.ontology.values import dimension_of

    assert dimension_of("541 Nm") == "torque"
    assert dimension_of("1627 Nm @ 1200-1700 rpm") == "torque"
    assert dimension_of("2000 Nm (202 kgm)") == "torque"
    assert dimension_of("30 NM") == "length"
    assert dimension_of("30+ nm") == "length"
    assert dimension_of("98 KN") == "force"
    assert dimension_of("120 kN") == "force"
    assert dimension_of("35 kn") == "speed"
    assert dimension_of("12 kts") == "speed"
