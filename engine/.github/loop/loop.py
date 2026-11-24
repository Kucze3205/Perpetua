#!/usr/bin/env python3
"""The engine's commands, zero agent. Each command's code lives in its own module (see the code map): the start of a
session in `session_start`, the policy scripts that shape its inputs in `policy_inputs`, the epilogue in `session_finalize`,
the checks of its results in `candidate_checks` (planned) and `check_results` (run), the start of an iteration in
`iteration_start`, the watchdog in `watchdog`.

Always run from a checkout of the default branch, never from a task branch (`run-policy-scripts` and `run-checks` run from
the copy of the engine in the bundle that "Prepare the session", "Prepare the checks" or `iteration_start.prepare` made from
it). The agent's work
arrives as commits fetched into this checkout; nothing is executed inside the agent's directory.

Commands: check-autopilot, check-credentials, session-settings, stage-inputs, stage-failed, run-policy-scripts, prepare-checks,
run-checks, take-results, start-iteration, watch, resume-after-limit.
"""
import sys

import candidate_checks
import check_results
import common as c
import iteration_start
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


STATUS_MOMENTS = ("take-results", "start-iteration", "watch")        # the end of a session, a start, a watchdog check
START_MOMENTS = ("take-results", "watch")       # the jobs that start an iteration follow these (iteration_start.prepare)


def main(argv):
    try:
        code = command(argv[1], argv[2:])
    finally:
        if argv[1] in START_MOMENTS:
            iteration_start.prepare()
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
    elif cmd == "stage-failed":
        watchdog.stage_failed(int(args[0]), "*Prepare the session* failed")
    elif cmd == "run-policy-scripts":
        policy_inputs.run(*args[:3])
    elif cmd == "prepare-checks":
        candidate_checks.prepare(int(args[0]), *args[1:5])
    elif cmd == "run-checks":
        check_results.run(args[0])
    elif cmd == "take-results":
        session_finalize.epilogue(int(args[0]), *args[1:5])
    elif cmd == "start-iteration":
        iteration_start.act()
    elif cmd == "watch":
        return watch()
    elif cmd == "resume-after-limit":
        watchdog.resume(int(args[0]))
    else:
        raise SystemExit("unknown command: " + cmd)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
