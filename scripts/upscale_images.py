#!/usr/bin/env python3
"""Deterministic batch upscaler wrapping the Upscayl CLI binary. Resumable, zero-token.

Usage:
  upscale_images.py --input-dir DIR [--output-dir DIR] [--topic-name NAME] [--model ultramix-balanced-4x]
                    [--scale 4] [--jpeg|--png] [--quality 100] [--suffix _4x]
                    [--recursive] [--limit N] [--overwrite] [--dry-run]

Uses the binary bundled with the Upscayl desktop app (override with UPSCAYL_BIN /
UPSCAYL_MODELS env vars). Outputs are validated by magic header and size; finished
files are skipped on rerun, so the same command can be repeated to resume.
By default, the script upscales with Ultramix Balanced 4x at 4x and converts the
upscaled PNG master to an Adobe Stock-compatible JPEG with MozJPEG-compatible
`cjpeg` at quality 100; the PNG master is deleted unless `--keep-png`.
Per-file status is recorded in `upscale_manifest.json` inside the output directory.
Exit code: 0 = all present, 2 = at least one failure.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
from fnmatch import fnmatch
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_BIN = "/Applications/Upscayl.app/Contents/Resources/bin/upscayl-bin"
DEFAULT_MODELS = "/Applications/Upscayl.app/Contents/Resources/models"
DEFAULT_DOWNLOADS_ROOT = (Path.home() / "Downloads")
DEFAULT_UPSCALE_MODEL = "ultramix-balanced-4x"
SRGB_PROFILE = "/System/Library/ColorSync/Profiles/sRGB Profile.icc"
INPUT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
GENERIC_INPUT_DIR_NAMES = {"artifacts", "generated_images", "images", "selected", "selected_32", "seeds", "variants", "upscaled"}
MIN_VALID_BYTES = 1000
ADOBE_MIN_MEGAPIXELS = 4.0
MOZJPEG_CANDIDATES = (
    "/opt/homebrew/opt/mozjpeg/bin/cjpeg",
    "/usr/local/opt/mozjpeg/bin/cjpeg",
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch-upscale images with the Upscayl CLI.")
    parser.add_argument("--input-dir", required=True, help="Directory with source images.")
    parser.add_argument("--output-dir", help="Target directory. Default: $HOME/Downloads/<topic>_upscale.")
    parser.add_argument("--topic-name", help="Topic label used for the default Downloads output directory when --output-dir is omitted.")
    parser.add_argument("--model", default=DEFAULT_UPSCALE_MODEL, help="Model name from the Upscayl models dir.")
    parser.add_argument("--scale", type=int, default=4, choices=[2, 3, 4], help="Upscale factor.")
    parser.add_argument("--suffix", default=None, help="Suffix appended to output file names. Default: _<scale>x.")
    parser.add_argument("--jpeg", dest="jpeg", action="store_true", help="Encode the result as JPEG via MozJPEG-compatible cjpeg (default).")
    parser.add_argument("--png", dest="jpeg", action="store_false", help="Keep the final output as PNG instead of JPEG.")
    parser.set_defaults(jpeg=True)
    parser.add_argument("--quality", type=int, default=100, help="JPEG quality 1-100 for --jpeg.")
    parser.add_argument("--keep-png", action="store_true", help="Keep the PNG master next to the JPEG.")
    parser.add_argument("--exclude", action="append", default=[], help="Glob pattern (fnmatch) for file names to skip; repeatable.")
    parser.add_argument("--recursive", action="store_true", help="Scan the input directory recursively.")
    parser.add_argument("--limit", type=int, default=0, help="Process at most N images (smoke runs).")
    parser.add_argument("--overwrite", action="store_true", help="Re-upscale even if a valid output exists.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned commands without running them.")
    parser.add_argument("--upscayl-bin", default=os.environ.get("UPSCAYL_BIN", DEFAULT_BIN))
    parser.add_argument("--models-dir", default=os.environ.get("UPSCAYL_MODELS", DEFAULT_MODELS))
    parser.add_argument("--mozjpeg-bin", default=os.environ.get("MOZJPEG_BIN"), help="Path to MozJPEG-compatible cjpeg. Default: Homebrew mozjpeg path, then PATH cjpeg.")
    args = parser.parse_args()
    if args.suffix is None:
        args.suffix = f"_{args.scale}x"
    args.quality = max(1, min(args.quality, 100))

    input_dir = Path(args.input_dir).expanduser().resolve()
    if not input_dir.is_dir():
        raise SystemExit(f"Input directory not found: {input_dir}")
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else default_output_dir_for_input(input_dir, args.topic_name)
    output_dir.mkdir(parents=True, exist_ok=True)

    upscayl_bin = Path(args.upscayl_bin).expanduser()
    models_dir = Path(args.models_dir).expanduser()
    mozjpeg_bin = find_mozjpeg_bin(args.mozjpeg_bin)
    if not args.dry_run:
        if not upscayl_bin.is_file():
            raise SystemExit(
                f"upscayl-bin not found: {upscayl_bin}\n"
                "Install the Upscayl desktop app or set UPSCAYL_BIN."
            )
        if not (models_dir / f"{args.model}.bin").is_file():
            available = sorted(p.stem for p in models_dir.glob("*.bin"))
            raise SystemExit(f"Model '{args.model}' not found in {models_dir}. Available: {available}")
        if args.jpeg and mozjpeg_bin is None:
            raise SystemExit(
                "MozJPEG-compatible `cjpeg` not found. Install with `brew install mozjpeg` "
                "or pass --mozjpeg-bin /path/to/cjpeg."
            )

    sources = collect_inputs(input_dir, output_dir, args.recursive, args.exclude)
    if args.limit > 0:
        sources = sources[: args.limit]
    if not sources:
        print(f"No input images found in {input_dir}")
        return

    manifest_path = output_dir / "upscale_manifest.json"
    manifest = load_manifest(manifest_path)
    manifest["settings"] = {
        "model": args.model,
        "scale": args.scale,
        "jpeg": args.jpeg,
        "quality": args.quality,
        "jpegEncoder": "mozjpeg-compatible-cjpeg" if args.jpeg else None,
        "mozjpegBin": str(mozjpeg_bin) if mozjpeg_bin else None,
        "inputDir": str(input_dir),
        "outputDir": str(output_dir),
        "upscaylBin": str(upscayl_bin),
    }

    ok = skipped = failed = 0
    for src in sources:
        final_ext = ".jpg" if args.jpeg else ".png"
        stem = src.stem + args.suffix
        final_path = output_dir / (stem + final_ext)
        png_path = output_dir / (stem + ".png")

        if is_valid_image(final_path) and not args.overwrite:
            skipped += 1
            record(manifest, manifest_path, src, "skip", final_path, 0.0)
            continue

        cmd = [
            str(upscayl_bin),
            "-i", str(src),
            "-o", str(png_path),
            "-n", args.model,
            "-s", str(args.scale),
            "-m", str(models_dir),
            "-f", "png",
        ]
        if args.dry_run:
            print(" ".join(cmd))
            if args.jpeg:
                print(f"mozjpeg: {mozjpeg_bin or '<missing cjpeg>'} -quality {args.quality} -optimize -> {final_path}")
            continue

        t0 = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0 or not is_valid_image(png_path):
            failed += 1
            record(manifest, manifest_path, src, "fail", final_path, time.time() - t0,
                   error=(result.stderr or result.stdout)[-2000:])
            print(f"  {src.name}: FAIL (see upscale_manifest.json)", flush=True)
            continue

        if args.jpeg:
            converted, conversion = convert_to_jpeg(png_path, final_path, args.quality, mozjpeg_bin)
            if not converted:
                failed += 1
                record(manifest, manifest_path, src, "fail", final_path, time.time() - t0,
                       error=conversion.get("error", "MozJPEG conversion failed"),
                       jpegEncoder=conversion.get("encoder"),
                       jpegMethod=conversion.get("method"))
                print(f"  {src.name}: FAIL (JPEG conversion)", flush=True)
                continue
            if not args.keep_png:
                png_path.unlink(missing_ok=True)
        else:
            conversion = {}

        seconds = time.time() - t0
        mp = megapixels(final_path if not args.jpeg else png_path if args.keep_png else None) or megapixels_from_source(src, args.scale)
        warn = f" [WARN <{ADOBE_MIN_MEGAPIXELS}MP]" if mp and mp < ADOBE_MIN_MEGAPIXELS else ""
        ok += 1
        record(manifest, manifest_path, src, "ok", final_path, seconds,
               megapixels=mp,
               outputBytes=final_path.stat().st_size if final_path.exists() else None,
               jpegQuality=args.quality if args.jpeg else None,
               jpegEncoder=conversion.get("encoder") if args.jpeg else None,
               jpegMethod=conversion.get("method") if args.jpeg else None)
        print(f"  {src.name} -> {final_path.name} ({seconds:.1f}s, {mp or '?'} MP){warn}", flush=True)

    if not args.dry_run:
        print(f"DONE: ok={ok} skip={skipped} fail={failed} -> {output_dir}")
        sys.exit(2 if failed else 0)


def collect_inputs(input_dir: Path, output_dir: Path, recursive: bool, exclude: list[str] | None = None) -> list[Path]:
    iterator = input_dir.rglob("*") if recursive else input_dir.glob("*")
    patterns = exclude or []
    sources = []
    for path in sorted(iterator):
        if not path.is_file() or path.suffix.lower() not in INPUT_EXTENSIONS:
            continue
        if output_dir in path.parents:
            continue
        if any(fnmatch(path.name, pat) for pat in patterns):
            continue
        sources.append(path)
    return sources


def default_output_dir_for_input(input_dir: Path, topic_name: str | None) -> Path:
    topic_source = (topic_name or "").strip()
    if not topic_source:
        topic_source = input_dir.name
        if topic_source.lower() in GENERIC_INPUT_DIR_NAMES and input_dir.parent.name:
            topic_source = input_dir.parent.name
    topic_slug = slugify_topic_name(topic_source, fallback="images")
    return DEFAULT_DOWNLOADS_ROOT / f"{topic_slug}_upscale"


def slugify_topic_name(value: str, fallback: str = "images") -> str:
    slug = re.sub(r"[^\w]+", "_", value.lower(), flags=re.UNICODE).strip("_")
    return slug or fallback


def is_valid_image(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < MIN_VALID_BYTES:
        return False
    head = path.read_bytes()[:12]
    return (
        head.startswith(PNG_MAGIC)
        or head.startswith(JPEG_MAGIC)
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
    )


def find_mozjpeg_bin(explicit: str | None) -> Path | None:
    candidates = []
    if explicit:
        candidates.append(explicit)
    candidates.extend(MOZJPEG_CANDIDATES)
    path_cjpeg = shutil.which("cjpeg")
    if path_cjpeg:
        candidates.append(path_cjpeg)
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_file():
            return path
    return None


def convert_to_jpeg(png_path: Path, jpeg_path: Path, quality: int, mozjpeg_bin: Path | None) -> tuple[bool, dict]:
    if mozjpeg_bin is None:
        return False, {"encoder": None, "method": None, "error": "missing MozJPEG-compatible cjpeg"}

    direct = run_cjpeg(mozjpeg_bin, png_path, jpeg_path, quality)
    if direct.returncode == 0 and is_valid_image(jpeg_path):
        return True, {"encoder": str(mozjpeg_bin), "method": "direct"}

    jpeg_path.unlink(missing_ok=True)

    if shutil.which("sips") is None:
        return False, {
            "encoder": str(mozjpeg_bin),
            "method": "direct",
            "error": (direct.stderr or direct.stdout or "cjpeg direct conversion failed")[-2000:],
        }

    with tempfile.TemporaryDirectory(prefix="upscale-mozjpeg-") as tmp:
        tga_path = Path(tmp) / f"{png_path.stem}.tga"
        sips_cmd = ["sips", "-s", "format", "tga", str(png_path), "--out", str(tga_path)]
        sips_result = subprocess.run(sips_cmd, capture_output=True, text=True, check=False)
        if sips_result.returncode != 0 or not tga_path.is_file():
            return False, {
                "encoder": str(mozjpeg_bin),
                "method": "tga-fallback",
                "error": (sips_result.stderr or sips_result.stdout or "sips TGA conversion failed")[-2000:],
            }
        fallback = run_cjpeg(mozjpeg_bin, tga_path, jpeg_path, quality, targa=True)
        if fallback.returncode == 0 and is_valid_image(jpeg_path):
            return True, {"encoder": str(mozjpeg_bin), "method": "tga-fallback"}
        return False, {
            "encoder": str(mozjpeg_bin),
            "method": "tga-fallback",
            "error": (fallback.stderr or fallback.stdout or "cjpeg TGA conversion failed")[-2000:],
        }


def run_cjpeg(cjpeg_bin: Path, input_path: Path, jpeg_path: Path, quality: int, targa: bool = False) -> subprocess.CompletedProcess[str]:
    cmd = [
        str(cjpeg_bin),
        "-quality", str(quality),
        "-optimize",
    ]
    if targa:
        cmd.append("-targa")
    if Path(SRGB_PROFILE).is_file():
        cmd.extend(["-icc", SRGB_PROFILE])
    cmd.extend(["-outfile", str(jpeg_path), str(input_path)])
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def megapixels(path: Path | None) -> float | None:
    """Read image dimensions from PNG/JPEG headers."""
    dims = dimensions(path)
    if dims is None:
        return None
    width, height = dims
    return round(width * height / 1_000_000, 1)


def dimensions(path: Path | None) -> tuple[int, int] | None:
    if path is None or not path.is_file():
        return None
    data = path.read_bytes()
    head = data[:24]
    if not head.startswith(PNG_MAGIC) or len(head) < 24:
        if head.startswith(JPEG_MAGIC):
            return jpeg_dimensions(data)
        return None
    return struct.unpack(">II", head[16:24])


def jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    idx = 2
    while idx + 9 < len(data):
        if data[idx] != 0xFF:
            idx += 1
            continue
        marker = data[idx + 1]
        idx += 2
        if marker in {0xD8, 0xD9}:
            continue
        if marker == 0xDA:
            break
        if idx + 2 > len(data):
            break
        length = struct.unpack(">H", data[idx:idx + 2])[0]
        if length < 2 or idx + length > len(data):
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height, width = struct.unpack(">HH", data[idx + 3:idx + 7])
            return width, height
        idx += length
    return None


def megapixels_from_source(src: Path, scale: int) -> float | None:
    base = megapixels(src)
    return round(base * scale * scale, 1) if base is not None else None


def record(manifest: dict, manifest_path: Path, src: Path, status: str, output: Path, seconds: float, **extra) -> None:
    manifest.setdefault("items", {})[src.name] = {
        "status": status,
        "source": str(src),
        "output": str(output),
        "seconds": round(seconds, 1),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        **extra,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_manifest(path: Path) -> dict:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"schemaVersion": 1, "createdAt": datetime.now(timezone.utc).isoformat()}


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
