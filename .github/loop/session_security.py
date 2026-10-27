"""Engine-owned tool and network settings. Role profiles in policy cannot weaken these."""
import re
import os
import shlex


LOCAL_TOOLS = {"Read", "Grep", "Glob", "Write", "Edit", "NotebookEdit", "TodoWrite", "Skill", "Bash"}
WEB_TOOLS = {"WebSearch", "WebFetch"}
MIN_CLI = (2, 1, 284)


def check_session_directory(work):
    """Task-branch links must not redirect trusted staging writes."""
    session = os.path.join(work, ".session")
    expected = os.path.join(os.path.realpath(work), ".session")

    def check(path):
        target = os.path.join(expected, os.path.relpath(path, session))
        if os.path.normcase(os.path.realpath(path)) != os.path.normcase(os.path.normpath(target)):
            raise SystemExit("session directory contains a redirected path: " + path)

    check(session)
    if os.path.lexists(session) and not os.path.isdir(session):
        raise SystemExit("session path must be a directory")
    for root, directories, files in os.walk(session, followlinks=False):
        for name in directories + files:
            check(os.path.join(root, name))


def profile_error(profile, cli_version):
    if "internet" in profile and type(profile["internet"]) is not bool:
        return "internet must be a boolean"
    if "agent" in profile and type(profile["agent"]) is not bool:
        return "agent must be a boolean"
    tools = profile.get("tools") or []
    if not isinstance(tools, list) or any(not isinstance(t, str) for t in tools):
        return "tools must be a list of tool names or permission rules"
    for tool in tools:
        name = tool.split("(", 1)[0]
        if name not in LOCAL_TOOLS | WEB_TOOLS:
            return "unsupported tool: " + name
        if not profile.get("internet", False) and name in WEB_TOOLS:
            return "internet:false conflicts with tool " + name
    if profile.get("agent") is not False and not profile.get("internet", False):
        m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", str(cli_version))
        if not m or tuple(map(int, m.groups())) < MIN_CLI:
            return "internet:false requires a pinned Claude CLI >= 2.1.284"
    return None


def settings(profile, trusted, nested=False):
    """Bash children are isolated; the parent CLI retains its provider connection.

    `nested`: the CLI itself runs inside the executor boundary (a bwrap). Its own bwrap then cannot mount a fresh procfs
    there (`bwrap: Can't mount proc on /newroot/proc`), so it is told to bind the boundary's /proc, whose PID 1 environ,
    mem and task views the boundary already masks."""
    out = {"hooks": {"PostToolUse": [{"matcher": "*", "hooks": [
        {"type": "command", "command": "python3 " + shlex.quote(os.path.join(trusted, "remaining.py"))}]}]}}
    if not profile.get("internet", False):
        out["sandbox"] = {
            "enabled": True, "failIfUnavailable": True,
            "autoAllowBashIfSandboxed": False, "allowUnsandboxedCommands": False,
            "excludedCommands": [],
            "network": {"allowedDomains": [], "deniedDomains": ["*"],
                        "strictAllowlist": True, "allowAllUnixSockets": False,
                        "allowUnixSockets": [], "allowLocalBinding": False},
            "filesystem": {"denyWrite": [trusted], "denyRead": ["/proc"]},
        }
        if nested:
            out["sandbox"]["enableWeakerNestedSandbox"] = True
        out["permissions"] = {"deny": ["WebFetch", "WebSearch", "Agent", "Task", "Monitor", "PowerShell", "Skill",
                                               "Read(/proc/**)", "Write(%s/**)" % trusted, "Edit(%s/**)" % trusted]}
    return out


def tool_names(profile):
    names = {t.split("(", 1)[0] for t in profile.get("tools", [])} | {"Skill"}
    if not profile.get("internet", False):
        names.discard("Skill")  # staged role instructions replace executable skill discovery
    return sorted(names)


def check_skills(root):
    """Scripts are data; hook/interpolation execution bypasses Bash isolation."""
    import yaml
    for directory, _, names in os.walk(root, followlinks=False):
        if "SKILL.md" not in names:
            continue
        path = os.path.join(directory, "SKILL.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        front = text.split("---", 2)
        metadata = yaml.safe_load(front[1]) if len(front) == 3 and not front[0].strip() else {}
        if (isinstance(metadata, dict) and metadata.get("hooks")) or re.search(r"!`", text):
            raise SystemExit("internet:false cannot load executable skill hooks or interpolation: " + path)
