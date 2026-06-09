import hashlib
import json
import time

import redis.asyncio as aioredis

from ..config import settings
from ..logger import get_logger

logger = get_logger()


def generate_cache_key(
    model: str,
    canonical_messages: list[dict],
    temperature: float | None = None,
    max_tokens: int | None = None,
    top_p: float | None = None,
    presence_penalty: float | None = None,
    frequency_penalty: float | None = None,
) -> str:
    payload = {"model": model, "messages": canonical_messages}
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if top_p is not None:
        payload["top_p"] = top_p
    if presence_penalty is not None:
        payload["presence_penalty"] = presence_penalty
    if frequency_penalty is not None:
        payload["frequency_penalty"] = frequency_penalty

    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    digest = hashlib.sha256(raw).hexdigest()
    return f"exact:{digest}"


def tokens_saved_from_cached(cached_response: dict) -> int:
    return cached_response.get("usage", {}).get("prompt_tokens", 0)


class ExactCache:
    def __init__(self, redis_client: aioredis.Redis, ttl: int = 3600):
        self.redis = redis_client
        self._default_ttl = ttl

    async def get(self, key: str) -> dict | None:
        data = await self.redis.get(key)
        if data is None:
            return None
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            logger.error("cache_corrupt_entry", extra={"key": key})
            await self.redis.delete(key)
            return None

    async def set(self, key: str, value: dict, ttl: int | None = None) -> None:
        serialized = json.dumps(value, ensure_ascii=False)
        await self.redis.setex(key, ttl or self._default_ttl, serialized)

    async def delete(self, pattern: str = "exact:*") -> int:
        cursor = 0
        deleted = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)
            if keys:
                deleted += await self.redis.delete(*keys)
            if cursor == 0:
                break
        return deleted

    async def exists(self, key: str) -> bool:
        return await self.redis.exists(key) > 0

    async def key_ttl(self, key: str) -> int:
        return await self.redis.ttl(key)

    async def size(self) -> int:
        cursor = 0
        count = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match="exact:*", count=100)
            count += len(keys)
            if cursor == 0:
                break
        return count

    async def ping(self) -> float:
        start = time.monotonic()
        await self.redis.ping()
        return (time.monotonic() - start) * 1000
