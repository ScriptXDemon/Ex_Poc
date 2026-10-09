"""Variant / footnote marker parser: binds markers (*, **, †, ¹, (1)) to their legend text.

Example (AW149):  value block "2,893 m (9,490 ft)* / 2,712 m (8,900 ft)**"
                  legend block "*General Electric CT7-2E1 / **Safran Aneto-1K"
-> MarkerBinding("*", "General Electric CT7-2E1"), MarkerBinding("**", "Safran Aneto-1K")
"""

from __future__ import annotations

import re

from ..contracts.document import BlockType, SemanticDocument
from ..contracts.entities import MarkerBinding

MARKER = r"(\*{1,4}|†{1,2}|‡|§|[¹²³⁴⁵⁶⁷⁸⁹]|\(\d{1,2}\)|\[\d{1,2}\])"
_LEGEND_START = re.compile(rf"^\s*{MARKER}\s*(?=[A-Za-z0-9(À-ɏ])")
_LEGEND_PAIR = re.compile(rf"(?:^|[\s/;,|])\s*{MARKER}\s*([^*†‡§¹²³⁴⁵⁶⁷⁸⁹/;|]{{2,160}})")
_USAGE = re.compile(rf"(?<=[\w\)\]%°″\"']){MARKER}(?![\w])")


def find_marker_bindings(doc: SemanticDocument) -> list[MarkerBinding]:
    legends: dict[str, tuple[str, str]] = {}
    legend_blocks: set[str] = set()
    for b in doc.blocks:
        if b.type == BlockType.heading or len(b.text) > 500:
            continue
        text = b.text
        pairs = _LEGEND_PAIR.findall(text)
        starts = bool(_LEGEND_START.match(text))
        if starts or (len(pairs) >= 2 and len({p[0] for p in pairs}) >= 2):
            for marker, legend in pairs if pairs else []:
                legend = legend.strip(" .:-–")
                if len(legend) < 2 or re.fullmatch(r"[\d\s.,]+", legend):
                    continue
                if marker not in legends:
                    legends[marker] = (legend, b.block_id)
                    legend_blocks.add(b.block_id)
    if not legends:
        return []
    usage: dict[str, list[str]] = {m: [] for m in legends}
    for b in doc.blocks:
        if b.block_id in legend_blocks:
            continue
        for m in _USAGE.findall(b.text):
            if m in usage and b.block_id not in usage[m]:
                usage[m].append(b.block_id)
    return [
        MarkerBinding(marker=m, legend_text=t, legend_block_id=bid, used_in_block_ids=usage.get(m, []))
        for m, (t, bid) in legends.items()
        if usage.get(m)
    ]
