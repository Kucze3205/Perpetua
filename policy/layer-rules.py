#!/usr/bin/env python3
"""Which layer each role belongs to, and whether managed definitions are complete enough to become active. The engine runs
this in the tokenless job (`python3 layer-rules.py facts.json`) for a change to `execution/` or `policy/` before it merges,
and prints nothing else: one JSON object {"errors": ["<why the definitions are refused>", ...]}. The engine has already
checked the catalog's structure (valid YAML, role names, one profile per role, no links) and adds nothing to these errors;
an invalid answer fails the iteration and rolls `policy/` back. This script runs from the active `policy/`, so a change to
it takes effect for the next change, not for its own.

facts.json: activation (the definitions are about to become the active ones), shaper (role name or null), policy_roles
(roles that must belong to the policy layer), roles ({role: {layer, agent, command, skill, skill_layers}}: `skill` is whether
the role has SKILL.md in its own layer, `skill_layers` the layers holding a directory for it).
"""
import json
import sys

facts = json.load(open(sys.argv[1], encoding="utf-8"))
roles, shaper = facts.get("roles", {}), facts.get("shaper")
errors = []
for role, r in sorted(roles.items()):
    if role in facts.get("policy_roles", []) and r["layer"] != "policy":
        errors.append("role `%s` must belong to the policy layer" % role)
    if role == shaper and r["layer"] == "policy":
        errors.append("policy role `%s` cannot be the execution shaper" % role)
    if r["skill_layers"] and r["skill_layers"] != [r["layer"]]:
        errors.append("role `%s` has a skill in the wrong or both layers" % role)
if facts.get("activation") and shaper is not None:
    r = roles.get(shaper)
    if r is None or r["layer"] != "execution":
        errors.append("shaper `%s` has no execution profile" % shaper)
    elif r["agent"] and not r["skill"]:
        errors.append("shaper `%s` has no complete local skill" % shaper)
    elif not r["agent"] and not r["command"]:
        errors.append("script shaper `%s` has no command" % shaper)
print(json.dumps({"errors": errors}))
