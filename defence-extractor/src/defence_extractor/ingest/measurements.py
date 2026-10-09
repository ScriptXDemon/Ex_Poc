"""Deterministic measurement detector.

Finds number(+unit) expressions in text and assembles compound forms: ranges, bounds, dual units,
composites (L x W x H), ratios (VSWR 3.5:1), calibres (7.62x51 mm, .50 BMG, L52), counts ("3-man crew"),
percentages and vehicle drive configurations (8x8). Output spans are verbatim substrings of the input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..contracts.inventory import Comparator, Measurement, Quantity
from .units import COUNT_NOUNS, UNIT_RX, lookup_unit

NUM = (
    r"(?:\d{1,3}(?:[,\u00a0\u202f]\d{3})+(?:\.\d+)?"  # 11,660 / 2 893 (nbsp) / 1,000.5
    r"|\d{1,3}(?:\.\d{3})+(?:,\d+)?"  # 11.660 (EU thousands) / 1.000,5
    r"|\d+(?:[.,]\d+)?"  # 155 / 4.69 / 4,69
    r"|\.\d+)"  # .50
)
SIGN = r"[-+−±]?"
SP = r"[ \t\u00a0\u202f]?"  # optional single space between number and unit
UNIT_END = r"(?![A-Za-z0-9\u00C0-\u024F\u0400-\u04FF])"
U = rf"{UNIT_RX}{UNIT_END}"
QTY = rf"{SIGN}{NUM}{SP}-?{SP}{U}"
RANGE_SEP = r"\s*(?:-|–|—|‑|to|~|until|bis|à|a|до|至)\s*"
BOUND_PFX = (
    r"(?:up\s+to|upto|max(?:imum)?\.?|min(?:imum)?\.?|over|more\s+than|less\s+than|under|above|below|"
    r"approx(?:imately|\.)?|about|around|ca\.|circa|nearly|almost|in\s+excess\s+of|exceeding|greater\s+than|"
    r"at\s+least|>=|<=|≥|≤|>|<|~|≈|\+/-|±|bis\s+zu|jusqu'à|до)"
)

_PATTERNS: list[tuple[str, str, int]] = [
    # (kind, regex, priority) — higher priority wins on equal span length
    ("composite", rf"{SIGN}{NUM}{SP}(?:{U})?\s*[x×X\*]\s*{NUM}{SP}(?:{U})?(?:\s*[x×X\*]\s*{NUM}{SP}(?:{U})?)?", 9),
    ("range", rf"{SIGN}{NUM}{SP}(?:{U})?{RANGE_SEP}{SIGN}{NUM}{SP}-?{SP}{U}", 8),
    ("range", rf"(?:from|between|von|de|от)\s+{SIGN}{NUM}{SP}(?:{U})?\s*(?:to|and|bis|à|a|до)\s*{SIGN}{NUM}{SP}{U}", 8),
    ("dual", rf"{QTY}\s*[\(\[]\s*(?:{BOUND_PFX}\s*)?{QTY}\s*[\)\]]", 7),
    ("dual", rf"{QTY}\s*/\s*{QTY}", 6),
    ("calibre", r"\b\d{1,2}(?:[.,]\d{1,2})?\s?[x×]\s?\d{2,3}(?:\s?mm)?(?:\s?[A-Z][A-Za-z]+)?\b", 7),
    ("calibre", r"(?<![\w.])\.\d{2,3}\s?(?:cal\.?|caliber|calibre|BMG|ACP|AE|Win(?:chester)?|WM|Mag(?:num)?|Lapua(?:\s?Mag(?:num)?)?|NATO|S&W|SPC|Blackout|BLK|Norma(?:\s?Mag)?|PRC|Rem(?:ington)?(?:\s?Mag)?|Creedmoor)\b", 7),
    ("calibre", r"\b\d{1,3}(?:[.,]\d{1,2})?\s?mm\s?(?:NATO|Parabellum|Luger|Makarov|Auto|Short)\b", 7),
    ("calibre", r"\b\d{1,3}\s?(?:mm\s?)?/\s?\d{2}\s?(?:cal(?:ibre|iber)?s?)\b", 7),
    ("calibre", r"\bL\s?/?\s?\d{2}\b", 3),
    ("calibre", r"\b\d{2}\s?-?\s?(?:cal(?:ibre|iber)s?|calibres|calibers|caliber|calibre)\b", 6),
    ("calibre", r"(?i:\b\d{1,2}\s?(?:gauge|ga)\b)", 5),
    ("ratio", rf"\b{NUM}\s?:\s?1\b", 5),
    ("mach", rf"\bMach\s?{NUM}\b", 6),
    ("magnification", rf"(?:\b[xX×]\s?{NUM}\b|\b{NUM}\s?[xX×](?=\s|$|[,;)])(?!\s?\d))", 2),
    ("config", r"\b(?:4|6|8|10|12|16)\s?[x×]\s?(?:2|4|6|8|10|12|16)\b", 6),
    ("percent", rf"{SIGN}{NUM}\s?(?:%|(?i:percent|per\s?cent|pct)\b)", 5),
    ("count", rf"\b{NUM}\s?-?\s?(?i:(?:{'|'.join(COUNT_NOUNS)}))\b", 4),
    ("scalar", rf"{NUM}\s?\+\s?{U}", 6),  # 30+ nm
    ("scalar", QTY, 5),
]
_COMPILED = [(k, re.compile(rx), p) for k, rx, p in _PATTERNS]
_BOUND_BEFORE = re.compile(rf"({BOUND_PFX})\s*$", re.IGNORECASE)
_PLUS_AFTER = re.compile(r"^\s?\+")
_NUM_RX = re.compile(NUM)
_QTY_RX = re.compile(rf"(?P<num>{SIGN}{NUM}){SP}-?{SP}(?P<unit>{U})")
_YEARISH = re.compile(r"^(?:19|20)\d{2}$")

_COMPARATOR_WORDS = [
    (re.compile(r"up\s+to|upto|max|maximum|bis\s+zu|jusqu|до", re.I), Comparator.up_to),
    (re.compile(r"min|minimum|at\s+least", re.I), Comparator.at_least),
    (re.compile(r"over|more\s+than|above|in\s+excess|exceeding|greater|>(?!=)", re.I), Comparator.gt),
    (re.compile(r"less\s+than|under|below|<(?!=)", re.I), Comparator.lt),
    (re.compile(r">=|≥", re.I), Comparator.ge),
    (re.compile(r"<=|≤", re.I), Comparator.le),
    (re.compile(r"approx|about|around|ca\.|circa|nearly|almost|~|≈|±|\+/-", re.I), Comparator.approx),
]


@dataclass
class _Hit:
    kind: str
    start: int
    end: int
    prio: int
    extra: dict = field(default_factory=dict)


def parse_number(s: str, lang: str | None = None) -> float | None:
    """Parse a number written with locale-dependent separators. Returns None if it is not a number."""
    s = s.strip().replace("\u00a0", "").replace("\u202f", "").replace(" ", "").replace("−", "-")
    if not s:
        return None
    sign = 1.0
    if s[0] in "+-±":
        sign = -1.0 if s[0] == "-" else 1.0
        s = s[1:]
    eu = lang is not None and not lang.startswith("en")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3 and not eu):
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    elif "." in s:
        parts = s.split(".")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3 and eu and parts[0] != "0"):
            s = s.replace(".", "")
    try:
        return sign * float(s)
    except ValueError:
        return None


_PLUS_NUM = re.compile(r"(\d)\s?\+(?=\s?[^\d\s])")
_UNIT_AFTER = re.compile(rf"^{SP}-?{SP}(?P<unit>{U})")


def _quantities(span: str, lang: str | None) -> list[Quantity]:
    """Every number in the span, each with the unit that directly follows it (if any)."""
    out: list[Quantity] = []
    span = _PLUS_NUM.sub(r"\1", span)  # "30+ nm" -> "30 nm" (the comparator is kept on the Measurement)
    consumed = 0
    for m in _NUM_RX.finditer(span):
        if m.start() < consumed:
            continue  # digit inside a unit we already consumed ("m3", "km2")
        v = parse_number(m.group(0), lang)
        if v is None:
            continue
        s = m.start()
        if s > 0 and span[s - 1] in "-−" and (s == 1 or not span[s - 2].isdigit()):
            v = -v
        um = _UNIT_AFTER.match(span[m.end():])
        unit = um.group("unit") if um else None
        consumed = m.end() + (um.end() if um else 0)
        u = lookup_unit(unit) if unit else None
        out.append(
            Quantity(value=v, unit=unit, unit_key=u.key if u else None, dimension=u.dimension if u else None,
                     raw=span[s:consumed])
        )
    return out


def find_measurements(text: str, lang: str | None = None) -> list[Measurement]:
    """Return non-overlapping measurement spans in ``text`` (verbatim substrings)."""
    if not text or not any(ch.isdigit() for ch in text):
        return []
    hits: list[_Hit] = []
    for kind, rx, prio in _COMPILED:
        for m in rx.finditer(text):
            s, e = m.start(), m.end()
            frag = text[s:e].strip()
            if not frag or not any(ch.isdigit() for ch in frag):
                continue
            # trim trailing/leading spaces from span
            while s < e and text[s].isspace():
                s += 1
            while e > s and text[e - 1].isspace():
                e -= 1
            if kind == "composite" and not re.search(UNIT_RX, text[s:e]):
                continue  # composites need a unit somewhere (otherwise config / product code)
            if kind == "calibre" and text[s:e].startswith("L") and not _calibre_context(text, s):
                continue
            hits.append(_Hit(kind, s, e, prio))
    if not hits:
        return []
    # choose longest, then highest priority, non-overlapping
    hits.sort(key=lambda h: (-(h.end - h.start), -h.prio, h.start))
    chosen: list[_Hit] = []
    taken = [False] * (len(text) + 1)
    for h in hits:
        if any(taken[i] for i in range(h.start, h.end)):
            continue
        for i in range(h.start, h.end):
            taken[i] = True
        chosen.append(h)
    chosen.sort(key=lambda h: h.start)

    out: list[Measurement] = []
    for h in chosen:
        s, e = h.start, h.end
        comparator = Comparator.eq
        kind = h.kind
        # bound prefix immediately before the span ("up to 70 km", "> 35,000 ft")
        pre = text[max(0, s - 24) : s]
        bm = _BOUND_BEFORE.search(pre)
        if bm and kind in ("scalar", "range", "dual", "count", "percent", "mach"):
            word = bm.group(1)
            s = s - (len(pre) - bm.start(1))
            comparator = _comparator_for(word)
            if kind == "scalar":
                kind = "bound"
        span = text[s:e]
        if kind == "scalar" and "+" in span and re.search(rf"{NUM}\s?\+", span):
            comparator, kind = Comparator.at_least, "bound"
        qs = _quantities(span, lang)
        if kind in ("scalar", "bound") and len(qs) == 1 and qs[0].unit is None:
            continue
        if kind == "count" and qs and _YEARISH.match(f"{qs[0].value:.0f}") and qs[0].value > 1900:
            continue
        if kind in ("scalar", "bound") and len(qs) == 1 and qs[0].unit_key == "s" and 1900 <= qs[0].value <= 2099:
            continue  # "the 1990s"
        if kind in ("composite", "calibre") and "x" in span.lower().replace("×", "x"):
            kind = _classify_axb(span, lang) or kind
        dims = {q.dimension for q in qs if q.dimension}
        m = Measurement(
            text=span,
            start=s,
            end=e,
            kind=kind,
            comparator=comparator,
            quantities=qs,
            dimension=next(iter(dims)) if len(dims) == 1 else (_kind_dimension(kind) if not dims else "mixed"),
        )
        out.append(m)
    return out


_AXB = re.compile(rf"^\s*({NUM})\s?({UNIT_RX})?\s*[x×X\*]\s*({NUM})\s?(mm)?\s*$")


def _classify_axb(span: str, lang: str | None) -> str | None:
    """Disambiguate 'A x B' spans: cartridge designation (7.62x51, 30x173 mm), multiplier (2 x 30 mm) or composite."""
    m = _AXB.match(span)
    if not m:
        return None
    a, unit_a, b = parse_number(m.group(1), lang), m.group(2), parse_number(m.group(3), lang)
    if a is None or b is None or unit_a:
        return None
    if a >= 4.5 and 18 <= b <= 230 and b > a and float(b).is_integer():
        return "calibre"
    if float(a).is_integer() and a <= 16:
        return "multiplier"
    return None


def _comparator_for(word: str) -> Comparator:
    for rx, comp in _COMPARATOR_WORDS:
        if rx.search(word):
            return comp
    return Comparator.approx


def _kind_dimension(kind: str) -> str | None:
    return {
        "calibre": "length",
        "count": "count",
        "percent": "ratio",
        "ratio": "ratio",
        "config": "configuration",
        "mach": "speed",
        "magnification": "magnification",
        "multiplier": "count",
    }.get(kind)


_CAL_CTX = re.compile(r"(?i)barrel|gun|howitzer|cannon|calib|calibre|caliber|ordnance|tube")


def _calibre_context(text: str, pos: int) -> bool:
    window = text[max(0, pos - 60) : pos + 60]
    return bool(_CAL_CTX.search(window))


# ---------------------------------------------------------------------------------------------
# helpers used by dismissal rules and the value parser
# ---------------------------------------------------------------------------------------------
PRICE_RX = re.compile(
    r"(?i)(?:[$€£¥₹]|\b(?:usd|eur|gbp|inr|rs\.?|dollars?|euros?|rupees?)\b)\s?\d|\d\s?(?:million|billion|bn|mn|crore|lakh)\b"
)
DATE_RX = re.compile(
    r"(?i)\b(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{4})\b"
)
PHONE_RX = re.compile(r"(?:\+\d{1,3}[\s-]?)?(?:\(\d{2,4}\)[\s-]?)?\d{3,4}[\s-]\d{3,4}(?:[\s-]\d{2,4})?")


def looks_like_price(text: str) -> bool:
    return bool(PRICE_RX.search(text))


def looks_like_date(text: str) -> bool:
    return bool(DATE_RX.search(text))
