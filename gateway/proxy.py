import time
from collections.abc import AsyncIterator

import httpx

from .config import settings
from .logger import get_logger

logger = get_logger()


class DeepSeekUpstreamError(Exception):
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"DeepSeek returned {status_code}: {body[:200]}")


class DeepSeekProxy:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.base_url = settings.deepseek_base_url

    async def chat_completion(
        self, request_data: dict, auth_header: str
    ) -> tuple[dict, float]:
        start = time.monotonic()
        headers = {
            "Authorization": auth_header,
            "Content-Type": "application/json",
        }
        response = await self.client.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=request_data,
            timeout=120.0,
        )
        latency = (time.monotonic() - start) * 1000

        if response.status_code >= 400:
            error_body = response.text
            logger.error(
                "deepseek_upstream_error",
                extra={"status_code": response.status_code, "body": error_body},
            )
            raise DeepSeekUpstreamError(response.status_code, error_body)

        return response.json(), latency

    async def chat_completion_stream(
        self, request_data: dict, auth_header: str
    ) -> AsyncIterator[bytes]:
        headers = {
            "Authorization": auth_header,
            "Content-Type": "application/json",
        }
        async with self.client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=request_data,
            timeout=120.0,
        ) as response:
            if response.status_code >= 400:
                error_body = await response.aread()
                logger.error(
                    "deepseek_upstream_stream_error",
                    extra={
                        "status_code": response.status_code,
                        "body": error_body.decode()[:200],
                    },
                )
                raise DeepSeekUpstreamError(
                    response.status_code, error_body.decode()
                )

            async for chunk in response.aiter_bytes():
                yield chunk
