"""Static checks of the engine as a whole: it parses, it is self-consistent, and it carries nothing of the project it came from.
This file is ASCII only (checked below), so it hides no Polish either."""
import os
import re
import subprocess
import unittest

import yaml

import fakes
from fakes import ENGINE, common as c, iteration as it

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
            self.assertEqual(seen, {"session.yml", "resume.yml", "watchdog.yml", "owner-request.yml", "tokenless.yml", "tokenless-job.yml"})

    def test_run_names_keep_their_hash(self):
        """An unquoted ' #' in a YAML value is a comment: run-name became 'session' and no run matched its session (#84)."""
        for f, text, doc in self.workflows():
            name = doc.get("run-name")
            if name is None:
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
                if "uses: actions/checkout@v4" in line:
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

    def test_engine_files_the_workflows_and_scripts_name_exist(self):
        for f, text, doc in self.workflows():
            for m in re.findall(r"\.github/loop/([A-Za-z_.-]+)", text):
                self.assertTrue(os.path.exists(os.path.join(ENGINE, ".github", "loop", m)), (f, m))

    def test_the_agent_never_gets_the_github_token(self):
        with open(os.path.join(ENGINE, ".github", "workflows", "session.yml"), encoding="utf-8") as fh:
            text = fh.read()
        workflow = yaml.safe_load(text)
        step = str(next(step for step in workflow["jobs"]["session"]["steps"] if step.get("name") == "Session"))
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
        self.assertEqual(job["needs"], ["prep", "stage"])
        self.assertEqual(jobs["epilogue"]["needs"], ["prep", "stage", "session"])
        self.assertIn("github.token", str(jobs["epilogue"]))

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
                self.assertEqual(it.limits_exceeded(profile), [], role)
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


if __name__ == "__main__":
    unittest.main()
