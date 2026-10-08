import os
import subprocess
import sys
from pathlib import Path

import pytest

from imgopt_lib import tools as T

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "imgopt.py"


def fake(directory: Path, name: str, version_line: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/sh\necho '{version_line}' >&2\n")
    path.chmod(0o755)
    return path


def env(path_dirs=(), bundle=None, kegs=()):
    return T.Env(os.pathsep.join(str(d) for d in path_dirs), bundle, tuple(kegs), None)


def test_libjpeg_turbo_jpegtran_on_path_is_rejected(tmp_path):
    fake(tmp_path / "bin", "jpegtran", "libjpeg-turbo version 3.1.4.1")
    tool = T.resolve("jpegtran", env([tmp_path / "bin"]))
    assert not tool.ok
    assert "not mozjpeg" in tool.note


def test_bundle_mozjpeg_jpegtran_wins_over_path(tmp_path):
    fake(tmp_path / "bin", "jpegtran", "mozjpeg version 4.1.5")
    fake(tmp_path / "bundle", "jpegtran", "mozjpeg version 4.1.5 bundle")
    tool = T.resolve("jpegtran", env([tmp_path / "bin"], bundle=tmp_path / "bundle"))
    assert tool.source == "bundle"
    assert "bundle" in tool.version


def test_cjpeg_comes_from_the_mozjpeg_keg_not_libjpeg_turbo(tmp_path):
    fake(tmp_path / "bin", "cjpeg", "libjpeg-turbo version 3.1.4.1")
    fake(tmp_path / "keg", "cjpeg", "mozjpeg version 4.1.5")
    tool = T.resolve("cjpeg", env([tmp_path / "bin"], kegs=[tmp_path / "keg"]))
    assert tool.source == "keg"


def test_other_tools_prefer_path_then_bundle(tmp_path):
    fake(tmp_path / "bundle", "pngquant", "2.0")
    assert T.resolve("pngquant", env(bundle=tmp_path / "bundle")).source == "bundle"
    fake(tmp_path / "bin", "pngquant", "3.0.2")
    tool = T.resolve("pngquant", env([tmp_path / "bin"], bundle=tmp_path / "bundle"))
    assert (tool.source, tool.version) == ("path", "3.0.2")


def test_lossless_recompress_needs_no_metric_tools():
    req = T.requirements("recompress", "lossless", {"jpeg", "png"})
    assert "ffmpeg" not in req.all and "ssimulacra2" not in req.all
    assert set(req.quality) == {"jpegoptim", "jpegtran", "oxipng"}


def test_lossy_recompress_requires_metrics_and_blocks_on_encoders():
    req = T.requirements("recompress", "high", {"jpeg"})
    assert {"ffmpeg", "ssimulacra2"} <= set(req.required)
    assert {"guetzli", "cjpeg"} <= set(req.quality)
    assert set(req.optional) == {"butteraugli_main", "chrome"}


def test_formats_narrow_the_check():
    req = T.requirements("recompress", "lossless", {"png"})
    assert "gifsicle" not in req.all and "svgo" not in req.all


def test_svg_requires_the_renderer():
    assert "rsvg-convert" in T.requirements("recompress", "lossless", {"svg"}).required


@pytest.mark.parametrize("target,encoder", [("webp", "cwebp"), ("avif", "avifenc"), ("jpeg", "cjpeg")])
def test_target_encoder_is_required(target, encoder):
    assert encoder in T.requirements("convert", "high", {"png"}, target).required


def test_compare_job_requires_metrics_only():
    req = T.requirements("compare")
    assert req.required == ("pillow", "ffmpeg", "ssimulacra2")
    assert req.optional == ("butteraugli_main",)


def test_missing_quality_tool_blocks_until_waived(tmp_path):
    req = T.Requirements(("pillow",), ("guetzli",), ())
    assert T.check(req, env()).blocked
    chk = T.check(req, env(), allow_missing=["guetzli"])
    assert not chk.blocked
    assert chk.waived == ("guetzli",)


def test_required_tools_cannot_be_waived():
    req = T.Requirements(("pillow", "ffmpeg"), (), ())
    chk = T.check(req, env(), allow_missing=["ffmpeg"])
    assert chk.blocked
    assert chk.refused_waivers == ("ffmpeg",)


def test_report_names_what_each_missing_tool_adds_and_one_install_line():
    req = T.Requirements(("pillow",), ("guetzli", "pngquant"), ())
    text = T.report(T.check(req, env()), job="recompress", profile="high", platform="darwin")
    assert "perceptual JPEG encoder" in text
    assert "brew install guetzli pngquant" in text
    assert "BLOCKED" in text


def test_install_lines_on_linux_point_to_mozjpeg_builds():
    lines = T.install_lines(["pngquant", "cjpeg"], platform="linux")
    assert lines[0] == "sudo apt install pngquant"
    assert any("mozjpeg" in line for line in lines)


def test_ensure_raises_with_the_report():
    with pytest.raises(T.ToolingError, match="BLOCKED"):
        T.ensure("recompress", "high", {"jpeg"}, env=env())


def test_doctor_cli_reports_and_exits_by_readiness(factory):
    png = factory.logo()
    proc = subprocess.run([sys.executable, str(SCRIPT), "doctor", "--job", "recompress", str(png)],
                          capture_output=True, text=True)
    assert proc.returncode in (0, 2), proc.stderr
    assert "imgopt doctor: job=recompress profile=lossless" in proc.stdout
    assert ("Ready." in proc.stdout) == (proc.returncode == 0)
