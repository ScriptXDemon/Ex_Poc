"""Check an answer key against the parsed text dump of its document (written by scripts/dump_pages.py):
every value must occur in the document (verbatim, whitespace / case-insensitive) - at its block when `b` is given -
and positions / pages are reported. With --set-pos, facts without a position get one from where the value first
occurs (thirds of the document: start / middle / end).

    python scripts/gold_check.py eval/gold_large/<doc>.yaml <dump.txt> [--set-pos]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

LINE = re.compile(r"^\[(?:p(\d+)\|)?([0-9.]+)\|([^|]+)\|[^\]]*\] (.*)$")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def main(argv: list[str]) -> int:
    key_path, dump_path = Path(argv[0]), Path(argv[1])
    d = yaml.safe_load(key_path.read_text(encoding="utf-8"))
    lines = []
    for ln in dump_path.read_text(encoding="utf-8").splitlines():
        m = LINE.match(ln)
        if m:
            lines.append((int(m.group(1)) if m.group(1) else None, float(m.group(2)), m.group(3), norm(m.group(4))))
    blocks = {b: (pg, pos, t) for pg, pos, b, t in lines}
    bad, out = 0, []
    for i, f in enumerate(d.get("facts") or []):
        cands = [norm(f["v"])] + [norm(a) for a in f.get("any", [])]
        where = None
        if f.get("b") in blocks and any(c and c in blocks[f["b"]][2] for c in cands):
            where = blocks[f["b"]]
        if where is None:
            where = next(((pg, pos, t) for pg, pos, b, t in lines if any(c and c in t for c in cands)), None)
            if where is not None and f.get("b"):
                out.append(f"  ~ fact {i} ({f['p']} {f.get('param') or 'text'}): found, but not in block {f['b']}")
        if where is None:
            bad += 1
            out.append(f"  ! fact {i} ({f['p']} {f.get('param') or 'text'}): value not in document: {f['v']!r}")
            continue
        pg, pos = where[0], where[1]
        prod = (d.get("products") or {}).get(f["p"]) or {}
        want = f.get("pos") or prod.get("pos")
        got = "start" if pos < 1 / 3 else "middle" if pos < 2 / 3 else "end"
        if want is None and "--set-pos" in argv:
            f["pos"] = got
            if pg:
                f["page"] = pg
        elif want and want != got:
            out.append(f"  ~ fact {i} ({f['p']}): labelled {want}, value first found at {pos:.2f}" + (f" p{pg}" if pg else ""))
    for g in d.get("negatives") or []:
        if not any(norm(g) in t for *_, t in lines):
            out.append(f"  ! negative not in document: {g!r}")
    n = len(d.get("facts") or [])
    print(f"{key_path.name}: {n} facts, {len(d.get('products') or {})} products, {bad} values not found")
    print("\n".join(out))
    if "--set-pos" in argv:
        text = key_path.read_text(encoding="utf-8")
        head = text.split("\nfacts:")[0]
        body = "\nfacts:\n" + "".join("  - " + yaml.safe_dump(f, default_flow_style=True, allow_unicode=True, width=10000).strip() + "\n"
                                      for f in d["facts"])
        tail = "negatives: " + yaml.safe_dump(d.get("negatives") or [], default_flow_style=True, allow_unicode=True, width=10000).strip() + "\n"
        key_path.write_text(head + body + tail, encoding="utf-8")
        print(f"positions written to {key_path}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
