"""Telemetry service that wraps StatsTracker and CostCalculator for per-agent metrics."""

from gateway.logger import get_logger
from gateway.metrics.cost import CostCalculator
from gateway.stats import StatsTracker
from gateway.telemetry.schemas import (
    AgentTelemetry,
    RankingEntry,
    TelemetryOverview,
)

logger = get_logger()

KNOWN_AGENTS = ["hermes", "opencode", "qoder", "vscode"]


class TelemetryService:
    """Aggregates per-agent telemetry from StatsTracker and CostCalculator.

    Provides ranking methods and overview summaries for the telemetry API.
    """

    def __init__(self, stats: StatsTracker, cost_calculator: CostCalculator):
        self.stats = stats
        self.cost_calculator = cost_calculator

    async def get_overall_summary(self) -> TelemetryOverview:
        """Return a full telemetry overview with per-agent data and rankings."""
        overall = await self.stats.get_stats()
        agent_data = await self._get_agent_details()

        # Build rankings sorted by composite score
        rankings = self._build_rankings(agent_data)

        total_requests = overall.get("total_requests", 0)
        total_hits = overall.get("hits", 0)
        total_misses = overall.get("misses", 0)
        total_prompt = overall.get("tokens_prompt", 0)
        total_completion = overall.get("tokens_completion", 0)
        total_saved = overall.get("tokens_saved", 0)
        total_spent = overall.get("cost_spent_usd", 0.0)
        total_cost_saved = overall.get("cost_saved_usd", 0.0)
        avg_latency = overall.get("avg_latency_ms", 0.0)

        return TelemetryOverview(
            total_requests=total_requests,
            total_hits=total_hits,
            total_misses=total_misses,
            hit_rate_pct=overall.get("hit_rate_pct", 0.0),
            total_prompt_tokens=total_prompt,
            total_completion_tokens=total_completion,
            total_tokens_saved=total_saved,
            total_cost_spent_usd=round(total_spent, 6),
            total_cost_saved_usd=round(total_cost_saved, 6),
            total_net_cost_usd=round(total_cost_saved - total_spent, 6),
            avg_latency_ms=avg_latency,
            agents=agent_data,
            rankings=rankings,
        )

    async def get_agent_rankings(
        self, sort_by: str = "composite"
    ) -> list[RankingEntry]:
        """Return agents ranked by a given criterion.

        Supported sort_by values:
          - composite:       custom efficiency score (default)
          - token_consumption: most tokens used first
          - cache_efficiency: highest hit rate first
          - cost:            highest cost saved first
        """
        agent_data = await self._get_agent_details()
        if sort_by == "token_consumption":
            agent_data.sort(
                key=lambda a: a.total_tokens, reverse=True
            )
            return self._build_rankings(agent_data, sort_by=sort_by)
        if sort_by == "cache_efficiency":
            agent_data.sort(
                key=lambda a: a.hit_rate_pct, reverse=True
            )
            return self._build_rankings(agent_data, sort_by=sort_by)
        if sort_by == "cost":
            agent_data.sort(
                key=lambda a: a.cost_saved_usd, reverse=True
            )
            return self._build_rankings(agent_data, sort_by=sort_by)
        # Default: composite score
        return self._build_rankings(agent_data, sort_by="composite")

    async def get_agent_detail(self, agent_id: str) -> AgentTelemetry:
        """Return detailed telemetry for a single agent."""
        details = await self._get_single_agent_detail(agent_id)
        return details

    async def _get_agent_details(self) -> list[AgentTelemetry]:
        """Fetch detailed metrics for all known agents."""
        results = []
        for agent_id in KNOWN_AGENTS:
            try:
                detail = await self._get_single_agent_detail(agent_id)
                results.append(detail)
            except Exception as exc:
                logger.warning(
                    "telemetry_agent_detail_error",
                    extra={"agent_id": agent_id, "error": str(exc)},
                )
        return results

    async def _get_single_agent_detail(
        self, agent_id: str
    ) -> AgentTelemetry:
        """Get per-agent metrics from Redis stats keys."""
        import time
        from datetime import datetime as dt, timezone as dt_timezone

        redis = self.stats.redis

        hits_key = f"stats:agent:{agent_id}:hits"
        misses_key = f"stats:agent:{agent_id}:misses"
        requests_key = f"stats:agent:{agent_id}:requests"

        hits = int(await redis.get(hits_key) or 0)
        misses = int(await redis.get(misses_key) or 0)
        requests = int(await redis.get(requests_key) or 0)
        total_cache_ops = hits + misses
        hit_rate = (
            round(hits / total_cache_ops * 100, 1)
            if total_cache_ops > 0
            else 0.0
        )

        # We don't track per-agent tokens/cost in Redis yet, so derive from
        # overall stats proportionally by request share
        overall = await self.stats.get_stats()
        total_requests = overall.get("total_requests", 0)

        if total_requests > 0 and requests > 0:
            share = requests / total_requests
            prompt_tokens = int(overall.get("tokens_prompt", 0) * share)
            completion_tokens = int(overall.get("tokens_completion", 0) * share)
            tokens_saved = int(overall.get("tokens_saved", 0) * share)
            cost_spent = overall.get("cost_spent_usd", 0.0) * share
            cost_saved = overall.get("cost_saved_usd", 0.0) * share
        else:
            prompt_tokens = 0
            completion_tokens = 0
            tokens_saved = 0
            cost_spent = 0.0
            cost_saved = 0.0

        # Per-agent latency: we store per-request latency in a daily list keyed
        # by date, but not per-agent. Use overall avg as fallback.
        avg_latency = overall.get("avg_latency_ms", 0.0)

        total_tokens = prompt_tokens + completion_tokens

        return AgentTelemetry(
            agent_id=agent_id,
            requests=requests,
            hits=hits,
            misses=misses,
            hit_rate_pct=hit_rate,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            tokens_saved=tokens_saved,
            cost_spent_usd=round(cost_spent, 6),
            cost_saved_usd=round(cost_saved, 6),
            net_cost_usd=round(cost_saved - cost_spent, 6),
            avg_latency_ms=avg_latency,
        )

    def _build_rankings(
        self,
        agent_data: list[AgentTelemetry],
        sort_by: str = "composite",
    ) -> list[RankingEntry]:
        """Rank agents by a composite efficiency score or a specific criterion.

        Score factors (composite):
          - hit_rate_pct (0-100): weight 3
          - cost_saved_usd (normalized per-request): weight 2
          - tokens_saved (normalized per-request): weight 1
          - low latency bonus: weight 1 (inverse)
        """
        def _compute_score(agent: AgentTelemetry) -> float:
            if agent.requests == 0:
                return 0.0

            if sort_by == "token_consumption":
                return round(float(agent.total_tokens), 2)
            if sort_by == "cache_efficiency":
                return round(agent.hit_rate_pct, 2)
            if sort_by == "cost":
                return round(agent.cost_saved_usd, 6)

            # Composite score (0-100 scale)
            saved_per_req = agent.tokens_saved / agent.requests
            cost_saved_per_req = agent.cost_saved_usd / agent.requests
            latency_bonus = max(0, 1.0 - (agent.avg_latency_ms / 5000.0))
            score = (
                (agent.hit_rate_pct * 3.0)
                + (min(cost_saved_per_req * 1000, 100) * 2.0)
                + (min(saved_per_req / 100, 100) * 1.0)
                + (latency_bonus * 100 * 1.0)
            ) / 7.0
            return round(score, 2)

        scored = [
            (agent, agent.hit_rate_pct, _compute_score(agent))
            for agent in agent_data
        ]

        # Sort by score descending
        scored.sort(key=lambda x: x[2], reverse=True)

        rankings = []
        for rank, (agent, _, score) in enumerate(scored, start=1):
            rankings.append(
                RankingEntry(
                    rank=rank,
                    agent_id=agent.agent_id,
                    requests=agent.requests,
                    hit_rate_pct=agent.hit_rate_pct,
                    tokens_saved=agent.tokens_saved,
                    cost_saved_usd=agent.cost_saved_usd,
                    avg_latency_ms=agent.avg_latency_ms,
                    score=score,
                )
            )
        return rankings
