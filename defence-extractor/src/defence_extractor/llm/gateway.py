"""OpenAI-compatible LLM gateway (OpenRouter now, SGLang/vLLM in the datacenter later — same wire format).

Features: logical model roles, strict JSON-schema structured outputs, provider pinning, explicit reasoning control,
retries with backoff, one schema-repair retry, refusal detection, response cache, budget guard, per-call JSONL log.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Any, Generic, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..config import Settings
from ..contracts.common import Usage
from .budget import BudgetExceeded, BudgetGuard
from .cache import ResponseCache
from .schema_utils import strict_schema

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

REASONING = {
    "off": {"enabled": False},
    "low": {"effort": "low"},
    "medium": {"effort": "medium"},
    "high": {"effort": "high"},
}
_REFUSAL = re.compile(r"(?i)^\s*(i'?m sorry|i can(?:no|')t (?:help|assist|comply)|i am unable to|i won'?t)")


class LLMError(RuntimeError):
    pass


class RefusalError(LLMError):
    pass


class SchemaError(LLMError):
    pass


class ContextOverflowError(SchemaError):
    """The input leaves too little of the endpoint's context window for an answer: the caller splits its input
    (bisect_on_overflow and batch splitting catch SchemaError)."""


PROMPT_CHARS_PER_TOKEN = 3.0  # conservative: JSON, numbers and non-English text take more tokens per character
IMAGE_TOKENS = 2000  # a page or figure render at ~1400 px
MIN_ANSWER_TOKENS = 1200  # below this, a call cannot answer within the context window ...
FORCED_THINKING_TOKENS = 1500  # ... plus the reasoning spent first by a call that thinks


def prompt_tokens(text: str) -> int:
    """Prompt tokens, conservatively: Qwen-family tokenizers split numbers into single digits, so a spec table costs
    about one token per digit ("1,838,200" is 9 tokens, not 3); other text about PROMPT_CHARS_PER_TOKEN characters."""
    digits = sum(ch.isdigit() for ch in text)
    return int((len(text) - digits) / PROMPT_CHARS_PER_TOKEN + digits)


class SlowModelError(LLMError):
    """A guarded (primary) model produced too little too slowly: the call moves to the fallback model."""


class LLMResult(BaseModel, Generic[T]):
    parsed: Any
    usage: Usage
    model: str
    provider: str | None = None
    latency_s: float = 0.0
    cache_hit: bool = False


def _extract_json(text: str) -> Any:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        s, e = t.find("{"), t.rfind("}")
        if s >= 0 and e > s:
            return json.loads(t[s : e + 1])
        raise


class Gateway:
    def __init__(self, settings: Settings, log_path: str | Path, budget: BudgetGuard | None = None,
                 cache: ResponseCache | None = None):
        self.s = settings
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.budget = budget
        self.cache = cache
        self._sems = {r: asyncio.Semaphore(settings.llm_concurrency) for r in ("main", "verifier", "vision")}
        self._client: httpx.AsyncClient | None = None
        self.usage = Usage()
        self.by_doc: dict[str, Usage] = {}
        self.providers: dict[str, int] = {}
        self.providers_by_doc: dict[str, dict[str, int]] = {}
        self._log_lock = asyncio.Lock()
        self._breaker: dict[str, tuple[int, float]] = {}  # model -> (failures in a row, skip until)

    def seed_spend_from_log(self) -> None:
        """Per-document LLM spend from earlier processes of this run (the call log), so a restarted worker keeps
        enforcing the per-document cap instead of starting every document from zero."""
        if not self.log_path.exists():
            return
        with self.log_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                c = r.get("cost_usd") or 0.0
                if c and r.get("doc_id"):
                    self.by_doc.setdefault(r["doc_id"], Usage()).cost_usd += c

    def _account(self, doc_id: str, u: Usage, provider: str | None = None) -> None:
        self.usage.add(u)
        self.by_doc.setdefault(doc_id, Usage()).add(u)
        if provider:
            self.providers[provider] = self.providers.get(provider, 0) + 1
            d = self.providers_by_doc.setdefault(doc_id, {})
            d[provider] = d.get(provider, 0) + 1

    async def __aenter__(self) -> Gateway:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.s.request_timeout_s, connect=20))
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client is not None:
            await self._client.aclose()

    # ------------------------------------------------------------------------------------------
    async def call(self, *, agent: str, role: str, system: str, user: str, schema: type[T], doc_id: str = "",
                   reasoning: str = "off", max_tokens: int | None = None, temperature: float | None = None,
                   images: list[str] | None = None) -> LLMResult[T]:
        route = self.s.route(role)
        kw = dict(agent=agent, role=role, system=system, user=user, schema=schema, doc_id=doc_id, reasoning=reasoning,
                  max_tokens=max_tokens, temperature=temperature, images=images)
        fb = route.fallback_model
        if not fb or fb == route.model:
            return await self._call_model(route.model, **kw)
        if self._breaker_open(route.model):
            return await self._call_model(fb, **kw)
        try:
            res = await self._call_model(route.model, guarded=True, **kw)
            self._breaker.pop(route.model, None)
            return res
        except LLMError as e:
            self._breaker_fail(route.model)
            await self._log({"agent": agent, "role": role, "doc_id": doc_id, "model": route.model, "status": "fallback",
                             "fallback_model": fb, "error": f"{type(e).__name__}: {str(e)[:200]}"})
            return await self._call_model(fb, **kw)

    def _breaker_open(self, model: str) -> bool:
        _fails, until = self._breaker.get(model, (0, 0.0))
        return bool(until) and time.time() < until

    def _breaker_fail(self, model: str) -> None:
        fails, until = self._breaker.get(model, (0, 0.0))
        fails += 1
        if fails >= self.s.fallback_after_failures:
            until = time.time() + self.s.fallback_cooldown_s
        self._breaker[model] = (fails, until)

    async def _call_model(self, model: str, *, agent: str, role: str, system: str, user: str, schema: type[T],
                          doc_id: str = "", reasoning: str = "off", max_tokens: int | None = None,
                          temperature: float | None = None, images: list[str] | None = None,
                          guarded: bool = False) -> LLMResult[T]:
        route = self.s.route(role)
        # images: data URLs (page renders) sent with the text as one multimodal user message
        content: Any = user if not images else [{"type": "text", "text": user}] + [
            {"type": "image_url", "image_url": {"url": u}} for u in images]
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": schema.__name__, "strict": True, "schema": strict_schema(schema)}},
            "max_tokens": min(self.s.max_tokens_ceiling, (max_tokens or route.max_tokens) + (
                self.s.thinking_overhead_tokens if reasoning == "off" and self.s.llm_api_flavor != "openrouter" else 0)),
            "temperature": route.temperature if temperature is None else temperature,
            "usage": {"include": True},
        }
        if route.top_p is not None:
            body["top_p"] = route.top_p
        if route.supports_seed:
            body["seed"] = 7
        if self.s.llm_api_flavor == "openrouter":
            if reasoning in REASONING:
                body["reasoning"] = REASONING[reasoning]
            prov: dict[str, Any] = {"require_parameters": True, "allow_fallbacks": route.allow_fallbacks}
            if route.providers:
                prov["order"] = route.providers
            if route.quantizations:
                prov["quantizations"] = route.quantizations
            body["provider"] = prov
        else:  # self-hosted SGLang / vLLM (OpenAI-compatible)
            body.pop("usage", None)
            if "gpt-oss" in model:
                body["reasoning_effort"] = "low" if reasoning == "off" else reasoning
            else:  # Qwen3.x hybrid thinking: the chat template switch, and reasoning_effort, which the farm's proxy honours
                body["chat_template_kwargs"] = {"enable_thinking": reasoning != "off"}
                if reasoning != "off":
                    body["reasoning_effort"] = reasoning

        if self.s.context_window:  # the answer gets what the prompt leaves of the window; too little -> split upstream
            est = prompt_tokens(system + user) + IMAGE_TOKENS * len(images or []) + 64
            room = self.s.context_window - est
            thinks = reasoning != "off" or self.s.thinking_overhead_tokens > 0
            need = MIN_ANSWER_TOKENS + (FORCED_THINKING_TOKENS if thinks else 0)
            if room < need:  # it would think until the window is full and answer nothing: split upstream instead
                raise ContextOverflowError(f"{agent}: prompt ~{est} tokens leaves {room} of a {self.s.context_window}-token context")
            body["max_tokens"] = min(body["max_tokens"], room)
        key = hashlib.sha256(json.dumps({k: v for k, v in body.items() if k != "usage"}, sort_keys=True).encode()).hexdigest()
        if self.cache is not None and self.s.llm_cache:
            hit = self.cache.get(key)
            if hit is not None:
                try:
                    parsed = schema.model_validate(hit["parsed"])
                    u = Usage(calls=1, cache_hits=1)
                    self._account(doc_id, u)
                    await self._log({"agent": agent, "role": role, "doc_id": doc_id, "model": model, "cache_hit": True, "status": "ok"})
                    return LLMResult(parsed=parsed, usage=u, model=model, provider=hit.get("provider"), cache_hit=True)
                except ValidationError:
                    pass

        async with self._sems[role]:
            guard = (self.s.fallback_deadline_s, self.s.fallback_grace_s, self.s.fallback_min_tok_s) if guarded else None
            result = await self._call_with_repair(agent, role, body, schema, doc_id, guard=guard,
                                                  max_retries=0 if guarded else None)
        if self.cache is not None and self.s.llm_cache:
            self.cache.put(key, {"parsed": result.parsed.model_dump(mode="json"), "provider": result.provider})
        return result

    async def _call_with_repair(self, agent: str, role: str, body: dict, schema: type[T], doc_id: str,
                                guard: tuple[float, float, float] | None = None, max_retries: int | None = None) -> LLMResult[T]:
        total = Usage()
        t0 = time.time()
        data, text, provider = await self._post(agent, role, body, doc_id, total, guard=guard, max_retries=max_retries)
        try:
            parsed = schema.model_validate(_extract_json(text))
        except (json.JSONDecodeError, ValidationError, ValueError) as e:
            if data.get("_ctx_full"):  # cut off by the context window: a retry cannot fit more, the input must shrink
                raise ContextOverflowError(f"{agent}: answer cut off by the context window ({str(e)[:120]})") from e
            if data.get("_finish") == "length":
                body = dict(body, max_tokens=min(int(body["max_tokens"] * 1.6), 32000))
                repair_msgs = body["messages"] + [{"role": "user", "content": "Your previous answer was cut off. Answer again, more concisely, as valid JSON only."}]
            else:
                repair_msgs = body["messages"] + [
                    {"role": "assistant", "content": text[:6000]},
                    {"role": "user", "content": f"That output did not validate against the schema ({str(e)[:400]}). Return ONLY corrected JSON that matches the schema exactly."},
                ]
            body2 = dict(body, messages=repair_msgs)
            data, text, provider = await self._post(agent + ":repair", role, body2, doc_id, total, guard=guard,
                                                    max_retries=max_retries)
            try:
                parsed = schema.model_validate(_extract_json(text))
            except (json.JSONDecodeError, ValidationError, ValueError) as e2:
                raise SchemaError(f"{agent}: output failed schema validation after repair: {str(e2)[:300]}") from e2
        return LLMResult(parsed=parsed, usage=total, model=body["model"], provider=provider, latency_s=time.time() - t0)

    async def _post(self, agent: str, role: str, body: dict, doc_id: str, total: Usage,
                    guard: tuple[float, float, float] | None = None, max_retries: int | None = None) -> tuple[dict, str, str | None]:
        retries = self.s.llm_max_retries if max_retries is None else max_retries
        assert self._client is not None, "use 'async with Gateway(...)'"
        url = f"{self.s.openrouter_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self.s.openrouter_api_key or 'none'}"}
        if self.s.llm_api_flavor == "openrouter":
            headers.update({"HTTP-Referer": "https://defence-extractor.local", "X-Title": "defence-extractor"})
        attempt = 0
        relaxed = False
        grown = 0
        while True:
            attempt += 1
            if self.budget is not None:
                await self.budget.check(0.004 if role == "main" else 0.001)
            t0 = time.time()
            err = None
            try:
                if self._stream:
                    status, payload = await self._post_stream(url, body, headers, guard)
                elif guard is not None:
                    try:
                        r = await asyncio.wait_for(self._client.post(url, json=body, headers=headers), timeout=guard[0])
                    except asyncio.TimeoutError as e:
                        raise SlowModelError(f"{agent}: {body['model']} gave no answer within {guard[0]:.0f}s") from e
                else:
                    r = await self._client.post(url, json=body, headers=headers)
                    status = r.status_code
                    payload = r.json() if r.content else {}
            except (httpx.TimeoutException, httpx.TransportError, json.JSONDecodeError) as e:
                status, payload, err = 0, {}, f"{type(e).__name__}: {e}"
            latency = time.time() - t0
            if status == 200 and "choices" in payload and payload["choices"]:
                choice = payload["choices"][0]
                msg = choice.get("message") or {}
                text = msg.get("content") or ""
                usage = payload.get("usage") or {}
                u = Usage(
                    calls=1,
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    reasoning_tokens=int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0),
                    cached_tokens=int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
                    cost_usd=float(usage.get("cost") or 0.0),
                )
                total.add(u)
                provider = payload.get("provider")
                self._account(doc_id, u, provider)
                if self.budget is not None:
                    self.budget.record(u.cost_usd)
                finish = choice.get("finish_reason") or choice.get("native_finish_reason")
                await self._log({"agent": agent, "role": role, "doc_id": doc_id, "model": body["model"], "provider": provider,
                                 "prompt_tokens": u.prompt_tokens, "completion_tokens": u.completion_tokens,
                                 "reasoning_tokens": u.reasoning_tokens, "cached_tokens": u.cached_tokens,
                                 "cost_usd": u.cost_usd, "latency_s": round(latency, 2), "finish": finish, "attempt": attempt,
                                 "status": "ok"})
                if msg.get("refusal") or finish == "content_filter" or (not text.strip().startswith(("{", "`")) and _REFUSAL.match(text or "")):
                    raise RefusalError(f"{agent}: model refused ({finish}): {(msg.get('refusal') or text)[:200]}")
                ctx_full = bool(self.s.context_window) and finish == "length" and (
                    u.prompt_tokens + u.completion_tokens >= self.s.context_window - 32)
                if not text.strip():
                    err = f"empty content (finish={finish})"
                    if ctx_full:
                        raise ContextOverflowError(f"{agent}: {err}: context window full "
                                                   f"({u.prompt_tokens} + {u.completion_tokens} tokens)")
                    if finish == "length":  # reasoning used the whole budget: the same request would fail the same way
                        if grown >= 2 or attempt > retries or body.get("max_tokens", 0) >= self.s.max_tokens_ceiling:
                            raise LLMError(f"{agent}: {err} at max_tokens={body.get('max_tokens')}")
                        grown += 1
                        body = dict(body, max_tokens=min(self.s.max_tokens_ceiling, int(body["max_tokens"]) * 2))
                        continue
                    if attempt <= retries:
                        await asyncio.sleep(1.5 * attempt)
                        continue
                    raise LLMError(f"{agent}: {err}")
                return {"_finish": finish, "_ctx_full": ctx_full}, text, provider
            # ---- errors ------------------------------------------------------------------------
            emsg = err or json.dumps(payload.get("error") or payload)[:500]
            await self._log({"agent": agent, "role": role, "doc_id": doc_id, "model": body["model"], "status": f"http_{status}",
                             "error": emsg, "attempt": attempt, "latency_s": round(latency, 2)})
            if status in (400, 404) and not relaxed and ("endpoint" in emsg.lower() or "provider" in emsg.lower() or "quantiz" in emsg.lower()):
                relaxed = True  # no pinned provider can serve the request: relax pinning once (logged)
                body = dict(body, provider={"require_parameters": True, "allow_fallbacks": True})
                continue
            if status in (401, 402, 403):
                raise BudgetExceeded(f"{agent}: OpenRouter refused the key ({status}): {emsg}")
            if status == 400:
                raise LLMError(f"{agent}: bad request: {emsg}")
            if attempt > retries:
                raise LLMError(f"{agent}: failed after {attempt} attempts: {emsg}")
            await asyncio.sleep(min(30.0, (2 ** attempt) + random.random()))

    @property
    def _stream(self) -> bool:
        return self.s.llm_stream if self.s.llm_stream is not None else self.s.llm_api_flavor != "openrouter"

    async def _post_stream(self, url: str, body: dict, headers: dict,
                           guard: tuple[float, float, float] | None = None) -> tuple[int, dict]:
        if guard is None:
            return await self._stream_once(url, body, headers, None)
        try:
            return await asyncio.wait_for(self._stream_once(url, body, headers, guard), timeout=guard[0])
        except asyncio.TimeoutError as e:
            raise SlowModelError(f"{body['model']} did not finish within {guard[0]:.0f}s") from e

    async def _stream_once(self, url: str, body: dict, headers: dict,
                           guard: tuple[float, float, float] | None) -> tuple[int, dict]:
        """Streamed chat completion, returned in the non-streamed response shape. Self-hosted endpoints often sit behind
        a proxy with a short request timeout (95 s on the GPU farm) that a long JSON answer would exceed; a stream keeps
        the connection alive token by token."""
        assert self._client is not None
        content: list[str] = []
        reasoning: list[str] = []
        finish = None
        usage: dict = {}
        sbody = dict(body, stream=True, stream_options={"include_usage": True})
        t0, pieces = time.time(), 0
        async with self._client.stream("POST", url, json=sbody, headers=headers) as r:
            if r.status_code != 200:
                raw = await r.aread()
                try:
                    return r.status_code, json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    return r.status_code, {"error": raw[:500].decode("utf-8", "replace")}
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if chunk.get("error"):
                    return 500, {"error": chunk["error"]}
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for ch in chunk.get("choices") or []:
                    delta = ch.get("delta") or {}
                    if delta.get("content"):
                        content.append(delta["content"])
                    if delta.get("reasoning_content") or delta.get("reasoning"):
                        reasoning.append(delta.get("reasoning_content") or delta.get("reasoning"))
                    finish = ch.get("finish_reason") or finish
                    pieces += 1
                if guard is not None:  # too slow to be useful: give the call to the fallback model
                    elapsed = time.time() - t0
                    if elapsed > guard[1] and pieces / elapsed < guard[2]:
                        raise SlowModelError(f"{body['model']} streams {pieces / elapsed:.1f} tokens/s after {elapsed:.0f}s")
        return 200, {"choices": [{"message": {"content": "".join(content), "reasoning_content": "".join(reasoning)},
                                  "finish_reason": finish}], "usage": usage}

    async def _log(self, rec: dict) -> None:
        rec["ts"] = round(time.time(), 3)
        async with self._log_lock:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
