"""Budget guard: tracks spend from per-call usage and the OpenRouter key's live balance; stops before overspending."""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

log = logging.getLogger(__name__)


class BudgetExceeded(RuntimeError):
    pass


class BudgetGuard:
    def __init__(self, base_url: str, api_key: str, reserve_usd: float = 0.75, run_cap_usd: float | None = None,
                 refresh_every_s: float = 45.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.reserve = reserve_usd
        self.run_cap = run_cap_usd
        self.refresh_every = refresh_every_s
        self.run_spent = 0.0
        self._spent_since_refresh = 0.0
        self._remaining: float | None = None
        self._limit: float | None = None
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def refresh(self) -> None:
        if not self.api_key or "openrouter.ai" not in self.base_url:
            self._last = time.time()  # self-hosted endpoint: no dollar balance to track
            return
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(f"{self.base_url}/key", headers={"Authorization": f"Bearer {self.api_key}"})
                r.raise_for_status()
                d = r.json().get("data", {})
            self._limit = d.get("limit")
            rem = d.get("limit_remaining")
            if rem is None and self._limit is not None:
                rem = float(self._limit) - float(d.get("usage", 0.0))
            self._remaining = float(rem) if rem is not None else None
            self._spent_since_refresh = 0.0
            self._last = time.time()
        except Exception as e:  # never block extraction because the balance endpoint hiccuped
            log.warning("budget refresh failed: %s", e)
            self._last = time.time()

    @property
    def remaining(self) -> float | None:
        if self._remaining is None:
            return None
        return self._remaining - self._spent_since_refresh

    async def check(self, estimate_usd: float = 0.002) -> None:
        async with self._lock:
            if self._remaining is None or time.time() - self._last > self.refresh_every or self._spent_since_refresh > 0.5:
                await self.refresh()
            rem = self.remaining
            if rem is not None and rem - self.reserve < estimate_usd:
                raise BudgetExceeded(f"OpenRouter balance ${rem:.2f} is at the reserve (${self.reserve:.2f}); stopping")
            if self.run_cap is not None and self.run_spent + estimate_usd > self.run_cap:
                raise BudgetExceeded(f"run spend cap ${self.run_cap:.2f} reached (spent ${self.run_spent:.2f})")

    def record(self, cost_usd: float) -> None:
        self.run_spent += cost_usd
        self._spent_since_refresh += cost_usd

    def snapshot(self) -> dict:
        return {"run_spent_usd": round(self.run_spent, 4), "remaining_usd": None if self.remaining is None else round(self.remaining, 4),
                "limit_usd": self._limit}
