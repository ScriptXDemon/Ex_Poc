"""Temporal orchestration (durable, resumable, per-stage retries). See service.py for worker/client helpers.

Kept import-free on purpose: Temporal's workflow sandbox imports this package when it loads workflows.py.
"""
