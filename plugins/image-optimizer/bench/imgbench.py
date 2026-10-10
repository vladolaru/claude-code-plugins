#!/usr/bin/env python3
"""imgbench: build the image-optimizer benchmark corpus and run imgopt on it (dev tooling, not shipped behavior)."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts"))

from imgbench.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
