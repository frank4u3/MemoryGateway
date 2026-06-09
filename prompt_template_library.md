# Prompt Template Library — Prefix Cache Maximization

## 1. Canonical Upstream Payload Layout

Every request sent to DeepSeek must follow this exact JSON structure with deterministic key ordering and byte-identical prefix. The leading bytes (the first ~50-80% of the prompt) must be **identical across all agents and all requests**.

```
PAYLOAD FRAME (JSON keys in deterministic order)
├── "model": "deepseek-chat"                    ← fixed
├── "messages": [
│   ├── #0 [CANONICAL SYSTEM PREFIX]            ← global, agent-agnostic (same bytes always)
│   ├── #1 [AGENT INSTRUCTIONS]                 ← normalized agent ID, stable
│   ├── #2 [PROJECT CONTEXT]                    ← <workspace>-normalized paths
│   ├── #3 [MEMORY PACK SNIPPET]               ← first ~2000 tokens of memory pack
│   ├── #4 [FILE CONTEXT BLOCKS]               ← sorted, normalized
│   ├── #5 [CONVERSATION TURNS]                ← last N turns only
│   └── #6 [CURRENT USER QUERY]               ← variable suffix
│   ]
├── "max_tokens": <value or null>
├── "model": "deepseek-chat"
├── "presence_penalty": <value or null>
├── "temperature": <value or null>
├── "top_p": <value or null>
└── (ALL KEYS ALWAYS PRESENT — null if unset, for deterministic byte length)
```

---

## 2. Global Canonical System Prefix

The first message in every request. **Must be byte-identical for all agents.**

```python
GLOBAL_SYSTEM_PREFIX = [
    {
        "role": "system",
        "content": (
            "You are an AI coding assistant operating in a local development environment.\n"
            "Your workspace is <workspace>.\n"
            "Current timestamp: <timestamp>.\n"
            "Follow these rules:\n"
            "1. Write clean, idiomatic code following the project's conventions.\n"
            "2. Use existing libraries and patterns from the codebase.\n"
            "3. Only produce the code or explanation requested — no extra commentary.\n"
            "4. When referencing files, use paths relative to <workspace>.\n"
            "5. Never include placeholder credentials or secrets."
        )
    }
]
```

**Rationale**: This prefix is free of agent names, user-specific paths, session IDs, and timestamps. The `<timestamp>` placeholder is substituted with a **relative** format like "now" (which normalizes to `<timestamp>`). Every request from every agent shares this leading block verbatim.

---

## 3. Agent Instructions Template (Normalized)

Second message. Agent identification is normalized so different agents share the same prefix.

```python
def make_agent_instructions(agent_id: str) -> dict:
    return {
        "role": "system",
        "content": f"Agent: {agent_id}. Respond as a coding assistant in the {agent_id} environment."
    }
```

**Example outputs**:
- `"Agent: hermes. Respond as a coding assistant in the hermes environment."`
- `"Agent: opencode. Respond as a coding assistant in the opencode environment."`

After canonicalization, `agent: hermes` and `agent: opencode` differ in the 20th token. While not identical, this block is **short** (2-3 tokens at most vary). For agents that should share prefix, use a **generic instructions** block instead:

```python
GENERIC_AGENT_INSTRUCTIONS = {
    "role": "system",
    "content": "Agent: coding. This session is managed by the Memory Gateway."
}
```

This makes the agent instructions byte-identical for all agents.

---

## 4. Project Context Template

Third message. Normalizes paths to `<workspace>`.

```python
def make_project_context(context_blocks: list[dict]) -> dict:
    content_lines = []
    for block in context_blocks:
        normalized = normalize_text(block.get("content", ""))
        content_lines.append(normalized)
    return {
        "role": "system",
        "content": "Project Context:\n" + "\n---\n".join(content_lines)
    }
```

**Stable content**: The context blocks are stored in the ContextStore with deterministic IDs. The same blocks produce the same normalized bytes every time.

---

## 5. Memory Pack Snippet Template

Fourth message. Only the **first N tokens** of the current memory pack are injected. The memory pack changes nightly, so this block is stable for ~24 hours between regenerations.

```python
MEMORY_PACK_SYSTEM_PROMPT = {
    "role": "system",
    "content": "Project Memory Pack:\n{memory_pack_content}"
}
```

Where `memory_pack_content` is the concatenation of:
```
architecture.md
roadmap.md
current_state.md
coding_rules.md
active_tasks.md
repo_summary.md
```

Each file section is normalized through `normalize_text()` before concatenation.

---

## 6. File Context Blocks Template

Fifth message. File paths must be **sorted alphabetically** and paths normalized.

```python
def make_file_context(file_blocks: list[dict]) -> dict:
    sorted_blocks = sorted(file_blocks, key=lambda b: b.get("path", ""))
    parts = ["Relevant file context:"]
    for block in sorted_blocks:
        path = normalize_text(block.get("path", ""))
        content = normalize_text(block.get("content", ""))
        parts.append(f"<file path=\"{path}\">\n{content}\n</file>")
    return {
        "role": "system",
        "content": "\n".join(parts)
    }
```

**Key**: Sorting by path ensures that the same set of files always produces the same byte sequence, regardless of which agent requested them or in what order the retrieval returned them.

---

## 7. Conversation Window Template

Sixth message block. Conversation history is **truncated to the last N turns** (default 20). The truncation ensures that the prefixed context (messages 0-4) is always stable regardless of conversation length.

```python
def build_conversation(messages: list[dict], max_turns: int = 20) -> list[dict]:
    """Build conversation block from canonicalized messages."""
    system_msgs = [m for m in messages if m.get("role") == "system"]
    other_msgs = [m for m in messages if m.get("role") != "system"]
    if len(other_msgs) > max_turns:
        other_msgs = other_msgs[-max_turns:]
    return system_msgs + other_msgs
```

---

## 8. Full Prompt Assembly

```python
def build_upstream_payload(
    messages: list[dict],
    agent_id: str,
    model: str = "deepseek-chat",
    temperature: float | None = None,
    max_tokens: int | None = None,
    top_p: float | None = None,
    presence_penalty: float | None = None,
    frequency_penalty: float | None = None,
    max_conversation_turns: int = 20,
    memory_pack_content: str | None = None,
    context_blocks: list[dict] | None = None,
    file_context: list[dict] | None = None,
) -> dict:
    # Normalize all incoming message content
    normalized_messages = []
    for msg in messages:
        m = {k: v for k, v in msg.items() if v is not None}
        if isinstance(m.get("content"), str):
            m["content"] = normalize_text(m["content"])
        normalized_messages.append(m)

    # Deduplicate and reorder
    canonical_msgs = canonicalize_messages(
        normalized_messages,
        max_turns=max_conversation_turns,
        normalize_content=False,  # already normalized
    )

    # Build the upstream message list with stable prefix
    upstream_messages = list(GLOBAL_SYSTEM_PREFIX)

    # Agent instructions (use GENERIC for cross-agent prefix sharing)
    upstream_messages.append(GENERIC_AGENT_INSTRUCTIONS)

    # Project context (if any)
    if context_blocks:
        upstream_messages.append(make_project_context(context_blocks))

    # Memory pack (if any)
    if memory_pack_content:
        normalized_pack = normalize_text(memory_pack_content)
        upstream_messages.append({
            "role": "system",
            "content": f"Project Memory Pack:\n{normalized_pack}"
        })

    # File context (if any)
    if file_context:
        upstream_messages.append(make_file_context(file_context))

    # Append canonical conversation messages (excluding system
    # since we already injected our own)
    non_system = [m for m in canonical_msgs if m.get("role") != "system"]
    upstream_messages.extend(non_system)

    # Build payload with deterministic key ordering
    payload = OrderedDict()
    payload["messages"] = upstream_messages
    payload["model"] = model
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if top_p is not None:
        payload["top_p"] = top_p
    if presence_penalty is not None:
        payload["presence_penalty"] = presence_penalty
    if frequency_penalty is not None:
        payload["frequency_penalty"] = frequency_penalty

    return payload
```

---

## 9. Template Variants by Use Case

### 9.1 Code Generation Request
```
Prefix: GLOBAL_SYSTEM_PREFIX + GENERIC_AGENT_INSTRUCTIONS + PROJECT_CONTEXT
Tail:   "Generate a function that {description}. Constraints: {constraints}."
```

### 9.2 Code Review Request
```
Prefix: GLOBAL_SYSTEM_PREFIX + GENERIC_AGENT_INSTRUCTIONS + PROJECT_CONTEXT
Tail:   "Review the following code:\n{code_snippet}\nConcerns: {concerns}"
```

### 9.3 Bug Fix Request
```
Prefix: GLOBAL_SYSTEM_PREFIX + GENERIC_AGENT_INSTRUCTIONS + PROJECT_CONTEXT + MEMORY_PACK
Tail:   "Bug description: {description}\nError: {error}\nRelevant files: {file_list}"
```

### 9.4 Architecture Question
```
Prefix: GLOBAL_SYSTEM_PREFIX + GENERIC_AGENT_INSTRUCTIONS + PROJECT_CONTEXT + MEMORY_PACK
Tail:   "Question about the project architecture: {question}"
```

### 9.5 Refactoring Request
```
Prefix: GLOBAL_SYSTEM_PREFIX + GENERIC_AGENT_INSTRUCTIONS + PROJECT_CONTEXT + MEMORY_PACK + FILE_CONTEXT
Tail:   "Refactor {file_path} to {goal}. Current implementation:\n{current_code}"
```

---

## 10. Configuration Constants

```python
# config.py additions
UPSTREAM_PREFIX_CONFIG = {
    "canonical_system_prompt": GLOBAL_SYSTEM_PREFIX,
    "use_generic_agent_instructions": True,   # False to keep per-agent prompts
    "max_memory_pack_prefix_tokens": 2000,
    "max_conversation_turns": 20,
    "deterministic_json": True,
    "normalize_upstream_content": True,
    "inject_project_context": True,
    "inject_memory_pack": True,
    "sort_file_context": True,
}
```

## 11. Expected Byte-Level Prefix Stability

| Component | Bytes (approx) | Stability |
|---|---|---|
| Global system prefix | ~400 | 100% — never changes |
| Agent instructions (generic) | ~80 | 100% — never changes |
| Project context | ~200 | 100% — changes only when context blocks update |
| Memory pack snippet | ~4000 | Stable for ~24h between regenerations |
| File context | ~1000 | Variable per query but sorted + normalized |
| Conversation window | ~3000 | Variable per agent session |
| Current query | ~200 | Always unique |

**Total prefix length stable across all requests**: ~400 bytes  
**Total prefix length stable per memory-pack generation**: ~4700 bytes  
**Variable suffix**: ~4200 bytes (conversation + query)

This means **~53% of each upstream request** is byte-identical across all requests from all agents within the same memory-pack epoch, maximizing DeepSeek prefix cache utilization.
