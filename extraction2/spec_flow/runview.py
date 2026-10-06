# -*- coding: utf-8 -*-
"""The batch run's results for the results page: every page run so far (English first), and for the pages that have a
reviewer answer, how fully the flow extracted them. Scoring is the Excel's (build_xlsx.py): an item matches when the
parameter is the same, the values contain each other and the product names contain each other."""
import collections, glob, io, json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
RUN = os.environ.get("SPEC_FLOW_RUN") or os.path.join(HERE, "run")          # a batch run folder (results/, expected/, review/)
XLSX = os.environ.get("SPEC_FLOW_XLSX") or os.path.join(RUN, "spec_flow_pages.xlsx")
IN_, OUT_ = 0.36e-6, 0.40e-6                                  # DeepInfra $/token for qwen-2.5-72b

norm = lambda s: " ".join(re.findall(r"[^\W_]+", str(s or "").lower()))   # letters and digits only: "Surface - To - Air" = "Surface to Air"
vsame = lambda a, b: bool(norm(a) and norm(b)) and (" %s " % norm(a) in " %s " % norm(b) or " %s " % norm(b) in " %s " % norm(a))   # whole words: "5" is not in "15 km"


def items(o):
    return [(p.get("product") or "", s.get("parameter") or "", s.get("value_text") or "")
            for p in (o or {}).get("products", []) for s in p.get("specs", []) or []]


def score(model, exp, strict=True):
    m, e, used, hit = items(model), items(exp), set(), []
    for x in e:
        j = next((j for j, y in enumerate(m) if j not in used and vsame(y[2], x[2]) and vsame(y[0], x[0])
                  and (not strict or y[1] == x[1])), None)
        if j is not None:
            used.add(j); hit.append(x)
    return hit, [x for x in e if x not in hit], [y for j, y in enumerate(m) if j not in used]


def category(n_exp, n_got, n_hit, ran):
    if not ran:
        return "no model output"
    if not n_exp:
        return "correctly empty" if not n_got else "should be empty"
    r = n_hit / n_exp
    return "fully extracted" if r == 1 else "mostly (50-99%)" if r >= .5 else "partly (1-49%)" if n_hit else "nothing matched"


CATS = ["fully extracted", "mostly (50-99%)", "partly (1-49%)", "nothing matched", "correctly empty", "should be empty", "no model output"]
_cache = {"key": None}


def _load():
    files = glob.glob(os.path.join(RUN, "results", "*.json"))
    rfiles = glob.glob(os.path.join(RUN, "review", "*.json"))   # step 7, the review agent
    key = (len(files), max((os.path.getmtime(f) for f in files), default=0),
           len(rfiles), max((os.path.getmtime(f) for f in rfiles), default=0))
    if _cache["key"] == key:
        return _cache
    cases = {}
    for f in ("cases.json", "cases_rest.json"):
        p = os.path.join(RUN, f)
        if os.path.exists(p):
            for c in json.load(io.open(p, encoding="utf-8")):
                cases[c["Case id"]] = dict(c, sample="first 100" if f == "cases.json" else "rest")
    exp = {}
    for f in glob.glob(os.path.join(RUN, "expected", "batch_*", "expected.json")):
        for x in json.load(io.open(f, encoding="utf-8")):
            exp[x["case"]] = x
    res = {os.path.basename(f)[:-5]: f for f in files}
    rev = {}
    for f in rfiles:
        x = json.load(io.open(f, encoding="utf-8"))
        if not x.get("skipped"):
            rev[x["case"]] = x
    rows = []
    for cid, c in cases.items():
        if cid not in res:
            continue
        r = json.load(io.open(res[cid], encoding="utf-8"))
        try:
            model = json.loads(r.get("final_raw") or "")
        except ValueError:
            model = None
        calls = r.get("calls") or []
        tin, tout = sum(int(k[1]) for k in calls), sum(int(k[3]) for k in calls)
        row = {"case": cid, "sample": c["sample"], "tier": c["Tier"], "kind": c["Page kind"], "layout": c["Layout"],
               "language": c["Language"] or "und", "negative": c["Negative control?"] == "yes", "company": c["Company"],
               "url": c["Link"], "status": r.get("runner_error") or r.get("run_all") or "",
               "verify": r.get("p2", ""), "fits": r.get("p5", ""), "final": r.get("p6", ""),
               "got": len(items(model)) if model is not None else None, "secs": r.get("secs"),
               "cost": round(tin * IN_ + tout * OUT_, 5)}
        x = exp.get(cid)
        if x is not None:
            e = {"products": x["products"]}
            hit, missed, extra = score(model, e) if model is not None else ([], items(e), [])
            vhit = score(model, e, strict=False)[0] if model is not None else []
            n_e, n_g = len(items(e)), row["got"] or 0
            row.update(expected=n_e, matched=len(hit), value_matched=len(vhit), missed=len(missed), extra=len(extra),
                       precision=round(len(hit) / n_g, 3) if n_g else None, recall=round(len(hit) / n_e, 3) if n_e else None,
                       category=category(n_e, n_g, len(hit), model is not None))
        v = rev.get(cid)
        if v is not None:                                    # findings rows: kind, product, parameter, value, had, on page?, why, note
            fs = v.get("findings") or []
            cost = re.search(r"cost \$([\d.]+)", v.get("i7") or "")
            row["review"] = {"status": "error" if v.get("runner_error") or v.get("p7") == "error" else v.get("p7"),
                             "findings": len(fs), "kinds": dict(collections.Counter(f[0] for f in fs)),
                             "not_on_page": sum(f[5] != "yes" for f in fs),
                             "resolver": sum(f[6] == "the resolver dropped it" for f in fs),
                             "model_missed": sum(f[6] == "the model missed it" for f in fs),
                             "cost": float(cost.group(1)) if cost else 0}
        rows.append(row)
    # English first, then the other languages; each by case number
    rows.sort(key=lambda r: (r["language"] != "en", r["language"], int(re.sub(r"\D", "", r["case"]) or 0)))
    s = _summary(rows, len(cases))
    rv = [r["review"] for r in rows if "review" in r]
    s["review"] = {"pages": len(rv), "clean": sum(x["status"] == "clean" for x in rv), "errors": sum(x["status"] == "error" for x in rv),
                   "findings": sum(x["findings"] for x in rv), "not_on_page": sum(x["not_on_page"] for x in rv),
                   "resolver": sum(x["resolver"] for x in rv), "model_missed": sum(x["model_missed"] for x in rv),
                   "cost": round(sum(x["cost"] for x in rv), 3),
                   "kinds": sorted(sum((collections.Counter(x["kinds"]) for x in rv), collections.Counter()).items(), key=lambda k: -k[1])}
    _cache.update(key=key, rows=rows, cases=cases, exp=exp, res=res, rev=rev, summary=s)
    return _cache


def _agg(rs):
    hit, got, e = sum(r["matched"] for r in rs), sum(r["got"] or 0 for r in rs), sum(r["expected"] for r in rs)
    v = sum(r["value_matched"] for r in rs)
    return {"pages": len(rs), "expected": e, "got": got, "matched": hit, "precision": round(hit / got, 3) if got else None,
            "recall": round(hit / e, 3) if e else None, "value_recall": round(v / e, 3) if e else None,
            "full": sum(r["category"] == "fully extracted" for r in rs)}


def _summary(rows, total):
    scored = [r for r in rows if "category" in r]
    by = lambda k: sorted(([g, _agg([r for r in scored if r[k] == g])] for g in {r[k] for r in scored}),
                          key=lambda x: -(x[1]["recall"] or 0))
    status = collections.Counter("all six steps" if r["status"] == "All six steps ran." else
                                 "stopped at verify (carried on)" if r["status"].startswith("Stopped at step 2") else
                                 "a step failed" if r["status"].startswith("Stopped") else "runner error" for r in rows)
    return {"total": total, "run": len(rows), "english_run": sum(r["language"] == "en" for r in rows),
            "status": dict(status), "cost": round(sum(r["cost"] for r in rows), 3),
            "scored": _agg(scored) if scored else None,
            "scored_with_output": _agg([r for r in scored if r["category"] != "no model output"]) if scored else None,
            "categories": [[c, sum(r["category"] == c for r in scored)] for c in CATS],
            "by_layout": by("layout"), "by_language": [["English" if g == "en" else g, a] for g, a in by("language")],
            "by_tier": by("tier"), "by_kind": by("kind")}


def results():
    d = _load()
    return {"summary": d["summary"], "rows": d["rows"]}


def detail(cid):
    d = _load()
    r = json.load(io.open(d["res"][cid], encoding="utf-8"))
    try:
        model = json.loads(r.get("final_raw") or "")
    except ValueError:
        model = None
    x = d["exp"].get(cid)
    out = {"case": cid, "meta": d["cases"][cid], "run": {k: r.get(k) for k in (
        "run_all", "runner_error", "i1", "m2", "m3", "i4", "m5", "m6", "labels", "example", "verify_rows", "final_info")},
        "resolved": r.get("resolved"), "final_prompt": r.get("final_prompt"), "model_output": model,
        "model_raw": None if model is not None else r.get("final_raw")}
    if x is not None:
        e = {"products": x["products"]}
        hit, missed, extra = score(model, e) if model is not None else ([], items(e), [])
        out.update(expected=e, notes=x.get("notes"), matched=hit, missed=missed, extra=extra)
    v = d["rev"].get(cid)
    if v is not None:
        out["review"] = {k: v.get(k) for k in ("p7", "m7", "i7", "findings", "corrected", "runner_error")}
    return out


# ------------------------------------------------------------------ the batch run itself: status, start (resume), stop
BATCH_LOG = os.path.join(RUN, "orchestrate_ui.log")


def _batch_procs():
    import psutil
    out = []
    for p in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        if not (p.info["name"] or "").lower().startswith("python"):
            continue                                         # a shell whose command line only MENTIONS the script is not it
        scripts = [os.path.basename(a) for a in (p.info["cmdline"] or [])[1:3]]
        if "orchestrate.py" in scripts or "run_flow.py" in scripts:
            out.append((p, "orchestrator" if "orchestrate.py" in scripts else "worker"))
    return out


def batch_status():
    ps = _batch_procs()
    orch = [p for p, k in ps if k == "orchestrator"]
    tail = io.open(BATCH_LOG, encoding="utf-8", errors="replace").read().splitlines()[-4:] if os.path.exists(BATCH_LOG) else []
    return {"running": bool(ps), "workers": sum(k == "worker" for _, k in ps),
            "since": min(p.info["create_time"] for p in orch) if orch else None, "log": tail}


def batch_start():
    """resume the whole-corpus run (pages already done are skipped); detached, so it outlives this server"""
    import subprocess, sys
    if _batch_procs():
        return batch_status()
    subprocess.Popen([sys.executable, "orchestrate.py"], cwd=RUN, stdout=open(BATCH_LOG, "a"), stderr=subprocess.STDOUT,
                     creationflags=0x00000008 | 0x00000200, env=dict(os.environ, PYTHONIOENCODING="utf-8"))   # detached
    import time; time.sleep(2)
    return batch_status()


def batch_stop():
    """stop the orchestrator, its workers and their browsers; finished pages are kept, the ones in progress are redone"""
    import psutil
    for p, _ in _batch_procs():
        try:
            for c in p.children(recursive=True):
                c.kill()
            p.kill()
        except psutil.NoSuchProcess:
            pass
    return batch_status()


if __name__ == "__main__":                                   # self-check on the live run folder
    s = results()["summary"]
    assert s["run"] > 0 and s["scored"]["pages"] <= 100, s
    print(json.dumps({k: s[k] for k in ("total", "run", "english_run", "status", "cost", "scored", "categories")}, indent=1))
