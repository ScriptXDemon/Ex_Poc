"""Unit lexicon for defence specifications.

Each entry: surface forms -> (unit_key, dimension, pint expression or None, case_insensitive).
Single-letter units (m, g, t, s, h, l, N, W, V, A, J) are matched case-sensitively; longer ones case-insensitively.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class UnitDef:
    key: str
    dimension: str
    pint: str | None
    forms: tuple[str, ...]
    ci: bool = True  # case-insensitive matching


UNITS: list[UnitDef] = [
    # ---- length ---------------------------------------------------------------------------
    UnitDef("mm", "length", "millimeter", ("mm", "millimetre", "millimetres", "millimeter", "millimeters", "мм", "毫米")),
    UnitDef("cm", "length", "centimeter", ("cm", "centimetre", "centimetres", "centimeter", "centimeters", "см", "厘米")),
    UnitDef("um", "length", "micrometer", ("µm", "μm", "micron", "microns")),
    UnitDef("m_cep", "length", "meter", ("mCEP", "m CEP", "m-CEP")),
    UnitDef("km", "length", "kilometer", ("km", "kms", "kilometre", "kilometres", "kilometer", "kilometers", "км", "公里", "千米")),
    UnitDef("m", "length", "meter", ("m", "metre", "metres", "meter", "meters", "м", "米"), ci=False),
    UnitDef("nmi", "length", "nautical_mile", ("nmi", "n.mi.", "n.mi", "nautical miles", "nautical mile", "naut. mi")),
    # exact case only: "NM" / "nm" are nautical miles, "Nm" is newton-metres (torque), never the other way round
    UnitDef("NM", "length", "nautical_mile", ("NM",), ci=False),
    UnitDef("nm_ambiguous", "length", "nautical_mile", ("nm",), ci=False),
    UnitDef("in", "length", "inch", ("in.", "in", "inch", "inches", "″", '"', "''"), ci=False),
    UnitDef("ft", "length", "foot", ("ft", "feet", "foot", "′", "'"), ci=False),
    UnitDef("yd", "length", "yard", ("yd", "yds", "yard", "yards")),
    UnitDef("mi", "length", "mile", ("mi", "mile", "miles", "statute miles")),
    # ---- mass --------------------------------------------------------------------------------
    UnitDef("mg", "mass", "milligram", ("mg",), ci=False),
    UnitDef("kg", "mass", "kilogram", ("kg", "kgs", "kilogram", "kilograms", "kilo", "kilos", "кг", "公斤", "千克")),
    UnitDef("g", "mass", "gram", ("g", "gram", "grams", "gramme", "grammes", "г", "克"), ci=False),
    UnitDef("t", "mass", "metric_ton", ("t", "tonne", "tonnes", "ton", "tons", "metric tons", "metric ton", "mt", "т", "吨"), ci=False),
    UnitDef("lb", "mass", "pound", ("lb", "lbs", "lb.", "pound", "pounds")),
    UnitDef("oz", "mass", "ounce", ("oz", "ounce", "ounces")),
    UnitDef("gr", "mass", "grain", ("gr", "grain", "grains")),
    # ---- speed -------------------------------------------------------------------------------
    UnitDef("km/h", "speed", "kilometer/hour", ("km/h", "km/hr", "kmh", "kph", "kmph", "km per hour", "km / h", "км/ч", "公里/小时")),
    UnitDef("mph", "speed", "mile/hour", ("mph", "miles per hour", "mi/h")),
    UnitDef("knot", "speed", "knot", ("knots", "knot", "kt", "kts", "KTAS", "KIAS", "KCAS", "KEAS", "узлов", "узла")),
    UnitDef("kn", "speed", "knot", ("kn",), ci=False),
    UnitDef("m/s", "speed", "meter/second", ("m/s", "m/sec", "mps", "m s-1", "м/с")),
    UnitDef("ft/s", "speed", "foot/second", ("ft/s", "ft/sec", "fps", "f/s")),
    # ---- time --------------------------------------------------------------------------------
    UnitDef("ms", "time", "millisecond", ("ms", "msec", "millisecond", "milliseconds"), ci=False),
    UnitDef("s", "time", "second", ("s", "sec", "secs", "second", "seconds", "с", "秒"), ci=False),
    UnitDef("min", "time", "minute", ("min", "mins", "minute", "minutes", "мин", "分钟")),
    UnitDef("h", "time", "hour", ("h", "hr", "hrs", "hour", "hours", "ч", "小时"), ci=False),
    UnitDef("day", "time", "day", ("day", "days", "сут")),
    UnitDef("year", "time", "year", ("year", "years", "yr", "yrs")),
    # ---- rate of fire ------------------------------------------------------------------------
    UnitDef("rds/min", "rate", None, ("rds/min", "rd/min", "rounds/min", "rounds per minute", "rounds/minute", "rounds a minute",
                                       "r/min", "rpm", "spm", "shots/min", "shots per minute", "выстр/мин")),
    # ---- power / energy / force -----------------------------------------------------------------
    UnitDef("kW", "power", "kilowatt", ("kW", "kilowatt", "kilowatts", "кВт")),
    UnitDef("MW", "power", "megawatt", ("MW", "megawatt", "megawatts"), ci=False),
    UnitDef("W", "power", "watt", ("W", "watt", "watts", "Вт"), ci=False),
    UnitDef("hp", "power", "horsepower", ("hp", "bhp", "shp", "horsepower", "HP", "л.с.")),
    UnitDef("PS", "power", "metric_horsepower", ("PS",), ci=False),
    UnitDef("kVA", "power", None, ("kVA",)),
    UnitDef("kWh", "energy", "kilowatt_hour", ("kWh", "kW·h", "kW-h")),
    UnitDef("Wh", "energy", "watt_hour", ("Wh",), ci=False),
    UnitDef("MJ", "energy", "megajoule", ("MJ",), ci=False),
    UnitDef("kJ", "energy", "kilojoule", ("kJ",), ci=False),
    UnitDef("J", "energy", "joule", ("J", "joule", "joules"), ci=False),
    UnitDef("Nm", "torque", "newton*meter", ("Nm",), ci=False),
    UnitDef("Nm_sep", "torque", "newton*meter", ("N·m", "N.m", "N-m", "N m", "Н·м", "Н*м")),
    UnitDef("lbft", "torque", "foot*force_pound", ("lb-ft", "lbf-ft", "lb·ft", "ft-lb", "ft·lbf", "lb ft", "ft lb", "ft-lbs")),
    # "kN" / "KN" are kilonewtons (thrust "98 KN"); knots are "kn" (lower case), "kt", "kts", "knots"
    UnitDef("kN", "force", "kilonewton", ("kN", "KN"), ci=False),
    UnitDef("kN_cyr", "force", "kilonewton", ("кН",)),
    UnitDef("N", "force", "newton", ("N",), ci=False),
    UnitDef("lbf", "force", "force_pound", ("lbf", "lbs thrust", "lb thrust")),
    UnitDef("kgf", "force", "kilogram_force", ("kgf", "kgs thrust")),
    # ---- pressure -----------------------------------------------------------------------------
    UnitDef("bar", "pressure", "bar", ("bar", "bars")),
    UnitDef("psi", "pressure", "psi", ("psi",)),
    UnitDef("MPa", "pressure", "megapascal", ("MPa",)),
    UnitDef("kPa", "pressure", "kilopascal", ("kPa",)),
    # ---- angle --------------------------------------------------------------------------------
    UnitDef("deg", "angle", "degree", ("°", "º", "deg", "degree", "degrees", "град")),
    UnitDef("mil", "angle", None, ("mils", "mil", "‰")),
    UnitDef("mrad", "angle", "milliradian", ("mrad", "mrads", "milliradian", "milliradians")),
    UnitDef("moa", "angle", None, ("MOA", "moa")),
    # ---- temperature --------------------------------------------------------------------------
    UnitDef("degC", "temperature", "degC", ("°C", "° C", "ºC", "º C", "degC", "deg C", "℃")),
    UnitDef("degF", "temperature", "degF", ("°F", "° F", "ºF", "degF", "℉")),
    # ---- frequency / data ----------------------------------------------------------------------
    UnitDef("Hz", "frequency", "hertz", ("Hz", "hertz")),
    UnitDef("kHz", "frequency", "kilohertz", ("kHz",)),
    UnitDef("MHz", "frequency", "megahertz", ("MHz",)),
    UnitDef("GHz", "frequency", "gigahertz", ("GHz",)),
    UnitDef("THz", "frequency", "terahertz", ("THz",)),
    UnitDef("bps", "data_rate", None, ("bps", "bit/s", "kbps", "kbit/s", "kb/s", "Mbps", "Mbit/s", "Mb/s", "Gbps", "Gbit/s", "Gb/s")),
    # ---- volume / area -------------------------------------------------------------------------
    UnitDef("L", "volume", "liter", ("L", "l", "ltr", "ltrs", "litre", "litres", "liter", "liters", "л"), ci=False),
    UnitDef("mL", "volume", "milliliter", ("ml", "mL")),
    UnitDef("gal", "volume", "gallon", ("gal", "gallon", "gallons", "US gal", "US gallons")),
    UnitDef("m3", "volume", "meter**3", ("m³", "m3", "cu m", "cubic metres", "cubic meters")),
    UnitDef("cm3", "volume", "centimeter**3", ("cm³", "cm3", "cc")),
    UnitDef("m2", "area", "meter**2", ("m²", "m2", "sq m", "square metres", "square meters")),
    UnitDef("km2", "area", "kilometer**2", ("km²", "km2", "sq km")),
    UnitDef("ft2", "area", "foot**2", ("ft²", "ft2", "sq ft", "square feet")),
    # ---- electrical / rf ---------------------------------------------------------------------------
    UnitDef("kV", "voltage", "kilovolt", ("kV",)),
    UnitDef("V", "voltage", "volt", ("V", "VDC", "VAC", "V DC", "V AC", "volt", "volts"), ci=False),
    UnitDef("mA", "current", "milliampere", ("mA",), ci=False),
    UnitDef("A", "current", "ampere", ("amp", "amps", "ampere", "amperes")),
    UnitDef("Ah", "charge", "ampere_hour", ("Ah", "mAh")),
    UnitDef("dB", "level", None, ("dBi", "dBm", "dBW", "dBsm", "dBc", "dB", "dBA", "dB(A)")),
    # ---- misc ---------------------------------------------------------------------------------------
    UnitDef("percent", "ratio", "percent", ("%", "percent", "per cent", "pct", "процент", "processen", "Prozent")),
    UnitDef("g_load", "acceleration", None, ("g-load", "g load")),
    UnitDef("lux", "illuminance", None, ("lux", "lx")),
    UnitDef("lm", "luminous_flux", None, ("lm", "lumens", "lumen")),
    UnitDef("mach", "speed", None, ("Mach",)),
]

# canonical normalisation target per dimension (pint expressions)
NORMALIZED_UNIT = {
    "length": "meter",
    "mass": "kilogram",
    "speed": "kilometer/hour",
    "time": "hour",
    "power": "kilowatt",
    "energy": "kilojoule",
    "torque": "newton*meter",
    "force": "kilonewton",
    "pressure": "bar",
    "angle": "degree",
    "temperature": "degC",
    "frequency": "megahertz",
    "volume": "liter",
    "area": "meter**2",
    "voltage": "volt",
    "current": "ampere",
    "charge": "ampere_hour",
}

_FORM_INDEX: dict[str, UnitDef] = {}
_FORM_INDEX_CI: dict[str, UnitDef] = {}
for _u in UNITS:
    for _f in _u.forms:
        if _u.ci:
            _FORM_INDEX_CI.setdefault(_f.lower(), _u)
        else:
            _FORM_INDEX.setdefault(_f, _u)


def lookup_unit(form: str) -> UnitDef | None:
    form = form.strip()
    if form in _FORM_INDEX:
        return _FORM_INDEX[form]
    return _FORM_INDEX_CI.get(form.lower())


def _form_regex(form: str) -> str:
    esc = re.escape(form).replace(r"\ ", r"\s?")
    return esc


def unit_alternation() -> str:
    """Regex alternation over every unit surface form (longest first), with per-form case rules."""
    forms: list[tuple[str, bool]] = []
    for u in UNITS:
        for f in u.forms:
            forms.append((f, u.ci))
    forms.sort(key=lambda x: len(x[0]), reverse=True)
    parts = []
    for f, ci in forms:
        if f == "in":  # bare "in" is only an inch mark before a bracket/separator ("26.25 in [667 mm]")
            rx = r"in(?=\s?[\[\(/,;x×]|\s*$)"
        else:
            rx = _form_regex(f)
        parts.append(f"(?i:{rx})" if ci else rx)
    return "(?:" + "|".join(parts) + ")"


UNIT_RX = unit_alternation()

#: count nouns that turn a bare number into a technical quantity ("3-man crew", "8 rounds")
COUNT_NOUNS = (
    "man", "men", "person", "persons", "people", "crew", "crewmen", "seat", "seats", "round", "rounds",
    "missile", "missiles", "troop", "troops", "soldier", "soldiers", "passenger", "passengers", "personnel",
    "wheel", "wheels", "axle", "axles", "cylinder", "cylinders", "gear", "gears", "speed", "speeds",
    "tube", "tubes", "cell", "cells", "launcher", "launchers", "rocket", "rockets", "shell", "shells",
    "charge", "charges", "target", "targets", "channel", "channels", "station", "stations", "hardpoint",
    "hardpoints", "pylon", "pylons", "engine", "engines", "blade", "blades", "module", "modules",
    "antenna", "antennas", "canister", "canisters", "torpedo", "torpedoes", "bomb", "bombs", "berth", "berths",
    "stretcher", "stretchers", "litter", "litters", "pallet", "pallets", "drone", "drones", "uav", "uavs",
    "barrel", "barrels", "magazine", "magazines", "cartridge", "cartridges", "shot", "shots", "salvo",
)
