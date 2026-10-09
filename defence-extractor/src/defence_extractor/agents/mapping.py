"""A8 ontology mapping + A9 structuring (value parsing, multi-value splitting, composite decomposition)."""

from __future__ import annotations

import asyncio

import re

from rapidfuzz import fuzz, process

from ..contracts.common import SPEC_CLASSES, FactClass, OntologyStatus
from ..contracts.facts import CandidateFact, ParameterRef
from ..ontology import OntologyParameter, normalize_label
from ..ontology.values import dimension_of, parse_value
from ..pipeline.state import DocState
from .base import Runtime, ask
from .discovery import doc_header
from .prompts import A8_TASK
from .schemas import A8Out

# dimensions that are interchangeable for mapping purposes
_COMPAT = {("count", None), ("length", "length"), ("rate", "rotational_speed")}


_BARE_NUMBER = re.compile(r"[~≈<>±+]?\s*\d[\d.,\s]*(?:\s*(?:~|-|–|to)\s*\d[\d.,\s]*)?(?:\s*\([^)]*\))?")


def label_unit_dimension(label: str | None, lang: str | None = None) -> str | None:
    """Dimension of a unit given in the label rather than the cell: "Weight (approx. Kg)", "Barrel length (mm)"."""
    for inner in re.findall(r"\(([^)]{1,30})\)", label or ""):
        for tok in re.split(r"[\s,;]+", inner):
            tok = tok.strip(".")
            if tok and not tok.isdigit():
                d = dimension_of(f"1 {tok}", lang)
                if d is not None:
                    return d
    return None


def dims_compatible(param: OntologyParameter, value_text: str, lang: str | None, label: str | None = None) -> bool:
    if not param.dimension:
        return True
    vd = dimension_of(value_text, lang, expected=param.dimension)
    if vd is None and label and _BARE_NUMBER.fullmatch(value_text.strip()):
        vd = label_unit_dimension(label, lang)  # table cell "8" under header "Weight (approx. Kg)"
    if vd is None:
        # a numeric parameter with no measurable value: allowed only for count/text-typed params
        return param.type == "text" or param.dimension in ("count", "angle", "temperature")
    if vd == param.dimension:
        return True
    if param.dimension == "count" and vd in ("count", None):
        return True
    if param.dimension == "speed" and vd == "speed":
        return True
    return (vd, param.dimension) in _COMPAT


_PHYSICAL_DIMS = {"length", "mass", "speed", "power", "torque", "force", "pressure", "time", "volume", "area",
                  "frequency", "temperature", "energy", "rate"}


def _fits(p: OntologyParameter, value_text: str, lang: str | None, label: str | None = None) -> bool:
    """Can this value be an instance of parameter p? (unit/dimension compatibility, text params refuse bare numbers)"""
    if p.dimension:
        return dims_compatible(p, value_text, lang, label)
    vd = dimension_of(value_text, lang)
    if vd in _PHYSICAL_DIMS and len(value_text) <= 24:
        return False  # e.g. "541 Nm" is not a propulsion_type
    return True


_STOP = {"of", "the", "and", "or", "max", "maximum", "min", "minimum", "nominal", "approx", "total", "overall"}


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", normalize_label(s)) if t and t not in _STOP}


def _label_agrees(onto, label: str, p: OntologyParameter) -> bool:
    """Same concept: an alias of p, or the same content words (token-based: "Safety temperature" is not
    "Service temperature" / operating_temperature even though the strings are similar)."""
    k = normalize_label(label)
    if not k:
        return True
    if p.id in onto.alias_index.get(k, []):
        return True
    lt = _tokens(label)
    return bool(lt) and any(lt == _tokens(a) for a in [p.name, p.id.replace("_", " "), *p.aliases, *p.legacy])


# group headings and catch-all labels: a value under "Dimensions" or "Performance" is a width, a range, ... —
# the label does not name its slot, so the extractor's (context-aware) proposal does
_GROUP_LABELS = {"dimensions", "dimension", "performance", "capacity", "capacities", "characteristics", "general",
                 "general data", "technical data", "technical specifications", "specifications", "specification",
                 "specs", "data", "main characteristics", "key specifications", "key features", "features", "mobility",
                 "physical", "physical characteristics", "weights", "weights and dimensions", "armament", "protection",
                 "power", "propulsion", "powerpack", "power pack", "drivetrain", "automotive", "configuration",
                 "parameters", "properties", "technical properties", "details", "overview", "description", "envelope"}


def _generic_label(label: str | None) -> bool:
    k = normalize_label(label or "")  # (drops bracketed text: "Dimensions (LxWxH)" -> "dimensions")
    if not k:
        return True
    if k in _GROUP_LABELS or k.rstrip("s") in _GROUP_LABELS:
        return True
    # composite headings: "Crew and Capacity", "Range & Altitude", "Length & Dia", "Weight / Dimensions"
    base = re.sub(r"\([^)]*\)", " ", label or "")
    return bool(re.search(r"&|\+|\band\b|\w\s*/\s*\w", base, re.I)) and len(_tokens(base)) >= 2


def _refines(label: str | None, p: OntologyParameter) -> bool:
    """The proposal is a more specific form of the label: "Speed" -> cruise_speed, "Capacity" -> troop_capacity."""
    lt = _tokens(label or "")
    pt = _tokens(p.id.replace("_", " "))
    return bool(lt) and lt < pt


def _ref(p: OntologyParameter, label: str | None) -> ParameterRef:
    return ParameterRef(id=p.id, status=OntologyStatus(p.status if p.status in ("canonical", "candidate") else "canonical"),
                        source_label=label, display_name=p.name, category_path=[p.family])


_MAX_NAME_WORDS = 6


def _words(x: str | None) -> int:
    return len([w for w in (x or "").split("_") if w])


def _dynamic(f: CandidateFact) -> ParameterRef:
    """A new (dynamic) parameter: named by the extractor's id or the page label, always a short name for what the value
    measures (a sentence-like name is cut to its first words and flagged for the verifier)."""
    from ..ontology import load_ontology

    label_id = re.sub(r"[^a-z0-9]+", "_", normalize_label(f.source_parameter)).strip("_")
    hint = f.parameter_hint if f.parameter_hint and _words(f.parameter_hint) <= _MAX_NAME_WORDS else None
    label_ok = label_id if label_id and _words(label_id) <= _MAX_NAME_WORDS else None
    pid = hint or label_ok
    if pid is None:
        raw = label_id or f.parameter_hint or "property"
        pid = "_".join([w for w in raw.split("_") if w][:4]) or "property"
        f.anomalies.append("long_parameter_name")
    if load_ontology().get(pid) is not None:  # never reuse a canonical id for a dynamic property
        pid = label_ok if label_ok and load_ontology().get(label_ok) is None else f"{pid}_other"
    display = f.source_parameter if f.source_parameter and len(f.source_parameter.split()) <= 8 else pid.replace("_", " ")
    return ParameterRef(id=pid, status=OntologyStatus.dynamic, source_label=f.source_parameter,
                        display_name=display, category_path=["dynamic"])


async def a8_mapping(rt: Runtime, st: DocState, facts: list[CandidateFact] | None = None) -> None:
    onto = rt.onto
    lang = st.ingest.profile.language
    facts = facts if facts is not None else st.facts
    pending: list[CandidateFact] = []
    for f in facts:
        if f.parameter is not None:
            continue
        if f.fact_class in (FactClass.marketing_claim, FactClass.procurement_information):
            f.parameter, f.mapping_confidence = _dynamic(f), 1.0
            continue
        # candidates: the page's own label first, then the extractor's hint (trusted when the label agrees with it,
        # or when the label points at a parameter the value cannot be — e.g. row label "Engine" for "541 Nm")
        # A specific label that names a canonical parameter wins over the proposal; a missing, generic or composite
        # label ("Dimensions", "Crew and Capacity"), or one the proposal refines ("Speed" -> cruise_speed), defers to it.
        label_params = onto.lookup_label(f.source_parameter)
        hp = onto.get(f.parameter_hint) or next(iter(onto.lookup_label(f.parameter_hint)), None)
        label_fit = [p for p in label_params if _fits(p, f.value_text, lang, f.source_parameter)]
        hint_ok = hp is not None and _fits(hp, f.value_text, lang, f.source_parameter)
        lab = f.source_parameter
        hint_first = hint_ok and (_generic_label(lab) or _refines(lab, hp))
        hint_also = hint_ok and (_label_agrees(onto, lab, hp) or (bool(label_params) and not label_fit))
        cands: list[tuple[OntologyParameter, float]] = [(hp, 0.88)] if hint_first else []
        cands += [(p, 0.9) for p in label_fit if not (hint_first and p.id == hp.id)]
        if hint_also and not hint_first and hp.id not in {p.id for p, _ in cands}:
            cands.append((hp, 0.88))
        chosen = cands[0] if cands else None
        if chosen is None and (label_params or hp is not None):
            cands = [(p, 0.7) for p in [*label_params, *([hp] if hp else [])]]
        if chosen is not None:
            f.parameter, f.mapping_confidence = _ref(chosen[0], f.source_parameter), chosen[1]
        elif cands and not any(dims_compatible(p, f.value_text, lang, f.source_parameter) for p, _ in cands):
            f.parameter, f.mapping_confidence = _dynamic(f), 0.75  # unknown better than wrong (dimension clash)
            f.anomalies.append(f"dimension_mismatch:{cands[0][0].id}")
        elif not cands and f.fact_class not in SPEC_CLASSES:
            f.parameter, f.mapping_confidence = _dynamic(f), 0.95  # text fact (capability/feature/...): no canonical slot
        else:
            pending.append(f)
    if not pending:
        return
    # LLM decides the rest, with a fuzzy shortlist of candidate parameters per unique label
    keys: dict[str, list[CandidateFact]] = {}
    for f in pending:  # one decision per (label, proposal): facts under one heading can be different parameters
        k = normalize_label(f.source_parameter or f.parameter_hint or "") or f.parameter_hint or "?"
        keys.setdefault(f"{k}|{f.parameter_hint or ''}", []).append(f)
    choices = {}
    for p in onto.params.values():
        for a in [p.id.replace("_", " "), p.name, *p.aliases]:
            choices[normalize_label(a)] = p.id
    names = list(choices.keys())
    items = []
    for i, (k, fs) in enumerate(keys.items()):
        f0 = fs[0]
        short = []
        if f0.parameter_hint and onto.get(f0.parameter_hint):
            short.append(f0.parameter_hint)  # the extractor's proposal is always a candidate
        for match, score, _ in process.extract(k.split("|")[0], names, scorer=fuzz.token_set_ratio, limit=8):
            pid = choices[match]
            if pid not in short and score >= 55:
                short.append(pid)
        if not short:  # nothing in the ontology resembles it: a genuinely new (dynamic) property
            for f in fs:
                f.parameter, f.mapping_confidence = _dynamic(f), 0.9
            continue
        items.append((f"K{i}", k, fs, short[:5]))
    if not items:
        return
    async def map_batch(batch: list) -> dict:
        lines = []
        for key, k, fs, short in batch:
            f0 = fs[0]
            lines.append(f"{key} | label: {f0.source_parameter or '-'} | proposed: {f0.parameter_hint or '-'} | example value: "
                         f"{f0.value_text[:80]} | candidates: " + "; ".join(onto.get(s).prompt_line()[:160] for s in short))
        try:
            async with gate:
                res = await ask(rt, agent="A8", role="main", header=doc_header(st, []), evidence="",
                                task=A8_TASK.replace("{items}", "\n".join(lines)), schema=A8Out, doc_id=st.ref.doc_id,
                                reasoning="off", max_tokens=4000)
            return {m.key: m for m in res.parsed.mappings}
        except Exception as e:
            st.errors.append(f"A8: {type(e).__name__}: {e}")
            return {}

    # a catalogue has hundreds of distinct labels: one answer for all of them overflows (truncated JSON -> no
    # mapping at all), so labels go in batches of 60, decided in parallel
    gate = asyncio.Semaphore(6)
    parts = await asyncio.gather(*[map_batch(items[i:i + A8_BATCH]) for i in range(0, len(items), A8_BATCH)])
    dec = {k: v for part in parts for k, v in part.items()}
    for key, _k, fs, short in items:
        d = dec.get(key)
        for f in fs:
            p = onto.get(d.parameter_id) if d and d.parameter_id in short else None
            if p is not None and dims_compatible(p, f.value_text, lang, f.source_parameter):
                f.parameter, f.mapping_confidence = _ref(p, f.source_parameter), max(0.0, min(1.0, d.confidence))
            else:
                ref = _dynamic(f)
                if d and d.dynamic_name:
                    name = re.sub(r"[^a-z0-9_]+", "_", d.dynamic_name.lower()).strip("_")
                    if name and onto.get(name) is None:
                        ref.id = name
                # the LLM looked at the candidates and rejected them: a confident "dynamic" decision
                f.parameter, f.mapping_confidence = ref, (0.9 if d is not None else 0.75)
            f.agents.append("A8")


A8_BATCH = 60


# ---------------------------------------------------------------------------------------------------
# A9
# ---------------------------------------------------------------------------------------------------
_LIST_SEP = re.compile(r"\s*(?:,|;|\band\b|\bor\b|/|\|)\s*")
_LIST_CLASSES = {FactClass.compatibility, FactClass.target, FactClass.mission, FactClass.component, FactClass.payload}
_LWH = re.compile(r"(?i)l\s*[x×]\s*w\s*[x×]\s*h|length\s*[x×,/]\s*width|dimensions")


_ALT_SPLIT = re.compile(r"(?<=[\*\)\s])/(?=[\s\d])|\s/(?=\S)")
_MARKER_END = re.compile(r"(\*{1,4}|†{1,2}|‡|[¹²³⁴⁵⁶⁷⁸⁹])\s*$")


def _split_alternatives(st: DocState, f: CandidateFact) -> list[tuple[str, list]]:
    """'up to 40/ 54 (V-LAP)/ 70 (VULCANO) km', '177 kW / 235 kW', '3,948 m (12,953 ft)*/4,572 m (15,000 ft)**'
    -> one value per alternative, with the bracket qualifier or marker legend as a condition."""
    from ..contracts.facts import Condition
    from ..ingest.measurements import find_measurements

    v = f.value_text
    if f.fact_class not in SPEC_CLASSES or len(v) > 260:
        return []
    parts = [p.strip() for p in _ALT_SPLIT.split(v) if p and p.strip()]
    if len(parts) < 2 or len(parts) > 6 or not all(re.search(r"\d", p) for p in parts):
        return []
    ms = find_measurements(v)
    if len(ms) == 1 and ms[0].kind == "dual" and ms[0].end - ms[0].start >= len(v) - 3:
        return []  # same quantity in two units ("100mph/160km")
    legends = {m.marker: m.legend_text for m in (st.kg.markers if st.kg else [])}
    out = []
    for p in parts:
        conds = []
        mk = _MARKER_END.search(p)
        if mk and mk.group(1) in legends:
            conds.append(Condition(dimension="variant", value_text=legends[mk.group(1)]))
        br = re.search(r"\(([^)]{2,40})\)", p)
        if br and not re.search(r"\d", br.group(1)):
            conds.append(Condition(dimension="variant", value_text=br.group(1)))
        out.append((p, conds))
    return out


def _split_list(f: CandidateFact, multi: bool) -> list[str]:
    v = f.value_text
    if not (multi or f.fact_class in _LIST_CLASSES) or len(v) > 300:
        return []
    if re.search(r"\d\s*(?:-|–|to)\s*\d", v):
        return []
    parts = [p.strip(" .") for p in _LIST_SEP.split(v) if p and p.strip(" .")]
    parts = [p for p in parts if len(p) >= 2 and not re.fullmatch(r"(?:etc|e\.g|and|or)", p, re.I)]
    if len(parts) < 2 or any(len(p) > 120 for p in parts):
        return []
    return parts


def a9_structuring(rt: Runtime, st: DocState) -> list[CandidateFact]:
    """Parses values; splits independent multi-values into separate facts; decomposes L x W x H composites."""
    lang = st.ingest.profile.language
    new: list[CandidateFact] = []
    for f in list(st.facts):
        if f.value is not None:  # already structured in an earlier round
            continue
        f.value = parse_value(f.value_text, lang, context=f.source_parameter or "")
        p = rt.onto.get(f.parameter.id) if f.parameter else None
        if f.derived_from is None and f.verification.value == "pending":
            alts = _split_alternatives(st, f) if not f.applicability.conditions else []
            if alts:
                for part, conds in alts:
                    nf = f.model_copy(deep=True)
                    nf.fact_id = st.next_fact_id()
                    nf.value_text = part
                    nf.value = parse_value(part, lang, context=f.source_parameter or "")
                    nf.applicability.conditions = list(conds)
                    nf.derived_from, nf.method = f.fact_id, "split"
                    nf.evidence = [e.model_copy(update={"quote": part, "char_start": None, "char_end": None}) for e in f.evidence]
                    new.append(nf)
                f.rejection_reason = "split_into_parts"
                f.anomalies.append("split")
                continue
            parts = _split_list(f, bool(p and p.multi))
            if parts:
                for part in parts:
                    nf = f.model_copy(deep=True)
                    nf.fact_id = st.next_fact_id()
                    nf.value_text = part
                    nf.value = parse_value(part, lang)
                    nf.derived_from = f.fact_id
                    nf.method = "split"
                    nf.evidence = [e.model_copy(update={"quote": part, "char_start": None, "char_end": None}) for e in f.evidence]
                    new.append(nf)
                f.rejection_reason = "split_into_parts"
                f.anomalies.append("split")
                continue
            if f.value.kind == "composite" and len(f.value.components) == 3 and _LWH.search(f.source_parameter or ""):
                for pid, comp in zip(("length_overall", "width", "height"), f.value.components):
                    q = rt.onto.get(pid)
                    nf = f.model_copy(deep=True)
                    nf.fact_id = st.next_fact_id()
                    nf.parameter = _ref(q, f.source_parameter) if q else None
                    nf.value = parse_value(f.value_text, lang)
                    nf.value.kind, nf.value.nominal, nf.value.components = "scalar", comp, []
                    nf.derived_from, nf.method = f.fact_id, "composite"
                    new.append(nf)
    return new
