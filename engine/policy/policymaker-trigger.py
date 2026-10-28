#!/usr/bin/env python3
"""When the Policymaker is called next. The engine runs this in the tokenless job (`python3 policymaker-trigger.py facts.json`)
and prints nothing else: one JSON object, {"reason": "<why>"} or {"reason": null}. The engine adds its own rules (one open
Policymaker at a time; every iteration until the first session ended `done`) and validates the output; invalid output fails
the iteration and rolls `policy/` back.

facts.json: iteration, no_progress, audit_changed, new_suggestions, startup_pending, triggers (`policymaker_triggers` of
`policy/params.yml`: every_iterations, no_progress).
"""
import json
import sys

facts = json.load(open(sys.argv[1], encoding="utf-8"))
t = facts.get("triggers") or {}
n, k = facts.get("iteration"), facts.get("no_progress")
reason = None
if t.get("every_iterations") and n and n % t["every_iterations"] == 0:
    reason = "every %s iterations" % t["every_iterations"]
elif t.get("no_progress") and k and k % t["no_progress"] == 0:
    reason = "%s iterations without progress" % k
elif facts.get("audit_changed"):
    reason = "a new audit report"
elif facts.get("new_suggestions"):
    reason = "new session suggestions"
elif facts.get("startup_pending"):
    reason = "startup budget needs attention"
print(json.dumps({"reason": reason}))
