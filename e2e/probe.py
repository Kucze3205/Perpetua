"""E2E probe, run by e2e-probe.yml from the root of a checkout of the default branch with AUTOPILOT off.

Creates three issues exactly as the engine would for the next iteration (Policymaker via the engine's own
`start_policymaker`, an audit session, a worker with an `exports` marker for every source), stages each with the real
`loop.stage` into its own copy of the checkout, writes what it staged (sizes, prompt, contract) and closes the issues
unstarted. With AUTOPILOT off `launch` is a no-op, so no session runs."""
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, ".github/loop")
import common as c      # noqa: E402
import iteration as it  # noqa: E402
import loop             # noqa: E402

WORKS = Path(os.environ["WORKS"])
if c.autopilot_on():
    raise SystemExit("AUTOPILOT is on: refusing to create loop issues the watchdog could start")
n = it.next_number()
last = n - 1
EXPORTS = json.dumps([{"source": s, "iterations": [last]} for s in ("streams", "actions", "records", "reports")])
issues = {
    "policymaker": it.start_policymaker(n, "e2e probe: the Policymaker session of the next iteration"),
    "audit": c.create_issue("e2e probe: audit session of the next iteration", "Probe, closed unstarted.\n",
                            labels=("role:audit", c.ITER + str(n))),
    "worker": c.create_issue("e2e probe: worker with every export", "Probe, closed unstarted.\n\n<!-- exports: %s -->\n" % EXPORTS,
                             labels=("role:worker", c.ITER + str(n), "generation:0")),
}
summary = {"next_iteration": n, "issues": issues, "staged": {}, "errors": {}}
for name, number in issues.items():
    work = WORKS / name / "work"
    trusted = WORKS / name / "trusted"
    shutil.copytree(".", work, ignore=shutil.ignore_patterns("probe-works"), symlinks=True)
    try:
        loop.stage(number, str(work), str(trusted))
        sizes = {p.relative_to(work / ".session").as_posix(): p.stat().st_size
                 for p in (work / ".session").rglob("*") if p.is_file()}
        summary["staged"][name] = {"issue": number, "files": sizes, "bytes": sum(sizes.values())}
    except BaseException as error:      # a failed staging is data too
        summary["errors"][name] = repr(error)
    shutil.rmtree(work / ".git", ignore_errors=True)
    for path in list(work.iterdir()):   # keep only what the session would be given on top of the repository: .session
        if path.name != ".session":
            shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink()
    c.close_issue(number, "not planned")
(WORKS / "probe.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
(WORKS / "repository-files.json").write_text(json.dumps(
    {p.as_posix(): p.stat().st_size for p in Path(".").rglob("*")
     if p.is_file() and ".git" not in p.parts and "probe-works" not in p.parts}, indent=1), encoding="utf-8")
print(json.dumps(summary, indent=1))
