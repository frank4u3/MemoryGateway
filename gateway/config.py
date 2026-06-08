from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    gateway_port: int = 8765
    log_level: str = "INFO"
    max_connections: int = 100
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 3600
    cache_enabled: bool = True

    qdrant_url: str = "http://localhost:6333"
    qdrant_in_memory: bool = True

    semantic_cache_enabled: bool = False
    semantic_cache_threshold: float = 0.98

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
