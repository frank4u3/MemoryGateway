# Memory Gateway — Phase 3

OpenAI-compatible local proxy for DeepSeek with Redis-backed exact-match caching and canonical prompt builder. Routes all coding agent requests through a single endpoint. Identical requests return instantly from cache without calling DeepSeek.

## Architecture

```
Agent (Hermes/OpenCode/Qoder/VSCode)
    │ POST /v1/chat/completions
    │ X-Agent-ID: hermes
    │ Authorization: Bearer <deepseek-key>
    ▼
Memory Gateway (FastAPI :8765)
    │ Canonical Prompt Builder
    │   • Normalize UUIDs, timestamps, paths, sessions, agent IDs, temp files
    │   • Deduplicate system prompts
    │   • Collapse whitespace
    │   • Generate canonical_hash (SHA-256)
    │ Check Redis exact cache
    ├─ Hit  → return cached response
    └─ Miss → POST /chat/completions → DeepSeek API
               │ Store response in Redis
               ▼
              Return response
```

## Quick Start

### 1. Configure

```bash
cp .env.example .env
# Edit .env — set DEEPSEEK_BASE_URL, REDIS_URL if needed
```

### 2. Run with Docker

```bash
docker compose up --build
```

### 3. Test

```bash
curl -X POST http://localhost:8765/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
  -H "X-Agent-ID: hermes" \
  -d '{
    "model": "deepseek-chat",
    "messages": [{"role": "user", "content": "Hello"}],
    "temperature": 0.2
  }'
```

### 4. Run locally (without Docker)

```bash
pip install -r requirements.txt
# Start Redis separately (e.g., docker run -p 6379:6379 redis:7-alpine)
uvicorn gateway.main:app --host 0.0.0.0 --port 8765
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/health` | Health check with component status |
| POST | `/v1/chat/completions` | OpenAI-compatible chat completion |
| GET | `/v1/cache/stats` | Cache hit/miss stats |
| DELETE | `/v1/cache/exact` | Flush all exact cache entries |

### Headers

| Header | Required | Description |
|--------|----------|-------------|
| `Authorization` | Yes | `Bearer <deepseek-api-key>` — forwarded to DeepSeek |
| `X-Agent-ID` | Yes | One of: `hermes`, `opencode`, `qoder`, `vscode` |
| `X-Cache-Bypass` | No | Set to `true` to skip cache for this request |
| `Content-Type` | Yes | `application/json` |

### Streaming

Set `"stream": true` in the request body. The gateway forwards SSE chunks from DeepSeek transparently. Streaming responses are not cached.

## Agent Configuration

**Hermes / Python:**
```python
import openai
client = openai.OpenAI(
    api_key="<deepseek-key>",
    base_url="http://localhost:8765/v1",
    default_headers={"X-Agent-ID": "hermes"},
)
```

**OpenCode / Qoder (env vars):**
```env
OPENAI_BASE_URL=http://localhost:8765/v1
OPENAI_API_KEY=<deepseek-key>
```

## Testing

```bash
# Install dev dependencies
pip install pytest pytest-asyncio httpx

# Run tests
pytest -v
```

## Project Structure

```
memory-gateway/
├── gateway/
│   ├── __init__.py
│   ├── main.py              # FastAPI app, lifespan
│   ├── router.py            # Endpoint handlers
│   ├── proxy.py             # httpx client to DeepSeek
│   ├── models.py            # Pydantic request/response models
│   ├── config.py            # Settings from environment
│   ├── logger.py            # Structured JSON logging
│   ├── canonicalizer.py     # Prompt normalization
│   ├── stats.py             # Hit/miss metrics
│   └── cache/
│       ├── __init__.py
│       └── exact.py         # Redis exact cache wrapper
├── tests/
│   ├── test_unit.py              # Unit tests (models, config)
│   ├── test_integration.py       # Integration tests (endpoints)
│   ├── test_canonicalizer.py     # Canonicalizer unit tests (73 cases)
│   ├── test_cache.py             # Cache unit tests
│   ├── benchmark_cache.py        # Cache performance benchmarks
│   └── benchmark_canonicalizer.py # Canonicalizer performance benchmarks
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── MIGRATION_GUIDE.md
└── README.md
```

## Response Metadata

Responses include `x_gateway` metadata with cache status and canonical hash:

**Cache hit:**
```json
{
  "x_gateway": {
    "cache_tier": "exact",
    "cache_key": "exact:sha256:abc123...",
    "cache_hit": true,
    "tokens_saved": 150,
    "latency_ms": 1.2,
    "canonical_hash": "abc123def456..."
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
    "latency_ms": 142.3,
    "canonical_hash": "abc123def456..."
  }
}
```
