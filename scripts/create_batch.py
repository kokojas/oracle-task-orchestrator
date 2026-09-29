#!/usr/bin/env python3
"""Create a resumable Oracle batch with per-item prompt chains."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VALID_ARTIFACTS = {"presentation", "image", "text", "research", "video"}
DEFAULT_OUTPUT_ROOT = (Path.home() / "Downloads" / "Codex (1)" / "oracle-runs")
DEFAULT_GEMINI_VIDEO_MODEL = "gemini-3.1-pro"
VISUAL_REFERENCE_INSTRUCTION = (
    "Before generating any image or visual material, search the internet in the active "
    "web UI (ChatGPT or Gemini Web) for current, relevant visual references. Base the "
    "visual generation exclusively on those internet references and summarize the "
    "reference basis. Do not rely on memory-only references."
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an Oracle multitask batch.")
    parser.add_argument("--brief", required=True, help="Overall task brief.")
    parser.add_argument("--artifact", default="presentation", choices=sorted(VALID_ARTIFACTS))
    parser.add_argument("--items", type=int, default=10, help="Number of work items when --items-file is omitted.")
    parser.add_argument("--items-file", help="JSON array/object with item definitions.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Root for oracle batch runs.")
    parser.add_argument("--task-slug", help="Stable task slug. Default derives from brief.")
    parser.add_argument("--run-id", help="Run id. Default is timestamp.")
    parser.add_argument("--language", default="uk", help="Target language.")
    parser.add_argument("--slides", type=int, default=10, help="Default slide count for presentation items.")
    parser.add_argument("--engine", default="browser", choices=["browser", "api"])
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--reasoning-effort", choices=["none", "low", "medium", "high", "xhigh", "max"], help="API reasoning effort; hold constant across eval arms.")
    parser.add_argument("--reasoning-mode", choices=["standard", "pro"], help="API reasoning mode; hold constant across eval arms.")
    parser.add_argument("--independent-replicates", action="store_true", help="Force independent Oracle sessions for repeated identical prompts.")
    parser.add_argument("--timeout", help="Oracle per-call timeout, for example 10m; record in batch defaults.")
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--video-duration-seconds", type=int, default=8, help="Target duration for video artifacts.")
    parser.add_argument("--video-aspect", default="16:9", choices=["16:9", "9:16", "1:1", "4:3", "3:4"])
    parser.add_argument("--file", action="append", default=[], help="Source file/glob to attach to every item.")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.items < 1:
        raise SystemExit("--items must be >= 1")
    if args.artifact == "video" and args.model == "gpt-5.5":
        args.model = DEFAULT_GEMINI_VIDEO_MODEL

    brief = args.brief.strip()
    task_slug = args.task_slug or slugify(brief)[:70] or "oracle-task"
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    output_root = Path(args.output_root).expanduser().resolve()
    batch_dir = output_root / task_slug / run_id
    if batch_dir.exists() and any(batch_dir.iterdir()) and not args.overwrite:
        raise SystemExit(f"Batch directory already exists: {batch_dir}. Pass --overwrite to reuse it.")

    items = load_items(args.items_file, args.items, brief, args.artifact)
    items_dir = batch_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)

    now = now_iso()
    batch: dict[str, Any] = {
        "schemaVersion": 1,
        "createdAt": now,
        "updatedAt": now,
        "brief": brief,
        "artifact": args.artifact,
        "taskSlug": task_slug,
        "runId": run_id,
        "batchDir": str(batch_dir),
        "defaults": {
            "engine": args.engine,
            "model": args.model,
            "reasoningEffort": args.reasoning_effort,
            "reasoningMode": args.reasoning_mode,
            "independentReplicates": args.independent_replicates,
            "timeout": args.timeout,
            "language": args.language,
            "slides": args.slides,
            "concurrency": max(1, min(args.concurrency, 10)),
            "sourceFiles": args.file,
        },
        "items": [],
    }

    for index, item in enumerate(items, start=1):
        item_id = f"{index:02d}-{slugify(item.get('title') or item.get('brief') or brief)[:48] or 'item'}"
        item_dir = items_dir / item_id
        item_dir.mkdir(parents=True, exist_ok=True)
        chain = build_chain(args, brief, item, item_id, item_dir)
        chain_path = item_dir / "chain.json"
        manifest_path = item_dir / "manifest.json"
        write_json(chain_path, chain)
        write_json(
            manifest_path,
            {
                "schemaVersion": 1,
                "createdAt": now,
                "updatedAt": now,
                "itemId": item_id,
                "status": "pending",
                "chainPath": str(chain_path),
                "outputs": default_outputs(item_dir, item),
            },
        )
        batch["items"].append(
            {
                "id": item_id,
                "index": index,
                "title": item.get("title") or f"{args.artifact.title()} {index:02d}",
                "brief": item.get("brief") or brief,
                "status": "pending",
                "itemDir": str(item_dir),
                "chainPath": str(chain_path),
                "manifestPath": str(manifest_path),
                "outputs": default_outputs(item_dir, item),
            }
        )

    batch_path = batch_dir / "batch.json"
    write_json(batch_path, batch)
    print(str(batch_path))


def load_items(path: str | None, count: int, brief: str, artifact: str) -> list[dict[str, Any]]:
    if path:
        payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        raw_items = payload.get("items") if isinstance(payload, dict) else payload
        if not isinstance(raw_items, list):
            raise SystemExit("--items-file must contain a JSON array or an object with an items array")
        items = []
        for raw in raw_items:
            if isinstance(raw, str):
                items.append({"title": raw, "brief": raw})
            elif isinstance(raw, dict):
                items.append(raw)
            else:
                raise SystemExit("Each item must be a string or object")
        return items
    return [
        {
            "title": f"{artifact.title()} {idx:02d}",
            "brief": f"{brief}\n\nWork item {idx:02d}: create a distinct {artifact} variant.",
        }
        for idx in range(1, count + 1)
    ]


def build_chain(args: argparse.Namespace, batch_brief: str, item: dict[str, Any], item_id: str, item_dir: Path) -> dict[str, Any]:
    artifact = args.artifact
    if artifact == "presentation":
        initial = presentation_initial_prompt(args, batch_brief, item)
        followups = [presentation_spec_prompt(args, item)]
    elif artifact == "image":
        initial = generic_prompt(
            args,
            batch_brief,
            item,
            "Create image prompts and the final generation request.\n\n"
            f"Mandatory visual reference instruction: {VISUAL_REFERENCE_INSTRUCTION}",
        )
        followups = []
    elif artifact == "research":
        initial = generic_prompt(args, batch_brief, item, "Research the topic, cite sources, and produce a concise report.")
        followups = []
    elif artifact == "video":
        initial = video_prompt(args, batch_brief, item)
        followups = []
    else:
        initial = generic_prompt(args, batch_brief, item, "Write the requested text deliverable.")
        followups = []

    return {
        "schemaVersion": 1,
        "createdAt": now_iso(),
        "itemId": item_id,
        "artifact": artifact,
        "title": item.get("title") or item_id,
        "brief": item.get("brief") or batch_brief,
        "itemDir": str(item_dir),
        "promptChain": {
            "initialPrompt": initial,
            "followUps": followups,
        },
    }


def presentation_initial_prompt(args: argparse.Namespace, batch_brief: str, item: dict[str, Any]) -> str:
    return f"""You are creating one presentation in an assistant-controlled Oracle batch.

Batch brief:
{batch_brief}

This work item:
Title: {item.get("title", "Untitled presentation")}
Brief: {item.get("brief", batch_brief)}
Audience: {item.get("audience", "general professional audience")}
Style: {item.get("style", "clear, polished, editorial, presentation-ready")}
Target language: {args.language}
Target slide count: {args.slides}

Work in this same chat. First produce:
- audience and objective
- slide-by-slide story outline
- visual system and tone
- image prompts needed for the deck
- risks or missing information

Every image prompt needed for this deck must include this exact visual reference instruction:
{VISUAL_REFERENCE_INSTRUCTION}

Return Markdown only for this first turn."""


def presentation_spec_prompt(args: argparse.Namespace, item: dict[str, Any]) -> str:
    return f"""Now produce the final presentation specification as strict JSON only.

No markdown fences. No commentary. Use this schema:
{{
  "title": "deck title",
  "subtitle": "short subtitle",
  "language": "{args.language}",
  "style": {{
    "theme": "short style name",
    "primaryColor": "1F4E79",
    "accentColor": "2CA6A4",
    "backgroundColor": "F7F9FB",
    "textColor": "1B1F24"
  }},
  "slides": [
    {{
      "layout": "title|section|content|two-column|image|closing",
      "title": "slide title",
      "subtitle": "optional subtitle",
      "claim": "one sentence slide takeaway",
      "bullets": ["3-5 concise bullets"],
      "left": ["optional left column bullets"],
      "right": ["optional right column bullets"],
      "imagePrompt": "prompt for ChatGPT image generation, or empty string. If non-empty, it must include: {VISUAL_REFERENCE_INSTRUCTION}",
      "speakerNotes": "speaker notes"
    }}
  ]
}}

Create exactly {args.slides} slides unless the item clearly needs fewer. Keep text concise enough to fit in editable PowerPoint shapes."""


def generic_prompt(args: argparse.Namespace, batch_brief: str, item: dict[str, Any], instruction: str) -> str:
    return f"""You are handling one work item in an assistant-controlled Oracle batch.

Batch brief:
{batch_brief}

Work item:
Title: {item.get("title", "Untitled")}
Brief: {item.get("brief", batch_brief)}
Target language: {args.language}

Instruction:
{instruction}

Return the final deliverable in Markdown."""


def video_prompt(args: argparse.Namespace, batch_brief: str, item: dict[str, Any]) -> str:
    return f"""You are creating one Gemini video generation work item in an assistant-controlled Oracle batch.

Batch brief:
{batch_brief}

Work item:
Title: {item.get("title", "Untitled video")}
Brief: {item.get("brief", batch_brief)}
Audience: {item.get("audience", "general audience")}
Style: {item.get("style", "clean, cinematic, useful, production-ready")}
Target language: {args.language}
Target duration: {item.get("durationSeconds", args.video_duration_seconds)} seconds
Target aspect ratio: {item.get("aspect", args.video_aspect)}

Use Gemini browser mode video generation / Veo if it is available in the active Gemini account.
For all visual scenes in the video prompt, include this visual reference instruction:
{VISUAL_REFERENCE_INSTRUCTION}

Create the video directly when the UI supports it. If a downloadable video artifact is not available, return a compact Markdown fallback containing:
- final video prompt
- scene-by-scene shot list with timing
- visual style and motion direction
- narration/on-screen text, if any
- retry notes for manual Gemini/Veo generation

Keep the response concise and artifact-oriented."""


def default_outputs(item_dir: Path, item: dict[str, Any]) -> dict[str, str]:
    pptx_name = slugify(item.get("title") or "presentation")[:80] or "presentation"
    video_name = slugify(item.get("title") or "video")[:80] or "video"
    return {
        "chat": str(item_dir / "chat.md"),
        "slideSpec": str(item_dir / "slide_spec.json"),
        "imagePrompts": str(item_dir / "image_prompts.md"),
        "videoPrompt": str(item_dir / "video_prompt.md"),
        "artifactsDir": str(item_dir / "artifacts"),
        "pptx": str(item_dir / f"{pptx_name}.pptx"),
        "video": str(item_dir / f"{video_name}.mp4"),
    }


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
    return re.sub(r"-+", "-", normalized).strip("-") or "oracle-task"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
