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
