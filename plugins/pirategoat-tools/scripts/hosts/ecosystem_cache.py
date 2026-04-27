"""Manage machine-wide ecosystem source cache.

Usage:
  python <plugin>/scripts/hosts/ecosystem_cache.py --update [--host NAME]
  python <plugin>/scripts/hosts/ecosystem_cache.py --list
  python <plugin>/scripts/hosts/ecosystem_cache.py --verify
"""

import argparse
import json
import sys
from pathlib import Path

# Absolute script execution puts scripts/hosts on sys.path. Add scripts/ so
# `from hosts...` imports resolve the same way they do under `python -m`.
SCRIPTS_DIR = str(Path(__file__).resolve().parents[1])
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from hosts.cache.manager import (
    KNOWN_ECOSYSTEM_REPOS, list_hosts, update_host, verify_hosts,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Manage ecosystem source cache.")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--update", action="store_true")
    g.add_argument("--list", action="store_true")
    g.add_argument("--verify", action="store_true")
    parser.add_argument("--host", help="Limit --update to a single host name.")
    args = parser.parse_args(argv)

    if args.update:
        known_names = {r.name for r in KNOWN_ECOSYSTEM_REPOS}
        if args.host and args.host not in known_names:
            print(json.dumps({
                "status": "error",
                "error": f"unknown ecosystem host: {args.host!r}. Known: {sorted(known_names)}",
            }, indent=2))
            return 2
        targets = ([args.host] if args.host
                   else [r.name for r in KNOWN_ECOSYSTEM_REPOS])
        results = [update_host(name) for name in targets]
        print(json.dumps({"action": "update", "results": results}, indent=2))
        return 0

    if args.list:
        print(json.dumps({"action": "list", "hosts": list_hosts()}, indent=2))
        return 0

    if args.verify:
        hosts = verify_hosts()
        print(json.dumps({"action": "verify", "hosts": hosts}, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
