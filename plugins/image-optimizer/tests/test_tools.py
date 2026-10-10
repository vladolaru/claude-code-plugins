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
    return T.Env(os.pathsep.join(str(d) for d in path_dirs), bundle, tuple(kegs))


def test_libjpeg_turbo_jpegtran_on_path_is_rejected(tmp_path):
    fake(tmp_path / "bin", "jpegtran", "libjpeg-turbo version 3.1.4.1")
    tool = T.resolve("jpegtran", env([tmp_path / "bin"]))
    assert not tool.ok
    assert "not mozjpeg" in tool.note


def test_mozjpeg_jpegtran_comes_from_the_keg_then_the_bundle_then_path(tmp_path):
    fake(tmp_path / "bin", "jpegtran", "mozjpeg version 4.1.5")
    fake(tmp_path / "bundle", "jpegtran", "mozjpeg version 4.1.5 bundle")
    tool = T.resolve("jpegtran", env([tmp_path / "bin"], bundle=tmp_path / "bundle"))
    assert tool.source == "bundle" and "bundle" in tool.version
    fake(tmp_path / "keg", "jpegtran", "mozjpeg version 4.1.6 keg")
    tool = T.resolve("jpegtran", env([tmp_path / "bin"], bundle=tmp_path / "bundle", kegs=[tmp_path / "keg"]))
    assert tool.source == "keg"


def test_jpegoptim_prefers_the_bundle_build_linked_to_mozjpeg(tmp_path):
    fake(tmp_path / "bin", "jpegoptim", "jpegoptim v1.5.6 (libjpeg-turbo)")
    fake(tmp_path / "bundle", "jpegoptim", "jpegoptim v1.4.4")
    assert T.resolve("jpegoptim", env([tmp_path / "bin"], bundle=tmp_path / "bundle")).source == "bundle"


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
    assert set(req.optional) == {"butteraugli_main"}


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


def test_jpeg_to_png_requires_the_png_ladder():
    req = T.requirements("convert", "high", {"jpeg"}, "png")
    assert "pngquant" in req.quality and "oxipng" in req.required
    assert not {"guetzli", "cjpeg", "jpegoptim"} & set(req.all)


def test_png_to_jpeg_requires_the_jpeg_encoders():
    req = T.requirements("convert", "high", {"png"}, "jpeg")
    assert {"cjpeg", "cjpegli"} <= set(req.required) and "guetzli" in req.quality
    assert not {"pngquant", "oxipng"} & set(req.all)


def test_jpeg_to_webp_requires_only_what_the_ladder_runs():
    req = T.requirements("convert", "high", {"jpeg"}, "webp")
    assert "cwebp" in req.required
    assert not {"guetzli", "cjpeg", "jpegoptim", "jpegtran"} & set(req.all)


def test_prepare_requires_the_pixel_encoders_not_the_lossless_ones():
    req = T.requirements("prepare", "high", {"jpeg"})
    assert {"cjpeg", "cjpegli"} <= set(req.required) and "guetzli" in req.quality
    assert "jpegoptim" not in req.all


@pytest.mark.parametrize("version_line", ["3.2.0", "unknown output"])
def test_svgo_older_than_4_is_rejected(tmp_path, version_line):
    fake(tmp_path / "bin", "svgo", version_line)
    tool = T.resolve("svgo", env([tmp_path / "bin"]))
    assert not tool.ok
    assert "svgo 4 or newer required" in tool.note


def test_svgo_4_is_accepted(tmp_path):
    fake(tmp_path / "bin", "svgo", "4.0.0")
    assert T.resolve("svgo", env([tmp_path / "bin"])).version == "4.0.0"


def test_the_bundled_oxipng_9_is_refused_and_blocks_until_oxipng_10_is_installed(tmp_path):
    fake(tmp_path / "bundle", "oxipng", "oxipng 9.0.0")
    req = T.Requirements(("pillow", "oxipng"), (), ())
    chk = T.check(req, env(bundle=tmp_path / "bundle"))
    assert list(chk.missing_required) == ["oxipng"]
    assert "oxipng 10 or newer required (oxipng 9.0.0)" in chk.tools["oxipng"].note
    assert "brew install oxipng" in T.report(chk, job="recompress", profile="lossless", platform="darwin")
    fake(tmp_path / "bin", "oxipng", "oxipng 10.2.1")
    tool = T.resolve("oxipng", env([tmp_path / "bin"], bundle=tmp_path / "bundle"))
    assert tool.ok and tool.source == "path" and tool.version == "oxipng 10.2.1"


def test_cache_id_is_the_version_when_the_binary_prints_one(tmp_path):
    exe = tmp_path / "oxipng"
    exe.write_text("")
    assert T.Tool("oxipng", str(exe), "oxipng 10.2.1", "path").cache_id == "oxipng 10.2.1"


@pytest.mark.parametrize("version", ["unknown", "unreadable (TimeoutExpired)", ""])
def test_cache_id_is_the_binary_hash_when_it_prints_no_version(tmp_path, version):
    """guetzli, ssimulacra2 and butteraugli_main print no version, so an upgrade must still change the key."""
    exe = tmp_path / "guetzli"
    exe.write_bytes(b"old build")
    before = T.Tool("guetzli", str(exe), version, "path").cache_id
    assert before.startswith("sha256:") and len(before) == len("sha256:") + 12
    os.utime(exe, (1, 1))
    assert T.Tool("guetzli", str(exe), version, "path").cache_id == before  # mtime alone changes nothing
    exe.write_bytes(b"a newer build")
    assert T.Tool("guetzli", str(exe), version, "path").cache_id != before


def test_label_names_the_version_or_the_build(tmp_path):
    exe = tmp_path / "ssimulacra2"
    exe.write_bytes(b"build")
    assert T.Tool("oxipng", str(exe), "oxipng 10.2.1", "path").label == "oxipng 10.2.1"
    unversioned = T.Tool("ssimulacra2", str(exe), "unknown", "path")
    assert unversioned.label == f"unknown build {unversioned.cache_id}"
    assert unversioned.cache_id in T.describe({"ssimulacra2": unversioned})


@pytest.mark.parametrize("job,target", [("recompress", "keep"), ("convert", "jpeg"), ("prepare", "keep")])
def test_jpegli_is_required_for_lossy_jpeg_and_cannot_be_waived(job, target):
    formats = {"png"} if job == "convert" else {"jpeg"}
    req = T.requirements(job, "medium", formats, target)
    assert "cjpegli" in req.required and "cjpegli" not in req.quality + req.optional
    chk = T.check(req, env(), allow_missing=["cjpegli"])
    assert chk.blocked and chk.refused_waivers == ("cjpegli",)
    text = T.report(chk, job=job, profile="medium", platform="darwin")
    assert "4.6-15 points" in text and "cjpegli on PATH" in text


def test_jpegli_is_not_asked_for_where_its_ladder_does_not_run():
    assert "cjpegli" not in T.requirements("recompress", "lossless", {"jpeg"}).all
    assert "cjpegli" not in T.requirements("recompress", "high", {"png"}).all
    [line] = T.install_lines(["cjpegli"], platform="darwin")
    assert "-DJPEGLI_ENABLE_OPENEXR=OFF" in line and "cjpegli on PATH" in line


def test_the_jpegli_build_line_makes_a_binary_that_loads_only_system_libraries():
    """-DBUILD_SHARED_LIBS=OFF alone still linked Homebrew's OpenEXR, giflib, libjpeg-turbo and libpng."""
    line = T.OTHER["cjpegli"]
    for flag in ("-DBUILD_SHARED_LIBS=OFF", "-DJPEGLI_ENABLE_OPENEXR=OFF", "-DJPEGLI_BUNDLE_LIBPNG=ON",
                 "-DCMAKE_DISABLE_FIND_PACKAGE_GIF=ON", "-DCMAKE_DISABLE_FIND_PACKAGE_JPEG=ON"):
        assert flag in line


def test_doctor_suggests_homebrew_for_tools_found_only_in_the_bundle(tmp_path):
    fake(tmp_path / "bundle", "pngquant", "3.0.2")
    fake(tmp_path / "bundle", "jpegoptim", "jpegoptim v1.4.4")
    req = T.Requirements(("pillow",), ("pngquant", "jpegoptim"), ())
    text = T.report(T.check(req, env(bundle=tmp_path / "bundle")), job="recompress", profile="lossless",
                    platform="darwin")
    assert "Older copies from the ImageOptim bundle" in text and "brew install pngquant" in text
    assert "jpegoptim" not in text.split("Older copies")[1].splitlines()[0]  # bundle-first by design
    fake(tmp_path / "bin", "pngquant", "3.0.3")
    text = T.report(T.check(req, env([tmp_path / "bin"], bundle=tmp_path / "bundle")), job="recompress",
                    profile="lossless", platform="darwin")
    assert "Older copies" not in text and "Ready." in text


def test_a_cjpegli_that_cannot_start_is_reported_missing(tmp_path):
    """A shared-library build aborts once its build folder is gone; doctor must not call it ok."""
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "cjpegli").write_text("#!/bin/sh\necho 'dyld: Library not loaded: libjpegli.dylib' >&2\nexit 134\n")
    (broken / "cjpegli").chmod(0o755)
    tool = T.resolve("cjpegli", env([broken]))
    assert not tool.ok and "does not run (exit 134: dyld: Library not loaded" in tool.note
    fake(tmp_path / "good", "cjpegli", "Usage: cjpegli INPUT OUTPUT [OPTIONS...]")
    assert T.resolve("cjpegli", env([tmp_path / "good"])).ok


def test_cache_id_of_a_binary_gone_since_resolve_does_not_raise(tmp_path):
    assert T.Tool("guetzli", str(tmp_path / "gone"), "unknown", "path").cache_id == "unknown:missing"
