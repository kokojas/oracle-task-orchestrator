---
name: oracle-task-orchestrator
description: Run Oracle as a multitask prompt-chain orchestrator for batches of presentations, images, videos, text, or research outputs.
metadata:
  short-description: Oracle batch orchestrator for parallel prompt chains
---

# Oracle Task Orchestrator

Use this skill to turn one user brief into a resumable Oracle batch. The default use case is many editable PowerPoint decks: one work item equals one independent Oracle browser/API run, with a prompt chain inside that run when the selected engine supports it.

## Local Output Root

All new generations and all material artifacts created through this skill must be saved locally under `$HOME/Downloads/Codex (1)`.

- Batch runs must use `$HOME/Downloads/Codex (1)/oracle-runs` as the output root.
- Always pass `--output-root "$HOME/Downloads/Codex (1)/oracle-runs"` when creating a batch unless the user explicitly requests a different local folder.
- Keep generated images, videos, decks, transcripts, logs, `jobs.json`, and retry outputs inside the relevant batch directory under that root. Write upscale derivatives to `$HOME/Downloads/<topic_name>_upscale`.
- Do not use `~/Documents/Codex` or any cloud/sync folder for new skill-generated artifacts unless the user explicitly overrides this policy.

## ChatGPT Project Isolation

All ChatGPT browser generations created through this skill must open the isolated ChatGPT project URL:

```text
https://chatgpt.com/
```

- Pass this URL to Oracle with `--chatgpt-url "https://chatgpt.com/"` for ChatGPT browser runs.
- The local `drive_images.py` and `run_batch.py` scripts default to this URL through `ORACLE_CHATGPT_URL`; override that environment variable only when the user explicitly requests a different isolated ChatGPT project/folder.
- Keep generated browser conversations and image generations for a task inside the requested isolated ChatGPT project instead of the default ChatGPT home context.

## Internet Reference Requirement For Visuals

For every image, presentation image, product mockup, visual slide asset, video visual prompt, or other visual material generated through this skill, the prompt sent to the active web UI (ChatGPT or Gemini Web) must explicitly instruct the model to search the internet first and base the visual generation exclusively on internet references.

Include this requirement directly in the generation prompt, not only in surrounding notes:

```text
Before generating, search the internet in the active web UI (ChatGPT or Gemini Web) for current, relevant visual references. Base the image exclusively on those internet references and summarize the reference basis. Do not rely on memory-only references.
```

For real products, brands, places, people, current events, fashion, architecture, or dated materials, prefer official or high-quality current web references and synthesize them without copying a single source image too closely.

## Core Workflow

1. Convert the user brief into concrete work items.
   - If the user gives only a short task, the assistant should decompose it into item titles/briefs first.
   - Default to 5 concurrent jobs (`--concurrency 5`). Concurrency map: 5 for batch start; `drive_images.py` drops to 3 by design after a rate-limit cooldown; reduce to 2 only when the ChatGPT/Gemini browser UI or account quotas become unstable.
   - For a single image or a small image-only request, use the lightweight image helper below instead of creating a full batch.
2. Create the batch:
   ```bash
   python3 scripts/create_batch.py \
     --brief "USER BRIEF" \
     --artifact presentation \
     --items 10 \
     --concurrency 5 \
     --output-root "$HOME/Downloads/Codex (1)/oracle-runs" \
     --items-file /path/to/items.json
   ```
   For Gemini video batches, use `--artifact video`; `create_batch.py` defaults that artifact to `--model gemini-3.1-pro`.
3. Preview without touching ChatGPT:
   ```bash
   python3 scripts/run_batch.py \
     --batch /path/to/batch.json \
     --dry-run \
     --concurrency 5
   ```
4. Run or resume:
   ```bash
   python3 scripts/run_batch.py \
     --batch /path/to/batch.json \
     --resume \
     --concurrency 5
   ```
5. Retry missing ChatGPT image artifacts with the adaptive driver (preferred over `--retry-images`):
   ```bash
   python3 scripts/drive_images.py \
     /path/to/jobs.json \
     --concurrency 3 \
     --stagger 8 \
     --logdir /path/to/batch/_drive_logs
   ```
   Build `jobs.json` from items that still miss images: one entry per missing image, with `target` inside the item's `artifacts/` dir and `prompt` taken from `image_prompts.md` / `slide_spec.json`. Keep `run_batch.py --retry-images` only as a fallback when a single oracle retry per item is enough.
6. Retry only missing Gemini video artifacts:
   ```bash
   python3 scripts/run_batch.py \
     --batch /path/to/batch.json \
     --retry-videos \
     --concurrency 5
   ```

## Lightweight Image Helper

## Gemini GUI Access Check

Gemini video work uses Oracle browser mode against Gemini Web/cookies, not the standalone Gemini API CLI. Before an expensive Gemini/Veo batch, run this smoke test:

```bash
oracle --engine browser \
  --model gemini-3.1-pro \
  --prompt "Reply only: OK" \
  --slug "gemini-access-smoke" \
  --write-output /tmp/oracle-gemini-access-smoke.md \
  --wait \
  --browser-manual-login \
  --timeout 90s \
  --force
```

Treat a saved `OK` as proof that the Gemini Web path is logged in and usable. If Oracle prints model-selection evidence such as `resolved=(unavailable)` or `status=unavailable`, do a one-item smoke run before any large video batch; the account may answer through Gemini Web while the exact requested model label is not verifiable in the UI. Do not test this path with `gemini -p --model gemini-3.1-pro`: that command uses the standalone Gemini API CLI and may fail with `ModelNotFoundError` even when Gemini Web through Oracle works.

For parallel Gemini Web/GUI runs, do not start several `--browser-manual-login` launches at once unless a smoke test proves it is stable; concurrent persistent-profile launches can fail with `connect ECONNREFUSED 127.0.0.1:<port>`. Preferred parallel mode requires an inline cookies file containing valid Google cookies, including `__Secure-1PSID` and `__Secure-1PSIDTS`: `oracle --engine browser --model gemini-3.1-pro --browser-inline-cookies-file "$HOME/.oracle/gemini-cookies.json" --prompt "PROMPT" --slug "gemini-parallel-item" --write-output "/path/to/output.md" --wait --timeout 12m --force`. If no valid inline cookies file exists, use sequential or lightly staggered `--browser-manual-login` runs instead. Do not combine `--copy-profile` with `--browser-manual-login`; Oracle rejects that combination. `--copy-profile` or `--browser-cookie-path` can still fail on macOS when cookie extraction cannot read the required Google cookies, so treat them as secondary options, not the default.

Oracle v0.21.3 is the supported engine baseline for this skill (latest GitHub release checked 2026-09-29). ChatGPT browser runs support the unified Intelligence picker and the explicit `gpt-5.6-sol` model alias. Pass `--browser-thinking-time high` when the chosen model exposes High; verify the selected model/effort in the run log or visible composer before interpreting results. The advanced browser flags, including `--browser-manual-login` and auto-reattach, are listed by `oracle --debug-help` rather than the short help. Gemini browser/cookie routing still needs smoke verification: treat `gemini-3.1-pro[browser]` logs as routing evidence, not final model proof. The browser-only Deep Think path is `--model gemini-3-deep-think`; if Oracle cannot verify Pro/extended mode in the visible Gemini UI, use record/replay or direct browser automation to click `3.1 Pro` and `Рівень міркування -> Розширений`, then verify the composer pill shows `Pro Розширений` before submitting.

For `artifact=image` requests that only need one final image or a small direct image generation, prefer `scripts/run_image.py` over `create_batch.py` + manual `jobs.json`.

This helper creates only:

- `jobs.json`
- `run_manifest.json`
- `artifacts/<filename>.png`
- `_drive_logs/<job-slug>.log`

It still uses `scripts/drive_images.py` internally, so rate-limit handling, resumability, and PNG validation remain the same. It also automatically prepends the mandatory active-web-UI internet-reference instruction when the prompt does not already include it.

Example:

```bash
python3 scripts/run_image.py \
  --task-slug "rectangle-liquid-glass-image" \
  --filename "rectangle-liquid-glass.png" \
  --prompt "Generate one polished liquid glass image inspired by the Rectangle macOS window-management app." \
  --concurrency 1 \
  --stagger 0
```

Preview without writing files or launching Oracle:

```bash
python3 scripts/run_image.py \
  --task-slug "preview-image" \
  --prompt "Generate one image." \
  --dry-run
```

Use the full batch workflow instead when the task needs multiple independent work items, prompt chains, presentation/video outputs, source-file attachment, or batch-level resume/status tracking.

## Output Contract

Batch root defaults to `$HOME/Downloads/Codex (1)/oracle-runs/<task-slug>/<run-id>/`.

Lightweight image root defaults to `$HOME/Downloads/Codex (1)/oracle-runs/<task-slug>/<run-id>/`.

Each batch contains:

- `batch.json`: batch metadata, defaults, work items, and run status.
- `items/<item-id>/chain.json`: prompt chain for the item.
- `items/<item-id>/manifest.json`: command, status, timestamps, and output paths.
- `items/<item-id>/chat.md`: Oracle answer/transcript captured by `--write-output`.
- `items/<item-id>/slide_spec.json`: presentation schema extracted from the Oracle answer.
- `items/<item-id>/image_prompts.md`: image prompts for retry/manual generation.
- `items/<item-id>/video_prompt.md`: Gemini/Veo video prompt and retry notes when artifact is `video`.
- `items/<item-id>/artifacts/`: generated/copied browser artifacts.
- `items/<item-id>/<title>.pptx`: editable PowerPoint deck when artifact is `presentation`.
- `items/<item-id>/<title>.mp4`: intended final video path when artifact is `video` and a downloadable video artifact is available.

## Image Retry Driver

`scripts/drive_images.py` is the default mechanism for generating or retrying ChatGPT image artifacts in bulk. It wraps one oracle browser call per image and adds what plain `--retry-images` lacks:

- Staged rate-limit ladder: banner detected → 7 min pause → single probe attempt → 10 min pause → resume at concurrency 3. A success resets the ladder; a recurring banner restarts it from the first break.
- Throttle detection: rate-limit phrases (including ChatGPT Plus image-quota messages) or a suspiciously fast no-image return (<45 s) are requeued; genuine refusals (e.g. content filter) are marked FAIL and not retried.
- Model-switcher guard: if the ChatGPT UI no longer shows the configured model label, the run aborts; relaunch with `ORACLE_IMG_MODEL=<label from the ChatGPT model menu>`.
- Resumability: a target counts as done when it is a valid PNG >1KB with a PNG header; rerun the same command to continue. Exit code 2 means non-rate-limit failures (see per-image logs in `--logdir`).
- IPv4-first DNS is forced for oracle subprocesses to avoid a Node/undici `setTypeOfService EINVAL` crash on macOS IPv6 sockets.

`jobs.json` shape:

```json
[
  {
    "target": "$HOME/Downloads/Codex (1)/oracle-runs/<task-slug>/<run-id>/items/<item-id>/artifacts/01.png",
    "slug": "<item-id>-img-01",
    "prompt": "Before generating, search the internet in the active web UI (ChatGPT or Gemini Web) for current, relevant visual references. Base the image exclusively on those internet references and summarize the reference basis. Do not rely on memory-only references. Then generate: image prompt text"
  }
]
```

After images land in `artifacts/`, rebuild the affected deck:

```bash
node scripts/build_pptx.js \
  --spec /path/to/items/<item-id>/slide_spec.json \
  --out "/path/to/items/<item-id>/<title>.pptx" \
  --assets-dir /path/to/items/<item-id>/artifacts
```

## Upscale Workflow

After image artifacts are complete, upscale them locally with the Upscayl CLI (deterministic, zero tokens, resumable):

```bash
python3 scripts/upscale_images.py \
  --input-dir /path/to/items/<item-id>/artifacts \
  --output-dir $HOME/Downloads/<topic_name>_upscale \
  --model ultramix-balanced-4x --scale 4 \
  --jpeg --quality 100
```

- Defaults: model `ultramix-balanced-4x`, `--scale 4`, JPEG output enabled, and `--quality 100`. The default final file is an Adobe Stock-compatible JPEG named `<stem>_4x.jpg` in `$HOME/Downloads/<topic_name>_upscale/`.
- JPEG compression uses MozJPEG-compatible `cjpeg` with optimized Huffman tables. Install with `brew install mozjpeg` or pass `--mozjpeg-bin /path/to/cjpeg`; the script records the encoder path and method in `upscale_manifest.json`.
- Use `--png` only when a PNG final is explicitly needed. Use `--keep-png` to retain the intermediate PNG master next to the JPEG.
- Requires the Upscayl desktop app; the binary and models are auto-detected at `/Applications/Upscayl.app/Contents/Resources/` (override with `UPSCAYL_BIN` / `UPSCAYL_MODELS`).
- Outputs are validated by magic header and size; rerunning the same command skips finished files. Use `--dry-run` to preview commands and `--limit 1` for a smoke run before large batches. Pass `--exclude '_*'` (repeatable fnmatch glob) to skip service files such as `_contact_sheet.png`.
- Stock platforms such as Adobe Stock require JPEG, min 4 MP — the script prints megapixels and warns below 4 MP.
- `upscale_manifest.json` in the output directory records settings and per-file status; exit code 2 means at least one failure.
- Expect roughly 1-3 minutes per image on `ultramix-balanced-4x` depending on source size and GPU. Upscaling is GPU-bound, so jobs run sequentially.

## Operating Defaults

- Oracle engine baseline: GitHub release `v0.21.3` (`oracle --version` should report `0.21.3`); recheck the upstream release before later upgrades.
- Model policy: existing image/deck workflows default to `gpt-5.5` with `--browser-thinking-time high`. For explicitly requested frontier-model reasoning evaluations, set `--model gpt-5.6-sol` with the same effort on every arm and verify browser selection. Do not mix model versions or effort levels within a comparison.
- Local output policy: all newly created skill artifacts must remain under `$HOME/Downloads/Codex (1)`, with Oracle batches under `$HOME/Downloads/Codex (1)/oracle-runs`.
- Visual reference policy: every prompt that asks ChatGPT or Gemini Web to generate an image, video, or visual asset must include the mandatory internet-reference instruction from the section above.
- Single-image policy: for simple image-only requests, prefer `scripts/run_image.py`; use `create_batch.py --artifact image` only when full batch metadata or prompt-chain behavior is actually needed.
- Upscale/export policy: for stock-ready postprocessing, default to `ultramix-balanced-4x --scale 4` followed by MozJPEG-compatible JPEG export at `--quality 100`, written to `$HOME/Downloads/<topic_name>_upscale`.
- Browser mode defaults for `run_batch.py`: `--engine browser --model gpt-5.5 --browser-thinking-time high --manual-login` (`--manual-login` is on by default). Note: `--browser-manual-login` is the flag of the raw `oracle` CLI only; `run_batch.py` and `run_chain.py` expose it as `--manual-login`.
- Gemini GUI smoke-test command: `oracle --engine browser --model gemini-3.1-pro --prompt "Reply only: OK" --slug "gemini-access-smoke" --write-output /tmp/oracle-gemini-access-smoke.md --wait --browser-manual-login --timeout 90s --force`.
- Video batches default to Gemini browser mode: `--engine browser --model gemini-3.1-pro`.
- `gemini-3-pro` is legacy; prefer `gemini-3.1-pro` for Gemini Web/GUI work unless a smoke test shows the account only exposes a different Gemini label.
- ChatGPT image generation/retry uses `gpt-5.5` + `--browser-thinking-time high` (`drive_images.py` default; override with `ORACLE_IMG_MODEL` / `ORACLE_IMG_THINKING_TIME` only if the ChatGPT model menu label differs or the user explicitly asks).
- The Oracle profile is `~/.oracle/browser-profile`.
- Browser runs use one Oracle command per item with repeated `--browser-follow-up` prompts to keep the chain in one ChatGPT conversation.
- If ChatGPT image artifacts are not captured, the item is marked `needs_image_retry`; PPTX generation still proceeds with editable image placeholders.
- For `needs_image_retry` items, prefer `scripts/drive_images.py` (adaptive rate-limit ladder, resumable, per-image queue); `--retry-images` remains a simple per-item fallback that does not survive ChatGPT throttling.
- If Gemini video generation does not expose a downloadable `.mp4`, `.mov`, `.m4v`, or `.webm` artifact through Oracle, the item is marked `needs_video_retry` and `video_prompt.md` is saved for manual or future automated Veo retry.
- Use `--retry-videos` to rerun only `needs_video_retry` items.
- API mode is allowed for text/research only when the user explicitly requests it or when browser mode is unsuitable.
- For text/research evaluations where ChatGPT browser model selection fails, use API mode with `--reasoning-effort` and `--reasoning-mode` recorded in the batch defaults. The batch scripts pass these flags through to Oracle v0.21.3. Keep the task text, model, effort, tools, and scoring rubric identical across comparison arms; label API results as API results rather than ChatGPT UI results.

## Items File Shape

`--items-file` can be a JSON array or an object with an `items` array:

```json
[
  {
    "title": "Deck 01 title",
    "brief": "Specific objective, audience, sources, and constraints.",
    "audience": "optional",
    "style": "optional",
    "durationSeconds": 8,
    "aspect": "16:9"
  }
]
```

When no items file is supplied, `create_batch.py` creates numbered placeholders from the brief. Replace or edit them before running expensive browser jobs.

## Quality Rules

- Do not start a 10-item live browser batch before a dry run and a 1-item smoke run.
- Keep source files explicit with `--file`; Oracle starts without project context.
- For presentation batches, inspect `slide_spec.json` and the generated `.pptx` before treating the batch as final.
- Use `--resume`; do not rerun completed items unless the user asks for regeneration.
