"""Real sources for the corpus: chosen once by `select` (Wikimedia API, Kodak, GPL repositories), pinned in
bench/sources.json with license and checksum, downloaded by `fetch`."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageStat

API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "imgbench/1 (https://github.com/vladolaru/claude-code-plugins)"
ALLOWED_LICENSE_PREFIXES = ("CC0", "Public domain", "CC BY", "Kodak", "GPL")
KODAK = "https://r0k.us/graphics/kodak/kodak/kodim{:02d}.png"
KODAK_LICENSE = "Kodak Lossless True Color Image Suite: released for unrestricted use"

# What `select` asks Wikimedia for. photo-camera takes PER_CATEGORY files from each category; product-plain takes up
# to PLAIN_POOL candidates in title order and keeps the first PLAIN_COUNT whose background is plain.
PHOTO_CAMERA_CATEGORIES = (
    "Category:Featured pictures of landscapes",
    "Category:Featured pictures of actors",  # stands in for "of people": performers and public figures only
    "Category:Featured pictures of architecture",
    "Category:Featured pictures of food",
)
PRODUCT_PLAIN_CATEGORIES = (
    "Category:Featured pictures of objects",
    "Category:Quality images of objects",
    "Category:Quality images of coins and medals",  # added: the first two hold only 14 plain backgrounds
    "Category:Quality images of clocks",
)
PER_CATEGORY = 5
PLAIN_POOL = 400
PLAIN_COUNT = 15
RULES = {"photo-camera": {"min_width": 3000, "max_bytes": 30_000_000},
         "product-plain": {"min_width": 1500, "max_bytes": 15_000_000}}


@dataclass(frozen=True)
class Source:
    key: str
    category: str
    url: str
    license: str
    author: str
    sha1: str | None
    sha256: str | None


@dataclass(frozen=True)
class GplRepo:
    """A repository whose images join the corpus: the largest `raster` PNG/JPEG files and `svg` SVG files under
    `paths`, with the license read from `license_file` (a WordPress readme.txt) at the pinned commit."""
    repo: str
    paths: tuple[str, ...]
    license_file: str
    raster: int = 10
    svg: int = 3


GPL_REPOS = (
    GplRepo("woocommerce/woocommerce", ("plugins/woocommerce/assets/images",), "plugins/woocommerce/readme.txt"),
    GplRepo("WordPress/gutenberg", ("packages/block-library/src", "docs/assets"), "readme.txt"),
    GplRepo("Automattic/jetpack", ("projects/plugins/jetpack/images",), "projects/plugins/jetpack/readme.txt"),
)


def _api_get(params: dict) -> dict:
    query = urllib.parse.urlencode({**params, "format": "json"})
    req = urllib.request.Request(f"{API}?{query}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _text(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html or "").strip()


def select(categories: dict[str, list[str]], counts: dict[str, int], *, rules: dict, api=_api_get) -> list[Source]:
    """Files from each Wikimedia category, title order, keeping allowed licenses and the size rules, until the
    category's count is reached. Deterministic for a given state of Commons; the result is pinned."""
    chosen: list[Source] = []
    for category, titles in categories.items():
        rule = rules.get(category, {})
        got: list[Source] = []
        for title in titles:
            data = api({"action": "query", "generator": "categorymembers", "gcmtitle": title, "gcmtype": "file",
                        "gcmlimit": "200", "prop": "imageinfo", "iiprop": "url|size|sha1|mime|extmetadata"})
            pages = sorted(data.get("query", {}).get("pages", {}).values(), key=lambda p: p["title"])
            for page in pages:
                info = page["imageinfo"][0]
                lic = info["extmetadata"].get("LicenseShortName", {}).get("value", "")
                if not lic.startswith(ALLOWED_LICENSE_PREFIXES) or info.get("mime") != "image/jpeg":
                    continue
                if info["width"] < rule.get("min_width", 0) or info["size"] > rule.get("max_bytes", 1 << 40):
                    continue
                got.append(Source(page["title"].removeprefix("File:"), category, info["url"], lic,
                                  _text(info["extmetadata"].get("Artist", {}).get("value", "")), info["sha1"], None))
                if len(got) >= counts[category]:
                    break
            if len(got) >= counts[category]:
                break
        chosen += got
    return chosen


def camera_photos(api=_api_get) -> list[Source]:
    """PER_CATEGORY photos from each photo-camera category, one category at a time so each contributes its share."""
    found: list[Source] = []
    for title in PHOTO_CAMERA_CATEGORIES:
        found += select({"photo-camera": [title]}, {"photo-camera": PER_CATEGORY}, api=api, rules=RULES)
    return found


def product_candidates(api=_api_get) -> list[Source]:
    return select({"product-plain": list(PRODUCT_PLAIN_CATEGORIES)}, {"product-plain": PLAIN_POOL}, api=api,
                  rules=RULES)


def kodak() -> list[Source]:
    return [Source(f"kodim{i:02d}.png", "photo-small", KODAK.format(i), KODAK_LICENSE, "Eastman Kodak", None, None)
            for i in range(1, 25)]


def _gh(path: str) -> dict:
    done = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(f"gh api {path} failed: {done.stderr.strip()}")
    return json.loads(done.stdout)


def _spdx(readme: str) -> str:
    """The SPDX-like id for the `License:` header of a WordPress readme.txt ("GPLv2 or later", "GPLv3")."""
    header = re.search(r"^License:\s*(.+)$", readme, re.MULTILINE)
    gpl = header and re.match(r"GPL\s*v?(\d)(?:\.0)?(\s*or later|\s*\+)?", header.group(1).strip(), re.IGNORECASE)
    if not gpl:
        raise ValueError(f"no GPL License: header in the readme ({header.group(1) if header else 'none'})")
    return f"GPL-{gpl.group(1)}.0-" + ("or-later" if gpl.group(2) else "only")


def _files_under(gh, repo: str, sha: str, path: str) -> list[dict]:
    """Every blob below `path` at commit `sha`, as {"path": full path, "size": bytes}. The tree is walked one folder
    at a time because a recursive listing of a whole monorepo is truncated."""
    node = gh(f"repos/{repo}/git/trees/{sha}")
    parts = path.strip("/").split("/")
    for i, part in enumerate(parts):
        entry = next((e for e in node["tree"] if e["path"] == part and e["type"] == "tree"), None)
        if entry is None:
            raise LookupError(f"{repo}@{sha[:10]} has no folder {path}")
        node = gh(f"repos/{repo}/git/trees/{entry['sha']}" + ("?recursive=1" if i == len(parts) - 1 else ""))
    if node.get("truncated"):
        raise LookupError(f"{repo}: the listing of {path} is truncated")
    return [{"path": f"{path.strip('/')}/{e['path']}", "size": e["size"]} for e in node["tree"] if e["type"] == "blob"]


def gpl_assets(repos=GPL_REPOS, gh=_gh) -> list[Source]:
    """The largest raster and SVG images of each repository at its current HEAD, pinned to that commit's sha."""
    found: list[Source] = []
    for r in repos:
        sha = gh(f"repos/{r.repo}/commits/HEAD")["sha"]
        readme = base64.b64decode(gh(f"repos/{r.repo}/contents/{r.license_file}?ref={sha}")["content"]).decode()
        license_id = _spdx(readme)
        files = [f for path in r.paths for f in _files_under(gh, r.repo, sha, path)]

        def largest(suffixes: tuple[str, ...], n: int) -> list[dict]:
            return sorted((f for f in files if f["path"].lower().endswith(suffixes)),
                          key=lambda f: (-f["size"], f["path"]))[:n]

        name = r.repo.split("/")[1]
        for f in largest((".png", ".jpg", ".jpeg"), r.raster) + largest((".svg",), r.svg):
            found.append(Source(f"{name}__{f['path'].replace('/', '__')}", "gpl-asset",
                                f"https://raw.githubusercontent.com/{r.repo}/{sha}/{f['path']}", license_id,
                                f"{r.repo} contributors", None, None))
    return found


def plain_background(path: Path) -> bool:
    """True for a studio backdrop: the outer 4% border (image downscaled to 256 wide) is nearly uniform
    (per-channel standard deviation <= 12) and either light (mean luminance >= 200) or dark (<= 40)."""
    with Image.open(path) as im:
        im.draft("RGB", (512, 512))
        im = im.convert("RGB")
        im = im.resize((256, max(1, round(im.height * 256 / im.width))), Image.LANCZOS)
    w, h = im.size
    bx, by = max(1, round(w * 0.04)), max(1, round(h * 0.04))
    strips = [im.crop(box) for box in ((0, 0, w, by), (0, h - by, w, h), (0, by, bx, h - by), (w - bx, by, w, h - by))]
    stats = [ImageStat.Stat(s) for s in strips]
    count = sum(s.count[0] for s in stats)
    for band in range(3):
        total = sum(s.sum[band] for s in stats)
        square = sum(s.sum2[band] for s in stats)
        if (square / count - (total / count) ** 2) ** 0.5 > 12:
            return False
    red, green, blue = (sum(s.sum[band] for s in stats) / count for band in range(3))
    mean = 0.299 * red + 0.587 * green + 0.114 * blue
    return mean >= 200 or mean <= 40


def download(url: str, dest: Path, attempts: int = 6) -> None:
    """Fetch `url` into `dest`; on HTTP 429/503 wait as long as the server asks (default 10 s, at most 120 s)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as fh:
                while chunk := r.read(1 << 20):
                    fh.write(chunk)
            return
        except urllib.error.HTTPError as error:
            if error.code not in (429, 503) or attempt == attempts:
                raise
            wait = error.headers.get("Retry-After", "10")
            time.sleep(min(int(wait) if wait.isdigit() else 10, 120))


def pick_plain(candidates: list[Source], count: int, dest: Path, get=download, pause: float = 0.0,
               log=lambda message: None) -> list[Source]:
    """The first `count` candidates (in order) whose file has a plain background. A candidate is downloaded into
    dest/_probe/ (unless dest/<category>/<key> already holds it from an earlier run, which only kept files do);
    the ones that pass end up in dest/<category>/<key> where `fetch` finds them, the rest are deleted, so only the
    kept files stay on disk. `pause` seconds between downloads keep a public server happy."""
    probe = dest / "_probe"
    probe.mkdir(parents=True, exist_ok=True)
    kept: list[Source] = []
    for s in candidates:
        if len(kept) >= count:
            break
        target = dest / s.category / s.key
        tmp = target if target.is_file() else probe / s.key
        try:
            if tmp is not target:
                get(s.url, tmp)
                time.sleep(pause)
            ok = _matches(s, tmp) and plain_background(tmp)
        except OSError as error:  # includes PIL.UnidentifiedImageError; a candidate that cannot be probed is skipped
            log(f"skipped {s.key}: {error}")
            ok = False
        if ok:
            if tmp is not target:
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp.replace(target)
            kept.append(s)
        else:
            tmp.unlink(missing_ok=True)
    shutil.rmtree(probe, ignore_errors=True)
    return kept


def _digest(path: Path, algo: str) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def fetch(sources: list[Source], dest: Path, get=download) -> list[str]:
    """Download what is missing into dest/<category>/<key>; a checksum mismatch deletes the file."""
    problems = []
    for s in sources:
        target = dest / s.category / s.key
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and _matches(s, target):
            continue
        tmp = target.with_suffix(target.suffix + ".part")
        try:
            get(s.url, tmp)
        except OSError as error:
            problems.append(f"{s.key}: download failed ({error})")
            tmp.unlink(missing_ok=True)
            continue
        if not _matches(s, tmp):
            problems.append(f"{s.key}: checksum does not match sources.json")
            tmp.unlink(missing_ok=True)
            continue
        tmp.replace(target)
    return problems


def _matches(s: Source, path: Path) -> bool:
    if s.sha1:
        return _digest(path, "sha1") == s.sha1
    if s.sha256:
        return _digest(path, "sha256") == s.sha256
    return True  # Kodak and GPL entries get sha256 pinned on the first fetch (pin_unpinned)


def pin_unpinned(sources: list[Source], dest: Path) -> list[Source]:
    """Sources without a checksum get the sha256 of what was downloaded, so later fetches are verified."""
    return [s if (s.sha1 or s.sha256) else
            Source(**{**asdict(s), "sha256": _digest(dest / s.category / s.key, "sha256")}) for s in sources]


def save(sources: list[Source], path: Path) -> None:
    path.write_text(json.dumps([asdict(s) for s in sources], indent=1))


def load(path: Path) -> list[Source]:
    return [Source(**s) for s in json.loads(path.read_text())]
