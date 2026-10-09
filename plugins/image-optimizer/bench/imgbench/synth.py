"""Synthetic corpus images: illustrations and icons as seeded SVG (rendered by rsvg-convert), and UI screenshots
rendered from the HTML templates in bench/templates by headless Chrome. Seeds make every file reproducible."""

from __future__ import annotations

import random
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Collection

from . import manifest

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
MAC_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
LICENSE = "MIT (generated)"

ILLUSTRATION_SEEDS, ILLUSTRATION_SIZE = range(1, 13), (1600, 1000)
ICON_SEEDS, ICON_WIDTHS = range(1, 9), (64, 128, 256, 512)
SHOT_VIEWPORTS = ((1280, 1), (1440, 2))  # (width, device scale factor)
SHOT_TEMPLATES = ("admin-table", "checkout", "dashboard", "product-grid", "settings", "article")
SHOT_HEIGHT = 900

PALETTES = {
    False: [("#fdf6e3", "#f4d9b0"), ("#e8f1fb", "#b9d3f0"), ("#eef7ee", "#bfe0c4")],
    True: [("#0b1026", "#2a3b6e"), ("#120a1f", "#3b1f4e"), ("#06141a", "#1d4552")],
}


def illustration_svg(seed: int, width: int, height: int, dark: bool) -> str:
    rnd = random.Random(seed)
    top, bottom = rnd.choice(PALETTES[dark])
    shapes = []
    for _ in range(rnd.randint(4, 9)):
        cx, cy, r = rnd.randint(0, width), rnd.randint(height // 3, height), rnd.randint(height // 10, height // 3)
        colour = f"#{rnd.randint(0x202020, 0xe0e0e0):06x}"
        shapes.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{colour}" filter="url(#soft)" opacity="0.85"/>')
    hills = " ".join(f"L{x},{height - rnd.randint(height // 8, height // 3)}" for x in range(0, width + 1, width // 6))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<defs><linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{top}"/>'
            f'<stop offset="1" stop-color="{bottom}"/></linearGradient>'
            '<filter id="soft"><feGaussianBlur stdDeviation="6"/></filter>'
            '<radialGradient id="sun"><stop offset="0" stop-color="#fff6c8"/><stop offset="1" stop-color="#fff6c8" '
            'stop-opacity="0"/></radialGradient></defs>'
            f'<rect width="{width}" height="{height}" fill="url(#sky)"/>'
            f'<circle cx="{width * 0.75:.0f}" cy="{height * 0.25:.0f}" r="{height * 0.2:.0f}" fill="url(#sun)"/>'
            + "".join(shapes)
            + f'<path d="M0,{height} {hills} L{width},{height} Z" fill="{bottom}" opacity="0.9"/></svg>')


def icon_svg(seed: int) -> str:
    rnd = random.Random(seed)
    colour = f"#{rnd.randint(0x103050, 0xd04080):06x}"
    kind = rnd.choice(["circle", "rounded", "star"])
    body = {"circle": '<circle cx="50" cy="50" r="38"/>',
            "rounded": '<rect x="14" y="14" width="72" height="72" rx="18"/>',
            "star": '<path d="M50 8 61 38 94 38 67 57 77 90 50 70 23 90 33 57 6 38 39 38Z"/>'}[kind]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><g fill="{colour}">{body}</g>'
            f'<text x="50" y="62" font-family="Helvetica, Arial" font-size="30" text-anchor="middle" fill="#fff">'
            f'{chr(65 + seed % 26)}</text></svg>')


def render_svg(svg: str, dest: Path, width: int, rsvg: str) -> Path:
    src = dest.with_suffix(".svg")
    src.write_text(svg)
    try:
        subprocess.run([rsvg, "-w", str(width), "-o", str(dest), str(src)], check=True, capture_output=True)
    finally:
        src.unlink(missing_ok=True)
    return dest


def find_chrome() -> str | None:
    """The Chrome or Chromium the bench renders screenshots with, or None when there is none on this machine."""
    found = shutil.which("google-chrome") or shutil.which("chromium")
    return found or (str(MAC_CHROME) if MAC_CHROME.exists() else None)


def screenshot(template: Path, dest: Path, *, width: int, scale: int, dark: bool, chrome: str) -> Path:
    """One headless-Chrome screenshot of `template` (the first SHOT_HEIGHT CSS px), `width * scale` pixels wide.
    Chrome runs against a throwaway profile folder, so it never touches the user's profile or a running Chrome."""
    url = template.resolve().as_uri() + ("?dark=1" if dark else "")
    profile = tempfile.mkdtemp(prefix="imgbench-chrome-")
    try:
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                        "--no-default-browser-check", f"--user-data-dir={profile}", "--force-color-profile=srgb",
                        f"--force-device-scale-factor={scale}", f"--window-size={width},{SHOT_HEIGHT}",
                        f"--screenshot={dest}", url], check=True, capture_output=True, timeout=120)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    return dest


def _rsvg(tools: dict) -> str | None:
    tool = tools.get("rsvg-convert")
    return tool.path if tool is not None and tool.ok else None


CATEGORIES = ("illustration", "icon", "screenshot")


def build_all(corpus: Path, tools: dict, chrome: str | None,
              only: Collection[str] = CATEGORIES) -> tuple[list[manifest.Entry], list[str]]:
    """The `only` categories (all three by default) under `corpus`, one Entry per file, plus a note for every
    wanted category that had to be skipped."""
    entries: list[manifest.Entry] = []
    notes: list[str] = []

    def add(category: str, name: str, transform: str, make) -> None:
        dest = corpus / category / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        make(dest)
        entries.append(manifest.entry_for(dest, corpus, category=category, origin="synthetic", transform=transform,
                                          license=LICENSE))

    rsvg = _rsvg(tools)
    if rsvg is None and ("illustration" in only or "icon" in only):
        notes.append("illustration and icon categories skipped: rsvg-convert not found")
    if rsvg is not None and "illustration" in only:
        width, height = ILLUSTRATION_SIZE
        for dark in (False, True):
            for seed in ILLUSTRATION_SEEDS:
                tone = "dark" if dark else "light"
                add("illustration", f"illustration-{seed:02d}-{tone}.png",
                    f"illustration_svg(seed={seed}, {width}x{height}, {tone}) rendered by rsvg-convert",
                    lambda dest, s=seed, d=dark: render_svg(illustration_svg(s, width, height, d), dest, width, rsvg))
    if rsvg is not None and "icon" in only:
        for seed in ICON_SEEDS:
            for icon_width in ICON_WIDTHS:
                add("icon", f"icon-{seed:02d}-{icon_width}.png",
                    f"icon_svg(seed={seed}) rendered by rsvg-convert at {icon_width} px",
                    lambda dest, s=seed, w=icon_width: render_svg(icon_svg(s), dest, w, rsvg))
    if chrome is None and "screenshot" in only:
        notes.append("screenshot category skipped: Chrome not found")
    if chrome is not None and "screenshot" in only:
        for name in SHOT_TEMPLATES:
            for width, scale in SHOT_VIEWPORTS:
                for dark in (False, True):
                    tone = "dark" if dark else "light"
                    add("screenshot", f"{name}-{width}-x{scale}-{tone}.png",
                        f"screenshot(templates/{name}.html, width {width}, scale {scale}, {tone}) in headless Chrome",
                        lambda dest, n=name, w=width, sc=scale, d=dark: screenshot(
                            TEMPLATES / f"{n}.html", dest, width=w, scale=sc, dark=d, chrome=chrome))
    return entries, notes
