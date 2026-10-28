"""Engine-owned tool settings. Role profiles in policy cannot weaken these."""
import os
import shlex


LOCAL_TOOLS = {"Read", "Grep", "Glob", "Write", "Edit", "NotebookEdit", "TodoWrite", "Skill", "Bash"}
WEB_TOOLS = {"WebSearch", "WebFetch"}


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


def profile_error(profile):
    if "agent" in profile and type(profile["agent"]) is not bool:
        return "agent must be a boolean"
    tools = profile.get("tools") or []
    if not isinstance(tools, list) or any(not isinstance(t, str) for t in tools):
        return "tools must be a list of tool names or permission rules"
    for tool in tools:
        name = tool.split("(", 1)[0]
        if name not in LOCAL_TOOLS | WEB_TOOLS:
            return "unsupported tool: " + name
    return None


def settings(profile, trusted):
    """No MCP and no subagent or background tools, whatever the profile lists."""
    return {"hooks": {"PostToolUse": [{"matcher": "*", "hooks": [
                {"type": "command", "command": "python3 " + shlex.quote(os.path.join(trusted, "remaining.py"))}]}]},
            "permissions": {"deny": ["Agent", "Task", "Monitor", "PowerShell"]}}


def tool_names(profile):
    # Staged role instructions replace executable skill discovery: Skill is available only when listed.
    return sorted({t.split("(", 1)[0] for t in profile.get("tools", [])})
