import pytest
from pydantic import ValidationError

from gateway.models import ChatCompletionRequest, ChatCompletionResponse, Message


class TestMessage:
    def test_valid_user_message(self):
        m = Message(role="user", content="Hello")
        assert m.role == "user"
        assert m.content == "Hello"

    def test_valid_system_message(self):
        m = Message(role="system", content="Be helpful")
        assert m.role == "system"

    def test_invalid_role(self):
        with pytest.raises(ValidationError):
            Message(role="admin", content="test")


class TestChatCompletionRequest:
    def test_minimal(self):
        data = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        req = ChatCompletionRequest(**data)
        assert req.model == "deepseek-chat"
        assert len(req.messages) == 1
        assert req.stream is False

    def test_with_optional_fields(self):
        data = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": "Hi"}],
            "temperature": 0.7,
            "top_p": 0.9,
            "max_tokens": 2048,
            "stream": True,
            "stop": ["\n"],
            "presence_penalty": 0.1,
            "frequency_penalty": 0.1,
        }
        req = ChatCompletionRequest(**data)
        assert req.temperature == 0.7
        assert req.max_tokens == 2048
        assert req.stream is True
        assert req.stop == ["\n"]

    def test_missing_model(self):
        with pytest.raises(ValidationError):
            ChatCompletionRequest(messages=[{"role": "user", "content": "Hi"}])

    def test_missing_messages(self):
        with pytest.raises(ValidationError):
            ChatCompletionRequest(model="deepseek-chat")

    def test_multiple_messages(self):
        data = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "Be helpful"},
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello!"},
            ],
        }
        req = ChatCompletionRequest(**data)
        assert len(req.messages) == 3
        assert req.messages[0].role == "system"
        assert req.messages[1].role == "user"
        assert req.messages[2].role == "assistant"


class TestChatCompletionResponse:
    def test_minimal_response(self):
        data = {
            "id": "chatcmpl-test123",
            "model": "deepseek-chat",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        resp = ChatCompletionResponse(**data)
        assert resp.id == "chatcmpl-test123"
        assert resp.model == "deepseek-chat"
        assert len(resp.choices) == 1
        assert resp.usage.total_tokens == 15
        assert resp.x_gateway is None

    def test_with_x_gateway_miss(self):
        data = {
            "id": "chatcmpl-test123",
            "model": "deepseek-chat",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello!"},
                    "finish_reason": "stop",
                }
            ],
            "x_gateway": {"cache_tier": "miss", "cache_hit": False, "tokens_saved": 0, "latency_ms": 45.2},
        }
        resp = ChatCompletionResponse(**data)
        assert resp.x_gateway.cache_tier == "miss"
        assert resp.x_gateway.cache_hit is False
        assert resp.x_gateway.tokens_saved == 0
        assert resp.x_gateway.latency_ms == 45.2

    def test_with_x_gateway_hit(self):
        data = {
            "id": "chatcmpl-test123",
            "model": "deepseek-chat",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello!"},
                    "finish_reason": "stop",
                }
            ],
            "x_gateway": {"cache_tier": "exact", "cache_hit": True, "cache_key": "exact:abc", "tokens_saved": 150},
        }
        resp = ChatCompletionResponse(**data)
        assert resp.x_gateway.cache_tier == "exact"
        assert resp.x_gateway.cache_hit is True
        assert resp.x_gateway.cache_key == "exact:abc"
        assert resp.x_gateway.tokens_saved == 150
