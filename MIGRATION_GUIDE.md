# Phase 2 Migration Guide: Redis Exact Cache

## Overview

Phase 2 adds Redis-backed exact-match caching. Identical requests return instantly from Redis without calling DeepSeek. A canonical prompt builder normalizes request structure to maximize cross-agent cache hits.

## What Changed

### New Dependencies
- `redis>=5.0.0` — async Redis client

### New Files
| File | Purpose |
|---|---|
| `gateway/cache/exact.py` | Redis wrapper, SHA-256 cache key generation, get/set/delete |
| `gateway/canonicalizer.py` | Prompt normalization (timestamps, paths, UUIDs, whitespace) |
| `gateway/stats.py` | Hit/miss counters, token savings tracking |

### Modified Files
| File | Change |
|---|---|
| `requirements.txt` | Added `redis>=5.0.0` |
| `gateway/config.py` | Added `redis_url`, `cache_ttl_seconds`, `cache_enabled` |
| `gateway/models.py` | `GatewayInfo` now has `cache_hit: bool` |
| `gateway/main.py` | Lifespan creates Redis client, cache, and stats tracker |
| `gateway/router.py` | Chat completions checks cache before proxying to DeepSeek |
| `docker-compose.yml` | Added Redis service with healthcheck |
| `.env.example` | Added Redis and cache config vars |

### Config Changes

Add these to your `.env`:
```env
# Redis
REDIS_URL=redis://localhost:6379/0

# Cache
CACHE_TTL_SECONDS=3600
CACHE_ENABLED=true
```

## Migration Steps

### Step 1: Install Dependencies
```bash
pip install redis>=5.0.0
```

### Step 2: Update `.env`
Copy the new vars from `.env.example` or add:
```env
REDIS_URL=redis://localhost:6379/0
CACHE_TTL_SECONDS=3600
CACHE_ENABLED=true
```

### Step 3: Start Redis
**With Docker (recommended):**
```bash
docker compose up -d redis
```

**Without Docker (local install):**
```bash
redis-server
```

### Step 4: Restart Gateway
```bash
uvicorn gateway.main:app --host 0.0.0.0 --port 8765
```

### Step 5: Verify Caching
```bash
# First request — cache miss
curl -s -X POST http://localhost:8765/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
  -H "X-Agent-ID: hermes" \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"Hello"}]}' \
  | python -c "import sys,json; d=json.load(sys.stdin); print('cache_hit:', d.get('x_gateway',{}).get('cache_hit'))"

# Second request (identical) — cache hit
# ... same command → cache_hit: true
```

## API Changes

### Response `x_gateway` Now Includes `cache_hit`:

**Cache hit:**
```json
{
  "x_gateway": {
    "cache_tier": "exact",
    "cache_key": "exact:sha256:abc123...",
    "cache_hit": true,
    "tokens_saved": 150,
    "latency_ms": 1.2
  }
}
```

**Cache miss:**
```json
{
  "x_gateway": {
    "cache_tier": "miss",
    "cache_key": "exact:sha256:abc123...",
    "cache_hit": false,
    "tokens_saved": 0,
    "latency_ms": 142.3
  }
}
```

### New Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/v1/cache/stats` | Cache hit/miss stats, per-agent breakdown |
| DELETE | `/v1/cache/exact` | Flush all exact cache entries |

### New Headers

| Header | Description |
|---|---|
| `X-Cache-Bypass: true` | Skip cache for this request, force DeepSeek call |

## Expected Behavior

### Cache Key Generation
1. Incoming messages are canonicalized (system first, whitespace normalized, timestamps/paths/UUIDs replaced)
2. SHA-256 hash of `{model, canonical_messages, temperature, max_tokens, ...}`
3. Key format: `exact:<64-char-hex>`

### Cache Lookup
- **Hit:** Returns stored DeepSeek response immediately (< 5ms)
- **Miss:** Forwards to DeepSeek, stores response in Redis with TTL, returns

### TTL
- Default: 3600 seconds (1 hour)
- Configurable via `CACHE_TTL_SECONDS` env var
- Applies to all exact cache entries

## Rollback

To disable caching without code changes, set:
```env
CACHE_ENABLED=false
```
Restart the gateway. Request flow falls back to Phase 1 behavior (direct pass-through to DeepSeek).

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run benchmark
pytest tests/benchmark_cache.py -v --benchmark-only
```
