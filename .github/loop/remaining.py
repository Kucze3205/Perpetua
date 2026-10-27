#!/usr/bin/env python3
"""PostToolUse hook of a session: after every tool tells the agent how many minutes of the session are left
(SESSION_END = epoch of the end)."""
import json
import os
import time

left = max(0, int((int(os.environ["SESSION_END"]) - time.time()) // 60))
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "%d min of the session left." % left}}))
