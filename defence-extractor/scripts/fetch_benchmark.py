"""Fetch the old benchmark pages (raw HTML snapshots) and keep those whose answer-key values are present.

usage: python scripts/fetch_benchmark.py eval/old_benchmark/bench.json data/benchmark
A page is 'usable' when it returns 200 and >= 80% of its answer-key values appear in the page text (bot-blocked
pages are skipped — no bot-evasion is attempted).
"""

from __future__ import annotations

import concurrent.futures as cf
import gzip
import html
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"


def fetch(url: str) -> tuple[int, bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
                                               "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip, deflate"})
    try:
        with urllib.request.urlopen(req, timeout=40, context=ssl.create_default_context()) as r:
            body = r.read()
            enc = r.headers.get("Content-Encoding", "")
            if enc == "gzip":
                body = gzip.decompress(body)
            elif enc == "deflate":
                body = zlib.decompress(body)
            return r.status, body, r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b"", url
    except Exception as e:  # noqa: BLE001
        return 0, str(e).encode(), url


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(s)).strip().lower()


def main() -> None:
    bench, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    pages = json.loads(bench.read_text())
    with cf.ThreadPoolExecutor(6) as ex:
        results = list(ex.map(lambda p: (p, fetch(p["url"])), pages))
    manifest = []
    for p, (code, body, final) in results:
        text = body.decode("utf-8", "ignore")
        vis = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", text)
        vis = norm(re.sub(r"<[^>]+>", " ", vis))
        keys = p["answer_key"]
        hit = sum(1 for r in keys if norm(r["page_value"]) in vis)
        usable = code == 200 and keys and hit / len(keys) >= 0.8
        if usable:
            (out / f"{p['id']}.html").write_bytes(body)
        manifest.append({"id": p["id"], "name": p["name"], "url": p["url"], "final_url": final, "http": code,
                         "answer_key_values": len(keys), "values_found": hit, "usable": bool(usable),
                         "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        print(f"{p['id']:6} {code:4} {hit:3}/{len(keys):3} usable={usable} {p['name']}")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"usable: {sum(m['usable'] for m in manifest)}/{len(manifest)}")


if __name__ == "__main__":
    main()
