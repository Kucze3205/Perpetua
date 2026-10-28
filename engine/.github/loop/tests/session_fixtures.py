"""Session worlds and staging checkouts; no test scenarios live here."""
import os
import unittest

import fakes
from fakes import common as c
from gate_fixtures import GATE_SH, SCRIPT_GATE


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


EXTRA_PROFILES = """ghost:
  write: ["src/**"]
  tools: [Read]
rule:
  agent: false
  command: "echo shaping"
  write: []
cmdless:
  agent: false
"""


class SessionCase(unittest.TestCase):
    YML = SCRIPT_GATE

    def setUp(self):
        self.w = fakes.worker_world(self.YML)
        self.hub = self.w.hub
        self.w.push_main({"policy/evaluator/gate.sh": GATE_SH, "execution/profiles.yml": fakes.EXECUTION_PROFILES + EXTRA_PROFILES}, "setup")
        c.sync_main()
        self.out = os.path.join(self.w.tmp, "gh-output")
        os.environ["GITHUB_OUTPUT"] = self.out
        self.home = os.path.join(self.w.tmp, "home")
        os.makedirs(self.home)
        os.environ["HOME"] = os.environ["USERPROFILE"] = self.home

    def tearDown(self):
        self.w.close()

    def issue(self, role="worker", body="## Goal\nx\n", labels=(), title="T"):
        return self.hub.create_issue(title, body, labels=["role:" + role, "loop:iteration 1"] + list(labels))

    def outputs(self):
        with open(self.out) as fh:
            return dict(l.rstrip("\n").split("=", 1) for l in fh if "=" in l)

    def work(self):
        self.w.n_work += 1
        d = os.path.join(self.w.tmp, "stagework%s" % self.w.n_work)
        fakes.sh(self.w.tmp, "git", "clone", "-q", "-c", "core.autocrlf=false", self.w.origin, d)
        return d


class LayeredSessionCase(SessionCase):
    def setUp(self):
        super().setUp()
        # A controlled owner-selected layout, leaving unrelated inactive skills.
        self.w.write(self.w.loop, 'engine.yml', 'resources:\n  format: layered\n')
        self.w.write(self.w.loop, 'policy/params.yml', fakes.POLICY_PARAMS.replace('orchestrator', 'worker'))
        self.w.write(self.w.loop, 'execution/profiles.yml', 'worker:\n  tools: [Read, Write]\n  write: [src/**, execution/**, policy/**]\n')
        self.w.write(self.w.loop, 'policy/profiles.yml', 'audit:\n  tools: [Read]\n  write: [audit/**]\n')
        for layer, role in (('execution', 'worker'), ('policy', 'audit'), ('policy', 'policymaker')):
            self.w.write(self.w.loop, '%s/roles/%s/SKILL.md' % (layer, role), '---\nname: %s\ndescription: fixture\n---\n%s role\n' % (role, layer))
        self.w.write(self.w.loop, 'execution/prompts/role.md', 'Execution prompt')
        self.w.write(self.w.loop, 'execution/protocol.md', 'Execution rules')
        fakes.sh(self.w.loop, 'git', 'add', '-A')
        fakes.sh(self.w.loop, 'git', 'commit', '-qm', 'layered setup')
        fakes.sh(self.w.loop, 'git', 'push', '-q', 'origin', 'HEAD:main')
