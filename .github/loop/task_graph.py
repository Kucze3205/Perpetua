"""Task graph validation, issue creation, dependencies, inputs and frozen storage.

Iteration controls phases and launches sessions after this module has saved the graph.
GitHub operations stay on common so the same graph runs against FakeHub in tests.
"""
import json
import re

import common as c
import owner_requests as requests
import session_exports


def tasks_path(n):
    return "iterations/%04d/tasks.json" % n


def read(n):
    """The frozen tasks of an iteration, or an empty list before any are placed."""
    try:
        with open(tasks_path(n), encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return []
    return (json.loads(text) or []) if text else []


MAX_TASKS = 50          # the kernel's cap on the tasks of one iteration's graph: shaper output and Policymaker orders alike
TASK_FIELDS = {"id", "title", "role", "body", "model", "effort", "blocked_by", "inputs", "exports", "start_branch", "limits", "consent"}


def validate_tasks(doc, ordered=False, taken=()):
    """Errors of a task graph (empty: valid), the engine's own; `iteration.validate_tasks` adds those of `policy/graph-rules.py`. `ordered`: the Policymaker's orders, the only tasks of the roles in
    `policymaker_only_roles` and only of them, unless no shaper is set: then it orders any role. `taken`: ids the iteration already uses."""
    profiles, parameters = c.profiles(), c.params()
    policymaker_roles = parameters["policymaker_only_roles"]
    tasks = doc.get("tasks") if isinstance(doc, dict) else None
    if not isinstance(tasks, list) or not tasks:
        return ["`tasks` must be a non-empty list"]
    if len(tasks) + len(taken) > MAX_TASKS:
        return ["the task graph would hold %s tasks; the cap is %s per iteration" % (len(tasks) + len(taken), MAX_TASKS)]
    # Validate shapes before membership checks or graph traversal: this is agent-authored JSON.
    # Only structurally valid tasks enter the graph; malformed tasks return diagnostics, not exceptions.
    errors, task_ids, graph = [], set(), {}
    for task in tasks:
        if not isinstance(task, dict):
            errors.append("a task is not an object")
            continue
        task_id = task.get("id")
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9-]+", task_id):
            errors.append("task id must contain only letters, digits and dashes: %r" % task_id)
            continue
        if task_id in task_ids or task_id in taken:
            errors.append("task id missing or duplicated: %r" % task_id)
        task_ids.add(task_id)
        extra = set(task) - TASK_FIELDS - ({"pass"} if ordered else set())   # `pass`: set by the engine on orders
        if extra:
            errors.append("task %s: unknown fields %s" % (task_id, sorted(extra)))
        if "exports" in task:
            errors.extend("task %s: %s" % (task_id, error) for error in session_exports.validate(task["exports"]))
        if "consent" in task:
            errors.extend("task %s: %s" % (task_id, error) for error in requests.consent_errors(task["consent"]))
        role = task.get("role")
        if not isinstance(role, str) or role not in profiles or role == "policymaker":
            errors.append("task %s: role %r has no profile" % (task_id, role))
        elif ordered and parameters["shaper"] and role not in policymaker_roles:
            errors.append("task %s: the Policymaker orders only %s" % (task_id, ", ".join(policymaker_roles)))
        elif not ordered and role in policymaker_roles:
            errors.append("task %s: role `%s` is ordered only by the Policymaker" % (task_id, role))
        for field in ("title", "body"):
            if not isinstance(task.get(field), str) or not task[field].strip():
                errors.append("task %s: `%s` missing" % (task_id, field))
        if task.get("model") and (not isinstance(task["model"], str) or task["model"] not in parameters["models"]):
            errors.append("task %s: unknown model alias %r" % (task_id, task["model"]))
        if task.get("effort") and (not isinstance(task["effort"], str) or task["effort"] not in parameters["efforts"]):
            errors.append("task %s: unknown effort %r" % (task_id, task["effort"]))
        limits = task.get("limits")
        if limits is not None and (not isinstance(limits, dict) or set(limits) - {"timeout_minutes", "max_turns"} or any(
                type(v) is not int or v < 1 for v in limits.values())):
            errors.append("task %s: `limits` takes only `timeout_minutes` and `max_turns`, as positive integers" % task_id)
        dependencies = task.get("blocked_by", [])
        if dependencies is None:
            dependencies = []
        if not isinstance(dependencies, list) or any(not isinstance(dependency, str) for dependency in dependencies):
            errors.append("task %s: blocked_by must be a list of task ids" % task_id)
        else:
            graph[task_id] = dependencies
    for task_id, dependencies in graph.items():
        unknown_dependencies = [dependency for dependency in dependencies if dependency not in task_ids]
        if unknown_dependencies:
            errors.append("task %s: blocked_by unknown %s" % (task_id, unknown_dependencies))
    if not errors and topological(graph) is None:
        errors.append("blocked_by has a cycle")
    return errors


def topological(graph):
    """Task ids in an order where blockers come first; None on a cycle."""
    order, state = [], {}

    def visit(x):
        if state.get(x) == 1:
            return False
        if state.get(x) == 2:
            return True
        state[x] = 1
        for d in graph.get(x, []):
            if not visit(d):
                return False
        state[x] = 2
        order.append(x)
        return True
    for x in graph:
        if not visit(x):
            return None
    return order


def width(tasks):
    """Largest dependency-depth level; a graph statistic, not a concurrency limit."""
    depth = {}
    byid = {t["id"]: t for t in tasks}

    def d(x):
        if x not in depth:
            depth[x] = 1 + max([d(b) for b in byid[x].get("blocked_by") or [] if b in byid] or [0])
        return depth[x]
    layers = {}
    for t in tasks:
        layers[d(t["id"])] = layers.get(d(t["id"]), 0) + 1
    return max(layers.values() or [0])


def create_tasks(n, tasks):
    """Create the issues of validated tasks, wire blocking and consent, add them to the frozen graph of the iteration.
    Returns the issue numbers, for the caller to start."""
    prof, label = c.profiles(), c.ITER + str(n)
    by_id = {t["id"]: t for t in tasks}
    for t in tasks:
        for l in ("role:" + t["role"],):
            c.ensure_label(l, "1D76DB")
    number = {}
    for tid in topological({t["id"]: [d for d in t.get("blocked_by") or [] if d in by_id] for t in tasks}):
        t = by_id[tid]
        body = t["body"].rstrip() + "\n\n<!-- task-id: %s -->\n" % tid
        if t.get("limits"):
            body += "<!-- limits: %s -->\n" % json.dumps(t["limits"], sort_keys=True)
        if t.get("start_branch"):
            body += "<!-- start-branch: %s -->\n" % t["start_branch"]
        if t.get("inputs"):
            body += "<!-- inputs: %s -->\n" % json.dumps(resolve_inputs(t["inputs"], number), sort_keys=True)
        if "exports" in t:
            body += "<!-- exports: %s -->\n" % json.dumps(t["exports"], sort_keys=True)
        labels = ["role:" + t["role"], label, "generation:0"]
        if t.get("model"):
            labels.append("model:" + t["model"])
        if t.get("effort"):
            labels.append("effort:" + t["effort"])
        number[tid] = c.create_issue(t["title"], body, labels=labels)
    for t in tasks:
        for d in t.get("blocked_by") or []:
            if d in number:
                c.add_blocker(number[t["id"]], number[d])
        why = requests.limits_exceeded(prof[t["role"]])
        if why or t.get("consent"):
            ask = t.get("consent") or {}
            secrets = ask.get("secrets") or []
            req = requests.open_request("consent", "Consent: `%s` session for task \"%s\"" % (t["role"], t["title"]),
                               ask.get("what") or "Run one `%s` session %s." % (t["role"], "beyond the accepted limits: " + ", ".join(why) if why else "with the secrets listed below"),
                               ask.get("why") or t["title"],
                               "The session starts only when you close this as completed; the wider profile and the secrets listed apply to that one session.",
                               ref="task-%s" % number[t["id"]], blocks=(number[t["id"]],), iteration=n,
                               frozen="secrets: %s" % ", ".join(secrets) if secrets else None)
            c.edit_labels(number[t["id"]], add=["consent:%s" % req])
    frozen = read(n) + [dict(t, issue=number[t["id"]]) for t in tasks]
    c.commit_main({tasks_path(n): json.dumps(frozen, indent=1, ensure_ascii=False) + "\n"}, "iteration %04d: task graph" % n)
    return list(number.values())


def resolve_inputs(inputs, number):
    out = []
    for x in inputs:
        if isinstance(x, dict) and x.get("from") in number:
            out.append({"issue": number[x["from"]], "name": x.get("name")})
        elif isinstance(x, dict):
            out.append(x)
    return out
