"""Exact AST recognition for the canonical reviewer builder heredoc."""

from __future__ import annotations

import ast
import shlex
from dataclasses import dataclass
from typing import Any


BUILDER_ENV_REQUIRED = frozenset({
    "PIRATEGOAT_PLUGIN_ROOT",
    "PIRATEGOAT_OUTPUT_DIR",
    "PIRATEGOAT_REVIEWER_NAME",
    "PIRATEGOAT_PR_ID",
})
BUILDER_ENV_OPTIONAL = frozenset({
    "PIRATEGOAT_PLUGIN_VERSION",
    # Historical measurement only: recorded transcripts briefly carried
    # this assignment even though the live envelope no longer emits it.
    "PIRATEGOAT_REVIEW_BUDGET",
})
BUILDER_ENV_NAMES = frozenset(BUILDER_ENV_REQUIRED | BUILDER_ENV_OPTIONAL)


_NON_STRAIGHT_LINE_NODES = (
    ast.If,
    ast.IfExp,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.With,
    ast.AsyncWith,
    ast.Match,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.BoolOp,
) + ((ast.TryStar,) if hasattr(ast, "TryStar") else ())


@dataclass(frozen=True)
class CanonicalBuilderProgram:
    """One reachable, receiver-bound canonical builder invocation."""

    env: dict[str, str]
    receiver: str
    statements: tuple[ast.stmt, ...]
    open_statement_index: int
    final_save_statement_index: int

    def receiver_calls_through_save(self) -> tuple[ast.Call, ...]:
        calls: list[ast.Call] = []
        for statement in self.statements[
            self.open_statement_index + 1:
            self.final_save_statement_index + 1
        ]:
            if not (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Attribute)
                and isinstance(statement.value.func.value, ast.Name)
                and statement.value.func.value.id == self.receiver
            ):
                continue
            calls.append(statement.value)
        return tuple(calls)


def parse_builder_envelope(
    command: Any,
) -> tuple[dict[str, str], ast.Module] | None:
    if not isinstance(command, str):
        return None
    lines = command.splitlines()
    first_line = lines[0] if lines else ""
    try:
        tokens = shlex.split(first_line)
    except ValueError:
        return None
    if tokens[-2:] != ["python3", "<<PY"]:
        return None

    assignments = tokens[:-2]
    if not (
        len(BUILDER_ENV_REQUIRED)
        <= len(assignments)
        <= len(BUILDER_ENV_NAMES)
    ):
        return None
    env: dict[str, str] = {}
    for token in assignments:
        name, separator, value = token.partition("=")
        if separator != "=" or name in env:
            return None
        env[name] = value
    if not BUILDER_ENV_REQUIRED <= set(env) <= BUILDER_ENV_NAMES:
        return None

    end = next(
        (index for index, line in enumerate(lines[1:], 1)
         if line.strip() == "PY"),
        None,
    )
    if end is None or any(line.strip() for line in lines[end + 1:]):
        return None
    try:
        tree = ast.parse("\n".join(lines[1:end]))
    except SyntaxError:
        return None
    return env, tree


def _is_terminal_statement(statement: ast.stmt) -> bool:
    if isinstance(statement, (ast.Raise, ast.Return)):
        return True
    if not (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Call)
    ):
        return False
    function = statement.value.func
    if isinstance(function, ast.Name):
        return function.id in {"exit", "quit"}
    return (
        isinstance(function, ast.Attribute)
        and isinstance(function.value, ast.Name)
        and (function.value.id, function.attr)
        in {("sys", "exit"), ("os", "_exit")}
    )


def _is_builder_open_assignment(statement: ast.stmt) -> bool:
    if not (
        isinstance(statement, ast.Assign)
        and len(statement.targets) == 1
        and isinstance(statement.targets[0], ast.Name)
        and isinstance(statement.value, ast.Call)
    ):
        return False
    call = statement.value
    return (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "open"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "ReviewOutputBuilder"
        and len(call.args) == 3
        and not call.keywords
        and not any(isinstance(argument, ast.Starred) for argument in call.args)
    )


def _exact_receiver_save(
    statement: ast.stmt, receiver: str
) -> ast.Call | None:
    if not (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Call)
    ):
        return None
    call = statement.value
    if not (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "save_draft"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == receiver
        and not call.args
        and not call.keywords
    ):
        return None
    return call


def recognize_canonical_builder_program(
    command: Any,
) -> CanonicalBuilderProgram | None:
    """Recognize one exact reachable open–mutate–save invocation.

    This intentionally models only the generated straight-line contract. It
    stops at unconditional top-level termination and rejects receiver aliases,
    rebindings, multiple opens, nested saves, and saves on another receiver.
    """
    parsed = parse_builder_envelope(command)
    if parsed is None:
        return None
    env, tree = parsed

    reachable: list[ast.stmt] = []
    for statement in tree.body:
        reachable.append(statement)
        if _is_terminal_statement(statement):
            break
    if any(
        isinstance(node, _NON_STRAIGHT_LINE_NODES)
        for statement in reachable
        for node in ast.walk(statement)
    ):
        return None

    opens = [
        (index, statement)
        for index, statement in enumerate(reachable)
        if _is_builder_open_assignment(statement)
    ]
    if len(opens) != 1:
        return None
    open_index, open_statement = opens[0]
    receiver = open_statement.targets[0].id

    save_indexes: list[int] = []
    for index, statement in enumerate(reachable):
        save_nodes = [
            node
            for node in ast.walk(statement)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "save_draft"
        ]
        if not save_nodes:
            continue
        exact = _exact_receiver_save(statement, receiver)
        if exact is None or save_nodes != [exact] or index <= open_index:
            return None
        save_indexes.append(index)
    if not save_indexes:
        return None
    final_save_index = save_indexes[-1]

    for index, statement in enumerate(reachable[:final_save_index + 1]):
        if index == open_index:
            continue
        # The receiver may be used only as the direct subject of a top-level
        # method call. Assigning it, aliasing it, or passing it dynamically
        # makes object identity unknowable to static reconstruction.
        receiver_names = [
            node for node in ast.walk(statement)
            if isinstance(node, ast.Name) and node.id == receiver
        ]
        if not receiver_names:
            continue
        allowed = (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Attribute)
            and isinstance(statement.value.func.value, ast.Name)
            and statement.value.func.value.id == receiver
            and receiver_names == [statement.value.func.value]
        )
        if not allowed:
            return None

    return CanonicalBuilderProgram(
        env=env,
        receiver=receiver,
        statements=tuple(reachable),
        open_statement_index=open_index,
        final_save_statement_index=final_save_index,
    )
