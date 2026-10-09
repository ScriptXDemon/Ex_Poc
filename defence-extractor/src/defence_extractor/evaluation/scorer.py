"""Strict scorer.

For every gold fact we decide one outcome:
  matched        value found among ACCEPTED facts of the right product (aligned by name/alias)
  misattributed  value found only among accepted facts of another product
  unresolved     value found only among unresolved facts (system abstained)
  missed         value not found in the output at all
Matched facts are further checked for parameter correctness and split/merge problems.
Accepted output facts on aligned products that match no gold fact are "extras" (precision review);
negatives (must-not-extract strings) that appear as accepted facts are violations.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from rapidfuzz import fuzz

ACCEPTED = {"verified", "verified_low_confidence"}
_NUM = re.compile(r"\d+(?:[.,]\d+)*")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("–", "-").replace("—", "-").replace("−", "-").replace("º", "°").replace("×", "x")
    s = re.sub(r"[®™]", "", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s.strip(" .;,")


def nums(s: str) -> list[float]:
    out = []
    for m in _NUM.findall(s or ""):
        t = m
        if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", t):
            t = t.replace(",", "")
        elif "," in t and "." not in t:
            t = t.replace(",", ".") if len(t.split(",")[-1]) != 3 else t.replace(",", "")
        try:
            out.append(float(t))
        except ValueError:
            pass
    return out


@dataclass
class GoldFact:
    idx: int
    p: str
    v: str
    k: str = "spec"
    param: str | None = None
    alt: list[str] = field(default_factory=list)
    any: list[str] = field(default_factory=list)
    nums: list[float] = field(default_factory=list)
    cond: str | None = None
    pos: str | None = None  # start | middle | end of the document (position-aware keys for long documents)
    page: int | None = None


@dataclass
class Gold:
    doc_id: str
    products: dict[str, dict]
    facts: list[GoldFact]
    negatives: list[str]
    partial: bool = False  # only sampled products are labelled: facts on other products are not counted


def load_gold(path: str | Path) -> Gold:
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    facts = []
    for i, f in enumerate(d.get("facts", [])):
        facts.append(GoldFact(idx=i, p=f["p"], v=str(f["v"]), k=f.get("k", "spec"), param=f.get("param"),
                              alt=list(f.get("alt", [])), any=[str(x) for x in f.get("any", [])],
                              nums=[float(x) for x in f.get("nums", [])], cond=f.get("cond"),
                              pos=f.get("pos") or (d.get("products", {}).get(f["p"], {}) or {}).get("pos"),
                              page=f.get("page") or (d.get("products", {}).get(f["p"], {}) or {}).get("page")))
    return Gold(doc_id=d["doc_id"], products=d.get("products", {}), facts=facts, negatives=[str(x) for x in d.get("negatives", [])],
                partial=bool(d.get("partial", False)))


@dataclass
class PredFact:
    entity_id: str
    value: str
    param: str | None
    label: str | None
    status: str  # verification
    kind: str  # spec | text
    ontology_status: str | None = None
    pages: tuple[int, ...] = ()


def pred_facts(res: dict) -> list[PredFact]:
    out: list[PredFact] = []
    for p in res.get("products", []):
        eid = p["entity_id"]
        for s in p.get("specifications", []):
            out.append(PredFact(eid, s["value_text"], s["parameter"]["id"], s["parameter"].get("source_label"), s["verification"],
                                "spec", s["parameter"].get("status"),
                                tuple(sorted({e["page"] for e in s.get("evidence") or [] if e.get("page")}))))
        for coll in ("capabilities", "features", "technologies", "components", "compatibility", "missions", "targets"):
            for t in p.get(coll, []):
                out.append(PredFact(eid, t["text"], coll, t.get("source_parameter"), t["verification"], "text"))
        for u in p.get("unresolved", []):
            out.append(PredFact(eid, u["value_text"], u.get("parameter"), u.get("label"), "unresolved", "spec"))
    for u in res.get("unattributed", []):
        out.append(PredFact(u.get("owner_entity_id") or "", u["value_text"], u.get("parameter"), u.get("label"), "unresolved", "spec"))
    return out


_UNIT_TOK = re.compile(r"\d\s*([a-z°%\"']+(?:/[a-z]+)?)")


def units(s: str) -> set[str]:
    return {u for u in _UNIT_TOK.findall(norm(s)) if u not in ("x", "or", "to", "and", "e", "s")}


def _has(needle: str, hay: str) -> bool:
    """needle occurs in hay as a whole token run: "800" is not inside "n318009", "0.23" not inside "10.235"."""
    i = hay.find(needle)
    while i >= 0:
        before = hay[i - 1] if i > 0 else " "
        j = i + len(needle)
        after = hay[j] if j < len(hay) else " "
        if not (needle[0].isalnum() and (before.isalnum() or (before == "." and needle[0].isdigit()))) and \
                not (needle[-1].isalnum() and (after.isalnum() or (after == "." and j + 1 < len(hay) and hay[j + 1].isdigit()))):
            return True
        i = hay.find(needle, i + 1)
    return False


def value_match(g: GoldFact, pv: str) -> str | None:
    gv, p = norm(g.v), norm(pv)
    if not p:
        return None
    if gv == p:
        return "exact"
    if gv and _has(gv, p) and len(p) <= 3 * len(gv) + 12:
        return "contains"
    for a in g.any:
        na = norm(a)
        if na and (na == p or (_has(na, p) and len(p) <= 3 * len(na) + 15)):
            return "alt"
    if _has(p, gv) and len(p) >= 0.6 * len(gv) and len(p) >= 3:
        return "partial"
    gn = g.nums or nums(g.v)
    pn = nums(pv)
    gu, pu = units(g.v), units(pv)
    if gu and pu and not (gu & pu):
        return None  # same numbers, different units ("70 km" vs "70 km/h")
    speed = re.compile(r"km/h|kmh|kph|mph|\bkn\b|\bkt\b|knots|m/s")
    if bool(speed.search(norm(g.v))) != bool(speed.search(p)) and re.search(r"\bkm\b|\bm\b|nm", norm(g.v) + " " + p):
        return None  # a speed is never a distance
    if gn and pn and all(any(abs(x - y) < 1e-6 for y in pn) for x in gn) and len(pn) <= len(gn) + 3:
        # same numbers: require some lexical overlap to avoid matching unrelated "12"
        if g.nums or fuzz.token_set_ratio(gv, p) >= 50 or len(gn) >= 2:
            return "numeric"
    if not gn and len(gv) >= 6 and fuzz.token_set_ratio(gv, p) >= 92:
        return "fuzzy"
    return None


_NAME_NUM = re.compile(r"\d+(?:\.\d+)?")


def same_product(a: str, b: str) -> bool:
    """Gold name ``a`` and predicted name ``b`` (both normalised) denote the same product: equal, or one contains the
    other / near-identical wording, AND the predicted name mentions every number of the gold name. The number rule
    keeps variants apart ("M322" vs "M338", "120 gr" vs "136 gr") and stops a family record (".308 Winchester") from
    standing in for a specific product (".308 Winchester 170 gr Lock Base")."""
    if not a or not b:
        return False
    if a == b:
        return True
    if not ((len(a) >= 3 and (a in b or b in a)) or fuzz.token_set_ratio(a, b) >= 92):
        return False
    return set(_NAME_NUM.findall(a)) <= set(_NAME_NUM.findall(b))


def align_products(gold: Gold, res: dict) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for gid, gp in gold.products.items():
        names = [norm(gp["name"])] + [norm(a) for a in gp.get("aliases", [])]
        hits = set()
        for p in res.get("products", []) + [e for e in res.get("entities", []) if e.get("entity_id")]:
            pn = [norm(p["name"])] + [norm(a) for a in p.get("aliases", [])]
            if any(same_product(a, b) for a in names for b in pn):
                hits.add(p["entity_id"])
        out[gid] = hits
    return out


def param_ok(g: GoldFact, pf: PredFact) -> str:
    if g.param is None or pf.kind == "text":
        return "n/a"
    want = {g.param, *g.alt}
    if pf.param in want:
        return "correct"
    if pf.ontology_status == "dynamic":
        lab = norm(pf.label or "") + " " + (pf.param or "").replace("_", " ")
        if any(fuzz.token_set_ratio(w.replace("_", " "), lab) >= 70 for w in want):
            return "dynamic_ok"
        return "dynamic_other"
    return "wrong_canonical"


def _ancestors(res: dict) -> dict[str, set[str]]:
    """entity id -> its parent chain (subsystem -> product), from the result's entity/product parent links."""
    parent = {e["entity_id"]: e.get("parent_entity_id") for e in res.get("entities", []) if e.get("entity_id")}
    for p in res.get("products", []):
        parent.setdefault(p["entity_id"], p.get("parent_entity_id"))
    out: dict[str, set[str]] = {}
    for eid in parent:
        chain, cur = set(), parent.get(eid)
        while cur and cur not in chain and len(chain) < 6:
            chain.add(cur)
            cur = parent.get(cur)
        out[eid] = chain
    return out


def _same_place(g: GoldFact, pf: PredFact) -> bool:
    """In a PDF, a value on another product is the same printed value only if it comes from the key fact's page (or the
    next / previous one); the same number on page 99 is a different cell, so the key fact was missed, not misattributed."""
    if not g.page or not pf.pages:
        return True
    return any(abs(p - int(g.page)) <= 1 for p in pf.pages)


def score_doc(gold: Gold, res: dict) -> dict[str, Any]:
    aligned = align_products(gold, res)
    anc = _ancestors(res)
    preds = pred_facts(res)
    accepted = [p for p in preds if p.status in ACCEPTED]
    unresolved = [p for p in preds if p.status == "unresolved"]
    used: dict[int, int] = {}
    rows = []
    # predictions that are a key fact of their own product: finding the same value there (two cartridges share
    # "800 m/s") is not a wrong-product error for another product that lacks it
    legit = {i for g2 in gold.facts for i, pf in enumerate(accepted)
             if pf.entity_id in aligned.get(g2.p, set()) and value_match(g2, pf.value)}
    for g in gold.facts:
        own = aligned.get(g.p, set())
        best = None
        for i, pf in enumerate(accepted):
            mt = value_match(g, pf.value)
            if mt and pf.entity_id in own:
                rank = {"exact": 0, "contains": 1, "alt": 2, "numeric": 3, "fuzzy": 4, "partial": 5}[mt]
                if best is None or rank < best[0] or (rank == best[0] and used.get(i, 0) < used.get(best[1], 0)):
                    best = (rank, i, mt)
        if best is not None:
            pf = accepted[best[1]]
            used[best[1]] = used.get(best[1], 0) + 1
            rows.append({"gold": g.__dict__, "outcome": "matched", "match": best[2], "pred": pf.__dict__,
                         "param": param_ok(g, pf)})
            continue
        # the value sits on a subsystem / component record whose parent chain leads to the right product
        # (e.g. a recovery vehicle's winch pulling force on the winch record, linked to the vehicle): counted as
        # matched, reported separately as via_subsystem
        sub = next(((i, pf) for i, pf in enumerate(accepted) if value_match(g, pf.value) and anc.get(pf.entity_id, set()) & own), None)
        if sub is not None:
            used[sub[0]] = used.get(sub[0], 0) + 1
            rows.append({"gold": g.__dict__, "outcome": "matched", "match": "via_subsystem", "pred": sub[1].__dict__,
                         "param": param_ok(g, sub[1]), "via_subsystem": True})
            continue
        other = next((pf for i, pf in enumerate(accepted)
                      if i not in legit and value_match(g, pf.value) and pf.entity_id not in own and _same_place(g, pf)), None)
        if other is not None:
            rows.append({"gold": g.__dict__, "outcome": "misattributed", "pred": other.__dict__})
            continue
        un = next((pf for pf in unresolved if value_match(g, pf.value)), None)
        if un is not None:
            rows.append({"gold": g.__dict__, "outcome": "unresolved", "pred": un.__dict__})
            continue
        rows.append({"gold": g.__dict__, "outcome": "missed"})
    merged = sum(1 for n in used.values() if n > 1)
    own_all = set().union(*aligned.values()) if aligned else set()
    matched_ids = set(used)
    extras = [pf.__dict__ for i, pf in enumerate(accepted) if i not in matched_ids and pf.entity_id in own_all]
    other_products = [pf.__dict__ for pf in accepted if pf.entity_id not in own_all]
    violations = []
    for neg in gold.negatives:
        nn = norm(neg)
        for pf in accepted:  # a key without products (negative control page) judges every accepted value
            if (pf.entity_id in own_all or not gold.products) and nn and nn in norm(pf.value):
                violations.append({"negative": neg, "pred": pf.__dict__})

    def recall(kinds: set[str]) -> dict:
        rs = [r for r in rows if r["gold"]["k"] in kinds]
        n = len(rs)
        c = {o: sum(1 for r in rs if r["outcome"] == o) for o in ("matched", "misattributed", "unresolved", "missed")}
        c["via_subsystem"] = sum(1 for r in rs if r.get("via_subsystem"))
        return {"n": n, **c, "recall": round(c["matched"] / n, 4) if n else None}

    core = recall({"spec", "compat", "target"})
    text = recall({"text"})
    by_pos: dict[str, dict] = {}
    for pos in ("start", "middle", "end"):
        rs = [r for r in rows if r["gold"].get("pos") == pos]
        if rs:
            by_pos[pos] = {"n": len(rs), "matched": sum(1 for r in rs if r["outcome"] == "matched"),
                           "misattributed": sum(1 for r in rs if r["outcome"] == "misattributed"),
                           "unresolved": sum(1 for r in rs if r["outcome"] == "unresolved")}
    spec_rows = [r for r in rows if r["outcome"] == "matched" and r.get("param") not in (None, "n/a")]
    pc = {k: sum(1 for r in spec_rows if r["param"] == k) for k in ("correct", "dynamic_ok", "dynamic_other", "wrong_canonical")}
    exact = sum(1 for r in rows if r["outcome"] == "matched" and r["match"] in ("exact", "contains", "alt"))
    return {
        "doc_id": gold.doc_id, "aligned": {k: sorted(v) for k, v in aligned.items()}, "partial": gold.partial,
        "core": core, "text": text, "param": pc, "verbatim_matches": exact, "by_pos": by_pos,
        "merged_pred_facts": merged, "extras": len(extras), "other_product_facts": 0 if gold.partial else len(other_products),
        "negative_violations": violations, "rows": rows, "extra_list": extras[:200], "other_list": other_products[:100],
        "accepted": len(accepted), "unresolved_out": len(unresolved),
    }


# ---------------------------------------------------------------------------------------------------
# old benchmark comparison (value-only, as the old report measured it, and product-aware)
# ---------------------------------------------------------------------------------------------------
def old_key_compare(page: dict, res: dict, gold: Gold | None) -> dict[str, Any]:
    preds = [p for p in pred_facts(res) if p.status in ACCEPTED]
    own = set().union(*align_products(gold, res).values()) if gold else {p["entity_id"] for p in res.get("products", [])}
    n = len(page["answer_key"])
    vo = pa = 0
    for r in page["answer_key"]:
        g = GoldFact(idx=0, p="P1", v=r["page_value"])
        hits = [pf for pf in preds if value_match(g, pf.value)]
        vo += bool(hits)
        pa += any(pf.entity_id in own for pf in hits)
    v3_caught = sum(1 for r in page["answer_key"] if r["status"] == "caught")
    return {"n": n, "value_only": vo, "product_aware": pa, "v3_value_only": v3_caught}


def score_run(run_dir: Path, gold_dir: Path, bench_json: Path | None = None) -> dict[str, Any]:
    bench = {p["id"]: p for p in json.loads(bench_json.read_text())} if bench_json and bench_json.exists() else {}
    docs = []
    for gp in sorted(gold_dir.glob("*.yaml")):
        gold = load_gold(gp)
        rp = run_dir / "docs" / gold.doc_id / "result.json"
        if not rp.exists():
            docs.append({"doc_id": gold.doc_id, "missing_result": True})
            continue
        res = json.loads(rp.read_text())
        sd = score_doc(gold, res)
        if gold.doc_id in bench:
            sd["old_key"] = old_key_compare(bench[gold.doc_id], res, gold)
        sd["cost_usd"] = res["execution"]["usage"]["cost_usd"]
        sd["ledger"] = res["ledger_summary"]
        docs.append(sd)
    ok = [d for d in docs if not d.get("missing_result")]

    def agg(key: str) -> dict:
        t = {k: sum(d[key].get(k, 0) for d in ok) for k in ("n", "matched", "via_subsystem", "misattributed", "unresolved", "missed")}
        t["recall"] = round(t["matched"] / t["n"], 4) if t["n"] else None
        return t

    params = {k: sum(d["param"][k] for d in ok) for k in ("correct", "dynamic_ok", "dynamic_other", "wrong_canonical")}
    by_pos = {}
    for pos in ("start", "middle", "end"):
        rs = [d["by_pos"][pos] for d in ok if pos in d.get("by_pos", {})]
        if rs:
            t = {k: sum(r[k] for r in rs) for k in ("n", "matched", "misattributed", "unresolved")}
            t["recall"] = round(t["matched"] / t["n"], 4) if t["n"] else None
            by_pos[pos] = t
    old = {k: sum(d.get("old_key", {}).get(k, 0) for d in ok) for k in ("n", "value_only", "product_aware", "v3_value_only")}
    matched_total = sum(d["core"]["matched"] + d["text"]["matched"] for d in ok)
    return {
        "documents": len(docs), "scored": len(ok), "core": agg("core"), "text": agg("text"), "param": params, "by_pos": by_pos,
        "verbatim_share": round(sum(d["verbatim_matches"] for d in ok) / max(1, matched_total), 4),
        "extras": sum(d["extras"] for d in ok), "other_product_facts": sum(d["other_product_facts"] for d in ok),
        "negative_violations": sum(len(d["negative_violations"]) for d in ok),
        "merged_pred_facts": sum(d["merged_pred_facts"] for d in ok),
        "ledger_open": sum(d["ledger"]["open"] for d in ok),
        "old_key": old, "cost_usd": round(sum(d.get("cost_usd", 0) for d in ok), 4), "docs": docs,
    }
