---
status: accepted
---

# A machine boundary instead of a session sandbox

Code the engine did not write never runs on a machine that holds the GitHub token. Agent sessions, the change gate,
the goal gate, checks, verification and logic kept in `policy/` run in one shared job with `permissions: {}`, and hand
back files and verdicts as data; a separate job on a fresh runner validates that data and merges, pushes and creates
issues with the token. We rejected keeping the bubblewrap sandbox for offline sessions and candidate commands: it
protected the Claude token only for roles that read nothing from outside, while internet roles already held the same
token unsandboxed; it failed on runners (`bwrap: Can't mount proc`) and cost about 1,200 lines of code, tests and docs.

## Consequences

- The owner accepts that any session can read and send the Claude token; the response to a leak is revoking it.
- `internet: false` cannot be enforced, so the `internet` profile key and the `internet_write` limit are gone; network
  access follows the session's tools.
- The enforced confidentiality of policy internals from implementation sessions ([ADR-0004](0004-separate-engine-core-and-policy.md))
  is dropped: an implementation session is asked not to read them.
- Status files from a session (duration, tokens) are trusted as data; usage limits that park the loop are checked by
  the engine itself.
- Any job of a run can upload artifacts: code that runs in a job can read `ACTIONS_RUNTIME_TOKEN` from the runner's
  memory, and the token stays valid after the job ends. So everything a later job relies on (an artifact, a verdict) is
  pinned by a digest that reaches it through the outputs of the job that made it, which only that job's runner sets
  (`artifact_pin.py`). A job that ran foreign code can still say what it likes in its own outputs: a later job with the
  token uses them only as data the engine bounds again. The candidate's checks run in one such job of the session's run
  ("Check the results"), between two jobs with the token: one bundles them, the other uses their results only for the
  same trees and otherwise runs them again in a Tokenless run. The start of an iteration does the same at the end of the
  run that found it due: `policy/policymaker-trigger.py` and the goal gate run in "Decide whether the Policymaker runs",
  and "Start the iteration" uses their output only for the same facts.
