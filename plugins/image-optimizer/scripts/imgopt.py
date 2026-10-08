#!/usr/bin/env python3
"""imgopt: measured image optimization for the image-optimizer plugin.

Run `python3 imgopt.py --help`. Use plain python3, not `python3 -I`:
Pillow may live in user site-packages.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import PIL  # noqa: F401
except ImportError:
    print("imgopt needs Pillow, which this python3 cannot import.\n"
          "Ask the human to run: python3 -m pip install --user pillow", file=sys.stderr)
    raise SystemExit(2)

from imgopt_lib.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
