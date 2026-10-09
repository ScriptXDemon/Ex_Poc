"""Language detection (deterministic: seeded langdetect)."""

from __future__ import annotations

from langdetect import DetectorFactory, detect_langs
from langdetect.lang_detect_exception import LangDetectException

DetectorFactory.seed = 0


def detect_language(text: str) -> tuple[str | None, float]:
    sample = (text or "")[:6000]
    if len(sample.strip()) < 40:
        return None, 0.0
    try:
        langs = detect_langs(sample)
    except LangDetectException:
        return None, 0.0
    if not langs:
        return None, 0.0
    best = langs[0]
    code = best.lang.split("-")[0]
    return code, float(best.prob)
