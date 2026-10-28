# Session instructions and network access

The engine owns the common contract in `.github/loop/session-protocol.md`, the
report-reader and task-graph sections in `.github/loop/task-graph.md`,
and the Policymaker's ownership/order contract in `.github/loop/policymaker.md`.
`loop.session_contract` selects sections by the engine-selected role and shaper label,
and adds verification/delivery instructions from shell access, and for execution roles the request not to read `policy/`.
Every role delivers file bytes, committed or not. A session is staged with a
baseline (`executor_boundary.record`); the epilogue builds one commit on it from the final files of the working tree
(`executor_boundary.import_candidate`, the same import as for execution sessions), without running the
agent's git. New untracked files outside the role's write paths (CLI stubs, runner home
files) are environment debris and are not admitted; symlinks, hard links and special files
refuse the admission. Write-path, protected-path, size and change checks apply unchanged.
All sessions retain trust, reporting and owner-request rules. Only the Policymaker
receives the policy-owned suggestion instructions; only it and the shaper receive graph and
report-reader instructions. There is no role-configurable contract selector.

Staging writes the selected contract once to a trusted file passed with
`--append-system-prompt-file`. There is no `.session/protocol.md` copy.
The selected layer's `protocol.md` work rules are placed in the starting user prompt;
editable rules never become system instructions. Work rules and starting templates
(`<layer>/prompts/role.md`, `policy/prompts/policymaker.md`) come from the accepted
tree. They cannot change parsed outputs, field ownership, required inputs, or
merge/security restrictions. Role skills remain
local to `<layer>/roles/` and staged with their complete references; global skill installations remain sources.

## Output admission

The engine records the staged baseline (`executor_boundary.record`) and never runs the
session's Git. The epilogue reads the regular files the working tree holds at the end,
committed or not, and builds one commit on the baseline from those bytes
(`executor_boundary.import_candidate`). New untracked files outside the role's write
paths (CLI stubs, runner home files) are environment debris and are not admitted.
Symlinks, hard links and special files refuse the admission. Binary contents, executable
modes and deletions are preserved. Worker Git configuration, hooks and attributes cannot
execute during the import. The result then passes the protected/write checks and the
merge path; the change gate still runs from the accepted tree with the reconstructed
candidate and accepted baseline.

Execution roles see the same checkout as policy roles. Policy internals are not hidden
from them (ADR-0006).

Candidate-owned product checks and task verification run in the tokenless job on a copy of the candidate tree
without `.git`, like the change gate; a job that returns nothing fails the command. The accepted change gate still
runs from the accepted tree with the full candidate and baseline required by ADR-0003.

## Model process and network access

Model sessions require the pinned native CLI and a non-root user. The outer
namespace admits individual runtime, DNS/CA and selected engine contract files.
The CLI retains network access to its provider. `launch_model` passes its credential
through a pipe identified by `CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR`, keeping the
value out of process environments and `/proc/*/environ`. Private procfs supports
the native CLI's nested sandbox; the inherited process guard denies ptrace and
cross-process memory syscalls. Subprocess environments scrub provider credentials.
These controls also apply online.

`internet` defaults to false and must be a boolean, including on script profiles.
Offline model sessions use the pinned Claude CLI's strict Bash sandbox: subprocesses
inherit network isolation; WebFetch/WebSearch and unsandboxed retries are unavailable.
The CLI process retains network access for model calls. Unsupported tool surfaces
(including MCP and subagents) are refused rather than assuming they inherit Bash
isolation. `--tools` limits available builtins independently of permission rules.
User/project settings are disabled and MCP configuration is empty. Offline sessions
disable native skill/command discovery, which could execute hooks or interpolation
outside the Bash sandbox. They read their complete staged role skill tree from
`.session/role/`; ordinary skill scripts run through sandboxed Bash. Executable hook
or interpolation content is rejected. Creating a new skill during the session cannot
re-enable native discovery.

Offline script roles use a private network namespace and inherit an engine seccomp
filter denying socket creation (including Unix sockets and io_uring), preventing
access to local proxy or container-daemon sockets. Script roles receive no model
credential. Supported script hosts are Linux x86_64 and aarch64. Online scripts
share the network and receive DNS/CA files, while retaining filesystem isolation.
Online profiles still require accepted-limit/consent checks; internet access does
not add tools.

The native sandbox is a boundary around tool commands, not around the trusted CLI
or engine hooks. Keep the runner and pinned CLI trusted. See the provider's
[sandbox contract](https://code.claude.com/docs/en/sandboxing) and
[settings precedence](https://code.claude.com/docs/en/settings).

Test commands, Linux requirements and opt-in live-provider evidence are in
[validation](validation.md).

## Sessions without a shell

One contract serves every role; what differs is the tools. The starting prompt and the protocol do not
assume a branch, a commit or a verification command. With `Bash` among its tools a session runs `## Verification`
itself before `done`. Without it (the shaper) it cannot, and `done` means the files the task asks for are written.
Neither layer gets a `Work on branch task/<n>` line: the engine admits the files a session leaves and never reads its
commits (`executor_boundary.import_candidate`, for execution and policy roles alike). Every contract says so and asks for no
commit. The engine runs `## Verification` in the epilogue for everyone.

A shaper reporting `partial` fails the iteration without retry; `after_session`
reads its graph only on `done`. Covered by
`ShaperFailureTest.test_partial_ends_the_iteration_and_the_graph_is_not_read` and
`InputBoundaryTest.test_a_session_without_a_shell_is_not_told_to_branch_commit_or_run_commands`.

## Session secrets

A session sees a secret only when the owner approved its name for that task. Three parts, each with one owner:

- **Pool** (the owner, `session.yml`): one line per secret the loop may ever hand out, in the `env` of the `Session`
  step, gated on the name: `NAME: ${{ contains(needs.prep.outputs.secrets, '|NAME|') && secrets.NAME || '' }}`.
  Without an approved name the line is empty.
- **Approval** (the owner, per task): the shaper lists `consent.secrets: [NAME…]` (names only); the list is frozen in
  the `consent` request and closing it as completed approves it. `resolve` outputs `secrets` (`|A|B|`) from that frozen
  text, so two sessions with different consents get different names. A new session of the same task uses the same
  consent; a changed list is a new request.
- **Start** (trusted code): `resolve` blocks the task with `secret-missing:NAME` when an approved name has no line in
  the `Session` step. `stage` writes the approved names to the trusted `secrets` file and, before any agent code runs,
  `agent.sh` checks that every one is non-empty (pool names that are not approved are already empty by the expression);
  if one is not, the agent does not start, the task is `blocked:
  secret-missing:NAME` and a `capability` request (no value) goes to the owner. Closing it creates nothing.

The GitHub token never reaches the agent. `gate.env_passthrough` in `engine.yml` is a separate owner list for the gate
command only, never for an agent (the tokenless job does not receive it yet).

Script roles receive staged data and their configured command, without a protocol
copy. Existing custom role skills must replace references to `.session/protocol.md`
when updating a loop.

## Session lifecycle and outputs

`session.yml` runs `prep` (guard, credential probe, resolve), then three jobs:

- `stage` (GitHub token): checks out the engine and the worker tree, stages the session and ships it as an artifact.
  The executor baseline goes in an artifact of its own, pinned by its sha256 in a job output; the session never
  receives it.
- `session` (`permissions: {}`; only the Claude token and the secrets the owner consented to): unpacks the staged
  bundle, runs `.github/loop/prepare.sh` and the role, and packs the results: status files, the working tree
  (`work.tar`) and the session's commits (`commits.bundle`). The agent's code never runs next to the GitHub token.
- `epilogue` (GitHub token, a fresh runner): `loop.epilogue` takes the artifact in as data
  (`session_artifact.py`). It refuses a tree with a path that escapes, one written through a link, a special file or an
  oversized file; links and hard links that stay are unpacked unfollowed and refused by the admission below. It checks
  the baseline against the staged digest, reads the commits from the bundle (kept under `refs/session/<issue>/`, never
  merged; it never fetches from the session's repository) and finalizes.

Status files (start, end, exit code, turns, tokens) are trusted as data. A usage limit that parks the loop is not: the
epilogue parks only when the engine's own probe (`common.probe_limit`) confirms it, and the probe sets the clock.
Both layers publish final file bytes against their staged baseline as described above; the engine does not read worker
commits or execute worker Git. The session sandbox is still on (ADR-0006 removes it in a later step).

The [session protocol](../../.github/loop/session-protocol.md) defines input/output
files, statuses and report field ownership. Agent and script roles use the same
file interfaces; scripts launch no model and consume no instruction tokens.
`timeout` and `--max-turns` enforce time and turn ceilings. Tokens are recorded,
not budgeted; the engine has no money limit. Policymaker tuning in
`policy/params.yml` also respects owner ceilings in `engine.yml`.

Files over `large_file_bytes` and binaries go in `.session/artifacts/`; the epilogue
uploads release assets to `iter-NNNN` and lists them in the report. Consumers name
them in `inputs`. Raw session streams use artifact `stream-NNNN-<issue>` and
`stream_retention_iterations` retention (default 8); upload failure does not fail
the session. [Exports](session-exports.md) defines selectors and audit defaults.

The shipped model integration uses `CLAUDE_CODE_OAUTH_TOKEN`, the watchdog probe
and subscription-limit parking/reset. A provider change affects `session.yml`,
`watchdog.yml`, `agent.sh`, the probe in `common.py` and `stream_filter.py`. Setup
must implement and check equivalents or record the guarantees the owner gives up.
