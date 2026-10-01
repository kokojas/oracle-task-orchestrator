#!/usr/bin/env python3
"""Validate a Codex Dynamic Harness plan JSON file."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

VALID_KINDS = {"agent", "tool-stage", "oracle-consult", "validator", "synthesis"}


def _nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and len(value) > 0


def validate(plan: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    if plan.get("schemaVersion") != 1:
        errors.append("schemaVersion must equal 1")

    goal = plan.get("goal")
    if not isinstance(goal, dict):
        errors.append("goal must be an object")
    else:
        if not isinstance(goal.get("outcome"), str) or not goal["outcome"].strip():
            errors.append("goal.outcome must be a non-empty string")
        if not _nonempty_list(goal.get("stopConditions")):
            errors.append("goal.stopConditions must contain at least one condition")

    final = plan.get("final")
    if not isinstance(final, dict):
        errors.append("final must be an object")
    elif not _nonempty_list(final.get("acceptanceCriteria")):
        errors.append("final.acceptanceCriteria must contain at least one criterion")

    nodes = plan.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        errors.append("nodes must be a non-empty array")
        return errors

    ids: list[str] = []
    node_by_id: dict[str, dict[str, Any]] = {}
    for index, node in enumerate(nodes):
        prefix = f"nodes[{index}]"
        if not isinstance(node, dict):
            errors.append(f"{prefix} must be an object")
            continue
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id.strip():
            errors.append(f"{prefix}.id must be a non-empty string")
            continue
        if node_id in node_by_id:
            errors.append(f"duplicate node id: {node_id}")
        ids.append(node_id)
        node_by_id[node_id] = node
        if node.get("kind") not in VALID_KINDS:
            errors.append(f"{prefix}.kind must be one of {sorted(VALID_KINDS)}")
        if not isinstance(node.get("objective"), str) or not node["objective"].strip():
            errors.append(f"{prefix}.objective must be a non-empty string")
        deps = node.get("dependsOn", [])
        if not isinstance(deps, list) or not all(isinstance(x, str) for x in deps):
            errors.append(f"{prefix}.dependsOn must be an array of node ids")

    known = set(node_by_id)
    for node_id, node in node_by_id.items():
        for dep in node.get("dependsOn", []):
            if dep not in known:
                errors.append(f"node {node_id} depends on missing node {dep}")
            if dep == node_id:
                errors.append(f"node {node_id} cannot depend on itself")

    # Cycle detection.
    state: dict[str, int] = {node_id: 0 for node_id in known}

    def visit(node_id: str, stack: list[str]) -> None:
        if state[node_id] == 1:
            cycle = " -> ".join(stack + [node_id])
            errors.append(f"dependency cycle detected: {cycle}")
            return
        if state[node_id] == 2:
            return
        state[node_id] = 1
        for dep in node_by_id[node_id].get("dependsOn", []):
            if dep in known:
                visit(dep, stack + [node_id])
        state[node_id] = 2

    for node_id in known:
        if state[node_id] == 0:
            visit(node_id, [])

    synthesizer = final.get("synthesizer") if isinstance(final, dict) else None
    if synthesizer is not None and synthesizer not in known:
        errors.append(f"final.synthesizer references missing node {synthesizer}")

    # Warn-as-error on impossible Oracle declaration to keep plans explicit.
    capabilities = plan.get("capabilities", {})
    oracle_available = isinstance(capabilities, dict) and capabilities.get("oracleMcp") is True
    for node_id, node in node_by_id.items():
        if node.get("kind") == "oracle-consult" and not oracle_available:
            fallback = node.get("fallback")
            if not isinstance(fallback, str) or not fallback.strip():
                errors.append(
                    f"node {node_id} uses oracle-consult while capabilities.oracleMcp is not true; "
                    "add a non-empty fallback"
                )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()

    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if not isinstance(plan, dict):
        print("ERROR: top-level JSON must be an object", file=sys.stderr)
        return 2

    errors = validate(plan)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    print("OK: harness plan is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
