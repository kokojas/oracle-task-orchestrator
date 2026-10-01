#!/usr/bin/env python3
from __future__ import annotations

import unittest

from validate_plan import validate


def valid_plan():
    return {
        "schemaVersion": 1,
        "task": "research",
        "goal": {
            "outcome": "verified report",
            "constraints": [],
            "nonGoals": [],
            "evidenceRequired": ["sources"],
            "stopConditions": ["material claims verified"],
        },
        "capabilities": {"multiAgent": True, "programmaticTools": True, "oracleMcp": True},
        "limits": {"maxParallel": 3, "maxRetriesPerNode": 1},
        "nodes": [
            {
                "id": "research",
                "kind": "agent",
                "role": "researcher",
                "dependsOn": [],
                "provider": "codex",
                "objective": "research independently",
                "expectedOutput": "findings",
                "writeScope": [],
                "requiresApproval": False,
            },
            {
                "id": "oracle",
                "kind": "oracle-consult",
                "role": "falsifier",
                "dependsOn": ["research"],
                "provider": "oracle",
                "objective": "challenge material claims",
                "expectedOutput": "counterevidence",
                "writeScope": [],
                "requiresApproval": False,
            },
            {
                "id": "synthesis",
                "kind": "synthesis",
                "role": "adjudicator",
                "dependsOn": ["research", "oracle"],
                "provider": "codex",
                "objective": "reconcile evidence",
                "expectedOutput": "final report",
                "writeScope": [],
                "requiresApproval": False,
            },
        ],
        "final": {
            "synthesizer": "synthesis",
            "acceptanceCriteria": ["claims have evidence states"],
        },
    }


class ValidatePlanTests(unittest.TestCase):
    def test_valid_plan(self):
        self.assertEqual(validate(valid_plan()), [])

    def test_cycle_is_rejected(self):
        plan = valid_plan()
        plan["nodes"][0]["dependsOn"] = ["synthesis"]
        errors = validate(plan)
        self.assertTrue(any("cycle" in e for e in errors), errors)

    def test_missing_dependency_is_rejected(self):
        plan = valid_plan()
        plan["nodes"][0]["dependsOn"] = ["missing"]
        errors = validate(plan)
        self.assertTrue(any("missing node" in e for e in errors), errors)

    def test_oracle_requires_capability_or_fallback(self):
        plan = valid_plan()
        plan["capabilities"]["oracleMcp"] = False
        errors = validate(plan)
        self.assertTrue(any("oracle-consult" in e for e in errors), errors)
        plan["nodes"][1]["fallback"] = "run a native Codex falsifier subagent"
        self.assertEqual(validate(plan), [])

    def test_acceptance_criteria_required(self):
        plan = valid_plan()
        plan["final"]["acceptanceCriteria"] = []
        errors = validate(plan)
        self.assertTrue(any("acceptanceCriteria" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
