"""The engine's contract for managed definitions in the current loop format.

Locations and ownership are separate from a session's write permissions. This
module describes the existing policy tree; it does not activate a new layout.
Persisted policy revisions remain the Git tree hash, including an empty string
when that tree is absent. Evaluator revisions retain their path-to-hash shape.
"""
ROOT = "policy"
PARAMS = ROOT + "/params.yml"
PROFILES = ROOT + "/profiles.yml"
RULES = ROOT + "/protocol.md"
EVALUATOR = ROOT + "/evaluator/"


def role_directory(role):
    return ROOT + "/roles/" + role


def prompt(role):
    return ROOT + "/prompts/" + ("policymaker" if role == "policymaker" else "role") + ".md"


def restore_paths():
    return [ROOT]


def revision(rev="HEAD", cwd="."):
    import common as c
    return c.tree_hash(rev, ROOT, cwd)


def evaluator_revision(rev="HEAD", cwd="."):
    import common as c
    return {path: c.tree_hash(rev, path, cwd) for path in c.cfg()["evaluator_paths"]}


def is_managed(path):
    import common as c
    return c.path_match(path, [ROOT + "/"]) or is_evaluator(path)


def is_protected(path):
    import common as c
    return c.path_match(path, c.cfg()["protected"])


def is_direct(path):
    import common as c
    return c.path_match(path, c.cfg()["direct_paths"])


def is_evaluator(path):
    import common as c
    return c.path_match(path, c.cfg()["evaluator_paths"])


def may_write(profile, path):
    import common as c
    allowed = c.path_match(path, profile.get("write") or [])
    return allowed and (not is_evaluator(path) or bool(profile.get("owns_evaluator")))


def evaluator_revision_string(rev="HEAD", cwd="."):
    """Legacy iteration records preserve path order and repeated configured paths."""
    import common as c
    hashes = evaluator_revision(rev, cwd)
    return ",".join(hashes[path] for path in c.cfg()["evaluator_paths"])
