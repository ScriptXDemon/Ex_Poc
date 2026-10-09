"""Render the final results page from runs/final_summary.json (see scripts/final_summary.py).

usage: python scripts/make_report.py runs/final_summary.json docs/results_report.html
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

LEVEL_NAMES = {1: "Level 1 · single-product spec pages", 2: "Level 2 · multi-product / variant pages",
               3: "Level 3 · news, articles, non-English", 4: "Level 4 · very long pages and catalogues"}


def e(x) -> str:
    return html.escape("" if x is None else str(x))


def pct(a: float | None, digits: int = 1) -> str:
    return "–" if a is None else f"{100 * a:.{digits}f}%"


def bar(v: float | None) -> str:
    w = 0 if v is None else max(0.0, min(1.0, v)) * 100
    return f"<span class='bar' aria-hidden='true'><span style='width:{w:.1f}%'></span></span>"


def gold_by_group(docs: list[dict]) -> dict[tuple, dict]:
    out: dict[tuple, dict] = {}
    for d in docs:
        g = out.setdefault((d["level"], d["format"]), {"n": 0, "m": 0, "tn": 0, "tm": 0, "mis": 0, "unres": 0, "docs": 0})
        g["docs"] += 1
        g["n"] += d["core"]["n"]
        g["m"] += d["core"]["matched"]
        g["tn"] += d["text"]["n"]
        g["tm"] += d["text"]["matched"]
        g["mis"] += d["core"]["misattributed"]
        g["unres"] += d["core"]["unresolved"]
    return out


def main() -> None:
    s = json.loads(Path(sys.argv[1]).read_text())
    out = Path(sys.argv[2])
    rs = s["run_stats"]
    groups = {(g["level"], g["format"]): g for g in rs["groups"]}
    gold = s["gold"]
    gg = gold_by_group(gold["docs"]) if gold else {}
    benches = {b["run"]: b for b in s["benchmarks"]}
    b_last = benches.get("bench4") or s["benchmarks"][-1]
    sel_by_group = s.get("selected_by_group", {})

    # ---- corpus table (level x format) ----
    corpus_rows = []
    tot = {"docs": 0, "products": 0, "specs": 0, "canonical": 0, "candidate": 0, "dynamic": 0, "text": 0, "unresolved": 0,
           "hint_products": 0, "hint_found": 0, "hint_with_specs": 0, "no_products": 0}
    for lvl in (1, 2, 3, 4):
        for fmt in ("html", "pdf"):
            g = groups.get((lvl, fmt))
            tc = s["by_group_true_cost"].get(f"L{lvl}-{fmt}", {})
            if not g:
                continue
            for k in tot:
                tot[k] += g.get(k, 0)
            dyn_share = g["dynamic"] / g["specs"] if g["specs"] else None
            hint = (f"{g['hint_found']}/{g['hint_products']}" if g["hint_products"] else "–")
            corpus_rows.append(
                f"<tr><td>L{lvl}</td><td>{fmt.upper()}</td><td class='n'>{g['docs']}<span class='of'>/{sel_by_group.get(f'L{lvl}-{fmt}', '')}</span></td>"
                f"<td class='n'>{g['products']}</td><td class='n'>{g['specs']}</td><td class='n'>{g['specs'] / max(1, g['docs']):.1f}</td>"
                f"<td class='n'>{pct(dyn_share, 0)}</td><td class='n'>{g['text']}</td><td class='n'>{g['no_products']}</td>"
                f"<td class='n'>{hint}</td><td class='n'>${tc.get('cost_per_doc', 0):.3f}</td></tr>")
    corpus_table = "".join(corpus_rows)

    # ---- gold per group ----
    gold_rows = []
    for lvl in (1, 2, 3, 4):
        for fmt in ("html", "pdf"):
            g = gg.get((lvl, fmt))
            if not g:
                continue
            r = g["m"] / g["n"] if g["n"] else None
            t = g["tm"] / g["tn"] if g["tn"] else None
            gold_rows.append(f"<tr><td>L{lvl}</td><td>{fmt.upper()}</td><td class='n'>{g['docs']}</td>"
                             f"<td class='n'>{g['m']}/{g['n']}</td><td>{bar(r)} {pct(r, 0)}</td><td class='n'>{g['tm']}/{g['tn']}</td>"
                             f"<td class='n'>{g['mis']}</td><td class='n'>{g['unres']}</td></tr>")
    gold_doc_rows = []
    for d in sorted(gold["docs"], key=lambda d: (d["level"] or 9, d["format"], d["doc_id"])) if gold else []:
        c, t = d["core"], d["text"]
        gold_doc_rows.append(
            f"<tr><td>L{d['level']} {e(d['format']).upper()}</td><td class='url'><a href='{e(d['url'])}' target='_blank' rel='noopener'>{e(d['url'])}</a></td>"
            f"<td class='n'>{c['matched']}/{c['n']}</td><td class='n'>{c.get('via_subsystem', 0)}</td><td class='n'>{c['misattributed']}</td>"
            f"<td class='n'>{c['unresolved']}</td><td class='n'>{c['missed']}</td><td class='n'>{t['matched']}/{t['n']}</td>"
            f"<td class='n'>{d['other_product_facts']}</td><td class='n'>${d['cost']:.3f}</td></tr>")

    # ---- benchmark progression ----
    bench_rows = []
    for name, note in (("bench1", "first full pipeline"), ("bench2", "after error-class fixes"),
                       ("bench3", "label-first mapping (regressed)"), ("bench4", "mapping fix (final)")):
        b = benches.get(name)
        if not b:
            continue
        c, t, p = b["core"], b["text"], b["param"]
        mapped = sum(p.values())
        bench_rows.append(
            f"<tr><td>{name}</td><td>{note}</td><td class='n'>{c['matched']}/{c['n']}</td><td>{bar(c['recall'])} {pct(c['recall'])}</td>"
            f"<td class='n'>{c['misattributed']}</td><td class='n'>{t['matched']}/{t['n']}</td>"
            f"<td class='n'>{pct((p['correct'] + p['dynamic_ok']) / mapped if mapped else None, 0)}</td>"
            f"<td class='n'>{p['wrong_canonical']}</td><td class='n'>{b['old_key']['product_aware']}/{b['old_key']['n']}</td></tr>")

    # ---- proposals / dynamic ----
    prop_rows = "".join(
        f"<tr><td class='mono'>{e(p['proposed_id'])}</td><td class='n'>{p['documents']}</td><td class='n'>{p['occurrences']}</td>"
        f"<td>{e(', '.join(list(p['source_labels'])[:4]))}</td><td>{e(' · '.join(p['examples'][:3]))}</td></tr>"
        for p in s["ontology_proposals_top"][:14])
    langs = ", ".join(f"{e(k)} {v}" for k, v in rs["languages"].items())
    ptypes = ", ".join(f"{e(k).replace('_', ' ')} {v}" for k, v in list(rs["page_types"].items())[:8])
    spec_status = rs["spec_status"]
    accepted = spec_status.get("verified", 0) + spec_status.get("verified_low_confidence", 0)
    gold_core = gold["core"] if gold else {}
    capped = s["budget_capped_docs"]

    page = f"""<title>Defence Extractor Corpus Run</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Semi+Condensed:wght@500;600;700&family=Source+Sans+3:wght@400;600&family=JetBrains+Mono:wght@400;600&display=swap">
<style>
/* Layout: one technical-report column; summary strip, then evidence tables in the order a reviewer checks them. */
:root {{
  --bg: #f3f5f3; --surface: #ffffff; --ink: #18201d; --muted: #59655f; --rule: #d6ddd9; --accent: #8a4f12;
  --good: #2c7a4b; --warn: #a36d0f; --bad: #a8382c; --track: #e3e8e5;
  --display: "Barlow Semi Condensed", "Arial Narrow", system-ui, sans-serif;
  --body: "Source Sans 3", system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: "JetBrains Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg: #111513; --surface: #181e1b; --ink: #e5ebe7; --muted: #9aa69f; --rule: #2a332f; --accent: #e09a4a;
  --good: #5cbf85; --warn: #e0b04a; --bad: #e57a6c; --track: #26302b; color-scheme: dark; }} }}
:root[data-theme="dark"] {{
  --bg: #111513; --surface: #181e1b; --ink: #e5ebe7; --muted: #9aa69f; --rule: #2a332f; --accent: #e09a4a;
  --good: #5cbf85; --warn: #e0b04a; --bad: #e57a6c; --track: #26302b; color-scheme: dark; }}
body {{ background: var(--bg); color: var(--ink); font: 16px/1.55 var(--body); }}
main {{ max-width: 1120px; margin: 0 auto; padding-inline: 16px; padding-block: 28px 64px; display: grid; gap: 34px; }}
h1, h2, h3 {{ font-family: var(--display); line-height: 1.15; text-wrap: balance; margin: 0; }}
h1 {{ font-size: clamp(30px, 4.4vw, 44px); font-weight: 700; letter-spacing: .2px; }}
h2 {{ font-size: 25px; font-weight: 600; }}
h3 {{ font-size: 18px; font-weight: 600; }}
p {{ margin: 0; max-width: 72ch; }}
a {{ color: var(--accent); }}
section {{ display: grid; gap: 12px; min-width: 0; }}
.eyebrow {{ font: 600 12px/1 var(--mono); letter-spacing: 1.2px; text-transform: uppercase; color: var(--accent); }}
.lede {{ color: var(--muted); font-size: 17px; }}
.strip {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 1px; background: var(--rule);
  border: 1px solid var(--rule); border-radius: 10px; overflow: hidden; }}
.kpi {{ background: var(--surface); padding: 16px 18px; display: grid; gap: 4px; }}
.kpi b {{ font: 600 30px/1.1 var(--display); font-variant-numeric: tabular-nums; }}
.kpi span {{ color: var(--muted); font-size: 14px; }}
.wrap {{ overflow-x: auto; border: 1px solid var(--rule); border-radius: 10px; background: var(--surface); }}
table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--rule); vertical-align: top; }}
th {{ font: 600 11.5px/1.3 var(--mono); letter-spacing: .6px; text-transform: uppercase; color: var(--muted); white-space: nowrap; }}
tr:last-child td {{ border-bottom: 0; }}
td.n {{ font-family: var(--mono); font-variant-numeric: tabular-nums; white-space: nowrap; font-size: 13px; }}
td.mono, .mono {{ font-family: var(--mono); font-size: 13px; }}
td.url {{ max-width: 340px; overflow-wrap: anywhere; font-size: 13px; }}
.of {{ color: var(--muted); }}
.bar {{ display: inline-block; width: 70px; height: 7px; background: var(--track); border-radius: 4px; vertical-align: middle; margin-right: 6px; overflow: hidden; }}
.bar span {{ display: block; height: 100%; background: var(--good); }}
.grid2 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 18px; }}
.note {{ background: var(--surface); border: 1px solid var(--rule); border-radius: 10px; padding: 14px 16px; display: grid; gap: 8px; min-width: 0; }}
.note ul {{ margin: 0; padding-left: 18px; display: grid; gap: 6px; }}
.tag {{ display: inline-block; font: 600 11px/1 var(--mono); padding: 4px 7px; border-radius: 999px; border: 1px solid currentColor; }}
.tag.fixed {{ color: var(--good); }} .tag.open {{ color: var(--warn); }}
code {{ font-family: var(--mono); font-size: 13px; background: var(--track); padding: 1px 5px; border-radius: 4px; }}
pre {{ font: 13px/1.5 var(--mono); background: var(--surface); border: 1px solid var(--rule); border-radius: 10px; padding: 12px 14px; overflow-x: auto; margin: 0; }}
.small {{ font-size: 13.5px; color: var(--muted); }}
</style>
<main>
<header style="display:grid;gap:10px">
  <div class="eyebrow">Defence product intelligence · extraction engine v1.1 · test report</div>
  <h1>Defence Extractor Corpus Run</h1>
  <p class="lede">The full agentic pipeline (deterministic parsing, Qwen3.8-27B extraction, gpt-oss-120b audit and verification, Temporal orchestration)
  run on the server over the curated <span class="mono">spec_pages_1000</span> corpus until the OpenRouter budget ran out,
  plus strict scoring against hand-written answer keys.</p>
</header>

<div class="strip" role="list">
  <div class="kpi" role="listitem"><b>{s['completed']}</b><span>documents processed of {s['selected']} in the corpus ({len(s.get('degraded_by_budget_stop', []))} cut short by the budget stop)</span></div>
  <div class="kpi" role="listitem"><b>{pct(gold_core.get('recall'))}</b><span>specs found on {len(gold['docs']) if gold else 0} hand-labelled corpus documents</span></div>
  <div class="kpi" role="listitem"><b>{pct(b_last['core']['recall'])}</b><span>specs found on the 17-page benchmark (old v3 answer key: {b_last['old_key']['product_aware']}/{b_last['old_key']['n']} vs 164)</span></div>
  <div class="kpi" role="listitem"><b>{accepted:,}</b><span>verified specifications in the output JSON</span></div>
  <div class="kpi" role="listitem"><b>${s['cost_per_completed_doc']:.3f}</b><span>average LLM cost per processed document</span></div>
</div>

<section>
  <h2>Accuracy on hand-labelled corpus documents</h2>
  <p>Two documents per level and format were labelled from the parsed page text before looking at the output
  ({len(gold['docs']) if gold else 0} documents; a 100+ page catalogue was swapped for a brochure). A gold fact counts as found only when its value appears
  among the <em>accepted</em> facts of the right product, or of a subsystem record linked to it. Misattributed: found on another product. Unresolved: the system abstained.</p>
  <div class="wrap"><table>
    <tr><th>Level</th><th>Format</th><th>Docs</th><th>Specs found</th><th>Recall</th><th>Text facts</th><th>Misattributed</th><th>Unresolved</th></tr>
    {''.join(gold_rows)}
    <tr><td colspan="3"><b>All</b></td><td class="n"><b>{gold_core.get('matched')}/{gold_core.get('n')}</b></td><td>{bar(gold_core.get('recall'))} <b>{pct(gold_core.get('recall'))}</b></td>
    <td class="n">{gold['text']['matched']}/{gold['text']['n']}</td><td class="n">{gold_core.get('misattributed')}</td><td class="n">{gold_core.get('unresolved')}</td></tr>
  </table></div>
  <p class="small">{gold_core.get('via_subsystem', 0)} of the found specs sit on a linked subsystem record (for example a recovery vehicle's winch pulling force on the winch, which the output links to the vehicle).
  Verbatim share {pct(gold['verbatim_share'], 0)}; must-not-extract violations {gold['negative_violations']}.</p>
  <details><summary class="small">Per-document results</summary>
  <div class="wrap" style="margin-top:8px"><table>
    <tr><th>Doc</th><th>Source</th><th>Specs</th><th>Via subsystem</th><th>Misattr.</th><th>Unres.</th><th>Missed</th><th>Text</th><th>Other-product facts</th><th>Cost</th></tr>
    {''.join(gold_doc_rows)}
  </table></div></details>
</section>

<section>
  <h2>Benchmark progression (17 pages from the old v3 report)</h2>
  <p>Same pages, strict scorer. The last column re-scores the old v3 answer key product-aware (v3 itself caught 164 of 203 that way).</p>
  <div class="wrap"><table>
    <tr><th>Run</th><th>Change</th><th>Specs found</th><th>Recall</th><th>Misattr.</th><th>Text facts</th><th>Parameter OK</th><th>Wrong canonical</th><th>Old key</th></tr>
    {''.join(bench_rows)}
  </table></div>
</section>

<section>
  <h2>Corpus run by level and format</h2>
  <p>Order: a random quarter of every level/format group first (so a budget stop still covers all groups, large documents included), then the rest smallest-first.
  Languages: {langs}. Page types: {ptypes}.</p>
  <div class="wrap"><table>
    <tr><th>Level</th><th>Format</th><th>Done</th><th>Products</th><th>Specs</th><th>Specs/doc</th><th>Dynamic</th><th>Text facts</th><th>No product</th><th>Index products found</th><th>Cost/doc</th></tr>
    {corpus_table}
  </table></div>
  <p class="small">Dynamic: specs whose parameter is not in the v0 baseline list (kept with the page's own label). Index products found: products the corpus gate
  (a different model) named for level-1/2 pages, matched by name against the extracted products — a label-free check of product identification.
  No product: documents where the system found no product (e.g. a safety notice, a charity page).</p>
</section>

<section class="grid2">
  <div class="note"><h3>New parameters seen across documents (A14 proposals)</h3>
  <p class="small">Dynamic properties clustered across documents; each needs approval before joining the catalogue. {s['ontology_proposals_total']} proposals in total.</p>
  <div class="wrap"><table><tr><th>Proposed id</th><th>Docs</th><th>Uses</th><th>Page labels</th><th>Examples</th></tr>{prop_rows}</table></div></div>
  <div class="note"><h3>Output health</h3><ul>
    <li>Ledger: every value-like item on every page ends as extracted, rejected (with a reason) or unresolved — 0 left open.</li>
    <li>Accepted specs: {spec_status.get('verified', 0):,} verified, {spec_status.get('verified_low_confidence', 0):,} verified with low confidence.</li>
    <li>Documents that hit the per-document spend cap: {len(capped)} (catalogues; {s['budget_skipped_ledger_items']:,} values marked <code>not_processed_document_budget</code>).</li>
    <li>Run budget stop: {len(s.get('degraded_by_budget_stop', []))} documents finished while the money ran out (some verification or mapping calls were refused, so those values stay unresolved), {s.get('stopped_midway', 0)} stopped mid-way. Failed documents: {len(s['failed'])}.</li>
  </ul></div>
</section>

<section>
  <h2>Problems found on real inputs, and what changed</h2>
  <div class="grid2">
    <div class="note"><ul>
      <li><span class="tag fixed">fixed</span> 50 of 559 HTML pages (9%) were decoded as Latin-1 because the charset tag came after a large inline style; ° ± – × became garbage. Pages are now decoded as UTF-8 first.</li>
      <li><span class="tag fixed">fixed</span> 8 pages were saved WordPress API responses (JSON in a viewer). They are unwrapped into the article HTML.</li>
      <li><span class="tag fixed">fixed</span> Two IWI brochures map ligatures to byte-swapped code points ("Ri昀氀ing"). Repaired inside Latin words.</li>
      <li><span class="tag fixed">fixed</span> Reader comments (about 1,000 blocks on one page) were read as product facts. Blocks outside the main content are now read only when the page map says they are product content.</li>
    </ul></div>
    <div class="note"><ul>
      <li><span class="tag fixed">fixed</span> Dense catalogue tables overflowed the output limit. Chunks are now capped by value count, a cut-off answer is retried on half the chunk, and the role step is batched for large entity lists.</li>
      <li><span class="tag fixed">fixed</span> A 1,514-block ammunition catalogue would have cost as much as a hundred ordinary pages. A per-document cap ($0.40) now stops new extraction work and reports what was skipped.</li>
      <li><span class="tag fixed">fixed</span> Parameter mapping regressed when a group heading such as "Dimensions" or "Performance" outranked the model's specific parameter. Generic, composite and refined labels now defer to it.</li>
      <li><span class="tag open">open</span> Bare table cells with the unit in the header ("Weight (approx. Kg)" → "8") fall back to dynamic parameters (about 0.7% of facts). Fixed in code after this run; it applies from the next run.</li>
    </ul></div>
  </div>
</section>

<section class="grid2">
  <div class="note"><h3>Where it is still weak</h3><ul>
    <li>Two products in one spec table (SHARKY 774/775): a few values land on the sibling product or are left unresolved.</li>
    <li>News and analysis pages: operational statements (readiness, ports visited) are still accepted as product facts more often than they should be.</li>
    <li>Components named inside a sentence (an ejection seat's catapult and parachute) are recorded as entities rather than as facts of the main product.</li>
    <li>Qwen3.8-27B with thinking off returns an empty list on about a quarter of extraction calls; each one is retried with light thinking, about 12% of the LLM cost.</li>
    <li>The hand-labelled sample is small (16 documents). Treat the per-level numbers as indicative.</li>
  </ul></div>
  <div class="note"><h3>Next steps</h3><ul>
    <li>Run the remaining documents and the capped catalogues on the data-centre models, where token cost is not a constraint.</li>
    <li>Review and approve the A14 parameter proposals into the catalogue.</li>
    <li>Label 20 to 30 more documents, weighted toward news, multi-product tables and non-English pages.</li>
    <li>Roll subsystem specs up into the parent product record in the output JSON, so consumers don't have to follow links.</li>
  </ul></div>
</section>

<section>
  <h2>Run it again</h2>
  <pre>ssh algotest
cd ~/projects/defence-extractor && source .venv/bin/activate
ORDER=budget bash scripts/run_corpus.sh &lt;run_id&gt; 40     # Temporal batch, resumable
python scripts/progress.py &lt;run_id&gt;                     # progress, spend, stage errors
dx report &lt;run_id&gt;                                      # corpus statistics + A14 proposals
dx eval &lt;run_id&gt; --gold eval/gold_corpus --bench none  # score against the answer keys</pre>
  <p class="small">Per-document outputs: <code>runs/&lt;run_id&gt;/docs/&lt;doc&gt;/result.json</code> (products, specifications with evidence and verification, the closed ledger, cost).
  Temporal UI: <code>ssh -L 8233:localhost:8233 algotest</code>, then open localhost:8233.</p>
</section>
</main>"""
    out.write_text(page, encoding="utf-8")
    print(f"wrote {out} ({len(page):,} chars)")


if __name__ == "__main__":
    main()
