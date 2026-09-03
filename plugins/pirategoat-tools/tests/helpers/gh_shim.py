"""Test doubles for the telemetry-sharing boundary.

``write_gh_shim`` emulates exactly the ``gh api`` protocol
``scripts/review/telemetry_share._run_gh`` speaks — the ``--hostname`` pin,
the raw ``--jq .login`` text, a ``--input -`` body on stdin, the Contents-API
GET/PUT shapes — and records every invocation. Both the unit suite and the
pipeline integration suite install it, so the emulation cannot drift between
them when that protocol changes.
"""

import json
from pathlib import Path


def write_gh_shim(
    bin_dir: Path,
    call_log: Path,
    *,
    login: str = "vlad",
    content_state: str = "missing",
    fail_code: int | None = None,
    fail_stderr: str | None = None,
    fail_jsonl_put: bool = False,
) -> Path:
    """Write an executable ``gh`` into ``bin_dir`` that logs each call to ``call_log``.

    Each log line is ``{"argv": [...], "body": <stdin or null>}``. ``login``
    is what ``api user --jq .login`` prints; ``content_state`` of
    ``"existing"`` makes Contents-API GETs return a blob SHA instead of 404;
    ``fail_code``/``fail_stderr`` make every call fail; ``fail_jsonl_put``
    fails only the PUT of a ``.jsonl`` path.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "gh"
    shim.write_text(
        "\n".join(
            (
                "#!/usr/bin/env python3",
                "import json",
                "import sys",
                f"CALL_LOG = {str(call_log)!r}",
                f"CONTENT_STATE = {content_state!r}",
                f"FAIL_CODE = {fail_code!r}",
                f"FAIL_STDERR = {fail_stderr!r}",
                f"FAIL_JSONL_PUT = {fail_jsonl_put!r}",
                "args = sys.argv[1:]",
                "# Read stdin only when gh itself would (--input -); otherwise the",
                "# shim must not block on an inherited stdin.",
                "body = None",
                "if '--input' in args and args[args.index('--input') + 1] == '-':",
                "    body = sys.stdin.read()",
                "with open(CALL_LOG, 'a', encoding='utf-8') as destination:",
                "    json.dump({'argv': args, 'body': body}, destination)",
                "    destination.write('\\n')",
                "if FAIL_CODE is not None:",
                "    if FAIL_STDERR is not None:",
                "        print(FAIL_STDERR, file=sys.stderr)",
                "    sys.exit(FAIL_CODE)",
                "if FAIL_JSONL_PUT and '-X' in args and any(a.endswith('.jsonl') for a in args):",
                "    raise SystemExit(1)",
                "# The host pin is asserted by tests from the log; route on the rest.",
                "if args[1:3] == ['--hostname', 'github.com']:",
                "    args = args[:1] + args[3:]",
                "if args[:2] == ['api', 'user']:",
                "    # `--jq .login` prints the raw login text, not JSON.",
                f"    print({login!r})",
                "    raise SystemExit(0)",
                "if args[:1] == ['api']:",
                "    if '-X' in args:",
                "        print('{}')",
                "        raise SystemExit(0)",
                "    if CONTENT_STATE == 'existing':",
                "        print(json.dumps({'sha': 'abc'}))",
                "        raise SystemExit(0)",
                "    print('gh: Not Found (HTTP 404)', file=sys.stderr)",
                "    raise SystemExit(1)",
                "raise SystemExit(2)",
            )
        ),
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return shim


def install_gh_shim(bin_dir: Path, call_log: Path, **shim_options) -> Path:
    """Write the shim and return the PATH entry that makes it the ``gh`` on PATH.

    The install convention — where the shim lives and where it logs — is
    shared alongside the protocol it emulates, so the two suites cannot
    drift on either. Callers prepend the returned directory to PATH.
    """
    write_gh_shim(bin_dir, call_log, **shim_options)
    return bin_dir


def gh_call_argv(call_log: Path) -> list[list[str]]:
    """Every recorded gh invocation's argv, dropping the stdin bodies."""
    return [argv for argv, _body in gh_requests(call_log)]


def gh_requests(call_log: Path) -> list[tuple[list[str], str | None]]:
    """Every recorded gh invocation as ``(argv, stdin body or None)``."""
    if not call_log.exists():
        return []
    return [
        (record["argv"], record["body"])
        for record in (
            json.loads(line)
            for line in call_log.read_text(encoding="utf-8").splitlines()
        )
    ]


def user_config_file(config_home: Path) -> Path:
    """The machine-local pirategoat config path under ``config_home``.

    One spelling of ``user_settings.user_config_path()``'s layout, so a
    relocation breaks the helper rather than a dozen literal paths.
    """
    return config_home / "pirategoat" / "config.json"


def write_user_config(config_home: Path, settings: dict) -> Path:
    """Write ``settings`` as the machine-local pirategoat config under ``config_home``."""
    config_path = user_config_file(config_home)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(settings), encoding="utf-8")
    return config_path
