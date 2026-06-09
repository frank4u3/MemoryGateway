# DeepSeek Prefix Cache Optimization Strategy

## 1. DeepSeek Prefix Cache Mechanics

DeepSeek applies a server-side prefix cache that discounts tokens when the leading portion of a new prompt exactly matches a previously seen prompt at the **byte/token level**. The cache is keyed by the first N tokens of the request body sent to `POST /chat/completions`. Key properties:

| Property | Detail |
|---|---|
| Matching granularity | Exact byte-for-byte at the tokenizer level |
| Discount | ~75% on cached prefix tokens (vs. uncached) |
| Scope | Per-model, per-deployment region |
| Cache window | Rolling — frequently seen prefixes stay hot |
| Breakage cause | Any token difference — whitespace, ordering, UUIDs, paths, timestamps, JSON key ordering |

**Implication**: Any two prompts that share a common prefix will get a discount on every token in that prefix. The goal is to **maximize the shared prefix across all requests from all agents**.

---

## 2. Current Request Flow — Gap Analysis

### 2.1 As-Built Flow

```
Agent → FastAPI Router → canonicalize_prompt() → local hash lookup (Redis exact cache)
                                                    ↓ miss
                                              proxy.chat_completion(raw request_data)
                                                    ↓
                                              DeepSeek (receives RAW, non-normalized messages)
```

### 2.2 Critical Finding: Canonicalizer Bypassed for Upstream

The canonicalizer runs only for the **local exact-cache hash** (`router.py:234-244`). The upstream request sent to DeepSeek at `router.py:315-317` uses `request_data` — the **original, un-normalized messages** from the agent.

This means:

- **UUIDs**, **absolute paths**, **timestamps**, **session IDs**, and **temp file references** are sent raw to DeepSeek
- Every agent-specific variation (different user home dirs, different session IDs, different timestamps) creates a **different token prefix**
- DeepSeek's server-side prefix cache gets **zero benefit** from the canonicalizer's work

### 2.3 Additional Gaps

| Gap | Impact |
|---|---|
| `build_upstream_messages()` exists but is never called | No structured prefix injected into upstream |
| JSON key ordering not guaranteed in request_data | Different key order → different bytes → cache miss |
| No explicit system prefix template shared across agents | Each agent sends different system prompts |
| Agent-specific content (paths, IDs, filenames) varies per request | Every request has unique leading tokens |
| No conversation window management for prefix stability | Long histories create unique trailing context |

---

## 3. Optimization Strategy

### 3.1 Tier-1: Fix the Canonicalization Gap (Highest ROI)

**Apply canonical content normalization to the upstream request body.**

Before:
```python
upstream_data, latency = await proxy.chat_completion(request_data, auth_header)
# request_data contains raw timestamps, paths, UUIDs
```

After:
```python
upstream_messages = build_upstream_messages(
    messages=request_data.get("messages", []),
    system_prefix=GLOBAL_SYSTEM_PREFIX,
    max_turns=settings.max_conversation_turns,
)
# Normalize content in upstream messages
for msg in upstream_messages:
    if isinstance(msg.get("content"), str):
        msg["content"] = normalize_text(msg["content"])

upstream_payload = {
    "model": request_data.get("model", "deepseek-chat"),
    "messages": upstream_messages,
    # Stable ordering of optional params
    "frequency_penalty": request_data.get("frequency_penalty"),
    "max_tokens": request_data.get("max_tokens"),
    "presence_penalty": request_data.get("presence_penalty"),
    "temperature": request_data.get("temperature"),
    "top_p": request_data.get("top_p"),
}
# Remove None values AFTER building dict with stable key order
upstream_payload = {k: v for k, v in upstream_payload.items() if v is not None}

upstream_data, latency = await proxy.chat_completion(upstream_payload, auth_header)
```

**Expected improvement**: +30-50% DeepSeek prefix cache hit rate on repeated queries across agents.

### 3.2 Tier-2: Structured Prefix Template

Inject a **fixed, repeatable prefix** at the beginning of every upstream request. The prefix must produce identical JSON bytes every time.

```
[GLOBAL SYSTEM PREFIX]     ← fixed text, same bytes every request
  ↓
[AGENT-SPECIFIC INSTRUCTIONS]  ← normalized (agent ID → placeholder)
  ↓
[PROJECT CONTEXT]          ← normalized (paths → <workspace>)
  ↓
[MEMORY PACK (truncated)]  ← first N tokens of current memory pack
  ↓
[FILE CONTEXT (sorted)]    ← alphabetically sorted, normalized paths
  ↓
[TRUNCATED CONVERSATION]   ← last max_turns turns only
  ↓
[CURRENT USER QUERY]       ← the actual question (variable suffix)
```

The **system prefix, agent instructions, and project context** are the same bytes for every single request. The memory pack changes only when the pack itself changes (nightly or on-demand). The variable tail is limited to file context + conversation + query.

### 3.3 Tier-3: JSON Serialization Stability

DeepSeek's prefix cache operates on the **raw HTTP request body bytes**. JSON serialization is notoriously unstable:

| Pitfall | Fix |
|---|---|
| Python dict key ordering (pre-3.7) | Use `OrderedDict` or sort keys |
| JSON key presence variation | Emit all keys (with `null` values) or strip uniformly |
| Whitespace variation | Use `json.dumps(separators=(',', ':'))` — no spaces |
| Unicode normalization | Ensure `ensure_ascii=False` and NFC-normalize all strings |
| Float representation | `temperature=0.1` serializes differently than `temperature=0.10` — always emit 1 decimal for 0.1-0.9 range |

**Required**: A deterministic JSON serializer that produces identical bytes for semantically identical payloads.

```python
def deterministic_json(payload: dict) -> bytes:
    """Produce cache-stable JSON bytes."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
```

### 3.4 Tier-4: Agent-Agnostic System Prompt

All agents (Hermes, OpenCode, Qoder, VSCode) currently send **different system prompts**. The gateway should:

1. **Strip agent-specific system prompts** from incoming messages
2. **Inject a canonical system prefix** that is identical for all agents
3. **Append agent-specific instructions** as a separate, normalized message

This ensures the first N bytes of every request are identical regardless of which agent sends them.

### 3.5 Tier-5: Conversation Window Management

Long conversations create unique trailing context that breaks prefix stability. Strategy:

- **Hard cap**: `max_turns=20` (configurable)
- **Rolling window**: Always keep the **last N turns** + **system prompt**
- **Memory pack as context**: Replace old conversation history with the memory pack (architecture, roadmap, current state) — this is stable across requests

---

## 4. Implementation Phases

| Phase | Change | Effort | Prefix Cache ROI |
|---|---|---|---|
| **P1** | Apply `normalize_text()` to upstream message content | 2 files, ~10 lines | +30-50% |
| **P2** | Inject canonical system prefix upstream | 1 file, ~15 lines | +20-30% |
| **P3** | Deterministic JSON serialization for upstream | 1 file, ~20 lines | +10-20% |
| **P4** | Agent-agnostic system prompt stripping | 1 file, ~30 lines | +10-15% |
| **P5** | Conversation window management + memory pack injection | 2 files, ~40 lines | +5-10% |

**Cumulative projected DeepSeek prefix cache hit improvement**: 65-95% of eligible tokens (prompts with shared context).

---

## 5. Measurement & Verification

### 5.1 Key Metrics

| Metric | How to measure |
|---|---|
| Upstream bytes shared across requests | Log `len(payload)` and shared prefix length (computed offline) |
| DeepSeek prefix cache tokens | DeepSeek API returns `prompt_tokens_details.cached_tokens` in usage (check API version) |
| Local exact cache hit rate | `/v1/metrics/cache` endpoint (already implemented) |
| Token savings per agent | `/v1/metrics/cost` endpoint (already implemented) |

### 5.2 A/B Testing

The gateway should support an `X-Prefix-Cache-Optimization` header to selectively enable/disable the optimization per-request for comparison.

---

## 6. Key Code Changes Required

### `gateway/canonicalizer.py`
- No changes needed (already has all normalization primitives)

### `gateway/router.py` (`_handle_non_streaming`)
- Replace `proxy.chat_completion(request_data, auth_header)` with canonicalized upstream payload
- Add `GLOBAL_SYSTEM_PREFIX` injection
- Add deterministic JSON serialization

### `gateway/proxy.py`
- Remove JSON serialization from here (move to caller for deterministic control)

### `gateway/config.py`
- Add `max_conversation_turns` setting
- Add `prefix_cache_enabled` toggle

### `gateway/metrics.py`
- Add `prefix_cache_shared_bytes` metric
- Add `upstream_request_size_bytes` metric
