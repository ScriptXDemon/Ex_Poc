"""Temporal activities: thin wrappers around the pipeline stage functions (state lives on disk, payloads are ids)."""

from __future__ import annotations

import asyncio
import logging

from temporalio import activity
from temporalio.exceptions import ApplicationError

from ..config import get_settings
from ..ingest.corpus import DocRef
from ..llm import BudgetExceeded
from ..pipeline.runner import assemble, build_runtime, run_stage
from ..pipeline.state import DocState

log = logging.getLogger(__name__)
CRITICAL = {"ingest", "page_map", "entities", "roles", "discovery"}
_RUNTIMES: dict[str, object] = {}
_LOCK = asyncio.Lock()


async def runtime_for(run_id: str, budget_cap: float | None = None):
    async with _LOCK:
        rt = _RUNTIMES.get(run_id)
        if rt is None:
            rt = build_runtime(get_settings(), run_id, budget_cap)
            await rt.gw.__aenter__()
            await rt.gw.budget.refresh()
            _RUNTIMES[run_id] = rt
        return rt


def _state(args: dict) -> DocState:
    s = get_settings()
    ref = DocRef.model_validate(args["ref"])
    return DocState.load(s.runs_dir, args["run_id"], ref.doc_id) or DocState(run_id=args["run_id"], ref=ref)


async def _heartbeat(every_s: float = 20.0) -> None:
    """Tell Temporal this activity is alive while a long stage runs (a 300-page PDF, a big catalogue), so a worker
    that dies is noticed within the heartbeat timeout instead of the start-to-close timeout."""
    while True:
        try:
            activity.heartbeat()
        except Exception:
            pass
        await asyncio.sleep(every_s)


@activity.defn
async def run_stage_activity(args: dict) -> dict:
    hb = asyncio.create_task(_heartbeat())
    try:
        return await _run_stage_activity(args)
    finally:
        hb.cancel()


async def _run_stage_activity(args: dict) -> dict:
    s = get_settings()
    rt = await runtime_for(args["run_id"], args.get("budget_cap"))
    st = _state(args)
    stage = args["stage"]
    if st.status in ("failed", "budget_exhausted") and stage not in st.done:
        return {"stage": stage, "abort": True, "status": st.status}
    try:
        await run_stage(rt, st, stage, s.runs_dir)
    except BudgetExceeded as e:
        st.status = "budget_exhausted"
        st.errors.append(f"{stage}: {e}")
        st.save(s.runs_dir)
        raise ApplicationError(str(e), type="BudgetExceeded", non_retryable=True)
    except Exception as e:
        if stage in CRITICAL:
            if activity.info().attempt >= 3:
                st.status = "failed"
                st.errors.append(f"{stage}: {type(e).__name__}: {str(e)[:300]}")
                st.save(s.runs_dir)
                return {"stage": stage, "abort": True, "status": "failed", "error": str(e)[:300]}
            raise  # Temporal retries the activity
        st.errors.append(f"{stage}: {type(e).__name__}: {str(e)[:300]}")
        st.done.append(stage)
        st.save(s.runs_dir)
    return {"stage": stage, "abort": False}


@activity.defn
async def assemble_activity(args: dict) -> dict:
    s = get_settings()
    rt = await runtime_for(args["run_id"], args.get("budget_cap"))
    st = _state(args)
    return await assemble(rt, st, s.runs_dir)
