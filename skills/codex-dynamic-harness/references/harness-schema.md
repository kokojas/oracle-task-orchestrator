# Harness plan schema

Use this JSON shape for non-trivial compiled harnesses. Fields may be extended when needed, but preserve the core contract so plans remain inspectable and resumable.

```json
{
  "schemaVersion": 1,
  "task": "short task label",
  "goal": {
    "outcome": "observable desired result",
    "constraints": ["material constraint"],
    "nonGoals": ["explicitly excluded work"],
    "evidenceRequired": ["test, source, artifact, or validator"],
    "stopConditions": ["observable completion condition"]
  },
  "capabilities": {
    "multiAgent": true,
    "programmaticTools": true,
    "oracleMcp": false
  },
  "limits": {
    "maxParallel": 3,
    "maxRetriesPerNode": 1
  },
  "nodes": [
    {
      "id": "research-a",
      "kind": "agent",
      "role": "independent researcher",
      "dependsOn": [],
      "provider": "codex",
      "objective": "answer one bounded question",
      "expectedOutput": "structured findings with evidence",
      "writeScope": [],
      "requiresApproval": false
    }
  ],
  "final": {
    "synthesizer": "synthesis",
    "acceptanceCriteria": ["all material claims have evidence states"]
  }
}
```

## Node kinds

- `agent`: independent semantic work, preferably native Codex subagent when available.
- `tool-stage`: bounded predictable orchestration of tools; suitable for programmatic tool calling.
- `oracle-consult`: external Oracle MCP consultation/research/artifact request.
- `validator`: deterministic or independent verification.
- `synthesis`: final reconciliation and answer/artifact production.

## Plan invariants

- Node IDs are unique.
- Every dependency names an existing node.
- The dependency graph is acyclic.
- At least one stop condition and one final acceptance criterion exist.
- Nodes with external side effects set `requiresApproval: true` unless the action was already explicitly authorized.
- Parallel editing nodes must have disjoint `writeScope` values.
- `provider: oracle` is valid only when Oracle MCP was discovered or the plan explicitly marks it unavailable and provides a fallback.
