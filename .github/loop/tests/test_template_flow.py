"""A fresh shipped template, explicitly selected roles and a controlled full iteration.

GitHub/provider sessions are simulated; git, setup, staging, gates, receipts and
rollback are real. Kernel/provider isolation is tested separately on Linux.
"""
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import fakes
from fakes import common as c, iteration as it, loop, task, tasks_json
import managed_resources as resources
import policy_changes
import session_reports as reports
import setup_roles
from test_merge import GATE_SH
from test_reports import PROPOSAL


class TemplateFlowTest(unittest.TestCase):
    def setUp(self):
        self.w = fakes.World()
        self.addCleanup(self.w.close)
        root = Path(self.w.loop)
        # Replace fixture definitions with precisely the material setup copies.
        for name in ('execution', 'policy'):
            shutil.rmtree(root / name, ignore_errors=True)
            shutil.copytree(Path(fakes.ENGINE, name), root / name)
        shutil.copyfile(Path(fakes.ENGINE, 'engine.yml'), root / 'engine.yml')
        setup_roles.prepare(root)
        # The setup map writes the execution skills; the policy roles ship with the engine.
        for role in ('orchestrator', 'worker'):
            skill = root / 'execution/roles' / role / 'SKILL.md'
            skill.parent.mkdir(parents=True)
            skill.write_text('---\nname: %s\ndescription: test role\n---\n# %s\n' % (role, role), encoding='utf-8')
        setup_roles.install(root, 'orchestrator', 'execution', {'tools': ['Read', 'Write'], 'write': ['journal/**']})
        setup_roles.install(root, 'worker', 'execution', {'tools': ['Read', 'Write'], 'write': ['src/**']})
        params = c.load_yaml(root / 'policy/params.yml')
        params['shaper'] = 'orchestrator'
        params['policymaker_triggers'] = {'every_iterations': None, 'no_progress': None}
        setup_roles.write_yaml(root / 'policy/params.yml', params)
        config = c.load_yaml(root / 'engine.yml')
        config['gate'] = {'kind': 'script', 'command': 'bash policy/evaluator/gate.sh'}
        setup_roles.write_yaml(root / 'engine.yml', config)
        self.w.write(root, 'policy/evaluator/gate.sh', GATE_SH)
        fakes.sh(root, 'git', 'add', '-A')
        fakes.sh(root, 'git', 'commit', '-qm', 'owner accepted explicit setup')
        fakes.sh(root, 'git', 'tag', 'setup-baseline')
        fakes.sh(root, 'git', 'push', '-q', 'origin', 'main')
        self.initial = setup_roles.validate(root, 'setup-baseline')
        os.environ['GITHUB_OUTPUT'] = str(root.parent / 'outputs')
        home = Path(self.w.tmp, 'home')
        home.mkdir()
        os.environ['HOME'] = os.environ['USERPROFILE'] = str(home)
        self.hub = self.w.hub

    def stage(self, issue):
        loop.resolve(issue)
        work = Path(self.w.tmp, 'stage-' + str(issue))
        fakes.sh(self.w.tmp, 'git', 'clone', '-q', self.w.origin, str(work))
        loop.stage(issue, str(work), str(work.parent / ('trusted-' + str(issue))))
        return work

    def test_selected_shaper_product_suggestion_atomic_change_and_rollback(self):
        self.assertEqual(it.start_iteration(), 0)
        shaper = self.hub.role_issues('orchestrator')[0]['number']
        staged = self.stage(shaper)
        capabilities = json.loads((staged / '.session/capabilities.json').read_text())
        self.assertIn('worker', [entry['role'] for entry in capabilities])
        self.assertNotIn('audit', [entry['role'] for entry in capabilities])
        self.w.session(shaper, session_files={'tasks.json': tasks_json(task('build'))})
        worker = self.hub.role_issues('worker')[0]['number']
        self.stage(worker)
        self.w.session(worker, files={'src/product.txt': 'accepted work'},
                       session_files={'suggestions.json': json.dumps([PROPOSAL])})
        self.assertEqual(self.w.origin_file('src/product.txt'), 'accepted work')
        record = json.loads(self.w.origin_file(it.record_path(0)))
        self.assertEqual(record['status'], 'closed')
        pending = reports.pending()
        self.assertEqual(len(pending), 1)
        # Keep the controller at an explicit boundary while a new set is prepared.
        with patch.object(loop, 'reconcile'):
            policymaker = it.start_policymaker(1, 'new session suggestion')
            staged = self.stage(policymaker)
            state = json.loads((staged / '.session/loop-state.json').read_text())
            self.assertEqual(state['suggestions'][0]['id'], pending[0]['id'])
            self.w.session(policymaker,
                           files={'execution/roles/worker/reference.md': 'public work guidance',
                                  'policy/roles/policymaker/reference.md': 'private governance'},
                           session_files={'suggestion-decisions.json': json.dumps([
                               {'id': pending[0]['id'], 'status': 'handled', 'reason': 'adapted both layers'}])})
            self.assertIsNone(self.w.origin_file('execution/roles/worker/reference.md'))
            loop.apply_policy()
        c.sync_main()
        receipt = json.loads(self.w.origin_file(policy_changes.path(policymaker)))
        self.assertEqual(receipt['policy_rev_before'], self.initial)
        self.assertEqual(receipt['policy_rev_after'], resources.revision())
        self.assertEqual(reports.pending(), [])
        self.assertEqual(self.w.origin_file('policy/roles/policymaker/reference.md'), 'private governance')
        self.assertTrue(it.revert_policy('controlled integration rollback'))
        self.assertEqual(resources.revision(), self.initial)
        self.assertIsNone(self.w.origin_file('execution/roles/worker/reference.md'))
        self.assertIsNone(self.w.origin_file('policy/roles/policymaker/reference.md'))
        self.assertEqual(self.w.origin_file('src/product.txt'), 'accepted work')
        self.assertIsNotNone(self.w.origin_file(it.record_path(0)))
        self.assertIsNotNone(self.w.origin_file(policy_changes.path(policymaker)))
