"""Layout repairs on a parsed PDF (deterministic, no model calls).

- broken words: "coeffi- cient" -> "coefficient", "cost- effective" -> "cost-effective", "space- and" kept
- dot leaders: a contents run ("STAR 30C/BP ........ 91") becomes navigation; a spec line
  ("Burn time, sec ........ 53.6") becomes "Burn time, sec: 53.6"
- spec cards: label, unit and number printed as separate boxes ("Muzzle velocity" / "m / sec" / "910" / "fps" /
  "2985") become one field ("Muzzle velocity: 910 m / sec; 2985 fps")
- text-layer quality per page (garbage characters, word-like tokens), to send a page with a broken text layer to OCR
"""

from __future__ import annotations

import re

from ..contracts.document import BlockType, ScopeHint, SemanticBlock

# --- broken words -----------------------------------------------------------------------------------
_BROKEN = re.compile(r"\b([A-Za-z]{2,})- ([a-z]{2,})\b")
_SUSPENDED = {"and", "or", "to", "nor", "as", "und", "oder", "bis", "et", "ou", "y", "o", "e"}  # "short- and long-range"
_WORD = re.compile(r"[A-Za-z]{2,}")


def vocabulary(texts) -> set[str]:
    """Lower-cased words the document prints whole (to tell a compound from a word broken at the line end)."""
    vocab: set[str] = set()
    for t in texts:
        vocab.update(w.lower() for w in _WORD.findall(_BROKEN.sub(" ", t or "")))
    return vocab


def dehyphenate(text: str, vocab: set[str]) -> str:
    def fix(m: re.Match) -> str:
        left, right = m.group(1), m.group(2)
        if right in _SUSPENDED:
            return m.group(0)
        if left.lower() in vocab and right in vocab and (left + right).lower() not in vocab:
            return f"{left}-{right}"  # a compound broken at its own hyphen ("cost- effective")
        return left + right
    return _BROKEN.sub(fix, text) if text and "- " in text else text


# --- text-layer quality ------------------------------------------------------------------------------
_CID = re.compile(r"\(cid:\d+\)|/g\d{2,}|/uni[0-9A-Fa-f]{4}")
_WORDLIKE = re.compile(r"[A-Za-zÀ-ÿĀ-žА-яЀ-ӿͰ-Ͽ؀-ۿ぀-ヿ一-鿿가-힯]{2,}")
_NUMLIKE = re.compile(r"^[\d.,%°/:;()+\-–×x*]+$")


def text_quality(text: str) -> float:
    """0 (garbage) .. 1 (clean): share of good characters times share of word- or number-like tokens."""
    s = (text or "").strip()
    if not s:
        return 0.0
    bad = sum(1 for ch in s if ch == "�" or 0xE000 <= ord(ch) <= 0xF8FF or (ord(ch) < 32 and ch not in "\n\r\t"))
    bad += 5 * len(_CID.findall(s))
    toks = s.split()
    good = sum(1 for t in toks if _WORDLIKE.search(t) or _NUMLIKE.match(t))
    return round(max(0.0, 1.0 - bad / max(1, len(s))) * good / max(1, len(toks)), 3)


# --- dot leaders -------------------------------------------------------------------------------------
_DOTS = re.compile(r"^(?P<label>.*?[^\s.…])\s*(?:\.{4,}|…{2,}|(?:\.\s){3,}\.?)\s*(?P<value>\S.*)$")
_CONTENTS = re.compile(r"(?i)^(?:table of )?contents?$|^index$|^inhalt(?:sverzeichnis)?$|^sommaire$|^indice$|^contenido$")


def _leader(b: SemanticBlock) -> re.Match | None:
    if b.type not in (BlockType.paragraph, BlockType.other, BlockType.key_value, BlockType.list_item) or len(b.text) > 240:
        return None
    return _DOTS.match(b.text)


def mark_contents_and_leaders(blocks: list[SemanticBlock]) -> None:
    """Contents runs -> navigation scope; other dot-leader lines -> "label: value" key/value blocks (in place)."""
    i, n = 0, len(blocks)
    while i < n:
        m = _leader(blocks[i])
        if not m:
            i += 1
            continue
        j, pages = i, []
        while j < n and (mj := _leader(blocks[j])) and re.fullmatch(r"\d{1,4}", mj.group("value").strip()):
            pages.append(int(mj.group("value")))
            j += 1
        if j - i >= 4 and all(a <= b for a, b in zip(pages, pages[1:])) and len(set(pages)) >= 3:
            for b in blocks[i:j]:  # a table of contents: page numbers, not specifications
                b.scope = ScopeHint.navigation
                b.attributes["contents"] = "1"
            i = j
            continue
        b = blocks[i]
        b.text = f"{m.group('label').strip()}: {m.group('value').strip()}"
        b.type = BlockType.key_value
        b.attributes["dot_leader"] = "1"
        i += 1
    section_is_contents = {b.section_id for b in blocks if b.type == BlockType.heading and _CONTENTS.match(b.text.strip())}
    for b in blocks:
        if b.section_id in section_is_contents and b.type != BlockType.heading:
            b.scope = ScopeHint.navigation
            b.attributes["contents"] = "1"


# --- spec cards --------------------------------------------------------------------------------------
_NUM = re.compile(r"^[<>≈~±≤≥]?\s*[-−+]?\d[\d.,]*\s*\*{0,3}$|^[-–—]$|^n/?a$", re.I)
_VALUNIT = re.compile(r"^[<>≈~±≤≥]?\s*[-−+]?\d[\d.,]*\s*[A-Za-z%°µ\"'/][A-Za-z%°µ\"'/. ]{0,10}\s*\*{0,3}$")
_UNIT = re.compile(r"^(?=.*[A-Za-z%°µ])[A-Za-z0-9%°µ/ .\-²³]{1,10}$")
_LABEL = re.compile(r"^(?=.*[A-Za-z]{3})[A-Za-z][A-Za-z ,()/&\-]{3,40}$")


_UNIT_WORDS = {
    "m", "mm", "cm", "km", "nm", "mi", "in", "inch", "inches", "ft", "yd", "s", "sec", "min", "h", "hr", "hrs", "kg", "g",
    "gr", "grains", "t", "lb", "lbs", "oz", "fps", "mph", "kmh", "kph", "kn", "kt", "kts", "knots", "rpm", "rds", "kw", "hp",
    "w", "v", "a", "mah", "ah", "wh", "hz", "khz", "mhz", "ghz", "n", "kn", "j", "kj", "psi", "bar", "pa", "kpa", "mpa",
    "°c", "°f", "c", "f", "%", "l", "ml", "gal", "°", "mil", "mrad", "moa", "db", "dbm", "x", "mph", "m²", "m³", "cc",
}


def _is_unit(t: str) -> bool:
    toks = [x for x in re.split(r"[\s/·]+", t.lower()) if x]
    return bool(toks) and len(t) <= 12 and all(x in _UNIT_WORDS or re.fullmatch(r"[a-z]\d{1,2}", x) for x in toks)


def _kind(text: str) -> str:
    t = text.strip()
    if _NUM.match(t):
        return "num"
    if _VALUNIT.match(t):
        return "valunit"
    if _is_unit(t):  # "m / sec", "fps", "G1": before labels, which they also look like
        return "unit"
    if _LABEL.match(t) and (len(t.split()) >= 2 or len(t) >= 6):
        return "label"
    if _UNIT.match(t):
        return "unit"
    return "text"


def join_spec_cards(blocks: list[SemanticBlock]) -> list[SemanticBlock]:
    """Runs of short label / unit / number boxes of one section and page become "label: value unit; value unit"
    blocks (the label block keeps its id; the joined boxes are dropped and listed in attributes["joined"])."""
    out: list[SemanticBlock] = []
    i, n = 0, len(blocks)

    def short(b: SemanticBlock) -> bool:
        return (b.type in (BlockType.paragraph, BlockType.other, BlockType.key_value) and b.scope == ScopeHint.main
                and b.table_id is None and 0 < len(b.text.strip()) <= 32)

    while i < n:
        b = blocks[i]
        if not short(b):
            out.append(b)
            i += 1
            continue
        j = i
        while j < n and short(blocks[j]) and blocks[j].section_id == b.section_id and blocks[j].page == b.page:
            j += 1
        run = blocks[i:j]
        kinds = [_kind(x.text) for x in run]
        nums = sum(1 for k in kinds if k in ("num", "valunit"))
        if len(run) < 4 or "label" not in kinds or nums == 0 or nums < 0.25 * len(run) or "text" in kinds:
            out.extend(run)
            i = j
            continue
        groups: list[dict] = []
        cur: dict | None = None
        pending_unit: str | None = None
        for blk, k in zip(run, kinds):
            t = blk.text.strip()
            if k == "label":
                cur = {"label": blk, "parts": [], "ids": [blk.block_id]}
                groups.append(cur)
                pending_unit = None
            elif cur is None:
                groups.append({"label": None, "block": blk})  # before the first label: keep as it is
            elif k == "unit":
                if cur["parts"] and cur["parts"][-1][1] is None and pending_unit is None and k == "unit" and \
                        cur.get("last") == "num":
                    cur["parts"][-1] = (cur["parts"][-1][0], t)  # "910" then "m/s"
                else:
                    pending_unit = t
                cur["ids"].append(blk.block_id)
                cur["last"] = "unit"
            elif k == "num":
                cur["parts"].append((t, pending_unit))
                pending_unit = None
                cur["ids"].append(blk.block_id)
                cur["last"] = "num"
            elif k == "valunit":
                if cur["parts"] or pending_unit:  # a value with its own unit after this label's values: its own line
                    groups.append({"label": None, "block": blk})
                    cur = None
                else:
                    cur["parts"].append((t, None))
                    cur["ids"].append(blk.block_id)
                    cur["last"] = "num"
        for g in groups:
            if g.get("label") is None:
                out.append(g["block"])
                continue
            lab = g["label"]
            if not g["parts"]:
                out.append(lab)
                continue
            vals = "; ".join(f"{v} {u}" if u else v for v, u in g["parts"])
            lab.text = f"{lab.text.strip()}: {vals}"
            lab.type = BlockType.key_value
            lab.attributes.pop("dup_of", None)
            lab.attributes["joined"] = ",".join(g["ids"][1:])
            out.append(lab)
        i = j
    for k, b in enumerate(out):
        b.order = k
    return out
