import hashlib
import json
import re
from dataclasses import dataclass
from typing import Optional

UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
    r"|"
    r"\d{4}-\d{2}-\d{2}"
)

SESSION_ID_RE = re.compile(
    r"(?:(?:sess|session|sid)[=_:\-])?[a-f0-9]{24,}"
    r"|"
    r"(?:sess|session|sid)[=_:\-][a-zA-Z0-9_-]{8,}",
    re.IGNORECASE,
)

AGENT_ID_IN_TEXT_RE = re.compile(
    r"(?:"
    r"(?:\bagent[=_:\-]?(?:id)?[=_:\-]?\s*)"
    r"|"
    r"(?:x-agent-id[=_:\-]?\s*)"
    r")(hermes|opencode|qoder|vscode)",
    re.IGNORECASE,
)

TEMP_FILE_RE = re.compile(
    r"(?:/tmp/|/var/tmp/|/private/tmp/|/temp/|/Temp/)"
    r"[a-zA-Z0-9._-]{3,}(?:\.(?:tmp|temp|swp|swo|bak|log))?"
    r"|"
    r"(?:[a-zA-Z]:\\(?:[^\\]+\\)+AppData\\Local\\Temp\\)"
    r"[a-zA-Z0-9_-]{3,}\.(?:tmp|temp)"
    r"|"
    r"\b[a-zA-Z0-9_-]{3,}\.(?:tmp|temp)\b",
    re.IGNORECASE,
)

ABSOLUTE_PATH_RE = re.compile(
    r"(?:/(?:[a-zA-Z0-9._~\-]+))(?:(?:/[a-zA-Z0-9._~\-]+)+)"
    r"|"
    r"(?:[a-zA-Z]:(?:\\(?:[a-zA-Z0-9._~\- ]+)){2,}\\?)",
)

WHITESPACE_RE = re.compile(r"\s+")

COMPILED_PATTERNS = (
    UUID_RE,
    TIMESTAMP_RE,
    SESSION_ID_RE,
    AGENT_ID_IN_TEXT_RE,
    TEMP_FILE_RE,
    ABSOLUTE_PATH_RE,
    WHITESPACE_RE,
)
PATTERN_LABELS = (
    "uuid",
    "timestamp",
    "session",
    "<AGENTID>",
    "tempfile",
    "path",
    "whitespace",
)

ROLE_ORDER = {"system": 0, "user": 1, "assistant": 2, "tool": 3}


@dataclass
class CanonicalPrompt:
    canonical_messages: list[dict]
    canonical_text: str
    canonical_hash: str


def _path_replacer(m: re.Match) -> str:
    path = m.group(0)
    is_unix = path.startswith("/")
    sep = "/" if is_unix else "\\"
    raw_parts = path.strip(sep).split(sep)
    parts = [p for p in raw_parts if p]

    if not parts:
        return "<workspace>"

    if (
        is_unix
        and parts[0].lower() in ("home", "users", "root")
        and len(parts) >= 3
    ):
        remaining = sep.join(parts[3:])
    elif is_unix and len(parts) >= 2:
        remaining = sep.join(parts[2:])
    elif not is_unix and len(parts) >= 3:
        remaining = sep.join(parts[3:])
    elif not is_unix and len(parts) >= 2:
        remaining = sep.join(parts[2:])
    else:
        remaining = sep.join(parts)

    return f"<workspace>{sep}{remaining}" if remaining else "<workspace>"


def _normalize_paths(text: str) -> str:
    return ABSOLUTE_PATH_RE.sub(_path_replacer, text)


def _normalize_uuids(text: str) -> str:
    return UUID_RE.sub("<uuid>", text)


def _normalize_timestamps(text: str) -> str:
    return TIMESTAMP_RE.sub("<timestamp>", text)


def _normalize_session_ids(text: str) -> str:
    return SESSION_ID_RE.sub("<session>", text)


def _normalize_agent_ids(text: str) -> str:
    return AGENT_ID_IN_TEXT_RE.sub(r"agent: \1", text)


def _normalize_temp_files(text: str) -> str:
    return TEMP_FILE_RE.sub("<tempfile>", text)


def _normalize_whitespace(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text).strip()


def normalize_text(text: str) -> str:
    text = _normalize_uuids(text)
    text = _normalize_timestamps(text)
    text = _normalize_session_ids(text)
    text = _normalize_agent_ids(text)
    text = _normalize_temp_files(text)
    text = _normalize_paths(text)
    text = _normalize_whitespace(text)
    return text


def _deduplicate_system_messages(messages: list[dict]) -> list[dict]:
    seen_normalized: set[str] = set()
    result: list[dict] = []
    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content", "") or ""
            norm = _normalize_whitespace(
                _normalize_uuids(
                    _normalize_timestamps(
                        _normalize_paths(
                            _normalize_temp_files(content)
                        )
                    )
                )
            )
            if norm in seen_normalized:
                continue
            seen_normalized.add(norm)
        result.append(msg)
    return result


def canonicalize_messages(
    messages: list[dict],
    max_turns: int | None = None,
) -> list[dict]:
    # 1. Strip None fields
    normalized: list[dict] = []
    for msg in messages:
        m = {k: v for k, v in msg.items() if v is not None}
        if "content" in m and isinstance(m["content"], str):
            m["content"] = normalize_text(m["content"])
        normalized.append(m)

    # 2. Deduplicate system prompts
    normalized = _deduplicate_system_messages(normalized)

    # 3. Sort: system first, then others in original order
    system_msgs = [m for m in normalized if m.get("role") == "system"]
    other_msgs = [m for m in normalized if m.get("role") != "system"]

    # 4. Truncate oldest turns if max_turns is set
    if max_turns is not None and len(other_msgs) > max_turns:
        other_msgs = other_msgs[-max_turns:]

    return system_msgs + other_msgs


def _canonical_text(messages: list[dict]) -> str:
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(f"{role}: {content}")
        elif isinstance(content, list):
            sub = " ".join(
                c.get("text", "") if isinstance(c, dict) else str(c)
                for c in content
            )
            parts.append(f"{role}: {sub}")
    return "\n".join(parts)


def canonicalize_prompt(
    messages: list[dict],
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    top_p: Optional[float] = None,
    presence_penalty: Optional[float] = None,
    frequency_penalty: Optional[float] = None,
) -> CanonicalPrompt:
    canonical_messages = canonicalize_messages(messages)

    payload: dict = {"messages": canonical_messages}
    if model:
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

    canonical_text = _canonical_text(canonical_messages)

    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    canonical_hash = hashlib.sha256(raw).hexdigest()

    return CanonicalPrompt(
        canonical_messages=canonical_messages,
        canonical_text=canonical_text,
        canonical_hash=canonical_hash,
    )


def generate_cache_key_params(
    model: str,
    messages: list,
    temperature: float | None = None,
    max_tokens: int | None = None,
    top_p: float | None = None,
    presence_penalty: float | None = None,
    frequency_penalty: float | None = None,
) -> str:
    result = canonicalize_prompt(
        messages=messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
        presence_penalty=presence_penalty,
        frequency_penalty=frequency_penalty,
    )
    return f"exact:{result.canonical_hash}"
