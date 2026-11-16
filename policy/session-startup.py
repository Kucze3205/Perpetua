#!/usr/bin/env python3
"""What a model session reads when it starts, how that is told to it, and when that is too much. The engine runs this in
the tokenless job (`python3 session-startup.py facts.json`) when it stages a session, and prints nothing else: one JSON
object {"reads": [path], "prompt": "<text>", "budget": {"status": "ok|over_budget|unmeasured", "tokens": <int or null>,
"detail": "<text>", "measurement": "<text>"}}. `reads` may only name paths of `documents`; the engine writes `prompt` as the
session's bootstrap prompt, and `.session/startup.json` from `budget`. The estimate is local, so no API key is needed. An
over-budget or unmeasured budget is a warning to the session and the Policymaker; it never blocks a session. Invalid output
fails the iteration and rolls `policy/` back.

facts.json: role, model, limit (`startup_token_warning` of `engine.yml`), shaper, declared (the `startup` list of the role's
SKILL.md or null), defaults (the staged state, capabilities, Policymaker rules and working memory this recipient has), fixed
(the task and the role skill), documents ({path: text} of the files above the recipient may read), problems (what the engine
could not offer), contract and prompt (the session's system text and task prompt), pending_notices (incidents the
Policymaker has not yet received).
"""
import json
import sys

facts = json.load(open(sys.argv[1], encoding="utf-8"))
documents = facts.get("documents", {})
declared = facts.get("declared")
errors = list(facts.get("problems", []))
if declared is not None and (not isinstance(declared, list) or any(not isinstance(path, str) for path in declared)):
    errors.append("startup must be a list of concrete workspace-relative file paths")
    declared = None
reads = []
for path in facts.get("fixed", []) + (facts.get("defaults", []) if declared is None else declared):
    if path in documents and path not in reads:
        reads.append(path)
for path in reads:
    if "\0" in documents[path] or "�" in documents[path]:
        errors.append("startup token counting requires UTF-8 text: " + path)

prompt = ("You are role `%s` of this loop. Read these startup documents once:\n%s\n"
          "Write .session/report.md. Startup diagnostics: .session/startup.json.\n"
          "Find historical inputs through .session/history/manifest.json when needed.\n\n" %
          (facts.get("role"), "\n".join("- " + json.dumps(path) for path in reads))) + facts.get("prompt", "")

budget = {"status": "unmeasured", "tokens": None, "detail": "",
          "measurement": "Estimate (4 bytes per token) of the contract, bootstrap and declared documents; "
                         "excludes this diagnostic output, CLI internals and later reads."}
if errors:
    budget["detail"] = "; ".join(errors)[:500]
else:
    text = facts.get("contract", "") + prompt + "".join("# %s\n\n%s" % (path, documents[path]) for path in reads)
    budget["tokens"] = -(-len(text.encode("utf-8")) // 4)
    budget["status"] = "over_budget" if budget["tokens"] > facts.get("limit", 100000) else "ok"
    if budget["status"] == "over_budget":
        budget["detail"] = "Reduce unnecessary startup reads"

if budget["status"] != "ok":
    prompt += "\nStartup budget warning (%s): read .session/startup.json; continue your task.\n" % budget["status"]
elif facts.get("role") == "policymaker" and facts.get("pending_notices"):
    prompt += "\nStartup notices need attention: read .session/startup.json and address them.\n"
print(json.dumps({"reads": reads, "prompt": prompt, "budget": budget}))
