"""Deferred-annotation drift guard for every module under scripts/.

Python 3.14 (PEP 649) evaluates annotations lazily, so an annotation
naming something the module never imports crashes only on the older
supported interpreters (3.10-3.13) — at import time, before the pipeline
can run — while a 3.14 test suite passes untouched. (This shipped once:
bootstrap.py annotated with an unimported ``Any`` and every reviewer
bootstrap on pre-3.14 died with NameError while all tests stayed green.)

Forcing every module-level annotation to evaluate makes the 3.14 suite
fail exactly where 3.10 would. On pre-3.14 interpreters the import alone
performs the check and the forced access is a no-op.

``__main__.py`` modules are excluded: importing one executes its CLI
path, and their behavior is covered functionally by their own tests.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _module_names() -> list[str]:
    names = []
    for path in sorted(SCRIPTS_DIR.rglob("*.py")):
        relative = path.relative_to(SCRIPTS_DIR)
        if relative.name == "__main__.py":
            continue
        if relative.name == "__init__.py":
            parts = relative.parent.parts
        else:
            parts = relative.with_suffix("").parts
        if parts:
            names.append(".".join(parts))
    return names


MODULES = _module_names()


def _evaluate_annotations(obj: object) -> None:
    """Force evaluation of an object's (possibly deferred) annotations."""
    inspect.get_annotations(obj)  # raises NameError on undefined names


@pytest.mark.parametrize("module_name", MODULES)
def test_module_annotations_evaluate(module_name):
    module = importlib.import_module(module_name)
    _evaluate_annotations(module)
    for obj in vars(module).values():
        if getattr(obj, "__module__", None) != module.__name__:
            continue
        if inspect.isfunction(obj):
            _evaluate_annotations(obj)
        elif inspect.isclass(obj):
            _evaluate_annotations(obj)
            for member in vars(obj).values():
                if isinstance(member, (staticmethod, classmethod)):
                    member = member.__func__
                if inspect.isfunction(member):
                    _evaluate_annotations(member)


def test_guard_covers_the_scripts_tree():
    """The walk must keep finding the real modules — an empty or shrunken
    parameterization would silently disable the guard."""
    assert "review.agent.bootstrap" in MODULES
    assert "analysis.review_metrics.measure" in MODULES
    assert len(MODULES) > 30
