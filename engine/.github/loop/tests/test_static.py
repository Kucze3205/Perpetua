"""Static checks of the engine as a whole: it parses, it is self-consistent, and it carries nothing of the project it came from.
This file is ASCII only (checked below), so it hides no Polish either."""
import os
import re
import subprocess
import unittest

import yaml

import fakes
from fakes import ENGINE, common as c, iteration as it
import artifact_pin
import owner_requests as requests

BASH = None
for cand in ("C:/Program Files/Git/bin/bash.exe", "C:/Program Files (x86)/Git/bin/bash.exe", "/bin/bash", "/usr/bin/bash"):
    if os.path.exists(cand):
        BASH = cand
        break

# Words of the project the engine was extracted from, and Polish: the engine is goal-independent and English.
POLISH_LETTERS = "[\u0105\u0107\u0119\u0142\u0144\u00f3\u015b\u017a\u017c\u0104\u0106\u0118\u0141\u0143\u00d3\u015a\u0179\u017b]"
FORBIDDEN = re.compile(
    r"block\s*blast|blockblast|emulator|\bapk\b|\bbench(mark)?\b|\bdqn\b|kvm|tesseract|\bbridge\b|drone|reinforc|\badb\b|"
    r"ASSETS_READ_TOKEN|\btorch\b|numpy|" + POLISH_LETTERS + r"|\b(rola|cykl|dozorca|orkiestrator|zadanie|petla|nastepca|weryfikacja|raport)\b",
    re.I)


# Inside a loop's own repository only the engine's code is the engine's; the rest (policy/, goal files) is the loop's.
IN_LOOP_REPO = os.path.isdir(os.path.join(ENGINE, ".git"))
TEMPLATE_ONLY = unittest.skipIf(IN_LOOP_REPO, "checks the template the plugin ships, not a loop's repository")


def engine_files():
    for root, dirs, files in os.walk(os.path.join(ENGINE, ".github") if IN_LOOP_REPO else ENGINE):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for f in files:
            if not f.endswith(".pyc"):
                yield os.path.join(root, f)


@TEMPLATE_ONLY
class TraceTest(unittest.TestCase):
    def test_no_trace_of_the_source_project_in_any_file_or_name(self):
        me = os.path.abspath(__file__)
        bad = []
        for p in engine_files():
            rel = os.path.relpath(p, ENGINE).replace("\\", "/")
            if FORBIDDEN.search(rel):
                bad.append(rel)
            if os.path.abspath(p) == me:
                continue
            with open(p, encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    m = FORBIDDEN.search(line)
                    if m:
                        bad.append("%s:%s: %s" % (rel, n, m.group(0)))
        self.assertEqual(bad, [])

    def test_this_file_is_ascii(self):
        with open(os.path.abspath(__file__), encoding="utf-8") as fh:
            self.assertIsNone(re.search(r"[^\x00-\x7f]", fh.read()))


class FilesTest(unittest.TestCase):
    def test_every_file_is_lf_only_utf8(self):
        for p in engine_files():
            with open(p, "rb") as fh:
                data = fh.read()
            self.assertNotIn(b"\r", data, p)
            data.decode("utf-8")

    def test_python_compiles(self):
        for p in engine_files():
            if p.endswith(".py"):
                with open(p, encoding="utf-8") as fh:
                    compile(fh.read(), p, "exec")

    @unittest.skipUnless(BASH, "no bash")
    def test_shell_scripts_parse(self):
        for p in engine_files():
            if p.endswith(".sh"):
                with open(p, encoding="utf-8") as fh:
                    self.assertTrue(fh.readline().startswith("#!"), p)
                r = subprocess.run([BASH, "-n", p], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, "%s: %s" % (p, r.stderr))

    def test_no_session_sandbox_remains(self):
        for p in engine_files():
            if p == __file__:
                continue
            with open(p, encoding="utf-8") as fh:
                text = fh.read().lower()
            # SUBPROCESS_ENV_SCRUB makes the CLI require bubblewrap: a session would exit before its first model call
            for word in ("bubblewrap", "bwrap", "seccomp", "offline_exec", "sandbox.sh", "subprocess_env_scrub"):
                self.assertNotIn(word, text, "%s still mentions %s" % (p, word))

    def test_version_stamp(self):
        with open(os.path.join(ENGINE, "ENGINE_VERSION"), encoding="utf-8") as fh:
            self.assertRegex(fh.read().strip(), r"^\d+\.\d+\.\d+$")


class WorkflowTest(unittest.TestCase):
    def workflows(self):
        d = os.path.join(ENGINE, ".github", "workflows")
        for f in sorted(os.listdir(d)):
            with open(os.path.join(d, f), encoding="utf-8") as fh:
                text = fh.read()
            yield f, text, yaml.safe_load(text)

    def test_they_parse_and_declare_permissions_and_the_switch(self):
        seen = set()
        for f, text, doc in self.workflows():
            seen.add(f)
            self.assertIn("jobs", doc, f)
            self.assertTrue("on" in doc or True in doc, f)                   # PyYAML reads `on:` as True
            self.assertIn("permissions", doc, f)
            if f not in ("watchdog.yml", "tokenless-job.yml"):          # the reusable job is only called from tokenless.yml
                self.assertIn("vars.AUTOPILOT == 'on'", text, f)               # the first condition of every loop workflow
        if not IN_LOOP_REPO:
            self.assertEqual(seen, {"session.yml", "resume.yml", "watchdog.yml", "tokenless.yml", "tokenless-job.yml", "iteration-start.yml"})

    def test_run_names_keep_their_hash(self):
        """An unquoted ' #' in a YAML value is a comment: run-name became 'session' and no run matched its session (#84)."""
        for f, text, doc in self.workflows():
            name = doc.get("run-name")
            if name is None or f in ("watchdog.yml", "tokenless.yml"):      # these two carry no issue number
                continue
            self.assertIn("#", name, f)
            rendered = re.sub(r"\$\{\{ (inputs\.issue|github\.event\.issue\.number) \}\}", "12", name)
            rendered = re.sub(r"\$\{\{.*?\}\}", "", rendered)
            if f == "session.yml":
                self.assertEqual(c.run_issue(rendered), "session #12")

    def test_no_workflow_uses_the_dangerous_inputs(self):
        for f, text, doc in self.workflows():
            self.assertNotIn("pull_request_target", text, f)
            self.assertNotIn("ANTHROPIC_API_KEY", str(doc.get("env", {})), f)
            for job in doc.get("jobs", {}).values():
                self.assertNotIn("ANTHROPIC_API_KEY", str(job.get("env", {})), f)
                self.assertNotIn("ANTHROPIC_API_KEY", str(job.get("steps", [])), f)
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if "uses: actions/checkout@v7" in line:
                    block = []
                    for nxt in lines[i + 1:]:
                        if re.match(r"^\s{6}- ", nxt):
                            break
                        block.append(nxt)
                    self.assertIn("persist-credentials: false", "\n".join(block), f)

    def test_every_engine_command_a_workflow_runs_exists(self):
        with open(os.path.join(ENGINE, ".github", "loop", "loop.py"), encoding="utf-8") as fh:
            src = fh.read()
        known = set(re.findall(r'cmd == "([a-z-]+)"', src))
        used = set()
        for f, text, doc in self.workflows():
            used |= set(re.findall(r"loop\.py ([a-z-]+)", text))
        self.assertTrue(used)
        self.assertEqual(used - known, set())

    def test_the_engine_has_exactly_these_commands(self):
        with open(os.path.join(ENGINE, ".github", "loop", "loop.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertEqual(set(re.findall(r'cmd == "([a-z-]+)"', src)),
                         {"check-autopilot", "check-credentials", "session-settings", "stage-inputs", "stage-failed", "run-policy-scripts",
                          "prepare-checks", "run-checks", "take-results", "start-iteration", "watch", "resume-after-limit"})

    def test_the_owner_sees_names_not_commands(self):
        """Every workflow, job and step has a name of its own: an unnamed step shows as `Run <its first command>`
        (the `resolve` step used to read `Run pip install -q pyyaml`). Names say what the thing does, in plain words."""
        shown = {"session.yml": "Session", "resume.yml": "Resume after limit",
                 "watchdog.yml": "Watchdog", "tokenless.yml": "Tokenless run", "tokenless-job.yml": "Tokenless job",
                 "iteration-start.yml": "Iteration start"}
        for f, text, doc in self.workflows():
            self.assertEqual(doc["name"], shown[f], f)
            for job_id, job in doc["jobs"].items():
                if f != "tokenless-job.yml":
                    self.assertTrue(job.get("name"), (f, job_id))
                for step in job.get("steps", []):
                    self.assertTrue(step.get("name"), (f, job_id, step))
                    self.assertNotRegex(step["name"], r"^(Run )?(pip|python3?|npm|bash|tar|mkdir)( |$)", (f, job_id))

    def test_the_session_prepares_in_one_job_and_installs_the_agent_only_for_agents(self):
        with open(os.path.join(ENGINE, ".github", "workflows", "session.yml"), encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        self.assertEqual(list(doc["jobs"]), ["prep", "policy", "session", "checks_prep", "checks", "epilogue", "start", "stage_failed"])
        self.assertEqual([j["name"] for j in doc["jobs"].values()],
                         ["Prepare the session", "Prepare the session inputs (policy scripts)", "Run the session", "Prepare the checks",
                          "Check the results", "Take in results", "Iteration start", "Try the session again later"])
        # a failed "Prepare the session" runs no later job but this one, which parks the session for its next attempt
        retry = doc["jobs"]["stage_failed"]
        self.assertEqual((retry["needs"], retry["if"]), ("prep", "always() && needs.prep.result == 'failure'"))
        self.assertIn("loop.py stage-failed", str(retry["steps"]))
        install = next(s for s in doc["jobs"]["session"]["steps"] if s["name"].startswith("Install "))
        self.assertEqual(install["if"], "needs.prep.outputs.kind == 'agent'")
        self.assertNotIn("Claude Code", install["name"])               # the tool comes from the engine's output, not from the file
        self.assertEqual(sum("actions/checkout" in s.get("uses", "") for s in doc["jobs"]["prep"]["steps"]), 2)

    def test_the_tokenless_run_name_is_the_one_the_engine_looks_for(self):
        with open(os.path.join(ENGINE, ".github", "workflows", "tokenless.yml"), encoding="utf-8") as fh:
            name = yaml.safe_load(fh)["run-name"]
        rendered = name.replace("${{ inputs.purpose || 'command' }}", "change gate for #12").replace("${{ inputs.key }}", "abc123")
        self.assertEqual(rendered, c.tokenless_title("change gate for #12", "abc123"))

    def test_engine_files_the_workflows_and_scripts_name_exist(self):
        for f, text, doc in self.workflows():
            for m in re.findall(r"\.github/loop/([A-Za-z_.-]+)", text):
                self.assertTrue(os.path.exists(os.path.join(ENGINE, ".github", "loop", m)), (f, m))

    def test_the_agent_never_gets_the_github_token(self):
        with open(os.path.join(ENGINE, ".github", "workflows", "session.yml"), encoding="utf-8") as fh:
            text = fh.read()
        workflow = yaml.safe_load(text)
        step = str(next(step for step in workflow["jobs"]["session"]["steps"] if "agent.sh" in step.get("run", "")))
        self.assertNotIn("github.token", step)
        self.assertNotIn("GH_TOKEN", step)
        self.assertNotIn("ANTHROPIC_API_KEY", step)
        with open(os.path.join(ENGINE, ".github", "loop", "agent.sh"), encoding="utf-8") as fh:
            sh = fh.read()
        self.assertIn("unset CLAUDE_CODE_OAUTH_TOKEN GH_TOKEN GITHUB_TOKEN", sh)
        code = [l for l in sh.splitlines() if not l.strip().startswith("#")]
        self.assertEqual([l for l in code if re.search(r"(^|[\s;|&(])gh\s", l)], [])          # the agent has no gh


    def test_the_tokenless_job_has_no_permissions_and_no_secret(self):
        for name in ("tokenless-job.yml", "tokenless.yml"):
            with open(os.path.join(ENGINE, ".github", "workflows", name), encoding="utf-8") as fh:
                text = fh.read()
            job = yaml.safe_load(text)["jobs"]["run"]
            self.assertEqual(job["permissions"], {}, name)
            if name == "tokenless-job.yml":
                self.assertEqual(yaml.safe_load(text)["permissions"], {})
                self.assertNotIn("secrets", text.replace("no GitHub secret", ""))
                self.assertNotIn("github.token", text)
                self.assertNotIn("GH_TOKEN", text)

    def test_the_session_job_has_no_permissions_and_only_the_claude_token(self):
        with open(os.path.join(ENGINE, ".github", "workflows", "session.yml"), encoding="utf-8") as fh:
            jobs = yaml.safe_load(fh)["jobs"]
        job = jobs["session"]
        self.assertEqual(job["permissions"], {})
        text = str(job)
        for forbidden in ("github.token", "GH_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_API_KEY", "actions/checkout"):
            self.assertNotIn(forbidden, text)
        text = re.sub(r"contains\(needs\.prep\.outputs\.secrets, '\|\w+\|'\) && secrets\.\w+ \|\| ''", "", text)    # consented names
        self.assertEqual(set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text)), {"CLAUDE_CODE_OAUTH_TOKEN"})
        # the token jobs sit on both sides of it and are other machines
        self.assertEqual(job["needs"], ["prep", "policy"])
        self.assertEqual(jobs["epilogue"]["needs"], ["prep", "policy", "session", "checks_prep", "checks"])
        self.assertIn("github.token", str(jobs["epilogue"]))

    def test_the_policy_scripts_run_in_a_job_without_the_token_that_the_session_waits_for(self):
        with open(os.path.join(ENGINE, ".github", "workflows", "session.yml"), encoding="utf-8") as fh:
            jobs = yaml.safe_load(fh)["jobs"]
        job = jobs["policy"]
        self.assertEqual((job["needs"], job["permissions"]), ("prep", {}))
        text = str(job)
        for forbidden in ("github.token", "GH_TOKEN", "GITHUB_TOKEN", "secrets.", "actions/checkout", "loop.py stage-inputs", "pip install"):
            self.assertNotIn(forbidden, text)
        self.assertEqual(sum("run-policy-scripts" in step.get("run", "") for step in job["steps"]), 1)
        # the session starts only on an `ok` result, and every artifact a job downloads is checked against a digest from the job that made it
        self.assertIn("needs.policy.outputs.ok == 'true'", jobs["session"]["if"])
        for job_id, digests in (("policy", ("needs.prep.outputs.staged", "needs.prep.outputs.bundle")),
                                ("session", ("needs.prep.outputs.staged", "needs.policy.outputs.digest"))):
            check = next(step for step in jobs[job_id]["steps"] if step["name"].startswith("Check the downloads"))
            self.assertEqual(sorted(re.findall(r"\$\{\{ (needs\.\w+\.outputs\.\w+) \}\}", str(check["env"]))), sorted(digests))
            self.assertEqual(sum(step["name"].startswith("Download") for step in jobs[job_id]["steps"][:len(digests) + 1]), len(digests))

    def test_the_checks_run_in_a_job_without_the_token_whose_outputs_alone_carry_the_results(self):
        with open(os.path.join(ENGINE, ".github", "workflows", "session.yml"), encoding="utf-8") as fh:
            jobs = yaml.safe_load(fh)["jobs"]
        job = jobs["checks"]
        self.assertEqual((job["needs"], job["permissions"]), (["prep", "checks_prep"], {}))
        text = str(job)
        for forbidden in ("github.token", "GH_TOKEN", "GITHUB_TOKEN", "secrets.", "actions/checkout", "pip install"):
            self.assertNotIn(forbidden, text)
        self.assertIn("needs.checks_prep.outputs.count != '0'", job["if"])
        check = next(step for step in job["steps"] if step["name"].startswith("Check the download"))
        self.assertEqual(re.findall(r"\$\{\{ (needs\.\w+\.outputs\.\w+) \}\}", str(check["env"])), ["needs.checks_prep.outputs.digest"])
        self.assertEqual(sum("run-checks" in step.get("run", "") for step in job["steps"]), 1)
        # "Prepare the checks" holds the token and runs only the engine; "Take in results" reads the results from the job outputs
        self.assertIn("prepare-checks", str(jobs["checks_prep"]))
        take = next(step for step in jobs["epilogue"]["steps"] if "take-results" in step.get("run", ""))
        self.assertEqual(take["env"]["CHECKS_RESULTS"], "${{ needs.checks.outputs.results }}")
        self.assertNotIn("checks", [s["with"]["name"] for s in jobs["epilogue"]["steps"] if "download-artifact" in s.get("uses", "")])

    def test_the_iteration_start_decides_without_the_token_and_starts_only_from_the_job_outputs(self):
        with open(os.path.join(ENGINE, ".github", "workflows", "iteration-start.yml"), encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        self.assertIn("workflow_call", doc[True])                       # PyYAML reads `on:` as True
        decide, begin = doc["jobs"]["decide"], doc["jobs"]["start"]
        self.assertEqual([j["name"] for j in (decide, begin)], ["Decide whether the Policymaker runs", "Start the iteration"])
        self.assertEqual(decide["permissions"], {})
        for forbidden in ("github.token", "GH_TOKEN", "GITHUB_TOKEN", "secrets.", "actions/checkout", "pip install"):
            self.assertNotIn(forbidden, str(decide))
        check = next(step for step in decide["steps"] if step["name"].startswith("Check the download"))
        self.assertEqual(re.findall(r"\$\{\{ (inputs\.\w+) \}\}", str(check["env"])), ["inputs.digest"])
        self.assertEqual(sum("run-checks" in step.get("run", "") for step in decide["steps"]), 1)
        # the start reads the decision from the job outputs only, never from an artifact, and runs even when the decision job did not
        self.assertEqual(begin["needs"], "decide")
        self.assertTrue(begin["if"].startswith("always()"))
        step = next(step for step in begin["steps"] if "start-iteration" in step.get("run", ""))
        self.assertEqual(step["env"], {"DECIDE_RESULTS": "${{ needs.decide.outputs.results }}"})
        self.assertFalse(any("download-artifact" in s.get("uses", "") for s in begin["steps"]))
        # both runs that can find a start due call it with the digest of the facts they pinned
        for name, job_id, command in (("session.yml", "epilogue", "take-results"), ("watchdog.yml", "watch", "watch")):
            with open(os.path.join(ENGINE, ".github", "workflows", name), encoding="utf-8") as fh:
                jobs = yaml.safe_load(fh)["jobs"]
            call = jobs["start"]
            self.assertEqual((call["name"], call["uses"], call["needs"]), ("Iteration start", "./.github/workflows/iteration-start.yml", job_id))
            self.assertEqual(call["with"], {k: "${{ needs.%s.outputs.%s }}" % (job_id, k) for k in ("count", "minutes", "digest")})
            run = next(s for s in jobs[job_id]["steps"] if "loop.py %s" % command in s.get("run", ""))
            self.assertEqual(run["env"]["START_PLAN"], "${{ runner.temp }}/start-facts")
            upload = next(s for s in jobs[job_id]["steps"] if s["name"] == "Upload the facts for the iteration start")
            self.assertEqual(upload["with"]["name"], "start-facts")

    @unittest.skipUnless(BASH, "no bash")
    def test_the_shell_check_of_the_facts_refuses_a_planted_file(self):
        import tempfile
        with open(os.path.join(ENGINE, ".github", "workflows", "iteration-start.yml"), encoding="utf-8") as fh:
            check = next(s for s in yaml.safe_load(fh)["jobs"]["decide"]["steps"] if s["name"].startswith("Check the download"))
        with tempfile.TemporaryDirectory() as tmp:
            facts = os.path.join(tmp, "start-facts")
            os.makedirs(facts)
            with open(os.path.join(facts, "checks.tar.gz"), "w", encoding="utf-8") as fh:
                fh.write("a")
            digest = artifact_pin.digest(facts)

            def check_runs():
                env = dict(os.environ, RUNNER_TEMP=tmp, FACTS=digest)
                return subprocess.run([BASH, "-e", "-c", check["run"]], env=env, capture_output=True, text=True).returncode
            self.assertEqual(check_runs(), 0)
            with open(os.path.join(facts, "results.json"), "w", encoding="utf-8") as fh:
                fh.write("planted")
            self.assertNotEqual(check_runs(), 0)

    @unittest.skipUnless(BASH, "no bash")
    def test_the_shell_check_of_a_download_refuses_a_swapped_artifact(self):
        """The `Check the downloads` step of the session job, run as it is: the digest `artifact_pin` computes passes, a swapped,
        added or missing file does not."""
        import tempfile
        with open(os.path.join(ENGINE, ".github", "workflows", "session.yml"), encoding="utf-8") as fh:
            check = next(step for step in yaml.safe_load(fh)["jobs"]["session"]["steps"] if step["name"].startswith("Check the downloads"))
        with tempfile.TemporaryDirectory() as tmp:
            ship, results = os.path.join(tmp, "ship"), os.path.join(tmp, "policy-results")
            for directory, files in ((ship, {"trusted.tar.gz": "a", "work.tar.gz": "b"}), (results, {"session.tar.gz": "c", "prompt.md": "d"})):
                os.makedirs(directory)
                for name, text in files.items():
                    with open(os.path.join(directory, name), "w", encoding="utf-8") as fh:
                        fh.write(text)
            hidden = os.path.join(results, ".x")

            def check_runs(digest_of_results=None):
                env = dict(os.environ, RUNNER_TEMP=tmp, STAGED=artifact_pin.digest(ship), RESULTS=digest_of_results or artifact_pin.digest(results))
                return subprocess.run([BASH, "-e", "-c", check["run"]], env=env, capture_output=True, text=True).returncode
            self.assertEqual(check_runs(), 0)
            digest = artifact_pin.digest(results)
            with open(os.path.join(results, "prompt.md"), "w", encoding="utf-8") as fh:
                fh.write("swapped")
            self.assertNotEqual(check_runs(digest), 0)
            with open(os.path.join(results, "prompt.md"), "w", encoding="utf-8") as fh:
                fh.write("d")
            self.assertEqual(check_runs(digest), 0)
            with open(hidden, "w", encoding="utf-8") as fh:
                fh.write("added")
            self.assertNotEqual(check_runs(digest), 0)
            os.remove(hidden)
            os.remove(os.path.join(results, "prompt.md"))
            self.assertNotEqual(check_runs(digest), 0)

    def test_every_checkout_leaves_no_credentials_behind(self):
        for f, text, doc in self.workflows():
            for step in [s for j in doc["jobs"].values() for s in j.get("steps", [])]:
                if str(step.get("uses", "")).startswith("actions/checkout"):
                    self.assertIs((step.get("with") or {}).get("persist-credentials"), False, f)


@TEMPLATE_ONLY
class ConfigTest(unittest.TestCase):
    def load(self, *parts):
        with open(os.path.join(ENGINE, *parts), encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    def test_engine_yml_has_only_known_keys_and_shows_every_default(self):
        doc = self.load("engine.yml")
        self.assertEqual(set(doc) - set(c.DEFAULTS), set())
        for k in c.DEFAULTS:
            self.assertIn(k, doc, "engine.yml should show %s" % k)

    def test_the_setup_parameters_are_null_on_purpose(self):
        doc = self.load("engine.yml")
        self.assertEqual(set(doc["params"].values()) | set(doc["cadence"].values()), {None})
        self.assertEqual(self.load("policy", "params.yml")["stream_retention_iterations"], 8)     # #87: streams are pruned by default

    def test_template_is_layered_with_policy_roles_and_no_execution_roles(self):
        self.assertEqual(self.load('engine.yml')['resources'], {'format': 'layered'})
        self.assertIsNone(self.load('policy', 'params.yml')['shaper'])
        self.assertEqual(self.load('execution', 'profiles.yml'), {})
        self.assertFalse(os.path.exists(os.path.join(ENGINE, 'execution', 'roles')))
        self.assertFalse(os.path.exists(os.path.join(ENGINE, 'templates')))

    def test_shipped_policy_roles_are_complete_and_within_limits(self):
        w = fakes.World()
        try:
            profs = self.load('policy', 'profiles.yml')
            self.assertEqual(set(profs), {'audit', 'evaluator', 'evaluator-verifier'})
            for role, profile in profs.items():
                self.assertEqual(requests.limits_exceeded(profile), [], role)
            for role in [*profs, 'policymaker']:
                path = os.path.join(ENGINE, 'policy', 'roles', role, 'SKILL.md')
                with open(path, encoding='utf-8') as fh:
                    fm = it.frontmatter(fh.read())[0]
                self.assertEqual(fm.get('name'), role)
                self.assertTrue(fm.get('description'), role)
        finally:
            w.close()

    def test_the_protocol_names_the_fields_the_engine_owns(self):
        with open(os.path.join(ENGINE, ".github", "loop", "session-protocol.md"), encoding="utf-8") as fh:
            text = fh.read()
        for field in ("attempts", "resume_after", "kicks", "conflicts", "fixes", "cause", "verdict", "merged", "branch", "artifacts"):
            self.assertIn(field, text)
            self.assertIn(field, c.OWNED)


class ProtectionTest(unittest.TestCase):
    def test_the_engines_own_files_are_protected_from_every_session(self):
        prot = c.DEFAULTS["protected"]
        for p in (".github/workflows/session.yml", ".github/loop/loop.py", "engine.yml", "ENGINE_VERSION", "iterations/0001.json",
                  "iterations/0001/tasks.json", "goal/gate.yml", "GOAL_REACHED"):
            self.assertTrue(c.path_match(p, prot), p)
        for p in ("policy/params.yml", "journal/0001.md", "src/x.py", "audit/0001.md"):
            self.assertFalse(c.path_match(p, prot), p)

    @TEMPLATE_ONLY
    def test_no_shipped_profile_can_write_the_engine(self):
        with open(os.path.join(ENGINE, "policy", "profiles.yml"), encoding="utf-8") as fh:
            profs = yaml.safe_load(fh)
        for role, p in profs.items():
            for g in p.get("write") or []:
                self.assertFalse(c.path_match(g.replace("**", "x"), c.DEFAULTS["protected"]), (role, g))


# A module that outgrows MODULE_LINES is hard for a session to read whole; new behavior goes into a module of its own.
# These two are already bigger: their size is frozen, may only shrink, and the entry is deleted once the file fits.
MODULE_LINES = 800
FROZEN_LINES = {"iteration.py": 863}


class ModuleSizeTest(unittest.TestCase):
    def test_no_engine_module_outgrows_its_limit(self):
        loop_dir = os.path.join(ENGINE, ".github", "loop")
        bad = []
        for name in sorted(f for f in os.listdir(loop_dir) if f.endswith(".py")):
            with open(os.path.join(loop_dir, name), encoding="utf-8") as fh:
                lines = len(fh.readlines())
            frozen = FROZEN_LINES.get(name)
            if frozen is None and lines > MODULE_LINES:
                bad.append("%s has %d lines, the limit is %d" % (name, lines, MODULE_LINES))
            elif frozen is not None and lines > frozen:
                bad.append("%s has %d lines, its frozen limit is %d" % (name, lines, frozen))
            elif frozen is not None and lines < frozen:
                bad.append("%s shrank to %d lines: lower its FROZEN_LINES entry to %d%s" % (
                    name, lines, lines, "" if lines > MODULE_LINES else " (delete it, it is under %d now)" % MODULE_LINES))
        self.assertEqual(bad, [], "Do not make a module bigger to fit new behavior: extract it into a module of its own.")


class CodeMapTest(unittest.TestCase):
    def test_every_engine_module_is_in_the_code_map(self):
        with open(os.path.join(ENGINE, "docs", "perpetua", "code-map.md"), encoding="utf-8") as fh:
            text = fh.read()
        missing = [f for f in sorted(os.listdir(os.path.join(ENGINE, ".github", "loop")))
                   if f.endswith(".py") and not re.search(r"(?<![\w-])" + re.escape(f), text)]
        self.assertEqual(missing, [], "modules missing from docs/perpetua/code-map.md: add a row for each")


if __name__ == "__main__":
    unittest.main()
