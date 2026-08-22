#!/usr/bin/env python3
"""Supported review-run and cohort metrics — CLI entry point.

The implementation lives in the `review_metrics` package next to this file;
see its docstring for the module layering. This path stays stable because
README.md, AGENTS.md, and the changelog document it as the supported interface.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from review_metrics.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
