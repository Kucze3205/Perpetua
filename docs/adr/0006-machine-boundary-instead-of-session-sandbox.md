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
