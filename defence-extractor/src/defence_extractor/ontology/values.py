"""A9 value parser: value_text -> StructuredValue (derived data; value_text itself is never modified)."""

from __future__ import annotations

import re
from functools import lru_cache

from ..contracts.facts import StructuredValue, ValuePart
from ..contracts.inventory import Comparator, Measurement, Quantity
from ..ingest.measurements import find_measurements
from ..ingest.units import NORMALIZED_UNIT, UNITS

_ENGINE_CTX = re.compile(r"(?i)\b(?:engine|torque|power|@|kw|hp|bhp|motor)\b")
_NANO_CTX = re.compile(r"(?i)wavelength|laser|nanomet|spectral|µm|μm")
_BOOL = re.compile(r"(?i)^(yes|no|optional|standard|n/?a|available|not available)$")


@lru_cache(maxsize=1)
def _ureg():
    import pint

    ureg = pint.UnitRegistry(autoconvert_offset_to_baseunit=True)
    return ureg


_PINT_BY_KEY = {u.key: u.pint for u in UNITS}


def _normalize(q: Quantity, context: str) -> ValuePart:
    part = ValuePart(value=q.value, unit=q.unit)
    if q.unit_key is None or q.dimension is None:
        return part
    pint_unit = _PINT_BY_KEY.get(q.unit_key)
    if q.unit_key == "nm_ambiguous":
        pint_unit = "nanometer" if _NANO_CTX.search(context) else "nautical_mile"
    target = NORMALIZED_UNIT.get(q.dimension)
    if not pint_unit or not target:
        if q.dimension == "rate":
            part.normalized_value, part.normalized_unit = q.value, "rounds/min"
        return part
    try:
        ureg = _ureg()
        val = ureg.Quantity(q.value, pint_unit).to(target)
        part.normalized_value = round(float(val.magnitude), 6)
        part.normalized_unit = target.replace("**", "^")
    except Exception:
        pass
    return part


def _primary(ms: list[Measurement], text: str, expected: str | None = None) -> Measurement | None:
    """The value's main measurement: the first one (qualifiers such as '@ 2500 rpm' follow it), or the first whose
    dimension matches the expected parameter dimension."""
    if not ms:
        return None
    if expected:
        for m in ms:
            if m.dimension == expected or any(q.dimension == expected for q in m.quantities):
                return m
    return ms[0]


def parse_value(value_text: str, lang: str | None = None, context: str = "") -> StructuredValue:
    text = (value_text or "").strip()
    if not text:
        return StructuredValue(kind="text", text="")
    if _BOOL.match(text):
        return StructuredValue(kind="boolean", text=text)
    ms = find_measurements(text, lang)
    m = _primary(ms, text)
    if m is None:
        return StructuredValue(kind="text", text=text)
    ctx = f"{context} {text}"
    qs = m.quantities
    sv = StructuredValue(comparator=m.comparator, dimension=m.dimension if m.dimension != "mixed" else None)
    rest = (text[: m.start] + " " + text[m.end :]).strip(" ,;:-()")
    if rest:
        sv.text = rest
    if m.kind in ("scalar", "bound", "mach", "percent", "count"):
        sv.kind = "bound" if m.comparator not in (Comparator.eq,) else ("count" if m.kind == "count" else "scalar")
        if qs:
            sv.nominal = _normalize(qs[0], ctx)
            if m.kind == "percent":
                sv.nominal.unit = "%"
    elif m.kind == "range":
        sv.kind = "range"
        if len(qs) >= 2:
            unit_q = qs[-1]
            lo = qs[0] if qs[0].unit else Quantity(value=qs[0].value, unit=unit_q.unit, unit_key=unit_q.unit_key,
                                                     dimension=unit_q.dimension, raw=qs[0].raw)
            sv.min, sv.max = _normalize(lo, ctx), _normalize(unit_q, ctx)
        elif qs:
            sv.max = _normalize(qs[0], ctx)
    elif m.kind == "dual":
        sv.kind = "scalar" if m.comparator == Comparator.eq else "bound"
        if qs:
            sv.nominal = _normalize(qs[0], ctx)
            sv.alternates = [_normalize(q, ctx) for q in qs[1:]]
    elif m.kind == "composite":
        sv.kind = "composite"
        unit_q = next((q for q in reversed(qs) if q.unit), None)
        for q in qs:
            if not q.unit and unit_q is not None:
                q = Quantity(value=q.value, unit=unit_q.unit, unit_key=unit_q.unit_key, dimension=unit_q.dimension, raw=q.raw)
            sv.components.append(_normalize(q, ctx))
    elif m.kind == "multiplier":
        sv.kind = "count"
        nums = re.findall(r"\d+(?:[.,]\d+)?", m.text)
        sv.multiplier = float(nums[0]) if nums else None
        unit_q = next((q for q in qs if q.unit), None)
        if unit_q is not None:
            sv.nominal = _normalize(unit_q, ctx)
    elif m.kind == "ratio":
        sv.kind = "ratio"
        sv.text = m.text if not sv.text else f"{m.text} {sv.text}"
    else:  # calibre, config, magnification -> keep textual
        sv.kind = "text"
        sv.text = text
        if m.kind == "calibre":
            mm = re.match(r"\s*(\d+(?:[.,]\d+)?)\s?mm", m.text)
            if mm:
                sv.nominal = ValuePart(value=float(mm.group(1).replace(",", ".")), unit="mm",
                                       normalized_value=float(mm.group(1).replace(",", ".")) / 1000.0,
                                       normalized_unit="meter")
                sv.dimension = "length"
    if sv.dimension == "rate" and _ENGINE_CTX.search(ctx):
        sv.dimension = "rotational_speed"
    return sv


def dimension_of(value_text: str, lang: str | None = None, expected: str | None = None) -> str | None:
    ms = find_measurements(value_text or "", lang)
    m = _primary(ms, value_text or "", expected)
    if m is None:
        return None
    if m.dimension == "mixed":
        dims = [q.dimension for q in m.quantities if q.dimension]
        return dims[0] if dims else None
    return m.dimension
