"""Streamed responses from self-hosted endpoints (behind a proxy timeout) are reassembled like a normal completion."""

import asyncio
import json

import httpx
from pydantic import BaseModel

from defence_extractor.config import Settings
from defence_extractor.llm.gateway import Gateway


class Out(BaseModel):
    facts: list[str]


def test_streamed_completion_is_reassembled(tmp_path):
    chunks = [
        {"choices": [{"delta": {"reasoning_content": "the range is given "}}]},
        {"choices": [{"delta": {"content": '{"facts": ['}}]},
        {"choices": [{"delta": {"content": '"120 km"]}'}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 7}},
    ]
    sse = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    gw = Gateway(Settings(llm_api_flavor="openai", llm_cache=False), tmp_path / "calls.jsonl")

    async def go():
        gw._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await gw.call(agent="T", role="main", system="s", user="u", schema=Out, doc_id="D")
        finally:
            await gw._client.aclose()

    res = asyncio.run(go())
    assert res.parsed.facts == ["120 km"]
    assert gw.by_doc["D"].completion_tokens == 7


def _sse(text: str) -> str:
    chunks = [{"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]},
              {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}]
    return "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"


def _gateway(tmp_path, handler, **kw):
    s = Settings(llm_api_flavor="openai", llm_cache=False, verifier_model="big-72b", verifier_fallback_model="small-27b", **kw)
    gw = Gateway(s, tmp_path / "calls.jsonl")
    gw._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return gw


def test_verifier_falls_back_when_the_primary_fails(tmp_path):
    seen = []

    def handler(request):
        model = json.loads(request.content)["model"]
        seen.append(model)
        if model == "big-72b":
            return httpx.Response(429, json={"error": {"message": "No deployments available"}})
        return httpx.Response(200, text=_sse('{"facts": ["ok"]}'), headers={"content-type": "text/event-stream"})

    gw = _gateway(tmp_path, handler, fallback_after_failures=2, llm_max_retries=0)

    async def go():
        try:
            out = []
            for _ in range(3):
                out.append(await gw.call(agent="A12", role="verifier", system="s", user="u", schema=Out, doc_id="D"))
            return out
        finally:
            await gw._client.aclose()

    res = asyncio.run(go())
    assert all(r.parsed.facts == ["ok"] and r.model == "small-27b" for r in res)
    # two failed tries of the primary open the breaker: the third call goes straight to the fallback
    assert seen.count("big-72b") == 2 and seen.count("small-27b") == 3


def test_too_slow_primary_stream_moves_to_the_fallback(tmp_path):
    def handler(request):
        model = json.loads(request.content)["model"]
        return httpx.Response(200, text=_sse('{"facts": ["%s"]}' % model), headers={"content-type": "text/event-stream"})

    # every stream counts as too slow: grace 0 s and a 10,000 tokens/s minimum
    gw = _gateway(tmp_path, handler, fallback_grace_s=0.0, fallback_min_tok_s=10000.0)

    async def go():
        try:
            return await gw.call(agent="A10", role="verifier", system="s", user="u", schema=Out, doc_id="D")
        finally:
            await gw._client.aclose()

    assert asyncio.run(go()).parsed.facts == ["small-27b"]


def test_an_answer_lost_to_reasoning_is_retried_with_a_bigger_budget(tmp_path):
    budgets = []

    def handler(request):
        body = json.loads(request.content)
        budgets.append(body["max_tokens"])
        if body["max_tokens"] < 20000:  # the model thinks until the limit and never answers
            chunks = [{"choices": [{"delta": {"reasoning_content": "thinking..."}, "finish_reason": "length"}]}]
            sse = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, text=_sse('{"facts": ["ok"]}'), headers={"content-type": "text/event-stream"})

    gw = _gateway(tmp_path, handler, thinking_overhead_tokens=2000, llm_max_retries=4)

    async def go():
        try:
            return await gw.call(agent="A2", role="main", system="s", user="u", schema=Out, doc_id="D", max_tokens=4000)
        finally:
            await gw._client.aclose()

    res = asyncio.run(go())
    assert res.parsed.facts == ["ok"]
    assert budgets == [6000, 12000, 24000]  # overhead added for "reasoning off", then doubled twice


def test_a_prompt_too_large_for_the_context_window_is_refused_before_sending(tmp_path):
    from defence_extractor.llm.gateway import ContextOverflowError, SchemaError

    sent = []

    def handler(request):
        sent.append(json.loads(request.content)["max_tokens"])
        return httpx.Response(200, text=_sse('{"facts": ["ok"]}'), headers={"content-type": "text/event-stream"})

    gw = _gateway(tmp_path, handler, context_window=8192)

    async def go(user):
        try:
            return await gw.call(agent="A5B", role="main", system="s", user=user, schema=Out, doc_id="D", max_tokens=12000)
        except SchemaError as e:
            return e

    assert isinstance(asyncio.run(go("x" * 21000)), ContextOverflowError)  # ~7k tokens: no room to answer
    res = asyncio.run(go("x" * 9000))  # ~3k tokens: the answer gets what is left of the window
    asyncio.run(gw._client.aclose())
    assert res.parsed.facts == ["ok"] and sent == [8192 - (3000 + 64)]  # 9,001 chars / 3 + 64 tokens of prompt



def test_with_forced_thinking_a_call_needs_room_for_the_reasoning_too(tmp_path):
    from defence_extractor.llm.gateway import ContextOverflowError, SchemaError

    def handler(request):
        return httpx.Response(200, text=_sse('{"facts": ["ok"]}'), headers={"content-type": "text/event-stream"})

    gw = _gateway(tmp_path, handler, context_window=8192, thinking_overhead_tokens=6000)

    async def go(user):
        try:
            return await gw.call(agent="A4", role="main", system="s", user=user, schema=Out, doc_id="D", max_tokens=8000)
        except SchemaError as e:
            return e
        finally:
            await gw._client.aclose()

    # ~6,900 prompt tokens leave ~1,300: enough for an answer, not for the reasoning before it
    assert isinstance(asyncio.run(go("x" * 20500)), ContextOverflowError)



def test_digits_count_as_one_token_each():
    from defence_extractor.llm.gateway import prompt_tokens

    assert prompt_tokens("x" * 300) == 100
    assert prompt_tokens("1,838,200") == 7  # seven digits at one token each; the commas at the text rate
    assert prompt_tokens("9" * 300) == 300



def test_a_call_that_reasons_asks_the_proxy_for_reasoning(tmp_path):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, text=_sse('{"facts": ["ok"]}'), headers={"content-type": "text/event-stream"})

    gw = _gateway(tmp_path, handler)

    async def go():
        try:
            await gw.call(agent="A3", role="main", system="s", user="u", schema=Out, doc_id="D", reasoning="off")
            await gw.call(agent="A3:retry_thinking", role="main", system="s", user="u2", schema=Out, doc_id="D", reasoning="low")
        finally:
            await gw._client.aclose()

    asyncio.run(go())
    assert "reasoning_effort" not in sent[0] and sent[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert sent[1]["reasoning_effort"] == "low" and sent[1]["chat_template_kwargs"] == {"enable_thinking": True}

