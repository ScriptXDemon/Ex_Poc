"""Self-contained HTML evaluation report (light/dark), one collapsible section per document."""

from __future__ import annotations

import html
from typing import Any

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2430;--muted:#5d6878;--line:#dde2e8;--good:#1f7a4d;--goodbg:#e3f3ea;--bad:#b3261e;--badbg:#fbe6e4;--warn:#8a5a00;--warnbg:#fff3d6;--link:#2456b8}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#13161b;--card:#1b1f26;--ink:#e6e9ee;--muted:#9aa4b2;--line:#2c323c;--good:#6fd39e;--goodbg:#173326;--bad:#f2948c;--badbg:#3a1d1b;--warn:#f3c969;--warnbg:#3a2e12;--link:#8fb3ff}}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,Segoe UI,sans-serif}
main{max-width:1180px;margin:auto;padding:24px 16px}h1{font-size:22px;margin:0 0 4px}.muted{color:var(--muted);font-size:13px}
.tiles{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}.tile{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px;min-width:150px}
.tile b{display:block;font-size:24px;font-variant-numeric:tabular-nums}
details{background:var(--card);border:1px solid var(--line);border-radius:8px;margin:10px 0;padding:10px 14px}summary{cursor:pointer}
.pill{border-radius:99px;padding:1px 9px;font-size:12px;margin:0 4px}.good{background:var(--goodbg);color:var(--good)}.bad{background:var(--badbg);color:var(--bad)}.warn{background:var(--warnbg);color:var(--warn)}
.wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:12.5px}th,td{text-align:left;border-bottom:1px solid var(--line);padding:4px 8px;vertical-align:top}
td.s-matched{color:var(--good);font-weight:600}td.s-missed{color:var(--bad);font-weight:600}td.s-misattributed,td.s-unresolved{color:var(--warn);font-weight:600}
h4{margin:14px 0 4px}a{color:var(--link)}
"""


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def _pct(a: float | None) -> str:
    return "–" if a is None else f"{100 * a:.1f}%"


def render(run_id: str, score: dict[str, Any], baseline: dict[str, Any] | None = None) -> str:
    c, t, p, old = score["core"], score["text"], score["param"], score["old_key"]
    matched_params = sum(p.values()) or 1
    tiles = [
        (_pct(c["recall"]), f"core recall (specs + compatibility + targets): {c['matched']} of {c['n']}"),
        (str(c["misattributed"]), "core facts attributed to the wrong product"),
        (str(c["unresolved"]), "core facts left unresolved (system abstained)"),
        (_pct(t["recall"]), f"text recall (capabilities, features, missions): {t['matched']} of {t['n']}"),
        (_pct((p["correct"] + p["dynamic_ok"]) / matched_params), "parameter correct (canonical or fitting dynamic)"),
        (str(p["wrong_canonical"]), "forced into a wrong canonical field"),
        (_pct(score["verbatim_share"]), "matched facts copied verbatim"),
        (str(score["negative_violations"]), "must-not-extract violations"),
        (str(score["ledger_open"]), "silent drops (ledger items left open)"),
        (f"${score['cost_usd']:.3f}", f"LLM cost for {score['scored']} documents"),
    ]
    if old.get("n"):
        tiles.insert(1, (f"{old['value_only']}/{old['n']}", f"old answer key, value-only (v3 had {old['v3_value_only']})"))
        tiles.insert(2, (f"{old['product_aware']}/{old['n']}", "old answer key, product-aware"))
    out = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>Extraction Benchmark</title><style>{CSS}</style></head><body><main>",
           f"<h1>Extraction benchmark — run {_e(run_id)}</h1>",
           "<p class='muted'>Gold labels were written from the page snapshots before any pipeline output was seen. "
           "A gold fact is <b>matched</b> only when its value is found among <b>verified</b> facts of the <b>right product</b>. "
           "Extras are verified facts on the product that match no gold fact (review for precision).</p>",
           "<div class='tiles'>" + "".join(f"<div class='tile'><b>{_e(a)}</b>{_e(b)}</div>" for a, b in tiles) + "</div>"]
    docs = sorted([d for d in score["docs"] if not d.get("missing_result")], key=lambda d: d["core"]["recall"] or 0)
    for d in docs:
        cc = d["core"]
        miss = cc["n"] - cc["matched"]
        cls = "good" if miss == 0 else ("warn" if miss <= 2 else "bad")
        ok = d.get("old_key", {})
        out.append(f"<details {'open' if miss else ''}><summary><b>{_e(d['doc_id'])}</b> "
                   f"<span class='pill {cls}'>core {cc['matched']}/{cc['n']}</span>"
                   f"<span class='pill'>text {d['text']['matched']}/{d['text']['n']}</span>"
                   + (f"<span class='muted'>old key {ok.get('value_only')}/{ok.get('n')} (v3 {ok.get('v3_value_only')})</span>" if ok else "")
                   + f" <span class='muted'>extras {d['extras']} · other products {d['other_product_facts']} · ${d.get('cost_usd', 0):.4f}</span></summary>")
        out.append("<h4>Gold facts</h4><div class='wrap'><table><tr><th>outcome</th><th>kind</th><th>gold parameter</th>"
                   "<th>gold value</th><th>output value</th><th>output parameter</th><th>param check</th></tr>")
        for r in d["rows"]:
            g, pr = r["gold"], r.get("pred") or {}
            out.append(f"<tr><td class='s-{_e(r['outcome'])}'>{_e(r['outcome'])}{(' (' + _e(r.get('match')) + ')') if r.get('match') else ''}</td>"
                       f"<td>{_e(g['k'])}</td><td>{_e(g.get('param'))}</td><td>{_e(g['v'])}{(' <span class=muted>[' + _e(g.get('cond')) + ']</span>') if g.get('cond') else ''}</td>"
                       f"<td>{_e(pr.get('value'))}</td><td>{_e(pr.get('param'))} <span class='muted'>{_e(pr.get('ontology_status') or '')}</span></td>"
                       f"<td>{_e(r.get('param', ''))}</td></tr>")
        out.append("</table></div>")
        if d["extra_list"]:
            out.append(f"<h4>Extras ({d['extras']}) — verified output facts with no gold match</h4><div class='wrap'><table>"
                       "<tr><th>parameter</th><th>label</th><th>value</th><th>status</th></tr>")
            for x in d["extra_list"][:80]:
                out.append(f"<tr><td>{_e(x['param'])}</td><td>{_e(x['label'])}</td><td>{_e(x['value'])}</td><td>{_e(x['status'])}</td></tr>")
            out.append("</table></div>")
        if d["negative_violations"]:
            out.append("<h4>Must-not-extract violations</h4><ul>" + "".join(
                f"<li>{_e(v['negative'])} → {_e(v['pred']['value'])}</li>" for v in d["negative_violations"]) + "</ul>")
        out.append("</details>")
    out.append("</main></body></html>")
    return "\n".join(out)
