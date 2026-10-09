"""Shared runtime + helpers for all agents."""

from __future__ import annotations

import asyncio
import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel

from ..config import Settings
from ..contracts.common import EvidenceRef
from ..contracts.document import SemanticDocument
from ..llm import Gateway, LLMResult, SchemaError
from ..ontology import Ontology
from .prompts import SHARED_RULES

T = TypeVar("T", bound=BaseModel)


@dataclass
class Runtime:
    settings: Settings
    gw: Gateway
    onto: Ontology
    embedder: object | None = None
    stage_usage: dict = field(default_factory=dict)


async def ask(rt: Runtime, *, agent: str, role: str, header: str, evidence: str, task: str, schema: type[T],
              doc_id: str, reasoning: str = "off", max_tokens: int | None = None,
              images: list[str] | None = None) -> LLMResult[T]:
    user = header.rstrip() + "\n\n" + (evidence.rstrip() + "\n\n" if evidence else "") + task.strip()
    return await rt.gw.call(agent=agent, role=role, system=SHARED_RULES, user=user, schema=schema, doc_id=doc_id,
                            reasoning=reasoning, max_tokens=max_tokens, images=images)


# ---------------------------------------------------------------------------------------------------
# text helpers
# ---------------------------------------------------------------------------------------------------
_WS = re.compile(r"\s+")


def doc_spent(rt: Runtime, st) -> float:
    """LLM spend on this document in this run: everything the gateway has paid for it, including calls made by
    earlier worker processes (seeded from the run's call log at start-up)."""
    u = rt.gw.by_doc.get(st.ref.doc_id)
    return u.cost_usd if u else 0.0


def over_doc_budget(rt: Runtime, st) -> bool:
    cap = getattr(rt.settings, "doc_budget_usd", 0) or 0
    if cap > 0 and doc_spent(rt, st) >= cap:
        return True
    tcap = getattr(rt.settings, "doc_budget_tokens", 0) or 0  # self-hosted endpoints report no cost: cap tokens
    u = rt.gw.by_doc.get(st.ref.doc_id)
    return bool(tcap > 0 and u is not None and u.completion_tokens >= tcap)


async def bisect_on_overflow(run: Callable[[list[str]], Awaitable], ids: list[str], depth: int = 0, max_depth: int = 3) -> list:
    """``run(ids)``; when the answer for these blocks does not fit (schema failure after the gateway's repair, i.e. a
    dense catalogue chunk overflowing max_tokens), split the blocks in half and run each half. Returns the results."""
    try:
        return [await run(ids)]
    except SchemaError:
        if depth >= max_depth or len(ids) < 2:
            raise
        mid = len(ids) // 2
        a, b = await asyncio.gather(bisect_on_overflow(run, ids[:mid], depth + 1, max_depth),
                                    bisect_on_overflow(run, ids[mid:], depth + 1, max_depth))
        return a + b


_HYPHENS = re.compile("[\u2010\u2011\u2012\u2043\ufe63\uff0d]")


def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace(" ", " ").replace(" ", " ").replace(" ", " ")
    s = s.replace("–", "-").replace("—", "-").replace("−", "-").replace("’", "'").replace("“", '"').replace("”", '"')
    s = _HYPHENS.sub("-", s).replace("\u00ad", "")  # non-breaking / figure hyphens (gpt-oss writes U+2011), soft hyphen
    return _WS.sub(" ", s).strip().lower()


def norm_name(s: str) -> str:
    s = re.sub(r"[®™©]", "", s or "")
    s = norm_text(s)
    return s.strip(" .,:;-'\"()")


def find_quote(doc: SemanticDocument, block_ids: list[str], value: str) -> list[EvidenceRef]:
    """Locate ``value`` verbatim (whitespace/case-insensitive) inside the cited blocks."""
    bmap = doc.block_map()
    refs: list[EvidenceRef] = []
    nv = norm_text(value)
    for bid in block_ids:
        b = bmap.get(bid)
        if b is None:
            continue
        i = b.text.find(value)
        if i >= 0:
            refs.append(EvidenceRef(block_id=bid, quote=value, char_start=i, char_end=i + len(value)))
            continue
        nt = norm_text(b.text)
        j = nt.find(nv) if nv else -1
        if j >= 0:
            # approximate span in original text: map by proportional search of first token
            first = value.strip().split(" ")[0] if value.strip() else ""
            k = b.text.lower().find(first.lower()) if first else -1
            start = k if k >= 0 else None
            refs.append(EvidenceRef(block_id=bid, quote=value, char_start=start,
                                    char_end=(start + len(value)) if start is not None else None))
        else:
            refs.append(EvidenceRef(block_id=bid, quote=None))
    return refs


def value_in_blocks(doc: SemanticDocument, block_ids: list[str], value: str) -> bool:
    bmap = doc.block_map()
    nv = norm_text(value)
    if not nv:
        return False
    for bid in block_ids:
        b = bmap.get(bid)
        if b is not None and nv in norm_text(b.text):
            return True
    return False


def mention_scan(doc: SemanticDocument, names: list[str], limit: int = 60) -> list[str]:
    pats = []
    for n in names:
        n = (n or "").strip()
        if len(n) < 2:
            continue
        esc = re.escape(n)
        pats.append(re.compile(rf"(?<![\w]){esc}(?![\w])", re.IGNORECASE))
    out: list[str] = []
    for b in doc.blocks:
        if b.attributes.get("dup_of"):
            continue
        if any(p.search(b.text) for p in pats):
            out.append(b.block_id)
            if len(out) >= limit:
                break
    return out
