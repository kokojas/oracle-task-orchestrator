# oracle-task-orchestrator

Resumable batches for presentation, image, video and research workflows.

## What it does

- One work item per independent Oracle run
- Dry-run preview and resumable batch state
- Image retry queue with rate-limit handling
- Editable PPTX build step and optional local image upscaling

## Quick start

Create a batch with `python3 scripts/create_batch.py --brief "Your brief" --artifact presentation --items 1 --output-root ./oracle-runs`; preview with `python3 scripts/run_batch.py --batch /path/to/batch.json --dry-run`, then run with `--resume`.

## Browse the repository

- `SKILL.md` — workflow specification
- `agents` — agent metadata
- `scripts` — implementation scripts

## Compatibility

Python 3, Node.js, Oracle CLI; `scripts/build_pptx.js` requires `pptxgenjs`. Browser jobs require sign-in. Set `ORACLE_CHATGPT_URL` for a particular project.

## Quality checks

Python and JavaScript syntax checks passed. Dry-run before a live batch, then inspect slide specifications and generated files.

## Security and privacy

Inspect source files and your own input data before running or publishing outputs. Do not commit credentials, browser cookies, client documents or generated work directories.

## Limits

External model access, quotas and browser changes affect live runs. The default ChatGPT URL is generic; project isolation requires `ORACLE_CHATGPT_URL`.
