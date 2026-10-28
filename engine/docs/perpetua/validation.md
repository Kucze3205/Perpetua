# Validating the layered engine

Run from the plugin root:

```sh
python -m unittest discover -s engine/.github/loop/tests
```

In a copied loop use `-s .github/loop/tests`. The suite never arms a real loop.
`test_template_flow.py` copies the actual shipped template, writes and registers the execution
roles, validates the committed setup baseline, stages the selected shaper, accepts
a work product through the accepted-tree gate, delivers a suggestion to the
Policymaker, activates changes to both layers and checks the change records and rollback.
Git and engine behavior are real; GitHub and provider session outputs are simulated.
`test_activation.py` additionally checks pending, rejected, incomplete and stale
independent evaluator verification and atomic activation of dependent definitions.

Use `python3` on Linux if that is the interpreter name. The host requirements of the
Linux tests (packages, kernel settings, the pinned CLI) are the install step of
[`engine-tests.yml`](https://github.com/Kucze3205/Perpetua/blob/main/.github/workflows/engine-tests.yml); nothing else lists them.
Windows skips the tests that need a POSIX shell, the symlink cases and the kernel-isolation tests, so a Windows run
does not prove isolation. The script boundary tests must execute on Linux; an environment skip is an outstanding check, not proof.
The opt-in live provider procedure is below. A passing fake CLI or a live API error
does not establish the real provider guarantee: the pinned CLI must successfully
call its model while subprocess probes demonstrate the network restrictions.

`test_agent_chain.py` runs the real `agent.sh` with a stand-in CLI. `PlainPromptTest` needs a
POSIX shell and checks that a prompt over 128 KiB reaches the CLI whole, through stdin.

Record the commit, command, platform, test count and skips for each validation run.
Report provider quota/authentication or missing kernel support separately from
test failures. Require both the full Linux regressions and the opted-in live
provider boundary check before claiming all ADR-0004 validation is complete.

The regression suite also covers engine ownership and local versioning (ADR-0001),
credential-free agent sessions (ADR-0002), the accepted-tree change gate without
an engine-owned accepted measurement (ADR-0003), limits, owner requests and rollback
triggers. Validation supplies no migration or reconfiguration procedure. Repeating
setup as a review is separate work.

## Session boundary checks

The [session boundary](session-boundary.md) defines what these checks exercise. In the commands below use
`engine/.github/loop/tests` in the plugin repository or `.github/loop/tests` in a copied loop.

Run `python3 -m unittest discover -s .github/loop/tests -p test_security.py -v` for the tool allowlist and
`-p test_static.py` for the workflow properties: the session job has `permissions: {}` and nothing mentions the removed
sandbox. No provider, native CLI or actual GitHub credential is needed; the engine tests workflow installs only
`python3-yaml`.

## Deployment checks

The default suite uses an in-memory GitHub and real temporary Git repositories.
It does not validate live GitHub workflow execution, release operations,
assignment or cancellation. Exercise those in a trial loop when a change needs
that evidence; documentation edits do not require arming a real loop.

`E2E_CAPTURE=on` (repository variable, off by default) makes `session.yml` upload
each whole `.session/`, staged contract, raw stream and checkout listing as
`capture-<iteration>-<issue>.tgz` on release `e2e-capture`. These include session
inputs; real loops leave capture off. Template-only tests in `test_static.py`
skip in a copied loop.
