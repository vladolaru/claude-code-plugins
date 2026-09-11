"""Shared reader for schemas/review-output.ts.

Several test files assert that the published TypeScript contract matches
the Python builders that actually produce artifacts (`test_output.py`,
`test_critic_adjustments.py`, `test_findings_ledger.py`). The text
extraction is the same in every case — one copy here, imported by all of
them, so a change to review-output.ts's formatting cannot silently stop
being checked in a copy nobody remembered to update.
"""

import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_SCHEMA_PATH = PLUGIN_ROOT / "schemas" / "review-output.ts"


def schema_text():
    return _SCHEMA_PATH.read_text()


def interface_body(name: str, *, extends: str = "") -> str:
    """The body of `export interface {name}[ extends {extends}] { ... }`."""
    schema = schema_text()
    suffix = f" extends {extends}" if extends else ""
    pattern = r"export interface " + name + suffix + r"\s*\{(.*?)\n\}"
    match = re.search(pattern, schema, re.DOTALL)
    assert match is not None, f"review-output.ts must declare {name}"
    return match.group(1)


def field_types(name: str, *, extends: str = "") -> dict:
    """Top-level `field: type;` / `field?: type;` pairs of one interface."""
    return dict(re.findall(
        r"^ {4}(\w+\??):\s*([^;]+);",
        interface_body(name, extends=extends), re.MULTILINE,
    ))


def type_alias(name: str) -> str:
    """The right-hand side of `[export] type {name} = ...;`, whitespace-collapsed."""
    schema = schema_text()
    match = re.search(
        rf"(?:export )?type {name} =\s*(.*?)(?=\n(?:export )?type |\n\n|\Z)",
        schema, re.DOTALL,
    )
    assert match is not None, f"review-output.ts must declare {name}"
    return " ".join(match.group(1).split())
