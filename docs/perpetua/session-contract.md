# Session instructions and network access

The engine owns the common contract in `.github/loop/session-protocol.md`, the
report-reader and task-graph sections in `.github/loop/task-graph.md`,
and the Policymaker's ownership/order contract in `.github/loop/policymaker.md`.
`loop.session_contract` selects sections by the engine-selected role and shaper label,
and adds verification/delivery instructions from shell access and execution isolation.
Execution sessions deliver file bytes; policy sessions deliver committed repository
changes. A policy role without a shell can publish session outputs, but must report
blocked if its task requires repository edits.
All sessions retain trust, reporting and owner-request rules. Only the Policymaker
receives suggestion-decision instructions; only it and the shaper receive graph and
report-reader instructions. There is no role-configurable contract selector.

Staging writes the selected contract once to a trusted file passed with
`--append-system-prompt-file`. There is no `.session/protocol.md` copy.
The selected layer's `protocol.md` work rules are placed in the starting user prompt;
editable rules never become system instructions. Work rules and starting templates
(`<layer>/prompts/role.md`, `policy/prompts/policymaker.md`) come from the accepted
tree. They cannot change parsed outputs, field ownership, required inputs, or
merge/security restrictions. Role skills remain
local to `<layer>/roles/` and staged with their complete references; global skill installations remain sources.

Every implementation session also runs inside the filesystem boundary in
`executor_boundary.py`, including model sessions and online scripts. Its work
repository has a new root commit containing product files and the filtered
recipient view; policy definitions, original Git objects and host checkouts are
absent. The trusted engine imports regular output bytes against the recorded
baseline, without executing worker Git. Missing isolation or admission metadata
fails closed. Policymaker sessions retain access to definitions in both layers;
protected contracts and accepted limits remain enforced at admission.

Model sessions require the pinned native CLI and run as a non-root user. The
outer namespace admits individual runtime files, DNS/CA files and selected engine
contracts, with private home, temp and process namespaces. The CLI can connect to
its provider. Its PID 1 environment, memory and task views are masked; an inherited
seccomp filter blocks ptrace and cross-process memory syscalls. Provider credentials
remain scrubbed from subprocess environments. These controls also apply online.

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

Offline script roles run inside a bubblewrap PID/network namespace and inherit an
engine seccomp filter denying socket creation (including Unix sockets and io_uring).
This prevents access to local proxy or container-daemon sockets. Script roles receive
no model credential. Missing isolation support fails the session; online execution is
never a fallback. Supported script hosts are Linux x86_64 and aarch64. Online profiles
still require the accepted-limit/consent checks; internet access does not add tools.

The native sandbox is a boundary around tool commands, not around the trusted CLI
or engine hooks. Keep the runner and pinned CLI trusted. See the provider's
[sandbox contract](https://code.claude.com/docs/en/sandboxing) and
[settings precedence](https://code.claude.com/docs/en/settings).

Run `python3 -m unittest discover -s .github/loop/tests -p test_security.py -v`.
The kernel test runs on Linux without credentials. To check the real provider and
Bash HTTP/raw-socket denial together, install the pinned CLI and sandbox dependencies,
set `LOOP_NETWORK_INTEGRATION=1` plus `CLAUDE_CODE_OAUTH_TOKEN`, and run the same command.
It first checks host internet, then requires a successful model result and a proof
file written by its Bash/Python subprocess with blocked HTTP, IPv4, and loopback
connections. This live check is skipped on other hosts or without explicit opt-in.

Run `test_model_boundary.py` separately on non-root Linux with the pinned native
CLI, bubblewrap and socat. Its local tests check namespace access, useful online
connectivity and credential-free native startup. With the same explicit opt-in
and credential, its provider matrix requires actual Read, Glob, Grep and Bash
tool calls in online and offline sessions, denied policy reads and a useful work
product. A surrogate CLI, successful startup, authenticated API error or skipped
provider test does not prove that matrix. See [validation](validation.md).

## Sessions without a shell

One contract serves every role; what differs is the tools. The starting prompt and the protocol do not
assume a branch, a commit or a verification command. With `Bash` among its tools a session runs `## Verification`
itself before `done`. Without it (the shaper) it cannot, and `done` means the files the task asks for are written.
Execution-layer sessions get no `Work on branch task/<n>` line: the engine imports their files and never reads their
commits (`executor_boundary.import_candidate`). The engine runs `## Verification` in the epilogue for everyone.

`partial` of a shaper stays the end of its iteration: the iteration fails, no retry, and `tasks.json` is not read
(`after_session` loads the graph only on `done`). The old cause, a prompt demanding a shell the shaper lacks, is
removed instead of adding a retry or a graph load that would turn a real shortfall into success. Covered by
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
command only, never for an agent.

Script roles launch no model and incur no instruction tokens. They receive staged data
files and their configured command, without a protocol copy. Existing custom role
skills must replace references to `.session/protocol.md` when updating a loop.
