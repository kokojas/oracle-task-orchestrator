# Harness selection patterns

## 1. Single
Use when the task is short, tightly dependent, or easy to validate. Do not create orchestration solely because subagents are available.

## 2. Programmatic tool stage
Use when control flow is predictable and intermediate tool output can be reduced in code: parallel lookups, joins, filtering, deduplication, bounded retries, schema validation, or deterministic aggregation.

Keep semantic decisions, approvals, final citations, and native-artifact validation in the model/direct-tool layer.

## 3. Fan-out and synthesize
Use for independent evidence gathering or separable review dimensions. Give each agent a distinct question and output contract. Reconcile conflicts explicitly.

Examples:
- independent causes of a failure;
- separate documents or source families;
- correctness/security/testing review of one diff.

## 4. Adversarial verification
Use for high-impact conclusions where a plausible first answer may be wrong.

Roles:
1. generator proposes a conclusion;
2. falsifier searches for counterevidence, edge cases, or alternative explanations;
3. adjudicator sees claims and evidence, not model prestige, and resolves or marks disputes.

Do not substitute majority vote for evidence.

## 5. Generate → validate → repair loop
Use when the output has a strong verification surface: tests, compilation, schema, visual checks, source support, or measurable acceptance criteria.

Each iteration should change only the failed parts when practical. Bound retries and surface persistent failure.

## 6. Hybrid research harness
A useful default for difficult research:

1. compile the question into 2–4 material branches;
2. fan out independent research;
3. extract material claims;
4. run adversarial or Oracle checks only on high-impact/high-uncertainty claims;
5. synthesize with unresolved conflicts visible;
6. stop when additional research no longer changes material claims.

## Escalation heuristic

Escalate from single-agent only when expected benefit exceeds coordination cost. Useful signals:
- independent branches can run without shared mutable state;
- a conclusion is high-impact and uncertain;
- the task can fail silently without an independent verifier;
- external evidence or another model family can materially challenge the result;
- the task may outlive one context and needs resumable state.
