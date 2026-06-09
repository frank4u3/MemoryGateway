"""Baseline collection service for cost/usage measurement.

Snapshots daily telemetry and provides aggregation for ROI reporting.
Enables running the gateway in pass-through mode to collect a week of
baseline data before enabling caching/optimization features.
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from typing import Optional

from gateway.logger import get_logger

logger = get_logger()

BASELINE_KEY = "baseline:snapshots"
FINALIZED_KEY = "baseline:final"
FINALIZED_INDEX_KEY = "baseline:final:index"


class BaselineService:
    """Tracks daily usage snapshots and computes baseline metrics."""

    def __init__(self, redis_client, stats):
        self._redis = redis_client
        self._stats = stats

    async def snapshot_today(self) -> dict | None:
        """Snapshot today's cumulative stats into a stored baseline record."""
        if self._redis is None:
            return None
        today = datetime.now(dt_timezone.utc).strftime("%Y-%m-%d")
        snapshot = await self._stats.get_daily_baseline(today)

        import json
        await self._redis.hset(BASELINE_KEY, today, json.dumps(snapshot))
        logger.info(
            "baseline_snapshot_stored",
            extra={
                "date": today,
                "requests": snapshot.get("requests", 0),
                "total_tokens": snapshot.get("total_tokens", 0),
                "cost_spent_usd": snapshot.get("cost_spent_usd", 0),
            },
        )
        return snapshot

    async def get_baseline(self, date_str: str | None = None) -> dict | None:
        """Get stored baseline for a specific date (defaults to today live)."""
        if self._redis is None:
            return None
        if date_str is None:
            return await self._stats.get_daily_baseline()

        import json
        raw = await self._redis.hget(BASELINE_KEY, date_str)
        if raw:
            return json.loads(raw)
        return None

    async def get_baseline_range(
        self, days: int = 7
    ) -> list[dict]:
        """Get stored baselines for the last N days."""
        if self._redis is None:
            return []

        import json
        raw = await self._redis.hgetall(BASELINE_KEY)
        if not raw:
            snapshots = {}
        else:
            snapshots = {k: json.loads(v) for k, v in raw.items()}

        # Generate date keys for last N days
        result = []
        now = datetime.now(dt_timezone.utc)
        for i in range(days - 1, -1, -1):
            ts = now.timestamp() - i * 86400
            date_str = datetime.fromtimestamp(ts, tz=dt_timezone.utc).strftime("%Y-%m-%d")
            if date_str in snapshots:
                result.append(snapshots[date_str])
        return result

    async def export_baseline(
        self, days: int = 7
    ) -> dict:
        """Export baseline data as a JSON-serializable dict for ROI analysis."""
        today_live = await self._stats.get_daily_baseline()
        stored = await self.get_baseline_range(days)

        all_days = stored[:]
        today_str = datetime.now(dt_timezone.utc).strftime("%Y-%m-%d")
        if today_live and not any(d.get("date") == today_str for d in all_days):
            all_days.append(today_live)

        totals = self._aggregate(all_days)
        daily_breakdown = self._daily_breakdown(all_days)

        return {
            "exported_at": datetime.now(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "days_collected": len(all_days),
            "totals": totals,
            "daily_breakdown": daily_breakdown,
            "projected_monthly": self._project_monthly(totals),
        }

    @staticmethod
    def _aggregate(days: list[dict]) -> dict:
        total_request = 0
        total_tokens = 0
        total_prompt = 0
        total_completion = 0
        total_cost = 0.0
        total_hits = 0
        total_misses = 0
        total_saved = 0

        agents: dict[str, dict] = {}

        for day in days:
            total_request += day.get("requests", 0)
            total_prompt += day.get("tokens_prompt", 0)
            total_completion += day.get("tokens_completion", 0)
            total_tokens += day.get("total_tokens", 0)
            total_cost += day.get("net_cost_usd", 0)
            total_hits += day.get("hits", 0)
            total_misses += day.get("misses", 0)
            total_saved += day.get("tokens_saved", 0)

            for agent_id, adata in day.get("agents", {}).items():
                if agent_id not in agents:
                    agents[agent_id] = {
                        "tokens_prompt": 0, "tokens_completion": 0,
                        "total_tokens": 0, "cost_spent_usd": 0.0, "cost_saved_usd": 0.0,
                    }
                agents[agent_id]["tokens_prompt"] += adata.get("tokens_prompt", 0)
                agents[agent_id]["tokens_completion"] += adata.get("tokens_completion", 0)
                agents[agent_id]["total_tokens"] += adata.get("total_tokens", 0)
                agents[agent_id]["cost_spent_usd"] += adata.get("cost_spent_usd", 0)
                agents[agent_id]["cost_saved_usd"] += adata.get("cost_saved_usd", 0)

        hit_rate = round(total_hits / total_request * 100, 1) if total_request > 0 else 0.0

        return {
            "requests": total_request,
            "hits": total_hits,
            "misses": total_misses,
            "hit_rate_pct": hit_rate,
            "tokens_prompt": total_prompt,
            "tokens_completion": total_completion,
            "total_tokens": total_tokens,
            "tokens_saved": total_saved,
            "net_cost_usd": round(total_cost, 8),
            "agents": agents,
        }

    @staticmethod
    def _daily_breakdown(days: list[dict]) -> list[dict]:
        return [
            {
                "date": d.get("date", ""),
                "requests": d.get("requests", 0),
                "tokens_prompt": d.get("tokens_prompt", 0),
                "tokens_completion": d.get("tokens_completion", 0),
                "total_tokens": d.get("total_tokens", 0),
                "cost_spent_usd": d.get("cost_spent_usd", 0),
                "hits": d.get("hits", 0),
            }
            for d in days
        ]

    @staticmethod
    def _project_monthly(totals: dict) -> dict:
        days = max(1, totals.get("requests", 0) * 0 + 1)
        scale = 30 / max(1, days) if days > 0 else 30
        return {
            "requests": int(totals.get("requests", 0) * scale),
            "total_tokens": int(totals.get("total_tokens", 0) * scale),
            "net_cost_usd": round(totals.get("net_cost_usd", 0) * scale, 2),
        }

    async def finalize(self, baseline_id: str) -> dict | None:
        """Freeze all current daily snapshots as a permanent named baseline.

        Aggregates every daily snapshot currently in Redis into a single
        frozen record stored under `baseline:final:{baseline_id}`.
        Returns the aggregate summary so callers can immediately see:
        { baseline_id, total_tokens, cost_usd }.

        Use this at the end of Week 1 (baseline collection) so that
        Week 2-4 optimizations have a permanent reference point.
        """
        if self._redis is None:
            return None

        import json

        raw = await self._redis.hgetall(BASELINE_KEY)
        if not raw:
            logger.warning("baseline_finalize_no_data",
                           extra={"baseline_id": baseline_id})
            return None

        all_days = [json.loads(v) for v in raw.values()]
        totals = self._aggregate(all_days)
        daily = self._daily_breakdown(all_days)

        finalized = {
            "baseline_id": baseline_id,
            "frozen_at": datetime.now(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "days_collected": len(all_days),
            "date_range": {
                "start": all_days[0].get("date", "") if all_days else "",
                "end": all_days[-1].get("date", "") if all_days else "",
            },
            "totals": totals,
            "daily_breakdown": daily,
            "projected_monthly": self._project_monthly(totals),
        }

        key = f"{FINALIZED_KEY}:{baseline_id}"
        await self._redis.set(key, json.dumps(finalized))
        await self._redis.sadd(FINALIZED_INDEX_KEY, baseline_id)

        logger.info(
            "baseline_finalized",
            extra={
                "baseline_id": baseline_id,
                "total_tokens": totals["total_tokens"],
                "net_cost_usd": totals["net_cost_usd"],
            },
        )

        return {
            "baseline_id": baseline_id,
            "total_tokens": totals["total_tokens"],
            "cost_usd": totals["net_cost_usd"],
        }

    async def get_finalized(self, baseline_id: str) -> dict | None:
        """Retrieve a previously finalized baseline by ID."""
        if self._redis is None:
            return None

        import json
        key = f"{FINALIZED_KEY}:{baseline_id}"
        raw = await self._redis.get(key)
        if raw:
            return json.loads(raw)
        return None

    async def list_finalized(self) -> list[str]:
        """List all finalized baseline IDs."""
        if self._redis is None:
            return []
        members = await self._redis.smembers(FINALIZED_INDEX_KEY)
        return sorted(members)

    async def compare_to_finalized(
        self, baseline_id: str
    ) -> dict | None:
        """Compare current live stats against a frozen baseline.

        Returns a comparison dict with absolute deltas and percentage changes
        for tokens and cost — the business metrics that matter.
        """
        if self._redis is None:
            return None

        frozen = await self.get_finalized(baseline_id)
        if frozen is None:
            return None

        today = await self._stats.get_daily_baseline()

        f_totals = frozen.get("totals", {})
        f_days = frozen.get("days_collected", 1)
        f_tokens = f_totals.get("total_tokens", 0)
        f_cost = f_totals.get("net_cost_usd", 0)

        t_tokens = today.get("total_tokens", 0)
        t_cost = today.get("net_cost_usd", 0)

        token_delta = t_tokens - (f_tokens / f_days if f_days > 0 else 0)
        cost_delta = t_cost - (f_cost / f_days if f_days > 0 else 0)

        return {
            "baseline_id": baseline_id,
            "frozen": {
                "total_tokens": f_tokens,
                "cost_usd": round(f_cost, 8),
                "days": f_days,
            },
            "current": {
                "total_tokens": t_tokens,
                "cost_usd": round(t_cost, 8),
            },
            "delta": {
                "tokens": round(token_delta, 0),
                "cost_usd": round(cost_delta, 8),
            },
        }
