"""Locust load test suite for Memory Gateway.

Usage:
    locust -f tests/load/locustfile.py --host=http://localhost:8765 --users=100 --spawn-rate=10

Scenarios:
    - Chat completion baseline
    - Cache hit/miss mix
    - Memory pack rebuild
    - Semantic search
    - Learning store/search
"""

import json
import os
import random
import uuid

from locust import HttpUser, between, task


AUTH_TOKEN = os.environ.get("MEMORY_GATEWAY_TOKEN", "test-token")
BASE_CHAT_PAYLOAD = {
    "model": "deepseek-chat",
    "messages": [
        {"role": "system", "content": "You are a helpful coding assistant."},
        {"role": "user", "content": "Write a Python function that sorts a list."},
    ],
    "max_tokens": 256,
    "temperature": 0.0,
}


class GatewayUser(HttpUser):
    wait_time = between(0.5, 2.0)

    def on_start(self):
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {AUTH_TOKEN}",
            "X-Agent-ID": random.choice(["hermes", "opencode", "qoder", "vscode"]),
        }

    # -- Chat completions --

    @task(15)
    def chat_completion_baseline(self):
        payload = dict(BASE_CHAT_PAYLOAD)
        payload["messages"][1]["content"] = random.choice([
            "Write a Python function that sorts a list.",
            "Explain how garbage collection works in Python.",
            "Show me a FastAPI route example.",
            "What is the time complexity of quicksort?",
        ])
        with self.client.post(
            "/v1/chat/completions",
            headers=self.headers,
            json=payload,
            catch_response=True,
        ) as resp:
            if resp.status_code not in (200, 502):
                resp.failure(f"Unexpected status {resp.status_code}")

    @task(5)
    def chat_completion_cacheable(self):
        """Sends identical requests to exercise the exact cache."""
        payload = dict(BASE_CHAT_PAYLOAD)
        payload["messages"][1]["content"] = "Write a hello world function."
        self.client.post(
            "/v1/chat/completions",
            headers=self.headers,
            json=payload,
        )

    # -- Metrics & health --

    @task(3)
    def health_check(self):
        self.client.get("/v1/health")

    @task(3)
    def metrics_dashboard(self):
        self.client.get("/v1/metrics/dashboard")

    @task(2)
    def prometheus_metrics(self):
        self.client.get("/v1/metrics/prometheus")

    @task(2)
    def telemetry_overview(self):
        self.client.get("/v1/telemetry/overview")

    # -- Memory pack --

    @task(2)
    def memory_pack_current(self):
        self.client.get("/v1/memory/pack/current")

    @task(1)
    def memory_pack_versions(self):
        self.client.get("/v1/memory/pack/versions")

    @task(1)
    def memory_pack_generate(self):
        self.client.post(
            "/v1/memory/pack/generate",
            headers=self.headers,
            json={"trigger_type": "manual"},
        )

    # -- Indexer --

    @task(2)
    def index_search(self):
        self.client.post(
            "/v1/index/search",
            headers=self.headers,
            json={"query": "sort function", "top_k": 5},
        )

    # -- Learning store/search --

    @task(2)
    def learning_search(self):
        self.client.post(
            "/v1/learning/search",
            headers=self.headers,
            json={"query": "database fix", "top_k": 10},
        )

    @task(1)
    def learning_store(self):
        learning_id = uuid.uuid4().hex[:16]
        self.client.post(
            "/v1/learning/store",
            headers=self.headers,
            json={
                "type": "bug_fix",
                "title": f"Load test fix {learning_id[:8]}",
                "content": "Test content for load testing.",
                "tags": ["load-test"],
                "resolved_by": "locust",
            },
        )

    # -- Artifact store/search --

    @task(2)
    def artifact_search(self):
        self.client.post(
            "/v1/artifact/search",
            headers=self.headers,
            json={"query": "API endpoint", "top_k": 5},
        )

    # -- Context registry --

    @task(1)
    def context_search(self):
        self.client.post(
            "/v1/context/search",
            headers=self.headers,
            json={"query": "config", "top_k": 5},
        )


class CacheLoadUser(HttpUser):
    """Focuses on cache hit rate under load."""

    wait_time = between(0.1, 0.5)

    def on_start(self):
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {AUTH_TOKEN}",
            "X-Agent-ID": "hermes",
        }

    @task
    def repeated_identical_request(self):
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "You are a code reviewer."},
                {"role": "user", "content": "Review this function."},
            ],
            "max_tokens": 100,
            "temperature": 0.0,
        }
        self.client.post(
            "/v1/chat/completions",
            headers=self.headers,
            json=payload,
        )
