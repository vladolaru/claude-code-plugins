"""File-format vocabulary and input expansion shared by every imgopt command."""

from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
from pathlib import Path

FORMAT_BY_EXT = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".gif": "gif",
                 ".svg": "svg", ".webp": "webp", ".avif": "avif"}
EXT_BY_FORMAT = {"jpeg": ".jpg", "png": ".png", "gif": ".gif", "svg": ".svg",
                 "webp": ".webp", "avif": ".avif"}
INPUT_FORMATS = ("jpeg", "png", "gif", "svg")
OUTPUT_FORMATS = ("keep", "jpeg", "png", "webp", "avif")
VENDORED = frozenset({"node_modules", "vendor", "bower_components"})
# `candidates` puts this file in every --out folder; `clean` deletes only folders that carry it, and folder inputs
# skip them. It is the only sign of a working folder: names like run.json and metrics.json are common in projects.
# It lives here, not in workdirs, because workdirs imports ladder, which imports imaging, which imports this module.
WORKDIR_MARKER = ".imgopt-workdir"
WORKDIR_MARKER_TEXT = "Made by `imgopt candidates`: a working folder that `imgopt clean` may delete.\n"


def command_line(script: Path, *args) -> str:
    """How every imgopt command printed for the agent to run is spelt: python3, the script, each argument
    shell-quoted, so a path with spaces pastes as one word in bash or zsh."""
    return " ".join(["python3", *(shlex.quote(str(a)) for a in (script, *args))])


def format_of(path: Path) -> str | None:
    return FORMAT_BY_EXT.get(Path(path).suffix.lower())


def _git(folder: Path, *args: str, stdin: str = "") -> subprocess.CompletedProcess | None:
    """``git -C folder <args>``, or None when git cannot run."""
    try:
        return subprocess.run(["git", "-C", str(folder), *args], input=stdin, capture_output=True, text=True,
                              timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None


def repo_root(folder: Path) -> Path | None:
    """The git work tree holding ``folder``, resolved; None outside one or without git."""
    proc = _git(folder, "rev-parse", "--show-toplevel")
    return Path(proc.stdout.strip()).resolve() if proc and proc.returncode == 0 else None


def _check_ignore(top: Path, files: list[Path]) -> set[Path] | None:
    """The ``files`` that the repository at ``top`` ignores, or None when git refuses the batch."""
    # the folder resolved, not the file: a symlinked image may point outside the repository
    rel = {str((f.parent.resolve() / f.name).relative_to(top)): f for f in files}
    proc = _git(top, "check-ignore", "--stdin", "-z", stdin="\0".join(rel))
    if proc is None or proc.returncode not in (0, 1):
        return None
    return {rel[p] for p in proc.stdout.split("\0") if p in rel}


def _ignores_folder(top: Path, folder: Path) -> bool:
    """Whether the repository at ``top`` ignores ``folder`` (asked as a folder, so `name/` patterns match)."""
    rel = (folder.parent.resolve() / folder.name).relative_to(top).as_posix() + "/"
    proc = _git(top, "check-ignore", "-q", "--", rel)
    return proc is not None and proc.returncode == 0


def _ignored(folder: Path, files: list[Path], repos: list[Path], skipped: list[str]) -> set[Path]:
    """The files git ignores, each asked of the repository that holds it, by that repository's own .gitignore,
    as git itself treats a submodule or a nested repository: the nearest of the ``repos`` roots the walk passed,
    else the work tree holding ``folder``. A file in no repository is not filtered. A repository that git will
    not answer for is named in ``skipped``, so the filter is never off without a word."""
    if not files:
        return set()
    outer = repo_root(folder)
    nearest_first = sorted(repos, key=lambda r: len(r.parts), reverse=True)
    by_repo: dict[Path, list[Path]] = {}
    for f in files:
        inner = next((r for r in nearest_first if f.is_relative_to(r)), None)
        top = inner.resolve() if inner is not None else outer
        if top is not None:
            by_repo.setdefault(top, []).append(f)
    ignored: set[Path] = set()
    for top, members in by_repo.items():
        found = _check_ignore(top, members)
        if found is None:
            skipped.append(f"{top}: git check-ignore failed, so its {len(members)} file(s) were not checked "
                           "against .gitignore")
        else:
            ignored |= found
    return ignored


def _walk(folder: Path, skipped: list[str]) -> list[Path]:
    """The images under ``folder``. Hidden, vendored and imgopt working folders are pruned, not entered (a
    repository root holds .git and node_modules), and each vendored or working folder is named once. The roots
    of the repositories the walk passes (a .git folder, or a submodule's .git file) decide whose ignores apply,
    and a repository inside a folder its enclosing repository ignores (a clone under third_party/) is not entered,
    as git does not enter it."""
    found: list[Path] = []
    repos: list[Path] = []
    outer: list[Path | None] = []  # the work tree holding ``folder``, asked once and only when a nested repo needs it

    def enclosing(here: Path) -> Path | None:
        inner = next((r for r in sorted(repos, key=lambda r: len(r.parts), reverse=True) if here.is_relative_to(r)),
                     None)
        if inner is not None:
            return inner.resolve()
        if not outer:
            outer.append(repo_root(folder))
        return outer[0]

    for root, dirs, files in os.walk(folder):
        here = Path(root)
        if ".git" in dirs or ".git" in files:
            repos.append(here)
        walked = []
        for d in sorted(dirs):
            if d.startswith("."):
                continue
            if d in VENDORED:
                skipped.append(f"{here / d}/: vendored, not walked")
            elif (here / d / WORKDIR_MARKER).is_file():
                skipped.append(f"{here / d}/: an imgopt working folder, not walked")
            elif (here / d / ".git").exists() and (top := enclosing(here)) and _ignores_folder(top, here / d):
                skipped.append(f"{here / d}/: git-ignored, not walked")
            else:
                walked.append(d)
        dirs[:] = walked
        found += [here / n for n in files
                  if not n.startswith(".") and format_of(Path(n)) in INPUT_FORMATS and (here / n).is_file()]
    found.sort()
    ignored = _ignored(folder, found, repos, skipped)
    skipped += [f"{f}: git-ignored" for f in found if f in ignored]
    return [f for f in found if f not in ignored]


def expand_inputs(paths, skipped: list[str] | None = None) -> list[Path]:
    """Files as given (never skipped); folders walked recursively, leaving out hidden parts, vendored folders
    (``VENDORED``), imgopt working folders (``WORKDIR_MARKER``) and git-ignored files, each appended to ``skipped``
    with why."""
    skipped = [] if skipped is None else skipped
    found: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            found += _walk(p, skipped)
        elif p.is_file():
            if format_of(p) not in INPUT_FORMATS:
                raise ValueError(f"{p}: not a supported image (jpg, jpeg, png, gif, svg)")
            found.append(p)
        else:
            raise ValueError(f"{p}: does not exist")
    out: list[Path] = []
    seen: set[Path] = set()
    for f in found:
        r = f.resolve()
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def subdir_name(path: Path) -> str:
    """Readable, collision-free folder name for one input inside --out."""
    p = Path(path).resolve()
    digest = hashlib.sha1(str(p).encode()).hexdigest()[:8]
    # Drop the root anchor: near "/" it would make the name absolute and escape --out.
    return "__".join(p.relative_to(p.anchor).parts[-3:]) + "--" + digest
