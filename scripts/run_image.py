#!/usr/bin/env python3
"""Run a lightweight Oracle image-only job.

This helper avoids the full batch/chain/manifest structure for simple image
generations. It creates only:

- jobs.json
- run_manifest.json
- artifacts/<image>.png
- _drive_logs/<job>.log
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parents[1]
DRIVE_IMAGES = SKILL_DIR / "scripts" / "drive_images.py"
DEFAULT_OUTPUT_ROOT = (Path.home() / "Downloads" / "Codex (1)" / "oracle-runs")
VISUAL_REFERENCE_INSTRUCTION = (
    "Before generating, search the internet in the active web UI (ChatGPT or Gemini Web) "
    "for current, relevant visual references. Base the image exclusively on those "
    "internet references and summarize the reference basis. Do not rely on "
    "memory-only references."
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create and run a lightweight Oracle image job.")
    prompt_group = parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt", help="Final image prompt.")
    prompt_group.add_argument("--prompt-file", help="Path to a text file containing the final image prompt.")
    parser.add_argument("--task-slug", help="Stable task slug. Default derives from the prompt.")
    parser.add_argument("--run-id", help="Run id. Default is UTC timestamp.")
    parser.add_argument("--filename", default="image.png", help="PNG filename inside artifacts/.")
    parser.add_argument("--job-slug", help="Oracle job slug. Default derives from task slug.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Root for Oracle image runs.")
    parser.add_argument("--concurrency", type=int, default=1, help="Image driver concurrency.")
    parser.add_argument("--stagger", type=float, default=0.0, help="Seconds to stagger image driver starts.")
    parser.add_argument("--model", default=os.environ.get("ORACLE_IMG_MODEL", "gpt-5.5"))
    parser.add_argument("--dry-run", action="store_true", help="Print planned paths and command without writing files.")
    parser.add_argument("--create-only", action="store_true", help="Write jobs/manifest but do not launch Oracle.")
    parser.add_argument("--overwrite", action="store_true", help="Allow reusing a non-empty run directory.")
    args = parser.parse_args()

    prompt = load_prompt(args.prompt, args.prompt_file)
    prompt = with_visual_reference_instruction(prompt)

    task_slug = args.task_slug or slugify(prompt)[:70] or "oracle-image"
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    output_root = Path(args.output_root).expanduser().resolve()
    run_dir = output_root / task_slug / run_id
    artifacts_dir = run_dir / "artifacts"
    log_dir = run_dir / "_drive_logs"
    jobs_path = run_dir / "jobs.json"
    manifest_path = run_dir / "run_manifest.json"

    filename = normalize_png_filename(args.filename)
    target = artifacts_dir / filename
    job_slug = args.job_slug or f"{task_slug[:58]}-img-01"
    job = {"target": str(target), "slug": job_slug, "prompt": prompt}
    command = [
        sys.executable,
        str(DRIVE_IMAGES),
        str(jobs_path),
        "--concurrency",
        str(max(1, min(args.concurrency, 10))),
        "--stagger",
        str(max(0.0, args.stagger)),
        "--logdir",
        str(log_dir),
    ]

    if args.dry_run:
        print(json.dumps({
            "runDir": str(run_dir),
            "jobsPath": str(jobs_path),
            "manifestPath": str(manifest_path),
            "target": str(target),
            "job": job,
            "command": command,
        }, ensure_ascii=False, indent=2))
        return

    if run_dir.exists() and any(run_dir.iterdir()) and not args.overwrite:
        raise SystemExit(f"Run directory already exists: {run_dir}. Pass --overwrite to reuse it.")

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    write_json(jobs_path, [job])

    manifest = base_manifest(args, run_dir, jobs_path, manifest_path, target, log_dir, job, command)
    status = "created" if args.create_only else "running"
    write_json(manifest_path, {**manifest, "status": status, "updatedAt": now_iso()})

    if args.create_only:
        print(str(manifest_path))
        return

    started = time.time()
    env = os.environ.copy()
    env["ORACLE_IMG_MODEL"] = args.model
    proc = subprocess.run(command, cwd=str(run_dir), capture_output=True, text=True, env=env)
    duration = round(time.time() - started, 3)
    target_ok = is_png(target)

    if target_ok and proc.returncode == 0:
        status = "ok"
    elif target_ok:
        status = "ok_with_driver_error"
    else:
        status = "failed"

    completed_manifest = {
        **manifest,
        "status": status,
        "updatedAt": now_iso(),
        "completedAt": now_iso(),
        "durationSeconds": duration,
        "returnCode": proc.returncode,
        "targetExists": target.exists(),
        "targetIsPng": target_ok,
        "stdoutTail": tail(proc.stdout),
        "stderrTail": tail(proc.stderr),
    }
    write_json(manifest_path, completed_manifest)

    print(f"status={status}")
    print(f"target={target}")
    print(f"manifest={manifest_path}")
    raise SystemExit(0 if target_ok else 2)


def load_prompt(prompt: str | None, prompt_file: str | None) -> str:
    if prompt_file:
        value = Path(prompt_file).expanduser().read_text(encoding="utf-8")
    else:
        value = prompt or ""
    value = value.strip()
    if not value:
        raise SystemExit("Prompt is empty.")
    return value


def with_visual_reference_instruction(prompt: str) -> str:
    has_current_instruction = "search the internet in the active web UI" in prompt
    has_legacy_instruction = "search the internet in web ChatGPT" in prompt
    if (has_current_instruction or has_legacy_instruction) and "Do not rely on memory-only references" in prompt:
        return prompt
    return f"{VISUAL_REFERENCE_INSTRUCTION} Then generate: {prompt}"


def base_manifest(
    args: argparse.Namespace,
    run_dir: Path,
    jobs_path: Path,
    manifest_path: Path,
    target: Path,
    log_dir: Path,
    job: dict[str, str],
    command: list[str],
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
        "mode": "lightweight-image",
        "model": args.model,
        "runDir": str(run_dir),
        "jobsPath": str(jobs_path),
        "manifestPath": str(manifest_path),
        "artifactsDir": str(target.parent),
        "logDir": str(log_dir),
        "target": str(target),
        "job": job,
        "command": command,
    }


def is_png(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 1000 and path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def normalize_png_filename(value: str) -> str:
    name = Path(value).name.strip() or "image.png"
    if not name.lower().endswith(".png"):
        name = f"{name}.png"
    return name


def slugify(value: str) -> str:
    translit = {
        "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie",
        "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l",
        "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ь": "",
        "ю": "iu", "я": "ia", "'": "", "’": "",
    }
    normalized = "".join(translit.get(ch, ch) for ch in value.lower())
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    return re.sub(r"-+", "-", normalized).strip("-") or "oracle-image"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def tail(value: str, limit: int = 4000) -> str:
    return value[-limit:] if value else ""


if __name__ == "__main__":
    main()
