# Oracle MCP adapter

Oracle is an optional external provider in this skill. It does not own the task graph.

## Discovery

Look for an Oracle MCP server and inspect the tools actually exposed. Common capabilities may include:
- `consult` for browser/API model consultation;
- `wait` for long detached runs;
- `sessions` for durable session inspection;
- image-generation helpers;
- project-source management.

Tool names and arguments can change. Use discovered schemas rather than hard-coding undocumented arguments.

## Routing

Use Oracle for:
- an independent second opinion from a browser-backed model;
- current public-web research when the Oracle browser mode is the intended evidence source;
- long research that benefits from durable Oracle session state;
- browser-only generation such as ChatGPT image artifacts.

Avoid Oracle for:
- deterministic file transformations;
- tasks the root Codex agent can validate directly;
- redundant "consensus" calls with no distinct evidence role.

## Long runs

When Oracle supports detached execution:
1. launch once;
2. store the returned session ID in task state;
3. continue independent work;
4. reattach/wait on the same session;
5. do not relaunch the same expensive prompt unless the prior run failed materially.

## Evidence contract

An Oracle result is evidence from another provider, not ground truth. For material factual claims, preserve its sources or verification surface. When Oracle and Codex disagree, compare evidence and assumptions rather than model identity.
