"""Command line interface:  dx <command> --help"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import get_settings
from .ingest.corpus import DocRef, load_benchmark, load_corpus

app = typer.Typer(add_completion=False, help="Defence product intelligence extraction")
console = Console()


def _interleave(refs: list[DocRef], seed: int) -> list[DocRef]:
    """Round-robin across (level, format) groups so a budget stop still leaves every group represented."""
    rnd = random.Random(seed)
    groups: dict[tuple, list[DocRef]] = {}
    for r in refs:
        groups.setdefault((r.level, r.format == "pdf"), []).append(r)
    for g in groups.values():
        rnd.shuffle(g)
    out: list[DocRef] = []
    keys = sorted(groups, key=lambda k: (k[0] if k[0] is not None else 9, k[1]))
    while any(groups[k] for k in keys):
        for k in keys:
            if groups[k]:
                out.append(groups[k].pop())
    return out


def _budget_order(refs: list[DocRef], seed: int, head_frac: float = 0.25) -> list[DocRef]:
    """Most documents per dollar while keeping every (level, format) group represented: first an unbiased random
    slice of every group (round-robin, so a budget stop still covers all groups, big documents included), then the
    rest smallest-first (cost grows with document size), so a budget stop leaves only the largest documents."""
    rnd = random.Random(seed)
    groups: dict[tuple, list[DocRef]] = {}
    for r in refs:
        groups.setdefault((r.level, r.format == "pdf"), []).append(r)
    head: dict[tuple, list[DocRef]] = {}
    rest: list[DocRef] = []
    for k, g in groups.items():
        g = list(g)
        rnd.shuffle(g)
        n = max(1, round(len(g) * head_frac))
        head[k], rest = g[:n], rest + g[n:]
    out: list[DocRef] = []
    keys = sorted(head, key=lambda k: (k[0] if k[0] is not None else 9, k[1]))
    while any(head[k] for k in keys):
        for k in keys:
            if head[k]:
                out.append(head[k].pop())
    rest.sort(key=lambda r: (r.chars if r.chars is not None else 10**9, r.doc_id))
    return out + rest


def _prioritize(refs: list[DocRef], run_dir: Path, first: str | None) -> list[DocRef]:
    """Documents already started in this run (resume them before spending on new ones), then ``first``
    (comma-separated ids or @file), then the selection order."""
    want = (Path(first[1:]).read_text().split() if first.startswith("@") else first.split(",")) if first else []
    rank = {d: i for i, d in enumerate(want)}
    started = {p.parent.name for p in (run_dir / "docs").glob("*/state.json") if not (p.parent / "result.json").exists()}
    head = [r for r in refs if r.doc_id in started or r.doc_id in rank]
    head.sort(key=lambda r: (r.doc_id not in started, rank.get(r.doc_id, 0)))
    ids = {r.doc_id for r in head}
    return head + [r for r in refs if r.doc_id not in ids]


def _select(refs: list[DocRef], levels: str | None, fmt: str | None, limit: int | None, ids: str | None, seed: int,
            per_level: int | None, order: str = "default") -> list[DocRef]:
    if ids:  # comma-separated ids or @file (one id per line)
        want = set(Path(ids[1:]).read_text().split() if ids.startswith("@") else ids.split(","))
        return [r for r in refs if r.doc_id in want]
    if order == "interleave":
        refs = _interleave(refs, seed)
    elif order == "budget":
        refs = _budget_order(refs, seed)
    if levels:
        lv = {int(x) for x in levels.split(",")}
        refs = [r for r in refs if r.level in lv]
    if fmt:
        refs = [r for r in refs if (r.format == "pdf") == (fmt == "pdf")]
    if per_level:
        rnd = random.Random(seed)
        out = []
        for lvl in sorted({r.level for r in refs}, key=lambda x: (x is None, x)):
            pool = [r for r in refs if r.level == lvl]
            out += rnd.sample(pool, min(per_level, len(pool)))
        refs = out
    if limit:
        refs = refs[:limit]
    return refs


@app.command()
def run(source: str = typer.Option("corpus", help="corpus | benchmark"),
        root: str = typer.Option(None, help="data root (default data/spec_pages_1000 or data/benchmark)"),
        run_id: str = typer.Option(None), levels: str = typer.Option(None), fmt: str = typer.Option(None, help="html|pdf"),
        limit: int = typer.Option(None), ids: str = typer.Option(None), per_level: int = typer.Option(None),
        seed: int = typer.Option(7), concurrency: int = typer.Option(4), budget_cap: float = typer.Option(None),
        order: str = typer.Option("default", help="default | interleave | budget"), verbose: bool = typer.Option(False)):
    """Run the full pipeline (local async runner) on a selection of documents."""
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = get_settings()
    root = root or str(s.data_dir / ("benchmark" if source == "benchmark" else "spec_pages_1000"))
    refs = load_benchmark(root) if source == "benchmark" else load_corpus(root)
    refs = _select(refs, levels, fmt, limit, ids, seed, per_level, order)
    run_id = run_id or time.strftime("run_%Y%m%d_%H%M%S")
    console.print(f"[bold]run {run_id}[/bold]: {len(refs)} documents, concurrency {concurrency}")
    from .pipeline.runner import build_runtime, run_batch

    rt = build_runtime(s, run_id, budget_cap)
    run_dir = s.runs_dir / run_id
    (run_dir / "selection.json").write_text(json.dumps([r.model_dump() for r in refs], indent=1))
    t0 = time.time()

    def on_done(sm: dict):
        console.print(f"  {sm.get('status'):17} {sm['doc_id'][:24]:24} L{sm.get('level')} specs={sm.get('specs', '-')} "
                      f"dyn={sm.get('dynamic_specs', '-')} text={sm.get('text_facts', '-')} unres={sm.get('unresolved', '-')} "
                      f"open={((sm.get('ledger') or {}).get('open', '-'))} ${sm.get('cost_usd', 0):.4f} {sm.get('seconds', 0)}s "
                      + (f"err={sm.get('error', '')[:80]}" if sm.get("status") != "completed" else ""))
        with (run_dir / "summaries.jsonl").open("a") as f:
            f.write(json.dumps(sm) + "\n")

    async def main():
        await rt.gw.budget.refresh()
        console.print(f"budget: {rt.gw.budget.snapshot()}")
        async with rt.gw:
            return await run_batch(rt, refs, run_id, s.runs_dir, concurrency=concurrency, on_done=on_done)

    out = asyncio.run(main())
    cost = sum(o.get("cost_usd", 0) or 0 for o in out)
    ok = sum(1 for o in out if o.get("status") == "completed")
    console.print(f"[bold]done[/bold] {ok}/{len(out)} completed in {time.time() - t0:.0f}s, cost ${cost:.4f}, "
                  f"budget {rt.gw.budget.snapshot()}")


@app.command("temporal-worker")
def temporal_worker(max_activities: int = typer.Option(24)):
    """Start a Temporal worker (stage activities + document/batch workflows)."""
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from .orchestrator.service import run_worker

    asyncio.run(run_worker(max_activities))


@app.command("temporal-run")
def temporal_run(source: str = typer.Option("corpus"), root: str = typer.Option(None), run_id: str = typer.Option(None),
                 levels: str = typer.Option(None), fmt: str = typer.Option(None), limit: int = typer.Option(None),
                 ids: str = typer.Option(None), per_level: int = typer.Option(None), seed: int = typer.Option(7),
                 concurrency: int = typer.Option(6), budget_cap: float = typer.Option(None), wait: bool = typer.Option(True),
                 order: str = typer.Option("default", help="default | interleave | budget"),
                 first: str = typer.Option(None, help="doc ids (comma-separated or @file) to run first")):
    """Submit a batch to Temporal (durable execution; resumable; visible in the Temporal UI)."""
    s = get_settings()
    root = root or str(s.data_dir / ("benchmark" if source == "benchmark" else "spec_pages_1000"))
    refs = load_benchmark(root) if source == "benchmark" else load_corpus(root)
    refs = _select(refs, levels, fmt, limit, ids, seed, per_level, order)
    run_id = run_id or time.strftime("run_%Y%m%d_%H%M%S")
    (s.runs_dir / run_id / "docs").mkdir(parents=True, exist_ok=True)
    refs = _prioritize(refs, s.runs_dir / run_id, first)
    (s.runs_dir / run_id / "selection.json").write_text(json.dumps([r.model_dump() for r in refs], indent=1))
    from .orchestrator.service import submit_batch

    console.print(f"[bold]temporal run {run_id}[/bold]: {len(refs)} documents, concurrency {concurrency}")
    out = asyncio.run(submit_batch(run_id, [r.model_dump() for r in refs], concurrency, budget_cap, wait))
    if wait:
        with (s.runs_dir / run_id / "summaries.jsonl").open("a") as f:
            for r in out["results"]:
                f.write(json.dumps(r) + "\n")
        console.print(f"done: {out['completed']}/{out['documents']} completed")
    else:
        console.print(out)


@app.command()
def summary(run_id: str):
    """Aggregate statistics for a run."""
    s = get_settings()
    rows = [json.loads(line) for line in (s.runs_dir / run_id / "summaries.jsonl").read_text().splitlines() if line.strip()]
    done = [r for r in rows if r.get("status") == "completed" and not r.get("cached")]
    t = Table(title=f"{run_id}: {len(done)} completed of {len(rows)}")
    for c in ("level", "fmt", "docs", "products", "specs", "dynamic", "text", "unresolved", "ledger open", "cost $", "s/doc"):
        t.add_column(c)
    groups: dict = {}
    for r in done:
        groups.setdefault((r.get("level"), r.get("format")), []).append(r)
    for (lvl, fmt), g in sorted(groups.items(), key=lambda x: (x[0][0] or 9, x[0][1])):
        t.add_row(str(lvl), fmt, str(len(g)), str(sum(r["products"] for r in g)), str(sum(r["specs"] for r in g)),
                  str(sum(r["dynamic_specs"] for r in g)), str(sum(r["text_facts"] for r in g)), str(sum(r["unresolved"] for r in g)),
                  str(sum(r["ledger"]["open"] for r in g)), f"{sum(r['cost_usd'] for r in g):.3f}",
                  f"{sum(r['seconds'] for r in g) / max(1, len(g)):.0f}")
    console.print(t)


@app.command("eval")
def evaluate(run_id: str, gold: str = typer.Option("eval/gold"), bench: str = typer.Option("eval/old_benchmark/bench.json")):
    """Score a run against gold labels (+ old answer key) and write eval.json / eval_report.html into the run folder."""
    from .evaluation.report import render
    from .evaluation.scorer import score_run

    s = get_settings()
    run_dir = s.runs_dir / run_id
    sc = score_run(run_dir, s.project_root / gold, s.project_root / bench)
    (run_dir / "eval.json").write_text(json.dumps(sc, indent=1, default=str))
    (run_dir / "eval_report.html").write_text(render(run_id, sc))
    c, t, p, o = sc["core"], sc["text"], sc["param"], sc["old_key"]
    console.print(f"[bold]{run_id}[/bold] scored {sc['scored']}/{sc['documents']} docs")
    console.print(f"  core recall {c['matched']}/{c['n']} = {c['recall']} (of which {c.get('via_subsystem', 0)} on a linked subsystem record)"
                  f"  | misattributed {c['misattributed']} | unresolved {c['unresolved']} | missed {c['missed']}")
    console.print(f"  text recall {t['matched']}/{t['n']} = {t['recall']}")
    if sc.get("by_pos"):
        console.print("  by position: " + " | ".join(f"{k} {v['matched']}/{v['n']} (mis {v['misattributed']}, unres {v['unresolved']})"
                                                     for k, v in sc["by_pos"].items()))
    console.print(f"  params: {p} | verbatim share {sc['verbatim_share']} | merged {sc['merged_pred_facts']}")
    console.print(f"  extras {sc['extras']} | other-product facts {sc['other_product_facts']} | negative violations {sc['negative_violations']} | ledger open {sc['ledger_open']}")
    console.print(f"  old answer key: value-only {o['value_only']}/{o['n']} (v3 {o['v3_value_only']}), product-aware {o['product_aware']}/{o['n']}")
    console.print(f"  cost ${sc['cost_usd']}  -> {run_dir / 'eval_report.html'}")


@app.command()
def report(run_id: str, min_docs: int = typer.Option(3)):
    """Corpus statistics + A14 ontology proposals + HTML run report (no gold labels needed)."""
    from .evaluation.corpus import ontology_proposals, render_run_report, run_stats

    s = get_settings()
    run_dir = s.runs_dir / run_id
    stats = run_stats(run_dir)
    props = ontology_proposals(run_dir, min_docs)
    (run_dir / "run_stats.json").write_text(json.dumps(stats, indent=1))
    (run_dir / "ontology_proposals.json").write_text(json.dumps(props, indent=1))
    (run_dir / "run_report.html").write_text(render_run_report(run_id, stats, props))
    console.print(f"{stats['documents']} documents, cost ${stats['cost_usd']}, {len(props)} ontology proposals -> {run_dir / 'run_report.html'}")


@app.command()
def budget():
    """Show the OpenRouter key balance."""
    from .llm import BudgetGuard

    s = get_settings()
    b = BudgetGuard(s.openrouter_base_url, s.openrouter_api_key)
    asyncio.run(b.refresh())
    console.print(b.snapshot())


@app.command()
def schema(out: str = typer.Option("docs/final_result.schema.json")):
    """Export the JSON Schema of the final document result."""
    from .contracts.result import FinalDocumentResult

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(FinalDocumentResult.model_json_schema(), indent=1))
    console.print(f"wrote {out}")


if __name__ == "__main__":
    app()
