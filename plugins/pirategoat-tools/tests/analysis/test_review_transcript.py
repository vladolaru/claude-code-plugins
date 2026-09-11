"""Deterministic tests for privacy-preserving review transcript enrichment."""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import textwrap
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
manifest_step_timeline = _mod._manifest_step_timeline
result_state = _mod._result_state
parse_builder_envelope = _mod.parse_builder_envelope
safe_model = _mod._safe_model

_bootstrap_spec = importlib.util.spec_from_file_location(
    "review_bootstrap_for_transcript_test", BOOTSTRAP_PATH
)
_bootstrap_mod = importlib.util.module_from_spec(_bootstrap_spec)
_bootstrap_spec.loader.exec_module(_bootstrap_mod)

_TEST_TRANSCRIPT_START = datetime(2026, 7, 20, 10, 0, 0, tzinfo=timezone.utc)
_MALFORMED_RUN_IDENTITIES = (0, "run unsafe")
_MALFORMED_RUN_ID_CASES = ("zero", "unsafe-prefix")


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


_POLL_COMMAND = 'python3 /plugin/scripts/review/agents_status.py --output-dir "/output"'


def _bash_exit(tool_id: str, command: str, code: int) -> list[dict]:
    """A non-zero Bash exit as the harness records it: `is_error`, the code
    on the content's first line, and a plain-string `toolUseResult` with no
    structured exit code."""
    return [
        _assistant(_call(tool_id, "Bash", command=command)),
        _result(
            tool_id,
            f"Exit code {code}\noutput",
            is_error=True,
            structured=f"Error: Exit code {code}\noutput",
        ),
    ]


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


def _adapter_prompt(
    output_dir: Path,
    instance: str | None = "repo-renewals-reviewer",
) -> str:
    """The exact adapter ref-mode command form step 6 emits (pipeline.py)."""
    instance_part = f"--instance-name {instance} " if instance else ""
    return (
        "python3 /plugin/review/agent/bootstrap.py "
        "--agent repo-reviewer-adapter "
        f"{instance_part}"
        "--repo-agent-ref .ai/agents/review/renewals.md "
        "--adapter-label 'Renewals reviewer' "
        "--execution inline --channel blocking --scope-domains code "
        "--model-tier '' "
        f'--range "base..head" --output-dir "{output_dir}"'
    )


def _builder_envelope(body: str | None, *, header: str | None = None) -> str:
    header = header or (
        "PIRATEGOAT_PLUGIN_ROOT=/plugin PIRATEGOAT_OUTPUT_DIR=/output "
        "PIRATEGOAT_REVIEWER_NAME=security PIRATEGOAT_PR_ID=42 "
        "PIRATEGOAT_PLUGIN_VERSION=1.114.0 python3 <<PY"
    )
    return (
        header
        if body is None
        else (
            f"{header}\n"
            "builder = ReviewOutputBuilder.open('/output', '42', 'security')\n"
            f"{body}\nbuilder.save_draft()\nPY"
        )
    )


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
        "assignment": {"assigned_files_by_agent": {"security-reviewer": ["src/in.py"]}},
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


class TestSafeModel:
    """The model sanitizer admits real Claude model ids and nothing else.

    It rejects rather than normalizes: a safe value is returned verbatim, so
    the bracketed context-window variant tag is preserved rather than stripped.

    review_transcript.py:29 is ONE regex, so the params here are one per
    distinct regex component rather than one per interesting-looking string:
    unbalanced and repeated brackets all fail the same optional-group anchor,
    non-str values all die on the same isinstance, and shell metacharacters
    die on the same literal `claude-` prefix that any non-model string does.
    """

    @pytest.mark.parametrize(
        "value",
        [
            "claude-sonnet-5",
            "claude-opus-5[1m]",
            "claude-" + "a" * 119 + "[1m]",
        ],
    )
    def test_accepts_and_preserves_real_model_ids(self, value):
        assert safe_model(value) == value

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "evil-model",
            "claude-opus 5",
            "claude-" + "a" * 200,
            "claude-x[]",
            "claude-x[1m]extra",
        ],
    )
    def test_rejects_unsafe_values(self, value):
        assert safe_model(value) is None


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
        ],
        ids=["instruction-envelope"],
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
            ("run:colon", "--output-dir={output_dir}"),
        ],
        ids=["quoted-space", "equals-colon"],
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
            "--output-dir $REVIEW_DIR",
            "--output-dir",
        ],
        ids=["prefix", "unresolved", "missing-value"],
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

    def test_legacy_agent_id_anchors_to_the_final_trailer(self, tmp_path):
        """Reviewer prose preceding the harness trailer may mention
        "agentId: <anything>" — a first-match scan would retain the prose
        token in the privacy-reduced report and correlate the wrong
        transcript. Only the final line-anchored trailer counts."""
        session = tmp_path / "legacy-prose.jsonl"
        output_dir = tmp_path / "pr-review-2"
        _write_jsonl(
            session,
            [
                _assistant(_call("t1", "Task", prompt=_agent_prompt(output_dir))),
                _result(
                    "t1",
                    "Finding: the log line shows agentId: merchant-123 inline.\n"
                    "agentId: prose-456 appeared at a line start too.\n"
                    "Done.\n"
                    "agentId: agent-real (use SendMessage with to: "
                    "'agent-real' to continue this agent)",
                ),
            ],
        )

        result = correlate_run_agents(session, output_dir, {"security-reviewer"})

        assert result[0]["agent_id"] == "agent-real"
        serialized = json.dumps(result)
        assert "merchant-123" not in serialized
        assert "prose-456" not in serialized

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
        ],
        ids=["plain", "list", "bold", "split-backtick"],
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
                            "builder.save_draft()"
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

    def test_pre_1_114_envelope_is_still_a_measured_builder_attempt(
        self, tmp_path
    ):
        """Transcripts are immutable; a reader that stops recognizing them lies.

        Every transcript recorded before 1.114.0 carries the four-assignment
        envelope. Refusing it would report `builder_attempted: false` for
        saves that demonstrably happened — a measured false, which is a wrong
        answer rather than a missing one, and which cohort.py would bucket
        into runs_without_builder_attempts instead of the unknown bucket that
        exists for genuinely unobservable state. Nothing here reads the
        appended version, so the older generation stays fully measurable.
        """
        command = (
            "PIRATEGOAT_PLUGIN_ROOT=/plugin PIRATEGOAT_OUTPUT_DIR=/output "
            "PIRATEGOAT_REVIEWER_NAME=security PIRATEGOAT_PR_ID=42 "
            "python3 <<'PY'\n"
            "builder = ReviewOutputBuilder.open('/output', '42', 'security')\n"
            "builder.save_draft()\nPY"
        )
        transcript = _write_jsonl(
            tmp_path / "legacy-envelope.jsonl",
            [
                _assistant(_call("builder", "Bash", command=command)),
                _result(
                    "builder",
                    "DRAFT TOTALS: safe",
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

    def test_envelope_names_are_the_whole_recognition(self):
        """Historical envelope generations stay measurable.

        Transcripts are immutable: a reader that stops recognizing a
        generation reports `builder_attempted: false` for saves that
        demonstrably happened, which cohort.py buckets into
        runs_without_builder_attempts instead of the unknown bucket that
        exists for genuinely unobservable state.
        """
        stable = (
            "PIRATEGOAT_PLUGIN_ROOT=/plugin PIRATEGOAT_OUTPUT_DIR=/output "
            "PIRATEGOAT_REVIEWER_NAME=security PIRATEGOAT_PR_ID=42 "
        )
        body = (
            "builder = ReviewOutputBuilder.open('/output', '42', 'security')\n"
            "builder.save_draft()\nPY"
        )
        # Empty-valued is the shape bootstrap emits when the run resolved no
        # version, and must not read as a foreign assignment.
        assert parse_builder_envelope(
            stable + "PIRATEGOAT_PLUGIN_VERSION= python3 <<PY\n" + body
        ) == {"output_dir": "/output", "reviewer": "security"}
        # HISTORICAL-ENVELOPE ALLOWANCE ONLY — not live. 1.114.0 briefly
        # carried the call-budget target on this envelope before a later fix
        # moved it to the assignment; the live envelope never emits this name
        # again (see test_envelope_never_carries_a_budget_assignment in
        # test_bootstrap_integration.py). run12's own recorded transcripts DO
        # carry it.
        assert parse_builder_envelope(
            stable
            + "PIRATEGOAT_PLUGIN_VERSION=1.114.0 PIRATEGOAT_REVIEW_BUDGET=80 "
            "python3 <<PY\n" + body
        ) == {"output_dir": "/output", "reviewer": "security"}

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param(
                "cat > \"$TMPDIR/perf_builder.py\" <<'PYEOF'\n"
                "import sys, os\n"
                "plugin_root = os.environ[\"PIRATEGOAT_PLUGIN_ROOT\"]\n"
                "PYEOF\n"
                "PIRATEGOAT_PLUGIN_ROOT=/plugin PIRATEGOAT_OUTPUT_DIR=/output "
                "PIRATEGOAT_REVIEWER_NAME=security PIRATEGOAT_PR_ID=42 "
                "PIRATEGOAT_PLUGIN_VERSION=1.114.0 "
                "python3 \"$TMPDIR/perf_builder.py\"",
                id="script-written-then-run-in-one-command",
            ),
            pytest.param(
                "cd /repo\n"
                "PIRATEGOAT_PLUGIN_ROOT=/plugin PIRATEGOAT_OUTPUT_DIR=/output "
                "PIRATEGOAT_REVIEWER_NAME=security PIRATEGOAT_PR_ID=42 "
                "PIRATEGOAT_PLUGIN_VERSION=1.114.0 python3 <<'PY'\npass\nPY",
                id="cd-on-the-first-line",
            ),
            pytest.param(
                "PIRATEGOAT_PLUGIN_ROOT=/plugin PIRATEGOAT_OUTPUT_DIR=/output "
                "PIRATEGOAT_REVIEWER_NAME=security PIRATEGOAT_PR_ID=42 "
                "PIRATEGOAT_PLUGIN_VERSION=1.114.0 "
                "python3 /private/tmp/scratch/concurrency_builder.py",
                id="script-path-instead-of-heredoc",
            ),
            pytest.param(
                "cd /repo && "
                "PIRATEGOAT_PLUGIN_ROOT=/plugin PIRATEGOAT_OUTPUT_DIR=/output "
                "PIRATEGOAT_REVIEWER_NAME=security PIRATEGOAT_PR_ID=42 "
                "python3 - <<'PY'\npass\nPY",
                id="chained-after-cd-with-stdin-dash",
            ),
        ],
    )
    def test_envelope_is_recognized_wherever_the_reviewer_ran_it(
        self, command
    ):
        """The envelope is the recognition, not its position in the command.

        Run 14 on pokedex: six of eighteen reviewers ran the exact envelope
        bootstrap prescribed, but wrote the program to a scratch file first,
        put `cd <repo>` on line one, or passed a script path instead of a
        heredoc. A first-line-only reader reported all six as
        `builder_attempted: false`, which cohort.py buckets as reviewers
        that never used the builder — for saves that demonstrably happened
        and finalized.
        """
        assert parse_builder_envelope(command) == {
            "output_dir": "/output", "reviewer": "security",
        }

    def test_envelope_names_inside_a_heredoc_body_are_not_an_envelope(self):
        """A Python line that mentions the names is the program, not a save."""
        command = (
            "python3 <<'PY'\n"
            "env = 'PIRATEGOAT_PLUGIN_ROOT=/plugin PIRATEGOAT_OUTPUT_DIR=/o "
            "PIRATEGOAT_REVIEWER_NAME=s PIRATEGOAT_PR_ID=1 python3'\n"
            "PY"
        )
        assert parse_builder_envelope(command) is None

    def test_real_bootstrap_builder_envelope_is_counted_as_one_attempt(
        self, tmp_path
    ):
        """The rendered envelope, from the real renderer, is recognized.

        Hand-built strings elsewhere pin the names; only this test proves
        what bootstrap actually emits is recognized. The budget no longer
        rides this envelope at all (it moved to the assignment
        in 1.114.0), so there is no budgeted/unbudgeted variant left to
        parametrize here — the shape pin for "the live envelope never
        carries a budget assignment" lives at the source in
        `test_envelope_never_carries_a_budget_assignment`
        (`test_bootstrap_integration.py`), including across both a
        calibrated and an uncalibrated run. If it did start appending one
        again, this test would still recognize it (the historical-envelope
        allowance in `_BUILDER_ENV_OPTIONAL` exists precisely so a
        recorded transcript carrying it is never misread as no-save) — this
        test only proves recognition, not envelope shape.
        """
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
            review_claimable_count=0,
            has_php=False,
            review_budget=80,
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
                    "DRAFT TOTALS: safe",
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
                        "PIRATEGOAT_PR_ID='42' "
                        "PIRATEGOAT_PLUGIN_VERSION='1.114.0' python3 <<'PY'"
                    ),
                ),
                id="harmless-quoting",
            ),
            pytest.param(
                _builder_envelope(
                    "print('safe')",
                    header=(
                        "PIRATEGOAT_PLUGIN_VERSION=1.114.0 "
                        "PIRATEGOAT_PR_ID=42 PIRATEGOAT_REVIEWER_NAME=security "
                        "PIRATEGOAT_OUTPUT_DIR=/output "
                        "PIRATEGOAT_PLUGIN_ROOT=/plugin python3 <<PY"
                    ),
                ),
                id="assignment-order",
            ),
            pytest.param(
                _builder_envelope(
                    "print('safe')",
                    header=(
                        "PIRATEGOAT_PLUGIN_ROOT= PIRATEGOAT_OUTPUT_DIR= "
                        "PIRATEGOAT_REVIEWER_NAME= PIRATEGOAT_PR_ID= "
                        "PIRATEGOAT_PLUGIN_VERSION= python3 <<PY"
                    ),
                ),
                id="empty-assignment-values",
            ),
        ],
    )
    def test_pipeline_builder_envelope_is_one_attempt(
        self, tmp_path, command
    ):
        transcript = _write_jsonl(
            tmp_path / "builder-envelope.jsonl",
            [
                _assistant(_call("builder", "Bash", command=command)),
                _result(
                    "builder",
                    "DRAFT TOTALS: safe",
                    is_error=None,
                    structured={"exitCode": 0},
                ),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])

        assert parse_builder_envelope(command) is not None
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
                    "for path in ['src/a.php', 'src/b.php']:\n"
                    "    builder.claim_files_reviewed(path)"
                ),
                id="loop-over-claims",
            ),
        ],
    )
    def test_envelope_with_an_unmodelled_body_is_still_an_attempt(
        self, tmp_path, command
    ):
        """The envelope is the attempt; the body is the reviewer's program.

        Analysis reads the artifact the envelope names, so a body this module
        cannot parse — or would not have modelled — says nothing about
        whether the save was attempted.
        """
        transcript = _write_jsonl(
            tmp_path / "unmodelled-builder-body.jsonl",
            [
                _assistant(_call("builder", "Bash", command=command)),
                _result("builder", "safe", structured={"exitCode": 0}),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])

        assert parse_builder_envelope(command) is not None
        assert result["artifact_writes"]["builder_attempted"] is True

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param(
                # Drops a REQUIRED name (PR_ID). Omitting the optional
                # PIRATEGOAT_PLUGIN_VERSION instead is the pre-1.114.0
                # envelope, which is recognized — see the legacy-generation
                # tests below.
                "PIRATEGOAT_PLUGIN_ROOT=/plugin PIRATEGOAT_OUTPUT_DIR=/output "
                "PIRATEGOAT_REVIEWER_NAME=security "
                "PIRATEGOAT_PLUGIN_VERSION=1.114.0 "
                "python3 <<PY\npass\nPY",
                id="missing-required-assignment",
            ),
            pytest.param(
                "PIRATEGOAT_PLUGIN_ROOT=/plugin PIRATEGOAT_OUTPUT_DIR=/output "
                "PIRATEGOAT_REVIEWER_NAME=security PIRATEGOAT_PR_ID=42 "
                "PIRATEGOAT_PLUGIN_VERSION=1.114.0 EXTRA=safe "
                "python3 <<PY\npass\nPY",
                id="extra-assignment",
            ),
            pytest.param(
                "PIRATEGOAT_PLUGIN_ROOT=/plugin PIRATEGOAT_PLUGIN_ROOT=/other "
                "PIRATEGOAT_OUTPUT_DIR=/output PIRATEGOAT_REVIEWER_NAME=security "
                "PIRATEGOAT_PR_ID=42 python3 <<PY\npass\nPY",
                id="duplicate-assignment",
            ),
            pytest.param(
                "PIRATEGOAT_PLUGIN_ROOT=/plugin PIRATEGOAT_OUTPUT_DIR=/output "
                "PIRATEGOAT_REVIEWER_NAME=security PIRATEGOAT_PR_ID=42 "
                "PIRATEGOAT_PLUGIN_VERSION=1.114.0 "
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
        self, tmp_path, command, request
    ):
        assert parse_builder_envelope(command) is None

        # Every row takes the identical "no envelope -> all zeros"
        # analyze_subagent path; only the first row also proves that full
        # artifact_writes shape and the privacy pin, so the rest stay a
        # plain unit check on parse_builder_envelope.
        if request.node.callspec.id != "missing-required-assignment":
            return

        transcript = _write_jsonl(
            tmp_path / "unrelated-heredoc.jsonl",
            [
                _assistant(_call("bash", "Bash", command=command)),
                _result("bash", "safe", is_error=False),
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
        assert command not in " ".join(_flatten_strings(result))

    def test_recovery_matches_relative_and_absolute_forms_of_one_file(
        self, tmp_path
    ):
        """A failed operation on a repo-relative path retried with the
        equivalent absolute path is the SAME file operation — raw-string
        hashing would report the failure as unrecovered."""
        transcript = _write_jsonl(
            tmp_path / "path-recovery.jsonl",
            [
                _assistant(_call("fail", "Read", file_path="src/a.py")),
                _result(
                    "fail",
                    "<tool_use_error>File does not exist.</tool_use_error>",
                    is_error=True,
                ),
                _assistant(
                    _call(
                        "retry", "Read",
                        file_path=str(tmp_path / "src" / "a.py"),
                    )
                ),
                _result("retry"),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])

        [failure] = result["tool_failures"]
        assert failure["recovered"] is True

    def test_earlier_read_success_does_not_mark_later_failure_recovered(
        self, tmp_path
    ):
        target = str(tmp_path / "safe.py")
        transcript = _write_jsonl(
            tmp_path / "earlier-read-success.jsonl",
            [
                _assistant(_call("success", "Read", file_path=target)),
                _result("success", is_error=False),
                _assistant(_call("failure", "Read", file_path=target)),
                _result("failure", "API Error: retry", is_error=None),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])

        assert result["tool_failures"] == [
            {
                "category": "api_error",
                "detector": "signature",
                "tool": "Read",
                "operation_class": "read",
                "normalized_target": _mod._file_target(target, tmp_path),
                "recovered": False,
                "recovery": "none",
            }
        ]

    def test_builder_success_on_different_target_marks_failure_recovered(
        self, tmp_path
    ):
        failed_command = _builder_envelope("print('attempt')")
        successful_command = _builder_envelope('builder.add_positive("safe")')
        transcript = _write_jsonl(
            tmp_path / "builder-different-target-recovery.jsonl",
            [
                _assistant(_call("failure", "Bash", command=failed_command)),
                _result(
                    "failure",
                    "failed",
                    is_error=None,
                    structured={"exitCode": 1},
                ),
                _assistant(_call("success", "Bash", command=successful_command)),
                _result(
                    "success",
                    "DRAFT TOTALS: safe",
                    is_error=None,
                    structured={"exitCode": 0},
                ),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])

        assert failed_command != successful_command
        assert result["tool_failures"] == [
            {
                "category": "structured_failure",
                "detector": "structured",
                "tool": "Bash",
                "operation_class": "builder_output_attempt",
                "normalized_target": _mod._opaque_target(failed_command),
                "recovered": True,
                "recovery": "later_success",
            }
        ]

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
                    "DRAFT TOTALS: safe",
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

    @pytest.mark.parametrize(
        "command,exit_code,category,recovery",
        [
            pytest.param(
                _POLL_COMMAND, 2, "poll_outcome", "not_applicable",
                id="poll-still-running",
            ),
            pytest.param(
                f"{_POLL_COMMAND} --wait --max-seconds 60", 3,
                "poll_outcome", "not_applicable", id="poll-wait-expired",
            ),
            pytest.param(
                f"{_POLL_COMMAND} 2>&1", 2, "poll_outcome", "not_applicable",
                id="poll-redirected",
            ),
            pytest.param(
                _POLL_COMMAND, 1, "structured_failure", "none", id="poll-error",
            ),
            pytest.param(
                "grep -n ALL_DONE /plugin/scripts/review/agents_status.py", 2,
                "structured_failure", "none", id="script-as-operand",
            ),
            pytest.param(
                "ls /output/missing.json", 1, "structured_failure", "none",
                id="other-command",
            ),
        ],
    )
    def test_poll_exit_is_listed_as_poll_outcome_by_program_and_code(
        self, tmp_path, command, exit_code, category, recovery
    ):
        """`agents_status.py` exits 2 (still running) and 3 (`--wait`
        expired) by contract; the harness flags both as errors. Only that
        program with those codes is a poll outcome."""
        transcript = _write_jsonl(
            tmp_path / "exit.jsonl", _bash_exit("call", command, exit_code)
        )

        [failure] = analyze_subagent(transcript, tmp_path, [])["tool_failures"]

        assert (failure["category"], failure["recovered"], failure["recovery"]) == (
            category, False, recovery,
        )

    def test_poll_series_ending_in_all_done_is_not_a_recovery(self, tmp_path):
        transcript = _write_jsonl(
            tmp_path / "poll-series.jsonl",
            [
                *_bash_exit("poll-1", _POLL_COMMAND, 2),
                *_bash_exit("poll-2", _POLL_COMMAND, 2),
                _assistant(_call("poll-3", "Bash", command=_POLL_COMMAND)),
                _result(
                    "poll-3",
                    "ALL_DONE: true",
                    is_error=False,
                    structured={"stdout": "ALL_DONE: true", "stderr": ""},
                ),
            ],
        )

        failures = analyze_subagent(transcript, tmp_path, [])["tool_failures"]

        assert [
            (f["category"], f["recovered"], f["recovery"]) for f in failures
        ] == [("poll_outcome", False, "not_applicable")] * 2

    def test_poll_outcome_does_not_recover_an_earlier_poll_error(self, tmp_path):
        transcript = _write_jsonl(
            tmp_path / "poll-after-error.jsonl",
            [
                *_bash_exit("error", _POLL_COMMAND, 1),
                *_bash_exit("poll", _POLL_COMMAND, 2),
            ],
        )

        failures = analyze_subagent(transcript, tmp_path, [])["tool_failures"]

        assert [(f["category"], f["recovered"]) for f in failures] == [
            ("structured_failure", False),
            ("poll_outcome", False),
        ]

    def test_search_miss_the_harness_interprets_is_not_a_failure(self, tmp_path):
        """The harness records a grep/rg miss as a success carrying
        `returnCodeInterpretation`, never as `is_error`, so a search that
        found nothing needs no category of its own."""
        transcript = _write_jsonl(
            tmp_path / "grep-miss.jsonl",
            [
                _assistant(_call("grep", "Bash", command="grep -n absent src/a.py")),
                _result(
                    "grep",
                    "",
                    is_error=False,
                    structured={
                        "stdout": "",
                        "stderr": "",
                        "interrupted": False,
                        "returnCodeInterpretation": "No matches found",
                    },
                ),
            ],
        )

        assert analyze_subagent(transcript, tmp_path, [])["tool_failures"] == []

    def test_bash_failure_cannot_recover_through_write_success(self, tmp_path):
        command = _builder_envelope("print('attempt')")
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
                            "builder.save_draft()"
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

    def test_detects_allowlisted_text_failure_signatures(self, tmp_path):
        transcript = _write_jsonl(
            tmp_path / "api_error.jsonl",
            [
                _assistant(_call("x", "Read", file_path=str(tmp_path / "safe.py"))),
                _result("x", "API Error: overloaded", is_error=None),
            ],
        )

        failure = analyze_subagent(transcript, tmp_path, [])["tool_failures"][0]
        assert failure["category"] == "api_error"
        assert failure["detector"] == "signature"

    @pytest.mark.parametrize(
        "content",
        ["File has not been read yet"],
        ids=["read-first"],
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

    def test_nonterminal_structured_fields_defer_to_failure_signatures(
        self, tmp_path
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
                    structured={"interrupted": False},
                ),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])
        assert result["tool_failures"][0]["category"] == "api_error"
        assert result["tool_failures"][0]["detector"] == "signature"
        assert result["observed_reads"]["all"] == []

    def test_nonterminal_structured_fields_without_signature_remain_unknown(
        self, tmp_path
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
                    structured={"status": "started"},
                ),
            ],
        )

        result = analyze_subagent(transcript, tmp_path, [])
        assert result["tool_failures"] == []
        assert result["observed_reads"]["all"] == []

    # _structured_success ends in one `status.lower() in {...}` membership
    # check, so enumerating the vocabulary never was a completeness pin —
    # "ok" is in the set and was never listed here.
    @pytest.mark.parametrize("status", ["success"])
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

    @pytest.mark.parametrize(
        "original_file,stale",
        [(None, True)],
        ids=["stale-recovered-null-original"],
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
        "tool_name,structured",
        [
            (
                "Read",
                {"filePath": "/safe/metadata.py"},
            ),
            (
                "Read",
                {
                    "type": "text",
                    "file": _current_read_result(
                        "/safe/truncated-type.py"
                    )["file"]
                    | {"truncatedByTokenCap": "true"},
                },
            ),
            (
                "Write",
                _current_write_result("/safe/unexpected-type.py")
                | {"type": "unexpected"},
            ),
            (
                "Write",
                _current_write_result("/safe/update-bad-patch.py", update=True)
                | {
                    "originalFile": None,
                    "structuredPatch": [{"oldStart": 1}],
                },
            ),
            (
                "Edit",
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
            "read-metadata-only",
            "read-token-cap-wrong-type",
            "write-unexpected-type",
            "write-update-null-original-bad-patch",
            "edit-bad-patch",
        ],
    )
    def test_near_miss_tool_shapes_remain_unknown(self, tool_name, structured):
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

    def test_write_shape_accepts_memdir_stamped_metadata(self):
        """Current successful Write results carry memdirStamped alongside
        the normal fields — rejecting it marks one ordinary write unknown
        and downgrades completeness-dependent metrics."""
        structured = _current_write_result("/safe/out.json") | {
            "memdirStamped": True
        }
        assert result_state(
            {"block": {"content": "ordinary result"}, "structured": structured},
            "Write",
            "write",
        )[0] == "success"

    def test_write_shape_rejects_non_boolean_memdir_stamped(self):
        structured = _current_write_result("/safe/out.json") | {
            "memdirStamped": "yes"
        }
        assert result_state(
            {"block": {"content": "ordinary result"}, "structured": structured},
            "Write",
            "write",
        )[0] == "unknown"

    @pytest.mark.parametrize(
        "tool_name,structured",
        [
            (
                "Grep",
                {
                    "mode": "content",
                    "filenames": ["src/a.py"],
                    "numFiles": 1,
                    "content": "src/a.py:1:match",
                    "numLines": 1,
                },
            ),
            (
                "Grep",
                {
                    "mode": "content",
                    "filenames": [],
                    "numFiles": 0,
                    "content": "",
                    "numLines": 0,
                    "appliedLimit": 100,
                },
            ),
            (
                "Grep",
                {"mode": "files_with_matches", "filenames": ["a.py"], "numFiles": 1},
            ),
            (
                "Grep",
                {
                    "mode": "count",
                    "filenames": ["a.py"],
                    "numFiles": 1,
                    "content": "a.py:2",
                    "numMatches": 2,
                },
            ),
            (
                "Glob",
                {
                    "durationMs": 5,
                    "filenames": [],
                    "numFiles": 0,
                    "truncated": False,
                },
            ),
        ],
        ids=[
            "grep-content",
            "grep-content-zero-matches",
            "grep-files-with-matches",
            "grep-count",
            "glob-empty",
        ],
    )
    def test_legacy_grep_and_glob_success_payloads_resolve_success(
        self, tool_name, structured
    ):
        """Older transcripts omit is_error on successful Grep/Glob calls —
        the structured payload is the success signal, and zero matches is
        still a successful call."""
        assert result_state(
            {"block": {"content": "ordinary result"}, "structured": structured},
            tool_name,
            tool_name.lower(),
        )[0] == "success"

    @pytest.mark.parametrize(
        "tool_name,structured",
        [
            (
                "Grep",
                {"mode": "regex", "filenames": [], "numFiles": 0},
            ),
            (
                "Glob",
                {
                    "durationMs": 5,
                    "filenames": [],
                    "numFiles": 0,
                    "truncated": "no",
                },
            ),
        ],
        ids=[
            "grep-unknown-mode",
            "glob-truncated-wrong-type",
        ],
    )
    def test_near_miss_grep_and_glob_shapes_remain_unknown(
        self, tool_name, structured
    ):
        assert result_state(
            {"block": {"content": "ordinary result"}, "structured": structured},
            tool_name,
            tool_name.lower(),
        )[0] == "unknown"

    def test_unknown_tool_cannot_reuse_read_success_shape(self, tmp_path):
        """A foreign tool never borrows Read's shape validator to claim a read.

        Shape validation exists so an EVIDENCE tool's payload can be mined;
        a tool this module mines nothing from must not acquire a read just
        by echoing Read's envelope — while still resolving, because a paired
        result with no error signal answered the only question asked of it.
        """
        (tmp_path / "safe").mkdir()
        (tmp_path / "safe" / "read.py").write_text("x = 1\n", encoding="utf-8")
        transcript = _write_jsonl(
            tmp_path / "unknown-tool-shape.jsonl",
            [
                _assistant(_call("only", "CustomTool", target="safe")),
                _result(
                    "only",
                    "ordinary result",
                    is_error=None,
                    structured=_current_read_result(
                        str(tmp_path / "safe" / "read.py")
                    ),
                ),
            ],
        )

        analysis = analyze_subagent(transcript, tmp_path, [])
        assert analysis["observed_reads"]["all"] == []
        assert analysis["unresolved_calls"] == 0
        assert analysis["tool_failures"] == []

    @pytest.mark.parametrize(
        "tool_name,tool_input,structured",
        [
            (
                "Write",
                {
                    "file_path": "/safe/write.py",
                    "content": (
                        "builder = ReviewOutputBuilder('safe')\n"
                        "builder.save_draft()"
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
    def test_validated_shapes_are_success_despite_prose_signatures(
        self, tmp_path, tool_name, tool_input, structured
    ):
        """A validated success-shaped payload beats the prose scan: result
        text embeds arbitrary content (matched source lines, file bodies,
        filenames), so an error string INSIDE that content must not turn a
        successful call into a recorded tool failure."""
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
        assert result["tool_failures"] == []
        assert result["unresolved_calls"] == 0

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
        ],
        ids=["read"],
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
            (False, {"interrupted": True, "success": True}),
            (False, {"status": "completed", "error": "structured error"}),
        ],
        ids=["error-flag-wins", "interrupted-wins", "error-field-wins"],
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
                            "builder.save_draft()"
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

    def test_extracts_successful_literal_repo_reads_and_classifies_scope(self, tmp_path):
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
        # A redirect's source and a pattern search read their file
        # operand; a pipeline's first stage is uncertain (its exit status
        # is masked), and globs, variables, tilde paths outside the repo,
        # traversal and failures still count nothing.
        assert observed == {
            "all": [
                "src/a.py",
                "src/c.py",
                "src/comment.py",
                "src/e.py",
                "src/f.py",
                "src/h.py",
                "src/j.py",
                "src/pid.py",
                "src/redir.py",
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
            "out_of_scope": [
                "src/j.py",
                "src/redir.py",
                "tests/b.py",
                "tests/d.py",
                "tests/g.py",
                "tests/i.py",
            ],
            "exhaustive": False,
        }

    @pytest.mark.parametrize("command, expected", [
        # A heredoc body is opaque up to its own terminator; a body line
        # naming a reader must not count (the over-count direction).
        ("python3 - <<'PY'\nimport json\nprint(1)\nsed -n 1p src/sneaky.py\nPY\ncat src/after.py", ["src/after.py"]),
        ("python3 - <<-EOF\n\tcat src/inner.py\n\tEOF\ncat src/outer.py", ["src/outer.py"]),
        # A here-string has no body.
        ("python3 -c 'x' <<< 'here'\ncat src/after.py", ["src/after.py"]),
        # A pipeline's exit status is its last stage's: without pipefail a
        # reader that failed before opening its file (`sed -n '[' a.php |
        # head` exits 0) is masked, so a piped reader is uncertain and not
        # counted. `|&` is a pipeline separator like `|`.
        ("cat src/a.py | head -5", []),
        ("cat src/a.py |& grep x", []),
        ("head -5 src/a.py", ["src/a.py"]),
        # `cd -` is unknown, like a variable.
        ("cd - && cat foo.py", []),
        # After an unknown directory only an absolute `cd` recovers; a
        # relative one is relative to the unknown.
        ("cd src && cd $VAR && cat lost.py\ncd src\ncat still-lost.py", []),
        ("cd src && cd .. && cat top.py", ["top.py"]),
        # `-f`/`--file` and `--expression=` supply the pattern, so every operand is a file.
        ("grep -f patterns.txt src/target.py", ["src/target.py"]),
        ("sed --expression=p src/foo.py", ["src/foo.py"]),
        # An input redirect's target is read.
        ("sed -n 1p < src/foo.py", ["src/foo.py"]),
        ("wc -l < src/foo.py > out.txt", ["src/foo.py"]),
        # The call's success certifies only the last foreground and-or list,
        # and only when nothing in it is `||`-conditional. A reader anywhere
        # else ran, perhaps, but may have failed before opening its file
        # (`sed -n '[' a.php; true` exits 0), so it is not counted; a `cd`
        # there still moves the detector, since its own success is checked
        # against the disk.
        ("false && cat src/never.py; true", []),
        ("test -f src/a.py && cat src/a.py", ["src/a.py"]),
        ("cat src/a.py || cat src/b.py", []),
        ("sed -n '[' src/a.py; true", []),
        ("cat src/a.py; true", []),
        # An `exit`, `exec` or `return` in an earlier list may have ended
        # the shell before the last list ran; exit 0 then proves nothing
        # about it. In the last list itself, `&& exit 0` follows the read.
        ("test -f src/optional.py || exit 0; cat src/app.py", []),
        ("exec true; cat src/a.py", []),
        ("return 0\ncat src/a.py", []),
        ("cat src/a.py && exit 0", ["src/a.py"]),
        # The same exit inside the last list: whatever follows it did not run.
        ("true && exit 0 && cat src/a.py", []),
        ("exec true && cat src/a.py", []),
        # A quoted operator is an argument, not a separator: `'&&'` here
        # is printf's operand and there is no `cat` command at all. Quoted
        # paths are still paths.
        ("printf '%s\\n' '&&' cat src/a.py", []),
        # A quoted argument may span lines: `cat src/a.py` inside it is
        # printf's text, and a reader after the closing quote is real.
        ("printf '%s\\n' 'line1\ncat src/a.py\nline3'", []),
        ("printf '%s\\n' 'line1\nline2'\ncat src/a.py", ["src/a.py"]),
        # A quote the call never closes leaves the rest of the command
        # unknown; nothing in it is counted.
        ("printf '%s\\n' 'open\ncat src/a.py", []),
        # `--` ends the options, not the pattern: the operand after it is
        # still the pattern, and only the one after that is a file.
        ("grep -e pat -- src/a.py", ["src/a.py"]),
        ("sed -n -- 1p src/a.py", ["src/a.py"]),
        ("echo 'a;b' ; cat src/a.py", ["src/a.py"]),
        ('cat "src/a b.py"', ["src/a b.py"]),
        # `exit 1` ends the shell too, but a call that did so did not
        # succeed, so a successful call's last list did run.
        ("cd src || exit 1; cat a.py", ["src/a.py"]),
        ("cd src && cat a.py; cat b.py", ["src/b.py"]),
        ("cd src &&\ncat a.py", ["src/a.py"]),
        ("false && cat src/bg.py &", []),
        # A backgrounded list runs in a subshell: its `cd` moves nothing in
        # the foreground, so the reader after it is at the root.
        ("cd src & cat a.py", ["a.py"]),
        # A `cd` that may not have run leaves the directory unknown.
        ("cd src || cd lib; cat a.py", []),
        # So does a `cd` to something that is not a directory: the shell
        # stayed put, and `foo.py` must not be counted at a made-up place.
        ("cd doesnotexist; cat foo.py", []),
        # ripgrep's `-r`/`--replace` takes a replacement; it is not grep's
        # recursion flag, and the pattern is never a file. `-r`/`--replace`
        # (space-separated) is one value-option branch; `--replace=repl` is
        # the separate inline `--flag=value` branch, which also proves `-n`
        # is skipped without supplying the pattern.
        ("rg -r repl pattern src/actual.py", ["src/actual.py"]),
        ("rg --replace=repl -n pattern src/actual.py", ["src/actual.py"]),
        # A directory operand (ripgrep walks it) is not a file read; `lib`
        # exists as a directory in the test repository.
        ("rg pattern lib", []),
        ("rg pattern lib/a.py", ["lib/a.py"]),
    ])
    def test_compound_edge_cases(self, tmp_path, command, expected):
        repo = tmp_path / "repo"
        (repo / "lib").mkdir(parents=True)
        (repo / "src").mkdir()
        (repo / "src" / "a.py").write_text("")
        entries = [_assistant(_call("c", "Bash", command=command)), _result("c")]
        transcript = _write_jsonl(tmp_path / "edge.jsonl", entries)
        observed = analyze_subagent(transcript, repo, [])["observed_reads"]
        assert observed["all"] == expected

    def test_a_backgrounded_cd_back_into_the_repository_moves_nothing(self, tmp_path):
        """After a `cd` outside the repository the foreground stays outside;
        a backgrounded `cd` back in is a subshell's, and the read that
        follows is not a repository read."""
        repo = tmp_path / "repo"
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "a.py").write_text("")
        command = f"cd {tmp_path}; cd {repo} & cat src/a.py"
        entries = [_assistant(_call("c", "Bash", command=command)), _result("c")]
        transcript = _write_jsonl(tmp_path / "bg.jsonl", entries)
        assert analyze_subagent(transcript, repo, [])["observed_reads"]["all"] == []

    def test_extracts_reads_from_the_harness_read_idioms(self, tmp_path):
        """Run 4's reconciliator read every source file through `sed -n`
        and `grep -n` inside `cd <repo> && …` compounds and measured zero
        repository reads, so step 9 called its verification UNVERIFIED and
        the report said it read no repository file. These are the idioms
        the harness itself hands agents in bypass-permissions sessions."""
        repo = tmp_path / "repo"
        (repo / "src").mkdir(parents=True)  # the idioms `cd src`
        calls_and_results = [
            (_call("sed", "Bash", command="sed -n '130,220p' src/k.py"), _result("sed")),
            (_call("cd-and", "Bash", command="cd src && grep -n foo l.py"), _result("cd-and")),
            (
                _call("cd-line", "Bash", command="cd src\nsed -n '1,5p' m.py | nl -ba"),
                _result("cd-line"),
            ),
            (_call("sed-i", "Bash", command="sed -i 's/a/b/' src/no.py"), _result("sed-i")),
            (_call("grep-r", "Bash", command="grep -rn foo src/"), _result("grep-r")),
            (
                _call("stderr", "Bash", command="cat -n src/n.py 2>&1 | head"),
                _result("stderr"),
            ),
            (
                _call(
                    "heredoc",
                    "Bash",
                    command="python3 - <<'PY'\nprint(open('src/hidden.py').read())\nPY\ncat src/after.py",
                ),
                _result("heredoc"),
            ),
            (_call("cd-var", "Bash", command="cd $OUT && cat rel.py"), _result("cd-var")),
            (
                _call("cd-abs", "Bash", command=f"cd {repo} && sed -n '3p' src/t.py"),
                _result("cd-abs"),
            ),
            (_call("semi", "Bash", command="cat src/o.py; cat src/p.py"), _result("semi")),
            (_call("grep-e", "Bash", command="grep -e foo src/q.py"), _result("grep-e")),
            (_call("grep-A", "Bash", command="grep -A3 foo src/r.py"), _result("grep-A")),
            (
                _call("show-pipe", "Bash", command="git show HEAD:src/e.py | cat -n | sed -n '1,120p'"),
                _result("show-pipe"),
            ),
            (_call("ls", "Bash", command="ls src/"), _result("ls")),
            (_call("failed", "Bash", command="sed -n '1p' src/failed.py"), _result("failed", is_error=True)),
        ]
        entries = []
        for call, result in calls_and_results:
            entries.extend([_assistant(call), result])
        transcript = _write_jsonl(tmp_path / "idioms.jsonl", entries)

        observed = analyze_subagent(transcript, repo, ["src/k.py", "src/l.py"])["observed_reads"]
        # `cd-line`, `stderr` and `show-pipe` pipe their reader into
        # another stage, which masks its exit status, and `semi` reads
        # `o.py` in a list whose status the call does not certify: uncounted.
        assert observed["all"] == [
            "src/after.py",
            "src/k.py",
            "src/l.py",
            "src/p.py",
            "src/q.py",
            "src/r.py",
            "src/t.py",
        ]
        assert observed["in_scope"] == ["src/k.py", "src/l.py"]
        assert observed["exhaustive"] is False


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


def test_manifest_step_timeline_rejects_foreign_run_event(tmp_path):
    manifest = _manifest("foreign-step", tmp_path, tmp_path / "run", started=[])
    manifest["run"]["id"] = "run-aaa"
    manifest["steps"] = [
        {
            "event": "step",
            "run_id": "run-zzz",
            "step": 2,
            "timestamp": (
                _TEST_TRANSCRIPT_START + timedelta(seconds=10)
            ).isoformat(),
        }
    ]

    transitions, complete = manifest_step_timeline(manifest)

    assert transitions == []
    assert complete is False


def test_manifest_step_timeline_rejects_identityless_event_for_identified_run(tmp_path):
    manifest = _manifest("missing-step-id", tmp_path, tmp_path / "run", started=[])
    manifest["run"]["id"] = "run-aaa"
    manifest["steps"] = [
        {
            "event": "step",
            "step": 2,
            "timestamp": (
                _TEST_TRANSCRIPT_START + timedelta(seconds=10)
            ).isoformat(),
        }
    ]

    transitions, complete = manifest_step_timeline(manifest)

    assert transitions == []
    assert complete is False


def test_manifest_step_timeline_accepts_matching_identified_run_events(tmp_path):
    manifest = _manifest("matching-step-id", tmp_path, tmp_path / "run", started=[])
    manifest["run"]["id"] = "run-aaa"
    manifest["steps"] = [
        {
            "event": "step",
            "run_id": "run-aaa",
            "step": step,
            "timestamp": (
                _TEST_TRANSCRIPT_START + timedelta(seconds=seconds)
            ).isoformat(),
        }
        for step, seconds in ((2, 10), (3, 20))
    ]

    transitions, complete = manifest_step_timeline(manifest)

    assert transitions == [
        (_TEST_TRANSCRIPT_START, "1"),
        (_TEST_TRANSCRIPT_START + timedelta(seconds=10), "2"),
        (_TEST_TRANSCRIPT_START + timedelta(seconds=20), "3"),
    ]
    assert complete is True


@pytest.mark.parametrize("identity_mode", ["none", "empty"])
def test_manifest_step_timeline_accepts_legacy_identityless_events(
    tmp_path, identity_mode
):
    manifest = _manifest("legacy-step", tmp_path, tmp_path / "run", started=[])
    identity = None if identity_mode == "none" else ""
    event = {
        "event": "step",
        "run_id": identity,
        "step": 2,
        "timestamp": (_TEST_TRANSCRIPT_START + timedelta(seconds=10)).isoformat(),
    }
    manifest["run"]["id"] = identity
    manifest["steps"] = [event]

    transitions, complete = manifest_step_timeline(manifest)

    assert transitions == [
        (_TEST_TRANSCRIPT_START, "1"),
        (_TEST_TRANSCRIPT_START + timedelta(seconds=10), "2"),
    ]
    assert complete is True


@pytest.mark.parametrize(
    "malformed_run_id", _MALFORMED_RUN_IDENTITIES, ids=_MALFORMED_RUN_ID_CASES
)
def test_manifest_step_timeline_rejects_malformed_manifest_identity(
    tmp_path, malformed_run_id
):
    manifest = _manifest("bad-manifest-id", tmp_path, tmp_path / "run", started=[])
    manifest["run"]["id"] = malformed_run_id
    manifest["steps"] = [
        {
            "event": "step",
            "run_id": malformed_run_id,
            "step": 2,
            "timestamp": (
                _TEST_TRANSCRIPT_START + timedelta(seconds=10)
            ).isoformat(),
        }
    ]

    transitions, complete = manifest_step_timeline(manifest)

    assert transitions == []
    assert complete is False


# The identity vocabulary belongs to the manifest-side test above: both call
# sites hand the same value to the same _normalize_run_identity
# (review_transcript.py:1663 and :1675). What is unique here is the per-event
# branch, which one malformed value reaches.
@pytest.mark.parametrize(
    "malformed_run_id", ["run unsafe"], ids=["embedded-space"]
)
def test_manifest_step_timeline_rejects_malformed_event_identity(
    tmp_path, malformed_run_id
):
    manifest = _manifest("bad-event-id", tmp_path, tmp_path / "run", started=[])
    manifest["steps"] = [
        {
            "event": "step",
            "run_id": malformed_run_id,
            "step": 2,
            "timestamp": (
                _TEST_TRANSCRIPT_START + timedelta(seconds=10)
            ).isoformat(),
        }
    ]

    transitions, complete = manifest_step_timeline(manifest)

    assert transitions == []
    assert complete is False


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


def test_duplicate_step_event_does_not_double_count_usage(tmp_path):
    """A resumed run that re-enters a pipeline step appends a second event
    for that same step number to the append-only telemetry log (e.g.
    `orchestration.py`'s step 9 re-observes on every re-entry). The
    stage-timeline reader must not crash on the repeat and must not
    double-count usage in that window: entries between the FIRST
    occurrence of the repeated step and the step that follows the LAST
    occurrence are attributed to that step exactly once each, never
    twice over because the transition fired twice.
    """
    sessions = tmp_path / "sessions"
    session = sessions / "dup-step.jsonl"
    run_dir = tmp_path / "run"
    manifest = _manifest("dup-step", tmp_path, run_dir, started=[])
    manifest["steps"] = [
        {
            "event": "step",
            "step": 9,
            "timestamp": (_TEST_TRANSCRIPT_START + timedelta(seconds=20)).isoformat(),
        },
        # The re-entry: same step number, a later timestamp, appended
        # rather than replacing the first — exactly what the append-only
        # log produces on a resumed run.
        {
            "event": "step",
            "step": 9,
            "timestamp": (_TEST_TRANSCRIPT_START + timedelta(seconds=30)).isoformat(),
        },
        {
            "event": "step",
            "step": 11,
            "timestamp": (_TEST_TRANSCRIPT_START + timedelta(seconds=40)).isoformat(),
        },
    ]
    _write_jsonl(
        session,
        [
            _at(_assistant(usage=_usage(1, 1)), 22),  # before the re-entry
            _at(_assistant(usage=_usage(2, 2)), 25),  # before the re-entry
            _at(_assistant(usage=_usage(4, 4)), 32),  # after the re-entry
        ],
    )

    result = enrich_run_transcript(manifest, sessions, set())

    assert result["warnings"] == []
    by_step = result["orchestrator_usage_by_step"]
    # 1 + 2 + 4 = 7 on each side — summed once, not once per transition.
    assert by_step["9"]["input_tokens"] == 7
    assert by_step["9"]["output_tokens"] == 7
    assert "11" not in by_step


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
            ("2026-07-20T11:00:00+00:00", "2026-07-20T10:00:00+00:00"),
        ],
        ids=["missing", "naive", "reversed"],
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

    def test_completed_run_window_is_inclusive(self, tmp_path):
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

    def test_open_ended_window_closes_at_next_human_prompt(self, tmp_path):
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        entries = [
            _at(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "review this"},
                },
                -1,
            ),
            _at(_assistant(usage=_usage(1, 2)), 0),
            _at(_assistant(usage=_usage(2, 3)), 60),
            _at(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "unrelated task"},
                },
                61,
            ),
            _at(_assistant(usage=_usage(100, 100)), 62),
        ]
        _write_jsonl(sessions / "open-ended-human.jsonl", entries)
        manifest = _manifest(
            "open-ended-human", tmp_path, output_dir, started=[]
        )
        manifest["run"]["ended_at"] = None

        result = enrich_run_transcript(manifest, sessions, set())

        assert result["usage"]["output_tokens"] == 5

    def test_open_ended_window_closes_when_first_post_start_record_is_human(
        self, tmp_path
    ):
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        entries = [
            _at(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "review this"},
                },
                -2,
            ),
            _at(_assistant(usage=_usage(1, 2)), -1),
            _at(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "unrelated task"},
                },
                1,
            ),
            _at(_assistant(usage=_usage(100, 100)), 2),
        ]
        _write_jsonl(sessions / "open-ended-buffered.jsonl", entries)
        manifest = _manifest(
            "open-ended-buffered", tmp_path, output_dir, started=[]
        )
        manifest["run"]["ended_at"] = None

        result = enrich_run_transcript(manifest, sessions, set())

        assert result["usage"]["output_tokens"] == 2

    def test_open_ended_window_ignores_synthetic_user_records(self, tmp_path):
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        entries = [
            _at(_assistant(usage=_usage(1, 2)), 0),
            _at(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": "<task-notification>agent done</task-notification>",
                    },
                },
                1,
            ),
            _at(_assistant(usage=_usage(2, 3)), 2),
        ]
        _write_jsonl(sessions / "open-ended-synthetic.jsonl", entries)
        manifest = _manifest(
            "open-ended-synthetic", tmp_path, output_dir, started=[]
        )
        manifest["run"]["ended_at"] = None

        result = enrich_run_transcript(manifest, sessions, set())

        assert result["usage"]["output_tokens"] == 5

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

    def test_task_notification_does_not_close_the_run_window(self, tmp_path):
        """A background-agent completion arriving between ended_at and the
        final response is harness-injected, not a human turn — the window
        must run through the presentation and close at the real prompt.

        The list-of-text-blocks shape is `_is_human_prompt`'s other content
        branch (a plain string is pinned by
        `test_open_ended_window_ignores_synthetic_user_records`)."""
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
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "  <task-notification>agent done"
                                    "</task-notification>"
                                ),
                            }
                        ],
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

    def test_synthetic_user_record_does_not_close_the_run_window(self, tmp_path):
        """An isMeta-flagged harness injection is not a human turn — one
        arriving between ended_at and the final presentation response must
        not close the window early. (The session_digest text-prefix shape
        is the same constant-tuple membership check
        `test_open_ended_window_ignores_synthetic_user_records` already
        pins with the task-notification prefix.)"""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        synthetic_record = {
            "type": "user",
            "isMeta": True,
            "message": {
                "role": "user",
                "content": "<system-reminder>\nnote\n</system-reminder>",
            },
        }
        entries = [
            _at(_assistant(usage=_usage(1, 2)), 50),
            _at(synthetic_record, 62),
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
        _write_jsonl(sessions / "synthetic-close.jsonl", entries)
        manifest = _manifest("synthetic-close", tmp_path, output_dir, started=[])
        manifest["run"]["ended_at"] = (
            _TEST_TRANSCRIPT_START + timedelta(seconds=60)
        ).isoformat()

        result = enrich_run_transcript(manifest, sessions, set())

        assert result["usage"]["output_tokens"] == 2 + 400

    def test_synthetic_user_record_does_not_reset_the_pending_turn(self, tmp_path):
        """A synthetic record between the triggering prompt and started_at
        must not start a fresh turn buffer — that would discard the opening
        turn's usage and tool calls from the run's evidence."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        synthetic_record = {
            "type": "user",
            "message": {
                "role": "user",
                "content": "<session_digest>compacted context</session_digest>",
            },
        }
        entries = [
            _at(
                {"type": "user", "message": {"role": "user", "content": "go"}},
                -5,
            ),
            _at(_assistant(usage=_usage(1, 2)), -3),
            _at(synthetic_record, -2),
            _at(_assistant(usage=_usage(2, 3)), 0),
        ]
        _write_jsonl(sessions / "synthetic-open.jsonl", entries)
        manifest = _manifest("synthetic-open", tmp_path, output_dir, started=[])

        result = enrich_run_transcript(manifest, sessions, set())

        assert result["usage"]["output_tokens"] == 2 + 3

    @pytest.mark.parametrize("damage_kind", ["time-gap", "parse-gap"])
    def test_superseded_turn_gaps_do_not_degrade_the_run(
        self, tmp_path, damage_kind
    ):
        """A damaged record in an older turn — an unparseable timestamp or a
        malformed JSON line — is discarded with that turn when a later
        prompt supersedes it; the bounded entries hold only current-run
        data, so availability must not go partial."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        older_prompt = _at(
            {
                "type": "user",
                "message": {"role": "user", "content": "earlier work"},
            },
            -10,
        )
        tail = [
            _at(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "review this"},
                },
                -1,
            ),
            _at(_assistant(usage=_usage(1, 2)), 0),
        ]
        session = sessions / f"superseded-{damage_kind}.jsonl"
        if damage_kind == "time-gap":
            # Older turn with a damaged (unparseable-timestamp) record.
            _write_jsonl(
                session,
                [
                    older_prompt,
                    {
                        "type": "assistant",
                        "message": {"role": "assistant"},
                        "timestamp": "not-a-time",
                    },
                    *tail,
                ],
            )
        else:
            # Older turn with a malformed JSON line.
            session.parent.mkdir(parents=True, exist_ok=True)
            session.write_bytes(
                json.dumps(older_prompt).encode()
                + b'\n{"damaged": \xff\n'
                + b"\n".join(json.dumps(e).encode() for e in tail)
                + b"\n"
            )
        manifest = _manifest(
            f"superseded-{damage_kind}", tmp_path, output_dir, started=[]
        )

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
                "repository_reads": None,
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
                , usage=_usage(1, 1)),
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
            pytest.param("reviewer", "parse-gap", id="reviewer-parse-gap"),
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
            main_entries.append(_assistant(call, usage=_usage(1, 1)))
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
                        ),
                        usage=_usage(1, 1),
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
        assert result["observed_reads"]["schema"] == 2
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
                    _assistant(call, usage=_usage(1, 1)),
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
                            ),
                            usage=_usage(1, 1),
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

    def test_subagent_resume_after_run_end_is_excluded(self, tmp_path):
        """A subagent resumed after the manifest's ended_at appends later
        turns to the same transcript file. The run window must bound that
        evidence — otherwise historical run metrics absorb post-run usage,
        reads, and failures and change over time."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        repo = tmp_path / "repo"
        repo.mkdir()
        session_id = "resumed-subagent"
        call = _call("reviewer", "Agent", prompt=_agent_prompt(output_dir))
        _write_jsonl(
            sessions / f"{session_id}.jsonl",
            [
                _assistant(call, usage=_usage(1, 1)),
                _result("reviewer", structured={"agentId": "reviewer-id"}),
            ],
        )
        resume_at = 2 * 3600  # one hour past ended_at
        _write_jsonl(
            sessions / session_id / "subagents" / "agent-reviewer-id.jsonl",
            [
                _at(
                    _assistant(
                        _call(
                            "in-run",
                            "Read",
                            file_path=str(repo / "src/in.py"),
                        ),
                        usage=_usage(10, 5),
                    ),
                    10,
                ),
                _at(_result("in-run"), 11),
                # Resume prompt: a genuine user turn past the window's end
                # closes it, exactly like the orchestrator transcript.
                _at(
                    {
                        "type": "user",
                        "message": {"role": "user", "content": "keep going"},
                    },
                    resume_at,
                ),
                _at(
                    _assistant(
                        _call(
                            "post-run",
                            "Read",
                            file_path=str(repo / "src/post-run.py"),
                        ),
                        usage=_usage(1000, 500),
                    ),
                    resume_at + 1,
                ),
                _at(
                    _result(
                        "post-run", "API Error: resumed failure", is_error=True
                    ),
                    resume_at + 2,
                ),
            ],
        )

        result = enrich_run_transcript(
            _manifest(session_id, repo, output_dir),
            sessions,
            {"security-reviewer"},
        )

        assert result["observed_reads"]["all"] == ["src/in.py"]
        agent_row = next(
            row
            for row in result["agent_usage"]
            if row["agent"] == "security-reviewer"
        )
        assert agent_row["usage"]["output_tokens"] == 5
        assert result["tool_failures"] == []
        assert result["correlation"]["complete"] is True

    def test_usage_less_agent_transcript_is_missing_evidence(self, tmp_path):
        """An agent transcript whose assistant records carry no usage
        payloads must degrade completeness, not report an exact zero-token
        complete run."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        repo = tmp_path / "repo"
        repo.mkdir()
        session_id = "usage-less-subagent"
        call = _call("reviewer", "Agent", prompt=_agent_prompt(output_dir))
        _write_jsonl(
            sessions / f"{session_id}.jsonl",
            [
                _assistant(call),
                _result("reviewer", structured={"agentId": "reviewer-id"}),
            ],
        )
        _write_jsonl(
            sessions / session_id / "subagents" / "agent-reviewer-id.jsonl",
            [
                _assistant(
                    _call("read", "Read", file_path=str(repo / "src/in.py"))
                ),
                _result("read"),
            ],
        )

        result = enrich_run_transcript(
            _manifest(session_id, repo, output_dir),
            sessions,
            {"security-reviewer"},
        )

        assert {
            "code": "agent_transcript_usage_missing",
            "agent": "security-reviewer",
        } in result["warnings"]
        assert result["agent_usage"][0]["available"] is True
        assert result["agent_usage"][0]["repository_reads"] is None
        assert result["completeness"]["agent_data"] is False
        assert result["completeness"]["scope_comparable_reads"] is False

    def test_usage_less_main_transcript_is_missing_evidence(self, tmp_path):
        """A settled run whose bounded main-session records carry no usage
        payloads has absent orchestrator evidence — reporting it complete
        would put exact zero-token totals into complete-cohort
        denominators."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        repo = tmp_path / "repo"
        repo.mkdir()
        session_id = "usage-less-main"
        _write_jsonl(
            sessions / f"{session_id}.jsonl",
            [
                _assistant(
                    _call(
                        "main-read",
                        "Read",
                        file_path=str(repo / "src/main.py"),
                    )
                ),
                _result("main-read"),
            ],
        )

        result = enrich_run_transcript(
            _manifest(session_id, repo, output_dir, started=[]),
            sessions,
            set(),
        )

        assert {
            "code": "orchestrator_transcript_usage_missing"
        } in result["warnings"]
        assert result["completeness"]["orchestrator_data"] is False
        assert result["completeness"]["usage"] is False

    def test_timestampless_agent_evidence_is_a_time_gap(self, tmp_path):
        """An assistant record without a usable timestamp cannot be bound to
        the run window — that is damaged evidence for the agent's family."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        repo = tmp_path / "repo"
        repo.mkdir()
        session_id = "timestampless-subagent"
        call = _call("reviewer", "Agent", prompt=_agent_prompt(output_dir))
        _write_jsonl(
            sessions / f"{session_id}.jsonl",
            [
                _assistant(call),
                _result("reviewer", structured={"agentId": "reviewer-id"}),
            ],
        )
        transcript = (
            sessions / session_id / "subagents" / "agent-reviewer-id.jsonl"
        )
        _write_jsonl(
            transcript,
            [
                _assistant(
                    _call("read", "Read", file_path=str(repo / "src/in.py")),
                    usage=_usage(10, 5),
                ),
                _result("read"),
            ],
        )
        stray = dict(
            _assistant(
                _call("late", "Read", file_path=str(repo / "src/late.py")),
                usage=_usage(1, 1),
            )
        )
        stray.pop("timestamp", None)
        with transcript.open("a") as stream:
            stream.write(json.dumps(stray) + "\n")

        result = enrich_run_transcript(
            _manifest(session_id, repo, output_dir),
            sessions,
            {"security-reviewer"},
        )

        assert {
            "code": "agent_transcript_time_gap",
            "agent": "security-reviewer",
        } in result["warnings"]
        assert result["agent_usage"][0]["available"] is True
        assert result["agent_usage"][0]["usage"]["output_tokens"] == 5
        assert result["agent_usage"][0]["repository_reads"] is None
        assert result["completeness"]["agent_data"] is False

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
                 usage=_usage(1, 1)),
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
            "schema": 2,
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

    def test_special_like_exact_identity_remains_a_regular_reviewer(
        self, tmp_path
    ):
        agent = "security-critic"
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
        manifest["assignment"]["assigned_files_by_agent"] = {agent: ["src/in.py"]}

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
        _write_jsonl(sessions / "session-invalid.jsonl", [_assistant(usage=_usage(1, 1))])

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
                            "builder.save_draft()"
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
        self, tmp_path
    ):
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        main = sessions / "session-gap.jsonl"
        _write_jsonl(
            main,
            [
                _assistant(_call("a1", "Agent", prompt=_agent_prompt(output_dir)), usage=_usage(1, 1)),
                _result("a1", structured={"agentId": "gap"}),
            ],
        )
        subagent = sessions / "session-gap" / "subagents" / "agent-gap.jsonl"
        _write_jsonl(subagent, [_assistant(usage=_usage(1, 1))])
        with subagent.open("a") as stream:
            stream.write('{"type": "truncated"\n')

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
        assert result["agent_usage"][0]["available"] is True
        assert result["agent_usage"][0]["repository_reads"] is None
        assert result["correlation"]["complete"] is False
        assert result["agent_data_complete"] is False
        assert result["usage_complete"] is False

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


def test_synthesis_call_without_result_is_an_expected_missing_dispatch(
    tmp_path,
):
    agent = "review-reconciliator"
    sessions = tmp_path / "sessions"
    output_dir = tmp_path / "run"
    _write_jsonl(
        sessions / "synthesis-missing.jsonl",
        [_assistant(_special_agent_call("synthesis", output_dir, agent), usage=_usage(1, 1))],
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


def test_malformed_synthesis_call_id_remains_expected_and_incomplete(
    tmp_path,
):
    # "missing" and non-string ids both fail the same isinstance(id, str)
    # check; the decision-reviewer agent proves the other special agent is
    # also a set member.
    agent = "decision-reviewer"
    secret = "PRIVATE_MALFORMED_ID_SENTINEL"
    sessions = tmp_path / "sessions"
    output_dir = tmp_path / "run"
    call = _special_agent_call("valid-placeholder", output_dir, agent)
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
        [_assistant(wrong_run, unknown_identity, unrelated, usage=_usage(1, 1))],
    )

    result = enrich_run_transcript(
        _manifest("malformed-unrelated", tmp_path, output_dir, started=[]),
        sessions,
        {"review-reconciliator", "decision-reviewer"},
    )

    # Malformed dispatch-shaped calls never create correlation expectations;
    # the malformed unrelated Read block, though, is unresolved orchestrator
    # evidence and degrades main completeness honestly.
    assert result["warnings"] == [
        {"code": "orchestrator_transcript_unresolved_calls"}
    ]
    assert result["correlation"]["expected"] == []
    assert result["correlation"]["expected_by_agent"] == {}
    assert result["correlation"]["expected_count"] == 0
    assert result["usage_complete"] is False


@pytest.mark.parametrize(
    "bad_input",
    [None, "not-an-object", ["src/a.py"], 7],
    ids=["missing", "string", "list", "int"],
)
def test_non_object_tool_input_counts_as_unresolved_evidence(
    tmp_path, bad_input
):
    """A tool_use with valid id/name but non-object input is a damaged
    record (0 of 14,889 surveyed real blocks deviate): substituting {}
    would let it pair and classify as success while its read path or
    builder command silently vanished from the evidence."""
    sessions = tmp_path / "sessions"
    output_dir = tmp_path / "run"
    broken_read = _call("read-1", "Read", file_path="src/a.py")
    if bad_input is None:
        broken_read.pop("input")
    else:
        broken_read["input"] = bad_input
    _write_jsonl(
        sessions / "broken-input.jsonl",
        [_assistant(broken_read, usage=_usage(1, 1)), _result("read-1")],
    )

    result = enrich_run_transcript(
        _manifest("broken-input", tmp_path, output_dir, started=[]),
        sessions,
        {"review-reconciliator", "decision-reviewer"},
    )

    assert result["warnings"] == [
        {"code": "orchestrator_transcript_unresolved_calls"}
    ]
    assert result["usage_complete"] is False


def test_non_object_dispatch_input_stays_with_correlation(tmp_path):
    """Dispatch anomalies belong to the correlation machinery — a damaged
    Agent block must not collapse per-family isolation into whole-run
    degradation."""
    sessions = tmp_path / "sessions"
    output_dir = tmp_path / "run"
    broken_dispatch = _call("d-1", "Agent", prompt="x")
    broken_dispatch["input"] = "not-an-object"
    _write_jsonl(
        sessions / "broken-dispatch-input.jsonl",
        [_assistant(broken_dispatch, usage=_usage(1, 1))],
    )

    result = enrich_run_transcript(
        _manifest("broken-dispatch-input", tmp_path, output_dir, started=[]),
        sessions,
        {"review-reconciliator", "decision-reviewer"},
    )

    assert result["warnings"] == []
    assert result["correlation"]["expected"] == []


def test_resolved_synthesis_call_is_complete_and_counted(tmp_path):
    agent = "review-reconciliator"
    sessions = tmp_path / "sessions"
    output_dir = tmp_path / "run"
    session_id = f"resolved-{agent}"
    agent_id = f"id-{agent}"
    _write_jsonl(
        sessions / f"{session_id}.jsonl",
        [
            _assistant(_special_agent_call("synthesis", output_dir, agent), usage=_usage(1, 1)),
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
        ("malformed-result", 1),
        ("duplicate-call-id", 2),
    ],
)
def test_unpairable_synthesis_results_remain_expected_but_uncorrelated(
    tmp_path, result_shape, expected_count
):
    sessions = tmp_path / "sessions"
    output_dir = tmp_path / "run"
    call = _special_agent_call("synthesis", output_dir, "review-reconciliator")
    if result_shape == "malformed-result":
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
    else:
        entries = [
            _assistant(
                call,
                _special_agent_call(
                    "synthesis", output_dir, "review-reconciliator"
                ),
            ),
            _result("synthesis", structured={"agentId": "duplicate"}),
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
             usage=_usage(1, 1)),
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
             usage=_usage(1, 1)),
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


class TestBudgetAndEvidenceCounts:
    """Round-7 accounting contracts: tool-call numerators, synthesis
    exclusion from builder metrics, and unresolved-call incompleteness."""

    def _run_with_subagent(
        self, tmp_path, subagent_entries, manifest_overrides=None
    ):
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        session_id = "accounting"
        _write_jsonl(
            sessions / f"{session_id}.jsonl",
            [
                _assistant(
                    _call("dispatch", "Agent", prompt=_agent_prompt(output_dir)),
                    usage=_usage(1, 1),
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
        for name, value in (manifest_overrides or {}).items():
            if name == "ended_at":
                manifest["run"]["ended_at"] = value
            else:
                manifest[name] = value
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
        assert entry["repository_reads"] == 1
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
        assert entry["available"] is True
        assert entry["usage"]["output_tokens"] == 2
        assert entry["repository_reads"] is None

    def test_malformed_tool_use_blocks_count_as_unresolved_calls(
        self, tmp_path
    ):
        """A tool_use block with a non-string id/name was an issued call
        that can never be paired — it must enter the budget numerator and
        flip the evidence families to partial, not vanish."""
        malformed_call = {
            "type": "tool_use",
            "id": None,
            "name": "Read",
            "input": {"file_path": "src/a.py"},
        }
        result = self._run_with_subagent(
            tmp_path,
            [
                _assistant(malformed_call, usage=_usage(1, 2)),
                _assistant(_call("ok", "Read", file_path="src/in.py")),
                _result("ok"),
            ],
        )

        assert {
            "code": "agent_transcript_unresolved_calls",
            "agent": "security-reviewer",
        } in result["warnings"]
        assert result["completeness"]["scope_comparable_reads"] is False
        [entry] = result["agent_usage"]
        assert entry["tool_calls"] == 2
        assert result["observed_reads"]["all"] == ["src/in.py"]

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

    def test_foreign_result_shape_on_an_unmined_tool_is_resolved(
        self, tmp_path
    ):
        """WebSearch/WebFetch/MCP results carry payload shapes this module
        does not model, and it mines no evidence from them. A PAIRED result
        with no error signal resolves the call: an unfamiliar shape is not
        missing evidence, and treating it as such fired this warning on
        every reviewer that ran a web search."""
        result = self._run_with_subagent(
            tmp_path,
            [
                _assistant(
                    _call("web", "WebSearch", query="wp nonce verification"),
                    usage=_usage(1, 2),
                ),
                _result(
                    "web",
                    "search results",
                    is_error=None,
                    structured={
                        "query": "wp nonce verification",
                        "results": [{"title": "t", "url": "https://example.test"}],
                        "durationSeconds": 3,
                    },
                ),
            ],
        )

        codes = {warning["code"] for warning in result["warnings"]}
        assert "agent_transcript_unresolved_calls" not in codes
        assert result["completeness"]["agent_data"] is True
        assert result["completeness"]["scope_comparable_reads"] is True
        assert result["tool_failures"] == []
        [entry] = result["agent_usage"]
        assert entry["tool_calls"] == 1

    def test_error_signal_beats_foreign_shape_on_an_unmined_tool(
        self, tmp_path
    ):
        """Resolving unfamiliar shapes must not swallow real failures: an
        explicit `is_error` on the same unmined tool is still a failure."""
        result = self._run_with_subagent(
            tmp_path,
            [
                _assistant(
                    _call("web", "WebFetch", url="https://example.test"),
                    usage=_usage(1, 2),
                ),
                _result(
                    "web",
                    "request failed",
                    is_error=True,
                    structured={"code": "ENOTFOUND"},
                ),
            ],
        )

        codes = {warning["code"] for warning in result["warnings"]}
        assert "agent_transcript_unresolved_calls" not in codes
        [failure] = result["tool_failures"]
        assert failure["tool"] == "Other"
        assert failure["category"] == "structured_failure"
        assert failure["detector"] == "structured"

    @pytest.mark.parametrize(
        "structured",
        [
            {"type": "file_unchanged", "file": {"filePath": "src/in.py"}},
            {"type": "image", "file": {"base64": "aWc=", "type": "image/png"}},
        ],
        ids=["file-unchanged", "image"],
    )
    def test_non_text_read_variants_are_successful_reads(
        self, tmp_path, structured
    ):
        """file_unchanged and image Read results are current non-error
        variants — they must count as observed reads with complete
        evidence, not as damaged unknowns."""
        result = self._run_with_subagent(
            tmp_path,
            [
                _assistant(
                    _call("read", "Read", file_path="src/in.py"),
                    usage=_usage(1, 2),
                ),
                _result("read", is_error=None, structured=structured),
            ],
        )

        assert result["warnings"] == []
        assert result["completeness"]["scope_comparable_reads"] is True
        assert result["observed_reads"]["in_scope"] == ["src/in.py"]

    def test_dangling_legacy_task_dispatch_stays_with_correlation(
        self, tmp_path
    ):
        """A legacy Task dispatch left without a result is the correlation
        machinery's evidence (missing agent, per-family) — it must not also
        degrade the whole main session as an unresolved call."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        _write_jsonl(
            sessions / "legacy-task.jsonl",
            [
                _assistant(
                    _call("t1", "Task", prompt=_agent_prompt(output_dir)),
                    usage=_usage(1, 2),
                ),
            ],
        )
        manifest = _manifest(
            "legacy-task", tmp_path, output_dir, started=["security-reviewer"]
        )

        result = enrich_run_transcript(manifest, sessions, {"security-reviewer"})

        codes = [warning["code"] for warning in result["warnings"]]
        assert "orchestrator_transcript_unresolved_calls" not in codes
        assert "expected_agent_uncorrelated" in codes

    def test_unresolved_orchestrator_call_marks_main_evidence_incomplete(
        self, tmp_path
    ):
        """A main-session call resolving to neither success nor failure is
        incomplete evidence — same contract as subagent calls."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        _write_jsonl(
            sessions / "dangling-main.jsonl",
            [
                _at(_assistant(usage=_usage(1, 2)), 0),
                _at(
                    _assistant(
                        _call("dangling", "Bash", command="git diff"),
                        usage=_usage(2, 3),
                    ),
                    10,
                ),
            ],
        )
        manifest = _manifest("dangling-main", tmp_path, output_dir, started=[])

        result = enrich_run_transcript(manifest, sessions, set())

        assert {
            "code": "orchestrator_transcript_unresolved_calls"
        } in result["warnings"]
        assert result["completeness"]["usage"] is False
        assert result["completeness"]["tool_failures"] is False

    def test_orchestrator_mcp_results_do_not_degrade_main_evidence(
        self, tmp_path
    ):
        """The orchestrator's own transcript is full of MCP and dispatch
        tools whose payload shapes this module does not model. The same
        classifier serves both halves, so a paired MCP result with no error
        signal resolves here too — otherwise the main session reported
        incomplete evidence on every run that touched an MCP server."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        _write_jsonl(
            sessions / "mcp-main.jsonl",
            [
                _at(_assistant(usage=_usage(1, 2)), 0),
                _at(
                    _assistant(
                        _call(
                            "mcp",
                            "mcp__linear-server__get_issue",
                            id="WOOPLUG-1",
                        ),
                        usage=_usage(2, 3),
                    ),
                    10,
                ),
                _at(
                    _result(
                        "mcp",
                        "issue payload",
                        is_error=None,
                        structured={"content": [{"type": "text", "text": "x"}]},
                    ),
                    11,
                ),
            ],
        )
        manifest = _manifest("mcp-main", tmp_path, output_dir, started=[])

        result = enrich_run_transcript(manifest, sessions, set())

        codes = [warning["code"] for warning in result["warnings"]]
        assert "orchestrator_transcript_unresolved_calls" not in codes
        assert result["completeness"]["orchestrator_data"] is True

    def test_scope_exempt_reviewer_reads_are_non_scope_comparable(
        self, tmp_path
    ):
        """tests-mutation-reviewer discovers its own scope — its reads must
        not be partitioned against the empty recorded mapping (which would
        report every legitimate read as out-of-scope), while it remains a
        regular reviewer for builder metrics."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        session_id = "mutation"
        _write_jsonl(
            sessions / f"{session_id}.jsonl",
            [
                _assistant(
                    _call(
                        "dispatch",
                        "Agent",
                        prompt=_agent_prompt(
                            output_dir, agent="tests-mutation-reviewer"
                        ),
                    )
                , usage=_usage(1, 1)),
                _result("dispatch", structured={"agentId": "mutation-agent"}),
            ],
        )
        _write_jsonl(
            sessions / session_id / "subagents" / "agent-mutation-agent.jsonl",
            [
                _assistant(
                    _call("read", "Read", file_path="tests/test_a.py"),
                    usage=_usage(1, 2),
                ),
                _result("read"),
            ],
        )
        manifest = _manifest(
            session_id, tmp_path, output_dir,
            started=["tests-mutation-reviewer"],
        )
        manifest["assignment"] = {"assigned_files_by_agent": {"tests-mutation-reviewer": []}}

        result = enrich_run_transcript(
            manifest, sessions, {"tests-mutation-reviewer"}
        )

        reads = result["observed_reads"]
        assert reads["non_scope_comparable"] == ["tests/test_a.py"]
        assert reads["out_of_scope"] == []
        assert result["completeness"]["scope_comparable_reads"] is True
        # Regular reviewer for builder metrics: its (non-builder) artifact
        # entry is present and counted.
        assert [
            item["agent"] for item in result["artifact_writes"]["by_agent"]
        ] == ["tests-mutation-reviewer"]

    def test_repo_reviewer_instance_dispatch_correlates_and_measures(
        self, tmp_path
    ):
        """A repo-contributed reviewer dispatches through the adapter
        template but acts under its repo-<id>-reviewer instance identity:
        the step-6 adapter command must correlate on --instance-name, the
        manifest's instance-named start must be recognized (not flip
        expected_invalid, which would zero every completeness family), and
        the instance measures as a scope-bearing regular reviewer."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        session_id = "adapter"
        _write_jsonl(
            sessions / f"{session_id}.jsonl",
            [
                _assistant(
                    _call(
                        "dispatch",
                        "Agent",
                        prompt=_adapter_prompt(output_dir),
                    )
                , usage=_usage(1, 1)),
                _result("dispatch", structured={"agentId": "adapter-agent"}),
            ],
        )
        _write_jsonl(
            sessions / session_id / "subagents" / "agent-adapter-agent.jsonl",
            [
                _assistant(
                    _call("read", "Read", file_path="src/in.py"),
                    usage=_usage(1, 2),
                ),
                _result("read"),
            ],
        )
        manifest = _manifest(
            session_id, tmp_path, output_dir,
            started=["repo-renewals-reviewer"],
        )
        manifest["assignment"] = {
            "assigned_files_by_agent": {"repo-renewals-reviewer": ["src/in.py"]}
        }

        result = enrich_run_transcript(
            manifest, sessions, {"repo-reviewer-adapter"}
        )

        assert result["correlation"]["expected"] == ["repo-renewals-reviewer"]
        [usage_entry] = result["agent_usage"]
        assert usage_entry["agent"] == "repo-renewals-reviewer"
        assert usage_entry["available"] is True
        assert result["observed_reads"]["in_scope"] == ["src/in.py"]
        assert result["completeness"]["scope_comparable_reads"] is True
        assert [
            item["agent"] for item in result["artifact_writes"]["by_agent"]
        ] == ["repo-renewals-reviewer"]

    def test_adapter_dispatch_without_instance_name_is_unrecognized(
        self, tmp_path
    ):
        """Bootstrap hard-fails ref-mode without --instance-name, so a
        ref-bearing command lacking one is malformed producer output — it
        must correlate to nothing, never to the shared template identity."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        _write_jsonl(
            sessions / "no-instance.jsonl",
            [
                _assistant(
                    _call(
                        "dispatch",
                        "Agent",
                        prompt=_adapter_prompt(output_dir, instance=None),
                    )
                ),
            ],
        )

        result = enrich_run_transcript(
            _manifest("no-instance", tmp_path, output_dir, started=[]),
            sessions,
            {"repo-reviewer-adapter"},
        )

        assert result["correlation"]["expected"] == []

    def test_non_shape_dynamic_agent_name_still_flips_invalid(self, tmp_path):
        """Instance recognition is by the exact producer shape — an
        arbitrary dynamic name in the manifest stays invalid evidence."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        _write_jsonl(sessions / "bad-name.jsonl", [_assistant()])

        result = enrich_run_transcript(
            _manifest(
                "bad-name", tmp_path, output_dir, started=["repo-renewals"]
            ),
            sessions,
            {"repo-reviewer-adapter"},
        )

        assert result["usage_complete"] is False
        assert result["completeness"]["scope_comparable_reads"] is False

    def test_damaged_scope_exempt_transcript_degrades_its_own_read_family(
        self, tmp_path
    ):
        """A scope-exempt reviewer's reads route to non_scope_comparable, so
        its damaged transcript must degrade THAT family — not report it
        complete while downgrading scope-comparable reads it never feeds.
        It stays a regular reviewer for builder metrics, so the artifact
        family degrades as before."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        session_id = "mutation"
        _write_jsonl(
            sessions / f"{session_id}.jsonl",
            [
                _assistant(
                    _call(
                        "dispatch",
                        "Agent",
                        prompt=_agent_prompt(
                            output_dir, agent="tests-mutation-reviewer"
                        ),
                    )
                , usage=_usage(1, 1)),
                _result("dispatch", structured={"agentId": "mutation-agent"}),
            ],
        )
        # Dangling Read call: tool_use with no paired result — unresolved
        # evidence for this agent.
        _write_jsonl(
            sessions / session_id / "subagents" / "agent-mutation-agent.jsonl",
            [
                _assistant(
                    _call("read", "Read", file_path="tests/test_a.py"),
                    usage=_usage(1, 2),
                ),
            ],
        )
        manifest = _manifest(
            session_id, tmp_path, output_dir,
            started=["tests-mutation-reviewer"],
        )
        manifest["assignment"] = {"assigned_files_by_agent": {"tests-mutation-reviewer": []}}

        result = enrich_run_transcript(
            manifest, sessions, {"tests-mutation-reviewer"}
        )

        completeness = result["completeness"]
        assert completeness["non_scope_comparable_reads"] is False
        assert completeness["scope_comparable_reads"] is True
        assert completeness["artifact_writes"] is False

    def test_running_manifest_caps_transcript_families_at_partial(
        self, tmp_path
    ):
        """An open-window running run keeps growing — observed usage is
        measured but no family may claim completeness until it settles."""
        result = self._run_with_subagent(
            tmp_path,
            [
                _assistant(
                    _call("read", "Read", file_path="src/in.py"),
                    usage=_usage(1, 2),
                ),
                _result("read"),
            ],
            manifest_overrides={"status": "running", "ended_at": None},
        )

        assert result["available"] is True
        assert result["usage"]["output_tokens"] > 0
        completeness = result["completeness"]
        assert all(value is False for value in completeness.values())

    def test_fractional_main_usage_downgrades_instead_of_truncating(
        self, tmp_path
    ):
        """A non-integral token count is corruption — excluded from totals
        and reported as a damaged record, never floored into an exact
        total that still claims completeness."""
        sessions = tmp_path / "sessions"
        output_dir = tmp_path / "run"
        entries = [
            _at(_assistant(usage=_usage(1, 2)), 0),
            _at(
                _assistant(
                    usage={
                        "input_tokens": 1,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "output_tokens": 1.9,
                    }
                ),
                10,
            ),
        ]
        _write_jsonl(sessions / "fractional.jsonl", entries)
        manifest = _manifest("fractional", tmp_path, output_dir, started=[])

        result = enrich_run_transcript(manifest, sessions, set())

        assert result["usage"]["output_tokens"] == 2
        assert {"code": "orchestrator_transcript_parse_gap"} in result["warnings"]
        assert result["completeness"]["usage"] is False

    def test_fractional_agent_usage_marks_a_damaged_record(self, tmp_path):
        result = self._run_with_subagent(
            tmp_path,
            [
                _assistant(
                    _call("read", "Read", file_path="src/in.py"),
                    usage={
                        "input_tokens": 2,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "output_tokens": 3.5,
                    },
                ),
                _result("read"),
            ],
        )

        assert {
            "code": "agent_transcript_parse_gap",
            "agent": "security-reviewer",
        } in result["warnings"]
        assert result["completeness"]["usage"] is False
        [entry] = result["agent_usage"]
        assert entry["usage"]["output_tokens"] == 0

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
                , usage=_usage(1, 1)),
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
        manifest["assignment"] = None

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
                , usage=_usage(1, 1)),
                _result("dispatch", structured={"agentId": "reviewer-agent"}),
                _assistant(
                    _special_agent_call("judge", output_dir, "critic")
                , usage=_usage(1, 1)),
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
                , usage=_usage(1, 1)),
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


class TestBootstrapCommandRecognition:
    """The canonical bootstrap-option allowlist must accept every command
    form step 6 actually emits — a rejected command leaves the dispatch
    uncorrelated and its usage, read, and builder metrics incomplete."""

    def test_step6_emitted_adapter_command_is_recognized(
        self, tmp_path, pipeline_mod
    ):
        """DRIFT GUARD: parse the adapter command pipeline step 6 actually
        generates, not a hand-written replica — a flag added to cmd_parts
        without an allowlist update must fail here, not in production
        correlation."""
        state = {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 2, 3, 5],
            "dispatched_agents": [{
                "name": "repo-renewals-reviewer",
                "adapter": "repo-reviewer-adapter",
                "ref": ".ai/agents/review/renewals.md",
                "label": "Renewals Expert",
                "channel": "blocking",
                "execution": "inline",
                "model": "opus",
                "scope_domains": ["code"],
            }],
        }
        guidance = pipeline_mod.get_step_guidance(
            6, "full", state, {"git": {"git_range": "abc..HEAD"}},
            output_dir=str(tmp_path),
        )
        command = next(
            line for line in guidance["actions"]
            if "bootstrap.py" in line and "--repo-agent-ref" in line
        )
        tokens = _mod._reviewer_bootstrap_tokens(command)
        assert tokens is not None
        assert tokens[tokens.index("--instance-name") + 1] == (
            "repo-renewals-reviewer"
        )


class TestScopeExemptRegistrySync:
    """_SCOPE_EXEMPT_REVIEWERS restates a registry fact (domain: null).

    Drift guard: a domainless reviewer added to agent_registry.json without
    updating the frozenset would have every read misclassified as
    out-of-scope against an empty scope mapping (the round-13 bug,
    re-created by registry growth). Synthesis identities route to the
    non-scope-comparable family instead and are excluded here.
    """

    def test_scope_exempt_matches_registry_domainless_reviewers(self):
        registry = json.loads(
            (PLUGIN_ROOT / "scripts" / "review" / "agent_registry.json")
            .read_text(encoding="utf-8")
        )
        # dispatch_class "special" domainless entries are excluded: they are
        # either synthesis identities (already in the non-scope-comparable
        # set) or dispatch templates like repo-reviewer-adapter, whose
        # per-instance executions run real domain scope discovery and appear
        # under instance names — scope-BEARING regular reviewers, never the
        # template identity itself.
        expected = {
            name
            for name, config in registry["agents"].items()
            if config.get("domain") is None
            and config.get("dispatch_class") != "special"
        }
        assert _mod._SCOPE_EXEMPT_REVIEWERS == expected


def _branched_tool_names(func, *variables: str) -> set[str]:
    """Tool names a function branches on, read from its own source.

    Parsed rather than restated: a hand-written replica of the branch set
    is exactly the drift this guard exists to catch. Recognizes every
    spelling the module actually reaches for — `name == "X"`, the
    reversed `"X" == name`, and `name in {...}` / `(...)` / `[...]` over
    string constants, which is the natural style here (`_operation`'s own
    `name in _SAFE_TOOL_NAMES` sits two functions away).
    """
    names: set[str] = set()
    source = textwrap.dedent(inspect.getsource(func))
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        operator = node.ops[0]
        left, right = node.left, node.comparators[0]
        if isinstance(operator, ast.Eq):
            # Either side may hold the variable; the other holds the name.
            for subject, value in ((left, right), (right, left)):
                if (
                    isinstance(subject, ast.Name)
                    and subject.id in variables
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    names.add(value.value)
        elif isinstance(operator, ast.In):
            if not isinstance(left, ast.Name) or left.id not in variables:
                continue
            if not isinstance(right, (ast.Set, ast.Tuple, ast.List)):
                continue
            names.update(
                element.value
                for element in right.elts
                if isinstance(element, ast.Constant)
                and isinstance(element.value, str)
            )
    return names


class TestEvidenceToolNameSync:
    """`_EVIDENCE_TOOL_NAMES` restates facts that live in two functions.

    It sits UPSTREAM of both: `_result_state` short-circuits on it before
    `_tool_shape_succeeded` ever runs, so adding a shape validator for a
    new tool without adding its name would silently disable the validator
    and start resolving that tool's results on pairing alone. The reverse
    direction matters too — a name left behind after its branch is gone
    keeps a tool out of the carve-out for no reason — so this is pinned as
    an EQUALITY, not a subset.
    """

    def _sources(self) -> tuple[set[str], set[str]]:
        validated = _branched_tool_names(_mod._tool_shape_succeeded, "tool_name")
        typed = _branched_tool_names(_mod._operation, "name")
        assert validated, "no tool-name branches found — parser drifted"
        assert typed, "no tool-name branches found — parser drifted"
        return validated, typed

    def test_evidence_tools_are_exactly_the_branched_tool_names(self):
        validated, typed = self._sources()

        assert validated | typed == _mod._EVIDENCE_TOOL_NAMES


def test_usage_summary_for_transcript_counts_a_split_response_once(tmp_path):
    path = tmp_path / "agent-1.jsonl"

    def entry(output, mid="m1"):
        return json.dumps({
            "type": "assistant",
            "message": {
                "id": mid,
                "model": "claude-sonnet-5",
                "usage": {
                    "input_tokens": 10,
                    "cache_creation_input_tokens": 5,
                    "cache_read_input_tokens": 100,
                    "output_tokens": output,
                },
            },
        })

    path.write_text("\n".join([entry(3), entry(9), entry(4, "m2"), "not json"]) + "\n")

    summary = _mod.usage_summary_for_transcript(path)

    assert summary["usage"] == {
        "input_tokens": 20,
        "cache_creation_input_tokens": 10,
        "cache_read_input_tokens": 200,
        "effective_input_tokens": 230,
        "output_tokens": 13,
    }
    assert summary["usage_by_model"] == {"claude-sonnet-5": summary["usage"]}
    assert summary["usage_valid"] is True
    assert summary["usage_observed"] is True
    assert summary["parse_gap"] is True
