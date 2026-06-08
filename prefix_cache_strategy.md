# Prefix Cache Strategy — Memory Gateway

**Date:** 2026-06-08
**Target:** Maximize DeepSeek provider-side prefix-cache reuse

---

## 1. Current State Analysis

### 1.1 The Critical Gap

The canonicalizer normalizes prompts for the Redis exact cache key, but **the canonicalized prompt is never sent to DeepSeek**. At `gateway/router.py:314`:

```python
upstream_data, upstream_latency = await proxy.chat_completion(
    request_data, auth_header  # ← original agent request, NOT canonicalized
)
```

`request_data` is `body.model_dump(exclude_none=True)` — the raw, un-normalized request from the agent. This means:

| Component | Sees canonicalized prompt? | Benefits? |
|-----------|--------------------------|-----------|
| Redis exact cache | Yes (hash key) | Exact-match Redis hits |
| DeepSeek prefix cache | **No** | **Zero cross-agent prefix reuse** |
| DeepSeek semantic cache | No (raw text embedded) | Reduced semantic hit rate |

### 1.2 Prompt Structure Variability

Agent prompts vary in 5 dimensions:

| Dimension | Variability | Impact on Prefix Cache |
|-----------|-------------|----------------------|
| **System prompt** | Each agent has unique instructions, injection order varies | High — different first tokens = different prefix |
| **File paths** | Per-user workspace paths (e.g., `/home/alice/` vs `/home/bob/`) | High — break prefix after first path mention |
| **Timestamps** | Every request has unique datetime strings | High — timestamps in system/user messages |
| **Sampler params** | `temperature`, `top_p`, `max_tokens` differ by agent | Medium — changes JSON body, different hash |
| **Conversation history** | Accumulates across turns, unique per session | Medium — growing prefix, rarely repeats |

### 1.3 Redis Cache Key Composition

Current hash includes: `messages` (after canonicalization) + `model` + `temperature` + `max_tokens` + `top_p` + `presence_penalty` + `frequency_penalty`.

Problem: Two agents asking the same question with `temperature=0.1` vs `temperature=0.2` produce different cache keys. DeepSeek prefix cache also sees different JSON bodies.

---

## 2. Strategy

### 2.1 Principle: Stable Prefix, Variable Suffix

DeepSeek's prefix cache works at the token level. The first N tokens of a request are compared to previously seen prefixes. If matched, the KV cache for those tokens is reused (≈75% discount on prompt tokens).

The strategy is to **maximize the number of identical leading tokens across requests**:

```
┌─────────────────────────────────────────────────────┐
│  STABLE PREFIX (identical across requests)           │
│  ┌────────────────────────────────────────────┐     │
│  │ System: <canonical system prompt>          │     │
│  │ System: <project context blocks>           │     │
│  │ System: <memory pack summaries>            │     │
│  │ System: <repository intelligence context>  │     │
│  └────────────────────────────────────────────┘     │
│  VARIABLE SUFFIX (changes per request)               │
│  ┌────────────────────────────────────────────┐     │
│  │ User: <current query>                      │     │
│  │ Assistant: <generated response>            │     │
│  └────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────┘
```

### 2.2 Tiered Cache Approach

```
Request
  │
  ├─► Redis Exact Cache (SHA-256 hash of canonicalized prompt)
  │   Purpose: Identical requests return instantly
  │   Hit rate: Depends on request repetition
  │
  ├─► DeepSeek Prefix Cache (natural token prefix matching)
  │   Purpose: Stable leading system messages hit cached KV
  │   Hit rate: Function of prefix stability
  │
  └─► Semantic Cache (Qdrant cosine similarity, ≥0.98)
      Purpose: Near-duplicate queries (disabled by default)
```

### 2.3 Specific Optimizations

#### O-1: Send canonicalized prompt to DeepSeek

The `_handle_non_streaming` function must replace `request_data` with the canonicalized message structure before forwarding to DeepSeek. This is the single highest-impact change.

**Implementation:**
```python
# In _handle_non_streaming, after canonicalization:
if cache_enabled:
    # Build canonical upstream request from canonicalized messages
    upstream_payload = {
        **request_data,
        "messages": cp.canonical_messages,  # ← canonicalized, normalized
    }
else:
    upstream_payload = request_data
```

**Projected gain:** All agents asking the same question with different paths/timestamps produce identical first tokens → DeepSeek prefix cache fires.

#### O-2: Inject canonical system prefix

The gateway should inject an agent-agnostic system prefix before the agent's own messages. This prefix is identical for every request from every agent.

**Template:**
```
You are an AI coding assistant. You operate in a multi-agent environment.
Project: <project_name>
Language: <primary_language>
Framework: <framework>
```

This prefix becomes the first ~50 tokens of every prompt → guaranteed prefix-cache hit for the KV of these tokens.

**Projected gain:** First 50+ tokens always cached across all requests.

#### O-3: Normalize sampler parameters

Map agent-specific parameter values to canonical defaults in the upstream request:

| Parameter | Agent Values | Canonical Value |
|-----------|-------------|-----------------|
| `temperature` | 0.0–0.3 | 0.1 (round to nearest 0.1) |
| `top_p` | 0.8–1.0 | 1.0 |
| `max_tokens` | 1024–16384 | Keep as-is (affects response, not prefix) |
| `frequency_penalty` | 0.0–0.5 | 0.0 |
| `presence_penalty` | 0.0 | 0.0 |

Apply quantization: round `temperature` to 1 decimal place, `top_p` to 1 decimal place. This makes e.g. `temperature=0.15` and `temperature=0.1` map to the same upstream parameter.

**Projected gain:** Reduces cache key variants by ~60% for parameter-sensitive keys.

#### O-4: Structured conversation window

Implement the `max_turns` parameter that is already in `canonicalize_messages()` but never used. Truncate conversation history to the last N turns (default: 10). The system messages (stable prefix) remain, while old conversation turns are dropped.

```python
cp = canonicalize_prompt(
    messages=messages,
    max_turns=10,  # ← currently None, unlimited
    ...
)
```

**Projected gain:** Prevents growing prompt from invalidating prefix cache. History beyond N turns doesn't affect current prefix.

#### O-5: Inject memory pack and context blocks into the system prefix

The gateway already has `ContextStore` and `MemoryStore` populated with:
- Project architecture, roadmap, coding rules
- Repository summaries
- Active task state

This content should be injected as system messages **after** the global system prefix but **before** the conversation history. This makes it part of the stable cached prefix.

**Projected gain:** Architecture, rules, and state information cached in DeepSeek's prefix — not charged on every request.

#### O-6: Conversation summary compression

When conversation history exceeds `max_turns`, compress older turns into a single summary:
```
System: Previous conversation summary: <compressed summary>
User: <most recent query>
```

This replaces N history turns with 1 summary message, keeping the prompt consistently sized.

---

## 3. Projected Improvements

| Optimization | Current | Projected | Mechanism |
|-------------|---------|-----------|-----------|
| Canonical upstream | Raw agent prompt | Normalized prompt | Direct prefix token match across agents |
| Global system prefix | Agent-specific | Agent-agnostic + project context | First 50–200 tokens identical |
| Sampler quantization | Raw parameters | Rounded parameters | Fewer unique cache key combinations |
| Conversation window | Unlimited | Last 10 turns | Prevents prompt size drift |
| Memory pack injection | Not sent | Included as system messages | Stable context in prefix |
| **Compound effect** | **No cross-agent prefix hits** | **40–65% estimated prefix reuse** | |

---

## 4. Revised Request Flow

```
Agent sends raw request
    │
    ▼
[Parse + Validate]
    │
    ▼
[Canonicalize Messages]
    ├─ Normalize UUIDs/timestamps/paths
    ├─ Sort: system first
    ├─ Deduplicate system messages
    ├─ Inject global system prefix (new)
    ├─ Inject memory pack / context blocks (new)
    └─ Truncate to max_turns (new)
    │
    ▼
[Generate Cache Key]
    ├─ Include canonical messages + quantized params
    ├─ Redis lookup
    └─ HIT → return cached
    │ MISS
    ▼
[Build DeepSeek Payload]
    ├─ Use canonicalized messages (CHANGED)
    ├─ Quantize sampler parameters (CHANGED)
    └─ Forward to DeepSeek
    │
    ▼
[Store Response]
    ├─ Redis exact cache
    └─ Semantic cache (if enabled)
    │
    ▼
[Return to agent]
```

---

## 5. Key Metrics to Track

| Metric | How | Current Baseline |
|--------|-----|-----------------|
| Upstream prompt tokens | `usage.prompt_tokens` | Per-request reporting |
| Prefix-cache hit tokens | DeepSeek `usage.prompt_tokens_details.cached_tokens` (if available) | Not tracked |
| Average prompt length | `stats.avg_prompt_tokens` | Not tracked |
| Cross-agent exact hit rate | Redis hits where agent_id differs from cached entry's agent | 0% |
| Parameter quantization efficiency | Unique cache keys before/after rounding | ~N unique / agent |

---

## 6. Implementation Order

| Step | Effort | Impact | Risk |
|------|--------|--------|------|
| 1. Send canonicalized messages upstream | 4h | Highest | Low (canonicalizer tested) |
| 2. Inject global system prefix | 2h | High | Low (only adds, never removes) |
| 3. Quantize sampler parameters | 1h | Medium | Low (backward-compatible cache key change) |
| 4. Wire max_turns (default 10) | 0.5h | Medium | Low |
| 5. Inject context blocks + memory pack | 4h | High | Medium (depends on store population) |
| 6. Conversation summary compression | 6h | Medium | Medium (LLM call overhead) |
