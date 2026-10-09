"""Temporal workflows: one DocumentWorkflow per document; BatchWorkflow runs documents with a sliding window.

This module deliberately imports nothing from the package: Temporal re-imports workflow code inside a deterministic
sandbox, so activities are referenced by name and the stage list is defined here (a unit test keeps it in sync with
pipeline.state.STAGES).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ChildWorkflowError

PIPELINE_STAGES = ["ingest", "vision", "page_map", "entities", "roles", "references", "product_map", "discovery",
                   "attribution", "known_hunt", "mapping", "structuring", "audit", "anomaly", "verification"]
_RETRY = RetryPolicy(initial_interval=timedelta(seconds=10), backoff_coefficient=2.0, maximum_attempts=3,
                     non_retryable_error_types=["BudgetExceeded"])


@workflow.defn
class DocumentWorkflow:
    @workflow.run
    async def run(self, args: dict) -> dict:
        doc_id = args["ref"]["doc_id"]
        for stage in PIPELINE_STAGES:
            try:
                res = await workflow.execute_activity(
                    "run_stage_activity", dict(args, stage=stage),
                    start_to_close_timeout=timedelta(minutes=150), heartbeat_timeout=timedelta(minutes=3),
                    retry_policy=_RETRY)
            except ActivityError as e:
                return {"doc_id": doc_id, "status": "budget_exhausted" if "Budget" in str(e.cause) else "failed",
                        "stage": stage, "error": str(e.cause)[:300]}
            if res.get("abort"):
                return {"doc_id": doc_id, "status": res.get("status", "failed"), "stage": stage, "error": res.get("error")}
        return await workflow.execute_activity("assemble_activity", args, start_to_close_timeout=timedelta(minutes=10),
                                               retry_policy=_RETRY)


@workflow.defn
class BatchWorkflow:
    @workflow.run
    async def run(self, args: dict) -> dict:
        run_id, refs, conc = args["run_id"], args["refs"], int(args.get("concurrency", 6))
        results: list[dict] = []
        pending: list = []
        queue = list(refs)
        stop = False

        async def child(ref: dict) -> dict:
            try:
                return await workflow.execute_child_workflow(
                    DocumentWorkflow.run, {"run_id": run_id, "ref": ref, "budget_cap": args.get("budget_cap")},
                    id=f"{run_id}--{ref['doc_id']}", task_queue=args["task_queue"])
            except ChildWorkflowError as e:
                return {"doc_id": ref["doc_id"], "status": "failed", "error": str(e.cause)[:300]}

        while queue or pending:
            while queue and len(pending) < conc and not stop:
                pending.append(asyncio.ensure_future(child(queue.pop(0))))
            if not pending:
                break
            done, pending = await workflow.wait(pending, return_when=asyncio.FIRST_COMPLETED)  # deterministic
            pending = list(pending)
            for d in done:
                r = d.result()
                results.append(r)
                if r.get("status") == "budget_exhausted":
                    stop = True  # finish running documents, start no new ones
            if stop:
                queue.clear()
        return {"run_id": run_id, "documents": len(refs),
                "completed": sum(1 for r in results if r.get("status") == "completed"), "results": results}
