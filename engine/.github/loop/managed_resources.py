"""Managed definitions: storage, execution layer and admission permissions.

Only the layered format is supported. Profiles assign each role to exactly
one execution layer; existing installations keep their own versioned engine.
"""
import os
import re
ROOT = "policy"
PARAMS = ROOT + "/params.yml"
EVALUATOR = ROOT + "/evaluator/"


EXECUTION = "execution"
LAYERS = (EXECUTION, ROOT)


def layered(cwd="."):
    import common as c
    value = c.load_yaml(os.path.join(cwd, "engine.yml")).get("resources", {})
    if not isinstance(value, dict) or set(value) - {"format"}:
        raise ValueError("resources supports only format: layered")
    fmt = value.get("format", "layered")
    if fmt != "layered":
        raise ValueError("unknown managed resources format: %s" % fmt)
    return True


def _load_profiles(path):
    """Reject duplicate keys rather than silently selecting a layer/profile."""
    import yaml

    class UniqueLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node)
            if not isinstance(key, str):
                raise ValueError("profile keys must be strings in %s" % path)
            if key in result:
                raise ValueError("duplicate profile key `%s` in %s" % (key, path))
            result[key] = loader.construct_object(value_node)
        return result

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.load(fh, Loader=UniqueLoader) or {}
    except FileNotFoundError:
        return {}
    except yaml.YAMLError as error:
        raise ValueError("invalid profiles YAML in %s" % path) from error


def _check_local_tree(cwd, root):
    """Definitions are local copies, never aliases into another layer or host."""
    checkout = os.path.realpath(cwd)
    tree = os.path.join(checkout, root)

    def check(path):
        if os.path.islink(path) or os.path.normcase(os.path.realpath(path)) != os.path.normcase(os.path.abspath(path)):
            raise ValueError("redirected managed definition: " + os.path.relpath(path, checkout))

    check(tree)
    for directory, directories, files in os.walk(tree, followlinks=False):
        for name in directories + files:
            check(os.path.join(directory, name))


def role_profiles(cwd="."):
    """Validate the whole active catalog before selecting any role."""
    import common as c
    layered(cwd)
    for layer in LAYERS:
        _check_local_tree(cwd, layer)
    shaper = c.load_yaml(os.path.join(cwd, PARAMS)).get("shaper")
    out = {}
    for layer in LAYERS:
        profiles = _load_profiles(os.path.join(cwd, layer, "profiles.yml"))
        if not isinstance(profiles, dict):
            raise ValueError("%s/profiles.yml must map role names to profiles" % layer)
        for role, profile in profiles.items():
            if not isinstance(role, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", role):
                raise ValueError("invalid role name: %s" % role)
            if role in out:
                raise ValueError("role `%s` belongs to both execution and policy layers" % role)
            if role == "policymaker":
                raise ValueError("Policymaker profile belongs to engine.yml; its skill belongs to policy")
            if not isinstance(profile, dict):
                raise ValueError("role `%s` must have a profile mapping" % role)
            if "layer" in profile:
                raise ValueError("role `%s` layer is assigned by its profiles.yml location" % role)
            required = ROOT if role in ("audit", "evaluator", c.cfg()["verifier_role"]) else None
            if role == shaper:
                if required == ROOT:
                    raise ValueError("policy role `%s` cannot be the execution shaper" % role)
                required = EXECUTION
            if required and layer != required:
                raise ValueError("role `%s` must belong to the %s layer" % (role, required))
            out[role] = dict(profile, layer=layer)
    # A skill directory in both layers is ambiguous even with only one profile.
    active = dict(out, policymaker={"layer": ROOT})
    for role, profile in active.items():
        other = ROOT if profile["layer"] == EXECUTION else EXECUTION
        if os.path.exists(os.path.join(cwd, other, "roles", role)):
            raise ValueError("role `%s` has a skill in the wrong or both layers" % role)
    return out


def role_layer(role):
    layered()
    profiles = role_profiles()
    if role == "policymaker":
        return ROOT
    if role not in profiles:
        raise ValueError("no layer/profile for role `%s`" % role)
    return profiles[role]["layer"]


def role_directory(role):
    return role_layer(role) + "/roles/" + role


def rules(role):
    return role_layer(role) + "/protocol.md"


def prompt(role):
    return role_layer(role) + "/prompts/" + ("policymaker" if role == "policymaker" else "role") + ".md"


def restore_paths():
    import common as c
    return list(dict.fromkeys([EXECUTION, ROOT] + c.cfg()["evaluator_paths"]))


def revision(rev="HEAD", cwd="."):
    import common as c
    layered(cwd)
    import hashlib
    import json
    trees = {path: c.tree_hash(rev, path, cwd) for path in restore_paths()}
    return "layered-v1:" + hashlib.sha256(json.dumps(trees, sort_keys=True).encode("utf-8")).hexdigest()


def evaluator_revision(rev="HEAD", cwd="."):
    import common as c
    return {path: c.tree_hash(rev, path, cwd) for path in c.cfg()["evaluator_paths"]}


def is_managed(path):
    import common as c
    return c.path_match(path, [ROOT + "/", EXECUTION + "/"]) or is_evaluator(path)


def is_protected(path):
    import common as c
    return c.path_match(path, c.cfg()["protected"])


def is_direct(path):
    import common as c
    return is_managed(path) or c.path_match(path, c.cfg()["direct_paths"])


def is_evaluator(path):
    import common as c
    return c.path_match(path, c.cfg()["evaluator_paths"])


def may_write(profile, path):
    import common as c
    if profile.get("layer") == EXECUTION and is_managed(path):
        return False
    allowed = c.path_match(path, profile.get("write") or [])
    return allowed and (not is_evaluator(path) or bool(profile.get("owns_evaluator")))


def evaluator_revision_string(rev="HEAD", cwd="."):
    """Legacy iteration records preserve path order and repeated configured paths."""
    import common as c
    hashes = evaluator_revision(rev, cwd)
    return ",".join(hashes[path] for path in c.cfg()["evaluator_paths"])


def validate_activation(cwd):
    """A layered boundary must publish a shaper (when one is set) together with its complete definition."""
    import common as c
    profiles = role_profiles(cwd)
    shaper = c.load_yaml(os.path.join(cwd, PARAMS)).get("shaper")
    if shaper is None:
        return
    if shaper not in profiles or profiles[shaper]["layer"] != EXECUTION:
        raise ValueError("shaper `%s` has no execution profile" % shaper)
    profile = profiles[shaper]
    if profile.get("agent", True):
        if not os.path.isfile(os.path.join(cwd, EXECUTION, "roles", shaper, "SKILL.md")):
            raise ValueError("shaper `%s` has no complete local skill" % shaper)
    elif not profile.get("command"):
        raise ValueError("script shaper `%s` has no command" % shaper)
