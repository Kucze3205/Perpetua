"""Iteration world and helpers shared by behavior-specific flow suites."""
import json
import unittest

import fakes
from fakes import common as c, iteration as it, task, tasks_json
from gate_fixtures import GATE_SH, SCRIPT_GATE


class Flow(unittest.TestCase):
    YML = SCRIPT_GATE
    PARAMS = None
    FIRST = False       # True: no Policymaker session has run yet, so the first iteration starts with one

    def setUp(self):
        self.w = fakes.worker_world(self.YML, first_iteration=self.FIRST)
        self.hub = self.w.hub
        files = {"policy/evaluator/gate.sh": GATE_SH}
        if self.PARAMS:
            files["policy/params.yml"] = fakes.POLICY_PARAMS + self.PARAMS
        self.w.push_main(files, "setup")
        c.sync_main()

    def tearDown(self):
        self.w.close()

    # -- helpers
    def open_iteration(self):
        return it.open_iteration()

    def session_issue(self, role, state="open", n=None):
        found = [x for x in self.hub.role_issues(role, state) if n is None or c.iteration_of(c.label_names(x)) == n]
        return found[-1]["number"] if found else None

    def play_shaper(self, *tasks, files=None, k=0):
        s = self.session_issue("orchestrator", n=k)
        self.w.session(s, files=files or {"journal/%04d.md" % k: "## State\nx\n"}, session_files={"tasks.json": tasks_json(*tasks)})
        return s

    def task_issue(self, tid):
        for x in self.hub.list_issues("all"):
            if c.marker(x["body"], "task-id") == tid:
                return x["number"]

    def record(self, k):
        t = self.w.origin_file(it.record_path(k))
        return json.loads(t) if t else None

    def finish(self, k, tid=None):
        """Iteration k from its shaper to its record, with one worker task."""
        tid = tid or "t%s" % k
        self.play_shaper(task(tid), k=k)
        self.w.session(self.task_issue(tid), files={"src/%s.txt" % tid: tid})

    def policymaker_session(self, n=0):
        """A Policymaker session opening iteration n, started by hand (the iteration is open without waiting for it)."""
        return it.start_policymaker(n, "a test")
