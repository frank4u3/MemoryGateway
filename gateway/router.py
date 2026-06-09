import time
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from .cache.exact import ExactCache, tokens_saved_from_cached
from .canonicalizer import build_upstream_messages, canonicalize_prompt
from .config import settings
from .semantic_cache import SemanticCache, SemanticCacheEntry
from .artifact import (
    Artifact,
    ArtifactStore,
    ArtifactType,
    SearchArtifactRequest,
    SearchArtifactResponse,
    StoreArtifactRequest,
    StoreArtifactResponse,
    UpdateArtifactRequest,
    UpdateArtifactResponse,
)
from .learning import (
    Learning,
    LearningResponse,
    LearningStore,
    LearningType,
    SearchLearningRequest,
    SearchLearningResponse,
    StoreLearningRequest,
    StoreLearningResponse,
    UpdateLearningRequest,
    UpdateLearningResponse,
)
from .memory_layer import (
    CreateMemoryRequest,
    CreateMemoryResponse,
    MemoryLayerStore,
    MemoryScope,
    SearchMemoryRequest,
    SearchMemoryResponse,
    ShareMemoryRequest,
    ShareMemoryResponse,
    SourceType,
    UpdatePermissionsRequest,
    UpdatePermissionsResponse,
)
from .context import (
    ContextStore,
    ContextResponse,
    ContextSearchResult,
    ContextType,
    RegisterContextRequest,
    RegisterContextResponse,
    SearchContextRequest,
    SearchContextResponse,
    UpdateContextRequest,
    UpdateContextResponse,
    register_block,
)
from .indexer import (
    CodeIndexStore,
    IndexRequest,
    IndexResponse,
    SearchRequest,
    SearchResponse,
    SearchResult,
    index_repository,
    parse_file,
)
from .logger import get_logger
from .memory import (
    MemoryPack,
    MemoryResponse,
    MemoryStore,
    RebuildRequest,
    build_pack,
    generate_pack,
)
from .metrics import (
    CostCalculator,
    DASHBOARD_HTML,
    MetricsOverview,
    CacheMetrics,
    CostMetrics,
    active_requests,
    cache_hit_rate,
    cache_hits_total,
    cache_misses_total,
    cost_saved_total,
    cost_spent_total,
    get_prometheus_text,
    latency_seconds,
    requests_total,
    tokens_completion_total,
    tokens_prompt_total,
    tokens_saved_total,
)
from .models import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    GatewayInfo,
    Usage,
)
from .proxy import DeepSeekProxy, DeepSeekUpstreamError
from .stats import StatsTracker
from .telemetry import TelemetryService, TelemetryOverview

logger = get_logger()
router = APIRouter()

_cost_calculator = CostCalculator()


def _get_agent_id(request: Request) -> str:
    agent_id = (request.headers.get("X-Agent-ID") or "").strip().lower()
    if not agent_id:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "message": "X-Agent-ID header is required",
                    "type": "invalid_request_error",
                    "code": "missing_agent_id",
                }
            },
        )
    authorized = settings.get_authorized_agents()
    if agent_id not in authorized:
        logger.warning("unknown_agent_id", extra={"agent_id": agent_id})
    return agent_id


def _get_auth_header(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth and not settings.deepseek_api_key:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "message": "Authorization header is required",
                    "type": "invalid_request_error",
                    "code": "missing_auth",
                }
            },
        )
    return auth


def _is_cache_bypass(request: Request) -> bool:
    val = request.headers.get("X-Cache-Bypass", "").strip().lower()
    return val in ("true", "1", "yes")


@router.get("/v1/health")
async def health():
    return {
        "status": "ok",
        "components": {
            "gateway": {"status": "ok", "version": "0.5.0"},
            "redis": {"status": "unknown"},
            "deepseek": {"status": "unknown"},
            "qdrant": {"status": "unknown"},
        },
            "version": "0.5.0",
    }


@router.get("/v1/cache/stats")
async def cache_stats(request: Request):
    stats: StatsTracker = request.app.state.stats
    overall = await stats.get_stats()
    by_agent = await stats.get_agent_stats()
    cache: ExactCache = request.app.state.cache
    return {
        "overall": overall,
        "by_agent": by_agent,
        "cache_size": await cache.size(),
    }


@router.delete("/v1/cache/exact")
async def flush_exact_cache(request: Request):
    cache: ExactCache = request.app.state.cache
    deleted = await cache.delete()
    return {"flushed": deleted}


@router.post("/v1/chat/completions")
async def chat_completions(
    body: ChatCompletionRequest,
    request: Request,
):
    agent_id = _get_agent_id(request)
    auth_header = _get_auth_header(request)
    proxy: DeepSeekProxy = request.app.state.proxy
    cache: ExactCache = request.app.state.cache
    stats: StatsTracker = request.app.state.stats

    request_data = body.model_dump(exclude_none=True)
    if settings.baseline_mode:
        cache_enabled = False
        if hasattr(request.app.state, "semantic_cache"):
            semantic_cache_enabled = False
    else:
        cache_enabled = settings.cache_enabled and not _is_cache_bypass(request)

    logger.info(
        "chat_completion_request",
        extra={
            "agent_id": agent_id,
            "model": body.model,
            "stream": body.stream,
            "message_count": len(body.messages),
            "cache_enabled": cache_enabled,
        },
    )

    await stats.record_request(agent_id)
    requests_total.labels(agent_id=agent_id).inc()

    if body.stream:
        if cache_enabled:
            logger.info("streaming_cache_skipped", extra={"agent_id": agent_id})
        return _handle_streaming(request_data, auth_header, agent_id, proxy)

    semantic_cache: SemanticCache | None = request.app.state.semantic_cache if hasattr(request.app.state, "semantic_cache") else None

    return await _handle_non_streaming(
        request_data, auth_header, agent_id, proxy, cache, stats, cache_enabled, request, semantic_cache
    )


async def _handle_non_streaming(
    request_data: dict,
    auth_header: str,
    agent_id: str,
    proxy: DeepSeekProxy,
    cache: ExactCache,
    stats: StatsTracker,
    cache_enabled: bool,
    request: Request,
    semantic_cache: SemanticCache | None = None,
) -> dict:
    cache_key = None
    canonical_hash = None
    if cache_enabled:
        messages = request_data.get("messages", [])
        cp = canonicalize_prompt(
            messages=messages,
            model=request_data.get("model", "deepseek-chat"),
            temperature=request_data.get("temperature"),
            max_tokens=request_data.get("max_tokens"),
            top_p=request_data.get("top_p"),
            presence_penalty=request_data.get("presence_penalty"),
            frequency_penalty=request_data.get("frequency_penalty"),
        )
        cache_key = f"exact:{cp.canonical_hash}"
        canonical_hash = cp.canonical_hash

        cached = await cache.get(cache_key)
        if cached is not None:
            saved = tokens_saved_from_cached(cached)
            await stats.record_hit(agent_id, saved)
            await stats.record_cache_key_hit(cache_key)
            cache_hits_total.labels(agent_id=agent_id).inc()
            tokens_saved_total.inc(saved)
            cost_saved = _cost_calculator.savings_from_cached_tokens(saved)
            cost_saved_total.inc(cost_saved)
            await stats.record_cost(0, cost_saved)
            cached["x_gateway"] = _make_gateway_info(
                cache_tier="exact",
                cache_key=cache_key,
                cache_hit=True,
                tokens_saved=saved,
                canonical_hash=canonical_hash,
            )
            logger.info(
                "cache_hit",
                extra={
                    "agent_id": agent_id,
                    "cache_key": cache_key,
                    "canonical_hash": canonical_hash,
                    "tokens_saved": saved,
                },
            )
            return cached

        # Semantic cache lookup (if enabled)
        if semantic_cache is not None:
            semantic_result = semantic_cache.search(
                canonical_text=cp.canonical_text,
                model=request_data.get("model", "deepseek-chat"),
                temperature=request_data.get("temperature"),
                max_tokens=request_data.get("max_tokens"),
                top_p=request_data.get("top_p"),
            )
            if semantic_result is not None:
                saved = tokens_saved_from_cached(semantic_result.response)
                await stats.record_hit(agent_id, saved)
                await stats.record_cache_key_hit(cache_key)
                cache_hits_total.labels(agent_id=agent_id).inc()
                tokens_saved_total.inc(saved)
                cost_saved = _cost_calculator.savings_from_cached_tokens(saved)
                cost_saved_total.inc(cost_saved)
                await stats.record_cost(0, cost_saved)
                semantic_result.response["x_gateway"] = _make_gateway_info(
                    cache_tier="semantic",
                    cache_key=cache_key,
                    cache_hit=True,
                    tokens_saved=saved,
                    canonical_hash=canonical_hash,
                    semantic_hit=True,
                    similarity_score=round(semantic_result.score, 4),
                )
                logger.info(
                    "semantic_cache_hit",
                    extra={
                        "agent_id": agent_id,
                        "cache_key": cache_key,
                        "canonical_hash": canonical_hash,
                        "similarity_score": round(semantic_result.score, 4),
                        "tokens_saved": saved,
                    },
                )
                return semantic_result.response

    start = time.monotonic()
    try:
        upstream_data, upstream_latency = await proxy.chat_completion(
            request_data, auth_header
        )
    except DeepSeekUpstreamError as e:
        await stats.record_miss(agent_id)
        cache_misses_total.labels(agent_id=agent_id).inc()
        _log_and_raise_upstream_error(e, agent_id)
    except Exception as e:
        await stats.record_miss(agent_id)
        cache_misses_total.labels(agent_id=agent_id).inc()
        logger.error("upstream_error", extra={"error": str(e), "agent_id": agent_id})
        raise HTTPException(
            status_code=502,
            detail={
                "error": {
                    "message": f"DeepSeek upstream error: {e}",
                    "type": "upstream_error",
                    "code": "upstream_error",
                }
            },
        )
    total_latency = (time.monotonic() - start) * 1000
    latency_seconds.observe(total_latency / 1000)

    if cache_key:
        await cache.set(cache_key, upstream_data)
        # Store in semantic cache (if enabled)
        if semantic_cache is not None and canonical_hash:
            try:
                entry = SemanticCacheEntry(
                    canonical_hash=canonical_hash,
                    canonical_text=cp.canonical_text,
                    model=request_data.get("model", "deepseek-chat"),
                    response=upstream_data,
                    temperature=request_data.get("temperature"),
                    max_tokens=request_data.get("max_tokens"),
                    top_p=request_data.get("top_p"),
                )
                semantic_cache.store(entry)
            except Exception as exc:
                logger.warning("semantic_cache_store_error", extra={"error": str(exc)})

    await stats.record_miss(agent_id)
    cache_misses_total.labels(agent_id=agent_id).inc()

    usage = upstream_data.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    tokens_prompt_total.inc(prompt_tokens)
    tokens_completion_total.inc(completion_tokens)
    cost_spent = _cost_calculator.cost_for_tokens(prompt_tokens, completion_tokens)
    cost_spent_total.inc(cost_spent)
    await stats.record_tokens(prompt_tokens, completion_tokens)
    await stats.record_tokens_for_agent(agent_id, prompt_tokens, completion_tokens)
    await stats.record_cost(cost_spent, 0)
    await stats.record_cost_for_agent(agent_id, cost_spent, 0)
    await stats.record_latency(total_latency)
    response = ChatCompletionResponse(
        id=upstream_data.get("id", f"chatcmpl-{uuid.uuid4().hex[:12]}"),
        object="chat.completion",
        created=upstream_data.get("created", int(time.time())),
        model=upstream_data.get("model", request_data.get("model", "deepseek-chat")),
        choices=[
            Choice(
                index=choice["index"],
                message=choice.get("message"),
                finish_reason=choice.get("finish_reason"),
            )
            for choice in upstream_data.get("choices", [])
        ],
        usage=Usage(
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
        ),
        x_gateway=_make_gateway_info(
            cache_tier="miss",
            cache_key=cache_key,
            cache_hit=False,
            tokens_saved=0,
            latency_ms=round(total_latency, 1),
            canonical_hash=canonical_hash,
        ),
    )

    logger.info(
        "chat_completion_response",
        extra={
            "agent_id": agent_id,
            "model": response.model,
            "total_tokens": usage.get("total_tokens", 0),
            "latency_ms": round(total_latency, 1),
            "cache_tier": "miss",
            "canonical_hash": canonical_hash,
        },
    )

    return response.model_dump(exclude_none=True)


def _handle_streaming(
    request_data: dict,
    auth_header: str,
    agent_id: str,
    proxy: DeepSeekProxy,
):
    request_data = {**request_data, "stream": True}

    async def event_generator():
        try:
            async for chunk in proxy.chat_completion_stream(
                request_data, auth_header
            ):
                yield chunk
        except DeepSeekUpstreamError as e:
            logger.error(
                "upstream_stream_error",
                extra={
                    "status": e.status_code,
                    "body": e.body,
                    "agent_id": agent_id,
                },
            )
        except Exception as e:
            logger.error(
                "upstream_stream_error",
                extra={"error": str(e), "agent_id": agent_id},
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _log_and_raise_upstream_error(e: DeepSeekUpstreamError, agent_id: str):
    logger.error(
        "deepseek_upstream_error",
        extra={"status_code": e.status_code, "body": e.body, "agent_id": agent_id},
    )
    raise HTTPException(
        status_code=502,
        detail={
            "error": {
                "message": f"DeepSeek upstream error: {e.body[:200]}",
                "type": "upstream_error",
                "code": "upstream_error",
            }
        },
    )


def _make_gateway_info(
    cache_tier: str,
    cache_key: str | None,
    cache_hit: bool,
    tokens_saved: int,
    latency_ms: float = 0,
    canonical_hash: str | None = None,
    semantic_hit: bool = False,
    similarity_score: float | None = None,
) -> dict:
    return GatewayInfo(
        cache_tier=cache_tier,
        cache_key=cache_key,
        cache_hit=cache_hit,
        tokens_saved=tokens_saved,
        latency_ms=round(latency_ms, 1),
        canonical_hash=canonical_hash,
        semantic_hit=semantic_hit,
        similarity_score=similarity_score,
    ).model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# Metrics endpoints
# ---------------------------------------------------------------------------


@router.get("/v1/metrics")
async def prometheus_metrics():
    return get_prometheus_text()


@router.get("/v1/metrics/overview", response_model=MetricsOverview)
async def metrics_overview(request: Request):
    stats: StatsTracker = request.app.state.stats
    overall = await stats.get_stats()
    agents = await stats.get_agent_stats()
    return MetricsOverview(
        total_requests=overall.get("total_requests", 0),
        total_hits=overall.get("hits", 0),
        total_misses=overall.get("misses", 0),
        hit_rate_pct=overall.get("hit_rate_pct", 0.0),
        tokens_saved=overall.get("tokens_saved", 0),
        tokens_prompt=overall.get("tokens_prompt", 0),
        tokens_completion=overall.get("tokens_completion", 0),
        cost_saved_usd=overall.get("cost_saved_usd", 0.0),
        cost_spent_usd=overall.get("cost_spent_usd", 0.0),
        avg_latency_ms=overall.get("avg_latency_ms", 0.0),
        agent_breakdown=agents,
    )


@router.get("/v1/metrics/cache", response_model=CacheMetrics)
async def metrics_cache(request: Request):
    stats: StatsTracker = request.app.state.stats
    overall = await stats.get_stats()
    daily_hits = await stats.get_daily_series("hits")
    daily_misses = await stats.get_daily_series("misses")
    top_keys = await stats.get_top_cache_keys()

    daily_hit_rate = []
    for h, m in zip(daily_hits, daily_misses):
        total = h["value"] + m["value"]
        rate = round(h["value"] / total * 100, 1) if total > 0 else 0.0
        daily_hit_rate.append({"date": h["date"], "rate": rate})

    return CacheMetrics(
        hit_rate_pct=overall.get("hit_rate_pct", 0.0),
        daily_hits=daily_hits,
        daily_misses=daily_misses,
        daily_hit_rate=daily_hit_rate,
        top_keys=top_keys,
        total_hits=overall.get("hits", 0),
        total_misses=overall.get("misses", 0),
    )


@router.get("/v1/metrics/cost", response_model=CostMetrics)
async def metrics_cost(request: Request):
    stats: StatsTracker = request.app.state.stats
    overall = await stats.get_stats()

    daily_spent = await stats.get_daily_float_series("cost_spent")
    daily_saved = await stats.get_daily_float_series("cost_saved")
    daily_savings_list = []
    for s in daily_spent:
        saved_vals = [d for d in daily_saved if d["date"] == s["date"]]
        sv = saved_vals[0]["value"] if saved_vals else 0
        daily_savings_list.append(
            {"date": s["date"], "savings": round(sv - s["value"], 6)}
        )

    weekly_savings = _compute_weekly_savings(daily_savings_list)

    return CostMetrics(
        cost_spent_usd=overall.get("cost_spent_usd", 0.0),
        cost_saved_usd=overall.get("cost_saved_usd", 0.0),
        net_savings_usd=round(
            overall.get("cost_saved_usd", 0.0) - overall.get("cost_spent_usd", 0.0),
            6,
        ),
        daily_cost_spent=daily_spent,
        daily_cost_saved=daily_saved,
        daily_savings=daily_savings_list,
        weekly_savings=weekly_savings,
        estimated_input_cost_per_1m=_cost_calculator.input_price,
        estimated_output_cost_per_1m=_cost_calculator.output_price,
        total_prompt_tokens=overall.get("tokens_prompt", 0),
        total_completion_tokens=overall.get("tokens_completion", 0),
    )


@router.get("/v1/dashboard")
async def dashboard():
    from fastapi.responses import HTMLResponse

    return HTMLResponse(content=DASHBOARD_HTML)


# ---------------------------------------------------------------------------
# Telemetry endpoints
# ---------------------------------------------------------------------------


@router.get("/v1/telemetry/overview", response_model=TelemetryOverview)
async def telemetry_overview(request: Request):
    telemetry: TelemetryService = request.app.state.telemetry
    return await telemetry.get_overall_summary()


@router.get("/v1/telemetry/agents")
async def telemetry_agents(request: Request):
    telemetry: TelemetryService = request.app.state.telemetry
    overview = await telemetry.get_overall_summary()
    agents = sorted(
        overview.agents, key=lambda a: a.total_tokens, reverse=True
    )
    return {"agents": [a.model_dump() for a in agents]}


@router.get("/v1/telemetry/agents/{agent_id}")
async def telemetry_agent_detail(agent_id: str, request: Request):
    telemetry: TelemetryService = request.app.state.telemetry
    detail = await telemetry.get_agent_detail(agent_id)
    return detail.model_dump()


@router.get("/v1/telemetry/rankings")
async def telemetry_rankings(
    request: Request,
    sort_by: str = "composite",
):
    telemetry: TelemetryService = request.app.state.telemetry
    valid_sorts = {"composite", "token_consumption", "cache_efficiency", "cost"}
    if sort_by not in valid_sorts:
        sort_by = "composite"
    rankings = await telemetry.get_agent_rankings(sort_by=sort_by)
    return {"rankings": [r.model_dump() for r in rankings], "sort_by": sort_by}


@router.get("/v1/telemetry/dashboard")
async def telemetry_dashboard():
    from fastapi.responses import HTMLResponse
    from gateway.telemetry.dashboard import TELEMETRY_DASHBOARD_HTML

    return HTMLResponse(content=TELEMETRY_DASHBOARD_HTML)


def _compute_weekly_savings(daily: list[dict]) -> list[dict]:
    weekly = {}
    for entry in daily:
        from datetime import datetime as dt

        d = dt.strptime(entry["date"], "%Y-%m-%d")
        week_start = d.strftime("%Y-W%W")
        weekly[week_start] = weekly.get(week_start, 0) + entry["savings"]
    return [
        {"week": k, "savings": round(v, 6)}
        for k, v in sorted(weekly.items())
    ]


# ---------------------------------------------------------------------------
# Baseline / ROI endpoints
# ---------------------------------------------------------------------------


@router.get("/v1/telemetry/baseline")
async def baseline_get(request: Request):
    """Return today's live baseline snapshot."""
    baseline_service = getattr(request.app.state, "_baseline_service", None)
    if baseline_service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": "Baseline service not initialized",
                    "type": "service_unavailable",
                    "code": "baseline_not_ready",
                }
            },
        )
    snapshot = await baseline_service.snapshot_today()
    return snapshot or {"date": "unknown", "message": "No data collected"}


@router.get("/v1/telemetry/baseline/export")
async def baseline_export(request: Request, days: int = 7):
    """Export baseline data for ROI analysis (last N days)."""
    baseline_service = getattr(request.app.state, "_baseline_service", None)
    if baseline_service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": "Baseline service not initialized",
                    "type": "service_unavailable",
                    "code": "baseline_not_ready",
                }
            },
        )
    return await baseline_service.export_baseline(days=days)


@router.get("/v1/telemetry/baseline/range")
async def baseline_range(request: Request, days: int = 7):
    """Get stored baseline records for the last N days."""
    baseline_service = getattr(request.app.state, "_baseline_service", None)
    if baseline_service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": "Baseline service not initialized",
                    "type": "service_unavailable",
                    "code": "baseline_not_ready",
                }
            },
        )
    return await baseline_service.get_baseline_range(days=days)


@router.post("/v1/telemetry/baseline/snapshot")
async def baseline_snapshot(request: Request):
    """Store today's data as a permanent baseline snapshot."""
    baseline_service = getattr(request.app.state, "_baseline_service", None)
    if baseline_service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": "Baseline service not initialized",
                    "type": "service_unavailable",
                    "code": "baseline_not_ready",
                }
            },
        )
    snapshot = await baseline_service.snapshot_today()
    if snapshot is None:
        return {"status": "no_data", "message": "No telemetry data to snapshot"}
    return {"status": "stored", "snapshot": snapshot}


@router.post("/v1/telemetry/baseline/finalize")
async def baseline_finalize(request: Request):
    """Freeze all current daily snapshots as a permanent named baseline.

    Body: {"baseline_id": "week1"}

    Aggregates every daily snapshot into one frozen record for
    permanent comparison. Call this at the end of baseline collection
    so future optimizations compare against a fixed reference point.
    """
    baseline_service = getattr(request.app.state, "_baseline_service", None)
    if baseline_service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": "Baseline service not initialized",
                    "type": "service_unavailable",
                    "code": "baseline_not_ready",
                }
            },
        )

    try:
        body = await request.json()
        baseline_id = body.get("baseline_id", "").strip() if body else ""
    except Exception:
        baseline_id = ""

    if not baseline_id:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "message": "baseline_id is required in request body",
                    "type": "validation_error",
                    "code": "missing_baseline_id",
                }
            },
        )

    result = await baseline_service.finalize(baseline_id)
    if result is None:
        return {"status": "no_data", "message": "No daily snapshots to finalize"}
    return {"status": "finalized", **result}


@router.get("/v1/telemetry/baseline/finalized")
async def baseline_list_finalized(request: Request):
    """List all finalized baseline IDs."""
    baseline_service = getattr(request.app.state, "_baseline_service", None)
    if baseline_service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": "Baseline service not initialized",
                    "type": "service_unavailable",
                    "code": "baseline_not_ready",
                }
            },
        )
    ids = await baseline_service.list_finalized()
    return {"baselines": ids}


@router.get("/v1/telemetry/baseline/finalized/{baseline_id}")
async def baseline_get_finalized(baseline_id: str, request: Request):
    """Get a previously finalized baseline by ID."""
    baseline_service = getattr(request.app.state, "_baseline_service", None)
    if baseline_service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": "Baseline service not initialized",
                    "type": "service_unavailable",
                    "code": "baseline_not_ready",
                }
            },
        )
    result = await baseline_service.get_finalized(baseline_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "message": f"Baseline '{baseline_id}' not found",
                    "type": "not_found",
                    "code": "baseline_not_found",
                }
            },
        )
    return result


@router.get("/v1/telemetry/baseline/compare/{baseline_id}")
async def baseline_compare(baseline_id: str, request: Request):
    """Compare current day's stats against a frozen baseline.

    Returns absolute deltas and percentage change for tokens and cost.
    """
    baseline_service = getattr(request.app.state, "_baseline_service", None)
    if baseline_service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": "Baseline service not initialized",
                    "type": "service_unavailable",
                    "code": "baseline_not_ready",
                }
            },
        )
    result = await baseline_service.compare_to_finalized(baseline_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "message": f"Baseline '{baseline_id}' not found",
                    "type": "not_found",
                    "code": "baseline_not_found",
                }
            },
        )
    return result


# ---------------------------------------------------------------------------
# Memory pack endpoints
# ---------------------------------------------------------------------------


@router.post("/v1/memory/rebuild", response_model=MemoryResponse)
async def memory_rebuild(body: RebuildRequest, request: Request):
    store: MemoryStore = request.app.state.memory_store

    files = generate_pack(path=body.path, files=body.files)
    pack = build_pack(files)
    store.save(pack)

    return MemoryResponse(
        version=pack.version,
        created_at=pack.created_at,
        checksum=pack.checksum,
        file_count=len(pack.files),
        files={f.filename: f.content for f in pack.files},
    )


@router.get("/v1/memory/current", response_model=MemoryResponse)
async def memory_current(request: Request):
    store: MemoryStore = request.app.state.memory_store
    pack = store.current()

    if pack is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "message": "No memory pack found. POST /v1/memory/rebuild first.",
                    "type": "not_found",
                    "code": "no_memory_pack",
                }
            },
        )

    return MemoryResponse(
        version=pack.version,
        created_at=pack.created_at,
        checksum=pack.checksum,
        file_count=len(pack.files),
        files={f.filename: f.content for f in pack.files},
    )


# ---------------------------------------------------------------------------
# Memory Pack Auto-Maintenance endpoints
# ---------------------------------------------------------------------------


@router.post("/v1/memory/pack/generate")
async def memory_pack_generate(request: Request):
    """Trigger memory pack regeneration. Accepts optional JSON body with trigger_type.

    Body: {"trigger_type": "git_commit|doc_change|arch_change|dep_change|manual"}
    Without a body, forces manual regeneration.
    """
    from gateway.memory.auto_maintenance import AutoMaintenanceService

    service: AutoMaintenanceService = getattr(
        request.app.state, "_auto_maintenance_service", None
    )
    if service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": "Auto-maintenance service not initialized",
                    "type": "service_unavailable",
                    "code": "maintenance_not_ready",
                }
            },
        )

    trigger_type = "manual"
    force = True
    try:
        body = await request.json()
        if body and isinstance(body, dict):
            tt = body.get("trigger_type", "").strip()
            if tt:
                trigger_type = tt
                force = False
    except Exception:
        pass

    try:
        if force:
            version_id = await service.generate_now(trigger_type=trigger_type)
        else:
            version_id = await service.handle_trigger(trigger_type)
    except Exception as exc:
        logger.error(
            "memory_pack_generate_failed",
            extra={"error": str(exc)},
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "message": f"Memory pack generation failed: {exc}",
                    "type": "internal_error",
                    "code": "generation_failed",
                }
            },
        )

    if version_id is None:
        return {
            "version_id": None,
            "trigger_type": trigger_type,
            "message": "Memory pack generation skipped (throttled or trigger disabled)",
        }

    return {
        "version_id": version_id,
        "trigger_type": trigger_type,
        "message": "Memory pack generated successfully",
    }


@router.get("/v1/memory/pack/versions")
async def memory_pack_list_versions(request: Request):
    """List all memory pack versions."""
    from gateway.memory.versioning import MemoryPackVersioning

    versioning: MemoryPackVersioning = getattr(
        request.app.state, "_memory_pack_versioning", None
    )
    if versioning is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": "Memory pack versioning not initialized",
                    "type": "service_unavailable",
                    "code": "versioning_not_ready",
                }
            },
        )

    versions = versioning.list_versions()
    current_id = versioning.get_current_version_id()
    return {
        "versions": versions,
        "current_version": current_id,
        "total": len(versions),
    }


@router.get("/v1/memory/pack/versions/{version_id}")
async def memory_pack_get_version(version_id: str, request: Request):
    """Get a specific version's content."""
    from gateway.memory.versioning import MemoryPackVersioning

    versioning: MemoryPackVersioning = getattr(
        request.app.state, "_memory_pack_versioning", None
    )
    if versioning is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": "Memory pack versioning not initialized",
                    "type": "service_unavailable",
                    "code": "versioning_not_ready",
                }
            },
        )

    mpv = versioning.get_version(version_id)
    if mpv is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "message": f"Version '{version_id}' not found",
                    "type": "not_found",
                    "code": "version_not_found",
                }
            },
        )

    return {
        "version_id": mpv.version_id,
        "manifest": mpv.manifest,
        "files": mpv.read_all_files(),
    }


@router.get("/v1/memory/pack/diff/{version_id}")
async def memory_pack_diff(version_id: str, request: Request):
    """Get diff between a version and current."""
    from gateway.memory.versioning import MemoryPackVersioning

    versioning: MemoryPackVersioning = getattr(
        request.app.state, "_memory_pack_versioning", None
    )
    if versioning is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": "Memory pack versioning not initialized",
                    "type": "service_unavailable",
                    "code": "versioning_not_ready",
                }
            },
        )

    current = versioning.get_current()
    if current is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "message": "No current memory pack found",
                    "type": "not_found",
                    "code": "no_current_pack",
                }
            },
        )

    target = versioning.get_version(version_id)
    if target is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "message": f"Version '{version_id}' not found",
                    "type": "not_found",
                    "code": "version_not_found",
                }
            },
        )

    from gateway.memory.diff import diff_packs

    report = diff_packs(
        old_files=target.read_all_files(),
        new_files=current.read_all_files(),
        from_version=version_id,
        to_version=current.version_id,
    )

    return report.model_dump()


@router.post("/v1/memory/pack/rollback/{version_id}")
async def memory_pack_rollback(version_id: str, request: Request):
    """Rollback to a previous version."""
    from gateway.memory.auto_maintenance import AutoMaintenanceService

    service: AutoMaintenanceService = getattr(
        request.app.state, "_auto_maintenance_service", None
    )
    if service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": "Auto-maintenance service not initialized",
                    "type": "service_unavailable",
                    "code": "maintenance_not_ready",
                }
            },
        )

    success = await service.rollback(version_id)
    if not success:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "message": f"Version '{version_id}' not found",
                    "type": "not_found",
                    "code": "version_not_found",
                }
            },
        )

    return {
        "version_id": version_id,
        "message": f"Rolled back to version {version_id}",
    }


@router.get("/v1/memory/pack/current")
async def memory_pack_current(request: Request):
    """Get the current memory pack content."""
    from gateway.memory.versioning import MemoryPackVersioning

    versioning: MemoryPackVersioning = getattr(
        request.app.state, "_memory_pack_versioning", None
    )
    if versioning is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": "Memory pack versioning not initialized",
                    "type": "service_unavailable",
                    "code": "versioning_not_ready",
                }
            },
        )

    current = versioning.get_current()
    if current is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "message": "No current memory pack found",
                    "type": "not_found",
                    "code": "no_current_pack",
                }
            },
        )

    return {
        "version_id": current.version_id,
        "manifest": current.manifest,
        "files": current.read_all_files(),
    }


# ---------------------------------------------------------------------------
# Context registration endpoints
# ---------------------------------------------------------------------------


@router.post("/v1/context/register", response_model=RegisterContextResponse)
async def context_register(body: RegisterContextRequest, request: Request):
    store: ContextStore = request.app.state.context_store

    block = register_block(
        store=store,
        type_=body.type,
        title=body.title,
        content=body.content,
        tags=body.tags,
        source=body.source,
    )

    return RegisterContextResponse(
        id=block.id,
        type=block.type,
        title=block.title,
        version=block.version,
        message="Context block registered",
    )


@router.post("/v1/context/update", response_model=UpdateContextResponse)
async def context_update(body: UpdateContextRequest, request: Request):
    store: ContextStore = request.app.state.context_store

    updates = {}
    if body.type is not None:
        updates["type"] = body.type
    if body.title is not None:
        updates["title"] = body.title
    if body.content is not None:
        updates["content"] = body.content
    if body.tags is not None:
        updates["tags"] = body.tags

    block = store.update(body.id, **updates)
    if block is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "message": f"Context block '{body.id}' not found",
                    "type": "not_found",
                    "code": "context_not_found",
                }
            },
        )

    return UpdateContextResponse(
        id=block.id,
        version=block.version,
        message="Context block updated",
    )


@router.get("/v1/context/{context_id}", response_model=ContextResponse)
async def context_get(context_id: str, request: Request):
    store: ContextStore = request.app.state.context_store
    block = store.get(context_id)

    if block is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "message": f"Context block '{context_id}' not found",
                    "type": "not_found",
                    "code": "context_not_found",
                }
            },
        )

    return ContextResponse(
        id=block.id,
        type=block.type,
        title=block.title,
        content=block.content,
        tags=block.tags,
        source=block.source,
        created_at=block.created_at,
        updated_at=block.updated_at,
        version=block.version,
    )


@router.post("/v1/context/search", response_model=SearchContextResponse)
async def context_search(body: SearchContextRequest, request: Request):
    store: ContextStore = request.app.state.context_store

    results = store.search(
        query=body.query,
        type_filter=body.type_filter,
        top_k=body.top_k,
    )

    return SearchContextResponse(
        results=results,
        query=body.query,
        total_hits=len(results),
    )


# ---------------------------------------------------------------------------
# Artifact endpoints
# ---------------------------------------------------------------------------


@router.post("/v1/artifact/store", response_model=StoreArtifactResponse)
async def artifact_store(body: StoreArtifactRequest, request: Request):
    store: ArtifactStore = request.app.state.artifact_store
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    artifact = Artifact(
        id=uuid.uuid4().hex[:16],
        type=body.type,
        title=body.title,
        content=body.content,
        creator_agent=body.creator_agent,
        git_commit=body.git_commit,
        tags=body.tags,
        project=body.project,
        created_at=now,
        updated_at=now,
        version=1,
    )
    saved = await store.save(artifact)
    if not saved:
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "message": "Failed to store artifact",
                    "type": "internal_error",
                    "code": "artifact_store_failed",
                }
            },
        )
    return StoreArtifactResponse(
        id=artifact.id,
        type=artifact.type,
        title=artifact.title,
        version=artifact.version,
        message="Artifact stored",
    )


@router.post("/v1/artifact/update", response_model=UpdateArtifactResponse)
async def artifact_update(body: UpdateArtifactRequest, request: Request):
    store: ArtifactStore = request.app.state.artifact_store

    updates = {}
    if body.type is not None:
        updates["type"] = body.type
    if body.title is not None:
        updates["title"] = body.title
    if body.content is not None:
        updates["content"] = body.content
    if body.git_commit is not None:
        updates["git_commit"] = body.git_commit
    if body.tags is not None:
        updates["tags"] = body.tags
    if body.project is not None:
        updates["project"] = body.project

    artifact = await store.update(body.id, **updates)
    if artifact is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "message": f"Artifact '{body.id}' not found",
                    "type": "not_found",
                    "code": "artifact_not_found",
                }
            },
        )

    return UpdateArtifactResponse(
        id=artifact.id,
        version=artifact.version,
        message="Artifact updated",
    )


@router.post("/v1/artifact/search", response_model=SearchArtifactResponse)
async def artifact_search(body: SearchArtifactRequest, request: Request):
    store: ArtifactStore = request.app.state.artifact_store

    results = await store.search(
        query=body.query,
        type_filter=body.type_filter,
        top_k=body.top_k,
        use_semantic=body.use_semantic,
    )

    return SearchArtifactResponse(
        results=results,
        query=body.query,
        total_hits=len(results),
    )


# ---------------------------------------------------------------------------
# Learning endpoints
# ---------------------------------------------------------------------------


@router.post("/v1/learning/store", response_model=StoreLearningResponse)
async def learning_store(body: StoreLearningRequest, request: Request):
    store: LearningStore = request.app.state.learning_store
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    learning = Learning(
        id=uuid.uuid4().hex[:16],
        type=body.type,
        title=body.title,
        content=body.content,
        source_issue=body.source_issue,
        resolved_by=body.resolved_by,
        tags=body.tags,
        project=body.project,
        created_at=now,
        updated_at=now,
        version=1,
    )
    saved = await store.save(learning)
    if not saved:
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "message": "Failed to store learning",
                    "type": "internal_error",
                    "code": "learning_store_failed",
                }
            },
        )
    return StoreLearningResponse(
        id=learning.id,
        type=learning.type,
        title=learning.title,
        version=learning.version,
        message="Learning stored",
    )


@router.post("/v1/learning/update", response_model=UpdateLearningResponse)
async def learning_update(body: UpdateLearningRequest, request: Request):
    store: LearningStore = request.app.state.learning_store

    updates = {}
    if body.type is not None:
        updates["type"] = body.type
    if body.title is not None:
        updates["title"] = body.title
    if body.content is not None:
        updates["content"] = body.content
    if body.source_issue is not None:
        updates["source_issue"] = body.source_issue
    if body.resolved_by is not None:
        updates["resolved_by"] = body.resolved_by
    if body.tags is not None:
        updates["tags"] = body.tags
    if body.project is not None:
        updates["project"] = body.project

    learning = await store.update(body.id, **updates)
    if learning is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "message": f"Learning '{body.id}' not found",
                    "type": "not_found",
                    "code": "learning_not_found",
                }
            },
        )

    return UpdateLearningResponse(
        id=learning.id,
        version=learning.version,
        message="Learning updated",
    )


@router.post("/v1/learning/search", response_model=SearchLearningResponse)
async def learning_search(body: SearchLearningRequest, request: Request):
    store: LearningStore = request.app.state.learning_store

    results = await store.search(
        query=body.query,
        type_filter=body.type_filter,
        top_k=body.top_k,
        use_semantic=body.use_semantic,
    )

    return SearchLearningResponse(
        results=results,
        query=body.query,
        total_hits=len(results),
    )


@router.get("/v1/learning/{learning_id}", response_model=LearningResponse)
async def learning_get(learning_id: str, request: Request):
    store: LearningStore = request.app.state.learning_store
    learning = await store.get(learning_id)

    if learning is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "message": f"Learning '{learning_id}' not found",
                    "type": "not_found",
                    "code": "learning_not_found",
                }
            },
        )

    return LearningResponse(
        id=learning.id,
        type=learning.type,
        title=learning.title,
        content=learning.content,
        source_issue=learning.source_issue,
        resolved_by=learning.resolved_by,
        tags=learning.tags,
        project=learning.project,
        created_at=learning.created_at,
        updated_at=learning.updated_at,
        version=learning.version,
    )


# ---------------------------------------------------------------------------
# Shared Agent Memory Layer endpoints
# ---------------------------------------------------------------------------


@router.post("/v1/memory/share", response_model=ShareMemoryResponse)
async def memory_share(body: ShareMemoryRequest, request: Request):
    layer: MemoryLayerStore = request.app.state.memory_layer
    agent_id = _get_agent_id(request)

    record_id = await layer.share(
        source_type=body.source_type,
        source_id=body.source_id,
        scope=body.scope,
        scope_value=body.scope_value,
        permissions=body.permissions,
        creator_agent=agent_id,
    )
    if record_id is None:
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "message": "Failed to share memory",
                    "type": "internal_error",
                    "code": "memory_share_failed",
                }
            },
        )

    return ShareMemoryResponse(
        id=record_id,
        source_type=body.source_type,
        source_id=body.source_id,
        scope=body.scope,
        message="Memory shared",
    )


@router.post("/v1/memory/context", response_model=SearchMemoryResponse)
async def memory_search(body: SearchMemoryRequest, request: Request):
    layer: MemoryLayerStore = request.app.state.memory_layer

    results = await layer.search(
        query=body.query,
        agent_id=body.agent_id,
        scope_filter=body.scope_filter,
        source_filter=body.source_filter,
        project_filter=body.project_filter,
        top_k=body.top_k,
    )

    return SearchMemoryResponse(
        results=results,
        query=body.query,
        agent_id=body.agent_id,
        total_hits=len(results),
    )


@router.get("/v1/memory/agent/{agent_id}/context", response_model=SearchMemoryResponse)
async def memory_agent_context(agent_id: str, request: Request):
    layer: MemoryLayerStore = request.app.state.memory_layer

    results = await layer.get_agent_context(agent_id)

    return SearchMemoryResponse(
        results=results,
        query="",
        agent_id=agent_id,
        total_hits=len(results),
    )


@router.post("/v1/memory/permissions", response_model=UpdatePermissionsResponse)
async def memory_permissions(body: UpdatePermissionsRequest, request: Request):
    layer: MemoryLayerStore = request.app.state.memory_layer
    agent_id = _get_agent_id(request)

    record = await layer.update_permissions(
        record_id=body.record_id,
        permissions=body.permissions,
        agent_id=agent_id,
    )
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "message": f"Memory record '{body.record_id}' not found or permission denied",
                    "type": "not_found",
                    "code": "memory_not_found",
                }
            },
        )

    return UpdatePermissionsResponse(
        id=record.id,
        message="Permissions updated",
    )


@router.delete("/v1/memory/{record_id}")
async def memory_delete(record_id: str, request: Request):
    layer: MemoryLayerStore = request.app.state.memory_layer
    agent_id = _get_agent_id(request)

    deleted = await layer.delete_record(record_id, agent_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "message": f"Memory record '{record_id}' not found or permission denied",
                    "type": "not_found",
                    "code": "memory_not_found",
                }
            },
        )

    return {"deleted": True, "id": record_id}


# ---------------------------------------------------------------------------
# Indexer endpoints
# ---------------------------------------------------------------------------


@router.post("/v1/index", response_model=IndexResponse)
async def index_endpoint(body: IndexRequest, request: Request):
    store: CodeIndexStore = request.app.state.index_store

    if body.path:
        symbols = index_repository(body.path)
    elif body.files:
        symbols = []
        for f in body.files:
            file_path = f.get("path", "")
            content = f.get("content", "")
            symbols.extend(parse_file(file_path, content))
    else:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "message": "Provide 'path' or 'files'",
                    "type": "invalid_request_error",
                    "code": "missing_index_source",
                }
            },
        )

    if not symbols:
        return IndexResponse(
            indexed_files=0,
            indexed_symbols=0,
            collection="code_index",
        )

    files_indexed = len({s.file_path for s in symbols})
    count = store.index_symbols(symbols)

    return IndexResponse(
        indexed_files=files_indexed,
        indexed_symbols=count,
        collection="code_index",
    )


@router.post("/v1/search", response_model=SearchResponse)
async def search_endpoint(body: SearchRequest, request: Request):
    store: CodeIndexStore = request.app.state.index_store

    results = store.search(
        query=body.query,
        top_k=body.top_k,
        filter_=body.filter_,
        collection=body.collection,
    )

    return SearchResponse(
        results=results,
        query=body.query,
        total_hits=len(results),
    )
