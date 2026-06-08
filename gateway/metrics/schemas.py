from pydantic import BaseModel
from typing import Optional


class MetricsOverview(BaseModel):
    total_requests: int
    total_hits: int
    total_misses: int
    hit_rate_pct: float
    tokens_saved: int
    tokens_prompt: int
    tokens_completion: int
    cost_saved_usd: float
    cost_spent_usd: float
    avg_latency_ms: float
    agent_breakdown: dict[str, dict]


class CacheMetrics(BaseModel):
    hit_rate_pct: float
    daily_hits: list[dict]
    daily_misses: list[dict]
    daily_hit_rate: list[dict]
    top_keys: list[dict]
    total_hits: int
    total_misses: int


class CostMetrics(BaseModel):
    cost_spent_usd: float
    cost_saved_usd: float
    net_savings_usd: float
    daily_cost_spent: list[dict]
    daily_cost_saved: list[dict]
    daily_savings: list[dict]
    weekly_savings: list[dict]
    estimated_input_cost_per_1m: float
    estimated_output_cost_per_1m: float
    total_prompt_tokens: int
    total_completion_tokens: int
