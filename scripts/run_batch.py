#!/usr/bin/env python3
"""Run a resumable Oracle batch and build local artifacts."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parents[1]
BUILD_PPTX = SKILL_DIR / "scripts" / "build_pptx.js"
DEFAULT_ORACLE_HOME = Path("~/.oracle").expanduser()
DEFAULT_PROFILE = DEFAULT_ORACLE_HOME / "browser-profile"
DEFAULT_CHATGPT_URL = os.environ.get(
    "ORACLE_CHATGPT_URL",
    "https://chatgpt.com/",
)
# Node 26 + undici can crash on macOS IPv6 sockets with
# `setTypeOfService EINVAL`; keep Oracle browser subprocesses IPv4-first.
ORACLE_NODE_OPTIONS = "--dns-result-order=ipv4first"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
DEFAULT_GEMINI_VIDEO_MODEL = "gemini-3.1-pro"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an Oracle multitask batch.")
    parser.add_argument("--batch", required=True, help="Path to batch.json.")
    parser.add_argument("--concurrency", type=int, help="Max concurrent item runs.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands and update manifests without launching Oracle.")
    parser.add_argument("--resume", action="store_true", help="Skip completed items.")
    parser.add_argument("--max-items", type=int, default=0, help="Run at most N selected items.")
    parser.add_argument("--oracle-bin", default="oracle")
    parser.add_argument("--node-bin", default="node")
    parser.add_argument("--engine", choices=["browser", "api"], help="Override batch default engine.")
    parser.add_argument("--model", help="Override batch default model.")
    parser.add_argument("--reasoning-effort", choices=["none", "low", "medium", "high", "xhigh", "max"], help="Override API reasoning effort.")
    parser.add_argument("--reasoning-mode", choices=["standard", "pro"], help="Override API reasoning mode.")
    parser.add_argument("--timeout", help="Override Oracle per-call timeout, for example 10m.")
    parser.add_argument("--manual-login", action="store_true", default=True)
    parser.add_argument("--no-manual-login", dest="manual_login", action="store_false")
    parser.add_argument("--overwrite", action="store_true", help="Rerun completed items.")
    parser.add_argument("--retry-images", action="store_true", help="Retry only items marked needs_image_retry.")
    parser.add_argument("--retry-videos", action="store_true", help="Retry only items marked needs_video_retry.")
    args = parser.parse_args()

    batch_path = Path(args.batch).expanduser().resolve()
    batch = load_json(batch_path)
    defaults = batch.get("defaults", {})
    concurrency = args.concurrency or int(defaults.get("concurrency") or 4)
    concurrency = max(1, min(concurrency, 10))

    items = select_items(batch.get("items", []), args.max_items, args.resume, args.overwrite, args.retry_images, args.retry_videos)
    if not items:
        print("No pending items selected.")
        return

    if shutil.which(args.oracle_bin) is None and not args.dry_run:
        raise SystemExit(f"Oracle executable not found: {args.oracle_bin}")
    if shutil.which(args.node_bin) is None and not args.dry_run:
        raise SystemExit(f"Node executable not found: {args.node_bin}")

    asyncio.run(run_items(args, batch, batch_path, items, concurrency))


async def run_items(args: argparse.Namespace, batch: dict[str, Any], batch_path: Path, items: list[dict[str, Any]], concurrency: int) -> None:
    sem = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(
        *(run_one_item(args, batch, batch_path, item, sem) for item in items),
        return_exceptions=True,
    )
    failures = [res for res in results if isinstance(res, Exception)]
    if failures:
        for failure in failures:
            print(f"ERROR: {failure}", file=sys.stderr)
        raise SystemExit(1)
    refresh_batch_status(batch, batch_path)


async def run_one_item(args: argparse.Namespace, batch: dict[str, Any], batch_path: Path, item: dict[str, Any], sem: asyncio.Semaphore) -> None:
    async with sem:
        item_dir = Path(item["itemDir"]).expanduser().resolve()
        chain = load_json(Path(item["chainPath"]).expanduser().resolve())
        manifest_path = Path(item["manifestPath"]).expanduser().resolve()
        manifest = load_json(manifest_path)
        outputs = item.get("outputs") or manifest.get("outputs") or {}
        artifacts_dir = Path(outputs["artifactsDir"]).expanduser().resolve()
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        if args.retry_images:
            await retry_images_for_item(args, batch, batch_path, item, manifest_path, manifest, outputs)
            return
        if args.retry_videos:
            await retry_video_for_item(args, batch, batch_path, item, manifest_path, manifest, outputs, chain)
            return

        command = build_oracle_command(args, batch, item, chain, outputs)

        if args.dry_run:
            print(" ".join(shell_quote(part) for part in command))
            return

        update_manifest(manifest_path, manifest, "running", command=command)
        update_batch_item(batch, batch_path, item["id"], "running")

        env = oracle_env()

        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(item_dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")

        stdout_path = item_dir / "oracle.stdout.log"
        stderr_path = item_dir / "oracle.stderr.log"
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")

        if proc.returncode != 0:
            update_manifest(
                manifest_path,
                manifest,
                "failed",
                returnCode=proc.returncode,
                stdoutPath=str(stdout_path),
                stderrPath=str(stderr_path),
                stderrTail=stderr[-4000:],
            )
            update_batch_item(batch, batch_path, item["id"], "failed")
            raise RuntimeError(f"{item['id']} failed with exit code {proc.returncode}")

        chat_path = Path(outputs["chat"]).expanduser().resolve()
        round_usages: list[dict[str, Any]] = []
        if (args.engine or batch.get("defaults", {}).get("engine")) == "api" and chain.get("promptChain", {}).get("followUps"):
            current_session = extract_session_slug(stdout)
            if not current_session:
                raise RuntimeError(f"{item['id']}: Oracle API session id missing for follow-up")
            first_usage = save_oracle_usage(current_session, item_dir)
            if first_usage:
                round_usages.append(first_usage)
                shutil.copyfile(item_dir / "oracle_usage.json", item_dir / "round_1_usage.json")
            if chat_path.is_file():
                shutil.copyfile(chat_path, item_dir / "round_1.md")
            for round_index, followup in enumerate(chain["promptChain"]["followUps"], start=2):
                followup_command = build_api_followup_command(args, batch, item, current_session, followup, chat_path, round_index)
                next_proc = await asyncio.create_subprocess_exec(
                    *followup_command, cwd=str(item_dir), env=env,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                next_stdout_b, next_stderr_b = await next_proc.communicate()
                stdout = next_stdout_b.decode("utf-8", errors="replace")
                next_stderr = next_stderr_b.decode("utf-8", errors="replace")
                (item_dir / f"round_{round_index}.stdout.log").write_text(stdout, encoding="utf-8")
                (item_dir / f"round_{round_index}.stderr.log").write_text(next_stderr, encoding="utf-8")
                (item_dir / f"round_{round_index}.command.json").write_text(json.dumps(followup_command, ensure_ascii=False, indent=2), encoding="utf-8")
                if next_proc.returncode != 0:
                    update_manifest(manifest_path, manifest, "failed", failedRound=round_index, stderrTail=next_stderr[-4000:])
                    update_batch_item(batch, batch_path, item["id"], "failed")
                    raise RuntimeError(f"{item['id']} follow-up round {round_index} failed with exit code {next_proc.returncode}")
                current_session = extract_session_slug(stdout)
                if not current_session:
                    raise RuntimeError(f"{item['id']}: Oracle API session id missing after round {round_index}")
                round_usage = save_oracle_usage(current_session, item_dir)
                if round_usage:
                    round_usages.append(round_usage)
                    shutil.copyfile(item_dir / "oracle_usage.json", item_dir / f"round_{round_index}_usage.json")
        if not chat_path.exists() or not chat_path.read_text(encoding="utf-8", errors="replace").strip():
            chat_path.write_text(stdout, encoding="utf-8")

        session_slug = extract_session_slug(stdout) or item["id"][:90]
        usage = save_oracle_usage(session_slug, item_dir)
        copied_artifacts = copy_oracle_artifacts(session_slug, artifacts_dir)
        status = postprocess_item(args, batch, item, chain, outputs, copied_artifacts)
        update_manifest(
            manifest_path,
            manifest,
            status,
            returnCode=0,
            stdoutPath=str(stdout_path),
            stderrPath=str(stderr_path),
            oracleSessionSlug=session_slug,
            oracleUsage=usage,
            roundUsages=round_usages,
            copiedArtifacts=copied_artifacts,
        )
        update_batch_item(batch, batch_path, item["id"], status)


def extract_session_slug(stdout: str) -> str | None:
    match = re.search(r"\bsid=([A-Za-z0-9_-]+)", stdout)
    if match:
        return match.group(1)
    match = re.search(r"^Session: ([A-Za-z0-9_-]+)$", stdout, re.MULTILINE)
    return match.group(1) if match else None


def save_oracle_usage(session_slug: str, item_dir: Path) -> dict[str, Any] | None:
    meta_path = DEFAULT_ORACLE_HOME / "sessions" / session_slug / "meta.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        usage = {
            "sessionId": session_slug,
            "model": meta.get("model"),
            "mode": meta.get("mode"),
            "usage": meta.get("usage"),
            "elapsedMs": meta.get("elapsedMs"),
            "status": meta.get("status"),
        }
        (item_dir / "oracle_usage.json").write_text(json.dumps(usage, indent=2) + "\n", encoding="utf-8")
        return usage
    except (OSError, ValueError):
        return None


def build_oracle_command(args: argparse.Namespace, batch: dict[str, Any], item: dict[str, Any], chain: dict[str, Any], outputs: dict[str, str]) -> list[str]:
    defaults = batch.get("defaults", {})
    engine = args.engine or defaults.get("engine") or "browser"
    model = args.model or defaults.get("model") or (DEFAULT_GEMINI_VIDEO_MODEL if batch.get("artifact") == "video" else "gpt-5.5")
    prompt_chain = chain.get("promptChain", {})
    initial = prompt_chain.get("initialPrompt", "")
    followups = prompt_chain.get("followUps", [])

    command = [
        args.oracle_bin,
        "--engine",
        engine,
        "--model",
        model,
        "--prompt",
        initial,
        "--slug",
        item["id"][:90],
        "--write-output",
        outputs["chat"],
        "--wait",
    ]
    timeout = args.timeout or defaults.get("timeout")
    if timeout:
        command.extend(["--timeout", timeout])

    if engine == "api":
        effort = args.reasoning_effort or defaults.get("reasoningEffort")
        mode = args.reasoning_mode or defaults.get("reasoningMode")
        if effort:
            command.extend(["--reasoning-effort", effort])
        if mode:
            command.extend(["--reasoning-mode", mode])
        if defaults.get("independentReplicates"):
            command.append("--force")

    if engine == "browser":
        if not model.startswith("gemini"):
            command.extend(["--chatgpt-url", DEFAULT_CHATGPT_URL])
        if args.manual_login:
            command.append("--browser-manual-login")
        if not model.startswith("gemini"):
            command.extend(["--browser-thinking-time", "high"])
        command.extend(["--browser-auto-reattach-delay", "30s"])
        command.extend(["--browser-auto-reattach-interval", "2m"])
        command.extend(["--browser-auto-reattach-timeout", "2m"])
        if batch.get("artifact") == "presentation":
            command.extend(["--generate-image", str(Path(outputs["artifactsDir"]) / "chatgpt-image.png")])
        for followup in followups:
            command.extend(["--browser-follow-up", followup])

    for source in defaults.get("sourceFiles", []):
        command.extend(["--file", source])

    return command


def build_api_followup_command(args: argparse.Namespace, batch: dict[str, Any], item: dict[str, Any], session_id: str, prompt: str, output_path: Path, round_index: int) -> list[str]:
    defaults = batch.get("defaults", {})
    command = [args.oracle_bin, "--engine", "api", "--model", args.model or defaults.get("model") or "gpt-5.5", "--followup", session_id, "--prompt", prompt, "--slug", f"{item['id'][:65]}-round-{round_index}", "--write-output", str(output_path), "--wait"]
    timeout = args.timeout or defaults.get("timeout")
    if timeout:
        command.extend(["--timeout", timeout])
    effort = args.reasoning_effort or defaults.get("reasoningEffort")
    mode = args.reasoning_mode or defaults.get("reasoningMode")
    if effort:
        command.extend(["--reasoning-effort", effort])
    if mode:
        command.extend(["--reasoning-mode", mode])
    if defaults.get("independentReplicates"):
        command.append("--force")
    return command


async def retry_images_for_item(
    args: argparse.Namespace,
    batch: dict[str, Any],
    batch_path: Path,
    item: dict[str, Any],
    manifest_path: Path,
    manifest: dict[str, Any],
    outputs: dict[str, str],
) -> None:
    image_prompts_path = Path(outputs["imagePrompts"]).expanduser().resolve()
    artifacts_dir = Path(outputs["artifactsDir"]).expanduser().resolve()
    if not image_prompts_path.exists():
        update_manifest(manifest_path, manifest, "needs_image_retry", reason="missing image_prompts.md")
        update_batch_item(batch, batch_path, item["id"], "needs_image_retry")
        return

    prompt = (
        "Generate presentation images for this deck from the following prompts. "
        "Return downloadable images when possible.\n\n"
        + image_prompts_path.read_text(encoding="utf-8", errors="replace")
    )
    defaults = batch.get("defaults", {})
    model = args.model or defaults.get("model") or "gpt-5.5"
    retry_output = artifacts_dir / "image-retry.md"
    image_target = artifacts_dir / "retry-image.png"
    command = [
        args.oracle_bin,
        "--engine",
        "browser",
        "--model",
        model,
        "--chatgpt-url",
        DEFAULT_CHATGPT_URL,
        "--prompt",
        prompt,
        "--slug",
        f"{item['id'][:70]}-image-retry",
        "--write-output",
        str(retry_output),
        "--wait",
        "--browser-manual-login",
        "--browser-thinking-time",
        "high",
        "--generate-image",
        str(image_target),
    ]

    if args.dry_run:
        print(" ".join(shell_quote(part) for part in command))
        return

    update_manifest(manifest_path, manifest, "retrying_images", imageRetryCommand=command)
    update_batch_item(batch, batch_path, item["id"], "retrying_images")

    env = oracle_env()
    proc = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(Path(item["itemDir"]).expanduser().resolve()),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    (artifacts_dir / "image-retry.stdout.log").write_text(stdout, encoding="utf-8")
    (artifacts_dir / "image-retry.stderr.log").write_text(stderr, encoding="utf-8")
    if proc.returncode != 0:
        update_manifest(manifest_path, manifest, "needs_image_retry", imageRetryReturnCode=proc.returncode, imageRetryStderrTail=stderr[-4000:])
        update_batch_item(batch, batch_path, item["id"], "needs_image_retry")
        return

    rebuild_pptx(args, outputs)
    has_images = any(path.suffix.lower() in IMAGE_EXTENSIONS for path in artifacts_dir.glob("*"))
    status = "completed" if has_images else "needs_image_retry"
    update_manifest(manifest_path, manifest, status, imageRetryReturnCode=0)
    update_batch_item(batch, batch_path, item["id"], status)


async def retry_video_for_item(
    args: argparse.Namespace,
    batch: dict[str, Any],
    batch_path: Path,
    item: dict[str, Any],
    manifest_path: Path,
    manifest: dict[str, Any],
    outputs: dict[str, str],
    chain: dict[str, Any],
) -> None:
    video_prompt_path = Path(outputs["videoPrompt"]).expanduser().resolve()
    artifacts_dir = Path(outputs["artifactsDir"]).expanduser().resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    # Never send the full chat transcript as a video prompt; fall back to the
    # compact rendered prompt (scenario, shot list with timing, narration).
    if video_prompt_path.exists():
        prompt = video_prompt_path.read_text(encoding="utf-8", errors="replace")
    else:
        prompt = render_video_prompt(chain)

    defaults = batch.get("defaults", {})
    model = args.model or defaults.get("model") or DEFAULT_GEMINI_VIDEO_MODEL
    command = [
        args.oracle_bin,
        "--engine",
        "browser",
        "--model",
        model,
        "--prompt",
        prompt,
        "--slug",
        f"{item['id'][:70]}-video-retry",
        "--write-output",
        str(artifacts_dir / "video-retry.md"),
        "--wait",
        "--browser-manual-login",
    ]

    if args.dry_run:
        print(" ".join(shell_quote(part) for part in command))
        return

    update_manifest(manifest_path, manifest, "retrying_video", videoRetryCommand=command)
    update_batch_item(batch, batch_path, item["id"], "retrying_video")

    env = oracle_env()
    proc = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(Path(item["itemDir"]).expanduser().resolve()),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    (artifacts_dir / "video-retry.stdout.log").write_text(stdout, encoding="utf-8")
    (artifacts_dir / "video-retry.stderr.log").write_text(stderr, encoding="utf-8")
    if proc.returncode != 0:
        update_manifest(manifest_path, manifest, "needs_video_retry", videoRetryReturnCode=proc.returncode, videoRetryStderrTail=stderr[-4000:])
        update_batch_item(batch, batch_path, item["id"], "needs_video_retry")
        return

    retry_slug = f"{item['id'][:70]}-video-retry"
    copied_artifacts = copy_oracle_artifacts(retry_slug, artifacts_dir)
    status = "completed" if has_video_artifact(artifacts_dir) else "needs_video_retry"
    update_manifest(manifest_path, manifest, status, videoRetryReturnCode=0, videoRetrySessionSlug=retry_slug, copiedArtifacts=copied_artifacts)
    update_batch_item(batch, batch_path, item["id"], status)


def postprocess_item(args: argparse.Namespace, batch: dict[str, Any], item: dict[str, Any], chain: dict[str, Any], outputs: dict[str, str], copied_artifacts: list[str]) -> str:
    if batch.get("artifact") == "video":
        return postprocess_video_item(chain, outputs, copied_artifacts)
    if batch.get("artifact") != "presentation":
        return "completed"

    chat_path = Path(outputs["chat"]).expanduser().resolve()
    spec_path = Path(outputs["slideSpec"]).expanduser().resolve()
    image_prompts_path = Path(outputs["imagePrompts"]).expanduser().resolve()
    pptx_path = Path(outputs["pptx"]).expanduser().resolve()
    artifacts_dir = Path(outputs["artifactsDir"]).expanduser().resolve()

    chat_text = chat_path.read_text(encoding="utf-8", errors="replace") if chat_path.exists() else ""
    spec = extract_slide_spec(chat_text)
    if spec is None:
        spec = fallback_slide_spec(chain, chat_text)
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    image_prompts_path.write_text(render_image_prompts(spec), encoding="utf-8")

    build_cmd = [
        args.node_bin,
        str(BUILD_PPTX),
        "--spec",
        str(spec_path),
        "--out",
        str(pptx_path),
        "--assets-dir",
        str(artifacts_dir),
    ]
    result = subprocess.run(build_cmd, text=True, capture_output=True, check=False)
    (pptx_path.parent / "build-pptx.stdout.log").write_text(result.stdout, encoding="utf-8")
    (pptx_path.parent / "build-pptx.stderr.log").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"PPTX build failed for {item['id']}: {result.stderr[-1000:]}")

    has_image_prompts = any((slide.get("imagePrompt") or "").strip() for slide in spec.get("slides", []))
    has_images = any(Path(path).suffix.lower() in IMAGE_EXTENSIONS for path in copied_artifacts)
    return "needs_image_retry" if has_image_prompts and not has_images else "completed"


def postprocess_video_item(chain: dict[str, Any], outputs: dict[str, str], copied_artifacts: list[str]) -> str:
    video_prompt_path = Path(outputs["videoPrompt"]).expanduser().resolve()
    video_prompt_path.write_text(render_video_prompt(chain), encoding="utf-8")
    has_video = any(Path(path).suffix.lower() in VIDEO_EXTENSIONS for path in copied_artifacts)
    return "completed" if has_video else "needs_video_retry"


def rebuild_pptx(args: argparse.Namespace, outputs: dict[str, str]) -> None:
    spec_path = Path(outputs["slideSpec"]).expanduser().resolve()
    pptx_path = Path(outputs["pptx"]).expanduser().resolve()
    artifacts_dir = Path(outputs["artifactsDir"]).expanduser().resolve()
    if not spec_path.exists():
        raise RuntimeError(f"Cannot rebuild PPTX; missing {spec_path}")
    build_cmd = [
        args.node_bin,
        str(BUILD_PPTX),
        "--spec",
        str(spec_path),
        "--out",
        str(pptx_path),
        "--assets-dir",
        str(artifacts_dir),
    ]
    result = subprocess.run(build_cmd, text=True, capture_output=True, check=False)
    (pptx_path.parent / "build-pptx-image-retry.stdout.log").write_text(result.stdout, encoding="utf-8")
    (pptx_path.parent / "build-pptx-image-retry.stderr.log").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"PPTX rebuild failed: {result.stderr[-1000:]}")


def extract_slide_spec(text: str) -> dict[str, Any] | None:
    candidates = []
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)
    candidates.extend(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL))
    object_match = re.search(r"\{.*\"slides\"\s*:\s*\[.*\}", text, flags=re.DOTALL)
    if object_match:
        candidates.append(object_match.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("slides"), list):
            return normalize_spec(parsed)
    return None


def fallback_slide_spec(chain: dict[str, Any], chat_text: str) -> dict[str, Any]:
    title = chain.get("title") or "Oracle Presentation"
    brief = chain.get("brief") or ""
    lines = [line.strip("-* \t") for line in chat_text.splitlines() if line.strip()]
    bullets = lines[:24] or [brief]
    slides = [{"layout": "title", "title": title, "subtitle": brief[:180], "bullets": [], "speakerNotes": ""}]
    for index in range(0, min(len(bullets), 20), 4):
        slides.append(
            {
                "layout": "content",
                "title": f"Key points {index // 4 + 1}",
                "bullets": bullets[index : index + 4],
                "imagePrompt": "",
                "speakerNotes": "",
            }
        )
    slides.append({"layout": "closing", "title": "Next steps", "bullets": ["Review and refine the generated deck."], "speakerNotes": ""})
    return normalize_spec({"title": title, "subtitle": brief[:180], "slides": slides})


def normalize_spec(spec: dict[str, Any]) -> dict[str, Any]:
    spec.setdefault("title", "Oracle Presentation")
    spec.setdefault("subtitle", "")
    style = spec.setdefault("style", {})
    style.setdefault("primaryColor", "1F4E79")
    style.setdefault("accentColor", "2CA6A4")
    style.setdefault("backgroundColor", "F7F9FB")
    style.setdefault("textColor", "1B1F24")
    normalized_slides = []
    for slide in spec.get("slides", []):
        if not isinstance(slide, dict):
            continue
        slide.setdefault("layout", "content")
        slide.setdefault("title", "Untitled")
        slide.setdefault("bullets", [])
        slide.setdefault("imagePrompt", "")
        slide.setdefault("speakerNotes", "")
        normalized_slides.append(slide)
    spec["slides"] = normalized_slides or [{"layout": "title", "title": spec["title"], "subtitle": spec.get("subtitle", ""), "bullets": []}]
    return spec


def render_image_prompts(spec: dict[str, Any]) -> str:
    lines = [f"# Image Prompts: {spec.get('title', 'Presentation')}", ""]
    found = False
    for idx, slide in enumerate(spec.get("slides", []), start=1):
        prompt = (slide.get("imagePrompt") or "").strip()
        if prompt:
            found = True
            lines.extend([f"## Slide {idx}: {slide.get('title', 'Untitled')}", prompt, ""])
    if not found:
        lines.append("No image prompts were provided.")
    return "\n".join(lines).rstrip() + "\n"


def render_video_prompt(chain: dict[str, Any]) -> str:
    prompt_chain = chain.get("promptChain", {})
    initial = (prompt_chain.get("initialPrompt") or "").strip()
    title = chain.get("title") or "Video"
    return f"# Video Prompt: {title}\n\n{initial}\n"


def copy_oracle_artifacts(session_slug: str, target_dir: Path) -> list[str]:
    session_artifacts = DEFAULT_ORACLE_HOME / "sessions" / session_slug / "artifacts"
    copied: list[str] = []
    if not session_artifacts.exists():
        return copied
    for src in session_artifacts.iterdir():
        if not src.is_file():
            continue
        dest = target_dir / src.name
        shutil.copy2(src, dest)
        copied.append(str(dest))
    return copied


def select_items(items: list[dict[str, Any]], max_items: int, resume: bool, overwrite: bool, retry_images: bool, retry_videos: bool) -> list[dict[str, Any]]:
    selected = []
    for item in items:
        status = item.get("status")
        if retry_images and status != "needs_image_retry":
            continue
        if retry_videos and status != "needs_video_retry":
            continue
        if resume and not retry_images and not retry_videos and not overwrite and status in {"completed", "needs_image_retry", "needs_video_retry"}:
            continue
        selected.append(item)
        if max_items and len(selected) >= max_items:
            break
    return selected


def update_batch_item(batch: dict[str, Any], batch_path: Path, item_id: str, status: str) -> None:
    for item in batch.get("items", []):
        if item.get("id") == item_id:
            item["status"] = status
            item["updatedAt"] = now_iso()
            break
    batch["updatedAt"] = now_iso()
    write_json(batch_path, batch)


def refresh_batch_status(batch: dict[str, Any], batch_path: Path) -> None:
    statuses = {item.get("status") for item in batch.get("items", [])}
    if statuses <= {"completed"}:
        status = "completed"
    elif "failed" in statuses:
        status = "partial"
    elif "needs_image_retry" in statuses:
        status = "needs_image_retry"
    elif "needs_video_retry" in statuses:
        status = "needs_video_retry"
    else:
        status = "running"
    batch["status"] = status
    batch["updatedAt"] = now_iso()
    write_json(batch_path, batch)


def update_manifest(path: Path, manifest: dict[str, Any], status: str, **extra: Any) -> None:
    manifest.update({"status": status, "updatedAt": now_iso(), **extra})
    write_json(path, manifest)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def has_video_artifact(artifacts_dir: Path) -> bool:
    return any(path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS for path in artifacts_dir.glob("*"))


def oracle_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("ORACLE_HOME_DIR", str(DEFAULT_ORACLE_HOME))
    env.setdefault("ORACLE_BROWSER_PROFILE_DIR", str(DEFAULT_PROFILE))
    existing_node_options = env.get("NODE_OPTIONS", "").strip()
    if ORACLE_NODE_OPTIONS not in existing_node_options.split():
        env["NODE_OPTIONS"] = " ".join(part for part in [existing_node_options, ORACLE_NODE_OPTIONS] if part)
    return env


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
