from pydantic import BaseModel
from typing import Literal, Optional


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list | None = None
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[list] = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[Message]
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    n: Optional[int] = None
    stream: bool = False
    stop: Optional[str | list[str]] = None
    max_tokens: Optional[int] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    logit_bias: Optional[dict[str, float]] = None
    user: Optional[str] = None


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class GatewayInfo(BaseModel):
    cache_tier: Literal["exact", "semantic", "miss"] = "miss"
    cache_key: Optional[str] = None
    cache_hit: bool = False
    tokens_saved: int = 0
    latency_ms: float = 0
    canonical_hash: Optional[str] = None
    semantic_hit: bool = False
    similarity_score: Optional[float] = None


class Choice(BaseModel):
    index: int
    message: Optional[dict] = None
    delta: Optional[dict] = None
    finish_reason: Optional[str] = None


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int = 0
    model: str
    choices: list[Choice]
    usage: Optional[Usage] = None
    x_gateway: Optional[GatewayInfo] = None
