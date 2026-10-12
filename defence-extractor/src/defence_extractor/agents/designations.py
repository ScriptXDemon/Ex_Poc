"""Model designations ("M322", "C7451", "RF-7850A-MR", "ORION 50S XLT", "T7B16"): recognising them in headings and
table labels, so catalogues whose models the entity model misses still get one entity per model (deterministic)."""

from __future__ import annotations

import re

# a token with letters and digits, at least 3 characters, not inside a longer word or number
DESIGNATION = re.compile(r"(?<![\w/.])(?=[A-Za-z0-9/-]*\d)(?=[A-Za-z0-9/-]*[A-Za-z])[A-Za-z0-9][A-Za-z0-9/-]{2,}(?<![/-])(?![\w.])")
# number + unit or a drive formula is a measurement, not a model: 120mm, 5.56mm, 16in, 6x6, 2nd
_NOT_MODEL = re.compile(r"(?i)^\d+(?:[.,]\d+)?(?:mm|cm|m|km|kg|g|t|in|ft|lb|lbs|kw|hp|kn|kv|v|w|hz|khz|mhz|ghz|nm|rpm|"
                        r"st|nd|rd|th|x\d+)$|^\d+x\d+$|^\d[\d.,]*[A-Za-z]{0,4}/[A-Za-z]{1,4}$|^\d+/\d+[A-Za-z]{0,3}$")
# headings that label an identifier of the product above them ("UNS No. C74500", "Product no. 4315021", "Part number")
LABEL_PREFIX = re.compile(r"(?i)^\s*(?:uns|product|part|item|order|catalog(?:ue)?|article|nsn|ref(?:erence)?|model|type|"
                          r"serial|drawing|spec(?:ification)?)\s*(?:no\.?|nr\.?|number|#|code)\b")
_LEAD = re.compile(r"^[\s›»•·▪■◆►\-–—*]+|^\d+(?:\.\d+)*\s+(?=[A-Za-z])")


def designation_tokens(text: str) -> set[str]:
    """Model-designation tokens in text, upper-cased ("M322", "C7451", "RF-7850A-MR")."""
    return {t.upper() for t in DESIGNATION.findall(text or "") if not _NOT_MODEL.match(t)}


def clean_heading(text: str) -> str:
    """A heading as a product name: without bullets, arrows or section numbers ("› M322" -> "M322")."""
    return _LEAD.sub("", (text or "").strip()).strip(" :.-–—")
