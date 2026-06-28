import time
from datetime import datetime, timezone as dt_timezone

import redis.asyncio as aioredis

from .logger import get_logger

logger = get_logger()

HITS_KEY = "stats:cache:hits"
MISSES_KEY = "stats:cache:misses"
TOKENS_SAVED_KEY = "stats:cache:tokens_saved"
TOKENS_PROMPT_KEY = "stats:tokens:prompt"
TOKENS_COMPLETION_KEY = "stats:tokens:completion"
REQUESTS_KEY = "stats:requests:total"
LATENCY_KEY = "stats:latency:recent"
CACHE_KEY_HITS = "stats:cache:key_hits"
AGENT_HITS_TPL = "stats:agent:{agent}:hits"
AGENT_MISSES_TPL = "stats:agent:{agent}:misses"
AGENT_REQUESTS_TPL = "stats:agent:{agent}:requests"

COST_SPENT_KEY = "stats:cost:spent"
COST_SAVED_KEY = "stats:cost:saved"

AGENT_TOKENS_PROMPT_TPL = "stats:agent:{agent}:tokens_prompt"
AGENT_TOKENS_COMPLETION_TPL = "stats:agent:{agent}:tokens_completion"
AGENT_COST_SPENT_TPL = "stats:agent:{agent}:cost_spent"
AGENT_COST_SAVED_TPL = "stats:agent:{agent}:cost_saved"

MAX_LATENCY_SAMPLES = 1000


def _today() -> str:
    return datetime.now(dt_timezone.utc).strftime("%Y-%m-%d")


class StatsTracker:
    def __init__(self, redis: aioredis.Redis):
        self.redis = redis

    async def record_request(self, agent_id: str) -> None:
        if self.redis is None:
            return
        today = _today()
        pipe = self.redis.pipeline()
        pipe.incr(REQUESTS_KEY)
        pipe.incr(AGENT_REQUESTS_TPL.format(agent=agent_id))
        pipe.incr(f"stats:daily:{today}:requests")
        await pipe.execute()

    async def record_hit(self, agent_id: str, tokens_saved: int) -> None:
        if self.redis is None: return
        today = _today()
        pipe = self.redis.pipeline()
        pipe.incr(HITS_KEY)
        pipe.incr(AGENT_HITS_TPL.format(agent=agent_id))
        pipe.incr(f"stats:daily:{today}:hits")
        if tokens_saved > 0:
            pipe.incrby(TOKENS_SAVED_KEY, tokens_saved)
            pipe.incrby(f"stats:daily:{today}:tokens_saved", tokens_saved)
        await pipe.execute()

    async def record_miss(self, agent_id: str) -> None:
        if self.redis is None: return
        today = _today()
        pipe = self.redis.pipeline()
        pipe.incr(MISSES_KEY)
        pipe.incr(AGENT_MISSES_TPL.format(agent=agent_id))
        pipe.incr(f"stats:daily:{today}:misses")
        await pipe.execute()

    async def record_tokens(
        self, prompt_tokens: int, completion_tokens: int
    ) -> None:
        if self.redis is None: return
        today = _today()
        pipe = self.redis.pipeline()
        pipe.incrby(TOKENS_PROMPT_KEY, prompt_tokens)
        pipe.incrby(TOKENS_COMPLETION_KEY, completion_tokens)
        pipe.incrby(f"stats:daily:{today}:tokens_prompt", prompt_tokens)
        pipe.incrby(
            f"stats:daily:{today}:tokens_completion", completion_tokens
        )
        await pipe.execute()

    async def record_tokens_for_agent(
        self, agent_id: str, prompt_tokens: int, completion_tokens: int
    ) -> None:
        today = _today()
        pipe = self.redis.pipeline()
        pipe.incrby(AGENT_TOKENS_PROMPT_TPL.format(agent=agent_id), prompt_tokens)
        pipe.incrby(AGENT_TOKENS_COMPLETION_TPL.format(agent=agent_id), completion_tokens)
        pipe.incrby(f"stats:daily:{today}:agent:{agent_id}:tokens_prompt", prompt_tokens)
        pipe.incrby(f"stats:daily:{today}:agent:{agent_id}:tokens_completion", completion_tokens)
        await pipe.execute()

    async def record_cost_for_agent(
        self, agent_id: str, cost_spent: float, cost_saved: float
    ) -> None:
        today = _today()
        pipe = self.redis.pipeline()
        pipe.incrbyfloat(AGENT_COST_SPENT_TPL.format(agent=agent_id), cost_spent)
        pipe.incrbyfloat(AGENT_COST_SAVED_TPL.format(agent=agent_id), cost_saved)
        pipe.incrbyfloat(f"stats:daily:{today}:agent:{agent_id}:cost_spent", cost_spent)
        pipe.incrbyfloat(f"stats:daily:{today}:agent:{agent_id}:cost_saved", cost_saved)
        await pipe.execute()

    async def record_cost(
        self, cost_spent: float, cost_saved: float
    ) -> None:
        today = _today()
        pipe = self.redis.pipeline()
        pipe.incrbyfloat(COST_SPENT_KEY, cost_spent)
        pipe.incrbyfloat(COST_SAVED_KEY, cost_saved)
        pipe.incrbyfloat(
            f"stats:daily:{today}:cost_spent", cost_spent
        )
        pipe.incrbyfloat(
            f"stats:daily:{today}:cost_saved", cost_saved
        )
        await pipe.execute()

    async def record_latency(self, latency_ms: float) -> None:
        key = f"{LATENCY_KEY}:{_today()}"
        pipe = self.redis.pipeline()
        pipe.rpush(key, latency_ms)
        pipe.ltrim(key, -MAX_LATENCY_SAMPLES, -1)
        pipe.expire(key, 86400 * 8)
        await pipe.execute()

    async def record_cache_key_hit(self, cache_key: str) -> None:
        pipe = self.redis.pipeline()
        pipe.zincrby(CACHE_KEY_HITS, 1, cache_key)
        pipe.expire(CACHE_KEY_HITS, 86400 * 8)
        await pipe.execute()

    async def get_stats(self) -> dict:
        hits = int(await self.redis.get(HITS_KEY) or 0)
        misses = int(await self.redis.get(MISSES_KEY) or 0)
        tokens = int(await self.redis.get(TOKENS_SAVED_KEY) or 0)
        total = hits + misses

        prompt = int(await self.redis.get(TOKENS_PROMPT_KEY) or 0)
        completion = int(
            await self.redis.get(TOKENS_COMPLETION_KEY) or 0
        )
        spent = float(await self.redis.get(COST_SPENT_KEY) or 0)
        saved = float(await self.redis.get(COST_SAVED_KEY) or 0)

        avg_latency = 0.0
        today = _today()
        latencies = await self.redis.lrange(
            f"{LATENCY_KEY}:{today}", 0, -1
        )
        if latencies:
            vals = [float(x) for x in latencies]
            avg_latency = round(sum(vals) / len(vals), 1)

        return {
            "hits": hits,
            "misses": misses,
            "hit_rate_pct": round(hits / total * 100, 1)
            if total > 0
            else 0.0,
            "total_requests": total,
            "tokens_saved": tokens,
            "tokens_prompt": prompt,
            "tokens_completion": completion,
            "cost_spent_usd": round(spent, 6),
            "cost_saved_usd": round(saved, 6),
            "avg_latency_ms": avg_latency,
        }

    async def get_agent_stats(self) -> dict:
        agents = ["hermes", "opencode", "qoder", "vscode"]
        result = {}
        for agent in agents:
            hits = int(
                await self.redis.get(
                    AGENT_HITS_TPL.format(agent=agent)
                )
                or 0
            )
            misses = int(
                await self.redis.get(
                    AGENT_MISSES_TPL.format(agent=agent)
                )
                or 0
            )
            requests = int(
                await self.redis.get(
                    AGENT_REQUESTS_TPL.format(agent=agent)
                )
                or 0
            )
            total = hits + misses
            result[agent] = {
                "requests": requests,
                "hits": hits,
                "misses": misses,
                "hit_rate_pct": round(hits / total * 100, 1)
                if total > 0
                else 0.0,
            }
        return result

    async def get_daily_series(self, metric: str, days: int = 14):
        results = []
        for i in range(days - 1, -1, -1):
            ts = time.time() - i * 86400
            from datetime import datetime as dt

            date_str = dt.fromtimestamp(ts).strftime("%Y-%m-%d")
            key = f"stats:daily:{date_str}:{metric}"
            val = int(await self.redis.get(key) or 0)
            results.append({"date": date_str, "value": val})
        return results

    async def get_daily_float_series(
        self, metric: str, days: int = 14
    ):
        results = []
        for i in range(days - 1, -1, -1):
            ts = time.time() - i * 86400
            from datetime import datetime as dt

            date_str = dt.fromtimestamp(ts).strftime("%Y-%m-%d")
            key = f"stats:daily:{date_str}:{metric}"
            val = float(await self.redis.get(key) or 0)
            results.append({"date": date_str, "value": val})
        return results

    async def get_top_cache_keys(self, limit: int = 20):
        keys = await self.redis.zrevrange(
            CACHE_KEY_HITS, 0, limit - 1, withscores=True
        )
        return [
            {"key": k.decode() if isinstance(k, bytes) else k, "hits": int(s)}
            for k, s in keys
        ]

    async def reset(self) -> None:
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match="stats:*", count=100)
            if keys:
                await self.redis.delete(*keys)
            if cursor == 0:
                break

    async def get_daily_baseline(self, date_str: str | None = None) -> dict:
        if date_str is None:
            date_str = _today()
        keys_base = f"stats:daily:{date_str}"
        prompt = int(await self.redis.get(f"{keys_base}:tokens_prompt") or 0)
        completion = int(await self.redis.get(f"{keys_base}:tokens_completion") or 0)
        requests = int(await self.redis.get(f"{keys_base}:requests") or 0)
        hits = int(await self.redis.get(f"{keys_base}:hits") or 0)
        misses = int(await self.redis.get(f"{keys_base}:misses") or 0)
        tokens_saved = int(await self.redis.get(f"{keys_base}:tokens_saved") or 0)
        cost_spent = float(await self.redis.get(f"{keys_base}:cost_spent") or 0)
        cost_saved = float(await self.redis.get(f"{keys_base}:cost_saved") or 0)

        agents = {}
        for agent_id in ["hermes", "opencode", "qoder", "vscode"]:
            a_req = int(await self.redis.get(f"{keys_base}:agent_requests:{agent_id}") or 0)
            if a_req == 0:
                a_req = int(await self.redis.get(f"{keys_base}:requests") or 0)
            a_prompt = int(await self.redis.get(f"{keys_base}:agent:{agent_id}:tokens_prompt") or 0)
            a_completion = int(await self.redis.get(f"{keys_base}:agent:{agent_id}:tokens_completion") or 0)
            a_spent = float(await self.redis.get(f"{keys_base}:agent:{agent_id}:cost_spent") or 0)
            a_saved = float(await self.redis.get(f"{keys_base}:agent:{agent_id}:cost_saved") or 0)
            agents[agent_id] = {
                "tokens_prompt": a_prompt,
                "tokens_completion": a_completion,
                "total_tokens": a_prompt + a_completion,
                "cost_spent_usd": round(a_spent, 8),
                "cost_saved_usd": round(a_saved, 8),
            }

        return {
            "date": date_str,
            "requests": requests,
            "hits": hits,
            "misses": misses,
            "tokens_prompt": prompt,
            "tokens_completion": completion,
            "total_tokens": prompt + completion,
            "tokens_saved": tokens_saved,
            "cost_spent_usd": round(cost_spent, 8),
            "cost_saved_usd": round(cost_saved, 8),
            "net_cost_usd": round(cost_saved - cost_spent, 8),
            "agents": agents,
        }
