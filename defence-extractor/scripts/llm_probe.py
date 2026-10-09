"""Probe an OpenAI-compatible LLM endpoint for what the pipeline needs: chat, strict JSON-schema output, thinking
on/off, image input and parallel throughput. Reads the endpoint from .env (FARM_BASE_URL / FARM_API_KEY, else
LLM_BASE_URL / LLM_API_KEY) and never prints the key.

    python scripts/llm_probe.py [--models qwen3.8:27b,qwen2.5-72b-instruct] [--parallel 8]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def env() -> dict[str, str]:
    out: dict[str, str] = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["facts"],
    "properties": {"facts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["parameter", "value_text"],
        "properties": {"parameter": {"type": "string"}, "value_text": {"type": "string"}}}}},
}
SPEC_TEXT = "The M322 cartridge weighs approx. 20 kg and has a muzzle velocity of 1,705 m/s; the M338 weighs approx. 21 kg."


def png_with_text() -> str:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (900, 260), "white")
    d = ImageDraw.Draw(img)
    for i, line in enumerate(["TECHNICAL DATA", "Max range: 120 km", "Weight: 45.5 kg", "Crew: 3"]):
        try:
            d.text((40, 30 + i * 52), line, fill="black", font_size=36)
        except TypeError:  # Pillow < 10.1: default bitmap font only
            d.text((40, 30 + i * 52), line, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


async def call(c: httpx.AsyncClient, base: str, key: str, body: dict, timeout: float = 300) -> tuple[dict | None, float, str]:
    t0 = time.time()
    try:
        r = await c.post(f"{base}/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
        dt = time.time() - t0
        if r.status_code != 200:
            return None, dt, f"HTTP {r.status_code}: {r.text[:300]}"
        return r.json(), dt, ""
    except Exception as e:  # network / timeout
        return None, time.time() - t0, f"{type(e).__name__}: {str(e)[:200]}"


def summary(j: dict | None) -> dict:
    if not j:
        return {}
    ch = (j.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    return {"content": content, "reasoning_chars": len(reasoning), "think_tag": "<think>" in content,
            "finish": ch.get("finish_reason"), "usage": j.get("usage") or {}}


async def probe_model(c, base, key, model, parallel):
    print(f"\n===== {model}")
    j, dt, err = await call(c, base, key, {"model": model, "messages": [{"role": "user", "content": "Hello!"}], "max_tokens": 300})
    s = summary(j)
    print(f"hello: {dt:.1f}s {err or repr(s['content'][:160])} | reasoning chars {s.get('reasoning_chars')} | usage {s.get('usage')}")

    q = "What is 17*23? Reply with the number only."
    for label, extra in (("thinking off (chat_template_kwargs)", {"chat_template_kwargs": {"enable_thinking": False}}),
                         ("thinking on (chat_template_kwargs)", {"chat_template_kwargs": {"enable_thinking": True}}),
                         ("thinking off (/no_think)", {"_suffix": " /no_think"}),
                         ("reasoning_effort=low", {"reasoning_effort": "low"})):
        suffix = extra.pop("_suffix", "")
        j, dt, err = await call(c, base, key, {"model": model, "messages": [{"role": "user", "content": q + suffix}],
                                               "max_tokens": 1500, **extra})
        s = summary(j)
        print(f"{label}: {dt:.1f}s {err or repr(s['content'][-60:])} | reasoning chars {s.get('reasoning_chars')} "
              f"| <think> in content {s.get('think_tag')} | completion tokens {(s.get('usage') or {}).get('completion_tokens')}")

    body = {"model": model, "temperature": 0.1, "max_tokens": 800,
            "messages": [{"role": "system", "content": "Extract every technical specification as JSON."},
                         {"role": "user", "content": SPEC_TEXT}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "Specs", "strict": True, "schema": SCHEMA}},
            "chat_template_kwargs": {"enable_thinking": False}}
    j, dt, err = await call(c, base, key, body)
    s = summary(j)
    ok = False
    if s:
        try:
            parsed = json.loads(s["content"])
            ok = isinstance(parsed.get("facts"), list) and all({"parameter", "value_text"} <= set(f) for f in parsed["facts"])
            print(f"json_schema: {dt:.1f}s valid={ok} facts={len(parsed.get('facts', []))} -> {s['content'][:220]}")
        except ValueError:
            print(f"json_schema: {dt:.1f}s NOT JSON -> {s['content'][:220]!r}")
    else:
        print(f"json_schema: {err}")

    try:
        img = png_with_text()
        j, dt, err = await call(c, base, key, {"model": model, "max_tokens": 300, "chat_template_kwargs": {"enable_thinking": False},
                                               "messages": [{"role": "user", "content": [
                                                   {"type": "text", "text": "Transcribe all text in this image exactly."},
                                                   {"type": "image_url", "image_url": {"url": img}}]}]})
        s = summary(j)
        print(f"image input: {dt:.1f}s {err or repr(s['content'][:200])}")
    except ImportError:
        print("image input: skipped (Pillow not installed)")

    prompt = "Write a 250-word technical description of a generic 4x4 armoured patrol vehicle."
    t0 = time.time()
    res = await asyncio.gather(*[call(c, base, key, {"model": model, "max_tokens": 600, "chat_template_kwargs": {"enable_thinking": False},
                                                      "messages": [{"role": "user", "content": prompt}]}) for _ in range(parallel)])
    wall = time.time() - t0
    toks = sum(((r[0] or {}).get("usage") or {}).get("completion_tokens") or 0 for r in res)
    errs = [r[2] for r in res if r[2]]
    lat = sorted(r[1] for r in res)
    print(f"parallel x{parallel}: wall {wall:.1f}s, {toks} completion tokens -> {toks / max(wall, 1e-6):.0f} tok/s total, "
          f"latency min {lat[0]:.1f}s max {lat[-1]:.1f}s, errors {len(errs)} {errs[:1]}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    e = env()
    ap.add_argument("--models", default=",".join(x for x in (e.get("FARM_MAIN_MODEL"), e.get("FARM_VERIFIER_MODEL")) if x))
    ap.add_argument("--parallel", type=int, default=8)
    a = ap.parse_args()
    base = (e.get("FARM_BASE_URL") or e.get("LLM_BASE_URL") or "").rstrip("/")
    key = e.get("FARM_API_KEY") or e.get("LLM_API_KEY") or ""
    print(f"endpoint {base} (key {'set' if key else 'MISSING'})")
    async with httpx.AsyncClient() as c:
        try:
            r = await c.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
            ids = [m.get("id") for m in (r.json().get("data") or [])] if r.status_code == 200 else []
            print(f"/models: HTTP {r.status_code} {ids[:20]}")
        except Exception as ex:
            print(f"/models: {type(ex).__name__}: {ex}")
        for m in [x for x in a.models.split(",") if x]:
            await probe_model(c, base, key, m, a.parallel)


if __name__ == "__main__":
    asyncio.run(main())
