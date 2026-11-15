"""What the agent CLI left behind: its machine result, the cause of a failed run, token usage and the
limit-reset time in its text. Pure reading, no GitHub.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone

import common as c


def machine_result(path):
    """The `result` event of a Claude session as a dict ({} when there is none)."""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    try:
        d = json.loads(raw)
        d = d[-1] if isinstance(d, list) and d else d
    except ValueError:
        d = {}
    return raw, d if isinstance(d, dict) else {}


def machine_cause(exec_path, exit_code):
    """Cause verbatim from the execution file, no interpretation. Never by `subtype`."""
    got = machine_result(exec_path)
    if got is None:
        return "no-execution-file exit=%s" % exit_code, False, None
    raw, d = got
    parts = []
    if d.get("api_error_status"):
        parts.append("api_error_status=%s" % d["api_error_status"])
    if d.get("terminal_reason"):
        parts.append("terminal_reason=%s" % d["terminal_reason"])
    if d.get("is_error") and not parts:
        parts.append("is_error=true")
    if exit_code not in ("0", "", None):
        parts.append("exit=%s" % exit_code)
    limited = bool(re.search(r"hit your .{0,40}limit|rate_limit", raw, re.I))
    m = re.search(r'"resets?_?[aA]t"\s*:\s*(\d{10,13})', raw)
    reset = None
    if m:
        ts = int(m.group(1))
        reset = datetime.fromtimestamp(ts / 1000 if ts > 10**11 else ts, timezone.utc)
    return " ".join(parts), limited, reset or text_reset(raw)


def usage_fields(exec_path):
    """Tokens and turns of a session from its `result` event: per model when `modelUsage` is there, else `usage`."""
    got = machine_result(exec_path)
    d = got[1] if got else {}
    u, mu = d.get("usage") or {}, d.get("modelUsage") or {}
    per = {m: {"in": v.get("inputTokens", 0), "out": v.get("outputTokens", 0), "cache_read": v.get("cacheReadInputTokens", 0),
               "cache_write": v.get("cacheCreationInputTokens", 0)} for m, v in mu.items()}
    if per:
        tot = {k: sum(v[k] for v in per.values()) for k in ("in", "out", "cache_read", "cache_write")}
    else:
        tot = {"in": u.get("input_tokens", 0), "out": u.get("output_tokens", 0), "cache_read": u.get("cache_read_input_tokens", 0),
               "cache_write": u.get("cache_creation_input_tokens", 0)}
    return {"turns": d.get("num_turns"), "peak_context": d.get("peak_context") or None, "tokens_in": tot["in"], "tokens_out": tot["out"], "cache_read": tot["cache_read"],
            "cache_write": tot["cache_write"], "models": json.dumps(per, separators=(",", ":"), sort_keys=True) if per else None}


RESET_TEXT = re.compile(r"resets\s+(?:([A-Z][a-z]{2})\s+(\d{1,2}),?\s+(?:at\s+)?)?(\d{1,2})(?::(\d{2}))?\s*([ap]m)\s*(?:\(([^)]+)\))?", re.I)


def text_reset(raw, ref=None):
    """'resets 7:20am (UTC)' / 'resets Oct 2, 5am (Europe/Warsaw)': the CLI's wording when there is no `resetsAt`."""
    m = RESET_TEXT.search(raw)
    if not m:
        return None
    mon, day, h, mi, ap, tz = m.groups()
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(tz) if tz else timezone.utc
    except Exception:
        zone = timezone.utc
    ref = (ref or c.now()).astimezone(zone)
    h = int(h) % 12 + (12 if ap.lower() == "pm" else 0)
    t = ref.replace(hour=h, minute=int(mi or 0), second=0, microsecond=0)
    if mon:
        t = t.replace(month=datetime.strptime(mon[:3].title(), "%b").month, day=int(day))
        if t < ref - timedelta(days=1):
            t = t.replace(year=t.year + 1)
    elif t <= ref:
        t += timedelta(days=1)
    return t.astimezone(timezone.utc)


def read_int(path):
    try:
        with open(path) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None
