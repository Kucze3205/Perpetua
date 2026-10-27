# Validating the layered engine

Run from the plugin root:

```sh
python -m unittest discover -s engine/.github/loop/tests
```

In a copied loop use `-s .github/loop/tests`. The suite never arms a real loop.
`test_template_flow.py` copies the actual shipped template, writes and registers the execution
roles, validates the committed setup baseline, stages the selected shaper, accepts
a work product through the accepted-tree gate, delivers a suggestion to the
Policymaker, activates changes to both layers and checks receipts and rollback.
Git and engine behavior are real; GitHub and provider session outputs are simulated.
`test_activation.py` additionally checks pending, rejected, incomplete and stale
independent evaluator verification and atomic activation of dependent definitions.

Run the full suite from the repository root: `python -m unittest discover -s engine/.github/loop/tests`
on Windows, `python3 -m unittest discover -s engine/.github/loop/tests` on Linux. The host requirements of the
Linux tests (packages, kernel settings, the pinned CLI) are the install step of
[`.github/workflows/engine-tests.yml`](../../../.github/workflows/engine-tests.yml); nothing else lists them.
Windows skips the kernel-isolation tests (they need Linux and bubblewrap), the tests that need a POSIX shell,
the symlink cases and the tests that need the native CLI, so a Windows run does not prove isolation. The script and
model boundary tests must execute on Linux; an environment skip is an outstanding check, not proof.
The opt-in live provider procedure is in [session sandbox](session-sandbox.md). A passing fake CLI or a live API error
does not establish the real provider guarantee: the pinned CLI must successfully
call its model while subprocess probes demonstrate denied policy/history/host
reads and the appropriate network restrictions.

`test_agent_chain.py` runs the real `agent.sh` with a stand-in CLI. `PlainPromptTest` needs a
POSIX shell and checks that a prompt over 128 KiB reaches the CLI whole, through stdin. `ExecutorChainTest`
builds the stand-in CLI from C (the boundary runs only a native CLI) and walks stage, executor admission, the isolated CLI, checkpoint and epilogue for an
execution-layer role and requires a completed session (`turns` of at least 1), an admitted work product and
no `origin` remote in the executor repository. Without its requirements it is skipped with the reason
printed.

Record the commit, command, platform, test count and skips for each validation run.
Report provider quota/authentication or missing kernel support separately from
test failures. Require both the full Linux regressions and the opted-in live
provider boundary check before claiming all ADR-0004 validation is complete.

The regression suite also covers engine ownership and local versioning (ADR-0001),
credential-free agent sessions (ADR-0002), the accepted-tree change gate without
an engine-owned accepted measurement (ADR-0003), limits, owner requests and rollback
triggers. Validation supplies no migration or reconfiguration procedure. Repeating
setup as a review is separate work.
