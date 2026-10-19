# Session instructions and network access

The engine owns `.github/loop/session-protocol.md` and the Policymaker's ownership/order
contract in `.github/loop/policymaker.md`. Staging writes the contract to a trusted file
passed with `--append-system-prompt-file`. It also combines that contract with
the selected layer's `protocol.md` in `.session/protocol.md`. Editable work rules and starting
templates (`<layer>/prompts/role.md`, `policy/prompts/policymaker.md`) take effect at the
next stage from the accepted tree. They cannot change parsed outputs, field
ownership, required inputs, or merge/security restrictions. Role skills remain
local to `<layer>/roles/` and staged with their complete references; global skill installations remain sources.

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
