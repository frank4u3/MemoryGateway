import httpx
import pytest
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient

from gateway.cache.exact import ExactCache
from gateway.main import create_app
from gateway.proxy import DeepSeekProxy
from gateway.stats import StatsTracker


@pytest.fixture
async def app():
    _app = create_app()
    http_client = httpx.AsyncClient()
    _app.state.proxy = DeepSeekProxy(http_client)

    redis = FakeAsyncRedis(decode_responses=True)
    _app.state.redis = redis
    _app.state.cache = ExactCache(redis, ttl=60)
    _app.state.stats = StatsTracker(redis)

    yield _app
    await http_client.aclose()
    await redis.aclose()


@pytest.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


class TestHealth:
    async def test_health_returns_ok(self, client):
        response = await client.get("/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.5.0"
        assert data["components"]["gateway"]["status"] == "ok"

    async def test_health_has_redis_component(self, client):
        response = await client.get("/v1/health")
        data = response.json()
        assert "redis" in data["components"]
        assert "gateway" in data["components"]
        assert "deepseek" in data["components"]


class TestCacheEndpoints:
    async def test_cache_stats(self, client):
        response = await client.get("/v1/cache/stats")
        assert response.status_code == 200
        data = response.json()
        assert "overall" in data
        assert "by_agent" in data
        assert "cache_size" in data

    async def test_cache_flush(self, client):
        response = await client.delete("/v1/cache/exact")
        assert response.status_code == 200
        data = response.json()
        assert "flushed" in data


class TestChatCompletionsValidation:
    async def test_missing_x_agent_id_returns_422(self, client):
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers={"Authorization": "Bearer test-key"},
        )
        assert response.status_code == 422
        data = response.json()
        assert "X-Agent-ID" in data["error"]["message"]

    async def test_empty_x_agent_id_returns_422(self, client):
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers={"X-Agent-ID": "", "Authorization": "Bearer test-key"},
        )
        assert response.status_code == 422

    async def test_missing_authorization_returns_401(self, client):
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers={"X-Agent-ID": "hermes"},
        )
        assert response.status_code == 401
        data = response.json()
        assert "Authorization" in data["error"]["message"]

    async def test_missing_body_returns_422(self, client):
        response = await client.post(
            "/v1/chat/completions",
            json={},
            headers={"X-Agent-ID": "hermes", "Authorization": "Bearer test-key"},
        )
        assert response.status_code == 422

    async def test_unknown_agent_id_returns_502(self, client):
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers={"X-Agent-ID": "custom-agent", "Authorization": "Bearer test-key"},
        )
        assert response.status_code == 502


class TestChatCompletionsProxy:
    async def test_upstream_connection_error_returns_502(self, client):
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers={"X-Agent-ID": "hermes", "Authorization": "Bearer test-key"},
        )
        assert response.status_code == 502
        data = response.json()
        assert data["error"]["type"] == "upstream_error"

    async def test_streaming_endpoint_accepted(self, client):
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True,
            },
            headers={"X-Agent-ID": "opencode", "Authorization": "Bearer test-key"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
