"""The E2E change gate: scores the goal of `experiment.GOAL` on the candidate and the accepted tree.

A point for `src/greeting.txt` with its line, a point for `src/farewell.txt` naming a closed iteration with an improving
merged session. Iteration records are protected (no session writes them) and an iteration cannot see its own, so the
second point needs a later iteration than the first, and an iteration after both has nothing left to improve.
Prints the engine's verdict JSON (engine.yml `gate`); runs from the accepted tree."""
import json
import os
import re
from pathlib import Path


def progressed(tree):
    """Numbers of closed iterations in `tree/iterations/` with an improving merged session."""
    out = set()
    for f in (tree / "iterations").glob("[0-9][0-9][0-9][0-9].json"):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sessions = (r.get("outcome") or {}).get("sessions") or []
        if r.get("status") == "closed" and any(s.get("merged") and s.get("improved") for s in sessions):
            out.add(r.get("iteration"))
    return out


def text(tree, path):
    try:
        return (tree / path).read_text(encoding="utf-8").rstrip("\r\n")
    except OSError:
        return None


def score(tree, records):
    """(points, wrong goal files) of `tree`; `records`: the tree whose iteration records count."""
    points, wrong = 0, []
    greeting, farewell = text(tree, "src/greeting.txt"), text(tree, "src/farewell.txt")
    if greeting == "hello from perpetua":
        points += 1
    elif greeting is not None:
        wrong.append("src/greeting.txt")
    m = re.fullmatch(r"goodbye after iteration (\d+)", farewell or "")
    if m and points and int(m.group(1)) in progressed(records):
        points += 1
    elif farewell is not None:
        wrong.append("src/farewell.txt")
    return points, wrong


def verdict(candidate, baseline):
    before, _ = score(baseline, baseline)
    after, wrong = score(candidate, baseline)
    if wrong:
        return {"verdict": "rejected", "improved": False, "detail": "wrong goal file(s): %s" % ", ".join(wrong)}
    if after < before:
        return {"verdict": "rejected", "improved": False, "detail": "goal points %s -> %s" % (before, after)}
    return {"verdict": "accepted", "improved": after > before, "detail": "goal points %s -> %s of 2" % (before, after)}


if __name__ == "__main__":
    print(json.dumps(verdict(Path(os.environ["CANDIDATE_DIR"]), Path(os.environ["BASELINE_DIR"]))))
