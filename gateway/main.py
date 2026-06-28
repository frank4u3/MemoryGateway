from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .artifact.store import ArtifactStore
from .cache.exact import ExactCache
from .config import settings
from .context.store import ContextStore
from .indexer.qdrant_store import create_store as create_index_store
from .learning.store import LearningStore
from .logger import get_logger, setup_logging
from .memory.store import MemoryStore
from .memory_layer.store import MemoryLayerStore
from .proxy import DeepSeekProxy
from .router import router
from .semantic_cache.store import SemanticCache
from .stats import StatsTracker
from .telemetry import TelemetryService
from .metrics import CostCalculator


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger = get_logger()
    logger.info(
        "gateway_startup",
        extra={
            "port": settings.gateway_port,
            "deepseek_base": settings.deepseek_base_url,
            "redis_url": settings.redis_url,
            "cache_ttl": settings.cache_ttl_seconds,
        },
    )

    if settings.redis_url and settings.redis_url.strip():
        redis_client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
        )
        cache = ExactCache(redis_client, ttl=settings.cache_ttl_seconds)
        stats = StatsTracker(redis_client)
        app.state.redis = redis_client
    else:
        logger.info("no_redis_url", extra={"detail": "running without Redis (baseline/fail-open mode)"})
        redis_client = None
        cache = None
        stats = StatsTracker(None)
        app.state.redis = None

    if not hasattr(app.state, "proxy") or app.state.proxy is None:
        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0),
            limits=httpx.Limits(max_connections=settings.max_connections),
        )
        app.state.proxy = DeepSeekProxy(http_client)
        app.state._own_proxy = True

    app.state.cache = cache
    app.state.stats = stats

    cost_calculator = CostCalculator()
    telemetry = TelemetryService(stats=stats, cost_calculator=cost_calculator)
    app.state.telemetry = telemetry
    logger.info("telemetry_service_initialized")

    from gateway.telemetry.baseline import BaselineService
    baseline_service = BaselineService(redis_client=redis_client, stats=stats)
    app.state._baseline_service = baseline_service
    logger.info("baseline_service_initialized")

    index_store = create_index_store(in_memory=settings.qdrant_in_memory)
    app.state.index_store = index_store
    logger.info(
        "index_store_initialized",
        extra={"in_memory": settings.qdrant_in_memory},
    )

    memory_store = MemoryStore(redis_client=redis_client)
    app.state.memory_store = memory_store
    logger.info("memory_store_initialized")

    context_store = ContextStore(redis_client=redis_client, index_store=index_store)
    app.state.context_store = context_store
    logger.info("context_store_initialized")

    artifact_store = ArtifactStore(redis_client=redis_client, index_store=index_store)
    app.state.artifact_store = artifact_store
    logger.info("artifact_store_initialized")

    learning_store = LearningStore(redis_client=redis_client, index_store=index_store)
    app.state.learning_store = learning_store
    logger.info("learning_store_initialized")

    memory_layer = MemoryLayerStore(
        redis_client=redis_client,
        context_store=context_store,
        artifact_store=artifact_store,
        index_store=index_store,
        memory_store=memory_store,
    )
    app.state.memory_layer = memory_layer
    logger.info("memory_layer_initialized")

    # Memory pack auto-maintenance
    from gateway.memory.versioning import MemoryPackVersioning
    from gateway.memory.generator import MemoryPackGenerator
    from gateway.memory.auto_maintenance import AutoMaintenanceService, TriggerConfig

    import os as _os
    _repo_path = _os.environ.get("MEMORY_PACK_REPO_PATH", ".")

    _versioning = MemoryPackVersioning(
        base_dir=_os.environ.get("MEMORY_PACK_DIR", "memory_packs")
    )
    app.state._memory_pack_versioning = _versioning
    logger.info("memory_pack_versioning_initialized")

    _generator = MemoryPackGenerator()
    _maintenance_config = TriggerConfig(
        on_git_commit=_os.environ.get("MEMORY_PACK_TRIGGER_GIT", "true").lower() == "true",
        on_doc_change=_os.environ.get("MEMORY_PACK_TRIGGER_DOC", "true").lower() == "true",
        on_arch_change=_os.environ.get("MEMORY_PACK_TRIGGER_ARCH", "true").lower() == "true",
        on_dep_change=_os.environ.get("MEMORY_PACK_TRIGGER_DEP", "true").lower() == "true",
        min_interval_seconds=int(_os.environ.get("MEMORY_PACK_MIN_INTERVAL", "60")),
    )
    _auto_maintenance = AutoMaintenanceService(
        versioning=_versioning,
        generator=_generator,
        trigger_config=_maintenance_config,
        repo_path=_repo_path,
    )
    app.state._auto_maintenance_service = _auto_maintenance
    logger.info("auto_maintenance_initialized")

    _auto_maintenance.install_git_hook(_repo_path)

    _watcher_enabled = _os.environ.get("MEMORY_PACK_WATCHER", "true").lower() == "true"
    if _watcher_enabled:
        from gateway.memory.watcher import FileWatcher
        _enabled = set()
        if _maintenance_config.on_git_commit:
            _enabled.add("git_commit")
        if _maintenance_config.on_doc_change:
            _enabled.add("doc_change")
        if _maintenance_config.on_arch_change:
            _enabled.add("arch_change")
        if _maintenance_config.on_dep_change:
            _enabled.add("dep_change")

        _watcher = FileWatcher(
            repo_path=_repo_path,
            on_trigger=_auto_maintenance.handle_trigger,
            enabled_triggers=_enabled,
            cooldown_seconds=_maintenance_config.min_interval_seconds,
        )
        await _watcher.start()
        app.state._memory_pack_watcher = _watcher
        logger.info("file_watcher_started")

    if settings.semantic_cache_enabled:
        semantic_cache = SemanticCache(
            location=":memory:" if settings.qdrant_in_memory else None,
            url=None if settings.qdrant_in_memory else "localhost",
            port=6333,
            threshold=settings.semantic_cache_threshold,
        )
        app.state.semantic_cache = semantic_cache
        logger.info(
            "semantic_cache_initialized",
            extra={"threshold": settings.semantic_cache_threshold},
        )
    else:
        logger.info("semantic_cache_disabled")

    yield

    if hasattr(app.state, "_memory_pack_watcher"):
        await app.state._memory_pack_watcher.stop()
    if getattr(app.state, "_own_proxy", False) and hasattr(app.state, "proxy"):
        await app.state.proxy.client.aclose()
    if redis_client:
        await redis_client.aclose()
    logger.info("gateway_shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Memory Gateway",
        version="0.5.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.detail,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ):
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "message": str(exc),
                    "type": "invalid_request_error",
                    "code": "validation_error",
                }
            },
        )

    app.include_router(router)

    return app


app = create_app()
