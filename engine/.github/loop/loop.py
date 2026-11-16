#!/usr/bin/env python3
"""The engine's commands, zero agent. Each command's code lives in its own module (see the code map): the start of a
session in `session_start`, the policy scripts that shape its inputs in `policy_inputs`, the epilogue in `session_finalize`,
the watchdog in `watchdog`.

Always run from a checkout of the default branch, never from a task branch (`run-policy-scripts` runs from the copy of the
engine in the bundle that "Prepare the session" made from it). The agent's work arrives as commits fetched into this
checkout; nothing is executed inside the agent's directory.

Commands: check-autopilot, check-credentials, session-settings, stage-inputs, run-policy-scripts, take-results, watch,
resume-after-limit.
"""
import sys

import common as c
import loop_status
import policy_inputs
import request_continuation
import session_finalize
import session_start
import watchdog


def watch():
    if not c.guard_ok() or c.outage_open():
        return 0
    code = c.probe()
    if code in (401, 403):
        c.open_outage("dead Claude credential (HTTP %s)" % code, "The probe of `CLAUDE_CODE_OAUTH_TOKEN` returned %s. No session can start." % code,
                      "Generate a new token (`claude setup-token`) and store it as the repo secret. Then close this issue.")
        c.say("outage: dead Claude credential (HTTP %s)" % code)
        return 1        # a red run from cron = an e-mail to the author of the workflow file
    request_continuation.sweep_requests()
    return watchdog.drive()


STATUS_MOMENTS = ("take-results", "watch")        # the end of a session, a watchdog check


def main(argv):
    code = command(argv[1], argv[2:])
    if argv[1] in STATUS_MOMENTS:
        loop_status.refresh()
    return code


def command(cmd, args):
    if cmd == "check-autopilot":
        return 0 if c.guard_ok() else 10
    if cmd == "check-credentials":
        code = c.probe()
        print("credential check: HTTP %s" % code)
        return 1 if code in (401, 403) else 0
    if cmd == "session-settings":
        session_start.resolve(int(args[0]))
    elif cmd == "stage-inputs":
        session_start.stage(int(args[0]), *args[1:4])
    elif cmd == "run-policy-scripts":
        policy_inputs.run(*args[:3])
    elif cmd == "take-results":
        session_finalize.epilogue(int(args[0]), *args[1:5])
    elif cmd == "watch":
        return watch()
    elif cmd == "resume-after-limit":
        watchdog.resume(int(args[0]))
    else:
        raise SystemExit("unknown command: " + cmd)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
