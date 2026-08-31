"""CLI entrypoint for host-context discovery.

Usage:
  python <plugin>/scripts/hosts/host_context.py --repo <repo-path> --output-dir <out-dir>

Writes host discovery output and embeds it in the canonical review context.
Also echoes JSON to stdout.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Absolute script execution puts scripts/hosts on sys.path. Add scripts/ so
# `from hosts...` imports resolve the same way they do under `python -m`.
SCRIPTS_DIR = str(Path(__file__).resolve().parents[1])
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from hosts.chain import ResolverChain
from review.run_paths import artifact_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Discover upstream host codebases for review.",
    )
    parser.add_argument("--repo", required=True, help="Path to the repo under review.")
    parser.add_argument("--output-dir", required=True,
                        help="Directory where host-context.json will be written.")
    args = parser.parse_args(argv)

    os.makedirs(args.output_dir, exist_ok=True)
    manifest = ResolverChain().run(args.repo)
    payload = manifest.to_dict()

    out_path = os.path.join(args.output_dir, "host-context.json")
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)

    review_context_path = artifact_path(args.output_dir, "review_context")
    review_context = {}
    if os.path.exists(review_context_path):
        try:
            with open(review_context_path) as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                review_context = loaded
        except (json.JSONDecodeError, OSError):
            review_context = {}
    review_context["host_context"] = payload
    with open(review_context_path, "w") as f:
        json.dump(review_context, f, indent=2)

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
