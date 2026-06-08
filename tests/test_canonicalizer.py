import pytest

from gateway.canonicalizer import (
    CanonicalPrompt,
    canonicalize_messages,
    canonicalize_prompt,
    normalize_text,
)


class TestUuidNormalization:
    def test_standard_uuid(self):
        assert "<uuid>" in normalize_text("550e8400-e29b-41d4-a716-446655440000")

    def test_uppercase_uuid(self):
        assert "<uuid>" in normalize_text("550E8400-E29B-41D4-A716-446655440000")

    def test_uuid_in_filename(self):
        result = normalize_text("log_550e8400-e29b-41d4-a716-446655440000.txt")
        assert "<uuid>" in result

    def test_multiple_uuids(self):
        result = normalize_text(
            "a=550e8400-e29b-41d4-a716-446655440000 b=6ba7b810-9dad-11d1-80b4-00c04fd430c8"
        )
        assert result.count("<uuid>") == 2

    def test_not_a_uuid(self):
        result = normalize_text("not-a-uuid-at-all")
        assert result == "not-a-uuid-at-all"

    def test_uuid_in_code(self):
        result = normalize_text('id = "550e8400-e29b-41d4-a716-446655440000"')
        assert "<uuid>" in result
        assert "550e8400" not in result


class TestTimestampNormalization:
    def test_iso_t_with_z(self):
        result = normalize_text("at 2026-06-08T14:31:22Z we saw")
        assert "<timestamp>" in result

    def test_iso_t_with_offset(self):
        result = normalize_text("2026-06-08T14:31:22+05:30 event")
        assert "<timestamp>" in result

    def test_iso_with_space(self):
        result = normalize_text("on 2026-06-08 12:00:00 we")
        assert "<timestamp>" in result

    def test_iso_with_millis(self):
        result = normalize_text("2026-06-08T14:31:22.123Z event")
        assert "<timestamp>" in result

    def test_date_only(self):
        result = normalize_text("on 2026-06-08 we started")
        assert "<timestamp>" in result

    def test_multiple_timestamps(self):
        result = normalize_text("from 2026-01-01 to 2026-12-31")
        assert result.count("<timestamp>") == 2

    def test_not_a_timestamp(self):
        result = normalize_text("version 2.0.1 released on schedule")
        assert "version 2.0.1" in result


class TestSessionIdNormalization:
    def test_sess_prefix_hex(self):
        result = normalize_text("session sess_a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8g9h0i")
        assert "<session>" in result

    def test_session_equals(self):
        result = normalize_text("session=a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8g9h0i")
        assert "<session>" in result

    def test_sid_colon(self):
        result = normalize_text("sid:a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d")
        assert "<session>" in result

    def test_normal_text_not_session(self):
        result = normalize_text("the session was productive")
        assert result == "the session was productive"


class TestAgentIdNormalization:
    def test_agent_equals_hermes(self):
        result = normalize_text("agent=hermes reported")
        assert "agent: hermes" in result

    def test_agent_id_colon_opencode(self):
        result = normalize_text("agent_id:opencode completed")
        assert "agent: opencode" in result

    def test_x_agent_id_header_pattern(self):
        result = normalize_text("x-agent-id=qoder says hello")
        assert "agent: qoder" in result

    def test_agent_normal_word_not_touched(self):
        result = normalize_text("the agent was notified")
        assert result == "the agent was notified"


class TestPathNormalization:
    def test_unix_home_path(self):
        result = normalize_text("in /home/frank/project/src/auth.py")
        assert "<workspace>" in result
        assert "frank" not in result

    def test_unix_users_path(self):
        result = normalize_text("in /Users/alice/work/src/main.go")
        assert "<workspace>" in result
        assert "alice" not in result

    def test_unix_opt_path(self):
        result = normalize_text("config at /opt/myapp/config/settings.yaml")
        assert "<workspace>" in result

    def test_unix_etc_path(self):
        result = normalize_text("read /etc/hosts file")
        assert "<workspace>" in result

    def test_unix_root_path(self):
        result = normalize_text("as /root/project/scripts/deploy.sh shows")
        assert "<workspace>" in result

    def test_windows_user_path(self):
        result = normalize_text(r"in C:\Users\frank\project\src\auth.py")
        assert "<workspace>" in result

    def test_windows_data_path(self):
        result = normalize_text(r"data in D:\data\files\doc.txt")
        assert "<workspace>" in result

    def test_relative_path_not_touched(self):
        result = normalize_text("see src/auth.py for details")
        assert "src/auth.py" in result
        assert "<workspace>" not in result

    def test_path_with_spaces(self):
        result = normalize_text(r"in C:\My Projects\Data\file.txt")
        assert "<workspace>" in result

    def test_path_preserves_relative_part(self):
        result = normalize_text("run /home/user/project/tests/test_api.py")
        assert "<workspace>/tests/test_api.py" in result.replace("\\", "/")


class TestTempFileNormalization:
    def test_unix_tmp_file(self):
        result = normalize_text("wrote /tmp/abc123.tmp")
        assert "<tempfile>" in result

    def test_unix_var_tmp(self):
        result = normalize_text("output to /var/tmp/xyz789")
        assert "<tempfile>" in result

    def test_windows_temp(self):
        result = normalize_text(r"used C:\Users\frank\AppData\Local\Temp\xyz.tmp to cache")
        assert "<tempfile>" in result

    def test_temp_extension(self):
        result = normalize_text("wrote data.tmp")
        assert "<tempfile>" in result

    def test_not_temp(self):
        result = normalize_text("template.html is ready")
        assert result == "template.html is ready"


class TestWhitespaceNormalization:
    def test_collapses_newlines(self):
        result = normalize_text("hello\n\n\nworld")
        assert result == "hello world"

    def test_collapses_tabs(self):
        result = normalize_text("hello\t\t\tworld")
        assert result == "hello world"

    def test_strips_outer_whitespace(self):
        assert normalize_text("  hello world  ") == "hello world"

    def test_mixed_whitespace(self):
        result = normalize_text("  hello\n  \n  world  ")
        assert result == "hello world"

    def test_only_whitespace(self):
        assert normalize_text("   \n  \t  ") == ""


class TestDuplicateSystemPrompt:
    def test_identical_system_prompts_deduplicated(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
            {"role": "system", "content": "You are helpful."},
        ]
        result = canonicalize_messages(msgs)
        assert len([m for m in result if m.get("role") == "system"]) == 1

    def test_different_system_prompts_not_deduplicated(self):
        msgs = [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Hi"},
            {"role": "system", "content": "Use Python 3."},
        ]
        result = canonicalize_messages(msgs)
        assert len([m for m in result if m.get("role") == "system"]) == 2

    def test_deduplication_ignores_whitespace_diff(self):
        msgs = [
            {"role": "system", "content": "You are  helpful."},
            {"role": "user", "content": "Hi"},
            {"role": "system", "content": "You are helpful."},
        ]
        result = canonicalize_messages(msgs)
        assert len([m for m in result if m.get("role") == "system"]) == 1

    def test_deduplication_normalizes_paths(self):
        msgs = [
            {"role": "system", "content": "Run /home/user/project/test.py"},
            {"role": "system", "content": "Run /home/other/project/test.py"},
        ]
        result = canonicalize_messages(msgs)
        assert len([m for m in result if m.get("role") == "system"]) == 1


class TestCanonicalizeMessages:
    def test_empty_messages(self):
        assert canonicalize_messages([]) == []

    def test_system_first(self):
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "system", "content": "Be helpful"},
        ]
        result = canonicalize_messages(msgs)
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"

    def test_normalizes_content(self):
        msgs = [{"role": "user", "content": "Check file_550e8400-e29b-41d4-a716-446655440000"}]
        result = canonicalize_messages(msgs)
        assert "<uuid>" in result[0]["content"]

    def test_strips_none_fields(self):
        msgs = [{"role": "user", "content": "Hi", "name": None, "tool_calls": None}]
        result = canonicalize_messages(msgs)
        assert "name" not in result[0]
        assert "tool_calls" not in result[0]

    def test_max_turns_truncates_oldest(self):
        msgs = [
            {"role": "user", "content": "First"},
            {"role": "assistant", "content": "Resp1"},
            {"role": "user", "content": "Second"},
            {"role": "assistant", "content": "Resp2"},
            {"role": "user", "content": "Third"},
        ]
        result = canonicalize_messages(msgs, max_turns=4)
        assert len(result) == 4
        assert result[0]["content"] == "Resp1"
        assert result[-1]["content"] == "Third"

    def test_deterministic_output(self):
        msgs_a = [
            {"role": "user", "content": "B"},
            {"role": "system", "content": "A"},
        ]
        msgs_b = [
            {"role": "system", "content": "A"},
            {"role": "user", "content": "B"},
        ]
        assert canonicalize_messages(msgs_a) == canonicalize_messages(msgs_b)

    def test_mixed_content_types(self):
        msgs = [
            {"role": "user", "content": [{"type": "text", "text": "Hello"}], "name": "test"},
        ]
        result = canonicalize_messages(msgs)
        assert len(result) == 1
        assert result[0]["role"] == "user"


class TestComplexCanonicalization:
    def test_full_pipeline(self):
        text = (
            "Session sess_a1b2c3d4e5f6a7b8c9d0e1f2a at 2026-06-08T14:31:22Z "
            "agent=hermes wrote /home/user/project/src/main.py "
            "on server 550e8400-e29b-41d4-a716-446655440000 "
            "output to /tmp/build.tmp"
        )
        result = normalize_text(text)
        assert "<session>" in result
        assert "<timestamp>" in result
        assert "agent: hermes" in result
        assert "<workspace>" in result
        assert "<uuid>" in result
        assert "<tempfile>" in result
        assert "sess_" not in result
        assert "2026-06-08" not in result
        assert "550e8400" not in result
        assert "/home/user" not in result
        assert "/tmp/build" not in result
        assert "agent=" not in result

    def test_hermes_agent_prompt_cacheable(self):
        msgs_a = [
            {"role": "system", "content": "You are hermes, an AI coding agent."},
            {"role": "user", "content": "Review /home/alice/project/src/api.py"},
        ]
        msgs_b = [
            {"role": "user", "content": "Review /home/bob/project/src/api.py"},
            {"role": "system", "content": "You are hermes, an AI coding agent."},
        ]
        result_a = canonicalize_messages(msgs_a)
        result_b = canonicalize_messages(msgs_b)
        assert result_a == result_b

    def test_identical_paths_normalize_same(self):
        a = normalize_text("/home/user/project/file.py has a bug")
        b = normalize_text("/home/other/project/file.py has a bug")
        assert a == b


class TestCanonicalizePrompt:
    def test_returns_canonical_prompt_dataclass(self):
        msgs = [{"role": "user", "content": "Hello"}]
        result = canonicalize_prompt(messages=msgs)
        assert isinstance(result, CanonicalPrompt)
        assert isinstance(result.canonical_messages, list)
        assert isinstance(result.canonical_text, str)
        assert isinstance(result.canonical_hash, str)

    def test_hash_is_sha256_hex(self):
        msgs = [{"role": "user", "content": "Hello"}]
        result = canonicalize_prompt(messages=msgs)
        assert len(result.canonical_hash) == 64
        int(result.canonical_hash, 16)

    def test_same_input_same_hash(self):
        msgs = [{"role": "user", "content": "Hello world"}]
        a = canonicalize_prompt(messages=msgs)
        b = canonicalize_prompt(messages=msgs)
        assert a.canonical_hash == b.canonical_hash

    def test_different_input_different_hash(self):
        a = canonicalize_prompt(messages=[{"role": "user", "content": "Hello"}])
        b = canonicalize_prompt(messages=[{"role": "user", "content": "World"}])
        assert a.canonical_hash != b.canonical_hash

    def test_model_affects_hash(self):
        msgs = [{"role": "user", "content": "Hello"}]
        a = canonicalize_prompt(messages=msgs, model="deepseek-chat")
        b = canonicalize_prompt(messages=msgs, model="deepseek-coder")
        assert a.canonical_hash != b.canonical_hash

    def test_temperature_affects_hash(self):
        msgs = [{"role": "user", "content": "Hi"}]
        a = canonicalize_prompt(messages=msgs, temperature=0.1)
        b = canonicalize_prompt(messages=msgs, temperature=0.5)
        assert a.canonical_hash != b.canonical_hash

    def test_paths_normalize_to_same_hash(self):
        a = canonicalize_prompt(
            messages=[{"role": "user", "content": "Review /home/alice/project/src/main.py"}],
        )
        b = canonicalize_prompt(
            messages=[{"role": "user", "content": "Review /home/bob/project/src/main.py"}],
        )
        assert a.canonical_hash == b.canonical_hash

    def test_timestamps_normalize_to_same_hash(self):
        a = canonicalize_prompt(
            messages=[{"role": "user", "content": "at 2026-06-08T14:31:22Z"}],
        )
        b = canonicalize_prompt(
            messages=[{"role": "user", "content": "at 2026-06-09T10:15:00Z"}],
        )
        assert a.canonical_hash == b.canonical_hash

    def test_uuids_normalize_to_same_hash(self):
        a = canonicalize_prompt(
            messages=[{"role": "user", "content": "id 550e8400-e29b-41d4-a716-446655440000"}],
        )
        b = canonicalize_prompt(
            messages=[{"role": "user", "content": "id 6ba7b810-9dad-11d1-80b4-00c04fd430c8"}],
        )
        assert a.canonical_hash == b.canonical_hash

    def test_session_ids_normalize_to_same_hash(self):
        a = canonicalize_prompt(
            messages=[{"role": "user", "content": "session sess_a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d"}],
        )
        b = canonicalize_prompt(
            messages=[{"role": "user", "content": "session sid_x1y2z3w4v5u6t7s8r9q0p1o2i3u4y5t"}],
        )
        assert a.canonical_hash == b.canonical_hash

    def test_writes_text_to_canonical_text(self):
        msgs = [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "World"}]
        result = canonicalize_prompt(messages=msgs)
        assert "user: Hello" in result.canonical_text
        assert "assistant: World" in result.canonical_text

    def test_duplicate_system_does_not_change_hash(self):
        msgs_unique = [
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "Hi"},
        ]
        msgs_duplicate = [
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "Hi"},
            {"role": "system", "content": "Be helpful."},
        ]
        a = canonicalize_prompt(messages=msgs_unique)
        b = canonicalize_prompt(messages=msgs_duplicate)
        assert a.canonical_hash == b.canonical_hash


class TestCanonicalHashEquivalence:
    def test_reorder_messages_same_hash(self):
        a = canonicalize_prompt(
            messages=[
                {"role": "user", "content": "B"},
                {"role": "system", "content": "A"},
            ],
        )
        b = canonicalize_prompt(
            messages=[
                {"role": "system", "content": "A"},
                {"role": "user", "content": "B"},
            ],
        )
        assert a.canonical_hash == b.canonical_hash

    def test_strip_none_fields_same_hash(self):
        a = canonicalize_prompt(
            messages=[{"role": "user", "content": "Hi", "name": None}],
        )
        b = canonicalize_prompt(
            messages=[{"role": "user", "content": "Hi"}],
        )
        assert a.canonical_hash == b.canonical_hash

    def test_different_whitespace_same_hash(self):
        a = canonicalize_prompt(
            messages=[{"role": "user", "content": "Hello   world"}],
        )
        b = canonicalize_prompt(
            messages=[{"role": "user", "content": "Hello world"}],
        )
        assert a.canonical_hash == b.canonical_hash

    def test_paths_dont_affect_equivalence(self):
        a = canonicalize_prompt(
            messages=[{"role": "user", "content": "see /home/user/project/src/main.py"}],
            model="deepseek-chat",
        )
        b = canonicalize_prompt(
            messages=[{"role": "user", "content": "see /home/other/project/src/main.py"}],
            model="deepseek-chat",
        )
        assert a.canonical_hash == b.canonical_hash

    def test_timestamps_dont_affect_equivalence(self):
        a = canonicalize_prompt(
            messages=[{"role": "user", "content": "ran at 2026-06-08T14:31:22Z"}],
        )
        b = canonicalize_prompt(
            messages=[{"role": "user", "content": "ran at 2026-06-09T10:15:00Z"}],
        )
        assert a.canonical_hash == b.canonical_hash

    def test_full_agent_prompt_cacheable(self):
        prompt_a = [
            {"role": "system", "content": "You are hermes, an AI coding agent."},
            {"role": "user", "content": "Find the bug in /home/user/project/src/main.py at line 42"},
        ]
        prompt_b = [
            {"role": "user", "content": "Find the bug in /home/other/project/src/main.py at line 42"},
            {"role": "system", "content": "You are hermes, an AI coding agent."},
        ]
        a = canonicalize_prompt(messages=prompt_a, model="deepseek-chat", temperature=0.1)
        b = canonicalize_prompt(messages=prompt_b, model="deepseek-chat", temperature=0.1)
        assert a.canonical_hash == b.canonical_hash
