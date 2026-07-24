"""Deterministic tests for privacy-preserving review transcript enrichment."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


TESTS_DIR = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "analysis" / "review_transcript.py"
BOOTSTRAP_PATH = PLUGIN_ROOT / "scripts" / "review" / "agent" / "bootstrap.py"

_spec = importlib.util.spec_from_file_location("review_transcript", SCRIPT_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

iter_jsonl = _mod.iter_jsonl
find_session_file = _mod.find_session_file
correlate_run_agents = _mod.correlate_run_agents
analyze_orchestrator_steps = _mod.analyze_orchestrator_steps
analyze_subagent = _mod.analyze_subagent
enrich_run_transcript = _mod.enrich_run_transcript
result_state = _mod._result_state
is_bootstrap_builder_heredoc = _mod._is_bootstrap_builder_heredoc

_bootstrap_spec = importlib.util.spec_from_file_location(
    "review_bootstrap_for_transcript_test", BOOTSTRAP_PATH
)
_bootstrap_mod = importlib.util.module_from_spec(_bootstrap_spec)
_bootstrap_spec.loader.exec_module(_bootstrap_mod)

_TEST_TRANSCRIPT_START = datetime(2026, 7, 20, 10, 0, 0, tzinfo=timezone.utc)


def _write_jsonl(path: Path, entries: list[object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamped: list[object] = []
    for index, entry in enumerate(entries):
        if isinstance(entry, dict) and "timestamp" not in entry:
            entry = {
                **entry,
                "timestamp": (_TEST_TRANSCRIPT_START + timedelta(seconds=index)).isoformat(),
            }
        timestamped.append(entry)
    path.write_text("\n".join(json.dumps(entry) for entry in timestamped) + "\n")
    return path


def _at(entry: dict, seconds: int) -> dict:
    return {
        **entry,
        "timestamp": (_TEST_TRANSCRIPT_START + timedelta(seconds=seconds)).isoformat(),
    }


def _assistant(
    *blocks: dict,
    usage: dict | None = None,
    model: str = "claude-sonnet-4-5",
    entry_usage: bool = False,
    message_id: str | None = None,
) -> dict:
    message = {"role": "assistant", "model": model, "content": list(blocks)}
    if message_id is not None:
        message["id"] = message_id
    entry = {"type": "assistant", "message": message}
    if usage is not None:
        if entry_usage:
            entry["usage"] = usage
        else:
            message["usage"] = usage
    return entry


def _call(tool_id: str, name: str, **tool_input: object) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


def _result(
    tool_id: str,
    content: str = "ok",
    *,
    is_error: bool | None = False,
    structured: object | None = None,
) -> dict:
    block = {"type": "tool_result", "tool_use_id": tool_id, "content": content}
    if is_error is not None:
        block["is_error"] = is_error
    entry = {"type": "user", "message": {"role": "user", "content": [block]}}
    if structured is not None:
        entry["toolUseResult"] = structured
    return entry


def _usage(input_tokens: int, output_tokens: int, create: int = 0, read: int = 0) -> dict:
    return {
        "input_tokens": input_tokens,
        "cache_creation_input_tokens": create,
        "cache_read_input_tokens": read,
        "output_tokens": output_tokens,
    }


def _agent_prompt(output_dir: Path, agent: str = "security-reviewer") -> str:
    return (
        "python3 /plugin/review/agent/bootstrap.py "
        f'--agent {agent} --range "base..head" --output-dir "{output_dir}"'
    )


def _builder_envelope(body: str | None, *, header: str | None = None) -> str:
    header = header or (
        "PIRATEGOAT_PLUGIN_ROOT=/plugin PIRATEGOAT_OUTPUT_DIR=/output "
        "PIRATEGOAT_REVIEWER_NAME=security PIRATEGOAT_PR_ID=42 python3 <<PY"
    )
    return header if body is None else f"{header}\n{body}\nPY"


def _special_agent_call(
    tool_id: str, output_dir: Path, agent: str
) -> dict:
    prefix = "- " if agent == "review-reconciliator" else ""
    value = f"`{output_dir}`" if prefix else str(output_dir)
    return _call(
        tool_id,
        "Agent",
        prompt=f"Synthesize review results\n{prefix}Output directory: {value}",
        subagent_type=agent,
        description=agent,
    )


def _structured_patch() -> list[dict]:
    return [
        {
            "oldStart": 1,
            "oldLines": 1,
            "newStart": 1,
            "newLines": 1,
            "lines": ["safe"],
        }
    ]


def _current_read_result(file_path: str) -> dict:
    return {
        "type": "text",
        "file": {
            "filePath": file_path,
            "content": "safe",
            "numLines": 1,
            "startLine": 1,
            "totalLines": 1,
        },
    }


def _current_write_result(file_path: str, *, update: bool = False) -> dict:
    return {
        "type": "create" if not update else "update",
        "content": "safe",
        "filePath": file_path,
        "originalFile": "before" if update else None,
        "structuredPatch": _structured_patch() if update else [],
        "userModified": False,
    }


def _current_edit_result(
    file_path: str, *, original_file: str | None = "before", stale: bool = False
) -> dict:
    result = {
        "filePath": file_path,
        "oldString": "before",
        "newString": "after",
        "originalFile": original_file,
        "replaceAll": False,
        "structuredPatch": _structured_patch(),
        "userModified": False,
    }
    if stale:
        result["staleRecovered"] = True
    return result


def _manifest(
    session_id: str | None,
    repo: Path,
    output_dir: Path,
    started: list[str] | None = None,
) -> dict:
    manifest = {
        "run": {
            "session_id": session_id,
            "repo_path": str(repo),
            "output_dir": str(output_dir),
            "started_at": _TEST_TRANSCRIPT_START.isoformat(),
            "ended_at": (_TEST_TRANSCRIPT_START + timedelta(hours=1)).isoformat(),
        },
        "steps": [],
        "coverage": {"by_agent": {"security-reviewer": ["src/in.py"]}},
    }
    if started is None:
        started = ["security-reviewer"]
    manifest["agents"] = {
        "started": [{"agent": agent} for agent in started],
    }
    return manifest


def _flatten_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _flatten_strings(child)]
    if isinstance(value, list):
        return [item for child in value for item in _flatten_strings(child)]
    return []


def _flatten_keys(value: object) -> list[str]:
    if isinstance(value, dict):
        return list(value) + [
            key for child in value.values() for key in _flatten_keys(child)
        ]
    if isinstance(value, list):
        return [key for child in value for key in _flatten_keys(child)]
    return []


def test_iter_jsonl_skips_bad_lines_individually(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"type": "assistant", "sequence": 1}),
                "not-json",
                json.dumps(["not", "an", "object"]),
                '{"type": "truncated"',
                "",
                json.dumps({"type": "user", "sequence": 2}),
            ]
        )
    )

    assert list(iter_jsonl(path)) == [
        {"type": "assistant", "sequence": 1},
        {"type": "user", "sequence": 2},
    ]


class TestFindSessionFile:
    def test_accepts_project_root_and_global_projects_root(self, tmp_path):
        project = tmp_path / "-project"
        expected = _write_jsonl(project / "session-1.jsonl", [])

        assert find_session_file(project, "session-1") == str(expected)
        assert find_session_file(tmp_path, "session-1") == str(expected)

    @pytest.mark.parametrize(
        "session_id",
        ["../session", "nested/session", "nested\\session", ".", "..", ""],
    )
    def test_rejects_invalid_or_traversing_session_ids(self, tmp_path, session_id):
        assert find_session_file(tmp_path, session_id) is None

    def test_returns_none_when_exact_match_is_ambiguous(self, tmp_path):
        _write_jsonl(tmp_path / "-one" / "same.jsonl", [])
        _write_jsonl(tmp_path / "-two" / "same.jsonl", [])

        assert find_session_file(tmp_path, "same") is None


class TestCorrelateRunAgents:
    def test_correlates_current_structured_agent_result_and_exact_path(self, tmp_path):
        session = tmp_path / "session-1.jsonl"
        output_dir = tmp_path / "pr-review-1"
        _write_jsonl(
            session,
            [
                _assistant(_call("a1", "Agent", prompt=_agent_prompt(output_dir))),
                _result(
                    "a1",
                    structured={"agentId": "abc123", "resolvedModel": "claude-opus-4-1"},
                ),
            ],
        )

        assert correlate_run_agents(session, output_dir, {"security-reviewer"}) == [
            {
                "agent": "security-reviewer",
                "agent_id": "abc123",
                "model": "claude-opus-4-1",
                "transcript": str(
                    tmp_path / "session-1" / "subagents" / "agent-abc123.jsonl"
                ),
            }
        ]

    @pytest.mark.parametrize(
        "prompt_template",
        [
            (
                "Review the assigned files carefully.\n"
                "{command}\n"
                "Return only after the review artifact is saved."
            ),
            (
                "Run this bootstrap command:\n"
                "```bash\n"
                "{command}\n"
                "```\n"
                "Then follow the generated instructions."
            ),
        ],
        ids=["instruction-envelope", "fenced-command"],
    )
    def test_correlates_one_canonical_bootstrap_inside_multiline_prompt(
        self, tmp_path, prompt_template
    ):
        session = tmp_path / "multiline.jsonl"
        output_dir = tmp_path / "run"
        _write_jsonl(
            session,
            [
                _assistant(
                    _call(
                        "agent",
                        "Agent",
                        prompt=prompt_template.format(
                            command=_agent_prompt(output_dir)
                        ),
                    )
                ),
                _result("agent", structured={"agentId": "multiline-agent"}),
            ],
        )

        result = correlate_run_agents(session, output_dir, {"security-reviewer"})

        assert [item["agent_id"] for item in result] == ["multiline-agent"]

    @pytest.mark.parametrize(
        "prompt",
        [
            (
                "{matching}\n"
                "{matching}"
            ),
            (
                "python3 /plugin/review/agent/bootstrap.py "
                "--agent security-reviewer --output-dir /reviews/other\n"
                "python3 /plugin/review/agent/bootstrap.py "
                "--agent tests-reviewer --output-dir {output_dir}"
            ),
            (
                "python3 /plugin/review/agent/bootstrap.py "
                "--agent security-reviewer --output-dir $OUTPUT_DIR"
            ),
        ],
        ids=["duplicate-command", "cross-combined-fields", "unresolved-path"],
    )
    def test_rejects_ambiguous_or_unresolved_multiline_bootstrap_prompts(
        self, tmp_path, prompt
    ):
        session = tmp_path / "ambiguous-multiline.jsonl"
        output_dir = tmp_path / "run"
        matching = _agent_prompt(output_dir)
        _write_jsonl(
            session,
            [
                _assistant(
                    _call(
                        "agent",
                        "Agent",
                        prompt=prompt.format(
                            matching=matching,
                            output_dir=output_dir,
                        ),
                    )
                ),
                _result("agent", structured={"agentId": "ambiguous-agent"}),
            ],
        )

        assert correlate_run_agents(
            session, output_dir, {"security-reviewer", "tests-reviewer"}
        ) == []

    @pytest.mark.parametrize(
        "directory_name,prompt_template",
        [
            ("run with spaces", '--output-dir "{output_dir}"'),
            ("run with spaces", "--output-dir {escaped_output_dir}"),
            ("run:colon", "--output-dir={output_dir}"),
        ],
        ids=["quoted-space", "escaped-space", "equals-colon"],
    )
    def test_parses_complete_shell_output_dir_option(
        self, tmp_path, directory_name, prompt_template
    ):
        session = tmp_path / f"{directory_name.replace(' ', '-')}.jsonl"
        output_dir = tmp_path / directory_name
        prompt = prompt_template.format(
            output_dir=output_dir,
            escaped_output_dir=str(output_dir).replace(" ", "\\ "),
        )
        _write_jsonl(
            session,
            [
                _assistant(
                    _call(
                        "a1",
                        "Agent",
                        prompt=f"bootstrap.py --agent security-reviewer {prompt}",
                    )
                ),
                _result("a1", structured={"agentId": "exact-option"}),
            ],
        )

        result = correlate_run_agents(session, output_dir, {"security-reviewer"})
        assert [item["agent_id"] for item in result] == ["exact-option"]

    @pytest.mark.parametrize(
        "prompt",
        [
            "--output-dir /reviews/run-old",
            "--output-dir /reviews/run/suffix",
            "--output-dir /reviews/run old",
            "--output-dir $REVIEW_DIR",
            "--output-dir",
        ],
        ids=["prefix", "suffix", "unquoted-space", "unresolved", "missing-value"],
    )
    def test_rejects_nonexact_or_malformed_output_dir_options(self, tmp_path, prompt):
        session = tmp_path / "bad-option.jsonl"
        _write_jsonl(
            session,
            [
                _assistant(
                    _call(
                        "a1",
                        "Agent",
                        prompt=f"bootstrap.py --agent security-reviewer {prompt}",
                    )
                ),
                _result("a1", structured={"agentId": "wrong-run"}),
            ],
        )

        assert correlate_run_agents(
            session, "/reviews/run old", {"security-reviewer"}
        ) == []

    def test_shorter_run_does_not_match_quoted_longer_run(self, tmp_path):
        session = tmp_path / "longer-run.jsonl"
        _write_jsonl(
            session,
            [
                _assistant(
                    _call(
                        "a1",
                        "Agent",
                        prompt=(
                            "bootstrap.py --agent security-reviewer "
                            '--output-dir "/reviews/run old"'
                        ),
                    )
                ),
                _result("a1", structured={"agentId": "wrong-run"}),
            ],
        )

        assert correlate_run_agents(
            session, "/reviews/run", {"security-reviewer"}
        ) == []

    def test_supports_legacy_text_agent_id_and_legacy_task_tool(self, tmp_path):
        session = tmp_path / "legacy.jsonl"
        output_dir = tmp_path / "pr-review-2"
        _write_jsonl(
            session,
            [
                _assistant(_call("t1", "Task", prompt=_agent_prompt(output_dir))),
                _result("t1", "Finished successfully\nagentId: legacy-7"),
            ],
        )

        result = correlate_run_agents(session, output_dir, {"security-reviewer"})
        assert result[0]["agent_id"] == "legacy-7"
        assert result[0]["transcript"].endswith(
            "legacy/subagents/agent-legacy-7.jsonl"
        )

    def test_supports_tool_result_structured_data_embedded_on_block(self, tmp_path):
        session = tmp_path / "embedded.jsonl"
        output_dir = tmp_path / "pr-review-embedded"
        embedded = {
            "type": "tool_result",
            "tool_use_id": "embedded-result",
            "content": "done",
            "is_error": False,
            "toolUseResult": {
                "agentId": "embedded-agent",
                "resolvedModel": "claude-haiku-4-5",
            },
        }
        _write_jsonl(
            session,
            [
                _assistant(
                    _call("embedded-result", "Agent", prompt=_agent_prompt(output_dir))
                ),
                {"type": "user", "message": {"content": [embedded]}},
            ],
        )

        result = correlate_run_agents(session, output_dir, {"security-reviewer"})
        assert result[0]["agent_id"] == "embedded-agent"
        assert result[0]["model"] == "claude-haiku-4-5"

    def test_rejects_arbitrary_resolved_model_values(self, tmp_path):
        session = tmp_path / "unsafe-model.jsonl"
        output_dir = tmp_path / "pr-review-model"
        _write_jsonl(
            session,
            [
                _assistant(_call("a1", "Agent", prompt=_agent_prompt(output_dir))),
                _result(
                    "a1",
                    structured={
                        "agentId": "safe-agent-id",
                        "resolvedModel": "PRIVATE_SECRET_SENTINEL",
                    },
                ),
            ],
        )

        result = correlate_run_agents(session, output_dir, {"security-reviewer"})
        assert result[0]["model"] is None

    def test_special_agent_uses_recognized_subagent_type(self, tmp_path):
        session = tmp_path / "special.jsonl"
        output_dir = tmp_path / "pr-review-3"
        _write_jsonl(
            session,
            [
                _assistant(
                    _call(
                        "s1",
                        "Agent",
                        prompt=f"Reconcile artifacts\n- Output directory: `{output_dir}`",
                        subagent_type="review-reconciliator",
                        description="Reconcile the review",
                    )
                ),
                _result("s1", structured={"agentId": "agent-special"}),
            ],
        )

        result = correlate_run_agents(session, output_dir, {"review-reconciliator"})
        assert result[0]["agent"] == "review-reconciliator"
        assert result[0]["agent_id"] == "agent-special"
        assert result[0]["transcript"].endswith("agent-special.jsonl")

    @pytest.mark.parametrize(
        "label",
        [
            "Output directory: {output_dir}",
            "- Output directory: `{output_dir}`",
            "**Output directory:** `{output_dir}`",
            "Output directory:\n`{output_dir}`",
            "- **Output directory:**\n  {output_dir}",
        ],
        ids=["plain", "list", "bold", "split-backtick", "split-list-bold"],
    )
    def test_special_agent_accepts_observed_output_directory_labels(
        self, tmp_path, label
    ):
        session = tmp_path / "special-label.jsonl"
        output_dir = tmp_path / "run"
        _write_jsonl(
            session,
            [
                _assistant(
                    _call(
                        "special",
                        "Agent",
                        prompt=(
                            "Synthesize the review.\n"
                            + label.format(output_dir=output_dir)
                            + "\nUse the existing artifacts."
                        ),
                        subagent_type="review-reconciliator",
                    )
                ),
                _result("special", structured={"agentId": "synthesis-agent"}),
            ],
        )

        result = correlate_run_agents(
            session, output_dir, {"review-reconciliator"}
        )

        assert [item["agent_id"] for item in result] == ["synthesis-agent"]

    @pytest.mark.parametrize(
        "prompt_template",
        [
            "Output directory: {output_dir}\nOutput directory: {output_dir}",
            "Output directory:\nTrailing instructions without a path",
            "Output directory: $OUTPUT_DIR",
            "Output directory: {other_dir}",
        ],
        ids=["duplicate", "missing-next-line-value", "unresolved", "mismatch"],
    )
    def test_special_agent_rejects_ambiguous_or_invalid_output_labels(
        self, tmp_path, prompt_template
    ):
        session = tmp_path / "invalid-special-label.jsonl"
        output_dir = tmp_path / "run"
        _write_jsonl(
            session,
            [
                _assistant(
                    _call(
                        "special",
                        "Agent",
                        prompt=prompt_template.format(
                            output_dir=output_dir,
                            other_dir=tmp_path / "other",
                        ),
                        subagent_type="review-reconciliator",
                    )
                ),
                _result("special", structured={"agentId": "wrong-run"}),
            ],
        )

        assert correlate_run_agents(
            session, output_dir, {"review-reconciliator"}
        ) == []

    def test_regular_reviewer_description_cannot_replace_exact_agent_argument(self, tmp_path):
        session = tmp_path / "description-only.jsonl"
        output_dir = tmp_path / "pr-review-description"
        _write_jsonl(
            session,
            [
                _assistant(
                    _call(
                        "description-only",
                        "Agent",
                        prompt=f"Inspect {output_dir}",
                        description="security-reviewer",
                    )
                ),
                _result("description-only", structured={"agentId": "wrong"}),
            ],
        )

        assert correlate_run_agents(session, output_dir, {"security-reviewer"}) == []

    def test_excludes_other_runs_prefix_collisions_unrecognized_and_unresolved(self, tmp_path):
        session = tmp_path / "mixed.jsonl"
        output_dir = tmp_path / "pr-review-4"
        other_dir = tmp_path / "pr-review-5"
        calls = [
            _call("exact", "Agent", prompt=_agent_prompt(output_dir)),
            _call("other", "Agent", prompt=_agent_prompt(other_dir)),
            _call("prefix", "Agent", prompt=_agent_prompt(Path(f"{output_dir}-old"))),
            _call("unknown", "Agent", prompt=_agent_prompt(output_dir, "mystery-agent")),
            _call("unresolved", "Agent", prompt=_agent_prompt(output_dir)),
        ]
        _write_jsonl(
            session,
            [
                _assistant(*calls),
                _result("exact", structured={"agentId": "right"}),
                _result("other", structured={"agentId": "wrong-run"}),
                _result("prefix", structured={"agentId": "wrong-prefix"}),
                _result("unknown", structured={"agentId": "wrong-agent"}),
                _result("unresolved", "completed without an identifier"),
            ],
        )

        result = correlate_run_agents(session, output_dir, {"security-reviewer"})
        assert [item["agent_id"] for item in result] == ["right"]

    def test_excludes_duplicate_tool_results_and_duplicate_normalized_agent_ids(self, tmp_path):
        session = tmp_path / "duplicates.jsonl"
        output_dir = tmp_path / "pr-review-6"
        _write_jsonl(
            session,
            [
                _assistant(
                    _call("dup-result", "Agent", prompt=_agent_prompt(output_dir)),
                    _call("first-id", "Agent", prompt=_agent_prompt(output_dir)),
                    _call("second-id", "Agent", prompt=_agent_prompt(output_dir)),
                ),
                _result("dup-result", structured={"agentId": "result-dup"}),
                _result("dup-result", structured={"agentId": "result-dup"}),
                _result("first-id", structured={"agentId": "same-id"}),
                _result("second-id", structured={"agentId": "agent-same-id"}),
            ],
        )

        assert correlate_run_agents(session, output_dir, {"security-reviewer"}) == []

    def test_rejects_result_that_precedes_call_and_reused_tool_id(self, tmp_path):
        session = tmp_path / "chronology.jsonl"
        output_dir = tmp_path / "run"
        _write_jsonl(
            session,
            [
                _result("before", structured={"agentId": "before-agent"}),
                _assistant(_call("before", "Agent", prompt=_agent_prompt(output_dir))),
                _assistant(
                    _call("reused", "Agent", prompt=_agent_prompt(output_dir)),
                    _call("reused", "Agent", prompt=_agent_prompt(output_dir)),
                ),
                _result("reused", structured={"agentId": "reused-agent"}),
            ],
        )

        assert correlate_run_agents(session, output_dir, {"security-reviewer"}) == []


class TestAnalyzeSubagent:
    def test_sums_cache_aware_usage_once_and_attributes_safe_models(self, tmp_path):
        transcript = _write_jsonl(
            tmp_path / "agent.jsonl",
            [
                _assistant(usage=_usage(2, 3, create=5, read=7)),
                _assistant(
                    usage=_usage(11, 13, create=17, read=19),
                    model="claude-opus-4-1",
                    entry_usage=True,
                    message_id="message-with-repeated-jsonl-blocks",
                ),
                _assistant(
                    _call("same-message-block", "Glob", pattern="safe"),
                    usage=_usage(11, 13, create=17, read=19),
                    model="claude-opus-4-1",
                    entry_usage=True,
                    message_id="message-with-repeated-jsonl-blocks",
                ),
                {"type": "progress", "usage": _usage(1000, 1000)},
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])
        assert result["usage"] == {
            "input_tokens": 13,
            "cache_creation_input_tokens": 22,
            "cache_read_input_tokens": 26,
            "effective_input_tokens": 61,
            "output_tokens": 16,
        }
        assert result["usage_by_model"]["claude-opus-4-1"]["output_tokens"] == 13

    def test_repeated_message_id_counts_final_cumulative_usage(self, tmp_path):
        """A response split across records shares message.id; later records
        carry the cumulative output count, so the last one is authoritative."""
        transcript = _write_jsonl(
            tmp_path / "agent.jsonl",
            [
                _assistant(
                    usage=_usage(2, 7, create=26089, read=8813),
                    model="claude-opus-4-1",
                    message_id="split-response",
                ),
                _assistant(
                    usage=_usage(2, 7, create=26089, read=8813),
                    model="claude-opus-4-1",
                    message_id="split-response",
                ),
                _assistant(
                    usage=_usage(2, 484, create=26089, read=8813),
                    model="claude-opus-4-1",
                    message_id="split-response",
                ),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])
        assert result["usage"] == {
            "input_tokens": 2,
            "cache_creation_input_tokens": 26089,
            "cache_read_input_tokens": 8813,
            "effective_input_tokens": 34904,
            "output_tokens": 484,
        }
        assert result["usage_by_model"]["claude-opus-4-1"]["output_tokens"] == 484

    def test_task_notification_aggregate_usage_contributes_no_tokens(
        self, tmp_path
    ):
        transcript = _write_jsonl(
            tmp_path / "task-notification.jsonl",
            [
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "<task-notification><usage>"
                                    "<subagent_tokens>9876</subagent_tokens>"
                                    "</usage></task-notification>"
                                ),
                            }
                        ],
                    },
                },
                _assistant(
                    usage=_usage(2, 3, create=5, read=7),
                    model="claude-opus-4-1",
                ),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])

        expected_usage = {
            "input_tokens": 2,
            "cache_creation_input_tokens": 5,
            "cache_read_input_tokens": 7,
            "effective_input_tokens": 14,
            "output_tokens": 3,
        }
        assert result["usage"] == expected_usage
        assert result["usage_by_model"] == {
            "claude-opus-4-1": expected_usage
        }

    def test_write_script_only_is_not_a_builder_attempt_or_recovery(self, tmp_path):
        secret = "PRIVATE_PROMPT_SENTINEL"
        transcript = _write_jsonl(
            tmp_path / "write-script.jsonl",
            [
                _assistant(
                    _call(
                        "first",
                        "Write",
                        file_path="/private/tmp/review-output.py",
                        content=(
                            f"builder = ReviewOutputBuilder({secret!r})\n"
                            "builder.save('/safe')"
                        ),
                    )
                ),
                _result("first", "File has not been read yet", is_error=None),
                _assistant(
                    _call(
                        "second",
                        "Write",
                        file_path="/private/tmp/review-output-unique.py",
                        content=(
                            "output = ReviewOutputBuilder('safe')\n"
                            "output.save('/safe')"
                        ),
                    )
                ),
                _result("second", "created", is_error=False),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])

        assert result["artifact_writes"] == {
            "builder_attempted": False,
            "builder_attempts": 0,
            "builder_successes": 0,
            "builder_failures": 0,
            "first_builder_attempt_succeeded": None,
            "recovered": False,
        }
        assert result["tool_failures"][0]["category"] == "write_requires_read"
        assert result["tool_failures"][0]["operation_class"] == "write"
        assert result["tool_failures"][0]["recovered"] is False
        assert secret not in " ".join(_flatten_strings(result))

    def test_real_bootstrap_builder_envelope_is_counted_as_one_attempt(
        self, tmp_path
    ):
        output_dir = tmp_path / "review output"
        bootstrap_output = _bootstrap_mod.build_output(
            agent_name="security-reviewer",
            plugin_root=str(PLUGIN_ROOT),
            status="OK",
            review_rules="",
            domain_rules=None,
            scope_output="=== REVIEW SCOPE ===\nSTATUS: OK",
            exploration_scope=None,
            output_dir=str(output_dir),
            pr_number="42",
            reviewer_name="security",
        )
        command_start = bootstrap_output.index("PIRATEGOAT_PLUGIN_ROOT=")
        command_end = bootstrap_output.index("\nPY", command_start) + len("\nPY")
        command = bootstrap_output[command_start:command_end]
        transcript = _write_jsonl(
            tmp_path / "real-bootstrap-envelope.jsonl",
            [
                _assistant(_call("builder", "Bash", command=command)),
                _result(
                    "builder",
                    "RECORDED COUNTS: safe",
                    is_error=None,
                    structured={"exitCode": 0},
                ),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])

        assert result["artifact_writes"] == {
            "builder_attempted": True,
            "builder_attempts": 1,
            "builder_successes": 1,
            "builder_failures": 0,
            "first_builder_attempt_succeeded": True,
            "recovered": False,
        }
        assert command not in " ".join(_flatten_strings(result))

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param(
                _builder_envelope(
                    "print('safe')",
                    header=(
                        "PIRATEGOAT_PLUGIN_ROOT='/plugin root' "
                        '"PIRATEGOAT_OUTPUT_DIR=/review output" '
                        "PIRATEGOAT_REVIEWER_NAME=security "
                        "PIRATEGOAT_PR_ID='42' python3 <<'PY'"
                    ),
                ),
                id="harmless-quoting",
            ),
            pytest.param(
                _builder_envelope(
                    "print('safe')",
                    header=(
                        "PIRATEGOAT_PR_ID=42 PIRATEGOAT_REVIEWER_NAME=security "
                        "PIRATEGOAT_OUTPUT_DIR=/output "
                        "PIRATEGOAT_PLUGIN_ROOT=/plugin python3 <<PY"
                    ),
                ),
                id="assignment-order",
            ),
            pytest.param(
                _builder_envelope("builder.add_positive(observation)"),
                id="variable-reference",
            ),
            pytest.param(
                _builder_envelope('builder.add_positive(f"{observation}")'),
                id="f-string",
            ),
            pytest.param(
                _builder_envelope('builder.add_positive("left" + "right")'),
                id="concatenation",
            ),
            pytest.param(
                _builder_envelope("raise SystemExit(0)"),
                id="early-exit",
            ),
            pytest.param(
                _builder_envelope("this is not: valid python"),
                id="invalid-python-body",
            ),
            pytest.param(
                _builder_envelope(
                    "print('safe')",
                    header=(
                        "PIRATEGOAT_PLUGIN_ROOT= PIRATEGOAT_OUTPUT_DIR= "
                        "PIRATEGOAT_REVIEWER_NAME= PIRATEGOAT_PR_ID= python3 <<PY"
                    ),
                ),
                id="empty-assignment-values",
            ),
            pytest.param(
                _builder_envelope(None),
                id="missing-body-and-footer",
            ),
        ],
    )
    def test_pipeline_builder_envelope_is_one_attempt_regardless_body(
        self, tmp_path, command
    ):
        transcript = _write_jsonl(
            tmp_path / "builder-envelope.jsonl",
            [
                _assistant(_call("builder", "Bash", command=command)),
                _result(
                    "builder",
                    "RECORDED COUNTS: safe",
                    is_error=None,
                    structured={"exitCode": 0},
                ),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])

        assert is_bootstrap_builder_heredoc(command) is True
        assert result["artifact_writes"] == {
            "builder_attempted": True,
            "builder_attempts": 1,
            "builder_successes": 1,
            "builder_failures": 0,
            "first_builder_attempt_succeeded": True,
            "recovered": False,
        }
        assert command not in " ".join(_flatten_strings(result))

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param(
                "PIRATEGOAT_PLUGIN_ROOT=/plugin PIRATEGOAT_OUTPUT_DIR=/output "
                "PIRATEGOAT_REVIEWER_NAME=security python3 <<PY\npass\nPY",
                id="missing-required-assignment",
            ),
            pytest.param(
                "PIRATEGOAT_PLUGIN_ROOT=/plugin PIRATEGOAT_OUTPUT_DIR=/output "
                "PIRATEGOAT_REVIEWER_NAME=security PIRATEGOAT_PR_ID=42 EXTRA=safe "
                "python3 <<PY\npass\nPY",
                id="extra-assignment",
            ),
            pytest.param(
                "PIRATEGOAT_PLUGIN_ROOT=/plugin PIRATEGOAT_PLUGIN_ROOT=/other "
                "PIRATEGOAT_OUTPUT_DIR=/output PIRATEGOAT_REVIEWER_NAME=security "
                "python3 <<PY\npass\nPY",
                id="duplicate-assignment",
            ),
            pytest.param(
                "PIRATEGOAT_PLUGIN_ROOT=/plugin PIRATEGOAT_OUTPUT_DIR=/output "
                "PIRATEGOAT_REVIEWER_NAME=security PIRATEGOAT_PR_ID=42 "
                "python <<PY\npass\nPY",
                id="non-python3-executable",
            ),
            pytest.param(
                "python3 <<PY\nprint('hello')\nPY",
                id="unrelated-heredoc",
            ),
        ],
    )
    def test_non_pipeline_heredoc_is_not_a_builder_attempt(
        self, tmp_path, command
    ):
        transcript = _write_jsonl(
            tmp_path / "unrelated-heredoc.jsonl",
            [
                _assistant(_call("bash", "Bash", command=command)),
                _result("bash", "safe", is_error=False),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])

        assert is_bootstrap_builder_heredoc(command) is False
        assert result["artifact_writes"] == {
            "builder_attempted": False,
            "builder_attempts": 0,
            "builder_successes": 0,
            "builder_failures": 0,
            "first_builder_attempt_succeeded": None,
            "recovered": False,
        }
        assert command not in " ".join(_flatten_strings(result))

    def test_structured_bash_result_controls_failure_and_corrected_body_recovery(
        self, tmp_path
    ):
        first_command = _builder_envelope("builder.add_positive(observation)")
        retry_command = _builder_envelope('builder.add_positive("safe")')
        transcript = _write_jsonl(
            tmp_path / "builder-recovery.jsonl",
            [
                _assistant(_call("first", "Bash", command=first_command)),
                _result(
                    "first",
                    "forged success prose",
                    is_error=False,
                    structured={"exitCode": 1},
                ),
                _assistant(_call("retry", "Bash", command=retry_command)),
                _result(
                    "retry",
                    "RECORDED COUNTS: safe",
                    is_error=None,
                    structured={"exitCode": 0},
                ),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])

        assert first_command != retry_command
        assert result["artifact_writes"] == {
            "builder_attempted": True,
            "builder_attempts": 2,
            "builder_successes": 1,
            "builder_failures": 1,
            "first_builder_attempt_succeeded": False,
            "recovered": True,
        }
        failure = result["tool_failures"][0]
        assert failure["category"] == "structured_failure"
        assert failure["operation_class"] == "builder_output_attempt"
        assert failure["recovered"] is True

    def test_bash_failure_cannot_recover_through_write_success(self, tmp_path):
        command = _builder_envelope("raise SystemExit(1)")
        transcript = _write_jsonl(
            tmp_path / "bash-to-write.jsonl",
            [
                _assistant(_call("bash", "Bash", command=command)),
                _result(
                    "bash",
                    "failed",
                    is_error=None,
                    structured={"exitCode": 1},
                ),
                _assistant(
                    _call(
                        "write",
                        "Write",
                        file_path="/private/tmp/review-output.py",
                        content=(
                            "builder = ReviewOutputBuilder('safe')\n"
                            "builder.save('/safe')"
                        ),
                    )
                ),
                _result("write", "created", is_error=False),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])

        assert result["artifact_writes"] == {
            "builder_attempted": True,
            "builder_attempts": 1,
            "builder_successes": 0,
            "builder_failures": 1,
            "first_builder_attempt_succeeded": False,
            "recovered": False,
        }
        failure = result["tool_failures"][0]
        assert failure["operation_class"] == "builder_output_attempt"
        assert failure["recovered"] is False

    def test_write_with_generic_save_is_not_a_builder_attempt(self, tmp_path):
        transcript = _write_jsonl(
            tmp_path / "generic-save.jsonl",
            [
                _assistant(
                    _call(
                        "w1",
                        "Write",
                        file_path="/private/tmp/model.py",
                        content="model.save(artifact)",
                    )
                ),
                _result("w1"),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])
        assert result["artifact_writes"]["builder_attempted"] is False
        assert result["artifact_writes"]["builder_attempts"] == 0

    def test_no_builder_attempt_is_distinct_from_builder_failure(self, tmp_path):
        transcript = _write_jsonl(
            tmp_path / "reads-only.jsonl",
            [_assistant(_call("r1", "Read", file_path=str(tmp_path / "a.py"))), _result("r1")],
        )

        result = analyze_subagent(transcript, tmp_path, [])
        assert result["artifact_writes"]["builder_attempted"] is False
        assert result["artifact_writes"]["first_builder_attempt_succeeded"] is None

    @pytest.mark.parametrize(
        "content,category",
        [
            ("Sibling tool call errored", "sibling_tool_failure"),
            ("<tool_use_error>Invalid request</tool_use_error>", "tool_use_error"),
            ("API Error: overloaded", "api_error"),
        ],
    )
    def test_detects_allowlisted_text_failure_signatures(self, tmp_path, content, category):
        transcript = _write_jsonl(
            tmp_path / f"{category}.jsonl",
            [
                _assistant(_call("x", "Read", file_path=str(tmp_path / "safe.py"))),
                _result("x", content, is_error=None),
            ],
        )

        failure = analyze_subagent(transcript, tmp_path, [])["tool_failures"][0]
        assert failure["category"] == category
        assert failure["detector"] == "signature"

    @pytest.mark.parametrize(
        "content",
        [
            "File has not been read yet",
            "Sibling tool call errored",
            "<tool_use_error>stale text</tool_use_error>",
            "API Error: stale text",
        ],
        ids=["read-first", "sibling", "tool-use", "api"],
    )
    def test_explicit_structured_success_ignores_failure_signatures(
        self, tmp_path, content
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        run_dir = tmp_path / "run"
        transcript = _write_jsonl(
            tmp_path / "structured-success.jsonl",
            [
                _assistant(
                    _call("read", "Read", file_path=str(repo / "src/safe.py"))
                ),
                _result(
                    "read",
                    content,
                    is_error=False,
                    structured={"exitCode": 0, "interrupted": False},
                ),
                _assistant(
                    _call(
                        "step",
                        "Bash",
                        command=(
                            "python3 /plugin/review/pipeline.py --step 1 "
                            f'--output-dir "{run_dir}"'
                        ),
                    ),
                    usage=_usage(1, 1),
                ),
                _result(
                    "step",
                    content,
                    is_error=False,
                    structured={"exitCode": 0, "interrupted": False},
                ),
                _assistant(usage=_usage(2, 2)),
            ],
        )

        analysis = analyze_subagent(transcript, repo, ["src/safe.py"])
        assert analysis["tool_failures"] == []
        assert analysis["observed_reads"]["all"] == ["src/safe.py"]

    @pytest.mark.parametrize(
        "structured",
        [
            {"interrupted": False},
            {"status": "started"},
            {"status": "running"},
            {"status": "pending"},
        ],
        ids=["not-interrupted", "started", "running", "pending"],
    )
    def test_nonterminal_structured_fields_defer_to_failure_signatures(
        self, tmp_path, structured
    ):
        target = tmp_path / "safe.py"
        transcript = _write_jsonl(
            tmp_path / "nonterminal.jsonl",
            [
                _assistant(_call("read", "Read", file_path=str(target))),
                _result(
                    "read",
                    "API Error: deterministic failure",
                    is_error=None,
                    structured=structured,
                ),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])
        assert result["tool_failures"][0]["category"] == "api_error"
        assert result["tool_failures"][0]["detector"] == "signature"
        assert result["observed_reads"]["all"] == []

    @pytest.mark.parametrize(
        "structured",
        [
            {"interrupted": False},
            {"status": "started"},
            {"status": "running"},
            {"status": "pending"},
        ],
        ids=["not-interrupted", "started", "running", "pending"],
    )
    def test_nonterminal_structured_fields_without_signature_remain_unknown(
        self, tmp_path, structured
    ):
        target = tmp_path / "safe.py"
        transcript = _write_jsonl(
            tmp_path / "nonterminal-unknown.jsonl",
            [
                _assistant(_call("read", "Read", file_path=str(target))),
                _result(
                    "read",
                    "ordinary progress",
                    is_error=None,
                    structured=structured,
                ),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])
        assert result["tool_failures"] == []
        assert result["observed_reads"]["all"] == []

    @pytest.mark.parametrize(
        "status",
        ["success", "succeeded", "complete", "completed"],
    )
    def test_terminal_success_status_takes_precedence_over_failure_signature(
        self, tmp_path, status
    ):
        target = tmp_path / "safe.py"
        transcript = _write_jsonl(
            tmp_path / f"terminal-{status}.jsonl",
            [
                _assistant(_call("read", "Read", file_path=str(target))),
                _result(
                    "read",
                    "API Error: stale text",
                    is_error=None,
                    structured={"status": status},
                ),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])
        assert result["tool_failures"] == []
        assert result["observed_reads"]["all"] == ["safe.py"]

    def test_current_read_shape_is_success_before_textual_signatures(self, tmp_path):
        secret = "PRIVATE_STRUCTURED_RESULT_SENTINEL"
        repo = tmp_path / "repo"
        target = repo / "src" / "safe.py"
        repo.mkdir()
        structured = _current_read_result(f"/private/{secret}.py")
        structured["file"]["content"] = secret
        transcript = _write_jsonl(
            tmp_path / "current-read.jsonl",
            [
                _assistant(_call("read", "Read", file_path=str(target))),
                _result(
                    "read",
                    "API Error: this is file text, not tool state",
                    is_error=None,
                    structured=structured,
                ),
            ],
        )

        result = analyze_subagent(transcript, repo, ["src/safe.py"])
        assert result["tool_failures"] == []
        assert result["observed_reads"]["all"] == ["src/safe.py"]
        assert secret not in " ".join(_flatten_strings(result))

    def test_current_read_shape_accepts_boolean_token_cap_metadata(self, tmp_path):
        secret = "PRIVATE_TRUNCATED_READ_SENTINEL"
        repo = tmp_path / "repo"
        target = repo / "src" / "safe.py"
        repo.mkdir()
        structured = _current_read_result(f"/private/{secret}.py")
        structured["file"]["content"] = f"API Error: {secret}"
        structured["file"]["truncatedByTokenCap"] = True
        transcript = _write_jsonl(
            tmp_path / "current-read-truncated.jsonl",
            [
                _assistant(_call("read", "Read", file_path=str(target))),
                _result(
                    "read",
                    "API Error: this is file text, not tool state",
                    is_error=None,
                    structured=structured,
                ),
            ],
        )

        result = analyze_subagent(transcript, repo, ["src/safe.py"])
        assert result["tool_failures"] == []
        assert result["observed_reads"]["all"] == ["src/safe.py"]
        assert secret not in " ".join(_flatten_strings(result))

    def test_current_write_shape_does_not_count_as_builder_attempt(self, tmp_path):
        target = "/private/tmp/review-output.py"
        transcript = _write_jsonl(
            tmp_path / "current-write.jsonl",
            [
                _assistant(
                    _call(
                        "write",
                        "Write",
                        file_path=target,
                        content=(
                            "builder = ReviewOutputBuilder('safe')\n"
                            "builder.save('/safe')"
                        ),
                    )
                ),
                _result(
                    "write",
                    "created",
                    is_error=None,
                    structured=_current_write_result(target),
                ),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])
        assert result["artifact_writes"] == {
            "builder_attempted": False,
            "builder_attempts": 0,
            "builder_successes": 0,
            "builder_failures": 0,
            "first_builder_attempt_succeeded": None,
            "recovered": False,
        }

    def test_current_write_update_shape_recovers_prior_write_failure(self, tmp_path):
        target = str(tmp_path / "safe.py")
        transcript = _write_jsonl(
            tmp_path / "current-write-update.jsonl",
            [
                _assistant(
                    _call("first", "Write", file_path=target, content="safe")
                ),
                _result("first", "API Error: retry", is_error=None),
                _assistant(
                    _call("second", "Write", file_path=target, content="safe")
                ),
                _result(
                    "second",
                    "updated",
                    is_error=None,
                    structured=_current_write_result(target, update=True),
                ),
            ],
        )

        failure = analyze_subagent(transcript, tmp_path, [])["tool_failures"][0]
        assert failure["tool"] == "Write"
        assert failure["recovered"] is True

    def test_current_write_update_with_null_original_recovers_ordinary_write(
        self, tmp_path
    ):
        target = str(tmp_path / "review-output.py")
        builder = (
            "builder = ReviewOutputBuilder('safe')\n"
            "builder.save('/safe')"
        )
        structured = _current_write_result(target, update=True)
        structured["originalFile"] = None
        transcript = _write_jsonl(
            tmp_path / "current-write-update-null-original.jsonl",
            [
                _assistant(
                    _call("first", "Write", file_path=target, content=builder)
                ),
                _result("first", "API Error: retry", is_error=None),
                _assistant(
                    _call("second", "Write", file_path=target, content=builder)
                ),
                _result(
                    "second",
                    "updated",
                    is_error=None,
                    structured=structured,
                ),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])
        assert result["artifact_writes"] == {
            "builder_attempted": False,
            "builder_attempts": 0,
            "builder_successes": 0,
            "builder_failures": 0,
            "first_builder_attempt_succeeded": None,
            "recovered": False,
        }
        assert result["tool_failures"][0]["operation_class"] == "write"
        assert result["tool_failures"][0]["recovered"] is True

    @pytest.mark.parametrize(
        "original_file,stale",
        [("before", False), (None, True)],
        ids=["ordinary", "stale-recovered-null-original"],
    )
    def test_current_edit_shape_recovers_a_prior_edit_failure(
        self, tmp_path, original_file, stale
    ):
        target = str(tmp_path / "safe.py")
        transcript = _write_jsonl(
            tmp_path / "current-edit.jsonl",
            [
                _assistant(
                    _call(
                        "first",
                        "Edit",
                        file_path=target,
                        old_string="before",
                        new_string="after",
                    )
                ),
                _result("first", "API Error: retry", is_error=None),
                _assistant(
                    _call(
                        "second",
                        "Edit",
                        file_path=target,
                        old_string="before",
                        new_string="after",
                    )
                ),
                _result(
                    "second",
                    "edited",
                    is_error=None,
                    structured=_current_edit_result(
                        target, original_file=original_file, stale=stale
                    ),
                ),
            ],
        )

        failure = analyze_subagent(transcript, tmp_path, [])["tool_failures"][0]
        assert failure["tool"] == "Edit"
        assert failure["recovered"] is True
        assert failure["recovery"] == "later_success"

    def test_current_edit_shape_does_not_recover_a_different_target(self, tmp_path):
        first_target = str(tmp_path / "first.py")
        second_target = str(tmp_path / "second.py")
        transcript = _write_jsonl(
            tmp_path / "current-edit-other-target.jsonl",
            [
                _assistant(
                    _call(
                        "first",
                        "Edit",
                        file_path=first_target,
                        old_string="before",
                        new_string="after",
                    )
                ),
                _result("first", "API Error: retry", is_error=None),
                _assistant(
                    _call(
                        "second",
                        "Edit",
                        file_path=second_target,
                        old_string="before",
                        new_string="after",
                    )
                ),
                _result(
                    "second",
                    "edited",
                    is_error=None,
                    structured=_current_edit_result(second_target),
                ),
            ],
        )

        failure = analyze_subagent(transcript, tmp_path, [])["tool_failures"][0]
        assert failure["tool"] == "Edit"
        assert failure["recovered"] is False

    @pytest.mark.parametrize(
        "tool_name,tool_input,structured",
        [
            (
                "Read",
                {"file_path": "/safe/unexpected-type.py"},
                _current_read_result("/safe/unexpected-type.py")
                | {"type": "unexpected"},
            ),
            (
                "Read",
                {"file_path": "/safe/metadata.py"},
                {"filePath": "/safe/metadata.py"},
            ),
            (
                "Read",
                {"file_path": "/safe/read.py"},
                {
                    "type": "text",
                    "file": {
                        "filePath": "/safe/read.py",
                        "content": "safe",
                        "numLines": True,
                        "startLine": 1,
                        "totalLines": 1,
                    },
                },
            ),
            (
                "Read",
                {"file_path": "/safe/truncated-type.py"},
                {
                    "type": "text",
                    "file": _current_read_result(
                        "/safe/truncated-type.py"
                    )["file"]
                    | {"truncatedByTokenCap": "true"},
                },
            ),
            (
                "Read",
                {"file_path": "/safe/unrelated-metadata.py"},
                {
                    "type": "text",
                    "file": _current_read_result(
                        "/safe/unrelated-metadata.py"
                    )["file"]
                    | {"unrelated": False},
                },
            ),
            (
                "Write",
                {
                    "file_path": "/safe/write.py",
                    "content": (
                        "builder = ReviewOutputBuilder('safe')\n"
                        "builder.save('/safe')"
                    ),
                },
                {
                    "type": "create",
                    "content": "safe",
                    "filePath": "/safe/write.py",
                    "originalFile": None,
                    "structuredPatch": _structured_patch(),
                    "userModified": False,
                },
            ),
            (
                "Write",
                {
                    "file_path": "/safe/unexpected-type.py",
                    "content": (
                        "builder = ReviewOutputBuilder('safe')\n"
                        "builder.save('/safe')"
                    ),
                },
                _current_write_result("/safe/unexpected-type.py")
                | {"type": "unexpected"},
            ),
            (
                "Write",
                {
                    "file_path": "/safe/update-crossed.py",
                    "content": (
                        "builder = ReviewOutputBuilder('safe')\n"
                        "builder.save('/safe')"
                    ),
                },
                _current_write_result("/safe/update-crossed.py")
                | {"type": "update"},
            ),
            (
                "Write",
                {
                    "file_path": "/safe/update-bad-patch.py",
                    "content": (
                        "builder = ReviewOutputBuilder('safe')\n"
                        "builder.save('/safe')"
                    ),
                },
                _current_write_result("/safe/update-bad-patch.py", update=True)
                | {
                    "originalFile": None,
                    "structuredPatch": [{"oldStart": 1}],
                },
            ),
            (
                "Edit",
                {
                    "file_path": "/safe/edit.py",
                    "old_string": "before",
                    "new_string": "after",
                },
                {
                    "filePath": "/safe/edit.py",
                    "oldString": "before",
                    "newString": "after",
                    "originalFile": "before",
                    "replaceAll": False,
                    "structuredPatch": [{"oldStart": 1}],
                    "userModified": False,
                },
            ),
        ],
        ids=[
            "read-unexpected-type",
            "read-metadata-only",
            "read-bool-line-count",
            "read-token-cap-wrong-type",
            "read-unrelated-file-key",
            "write-create-with-patch",
            "write-unexpected-type",
            "write-update-null-original-empty-patch",
            "write-update-null-original-bad-patch",
            "edit-bad-patch",
        ],
    )
    def test_near_miss_tool_shapes_remain_unknown(
        self, tmp_path, tool_name, tool_input, structured
    ):
        operation = {
            "Read": "read",
            "Write": "write",
            "Edit": "edit",
        }[tool_name]
        assert result_state(
            {"block": {"content": "ordinary result"}, "structured": structured},
            tool_name,
            operation,
        )[0] == "unknown"
        transcript = _write_jsonl(
            tmp_path / f"near-miss-{tool_name}.jsonl",
            [
                _assistant(_call("tool", tool_name, **tool_input)),
                _result("tool", "ordinary result", is_error=None, structured=structured),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])
        assert result["tool_failures"] == []
        assert result["observed_reads"]["all"] == []
        if tool_name == "Write":
            assert result["artifact_writes"]["builder_successes"] == 0
            assert result["artifact_writes"]["first_builder_attempt_succeeded"] is None

    def test_unknown_tool_cannot_reuse_read_success_shape(self, tmp_path):
        transcript = _write_jsonl(
            tmp_path / "unknown-tool-shape.jsonl",
            [
                _assistant(_call("first", "CustomTool", target="safe")),
                _result("first", "API Error: retry", is_error=None),
                _assistant(_call("second", "CustomTool", target="safe")),
                _result(
                    "second",
                    "ordinary result",
                    is_error=None,
                    structured=_current_read_result("/safe/read.py"),
                ),
            ],
        )

        failure = analyze_subagent(transcript, tmp_path, [])["tool_failures"][0]
        assert failure["tool"] == "Other"
        assert failure["recovered"] is False

    @pytest.mark.parametrize(
        "tool_name,tool_input,structured",
        [
            (
                "Write",
                {
                    "file_path": "/safe/write.py",
                    "content": (
                        "builder = ReviewOutputBuilder('safe')\n"
                        "builder.save('/safe')"
                    ),
                },
                _current_write_result("/safe/write.py"),
            ),
            (
                "Edit",
                {
                    "file_path": "/safe/edit.py",
                    "old_string": "before",
                    "new_string": "after",
                },
                _current_edit_result("/safe/edit.py"),
            ),
        ],
        ids=["write", "edit"],
    )
    def test_write_and_edit_signatures_override_structural_success(
        self, tmp_path, tool_name, tool_input, structured
    ):
        transcript = _write_jsonl(
            tmp_path / f"shape-signature-{tool_name}.jsonl",
            [
                _assistant(_call("tool", tool_name, **tool_input)),
                _result(
                    "tool",
                    "API Error: deterministic failure",
                    is_error=None,
                    structured=structured,
                ),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])
        assert result["tool_failures"][0]["category"] == "api_error"
        if tool_name == "Write":
            assert result["artifact_writes"]["builder_failures"] == 0

    def test_nonterminal_status_prevents_tool_shape_success(self, tmp_path):
        target = str(tmp_path / "safe.py")
        structured = _current_read_result(target) | {"status": "running"}
        transcript = _write_jsonl(
            tmp_path / "nonterminal-read.jsonl",
            [
                _assistant(_call("read", "Read", file_path=target)),
                _result(
                    "read",
                    "ordinary progress",
                    is_error=None,
                    structured=structured,
                ),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])
        assert result["tool_failures"] == []
        assert result["observed_reads"]["all"] == []

    @pytest.mark.parametrize(
        "tool_name,tool_input,structured",
        [
            (
                "Read",
                {"file_path": "/safe/read.py"},
                _current_read_result("/safe/read.py") | {"exitCode": 1},
            ),
            (
                "Write",
                {
                    "file_path": "/safe/write.py",
                    "content": (
                        "builder = ReviewOutputBuilder('safe')\n"
                        "builder.save('/safe')"
                    ),
                },
                _current_write_result("/safe/write.py") | {"error": "safe"},
            ),
            (
                "Edit",
                {
                    "file_path": "/safe/edit.py",
                    "old_string": "before",
                    "new_string": "after",
                },
                _current_edit_result("/safe/edit.py") | {"success": False},
            ),
        ],
        ids=["read", "write", "edit"],
    )
    def test_structured_failure_wins_tool_specific_shape(
        self, tmp_path, tool_name, tool_input, structured
    ):
        transcript = _write_jsonl(
            tmp_path / f"shape-failure-{tool_name}.jsonl",
            [
                _assistant(_call("tool", tool_name, **tool_input)),
                _result("tool", "ordinary result", is_error=None, structured=structured),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])
        assert result["tool_failures"][0]["category"] == "structured_failure"
        assert result["observed_reads"]["all"] == []
        if tool_name == "Write":
            assert result["artifact_writes"]["builder_failures"] == 0

    def test_structured_failure_takes_precedence_and_ordinary_retry_recovers(self, tmp_path):
        target = tmp_path / "notes.txt"
        transcript = _write_jsonl(
            tmp_path / "retry.jsonl",
            [
                _assistant(_call("w1", "Write", file_path=str(target), content="safe")),
                _result(
                    "w1",
                    "API Error: text fallback",
                    is_error=False,
                    structured={"exitCode": 2, "error": "private detail"},
                ),
                _assistant(_call("w2", "Write", file_path=str(target), content="safe")),
                _result("w2", "ok", is_error=False, structured={"exitCode": 0}),
            ],
        )

        failure = analyze_subagent(transcript, tmp_path, [])["tool_failures"][0]
        assert failure["category"] == "structured_failure"
        assert failure["detector"] == "structured"
        assert failure["operation_class"] == "write"
        assert failure["recovered"] is True
        assert failure["recovery"] == "later_success"

    @pytest.mark.parametrize(
        "block_error,structured",
        [
            (True, {"exitCode": 0, "success": True}),
            (False, {"exitCode": 2}),
            (False, {"interrupted": True, "success": True}),
            (False, {"status": "completed", "error": "structured error"}),
        ],
        ids=["error-flag-wins", "exit-wins", "interrupted-wins", "error-field-wins"],
    )
    def test_structured_failure_wins_conflicting_success_fields(
        self, tmp_path, block_error, structured
    ):
        transcript = _write_jsonl(
            tmp_path / "conflict.jsonl",
            [
                _assistant(
                    _call("read", "Read", file_path=str(tmp_path / "safe.py"))
                ),
                _result(
                    "read",
                    "File has not been read yet",
                    is_error=block_error,
                    structured=structured,
                ),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])
        assert result["tool_failures"][0]["category"] == "structured_failure"
        assert result["observed_reads"]["all"] == []

    def test_result_before_call_cannot_create_success_or_failure(self, tmp_path):
        transcript = _write_jsonl(
            tmp_path / "result-before-call.jsonl",
            [
                _result("w1", "API Error: before", is_error=True),
                _assistant(
                    _call(
                        "w1",
                        "Write",
                        file_path="/private/tmp/review.py",
                        content=(
                            "builder = ReviewOutputBuilder('safe')\n"
                            "builder.save('/safe')"
                        ),
                    )
                ),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])
        assert result["tool_failures"] == []
        assert result["artifact_writes"]["builder_successes"] == 0
        assert result["artifact_writes"]["builder_failures"] == 0
        assert result["artifact_writes"]["first_builder_attempt_succeeded"] is None

    def test_extracts_only_narrow_successful_repo_reads_and_classifies_scope(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        calls_and_results = [
            (
                _call("read", "Read", file_path=str(repo / "src/a.py")),
                _result(
                    "read",
                    is_error=False,
                    structured={"filePath": str(repo / "src/a.py")},
                ),
            ),
            (
                _call("read-out", "Read", file_path=str(repo / "tests/b.py")),
                _result("read-out", is_error=None),
            ),
            (
                _call("diff", "Bash", command="git diff HEAD~1 -- src/c.py tests/d.py"),
                _result("diff"),
            ),
            (_call("show", "Bash", command="git show HEAD:src/e.py"), _result("show")),
            (_call("cat", "Bash", command="cat -- src/f.py"), _result("cat")),
            (_call("head", "Bash", command="head -n 5 tests/g.py"), _result("head")),
            (_call("tail", "Bash", command="tail -20 src/h.py"), _result("tail")),
            (_call("wc", "Bash", command="wc -l tests/i.py"), _result("wc")),
            (
                _call(
                    "comment",
                    "Bash",
                    command="cat src/comment.py # tests/comment-invented.py",
                ),
                _result("comment"),
            ),
            (
                _call("tail-pid", "Bash", command="tail --pid 123 src/pid.py"),
                _result("tail-pid"),
            ),
            (
                _call("glob", "Bash", command="cat src/*.py"),
                _result("glob"),
            ),
            (_call("tilde", "Bash", command="cat ~/private.py"), _result("tilde")),
            (_call("variable", "Bash", command="cat $TARGET"), _result("variable")),
            (
                _call("operator", "Bash", command="cat src/operator.py | wc -l"),
                _result("operator"),
            ),
            (
                _call("redirect", "Bash", command="cat src/redir.py > result.txt"),
                _result("redirect"),
            ),
            (
                _call("braces", "Bash", command="cat src/{one,two}.py"),
                _result("braces"),
            ),
            (
                _call("brackets", "Bash", command="cat src/[ab].py"),
                _result("brackets"),
            ),
            (_call("outside", "Read", file_path="/outside/private.py"), _result("outside")),
            (_call("traverse", "Bash", command="cat ../escape.py"), _result("traverse")),
            (_call("arbitrary", "Bash", command="rg secret src/j.py"), _result("arbitrary")),
            (
                _call("failed", "Read", file_path=str(repo / "src/failed.py")),
                _result("failed", is_error=True),
            ),
        ]
        entries = []
        for call, result in calls_and_results:
            entries.extend([_assistant(call), result])
        transcript = _write_jsonl(tmp_path / "reads.jsonl", entries)

        observed = analyze_subagent(
            transcript,
            repo,
            [
                "src/a.py",
                "src/c.py",
                "src/comment.py",
                "src/e.py",
                "src/f.py",
                "src/h.py",
                "src/pid.py",
            ],
        )["observed_reads"]
        assert observed == {
            "all": [
                "src/a.py",
                "src/c.py",
                "src/comment.py",
                "src/e.py",
                "src/f.py",
                "src/h.py",
                "src/pid.py",
                "tests/b.py",
                "tests/d.py",
                "tests/g.py",
                "tests/i.py",
            ],
            "in_scope": [
                "src/a.py",
                "src/c.py",
                "src/comment.py",
                "src/e.py",
                "src/f.py",
                "src/h.py",
                "src/pid.py",
            ],
            "out_of_scope": ["tests/b.py", "tests/d.py", "tests/g.py", "tests/i.py"],
            "exhaustive": False,
        }


def test_orchestrator_usage_uses_manifest_events_not_multiline_stage_commands(tmp_path):
    session = tmp_path / "main.jsonl"
    run_dir = tmp_path / "run"
    manifest = _manifest("main", tmp_path, run_dir, started=[])
    manifest["steps"] = [
        {
            "event": "step",
            "step": 1,
            "timestamp": (_TEST_TRANSCRIPT_START + timedelta(seconds=10)).isoformat(),
        },
        {
            "event": "step",
            "step": 1,
            "timestamp": (_TEST_TRANSCRIPT_START + timedelta(seconds=10)).isoformat(),
        },
        {
            "event": "step",
            "step": 3,
            "timestamp": (_TEST_TRANSCRIPT_START + timedelta(seconds=20)).isoformat(),
        },
    ]
    multiline_stage = (
        f'OUTPUT_DIR="{run_dir}"\n'
        "python3 /plugin/review/pipeline.py \\\n"
        "  --step 2 \\\n"
        '  --output-dir "$OUTPUT_DIR"'
    )
    _write_jsonl(
        session,
        [
            _at(
                _assistant(
                    _call("no-event-stage", "Bash", command=multiline_stage),
                    usage=_usage(1, 1),
                ),
                5,
            ),
            _at(_result("no-event-stage", structured={"exitCode": 0}), 6),
            _at(_assistant(usage=_usage(2, 2)), 11),
            _at(
                _assistant(
                    _call("still-no-event", "Bash", command=multiline_stage),
                    usage=_usage(3, 3),
                ),
                15,
            ),
            _at(_result("still-no-event", structured={"exitCode": 0}), 16),
            _at(_assistant(usage=_usage(4, 4)), 21),
        ],
    )

    stages, complete = analyze_orchestrator_steps(session, manifest)

    assert complete is True
    assert stages["unattributed"]["output_tokens"] == 0
    assert stages["1"]["output_tokens"] == 6
    assert stages["3"]["output_tokens"] == 4


def test_orchestrator_starts_step_one_at_run_start_without_step_events(tmp_path):
    session = tmp_path / "step-one.jsonl"
    run_dir = tmp_path / "run"
    manifest = _manifest("step-one", tmp_path, run_dir, started=[])
    _write_jsonl(
        session,
        [
            _at(_assistant(usage=_usage(1, 2)), 0),
            _at(_assistant(usage=_usage(2, 3)), 30),
        ],
    )

    stages, complete = analyze_orchestrator_steps(session, manifest)

    assert complete is True
    assert stages["1"]["output_tokens"] == 5
    assert stages["unattributed"]["output_tokens"] == 0


def test_orchestrator_step_usage_counts_final_cumulative_record(tmp_path):
    """Repeated message.id records carry cumulative usage — the last record is
    authoritative and is attributed to the stage where the response began, so
    per-step totals agree with total and per-model usage."""
    session = tmp_path / "split-usage.jsonl"
    run_dir = tmp_path / "run"
    manifest = _manifest("split-usage", tmp_path, run_dir, started=[])
    manifest["steps"] = [
        {
            "event": "step",
            "step": 3,
            "timestamp": (_TEST_TRANSCRIPT_START + timedelta(seconds=10)).isoformat(),
        },
    ]
    _write_jsonl(
        session,
        [
            _at(_assistant(usage=_usage(2, 7), message_id="split-response"), 5),
            _at(_assistant(usage=_usage(2, 7), message_id="split-response"), 6),
            _at(_assistant(usage=_usage(2, 484), message_id="split-response"), 12),
            _at(_assistant(usage=_usage(1, 3)), 15),
        ],
    )

    stages, complete = analyze_orchestrator_steps(session, manifest)

    assert complete is True
    assert stages["1"]["output_tokens"] == 484
    assert stages["1"]["input_tokens"] == 2
    assert stages["3"]["output_tokens"] == 3


@pytest.mark.parametrize(
    "steps",
    [
        [
            {
                "event": "step",
                "step": 2,
                "timestamp": "2026-07-20T10:00:20+00:00",
            },
            {
                "event": "step",
                "step": 3,
                "timestamp": "2026-07-20T10:00:10+00:00",
            },
        ],
        [
            {
                "event": "step",
                "step": 1,
                "timestamp": "2026-07-20T09:59:59+00:00",
            }
        ],
        [
            {
                "event": "step",
                "step": 12,
                "timestamp": "2026-07-20T11:00:01+00:00",
            }
        ],
    ],
    ids=["regressing-timestamps", "before-start", "after-end"],
)
def test_invalid_manifest_step_timeline_keeps_usage_unattributed(tmp_path, steps):
    sessions = tmp_path / "sessions"
    session = sessions / "invalid-timeline.jsonl"
    run_dir = tmp_path / "run"
    manifest = _manifest("invalid-timeline", tmp_path, run_dir, started=[])
    manifest["steps"] = steps
    _write_jsonl(session, [_at(_assistant(usage=_usage(2, 3)), 30)])

    result = enrich_run_transcript(manifest, sessions, set())

    assert result["completeness"]["orchestrator_data"] is False
    assert result["warnings"] == [{"code": "orchestrator_stage_timeline_invalid"}]
    assert result["orchestrator_usage_by_step"] == {
        "unattributed": _usage(2, 3) | {"effective_input_tokens": 2}
    }


class TestEnrichRunTranscript:
    def test_missing_session_identity_and_file_have_fixed_unavailable_shapes(self, tmp_path):
        missing_identity = enrich_run_transcript(
            _manifest(None, tmp_path, tmp_path / "run"),
            tmp_path,
            {"security-reviewer"},
        )
        assert missing_identity == {
            "available": False,
            "reason": "missing_session_id",
            "warnings": [],
            "orchestrator_usage_by_step": None,
            "agent_usage": None,
            "usage": None,
            "tool_failures": None,
            "artifact_writes": None,
            "observed_reads": None,
        }

        missing_file = enrich_run_transcript(
            _manifest("absent", tmp_path, tmp_path / "run"),
            tmp_path,
            {"security-reviewer"},
        )
        assert missing_file["available"] is False
        assert missing_file["reason"] == "session_not_found_or_ambiguous"
        for key in (
            "orchestrator_usage_by_step",
            "agent_usage",
            "usage",
            "tool_failures",
            "artifact_writes",
            "observed_reads",
        ):
            assert missing_file[key] is None

    @pytest.mark.parametrize(
        "started_at,ended_at",
        [
            (None, None),
            ("2026-07-20T10:00:00", "2026-07-20T11:00:00+00:00"),
            ("not-a-time", "2026-07-20T11:00:00+00:00"),
            ("2026-07-20T11:00:00+00:00", "2026-07-20T10:00:00+00:00"),
        ],
        ids=["missing", "naive", "malformed", "reversed"],
    )
    def test_invalid_manifest_run_windows_are_unavailable(
        self, tmp_path, started_at, ended_at
    ):
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        _write_jsonl(sessions / "invalid-window.jsonl", [_assistant(usage=_usage(1, 2))])
        manifest = _manifest("invalid-window", tmp_path, output_dir, started=[])
        if started_at is None:
            manifest["run"].pop("started_at")
        else:
            manifest["run"]["started_at"] = started_at
        manifest["run"]["ended_at"] = ended_at

        result = enrich_run_transcript(manifest, sessions, set())

        assert result == {
            "available": False,
            "reason": "invalid_run_window",
            "warnings": [],
            "orchestrator_usage_by_step": None,
            "agent_usage": None,
            "usage": None,
            "tool_failures": None,
            "artifact_writes": None,
            "observed_reads": None,
        }

    def test_run_window_is_inclusive_and_running_window_is_open_ended(self, tmp_path):
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        entries = [
            # Pre-run foreign work sits before the run's triggering prompt —
            # the window opens at that prompt.
            _at(_assistant(usage=_usage(100, 100)), -2),
            _at(
                {"type": "user", "message": {"role": "user", "content": "go"}},
                -1,
            ),
            _at(_assistant(usage=_usage(1, 2)), 0),
            _at(_assistant(usage=_usage(2, 3)), 60),
            # Post-run foreign work follows a human prompt — the completed
            # window closes there.
            _at(
                {"type": "user", "message": {"role": "user", "content": "next"}},
                61,
            ),
            _at(_assistant(usage=_usage(100, 100)), 62),
        ]
        _write_jsonl(sessions / "inclusive.jsonl", entries)
        manifest = _manifest("inclusive", tmp_path, output_dir, started=[])
        manifest["run"]["ended_at"] = (
            _TEST_TRANSCRIPT_START + timedelta(seconds=60)
        ).isoformat()

        bounded = enrich_run_transcript(manifest, sessions, set())

        assert bounded["usage"]["output_tokens"] == 5

        manifest["run"]["ended_at"] = None
        running = enrich_run_transcript(manifest, sessions, set())

        assert running["usage"]["output_tokens"] == 105

    def test_invalid_utf8_line_costs_one_line_not_the_enrichment(
        self, tmp_path
    ):
        """A damaged byte in the main session reports parse_gap for that
        line while the rest of the run's evidence still measures."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        good = [
            _at(_assistant(usage=_usage(1, 2)), 0),
            _at(_assistant(usage=_usage(2, 3)), 10),
        ]
        payload = b"\n".join(
            json.dumps(entry).encode("utf-8") for entry in good
        )
        session = sessions / "damaged.jsonl"
        session.parent.mkdir(parents=True, exist_ok=True)
        session.write_bytes(payload + b'\n{"type": "assistant", "x": "\xff"}\n')
        manifest = _manifest("damaged", tmp_path, output_dir, started=[])

        result = enrich_run_transcript(manifest, sessions, set())

        assert result["available"] is True
        assert result["usage"]["output_tokens"] == 5
        assert {"code": "orchestrator_transcript_parse_gap"} in result["warnings"]

    def test_aware_timestamps_accept_z_suffix(self):
        """Claude Code writes Z-suffixed timestamps; Python 3.10's
        fromisoformat() rejects them unless normalized like the metrics
        contract parser — without this every record becomes a time gap."""
        parsed = _mod._aware_timestamp("2026-07-23T06:28:20.661Z")
        assert parsed == datetime(
            2026, 7, 23, 6, 28, 20, 661000, tzinfo=timezone.utc
        )

    @pytest.mark.parametrize(
        "notification_content",
        [
            "<task-notification><usage>x</usage></task-notification>",
            [
                {
                    "type": "text",
                    "text": (
                        "  <task-notification>agent done"
                        "</task-notification>"
                    ),
                }
            ],
        ],
        ids=["string-content", "text-block-content"],
    )
    def test_task_notification_does_not_close_the_run_window(
        self, tmp_path, notification_content
    ):
        """A background-agent completion arriving between ended_at and the
        final response is harness-injected, not a human turn — the window
        must run through the presentation and close at the real prompt."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        entries = [
            _at(_assistant(usage=_usage(1, 2)), 50),
            # ended_at (+60) lands here; the notification and the final
            # presentation follow it.
            _at(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": notification_content,
                    },
                },
                62,
            ),
            _at(_assistant(usage=_usage(3, 400)), 64),
            _at(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "new task"},
                },
                70,
            ),
            _at(_assistant(usage=_usage(100, 100)), 71),
        ]
        _write_jsonl(sessions / "notified.jsonl", entries)
        manifest = _manifest("notified", tmp_path, output_dir, started=[])
        manifest["run"]["ended_at"] = (
            _TEST_TRANSCRIPT_START + timedelta(seconds=60)
        ).isoformat()

        result = enrich_run_transcript(manifest, sessions, set())

        assert result["usage"]["output_tokens"] == 2 + 400

    def test_superseded_turn_time_gap_does_not_degrade_the_run(
        self, tmp_path
    ):
        """A timestamp-less record in an older turn is discarded with that
        turn when a later prompt supersedes it — the bounded entries hold
        only current-run data, so availability must not go partial."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        entries = [
            # Older turn with a damaged (unparseable-timestamp) record.
            _at(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "earlier work"},
                },
                -10,
            ),
            {
                "type": "assistant",
                "message": {"role": "assistant"},
                "timestamp": "not-a-time",
            },
            # The run's triggering prompt supersedes that turn entirely.
            _at(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "review this"},
                },
                -1,
            ),
            _at(_assistant(usage=_usage(1, 2)), 0),
        ]
        _write_jsonl(sessions / "superseded.jsonl", entries)
        manifest = _manifest("superseded", tmp_path, output_dir, started=[])

        result = enrich_run_transcript(manifest, sessions, set())

        assert result["usage"]["output_tokens"] == 2
        assert result["warnings"] == []
        assert result["completeness"]["usage"] is True

    def test_pending_turn_time_gap_still_degrades_when_turn_survives(
        self, tmp_path
    ):
        """A gap inside the run's own triggering turn is run evidence."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        entries = [
            _at(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "review this"},
                },
                -5,
            ),
            {
                "type": "assistant",
                "message": {"role": "assistant"},
                "timestamp": "not-a-time",
            },
            _at(_assistant(usage=_usage(1, 2)), 0),
        ]
        _write_jsonl(sessions / "gap-in-trigger.jsonl", entries)
        manifest = _manifest("gap-in-trigger", tmp_path, output_dir, started=[])

        result = enrich_run_transcript(manifest, sessions, set())

        assert {"code": "orchestrator_transcript_time_gap"} in result["warnings"]

    def test_run_window_includes_the_opening_turn_before_started_at(
        self, tmp_path
    ):
        """telemetry.start() runs inside the Step 1 subprocess, so the
        assistant entry that invoked it — the opening turn with its usage —
        is timestamped just before started_at and must still be counted,
        attributed to step 1."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        entries = [
            _at(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "review this PR"},
                },
                -5,
            ),
            # The step-1 invocation entry: 139ms-class gap before started_at,
            # carrying the opening turn's heavy cache-read usage.
            _at(
                _assistant(
                    _call(
                        "step1", "Bash", command="python3 pipeline.py --step 1"
                    ),
                    usage=_usage(2, 7, read=73_944),
                ),
                -1,
            ),
            _at(_result("step1", structured={"exitCode": 0}), 1),
            _at(_assistant(usage=_usage(3, 5)), 10),
        ]
        _write_jsonl(sessions / "opening.jsonl", entries)
        manifest = _manifest("opening", tmp_path, output_dir, started=[])

        result = enrich_run_transcript(manifest, sessions, set())

        assert result["usage"]["output_tokens"] == 12
        assert result["usage"]["cache_read_input_tokens"] == 73_944
        by_step = result["orchestrator_usage_by_step"]
        assert by_step["1"]["cache_read_input_tokens"] == 73_944
        assert by_step["unattributed"]["output_tokens"] == 0

    def test_completed_run_window_includes_final_presentation_turn(
        self, tmp_path
    ):
        """telemetry.finalize() records ended_at inside the final step's
        subprocess, before the orchestrator reads the report and writes its
        summary — the window must stay open through that in-flight turn and
        close at the next human prompt."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        entries = [
            _at(
                _assistant(
                    _call(
                        "step11",
                        "Bash",
                        command="python3 pipeline.py --step 11",
                    ),
                    usage=_usage(1, 2),
                ),
                50,
            ),
            # ended_at (+60) lands here, inside the step-11 subprocess.
            _at(_result("step11", structured={"exitCode": 0}), 61),
            _at(
                _assistant(
                    _call(
                        "report",
                        "Read",
                        file_path=str(output_dir / "review-report.md"),
                    ),
                    usage=_usage(2, 3),
                ),
                62,
            ),
            _at(_result("report"), 63),
            _at(_assistant(usage=_usage(3, 400)), 64),
            # Next human prompt — later work is not this run's.
            _at(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "new task"},
                },
                70,
            ),
            _at(_assistant(usage=_usage(100, 100)), 71),
        ]
        _write_jsonl(sessions / "presentation.jsonl", entries)
        manifest = _manifest("presentation", tmp_path, output_dir, started=[])
        manifest["run"]["ended_at"] = (
            _TEST_TRANSCRIPT_START + timedelta(seconds=60)
        ).isoformat()

        result = enrich_run_transcript(manifest, sessions, set())

        assert result["usage"]["output_tokens"] == 2 + 3 + 400

    def test_same_session_is_bounded_before_dispatch_usage_and_failure_analysis(
        self, tmp_path
    ):
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        current_prompt = (
            "Review the assigned scope.\n"
            f"{_agent_prompt(output_dir)}\n"
            "Save the generated artifact."
        )
        _write_jsonl(
            sessions / "bounded.jsonl",
            [
                _at(
                    _assistant(
                        _call("old", "Agent", prompt=_agent_prompt(output_dir)),
                        usage=_usage(100, 100),
                    ),
                    -20,
                ),
                _at(_result("old", structured={"agentId": "old-agent"}), -19),
                # The current run's triggering prompt — prior-session work
                # above stays outside the window.
                _at(
                    {
                        "type": "user",
                        "message": {"role": "user", "content": "review this"},
                    },
                    -1,
                ),
                _at(
                    _assistant(
                        _call("current", "Agent", prompt=current_prompt),
                        usage=_usage(1, 2),
                    ),
                    10,
                ),
                _at(
                    _result("current", structured={"agentId": "current-agent"}),
                    11,
                ),
                _at(_assistant(usage=_usage(2, 3)), 12),
                # Later unrelated work follows a human prompt — the completed
                # window closes there.
                _at(
                    {
                        "type": "user",
                        "message": {"role": "user", "content": "unrelated task"},
                    },
                    3650,
                ),
                _at(
                    _assistant(
                        _call("later", "Bash", command="false"),
                        usage=_usage(100, 100),
                    ),
                    3700,
                ),
                _at(
                    _result(
                        "later",
                        "API Error: later unrelated failure",
                        is_error=True,
                        structured={"exitCode": 1},
                    ),
                    3701,
                ),
            ],
        )
        _write_jsonl(
            sessions / "bounded" / "subagents" / "agent-old-agent.jsonl",
            [_assistant(usage=_usage(50, 50))],
        )
        _write_jsonl(
            sessions / "bounded" / "subagents" / "agent-current-agent.jsonl",
            [_assistant(usage=_usage(4, 5))],
        )
        manifest = _manifest(
            "bounded", tmp_path, output_dir, started=["security-reviewer"]
        )

        result = enrich_run_transcript(
            manifest, sessions, {"security-reviewer"}
        )

        assert result["correlation"]["expected_count"] == 1
        assert result["correlation"]["correlated_count"] == 1
        assert result["correlation"]["missing_count"] == 0
        assert result["agent_usage"][0]["agent_id"] == "current-agent"
        assert result["usage"]["output_tokens"] == 10
        assert result["tool_failures"] == []
        assert result["orchestrator_usage_by_step"]["1"]["output_tokens"] == 5

    def test_timestamp_less_main_record_is_excluded_and_marks_data_partial(
        self, tmp_path
    ):
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        session = sessions / "timestamp-gap.jsonl"
        session.parent.mkdir(parents=True)
        session.write_text(
            json.dumps(_assistant(usage=_usage(100, 100)))
            + "\n"
            + json.dumps(_at(_assistant(usage=_usage(1, 2)), 10))
            + "\n"
        )

        result = enrich_run_transcript(
            _manifest("timestamp-gap", tmp_path, output_dir, started=[]),
            sessions,
            set(),
        )

        assert result["usage"]["output_tokens"] == 2
        assert result["completeness"]["orchestrator_data"] is False
        assert result["warnings"] == [{"code": "orchestrator_transcript_time_gap"}]

    def test_timestamp_less_session_metadata_does_not_create_a_time_gap(
        self, tmp_path
    ):
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        session = sessions / "metadata.jsonl"
        session.parent.mkdir(parents=True)
        records = [
            {"type": record_type, "metadata": "not run evidence"}
            for record_type in (
                "last-prompt",
                "mode",
                "permission-mode",
                "file-history-snapshot",
                "ai-title",
            )
        ]
        records.extend(
            [
                _at(_assistant(usage=_usage(1, 2)), 10),
                _at({"type": "user", "message": {"content": []}}, 11),
            ]
        )
        session.write_text("\n".join(json.dumps(record) for record in records) + "\n")

        result = enrich_run_transcript(
            _manifest("metadata", tmp_path, output_dir, started=[]),
            sessions,
            set(),
        )

        assert result["usage"]["output_tokens"] == 2
        assert result["completeness"]["orchestrator_data"] is True
        assert result["warnings"] == []

    def test_missing_correlated_subagent_is_partial_not_silent_zero(self, tmp_path):
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        main = sessions / "session-1.jsonl"
        _write_jsonl(
            main,
            [
                _assistant(
                    _call("a1", "Agent", prompt=_agent_prompt(output_dir)),
                    usage=_usage(1, 2),
                ),
                _result("a1", structured={"agentId": "missing-agent"}),
            ],
        )

        result = enrich_run_transcript(
            _manifest("session-1", tmp_path, output_dir),
            sessions,
            {"security-reviewer"},
        )
        assert result["available"] is True
        assert result["reason"] is None
        assert result["warnings"] == [
            {"code": "agent_transcript_missing", "agent": "security-reviewer"}
        ]
        assert result["agent_usage"] == [
            {
                "agent": "security-reviewer",
                "agent_id": "missing-agent",
                "model": None,
                "available": False,
                "usage": None,
                "usage_by_model": None,
                "tool_calls": None,
            }
        ]
        assert result["usage"]["output_tokens"] == 2
        assert result["correlation"] == {
            "expected_available": True,
            "expected": ["security-reviewer"],
            "expected_by_agent": {"security-reviewer": 1},
            "correlated": ["security-reviewer"],
            "correlated_by_agent": {"security-reviewer": 1},
            "missing": [],
            "missing_by_agent": {},
            "missing_transcripts": ["security-reviewer"],
            "expected_count": 1,
            "correlated_count": 1,
            "missing_count": 0,
            "complete": False,
        }
        assert result["agent_data_complete"] is False
        assert result["usage_complete"] is False
        assert result["completeness"] == {
            "orchestrator_data": True,
            "agent_data": False,
            "usage": False,
            "tool_failures": False,
            "artifact_writes": False,
            "scope_comparable_reads": False,
            "non_scope_comparable_reads": True,
            "observed_reads": False,
        }
        assert result["artifact_writes"]["available"] is False
        assert result["artifact_writes"]["complete"] is False
        assert result["artifact_writes"]["builder_attempted"] is None
        assert result["observed_reads"]["transcript_data_complete"] is False

    def test_orchestrator_reads_do_not_enter_reviewer_observed_reads(self, tmp_path):
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        repo = tmp_path / "repo"
        repo.mkdir()
        orchestrator_path = repo / "src/orchestrator.py"
        reviewer_path = repo / "src/in.py"
        _write_jsonl(
            sessions / "read-isolation.jsonl",
            [
                _assistant(
                    _call("main-read", "Read", file_path=str(orchestrator_path)),
                    usage=_usage(1, 2),
                ),
                _result("main-read"),
                _assistant(_call("agent", "Agent", prompt=_agent_prompt(output_dir))),
                _result("agent", structured={"agentId": "reviewer-read"}),
            ],
        )
        _write_jsonl(
            sessions
            / "read-isolation"
            / "subagents"
            / "agent-reviewer-read.jsonl",
            [
                _assistant(_call("read", "Read", file_path=str(reviewer_path))),
                _result("read"),
            ],
        )

        result = enrich_run_transcript(
            _manifest("read-isolation", repo, output_dir),
            sessions,
            {"security-reviewer"},
        )

        assert result["observed_reads"]["all"] == ["src/in.py"]
        assert result["observed_reads"]["in_scope"] == ["src/in.py"]
        assert result["observed_reads"]["out_of_scope"] == []
        assert result["usage"]["output_tokens"] == 2
        assert result["orchestrator_usage_by_step"]["1"]["output_tokens"] == 2

    def test_only_orchestrator_reads_produce_empty_agent_read_observation(self, tmp_path):
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        repo = tmp_path / "repo"
        repo.mkdir()
        _write_jsonl(
            sessions / "only-main-read.jsonl",
            [
                _assistant(
                    _call("main-read", "Read", file_path=str(repo / "src/main.py"))
                ),
                _result("main-read"),
            ],
        )

        result = enrich_run_transcript(
            _manifest("only-main-read", repo, output_dir, started=[]),
            sessions,
            {"security-reviewer"},
        )

        assert result["observed_reads"]["all"] == []
        assert result["observed_reads"]["in_scope"] == []
        assert result["observed_reads"]["out_of_scope"] == []
        assert result["observed_reads"]["transcript_data_complete"] is True

    @pytest.mark.parametrize(
        "incomplete_family,incomplete_mode",
        [
            pytest.param("reviewer", "uncorrelated", id="reviewer-uncorrelated"),
            pytest.param("reviewer", "missing", id="reviewer-missing-transcript"),
            pytest.param("reviewer", "parse-gap", id="reviewer-parse-gap"),
            pytest.param("synthesis", "uncorrelated", id="synthesis-uncorrelated"),
            pytest.param("synthesis", "missing", id="synthesis-missing-transcript"),
            pytest.param("synthesis", "parse-gap", id="synthesis-parse-gap"),
        ],
    )
    def test_observed_read_completeness_isolated_by_actor_family(
        self, tmp_path, incomplete_family, incomplete_mode
    ):
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        repo = tmp_path / "repo"
        repo.mkdir()
        session_id = f"{incomplete_family}-{incomplete_mode}"
        dispatches = {
            "reviewer": (
                _call("reviewer", "Agent", prompt=_agent_prompt(output_dir)),
                "reviewer-id",
                "src/reviewer.py",
            ),
            "synthesis": (
                _special_agent_call("synthesis", output_dir, "critic"),
                "synthesis-id",
                "src/synthesis.py",
            ),
        }
        main_entries = []
        for family, (call, agent_id, _relative_path) in dispatches.items():
            main_entries.append(_assistant(call))
            if family != incomplete_family or incomplete_mode != "uncorrelated":
                main_entries.append(
                    _result(call["id"], structured={"agentId": agent_id})
                )
        _write_jsonl(sessions / f"{session_id}.jsonl", main_entries)

        for family, (_call_value, agent_id, relative_path) in dispatches.items():
            if family == incomplete_family and incomplete_mode in {
                "uncorrelated",
                "missing",
            }:
                continue
            transcript = _write_jsonl(
                sessions
                / session_id
                / "subagents"
                / f"agent-{agent_id}.jsonl",
                [
                    _assistant(
                        _call(
                            "read",
                            "Read",
                            file_path=str(repo / relative_path),
                        )
                    ),
                    _result("read"),
                ],
            )
            if family == incomplete_family and incomplete_mode == "parse-gap":
                with transcript.open("a") as stream:
                    stream.write('{"type": "truncated"\n')

        result = enrich_run_transcript(
            _manifest(session_id, repo, output_dir, started=[]),
            sessions,
            {"security-reviewer", "critic"},
        )

        reviewer_complete = incomplete_family != "reviewer"
        synthesis_complete = incomplete_family != "synthesis"
        assert result["observed_reads"]["schema_version"] == 2
        assert result["completeness"]["scope_comparable_reads"] is reviewer_complete
        assert (
            result["completeness"]["non_scope_comparable_reads"]
            is synthesis_complete
        )
        assert result["completeness"]["observed_reads"] is False
        assert (
            result["observed_reads"][
                "scope_comparable_transcript_data_complete"
            ]
            is reviewer_complete
        )
        assert (
            result["observed_reads"][
                "non_scope_comparable_transcript_data_complete"
            ]
            is synthesis_complete
        )

    def test_reviewer_and_synthesis_reads_remain_after_orchestrator_isolation(
        self, tmp_path
    ):
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        repo = tmp_path / "repo"
        repo.mkdir()
        session_id = "review-and-synthesis-reads"
        dispatches = [
            (
                _call("reviewer", "Agent", prompt=_agent_prompt(output_dir)),
                "reviewer-read",
            ),
            (
                _special_agent_call(
                    "reconciliator", output_dir, "review-reconciliator"
                ),
                "reconciliator-read",
            ),
            (
                _special_agent_call(
                    "decision", output_dir, "decision-reviewer"
                ),
                "decision-read",
            ),
            (
                _special_agent_call("critic", output_dir, "critic"),
                "critic-read",
            ),
        ]
        main_entries = [
            _assistant(
                _call(
                    "main-read",
                    "Read",
                    file_path=str(repo / "src/orchestrator.py"),
                )
            ),
            _result("main-read"),
        ]
        for call, agent_id in dispatches:
            main_entries.extend(
                [
                    _assistant(call),
                    _result(call["id"], structured={"agentId": agent_id}),
                ]
            )
        _write_jsonl(sessions / f"{session_id}.jsonl", main_entries)

        for agent_id, relative_paths in (
            (
                "reviewer-read",
                ("src/in.py", "src/reviewer-out.py", "src/shared.py"),
            ),
            ("reconciliator-read", "src/reconcile.py"),
            ("decision-read", "src/decision.py"),
            ("critic-read", ("src/critic.py", "src/shared.py")),
        ):
            if isinstance(relative_paths, str):
                relative_paths = (relative_paths,)
            entries = []
            for index, relative_path in enumerate(relative_paths):
                entries.extend(
                    [
                        _assistant(
                            _call(
                                f"read-{index}",
                                "Read",
                                file_path=str(repo / relative_path),
                            )
                        ),
                        _result(f"read-{index}"),
                    ]
                )
            _write_jsonl(
                sessions
                / session_id
                / "subagents"
                / f"agent-{agent_id}.jsonl",
                entries,
            )

        result = enrich_run_transcript(
            _manifest(session_id, repo, output_dir),
            sessions,
            {
                "security-reviewer",
                "review-reconciliator",
                "decision-reviewer",
                "critic",
            },
        )

        assert result["correlation"]["complete"] is True
        assert result["observed_reads"]["all"] == [
            "src/in.py",
            "src/reviewer-out.py",
            "src/shared.py",
        ]
        assert result["observed_reads"]["in_scope"] == ["src/in.py"]
        assert result["observed_reads"]["out_of_scope"] == [
            "src/reviewer-out.py",
            "src/shared.py",
        ]
        assert result["observed_reads"]["non_scope_comparable"] == [
            "src/critic.py",
            "src/decision.py",
            "src/reconcile.py",
            "src/shared.py",
        ]
        assert "src/orchestrator.py" not in (
            result["observed_reads"]["all"]
            + result["observed_reads"]["non_scope_comparable"]
        )

    def test_retry_and_partial_synthesis_reads_remain_private_and_separate(
        self, tmp_path
    ):
        secret = "PRIVATE_SYNTHESIS_SENTINEL"
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        repo = tmp_path / "repo"
        repo.mkdir()
        session_id = "partial-synthesis-retry"
        _write_jsonl(
            sessions / f"{session_id}.jsonl",
            [
                _assistant(
                    _special_agent_call("first", output_dir, "critic"),
                    _special_agent_call("second", output_dir, "critic"),
                ),
                _result("first", structured={"agentId": "critic-first"}),
                _result("second", structured={"agentId": "critic-second"}),
            ],
        )
        for agent_id in ("critic-first", "critic-second"):
            transcript = _write_jsonl(
                sessions
                / session_id
                / "subagents"
                / f"agent-{agent_id}.jsonl",
                [
                    {"type": "user", "message": {"content": secret}},
                    _assistant(
                        _call(
                            "read-safe",
                            "Read",
                            file_path=str(repo / "src/critic.py"),
                        ),
                        _call(
                            "read-private",
                            "Read",
                            file_path=str(tmp_path / secret),
                        ),
                    ),
                    _result("read-safe", secret),
                    _result("read-private", secret),
                ],
            )
            if agent_id == "critic-second":
                with transcript.open("a") as stream:
                    stream.write('{"type": "truncated"\n')

        result = enrich_run_transcript(
            _manifest(session_id, repo, output_dir, started=[]),
            sessions,
            {"critic"},
        )

        assert result["correlation"]["expected_by_agent"] == {"critic": 2}
        assert result["correlation"]["correlated_by_agent"] == {"critic": 2}
        assert result["correlation"]["complete"] is False
        assert result["observed_reads"] == {
            "schema_version": 2,
            "all": [],
            "in_scope": [],
            "out_of_scope": [],
            "non_scope_comparable": ["src/critic.py"],
            "exhaustive": False,
            "scope_comparable_transcript_data_complete": True,
            "non_scope_comparable_transcript_data_complete": False,
            "transcript_data_complete": False,
        }
        assert secret not in " ".join(_flatten_strings(result))

    @pytest.mark.parametrize(
        "agent",
        ["security-critic", "critic-v2", "review-reconciliator-v2"],
    )
    def test_special_like_exact_identity_remains_a_regular_reviewer(
        self, tmp_path, agent
    ):
        assert _mod._is_special_agent(agent) is False

        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        repo = tmp_path / "repo"
        repo.mkdir()
        session_id = f"regular-{agent}"
        _write_jsonl(
            sessions / f"{session_id}.jsonl",
            [
                _assistant(
                    _call(
                        "reviewer",
                        "Agent",
                        prompt=_agent_prompt(output_dir, agent),
                    )
                ),
                _result("reviewer", structured={"agentId": "regular-id"}),
            ],
        )
        _write_jsonl(
            sessions
            / session_id
            / "subagents"
            / "agent-regular-id.jsonl",
            [
                _assistant(
                    _call(
                        "read",
                        "Read",
                        file_path=str(repo / "src/in.py"),
                    )
                ),
                _result("read"),
            ],
        )
        manifest = _manifest(
            session_id, repo, output_dir, started=[agent]
        )
        manifest["coverage"]["by_agent"] = {agent: ["src/in.py"]}

        result = enrich_run_transcript(
            manifest,
            sessions,
            {agent},
        )

        assert result["correlation"]["correlated"] == [agent]
        assert result["observed_reads"]["all"] == ["src/in.py"]
        assert result["observed_reads"]["in_scope"] == ["src/in.py"]
        assert result["observed_reads"]["non_scope_comparable"] == []

    def test_started_agent_without_result_is_explicitly_uncorrelated(self, tmp_path):
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        _write_jsonl(
            sessions / "session-no-result.jsonl",
            [_assistant(usage=_usage(1, 2))],
        )

        result = enrich_run_transcript(
            _manifest("session-no-result", tmp_path, output_dir),
            sessions,
            {"security-reviewer"},
        )

        assert result["correlation"] == {
            "expected_available": True,
            "expected": ["security-reviewer"],
            "expected_by_agent": {"security-reviewer": 1},
            "correlated": [],
            "correlated_by_agent": {},
            "missing": ["security-reviewer"],
            "missing_by_agent": {"security-reviewer": 1},
            "missing_transcripts": [],
            "expected_count": 1,
            "correlated_count": 0,
            "missing_count": 1,
            "complete": False,
        }
        assert result["warnings"] == [
            {"code": "expected_agent_uncorrelated", "agent": "security-reviewer"}
        ]
        assert result["agent_data_complete"] is False
        assert result["usage_complete"] is False
        assert result["artifact_writes"]["builder_attempted"] is None
        assert result["artifact_writes"]["complete"] is False

    def test_unrecognized_expected_identity_fails_closed_without_echoing_value(
        self, tmp_path
    ):
        secret = "PRIVATE_SECRET_SENTINEL"
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        _write_jsonl(sessions / "session-invalid.jsonl", [_assistant()])

        result = enrich_run_transcript(
            _manifest(
                "session-invalid",
                tmp_path,
                output_dir,
                started=[secret],
            ),
            sessions,
            {"security-reviewer"},
        )

        assert result["warnings"] == [
            {"code": "expected_agent_identity_invalid"}
        ]
        assert result["correlation"]["expected"] == []
        assert result["correlation"]["complete"] is False
        assert secret not in " ".join(_flatten_strings(result))

    def test_each_manifest_retry_requires_a_correlated_dispatch(self, tmp_path):
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        _write_jsonl(
            sessions / "session-retry.jsonl",
            [
                _assistant(_call("a1", "Agent", prompt=_agent_prompt(output_dir))),
                _result("a1", structured={"agentId": "retry-agent"}),
            ],
        )
        _write_jsonl(
            sessions / "session-retry" / "subagents" / "agent-retry-agent.jsonl",
            [_assistant(usage=_usage(1, 2))],
        )
        manifest = _manifest(
            "session-retry",
            tmp_path,
            output_dir,
            started=["security-reviewer", "security-reviewer"],
        )

        result = enrich_run_transcript(
            manifest,
            sessions,
            {"security-reviewer"},
        )

        assert result["correlation"]["expected"] == ["security-reviewer"]
        assert result["correlation"]["expected_by_agent"] == {
            "security-reviewer": 2
        }
        assert result["correlation"]["correlated_by_agent"] == {
            "security-reviewer": 1
        }
        assert result["correlation"]["missing_by_agent"] == {
            "security-reviewer": 1
        }
        assert result["correlation"]["expected_count"] == 2
        assert result["correlation"]["correlated_count"] == 1
        assert result["correlation"]["missing_count"] == 1
        assert result["correlation"]["complete"] is False
        assert result["agent_data_complete"] is False
        assert result["usage_complete"] is False
        assert result["artifact_writes"]["builder_attempted"] is None
        assert result["artifact_writes"]["complete"] is False

    def test_observed_agent_metrics_are_retained_but_marked_partial(self, tmp_path):
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        repo = tmp_path / "repo"
        repo.mkdir()
        _write_jsonl(
            sessions / "session-partial.jsonl",
            [
                _assistant(_call("a1", "Agent", prompt=_agent_prompt(output_dir))),
                _result("a1", structured={"agentId": "observed"}),
            ],
        )
        _write_jsonl(
            sessions / "session-partial" / "subagents" / "agent-observed.jsonl",
            [
                _assistant(
                    _call(
                        "builder",
                        "Write",
                        file_path="/private/tmp/builder.py",
                        content=(
                            "builder = ReviewOutputBuilder('safe')\n"
                            "builder.save('/safe')"
                        ),
                    ),
                    usage=_usage(1, 2),
                ),
                _result("builder"),
                _assistant(
                    _call("read", "Read", file_path=str(repo / "src/in.py"))
                ),
                _result("read"),
            ],
        )
        manifest = _manifest(
            "session-partial",
            repo,
            output_dir,
            started=["security-reviewer", "tests-reviewer"],
        )

        result = enrich_run_transcript(
            manifest,
            sessions,
            {"security-reviewer", "tests-reviewer"},
        )

        assert result["correlation"]["missing"] == ["tests-reviewer"]
        assert result["artifact_writes"]["builder_attempted"] is None
        assert result["artifact_writes"]["available"] is True
        assert result["artifact_writes"]["complete"] is False
        assert result["artifact_writes"]["by_agent"][0][
            "builder_attempted"
        ] is False
        assert result["usage"]["output_tokens"] == 2
        assert result["usage_complete"] is False
        assert result["observed_reads"]["all"] == ["src/in.py"]
        assert result["observed_reads"]["transcript_data_complete"] is False
        assert result["completeness"]["tool_failures"] is False

    def test_malformed_correlated_subagent_line_emits_fixed_partial_warning(
        self, tmp_path, monkeypatch
    ):
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        main = sessions / "session-gap.jsonl"
        _write_jsonl(
            main,
            [
                _assistant(_call("a1", "Agent", prompt=_agent_prompt(output_dir))),
                _result("a1", structured={"agentId": "gap"}),
            ],
        )
        subagent = sessions / "session-gap" / "subagents" / "agent-gap.jsonl"
        _write_jsonl(subagent, [_assistant(usage=_usage(1, 1))])
        with subagent.open("a") as stream:
            stream.write('{"type": "truncated"\n')
        original_open = Path.open
        subagent_opens = 0
        resolved_subagent = subagent.resolve(strict=False)

        def counted_open(path, *args, **kwargs):
            nonlocal subagent_opens
            if path.resolve(strict=False) == resolved_subagent:
                subagent_opens += 1
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", counted_open)

        result = enrich_run_transcript(
            _manifest("session-gap", tmp_path, output_dir),
            sessions,
            {"security-reviewer"},
        )

        assert result["available"] is True
        assert result["warnings"] == [
            {"code": "agent_transcript_parse_gap", "agent": "security-reviewer"}
        ]
        assert result["agent_usage"][0]["usage"]["output_tokens"] == 1
        assert result["correlation"]["complete"] is False
        assert result["agent_data_complete"] is False
        assert result["usage_complete"] is False
        assert subagent_opens == 1

    def test_correlated_reviewer_output_without_bash_envelope_reports_no_attempt(
        self, tmp_path
    ):
        secret = "PRIVATE_SECRET_SENTINEL"
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        repo = tmp_path / "repo"
        repo.mkdir()
        main = sessions / "session-2.jsonl"
        _write_jsonl(
            main,
            [
                _assistant(
                    _call(
                        "a1",
                        "Agent",
                        prompt=_agent_prompt(output_dir).replace(
                            "base..head", f"base..head {secret}"
                        ),
                    ),
                    usage=_usage(2, 3, create=5, read=7),
                ),
                _result(
                    "a1",
                    f"completed {secret}",
                    structured={
                        "agentId": "agent-22",
                        "resolvedModel": "claude-opus-4-1",
                        "usage": _usage(1000, 1000, 1000, 1000),
                        "prompt": secret,
                        "content": secret,
                    },
                ),
            ],
        )
        subagent = sessions / "session-2" / "subagents" / "agent-22.jsonl"
        _write_jsonl(
            subagent,
            [
                {"type": "user", "message": {"content": secret}},
                _assistant(
                    _call(
                        "read",
                        "Read",
                        file_path=str(repo / "src/in.py"),
                    ),
                    usage=_usage(11, 13, create=17, read=19),
                    model="claude-opus-4-1",
                ),
                _result("read", secret),
                _assistant(
                    _call(
                        "write",
                        "Write",
                        file_path=str(output_dir / "security-reviewer.json"),
                        content=f'{{"review": "{secret}"}}',
                    )
                ),
                _result("write", "created", is_error=False),
            ],
        )

        result = enrich_run_transcript(
            _manifest("session-2", repo, output_dir),
            sessions,
            {"security-reviewer"},
        )
        assert result["available"] is True
        assert result["warnings"] == []
        assert result["correlation"] == {
            "expected_available": True,
            "expected": ["security-reviewer"],
            "expected_by_agent": {"security-reviewer": 1},
            "correlated": ["security-reviewer"],
            "correlated_by_agent": {"security-reviewer": 1},
            "missing": [],
            "missing_by_agent": {},
            "missing_transcripts": [],
            "expected_count": 1,
            "correlated_count": 1,
            "missing_count": 0,
            "complete": True,
        }
        assert result["agent_data_complete"] is True
        assert result["usage_complete"] is True
        assert all(result["completeness"].values())
        assert result["usage"] == {
            "input_tokens": 13,
            "cache_creation_input_tokens": 22,
            "cache_read_input_tokens": 26,
            "effective_input_tokens": 61,
            "output_tokens": 16,
        }
        assert result["agent_usage"][0]["usage"]["output_tokens"] == 13
        assert result["observed_reads"]["all"] == ["src/in.py"]
        assert result["observed_reads"]["transcript_data_complete"] is True
        assert result["artifact_writes"]["builder_attempted"] is False
        assert result["artifact_writes"]["complete"] is True
        assert result["artifact_writes"]["by_agent"] == [
            {
                "agent": "security-reviewer",
                "builder_attempted": False,
                "builder_attempts": 0,
                "builder_successes": 0,
                "builder_failures": 0,
                "first_builder_attempt_succeeded": None,
                "recovered": False,
            }
        ]
        assert secret not in " ".join(_flatten_strings(result))
        keys = _flatten_keys(result)
        assert all(
            forbidden not in keys
            for forbidden in ("prompt", "content", "command", "tool_result", "source")
        )


@pytest.mark.parametrize(
    "agent",
    ["review-reconciliator", "decision-reviewer"],
)
def test_synthesis_call_without_result_is_an_expected_missing_dispatch(
    tmp_path, agent
):
    sessions = tmp_path / "sessions"
    output_dir = tmp_path / "run"
    _write_jsonl(
        sessions / "synthesis-missing.jsonl",
        [_assistant(_special_agent_call("synthesis", output_dir, agent))],
    )

    result = enrich_run_transcript(
        _manifest("synthesis-missing", tmp_path, output_dir, started=[]),
        sessions,
        {"review-reconciliator", "decision-reviewer"},
    )

    assert result["correlation"]["expected"] == [agent]
    assert result["correlation"]["expected_by_agent"] == {agent: 1}
    assert result["correlation"]["correlated"] == []
    assert result["correlation"]["correlated_by_agent"] == {}
    assert result["correlation"]["missing"] == [agent]
    assert result["correlation"]["missing_by_agent"] == {agent: 1}
    assert result["correlation"]["expected_count"] == 1
    assert result["correlation"]["correlated_count"] == 0
    assert result["correlation"]["missing_count"] == 1
    assert result["warnings"] == [
        {"code": "expected_agent_uncorrelated", "agent": agent}
    ]
    assert result["correlation"]["complete"] is False
    assert result["agent_data_complete"] is False
    assert result["usage_complete"] is False
    assert result["completeness"]["agent_data"] is False
    assert result["completeness"]["usage"] is False


@pytest.mark.parametrize(
    "agent,id_mode",
    [
        ("review-reconciliator", "missing"),
        ("decision-reviewer", "non-string"),
    ],
    ids=["step8-missing-id", "step10-non-string-id"],
)
def test_malformed_synthesis_call_id_remains_expected_and_incomplete(
    tmp_path, agent, id_mode
):
    secret = "PRIVATE_MALFORMED_ID_SENTINEL"
    sessions = tmp_path / "sessions"
    output_dir = tmp_path / "run"
    call = _special_agent_call("valid-placeholder", output_dir, agent)
    if id_mode == "missing":
        call.pop("id")
    else:
        call["id"] = {secret: True}
    _write_jsonl(
        sessions / "malformed-synthesis.jsonl",
        [_assistant(call, usage=_usage(1, 2))],
    )

    result = enrich_run_transcript(
        _manifest("malformed-synthesis", tmp_path, output_dir, started=[]),
        sessions,
        {"review-reconciliator", "decision-reviewer"},
    )

    assert result["warnings"] == [
        {"code": "agent_dispatch_schema_gap", "agent": agent},
        {"code": "expected_agent_uncorrelated", "agent": agent},
    ]
    assert result["correlation"]["expected_by_agent"] == {agent: 1}
    assert result["correlation"]["correlated_by_agent"] == {}
    assert result["correlation"]["missing_by_agent"] == {agent: 1}
    assert result["correlation"]["expected_count"] == 1
    assert result["correlation"]["correlated_count"] == 0
    assert result["correlation"]["missing_count"] == 1
    assert result["correlation"]["complete"] is False
    assert result["usage_complete"] is False
    assert result["completeness"]["agent_data"] is False
    assert result["completeness"]["usage"] is False
    assert result["completeness"]["tool_failures"] is False
    # Builder compliance is regular-reviewer evidence only: a missing
    # SYNTHESIS transcript does not degrade it, and with no expected
    # regular reviewers it is complete-and-empty.
    assert result["completeness"]["artifact_writes"] is True
    assert result["completeness"]["observed_reads"] is False
    assert secret not in " ".join(_flatten_strings(result))


def test_malformed_unrelated_or_wrong_run_calls_do_not_affect_expectations(tmp_path):
    sessions = tmp_path / "sessions"
    output_dir = tmp_path / "run"
    wrong_output = tmp_path / "other"
    wrong_run = _special_agent_call(
        "placeholder", wrong_output, "review-reconciliator"
    )
    wrong_run.pop("id")
    unknown_identity = _special_agent_call(
        "placeholder", output_dir, "mystery-agent"
    )
    unknown_identity["id"] = 123
    unrelated = _call("placeholder", "Read", file_path="/safe/read.py")
    unrelated.pop("id")
    _write_jsonl(
        sessions / "malformed-unrelated.jsonl",
        [_assistant(wrong_run, unknown_identity, unrelated)],
    )

    result = enrich_run_transcript(
        _manifest("malformed-unrelated", tmp_path, output_dir, started=[]),
        sessions,
        {"review-reconciliator", "decision-reviewer"},
    )

    assert result["warnings"] == []
    assert result["correlation"]["expected"] == []
    assert result["correlation"]["expected_by_agent"] == {}
    assert result["correlation"]["expected_count"] == 0
    assert result["correlation"]["complete"] is True
    assert result["usage_complete"] is True


@pytest.mark.parametrize(
    "agent",
    ["review-reconciliator", "decision-reviewer"],
)
def test_resolved_synthesis_call_is_complete_and_counted(tmp_path, agent):
    sessions = tmp_path / "sessions"
    output_dir = tmp_path / "run"
    session_id = f"resolved-{agent}"
    agent_id = f"id-{agent}"
    _write_jsonl(
        sessions / f"{session_id}.jsonl",
        [
            _assistant(_special_agent_call("synthesis", output_dir, agent)),
            _result("synthesis", structured={"agentId": agent_id}),
        ],
    )
    _write_jsonl(
        sessions / session_id / "subagents" / f"agent-{agent_id}.jsonl",
        [_assistant(usage=_usage(2, 3))],
    )

    result = enrich_run_transcript(
        _manifest(session_id, tmp_path, output_dir, started=[]),
        sessions,
        {"review-reconciliator", "decision-reviewer"},
    )

    assert result["warnings"] == []
    assert result["correlation"]["expected"] == [agent]
    assert result["correlation"]["expected_by_agent"] == {agent: 1}
    assert result["correlation"]["correlated"] == [agent]
    assert result["correlation"]["correlated_by_agent"] == {agent: 1}
    assert result["correlation"]["missing"] == []
    assert result["correlation"]["missing_by_agent"] == {}
    assert result["correlation"]["expected_count"] == 1
    assert result["correlation"]["correlated_count"] == 1
    assert result["correlation"]["missing_count"] == 0
    assert result["correlation"]["complete"] is True
    assert result["agent_data_complete"] is True
    assert result["usage_complete"] is True
    assert result["agent_usage"][0]["usage"]["output_tokens"] == 3


@pytest.mark.parametrize(
    "result_shape,expected_count",
    [
        ("duplicate-results", 1),
        ("malformed-result", 1),
        ("earlier-result", 1),
        ("duplicate-call-id", 2),
        ("ambiguous-agent-id", 2),
    ],
)
def test_unpairable_synthesis_results_remain_expected_but_uncorrelated(
    tmp_path, result_shape, expected_count
):
    sessions = tmp_path / "sessions"
    output_dir = tmp_path / "run"
    call = _special_agent_call("synthesis", output_dir, "review-reconciliator")
    if result_shape == "duplicate-results":
        entries = [
            _assistant(call),
            _result("synthesis", structured={"agentId": "one"}),
            _result("synthesis", structured={"agentId": "two"}),
        ]
    elif result_shape == "malformed-result":
        entries = [
            _assistant(call),
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": 123,
                            "content": "malformed",
                        }
                    ],
                },
            },
        ]
    elif result_shape == "earlier-result":
        entries = [
            _result("synthesis", structured={"agentId": "early"}),
            _assistant(call),
        ]
    elif result_shape == "duplicate-call-id":
        entries = [
            _assistant(
                call,
                _special_agent_call(
                    "synthesis", output_dir, "review-reconciliator"
                ),
            ),
            _result("synthesis", structured={"agentId": "duplicate"}),
        ]
    else:
        entries = [
            _assistant(
                call,
                _special_agent_call(
                    "second", output_dir, "review-reconciliator"
                ),
            ),
            _result("synthesis", structured={"agentId": "same"}),
            _result("second", structured={"agentId": "same"}),
        ]
    _write_jsonl(sessions / "unpairable.jsonl", entries)

    result = enrich_run_transcript(
        _manifest("unpairable", tmp_path, output_dir, started=[]),
        sessions,
        {"review-reconciliator"},
    )

    assert result["correlation"]["expected_by_agent"] == {
        "review-reconciliator": expected_count
    }
    assert result["correlation"]["correlated_by_agent"] == {}
    assert result["correlation"]["missing_by_agent"] == {
        "review-reconciliator": expected_count
    }
    assert result["correlation"]["expected_count"] == expected_count
    assert result["correlation"]["correlated_count"] == 0
    assert result["correlation"]["missing_count"] == expected_count
    assert result["correlation"]["complete"] is False


def test_manifest_and_main_call_observations_merge_without_double_counting(tmp_path):
    sessions = tmp_path / "sessions"
    output_dir = tmp_path / "run"
    _write_jsonl(
        sessions / "union.jsonl",
        [
            _assistant(
                _call("reviewer", "Agent", prompt=_agent_prompt(output_dir)),
                _special_agent_call(
                    "reconciler", output_dir, "review-reconciliator"
                ),
            ),
            _result("reviewer", structured={"agentId": "reviewer-id"}),
            _result("reconciler", structured={"agentId": "reconciler-id"}),
        ],
    )
    for agent_id in ("reviewer-id", "reconciler-id"):
        _write_jsonl(
            sessions / "union" / "subagents" / f"agent-{agent_id}.jsonl",
            [_assistant(usage=_usage(1, 1))],
        )

    result = enrich_run_transcript(
        _manifest("union", tmp_path, output_dir, started=["security-reviewer"]),
        sessions,
        {"security-reviewer", "review-reconciliator"},
    )

    assert result["correlation"]["expected_by_agent"] == {
        "review-reconciliator": 1,
        "security-reviewer": 1,
    }
    assert result["correlation"]["correlated_by_agent"] == {
        "review-reconciliator": 1,
        "security-reviewer": 1,
    }
    assert result["correlation"]["expected_count"] == 2
    assert result["correlation"]["correlated_count"] == 2
    assert result["correlation"]["missing_count"] == 0
    assert result["correlation"]["complete"] is True


def test_multiple_retry_calls_are_counted_as_distinct_dispatches(tmp_path):
    sessions = tmp_path / "sessions"
    output_dir = tmp_path / "run"
    _write_jsonl(
        sessions / "retries.jsonl",
        [
            _assistant(
                _call("first", "Agent", prompt=_agent_prompt(output_dir)),
                _call("second", "Agent", prompt=_agent_prompt(output_dir)),
            ),
            _result("first", structured={"agentId": "first-id"}),
            _result("second", structured={"agentId": "second-id"}),
        ],
    )
    for agent_id in ("first-id", "second-id"):
        _write_jsonl(
            sessions / "retries" / "subagents" / f"agent-{agent_id}.jsonl",
            [_assistant(usage=_usage(1, 1))],
        )

    result = enrich_run_transcript(
        _manifest(
            "retries",
            tmp_path,
            output_dir,
            started=["security-reviewer", "security-reviewer"],
        ),
        sessions,
        {"security-reviewer"},
    )

    assert result["warnings"] == []
    assert result["correlation"]["expected_by_agent"] == {
        "security-reviewer": 2
    }
    assert result["correlation"]["correlated_by_agent"] == {
        "security-reviewer": 2
    }
    assert result["correlation"]["missing_by_agent"] == {}
    assert result["correlation"]["expected_count"] == 2
    assert result["correlation"]["correlated_count"] == 2
    assert result["correlation"]["missing_count"] == 0
    assert result["correlation"]["complete"] is True
    assert len(result["agent_usage"]) == 2


class TestBudgetAndEvidenceAccounting:
    """Round-7 accounting contracts: tool-call numerators, synthesis
    exclusion from builder metrics, and unresolved-call incompleteness."""

    def _run_with_subagent(self, tmp_path, subagent_entries):
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        session_id = "accounting"
        _write_jsonl(
            sessions / f"{session_id}.jsonl",
            [
                _assistant(
                    _call("dispatch", "Agent", prompt=_agent_prompt(output_dir))
                ),
                _result("dispatch", structured={"agentId": "reviewer-agent"}),
            ],
        )
        _write_jsonl(
            sessions / session_id / "subagents" / "agent-reviewer-agent.jsonl",
            subagent_entries,
        )
        manifest = _manifest(
            session_id, tmp_path, output_dir, started=["security-reviewer"]
        )
        return enrich_run_transcript(manifest, sessions, {"security-reviewer"})

    def test_agent_usage_carries_tool_call_counts(self, tmp_path):
        """Budget utilization needs a numerator: the actual number of tool
        calls the agent issued, alongside the budget_target denominator."""
        result = self._run_with_subagent(
            tmp_path,
            [
                _assistant(
                    _call("read", "Read", file_path="src/a.py"),
                    usage=_usage(1, 2),
                ),
                _result("read"),
                _assistant(_call("search", "Glob", pattern="src/*.py")),
                _result("search"),
            ],
        )

        [entry] = result["agent_usage"]
        assert entry["tool_calls"] == 2
        assert result["completeness"]["agent_data"] is True

    def test_unresolved_tool_call_marks_agent_evidence_incomplete(
        self, tmp_path
    ):
        """A transcript ending after tool_use but before tool_result (agent
        crash mid-call) is truncated evidence — the run must not report
        complete empty read/failure data."""
        result = self._run_with_subagent(
            tmp_path,
            [
                _assistant(
                    _call("dangling", "Read", file_path="src/a.py"),
                    usage=_usage(1, 2),
                ),
            ],
        )

        assert {
            "code": "agent_transcript_unresolved_calls",
            "agent": "security-reviewer",
        } in result["warnings"]
        assert result["completeness"]["scope_comparable_reads"] is False
        assert result["completeness"]["agent_data"] is False
        assert result["artifact_writes"]["complete"] is False
        [entry] = result["agent_usage"]
        assert entry["tool_calls"] == 1

    def test_duplicate_tool_call_ids_mark_agent_evidence_incomplete(
        self, tmp_path
    ):
        """Repeated tool-use IDs make pairing ambiguous — the skipped calls
        vanish from reads and failures, so the evidence is incomplete."""
        result = self._run_with_subagent(
            tmp_path,
            [
                _assistant(
                    _call("dup", "Read", file_path="src/a.py"),
                    usage=_usage(1, 2),
                ),
                _result("dup"),
                _assistant(_call("dup", "Read", file_path="src/b.py")),
                _result("dup"),
            ],
        )

        assert {
            "code": "agent_transcript_unresolved_calls",
            "agent": "security-reviewer",
        } in result["warnings"]
        assert result["completeness"]["scope_comparable_reads"] is False
        [entry] = result["agent_usage"]
        assert entry["tool_calls"] == 2
        assert result["observed_reads"]["all"] == []

    def test_unclassifiable_result_marks_agent_evidence_incomplete(
        self, tmp_path
    ):
        """A paired result whose payload matches no recognized schema is
        unclassifiable evidence — the families must not claim completeness."""
        result = self._run_with_subagent(
            tmp_path,
            [
                _assistant(
                    _call("odd", "Read", file_path="src/a.py"),
                    usage=_usage(1, 2),
                ),
                # Structured payload with an unrecognized shape resolves to
                # neither success nor failure.
                _result(
                    "odd",
                    structured={"unrecognized": {"shape": True}},
                    is_error=None,
                ),
            ],
        )

        assert {
            "code": "agent_transcript_unresolved_calls",
            "agent": "security-reviewer",
        } in result["warnings"]
        assert result["completeness"]["scope_comparable_reads"] is False

    def test_missing_scope_mapping_downgrades_reads_but_not_usage(
        self, tmp_path
    ):
        """Without an authoritative by_agent scope mapping the in/out
        partition is unsupported — the reads family goes partial instead of
        reporting every read as out-of-scope with complete confidence.
        Usage and builder evidence do not depend on scope and stay
        complete."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        session_id = "scopeless"
        _write_jsonl(
            sessions / f"{session_id}.jsonl",
            [
                _assistant(
                    _call("dispatch", "Agent", prompt=_agent_prompt(output_dir))
                ),
                _result("dispatch", structured={"agentId": "reviewer-agent"}),
            ],
        )
        _write_jsonl(
            sessions / session_id / "subagents" / "agent-reviewer-agent.jsonl",
            [
                _assistant(
                    _call("read", "Read", file_path="src/in.py"),
                    usage=_usage(1, 2),
                ),
                _result("read"),
            ],
        )
        manifest = _manifest(
            session_id, tmp_path, output_dir, started=["security-reviewer"]
        )
        manifest["coverage"] = None

        result = enrich_run_transcript(manifest, sessions, {"security-reviewer"})

        assert {
            "code": "agent_scope_evidence_missing",
            "agent": "security-reviewer",
        } in result["warnings"]
        assert result["completeness"]["scope_comparable_reads"] is False
        assert result["completeness"]["observed_reads"] is False
        assert result["observed_reads"]["all"] == ["src/in.py"]
        assert result["completeness"]["agent_data"] is True
        assert result["completeness"]["usage"] is True
        assert result["completeness"]["artifact_writes"] is True

    def test_missing_synthesis_transcript_keeps_builder_compliance_complete(
        self, tmp_path
    ):
        """Builder compliance is regular-reviewer evidence; a missing critic
        transcript degrades the synthesis read family, not artifact data."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        session_id = "critic-missing"
        _write_jsonl(
            sessions / f"{session_id}.jsonl",
            [
                _assistant(
                    _call("dispatch", "Agent", prompt=_agent_prompt(output_dir))
                ),
                _result("dispatch", structured={"agentId": "reviewer-agent"}),
                _assistant(
                    _special_agent_call("judge", output_dir, "critic")
                ),
                _result("judge", structured={"agentId": "critic-agent"}),
            ],
        )
        _write_jsonl(
            sessions / session_id / "subagents" / "agent-reviewer-agent.jsonl",
            [
                _assistant(
                    _call("read", "Read", file_path="src/in.py"),
                    usage=_usage(1, 2),
                ),
                _result("read"),
            ],
        )
        # No transcript for critic-agent — its evidence is missing.
        manifest = _manifest(
            session_id, tmp_path, output_dir, started=["security-reviewer"]
        )

        result = enrich_run_transcript(
            manifest, sessions, {"security-reviewer", "critic"}
        )

        assert result["completeness"]["scope_comparable_reads"] is True
        assert result["completeness"]["non_scope_comparable_reads"] is False
        assert result["completeness"]["agent_data"] is False
        assert result["completeness"]["artifact_writes"] is True
        assert result["artifact_writes"]["complete"] is True
        assert result["artifact_writes"]["builder_attempted"] is False

    def test_synthesis_only_run_keeps_builder_metrics_available(
        self, tmp_path
    ):
        """A complete run that dispatched only synthesis agents has nothing
        for builder metrics to observe — available and empty, not the
        contradictory available=false/complete=true the sanitizer rejects."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        session_id = "synthesis-only"
        _write_jsonl(
            sessions / f"{session_id}.jsonl",
            [
                _assistant(
                    _special_agent_call(
                        "reconcile", output_dir, "review-reconciliator"
                    )
                ),
                _result("reconcile", structured={"agentId": "reconciler-agent"}),
            ],
        )
        _write_jsonl(
            sessions / session_id / "subagents" / "agent-reconciler-agent.jsonl",
            [
                _assistant(
                    _call("read", "Read", file_path="src/a.py"),
                    usage=_usage(1, 2),
                ),
                _result("read"),
            ],
        )
        manifest = _manifest(session_id, tmp_path, output_dir, started=[])

        result = enrich_run_transcript(
            manifest, sessions, {"review-reconciliator"}
        )

        artifacts = result["artifact_writes"]
        assert artifacts["available"] is True
        assert artifacts["complete"] is True
        assert artifacts["builder_attempted"] is False
        assert artifacts["by_agent"] == []

    def test_synthesis_agents_stay_out_of_builder_attempt_metrics(
        self, tmp_path
    ):
        """Synthesis agents are not subject to the reviewer builder-envelope
        contract; their builder_attempted=false rows must not inflate the
        noncompliance denominator."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        session_id = "synthesis-artifacts"
        _write_jsonl(
            sessions / f"{session_id}.jsonl",
            [
                _assistant(
                    _call("dispatch", "Agent", prompt=_agent_prompt(output_dir))
                ),
                _result("dispatch", structured={"agentId": "reviewer-agent"}),
                _assistant(
                    _special_agent_call(
                        "reconcile", output_dir, "review-reconciliator"
                    )
                ),
                _result("reconcile", structured={"agentId": "reconciler-agent"}),
            ],
        )
        for agent_id in ("reviewer-agent", "reconciler-agent"):
            _write_jsonl(
                sessions / session_id / "subagents" / f"agent-{agent_id}.jsonl",
                [
                    _assistant(
                        _call(f"{agent_id}-read", "Read", file_path="src/a.py"),
                        usage=_usage(1, 2),
                    ),
                    _result(f"{agent_id}-read"),
                ],
            )
        manifest = _manifest(
            session_id, tmp_path, output_dir, started=["security-reviewer"]
        )

        result = enrich_run_transcript(
            manifest, sessions, {"security-reviewer", "review-reconciliator"}
        )

        by_agent = result["artifact_writes"]["by_agent"]
        assert [item["agent"] for item in by_agent] == ["security-reviewer"]
