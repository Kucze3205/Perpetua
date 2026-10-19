# Validating the layered engine

Run from the plugin root:

```sh
python -m unittest discover -s engine/.github/loop/tests
```

In a copied loop use `-s .github/loop/tests`. The suite never arms a real loop.
`test_template_flow.py` copies the actual inactive template, explicitly installs
roles, validates the committed setup baseline, stages the selected shaper, accepts
a work product through the accepted-tree gate, delivers a suggestion to the
Policymaker, activates changes to both layers and checks receipts and rollback.
Git and engine behavior are real; GitHub and provider session outputs are simulated.
`test_activation.py` additionally checks pending, rejected, incomplete and stale
independent evaluator verification and atomic activation of dependent definitions.

Run the full suite on supported Linux hosts with bubblewrap and namespace/seccomp
support. Windows checks do not prove kernel isolation. The script and model boundary
tests must execute on Linux; an environment skip is an outstanding check, not proof.
Use the prerequisites and opt-in live provider procedure in
[session contracts](session-contract.md). A passing fake CLI or a live API error
does not establish the real provider guarantee: the pinned CLI must successfully
call its model while subprocess probes demonstrate denied policy/history/host
reads and the appropriate network restrictions.

Record the commit, command, platform, test count and skips for each validation run.
Report provider quota/authentication or missing kernel support separately from
test failures. Require both the full Linux regressions and the opted-in live
provider boundary check before claiming all ADR-0004 validation is complete.

The regression suite also covers engine ownership and local versioning (ADR-0001),
credential-free agent sessions (ADR-0002), the accepted-tree change gate without
an engine-owned accepted measurement (ADR-0003), limits, owner requests and rollback
triggers. Validation supplies no migration or reconfiguration procedure. Repeating
setup as a review is separate work.
