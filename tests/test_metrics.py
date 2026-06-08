import pytest
from fakeredis import FakeAsyncRedis

from gateway.metrics.cost import CostCalculator
from gateway.metrics.prometheus import (
    get_prometheus_text,
    requests_total,
    cache_hits_total,
    cache_misses_total,
    tokens_prompt_total,
    tokens_completion_total,
    tokens_saved_total,
    cost_spent_total,
    cost_saved_total,
    latency_seconds,
)
from gateway.metrics.schemas import (
    CacheMetrics,
    CostMetrics,
    MetricsOverview,
)

# ---------------------------------------------------------------------------
# Cost calculator tests
# ---------------------------------------------------------------------------


class TestCostCalculator:
    def test_cost_for_tokens(self):
        calc = CostCalculator()
        cost = calc.cost_for_tokens(1_000_000, 500_000)
        expected = 0.14 + 0.21
        assert cost == pytest.approx(expected, rel=0.01)

    def test_savings_from_cached_tokens(self):
        calc = CostCalculator()
        saved = calc.savings_from_cached_tokens(1_000_000)
        assert saved == pytest.approx(0.14, rel=0.01)

    def test_zero_tokens(self):
        calc = CostCalculator()
        assert calc.cost_for_tokens(0, 0) == 0.0
        assert calc.savings_from_cached_tokens(0) == 0.0

    def test_small_amounts(self):
        calc = CostCalculator()
        cost = calc.cost_for_tokens(1000, 500)
        assert cost > 0
        assert cost < 0.001

    def report_includes_all_keys(self):
        calc = CostCalculator()
        report = calc.report(1000, 500, 200)
        assert "cost_spent_usd" in report
        assert "cost_saved_usd" in report
        assert "net_savings_usd" in report
        assert "total_prompt_tokens" in report
        assert "total_completion_tokens" in report

    def test_custom_pricing(self):
        calc = CostCalculator(
            input_price_per_1m=0.5, output_price_per_1m=1.5
        )
        cost = calc.cost_for_tokens(1_000_000, 1_000_000)
        assert cost == pytest.approx(2.0, rel=0.01)


# ---------------------------------------------------------------------------
# Prometheus metrics tests
# ---------------------------------------------------------------------------


class TestPrometheusMetrics:
    def test_counters_record(self):
        requests_total.labels(agent_id="test").inc()
        requests_total.labels(agent_id="test").inc()
        out = get_prometheus_text()
        assert 'gateway_requests_total{agent_id="test"} 2.0' in out

    def test_separate_agent_labels(self):
        requests_total.labels(agent_id="a_labels").inc()
        requests_total.labels(agent_id="b_labels").inc()
        requests_total.labels(agent_id="b_labels").inc()
        out = get_prometheus_text()
        assert 'gateway_requests_total{agent_id="a_labels"} 1.0' in out
        assert 'gateway_requests_total{agent_id="b_labels"} 2.0' in out

    def test_cache_hits_misses(self):
        cache_hits_total.labels(agent_id="test_hits").inc(3)
        cache_misses_total.labels(agent_id="test_hits").inc(2)
        out = get_prometheus_text()
        assert 'gateway_cache_hits_total{agent_id="test_hits"} 3.0' in out
        assert 'gateway_cache_misses_total{agent_id="test_hits"} 2.0' in out

    def test_tokens_counters(self):
        tokens_prompt_total.inc(100)
        tokens_completion_total.inc(50)
        tokens_saved_total.inc(200)
        out = get_prometheus_text()
        assert "gateway_tokens_prompt_total" in out
        assert "gateway_tokens_completion_total" in out
        assert "gateway_tokens_saved_total" in out

    def test_cost_counters(self):
        cost_spent_total.inc(0.001)
        cost_saved_total.inc(0.005)
        out = get_prometheus_text()
        assert "gateway_cost_spent_total" in out
        assert "gateway_cost_saved_total" in out

    def test_latency_histogram(self):
        latency_seconds.observe(0.5)
        latency_seconds.observe(1.0)
        out = get_prometheus_text()
        assert "gateway_latency_seconds_count" in out
        assert "gateway_latency_seconds_bucket" in out

    def test_prometheus_text_format(self):
        text = get_prometheus_text()
        assert "# HELP" in text
        assert "# TYPE" in text
        assert "gateway_requests_total" in text


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestMetricsSchemas:
    def test_metrics_overview(self):
        m = MetricsOverview(
            total_requests=100,
            total_hits=80,
            total_misses=20,
            hit_rate_pct=80.0,
            tokens_saved=5000,
            tokens_prompt=10000,
            tokens_completion=5000,
            cost_saved_usd=0.001,
            cost_spent_usd=0.002,
            avg_latency_ms=250.0,
            agent_breakdown={"hermes": {"hits": 50, "misses": 10}},
        )
        assert m.total_requests == 100
        assert m.hit_rate_pct == 80.0

    def test_cache_metrics(self):
        m = CacheMetrics(
            hit_rate_pct=75.0,
            daily_hits=[{"date": "2026-01-01", "value": 10}],
            daily_misses=[{"date": "2026-01-01", "value": 3}],
            daily_hit_rate=[{"date": "2026-01-01", "rate": 76.9}],
            top_keys=[{"key": "exact:abc", "hits": 5}],
            total_hits=100,
            total_misses=50,
        )
        assert m.hit_rate_pct == 75.0
        assert len(m.top_keys) == 1

    def test_cost_metrics(self):
        m = CostMetrics(
            cost_spent_usd=1.0,
            cost_saved_usd=2.0,
            net_savings_usd=1.0,
            daily_cost_spent=[],
            daily_cost_saved=[],
            daily_savings=[],
            weekly_savings=[],
            estimated_input_cost_per_1m=0.14,
            estimated_output_cost_per_1m=0.42,
            total_prompt_tokens=1000,
            total_completion_tokens=500,
        )
        assert m.net_savings_usd == 1.0
        assert m.total_prompt_tokens == 1000


# ---------------------------------------------------------------------------
# Integration: stats tracker extended functionality tests
# ---------------------------------------------------------------------------


class TestStatsTrackerExtended:
    @pytest.fixture
    async def stats(self):
        r = FakeAsyncRedis(decode_responses=True)
        from gateway.stats import StatsTracker

        s = StatsTracker(r)
        await s.reset()
        yield s
        await s.reset()
        await r.aclose()

    @pytest.mark.asyncio
    async def test_record_tokens(self, stats):
        await stats.record_tokens(1000, 500)
        overall = await stats.get_stats()
        assert overall["tokens_prompt"] == 1000
        assert overall["tokens_completion"] == 500

    @pytest.mark.asyncio
    async def test_record_cost(self, stats):
        await stats.record_cost(0.001, 0.002)
        overall = await stats.get_stats()
        assert overall["cost_spent_usd"] == pytest.approx(0.001)
        assert overall["cost_saved_usd"] == pytest.approx(0.002)

    @pytest.mark.asyncio
    async def test_record_latency(self, stats):
        await stats.record_latency(100.0)
        await stats.record_latency(200.0)
        overall = await stats.get_stats()
        assert overall["avg_latency_ms"] == pytest.approx(150.0)

    @pytest.mark.asyncio
    async def test_record_cache_key_hit(self, stats):
        await stats.record_cache_key_hit("exact:abc")
        await stats.record_cache_key_hit("exact:abc")
        await stats.record_cache_key_hit("exact:def")
        top = await stats.get_top_cache_keys()
        assert len(top) == 2
        assert top[0]["key"] == "exact:abc"
        assert top[0]["hits"] == 2

    @pytest.mark.asyncio
    async def test_get_daily_series(self, stats):
        await stats.record_request("test")
        await stats.record_hit("test", 100)
        await stats.record_miss("test")
        series = await stats.get_daily_series("requests")
        assert len(series) == 14
        assert series[-1]["value"] >= 1  # today should have data
