#!/usr/bin/env python3
"""Which layer each role belongs to. The engine runs
this in the tokenless job (`python3 layer-rules.py facts.json`) for a change to `execution/` or `policy/` before it merges,
and prints nothing else: one JSON object {"errors": ["<why the definitions are refused>", ...]}. The engine has already
checked the catalog's structure (valid YAML, role names, one profile per role, no links) and the shaper (set, with a
complete execution profile), and adds these errors to its own;
an invalid answer fails the iteration and rolls `policy/` back. This script runs from the active `policy/`, so a change to
it takes effect for the next change, not for its own.

facts.json: activation (the definitions are about to become the active ones), shaper (the role name), policy_roles
(roles that must belong to the policy layer), roles ({role: {layer, agent, command, skill, skill_layers}}: `skill` is whether
the role has SKILL.md in its own layer, `skill_layers` the layers holding a directory for it).
"""
import json
import sys

facts = json.load(open(sys.argv[1], encoding="utf-8"))
roles = facts.get("roles", {})
errors = []
for role, r in sorted(roles.items()):
    if role in facts.get("policy_roles", []) and r["layer"] != "policy":
        errors.append("role `%s` must belong to the policy layer" % role)
    if r["skill_layers"] and r["skill_layers"] != [r["layer"]]:
        errors.append("role `%s` has a skill in the wrong or both layers" % role)
print(json.dumps({"errors": errors}))
