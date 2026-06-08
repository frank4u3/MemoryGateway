"""Prometheus metrics for the Memory Gateway."""

from prometheus_client import Counter, Histogram, Gauge, generate_latest, REGISTRY

# ---- Counters ----

requests_total = Counter(
    "gateway_requests_total",
    "Total number of requests",
    labelnames=["agent_id"],
)

cache_hits_total = Counter(
    "gateway_cache_hits_total",
    "Total cache hits",
    labelnames=["agent_id"],
)

cache_misses_total = Counter(
    "gateway_cache_misses_total",
    "Total cache misses",
    labelnames=["agent_id"],
)

tokens_prompt_total = Counter(
    "gateway_tokens_prompt_total",
    "Total prompt tokens sent to upstream",
)

tokens_completion_total = Counter(
    "gateway_tokens_completion_total",
    "Total completion tokens received from upstream",
)

tokens_saved_total = Counter(
    "gateway_tokens_saved_total",
    "Total tokens saved by cache",
)

cost_spent_total = Counter(
    "gateway_cost_spent_total",
    "Total estimated cost spent (USD)",
)

cost_saved_total = Counter(
    "gateway_cost_saved_total",
    "Total estimated cost saved by cache (USD)",
)

# ---- Histograms ----

latency_seconds = Histogram(
    "gateway_latency_seconds",
    "Request latency in seconds",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

# ---- Gauges ----

cache_hit_rate = Gauge(
    "gateway_cache_hit_rate",
    "Current cache hit rate (0-100)",
)

active_requests = Gauge(
    "gateway_active_requests",
    "Number of requests currently in flight",
)


def get_prometheus_text() -> str:
    """Return the prometheus metrics as text."""
    return generate_latest(REGISTRY).decode("utf-8")



