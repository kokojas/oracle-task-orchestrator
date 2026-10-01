---
name: codex-dynamic-harness
description: Compile difficult Codex tasks into a right-sized, task-specific execution harness that can use native subagents, programmatic tool orchestration, deterministic validators, and an optional Oracle MCP second-opinion or web-research backend. Use when the task is multi-step, benefits from independent parallel work or adversarial verification, has uncertain execution depth, or needs an explicit goal/evidence/stop contract. Do not use for simple single-agent tasks where orchestration would add overhead.
---

# Codex Dynamic Harness

Turn a difficult task into the smallest useful execution harness, run it with the capabilities actually available in the current Codex runtime, and preserve enough structured state to verify and resume the work.

## Core rule

Do not orchestrate by default. First decide whether a single Codex agent with direct tools can complete the task reliably. Escalate only when parallelism, independent verification, long-running state, or cross-model evidence materially improves the result.

## Workflow

1. **Discover capabilities before designing the harness.**
   - Identify whether native multi-agent delegation is available.
   - Identify whether programmatic tool orchestration / `exec` is available.
   - Identify relevant MCP servers, especially an Oracle server exposing tools such as `consult`, `wait`, `sessions`, or image-generation helpers.
   - Identify deterministic local validators, tests, linters, schemas, or repository-specific commands.
   - Never assume a capability exists because it is documented for another OpenAI runtime.

2. **Compile a compact goal contract.**
   Define:
   - outcome;
   - material constraints and non-goals;
   - evidence or artifact required to prove completion;
   - allowed side effects and approval boundaries;
   - stop conditions;
   - maximum useful parallelism / retries.

3. **Choose the smallest harness mode.**
   Use the decision rules in `references/patterns.md`.
   - `single`: one agent, direct tools.
   - `program`: bounded predictable tool graph; use programmatic orchestration when available.
   - `fanout`: independent subagents, followed by synthesis.
   - `adversarial`: generator / falsifier / adjudicator for material claims or architecture decisions.
   - `loop`: iterative generate → validate → repair until an observable stop condition is met.
   - `hybrid`: combine modes only when each added layer has a distinct verification role.

4. **Write a harness plan before expensive execution.**
   For non-trivial harnesses, materialize a JSON plan conforming to `references/harness-schema.md`. Keep it under a task-local state directory when filesystem access exists. Validate it with `scripts/validate_plan.py` when possible.

5. **Execute with capability fallbacks.**
   - Native subagents: delegate independent questions with explicit expected outputs and no overlapping write scopes.
   - Programmatic tool calling: use only for bounded control flow where code can safely parallelize, filter, join, retry, or validate tool outputs. Keep semantic judgment and approval-sensitive writes outside the program.
   - Oracle MCP: use as an external provider adapter when a genuinely independent model/browser perspective, web research, Deep Research, or browser-generated artifact adds value. Prefer one precise consult over repeated vague calls. For long Oracle runs, retain the returned session identifier and reattach/wait rather than launching duplicates.
   - No advanced capability: execute the same plan sequentially in the root Codex agent; preserve node boundaries and evidence states.

6. **Verify before synthesis.**
   - Prefer deterministic checks first.
   - Verify material claims or risky design decisions independently when practical.
   - Record unresolved conflicts instead of majority-voting them away.
   - Do not let the same generative pass be the only acceptance test for its own output.

7. **Stop on evidence, not exhaustion.**
   Stop when the goal contract is satisfied, required validators pass, material contradictions are resolved or explicitly marked unresolved, and another iteration is unlikely to change the decision materially. Stop earlier when blocked by missing permission, unavailable capability, budget, or required evidence.

## Oracle policy

Treat Oracle as a provider adapter, not the orchestrator.

Use Oracle when at least one is true:
- browser-backed ChatGPT/Gemini capabilities are specifically useful;
- independent web research or a second model family can falsify a material conclusion;
- a signed-in browser subscription provides a capability unavailable in the current Codex runtime;
- a browser-generated artifact is part of the task.

Do not call Oracle merely to make the harness look multi-model. The root Codex harness owns decomposition, state, stop conditions, and final adjudication.

See `references/oracle-adapter.md` for routing details.

## State and resumability

When filesystem access exists, prefer external state over conversation memory:

```text
.harness/<task-slug>/
├── plan.json
├── status.json
├── evidence.jsonl
├── conflicts.jsonl
└── artifacts/
```

Write only what materially improves recovery, auditability, or validation. Do not create this structure for small tasks.

## Safety and write boundaries

- Parallel agents must not edit the same files unless the plan explicitly serializes their changes.
- Side-effecting external actions require the same user authorization they would require without the harness.
- Generated JavaScript/programmatic orchestration is not an authorization boundary.
- Prefer direct tool calls for approval-sensitive writes and final citation/native-artifact validation.
- Never hide a failed validator or unresolved evidence conflict behind a synthesized answer.

## Acceptance

A dynamic harness is successful only if it improves at least one of: correctness, evidence coverage, latency through safe parallelism, recoverability, or independent verification without creating disproportionate orchestration cost.

If a simpler single-agent execution would be equally reliable, use it instead.
