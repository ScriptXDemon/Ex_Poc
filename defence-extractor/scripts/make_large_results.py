"""Build docs/large_file_results.html from the large-file run evaluation (stdlib only).

Inputs (pulled from the server into runs/large_report/): large1_eval.json (dx eval large1 --gold eval/gold_large),
large1_report.json (scripts/large_report.py large1 --json), corpus1_eval.json and corpus1_report.json (the same for the
old run), plus the answer keys in eval/gold_large (for image-only facts) and a small extras.json with run facts.

    python3 scripts/make_large_results.py
"""

from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IN = ROOT / "runs" / "large_report"
OUT = ROOT / "docs" / "large_file_results.html"

NAMES = {
    "pdf_b7c2369cde80f556": ("L3Harris radio catalogue (Interlink CS)", "pdf"),
    "pdf_d01bfafbe389a5f8": ("Northrop Grumman propulsion catalogue", "pdf"),
    "pdf_d717bf66f633d689": ("Future Forces Forum 2022 catalogue", "pdf"),
    "pdf_b1dcc677b47283e3": ("Poongsan copper-alloy catalogue", "pdf"),
    "pdf_ed4c47836e872292": ("Leonardo DRS ADACS catalogue", "pdf"),
    "pdf_d802f1477a652544": ("Lapua 2026 ammunition catalogue", "pdf"),
    "pdf_5b039048bbb93ebd": ("Elbit tank-ammunition catalogue", "pdf"),
    "pdf_e9e47aa8ee65e39e": ("IWI US 2020 catalogue (image pages)", "pdf"),
    "doc_6c07c88a456f9130": ("DID: P-8 Poseidon article", "html"),
    "doc_6e1d0c3d81160802": ("Autel EVO Max 4T product page", "html"),
    "doc_5a4cfa2b110b8e65": ("DID: Arleigh Burke contracts log", "html"),
    "doc_45fc640b99a0fe26": ("Defense news listing, page 3", "html"),
    "doc_ea9543d8cb971d07": ("Oshkosh news listing (no specs)", "html"),
    "doc_5a93f5203ebff67d": ("Navy Lookout: Type 26 guide", "html"),
    "doc_743031a2b7a8dd0b": ("Think Defence: Aster / Sea Viper", "html"),
}
POS = ("start", "middle", "end")


def esc(x) -> str:
    return html.escape(str(x))


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.0f}%" if b else "–"


def load(name: str):
    p = IN / name
    return json.loads(p.read_text()) if p.exists() else None


def by_doc(ev) -> dict:
    return {d["doc_id"]: d for d in (ev or {}).get("docs", [])}


def pos_totals(docs: dict, only: set[str] | None = None) -> dict:
    out = {p: {"n": 0, "matched": 0, "misattributed": 0, "unresolved": 0} for p in POS}
    for did, d in docs.items():
        if only is not None and did not in only:
            continue
        for p, c in (d.get("by_pos") or {}).items():
            if p in out:
                for k in out[p]:
                    out[p][k] += c.get(k, 0)
    return out


def core(docs: dict, only: set[str] | None = None) -> dict:
    t = {"n": 0, "matched": 0, "misattributed": 0, "unresolved": 0, "missed": 0}
    for did, d in docs.items():
        if only is not None and did not in only:
            continue
        c = d.get("core") or {}
        for k in t:
            t[k] += c.get(k, 0)
    return t


def bar(v: float, cls: str = "") -> str:
    return f'<span class="bar {cls}"><span style="width:{max(0.0, min(100.0, v)):.0f}%"></span></span>'


def image_facts(new_docs: dict) -> tuple[int, int]:
    """Matched / total answer-key facts marked src: image (readable only through page vision)."""
    import re
    n = m = 0
    for did, d in new_docs.items():
        kp = ROOT / "eval" / "gold_large" / f"{did}.yaml"
        if not kp.exists():
            continue
        lines = [ln for ln in kp.read_text(encoding="utf-8").splitlines() if ln.strip().startswith("- {p:")]
        img = {i for i, ln in enumerate(lines) if re.search(r"\bsrc: image\b", ln)}
        for r in d.get("rows", []):
            if r["gold"]["idx"] in img:
                n += 1
                m += r["outcome"] == "matched"
    return m, n


def main() -> None:
    new_ev, old_ev = load("large1_eval.json"), load("corpus1_eval.json")
    new_rep, extras = load("large1_report.json") or {}, load("extras.json") or {}
    new, old = by_doc(new_ev), by_doc(old_ev)
    rep = {r["doc_id"]: r for r in new_rep.get("docs", [])}
    scored_new = {k for k, d in new.items() if (d.get("core") or {}).get("n")}
    both = {k for k in scored_new if (old.get(k, {}).get("core") or {}).get("n")}
    cn, co = core(new, both), core(old, both)
    call = core(new)
    pn, po, pall = pos_totals(new, both), pos_totals(old, both), pos_totals(new)
    wo = lambda c: c["misattributed"] / max(1, c["matched"] + c["misattributed"])
    img_m, img_n = image_facts(new)
    spend = extras.get("spend_by_doc") or {}
    cost = extras.get("cost_usd") or sum((rep.get(k) or {}).get("cost_usd", 0) for k in rep)

    css = (ROOT / "docs" / "large_file_plan.html").read_text(encoding="utf-8")
    style = css[css.find("<style>"): css.find("</style>") + 8]
    fonts = css[css.find("<link"): css.find("<style>")]

    kpis = [
        (pct(cn["matched"], cn["n"]), f"of the sampled specs found on the {len(both)} large documents the old run also processed "
         f"({pct(co['matched'], co['n'])} before)", "good" if cn["matched"] > co["matched"] else "warn"),
        (pct(pn["end"]["matched"], pn["end"]["n"]), f"found in the last part of those documents ({pct(po['end']['matched'], po['end']['n'])} before): "
         "accuracy no longer fades with depth", "good"),
        (f"{100 * wo(cn):.1f}%", f"of found values on the wrong product ({100 * wo(co):.1f}% before)", "good" if wo(cn) < wo(co) else "warn"),
        (pct(call["matched"], call["n"]), f"recall on all {len(scored_new)} scored large documents "
         f"({call['matched']} of {call['n']} sampled specs)", ""),
        (f"${cost:.2f}", extras.get("cost_note") or f"LLM cost for {len(rep) or len(new)} documents", ""),
    ]
    kpi_html = "".join(f'<div class="kpi" role="listitem"><b class="{c}">{esc(v)}</b><span>{esc(t)}</span></div>' for v, t, c in kpis)

    def pos_row(label, p):
        cells = "".join(f'<td class="n">{bar(100 * p[x]["matched"] / max(1, p[x]["n"]), "ok")}{pct(p[x]["matched"], p[x]["n"])} '
                        f'<span class="small">({p[x]["matched"]}/{p[x]["n"]}, wrong product {p[x]["misattributed"]})</span></td>' for x in POS)
        return f"<tr><td>{esc(label)}</td>{cells}</tr>"

    pos_html = (pos_row(f"Before: old run, {len(both)} documents", po) + pos_row(f"Now: same {len(both)} documents", pn)
                + pos_row(f"Now: all {len(scored_new)} scored documents", pall))

    rows = []
    for did, (name, fmt) in NAMES.items():
        d, r, o = new.get(did) or {}, rep.get(did) or {}, old.get(did) or {}
        c, oc = d.get("core") or {}, o.get("core") or {}
        size = f'{r.get("pages")} pp' if r.get("pages") else f'{(r.get("chars") or 0) // 1000}k chars'
        bp = " · ".join(f'{x[0]} {d["by_pos"][x]["matched"]}/{d["by_pos"][x]["n"]}' for x in POS if x in (d.get("by_pos") or {}))
        before = f'{oc.get("matched")}/{oc.get("n")}' if oc.get("n") else ("not run" if did != "doc_ea9543d8cb971d07" else "–")
        notes = []
        if r.get("budget_skip"):
            notes.append("budget cap reached")
        vp = r.get("vision_pages") or {}
        if sum((vp.get("blocks_added") or {}).values()):
            k, n = len(vp.get("blocks_added") or {}), len(vp.get("checked") or [])
            notes.append(f'vision: {sum(vp["blocks_added"].values())} blocks from {k} of {n} image page{"s" if n != 1 else ""}')
        rows.append(
            f'<tr><td>{esc(name)}<div class="small mono">{esc(did)}</div></td><td class="n">{esc(size)}</td>'
            f'<td class="n">{c.get("matched", "–")}/{c.get("n", "–")}</td><td class="n">{pct(c.get("matched", 0), c.get("n", 0))}</td>'
            f'<td class="n">{c.get("misattributed", "–")}</td><td class="n">{c.get("unresolved", "–")}</td>'
            f'<td class="n">{esc(before)}</td><td class="small">{esc(bp)}</td><td class="n">{("$%.2f" % spend[did]) if did in spend else "running"}</td>'
            f'<td class="small">{esc("; ".join(notes))}</td></tr>')
    doc_html = "".join(rows)

    acc = {"canonical": 0, "candidate": 0, "dynamic": 0}
    for r in rep.values():
        for k, v in (r.get("accepted_specs") or {}).items():
            acc[k] = acc.get(k, 0) + v
    acc_total = max(1, sum(acc.values()))
    tot = new_rep.get("total") or {}

    body = TEMPLATE.format(
        fonts=fonts, style=style, kpis=kpi_html, pos=pos_html, docs=doc_html,
        n_scored=len(scored_new), n_both=len(both),
        dyn_share=pct(acc["dynamic"], acc_total), cand_share=pct(acc["candidate"], acc_total),
        canon_share=pct(acc["canonical"], acc_total), acc_total=sum(acc.values()),
        dyn_matched=tot.get("dyn_matched", 0), dyn_mis=tot.get("dyn_misattributed", 0),
        img_m=img_m, img_n=img_n, comment_vals=tot.get("comment_values", 0),
        **{k: v for k, v in extras.items() if isinstance(v, (str, int, float)) and k not in ("cost_usd", "pages", "cost_note")},
    )
    OUT.write_text(body, encoding="utf-8")
    print(f"wrote {OUT} ({len(body)} chars)")


TEMPLATE = (ROOT / "scripts" / "large_results_template.html").read_text(encoding="utf-8") if (ROOT / "scripts" / "large_results_template.html").exists() else ""

if __name__ == "__main__":
    main()
