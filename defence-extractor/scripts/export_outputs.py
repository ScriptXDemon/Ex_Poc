"""Export every processed document as a self-contained folder: the exact input file next to the JSON the pipeline
produced for it, plus a readable summary, the source reference and (where one exists) the hand-written answer key.

Standard library only (file organisation, no pipeline code).
usage: python3 scripts/export_outputs.py [out_dir]          (default: extraction_outputs/)
"""

from __future__ import annotations

import collections
import csv
import html
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "extraction_outputs"
TEXT_COLLS = ("capabilities", "features", "technologies", "components", "compatibility", "missions", "targets")


def local_input(server_path: str) -> Path | None:
    for marker, base in (("data/spec_pages_1000/", ROOT / "spec_pages_1000"), ("data/benchmark/", ROOT / "data" / "benchmark")):
        if marker in server_path:
            p = base / server_path.split(marker, 1)[1]
            return p if p.exists() else None
    return None


def true_costs(run_dir: Path) -> dict[str, float]:
    out: dict[str, float] = collections.defaultdict(float)
    log = run_dir / "llm_calls.jsonl"
    if log.exists():
        for line in log.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            out[r.get("doc_id") or "?"] += r.get("cost_usd") or 0.0
    return out


def gold_scores(run_dir: Path) -> dict[str, dict]:
    p = run_dir / "eval.json"
    if not p.exists():
        return {}
    return {d["doc_id"]: d for d in json.loads(p.read_text())["docs"] if not d.get("missing_result")}


def md(s) -> str:
    return ("" if s is None else str(s)).replace("|", "\\|").replace("\n", " ").strip()


def summary_md(res: dict, ref: dict, status: str, cost: float, score: dict | None) -> str:
    d = res["document"]
    prods = res.get("products", [])
    n_specs = sum(len(p.get("specifications", [])) for p in prods)
    n_text = sum(len(p.get(c, [])) for p in prods for c in TEXT_COLLS)
    n_unres = sum(len(p.get("unresolved", [])) for p in prods) + len(res.get("unattributed", []))
    L = [f"# {md(d.get('title') or ref.get('doc_id'))}", "",
         f"- **Source:** {ref.get('url') or d.get('url')}",
         f"- **Input file:** `{Path(ref['_input_name']).name if ref.get('_input_name') else '-'}` (copied here as `input.{ref['format'] if ref['format'] == 'pdf' else 'html'}`)",
         f"- **Corpus level:** {ref.get('level') if ref.get('level') is not None else 'benchmark'} · **format:** {ref['format']} · "
         f"**language:** {d.get('language')} · **page type (A2):** {d.get('page_type')} / {d.get('page_scope')}",
         f"- **Status:** {status} · **LLM cost:** ${cost:.4f}",
         f"- **Output:** {len(prods)} products, {n_specs} specifications, {n_text} text facts, {n_unres} unresolved values; "
         f"ledger {res['ledger_summary'].get('total')} items, open {res['ledger_summary'].get('open')}"]
    if score:
        c, t = score["core"], score["text"]
        L.append(f"- **Answer key score:** specs {c['matched']}/{c['n']} found (misattributed {c['misattributed']}, "
                 f"unresolved {c['unresolved']}, missed {c['missed']}); text facts {t['matched']}/{t['n']} — see `answer_key.yaml`")
    errs = res["execution"].get("errors") or []
    if errs:
        L.append(f"- **Run notes:** " + "; ".join(md(e)[:160] for e in errs[:4]))
    L.append("")
    if not prods:
        L += ["_No product was identified on this document._", ""]
    for p in prods:
        cls = ", ".join(f"{c.get('domain')}/{c.get('category')}" for c in (p.get("classification") or []) if isinstance(c, dict))
        L += [f"## {md(p['name'])}", "",
              f"`{p['entity_id']}` · role **{p.get('role')}** · type {p.get('entity_type')}"
              + (f" · class {cls}" if cls else "") + (f" · manufacturer {md(p.get('manufacturer'))}" if p.get("manufacturer") else "")
              + (f" · part of `{p['parent_entity_id']}`" if p.get("parent_entity_id") else ""), ""]
        if p.get("aliases"):
            L += [f"Also called: {md(', '.join(p['aliases'][:8]))}", ""]
        specs = p.get("specifications", [])
        if specs:
            L += ["| Parameter | Value | Catalogue | Verification | Evidence (quote · block) |", "|---|---|---|---|---|"]
            for s in specs:
                prm = s["parameter"]
                ev = (s.get("evidence") or [{}])[0]
                cond = "; ".join(c.get("value_text", "") for c in (s.get("applies_when") or {}).get("conditions", []) if isinstance(c, dict))
                val = md(s["value_text"]) + (f" _(when: {md(cond)})_" if cond else "")
                L.append(f"| {md(prm.get('display_name') or prm['id'])} `{md(prm['id'])}` | {val} | {prm.get('status')} | "
                         f"{s.get('verification')} | \"{md(ev.get('quote'))[:90]}\" · {ev.get('block_id', '')} |")
            L.append("")
        for coll in TEXT_COLLS:
            items = p.get(coll, [])
            if items:
                L.append(f"**{coll.capitalize()}:** " + " · ".join(md(x["text"])[:140] for x in items))
                L.append("")
        if p.get("unresolved"):
            L.append("**Unresolved (system abstained):** " + " · ".join(md(u.get("value_text"))[:80] for u in p["unresolved"]))
            L.append("")
    if res.get("unattributed"):
        L += ["## Values found but not attributed to a product", ""]
        L += [f"- {md(u.get('value_text'))[:120]} ({md(u.get('label'))})" for u in res["unattributed"][:40]]
        L.append("")
    return "\n".join(L)


def export_run(run: str, group_name, rows: list[dict], gold_dir: Path | None) -> None:
    run_dir = ROOT / "runs" / run
    sel = json.loads((run_dir / "selection.json").read_text())
    costs = true_costs(run_dir)
    scores = gold_scores(run_dir)
    for ref in sel:
        doc_id = ref["doc_id"]
        ddir = run_dir / "docs" / doc_id
        res_p = ddir / "result.json"
        inp = local_input(ref["path"])
        host = ref.get("host") or ((ref.get("url") or "").split("/")[2] if (ref.get("url") or "").count("/") >= 2 else "unknown")
        state = json.loads((ddir / "state.json").read_text()) if (ddir / "state.json").exists() else None
        if res_p.exists():
            res = json.loads(res_p.read_text())
            errs = res["execution"].get("errors") or []
            budget = [e.split(":")[0] for e in errs if "BudgetExceeded" in e]
            capped = any("document budget" in e for e in errs)
            status = ("completed" if not budget else f"completed, partly cut short by the run budget stop ({', '.join(sorted(set(budget)))})")
            if capped:
                status += "; per-document spend cap reached (some sections not read)"
        elif state is not None:
            res, status = None, ("stopped mid-way by the run budget stop (no output)" if state.get("status") == "budget_exhausted"
                                 else f"not finished ({state.get('status')}, no output)")
        else:
            res, status = None, "not processed (budget ran out before this document)"
        rel = None
        if res is not None:
            folder = OUT / group_name(ref) / f"{host}__{doc_id}"
            folder.mkdir(parents=True, exist_ok=True)
            ext = "pdf" if ref["format"] == "pdf" else "html"
            if inp is not None:
                shutil.copy2(inp, folder / f"input.{ext}")
            shutil.copy2(res_p, folder / "output.json")
            src = {k: v for k, v in ref.items() if k != "path"}
            src.update({"input_file_original_name": inp.name if inp else Path(ref["path"]).name,
                        "input_file_in_corpus": str(inp.relative_to(ROOT)) if inp else None,
                        "run_id": run, "status": status, "llm_cost_usd": round(costs.get(doc_id, 0.0), 5)})
            (folder / "source.json").write_text(json.dumps(src, indent=1, ensure_ascii=False), encoding="utf-8")
            ref2 = dict(ref, _input_name=inp.name if inp else Path(ref["path"]).name)
            (folder / "summary.md").write_text(summary_md(res, ref2, status, costs.get(doc_id, 0.0), scores.get(doc_id)), encoding="utf-8")
            if gold_dir is not None and (gold_dir / f"{doc_id}.yaml").exists():
                shutil.copy2(gold_dir / f"{doc_id}.yaml", folder / "answer_key.yaml")
            rel = folder.relative_to(OUT)
        prods = res.get("products", []) if res else []
        sc = scores.get(doc_id)
        rows.append({
            "run": run, "doc_id": doc_id, "level": ref.get("level") if ref.get("level") is not None else "benchmark",
            "format": ref["format"], "host": host, "source_url": ref.get("url") or "", "status": status,
            "products": len(prods), "specifications": sum(len(p.get("specifications", [])) for p in prods),
            "text_facts": sum(len(p.get(c, [])) for p in prods for c in TEXT_COLLS),
            "product_names": " | ".join(p["name"] for p in prods[:8]),
            "answer_key_score": f"{sc['core']['matched']}/{sc['core']['n']}" if sc else "",
            "llm_cost_usd": round(costs.get(doc_id, 0.0), 5),
            "folder": str(rel) if rel else "",
            "input_file": f"{rel}/input.{'pdf' if ref['format'] == 'pdf' else 'html'}" if rel and inp else "",
            "output_json": f"{rel}/output.json" if rel else "",
            "input_in_corpus": str(inp.relative_to(ROOT)) if inp else "",
        })


def write_index(rows: list[dict]) -> None:
    cols = ["run", "doc_id", "level", "format", "host", "source_url", "status", "products", "specifications", "text_facts",
            "product_names", "answer_key_score", "llm_cost_usd", "folder", "input_file", "output_json", "input_in_corpus"]
    with (OUT / "index.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    e = html.escape
    trs = []
    for i, r in enumerate(rows, 1):
        links = (f"<a href='{e(r['input_file'])}'>input</a> · <a href='{e(r['output_json'])}'>output.json</a> · "
                 f"<a href='{e(r['folder'])}/summary.md'>summary</a>") if r["folder"] else ""
        cls = "ok" if r["status"] == "completed" else ("part" if r["folder"] else "none")
        trs.append(f"<tr class='{cls}'><td>{i}</td><td>{e(r['run'])}</td><td>{e(str(r['level']))}</td><td>{e(r['format'])}</td>"
                   f"<td><a href='{e(r['source_url'])}'>{e(r['host'])}</a></td><td>{e(r['product_names'])}</td>"
                   f"<td class='n'>{r['specifications']}</td><td>{e(r['status'])}</td><td class='n'>{e(r['answer_key_score'])}</td>"
                   f"<td class='n'>{r['llm_cost_usd']:.4f}</td><td>{links}</td></tr>")
    done = sum(1 for r in rows if r["folder"])
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Extraction outputs</title><style>
:root{{--bg:#f4f6f5;--fg:#18201d;--mut:#5a665f;--rule:#d5ddd8;--acc:#8a4f12}}
@media (prefers-color-scheme:dark){{:root{{--bg:#121614;--fg:#e4eae6;--mut:#99a59e;--rule:#2b3430;--acc:#e09a4a}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif}}main{{padding:20px 16px;max-width:1500px;margin:auto}}
input{{padding:6px 8px;width:min(420px,100%);border:1px solid var(--rule);border-radius:6px;background:transparent;color:var(--fg)}}
table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border-bottom:1px solid var(--rule);padding:5px 7px;text-align:left;vertical-align:top}}
th{{position:sticky;top:0;background:var(--bg)}}td.n{{font-variant-numeric:tabular-nums;white-space:nowrap}}a{{color:var(--acc)}}
tr.none td{{color:var(--mut)}}.wrap{{overflow-x:auto}}</style></head><body><main>
<h1>Extraction outputs</h1><p>{done} documents with output folders (input file + output.json + summary.md + source.json) out of {len(rows)} listed.
Rows without links were not processed (the budget ran out) or stopped before producing output. Filter: <input id="q" placeholder="host, product, level, status…"></p>
<div class="wrap"><table id="t"><tr><th>#</th><th>Run</th><th>Level</th><th>Format</th><th>Source</th><th>Products</th><th>Specs</th><th>Status</th><th>Answer key</th><th>Cost $</th><th>Files</th></tr>
{''.join(trs)}</table></div></main>
<script>document.getElementById('q').addEventListener('input',e=>{{const q=e.target.value.toLowerCase();
for(const tr of document.querySelectorAll('#t tr:not(:first-child)'))tr.hidden=q&&!tr.textContent.toLowerCase().includes(q)}})</script></body></html>"""
    (OUT / "index.html").write_text(page, encoding="utf-8")


README = """# Extraction outputs

One folder per processed document. Each folder holds the exact file the pipeline read and the JSON it produced:

| File | What it is |
|---|---|
| `input.html` / `input.pdf` | the input document, byte-for-byte as processed (copied from `spec_pages_1000/` or the benchmark snapshots) |
| `output.json` | the pipeline's final result for that document (pipeline v1.1) |
| `summary.md` | the same result in readable form: each product with its specifications (parameter, value, catalogue status, verification, evidence quote) and text facts |
| `source.json` | where the input came from: source URL, corpus level, format, original file name, run id, status, LLM cost |
| `answer_key.yaml` | only for the 33 hand-labelled documents (17 benchmark + 16 corpus): the facts the output was scored against |

Start with `index.html` (open it in a browser; filter by host, product, level or status) or `index.csv` (one row per
document, including the documents that were not processed).

Layout: `corpus1/L<level>_<format>/<host>__<doc_id>/` for the spec_pages_1000 corpus run, `benchmark/<host>__<id>/`
for the 17 benchmark pages (run bench4).

## Reading output.json

- `document`: source URL, language, page type and scope decided by the page map (A2).
- `products[]`: one record per product / variant / subsystem; `parent_entity_id` links a subsystem to its product.
  - `specifications[]`: `parameter.id` + `parameter.status` (`canonical` = v0 baseline list, `candidate` = added to the
    catalogue, `dynamic` = new property kept with the page's own label `source_label`), `value_text` copied verbatim,
    `value` parsed (number, unit, range, comparator), `applies_when` (variant / condition), `evidence[]` (block id +
    quote), `confidence`, `verification` (`verified`, `verified_low_confidence`).
  - `capabilities`, `features`, `technologies`, `components`, `compatibility`, `missions`, `targets`: text facts.
  - `unresolved`: values the system found but would not commit to (abstained rather than guess).
- `entities`, `relations`, `companies`: everything named on the page and how it is related.
- `ledger[]`: every value-like item on the page and what happened to it (extracted / rejected with a reason / unresolved).
- `execution`: models, run notes and errors. A run note containing `BudgetExceeded` means the run's money ran out while
  this document was being processed, so some values stayed unverified.
"""


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    rows: list[dict] = []
    export_run("corpus1", lambda r: f"corpus1/L{r.get('level')}_{r['format']}", rows, ROOT / "eval" / "gold_corpus")
    export_run("bench4", lambda r: "benchmark", rows, ROOT / "eval" / "gold")
    write_index(rows)
    (OUT / "README.md").write_text(README, encoding="utf-8")
    n = sum(1 for r in rows if r["folder"])
    print(f"{n} document folders, {len(rows)} index rows -> {OUT}")


if __name__ == "__main__":
    main()
