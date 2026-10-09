"""Temporal worker + client helpers."""

from __future__ import annotations

from temporalio.client import Client
from temporalio.worker import Worker

from ..config import get_settings
from .activities import assemble_activity, run_stage_activity
from .workflows import BatchWorkflow, DocumentWorkflow

TASK_QUEUE = "dx-pipeline"


async def run_worker(max_activities: int = 24) -> None:
    s = get_settings()
    client = await Client.connect(s.temporal_address, namespace=s.temporal_namespace)
    worker = Worker(client, task_queue=TASK_QUEUE, workflows=[DocumentWorkflow, BatchWorkflow],
                    activities=[run_stage_activity, assemble_activity], max_concurrent_activities=max_activities)
    await worker.run()


async def submit_batch(run_id: str, refs: list[dict], concurrency: int, budget_cap: float | None, wait: bool = True) -> dict:
    s = get_settings()
    client = await Client.connect(s.temporal_address, namespace=s.temporal_namespace)
    args = {"run_id": run_id, "refs": refs, "concurrency": concurrency, "budget_cap": budget_cap, "task_queue": TASK_QUEUE}
    handle = await client.start_workflow(BatchWorkflow.run, args, id=f"batch--{run_id}", task_queue=TASK_QUEUE)
    if not wait:
        return {"workflow_id": handle.id}
    return await handle.result()
