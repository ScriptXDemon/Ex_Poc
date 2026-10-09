"""Corpus-level run statistics, A14 ontology-learning proposals (offline) and an HTML run report (no gold needed)."""

from __future__ import annotations

import collections
import html
import json
import re
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from ..ontology import load_ontology, normalize_label

ACCEPTED = {"verified", "verified_low_confidence"}


def load_results(run_dir: Path) -> list[dict]:
    out = []
    for p in sorted((run_dir / "docs").glob("*/result.json")):
        try:
            out.append(json.loads(p.read_text()))
        except Exception:
            continue
    return out


def _nm(s: str) -> str:
    s = re.sub(r"[®™]", "", (s or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _found(hint: str, products: list[dict]) -> dict | None:
    """The output product a corpus-index product name refers to (names/aliases; substring or close token match)."""
    h = _nm(hint)
    for p in products:
        for n in [p.get("name", ""), *p.get("aliases", [])]:
            b = _nm(n)
            if h and b and (h == b or (min(len(h), len(b)) >= 4 and (h in b or b in h)) or fuzz.token_set_ratio(h, b) >= 88):
                return p
    return None


def run_stats(run_dir: Path) -> dict[str, Any]:
    results = load_results(run_dir)
    sel = {}
    if (run_dir / "selection.json").exists():
        sel = {r["doc_id"]: r for r in json.loads((run_dir / "selection.json").read_text())}
    by_group: dict[tuple, dict] = {}
    params = collections.Counter()
    status = collections.Counter()
    fact_classes = collections.Counter()
    langs = collections.Counter()
    page_types = collections.Counter()
    for r in results:
        d = r["document"]
        ref = sel.get(d["doc_id"], {})
        key = (d.get("level") or ref.get("level"), d.get("format"))
        g = by_group.setdefault(key, {"docs": 0, "products": 0, "specs": 0, "canonical": 0, "candidate": 0, "dynamic": 0,
                                      "text": 0, "unresolved": 0, "ledger_items": 0, "ledger_open": 0, "cost": 0.0,
                                      "seconds": 0.0, "no_products": 0, "errors": 0, "hint_products": 0, "hint_found": 0,
                                      "hint_with_specs": 0})
        g["docs"] += 1
        g["products"] += len(r["products"])
        g["no_products"] += not r["products"]
        g["cost"] += r["execution"]["usage"]["cost_usd"]
        g["seconds"] += r["execution"]["duration_s"]
        g["errors"] += bool(r["execution"].get("errors"))
        g["ledger_items"] += r["ledger_summary"]["total"]
        g["ledger_open"] += r["ledger_summary"]["open"]
        # independent check: products named by the corpus gate (a different model) for this page
        for h in ref.get("hint_products", []):
            g["hint_products"] += 1
            hit = _found(h, r["products"])
            g["hint_found"] += hit is not None
            g["hint_with_specs"] += hit is not None and any(x["verification"] in ACCEPTED for x in hit.get("specifications", []))
        langs[d.get("language") or "?"] += 1
        page_types[d.get("page_type") or "?"] += 1
        for p in r["products"]:
            for s in p["specifications"]:
                g["specs"] += 1
                g[s["parameter"]["status"]] += 1
                params[(s["parameter"]["status"], s["parameter"]["id"])] += 1
                status[s["verification"]] += 1
                fact_classes[s["fact_class"]] += 1
            for coll in ("capabilities", "features", "technologies", "components", "compatibility", "missions", "targets"):
                g["text"] += len(p.get(coll, []))
            g["unresolved"] += len(p.get("unresolved", []))
        g["unresolved"] += len(r.get("unattributed", []))
    groups = [{"level": k[0], "format": k[1], **v} for k, v in sorted(by_group.items(), key=lambda x: (x[0][0] or 9, x[0][1] or ""))]
    return {"documents": len(results), "groups": groups, "languages": dict(langs.most_common()),
            "page_types": dict(page_types.most_common()), "spec_status": dict(status),
            "top_canonical": [(pid, n) for (st, pid), n in params.most_common() if st == "canonical"][:40],
            "top_candidate": [(pid, n) for (st, pid), n in params.most_common() if st == "candidate"][:40],
            "top_dynamic": [(pid, n) for (st, pid), n in params.most_common() if st == "dynamic"][:60],
            "cost_usd": round(sum(g["cost"] for g in groups), 4)}


def ontology_proposals(run_dir: Path, min_docs: int = 3) -> list[dict[str, Any]]:
    """A14 (offline): cluster dynamic properties across documents and propose promotions (needs human approval)."""
    onto = load_ontology()
    clusters: list[dict] = []
    for r in load_results(run_dir):
        doc = r["document"]["doc_id"]
        for p in r["products"]:
            cats = [c["category"] for c in p.get("classification", [])]
            for s in p["specifications"]:
                if s["parameter"]["status"] != "dynamic" or s["verification"] not in ACCEPTED:
                    continue
                key = s["parameter"]["id"]
                label = s["parameter"].get("source_label") or key.replace("_", " ")
                nk = normalize_label(key.replace("_", " "))
                target = None
                for c in clusters:
                    if fuzz.token_set_ratio(nk, c["norm"]) >= 90:
                        target = c
                        break
                if target is None:
                    target = {"norm": nk, "ids": collections.Counter(), "labels": collections.Counter(), "docs": set(),
                              "examples": [], "classes": collections.Counter(), "dims": collections.Counter()}
                    clusters.append(target)
                target["ids"][key] += 1
                target["labels"][label] += 1
                target["docs"].add(doc)
                if len(target["examples"]) < 6:
                    target["examples"].append(s["value_text"][:80])
                for c in cats:
                    target["classes"][c] += 1
                v = s.get("value") or {}
                if v.get("dimension"):
                    target["dims"][v["dimension"]] += 1
    out = []
    for c in clusters:
        if len(c["docs"]) < min_docs:
            continue
        pid = c["ids"].most_common(1)[0][0]
        occ = sum(c["ids"].values())
        # a measurable dimension only when most values carry it (a lone "x4" must not make "interface" a magnification)
        dim = c["dims"].most_common(1)[0] if c["dims"] else None
        dim = dim[0] if dim and dim[1] >= 0.5 * occ else "text / mixed"

        def closeness(p) -> float:
            return max(fuzz.token_set_ratio(c["norm"], normalize_label(a)) for a in [p.name, p.id.replace("_", " "), *p.aliases])

        near = max(onto.params.values(), key=closeness)
        near_score = closeness(near)
        out.append({"proposed_id": pid, "documents": len(c["docs"]), "occurrences": sum(c["ids"].values()),
                    "id_variants": dict(c["ids"].most_common(8)), "source_labels": dict(c["labels"].most_common(8)),
                    "examples": c["examples"], "product_classes": dict(c["classes"].most_common(5)),
                    "dimension": dim,
                    "nearest_existing": near.id if near_score >= 70 else None, "nearest_score": round(near_score),
                    "status": "proposed (requires approval)"})
    out.sort(key=lambda x: (-x["documents"], -x["occurrences"]))
    return out


def render_run_report(run_id: str, stats: dict, proposals: list[dict]) -> str:
    e = lambda x: html.escape("" if x is None else str(x))  # noqa: E731
    css = ("body{margin:0;background:#f6f7f9;color:#1d2430;font:14px/1.5 system-ui,sans-serif}main{max-width:1180px;margin:auto;padding:24px 16px}"
           "@media (prefers-color-scheme:dark){body{background:#13161b;color:#e6e9ee}table td,table th{border-color:#2c323c}}"
           "table{border-collapse:collapse;width:100%;font-size:12.5px;margin:8px 0 20px}th,td{text-align:left;border-bottom:1px solid #dde2e8;padding:4px 8px;vertical-align:top}"
           ".wrap{overflow-x:auto}h2{font-size:17px;margin:22px 0 6px}")
    rows = "".join(
        f"<tr><td>{e(g['level'])}</td><td>{e(g['format'])}</td><td>{g['docs']}</td><td>{g['products']}</td><td>{g['no_products']}</td>"
        f"<td>{g['specs']}</td><td>{g['canonical']}</td><td>{g['candidate']}</td><td>{g['dynamic']}</td><td>{g['text']}</td>"
        f"<td>{g['unresolved']}</td><td>{g['ledger_items']}</td><td>{g['ledger_open']}</td><td>${g['cost']:.3f}</td>"
        f"<td>{g['seconds'] / max(1, g['docs']):.0f}s</td>"
        f"<td>{(str(g['hint_found']) + '/' + str(g['hint_products']) + ' (' + str(g['hint_with_specs']) + ' with specs)') if g['hint_products'] else '-'}</td></tr>"
        for g in stats["groups"])
    dyn = "".join(f"<tr><td>{e(k)}</td><td>{n}</td></tr>" for k, n in stats["top_dynamic"])
    prop = "".join(
        f"<tr><td>{e(p['proposed_id'])}</td><td>{p['documents']}</td><td>{p['occurrences']}</td><td>{e(', '.join(p['source_labels']))}</td>"
        f"<td>{e(' | '.join(p['examples'][:3]))}</td><td>{e(p['dimension'])}</td><td>{e(p['nearest_existing'])} ({p['nearest_score']})</td></tr>"
        for p in proposals[:80])
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Corpus Run Report</title><style>{css}</style></head><body><main><h1>Corpus run {e(run_id)}</h1>"
            f"<p>{stats['documents']} documents · LLM cost ${stats['cost_usd']:.3f} · languages {e(stats['languages'])} · page types {e(stats['page_types'])}</p>"
            "<h2>By level and format</h2><div class='wrap'><table><tr><th>level</th><th>format</th><th>docs</th><th>products</th><th>docs without products</th>"
            "<th>specs</th><th>canonical</th><th>candidate</th><th>dynamic</th><th>text facts</th><th>unresolved</th><th>ledger items</th>"
            f"<th>ledger open</th><th>cost</th><th>time/doc</th><th>index products found</th></tr>{rows}</table></div>"
            "<p>index products found: products the corpus gate (a different model) named for level-1/2 pages, matched by name "
            "against the extracted products — an independent, label-free check of product identification.</p>"
            f"<h2>Most frequent dynamic (newly discovered) parameters</h2><div class='wrap'><table><tr><th>parameter</th><th>count</th></tr>{dyn}</table></div>"
            "<h2>A14 ontology proposals (dynamic properties seen in several documents — need approval)</h2><div class='wrap'><table>"
            "<tr><th>proposed id</th><th>docs</th><th>occurrences</th><th>source labels</th><th>examples</th><th>dimension</th><th>nearest existing</th></tr>"
            f"{prop}</table></div></main></body></html>")
