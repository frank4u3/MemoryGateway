"""Pydantic models for telemetry API responses."""

from pydantic import BaseModel


class AgentTelemetry(BaseModel):
    agent_id: str
    requests: int
    hits: int
    misses: int
    hit_rate_pct: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    tokens_saved: int
    cost_spent_usd: float
    cost_saved_usd: float
    net_cost_usd: float
    avg_latency_ms: float


class RankingEntry(BaseModel):
    rank: int
    agent_id: str
    requests: int
    hit_rate_pct: float
    tokens_saved: int
    cost_saved_usd: float
    avg_latency_ms: float
    score: float


class TelemetryOverview(BaseModel):
    total_requests: int
    total_hits: int
    total_misses: int
    hit_rate_pct: float
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens_saved: int
    total_cost_spent_usd: float
    total_cost_saved_usd: float
    total_net_cost_usd: float
    avg_latency_ms: float
    agents: list[AgentTelemetry]
    rankings: list[RankingEntry]
