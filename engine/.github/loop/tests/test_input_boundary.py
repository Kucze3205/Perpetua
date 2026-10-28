"""Layer-aware inputs at the public session staging boundary."""
import json
import os

import fakes
from fakes import common as c, iteration as it, loop, task
import session_reports as reports
from test_reports import PROPOSAL
from test_layered_resources import LayeredSessionTest
from test_session import read


class InputBoundaryTest(LayeredSessionTest):
    def started_issue(self, *args, **kwargs):
        n = self.issue(*args, **kwargs)
        loop.resolve(n)
        return n

    def stage_inputs(self, role='worker', body='', shaper=False, state=None, work=None):
        n = self.issue(role, body=body, labels=['loop:shaper'] if shaper else [])
        if state is not None:
            it.store_state(n, state)
        work = work or self.work()
        trusted = os.path.join(self.w.tmp, 'trusted-input-%s' % n)
        loop.stage(n, work, trusted)
        return n, os.path.join(work, '.session'), trusted

    def test_shaper_gets_execution_catalog_and_the_loop_state(self):
        worker = self.started_issue()
        c.update_report(worker, {'status': 'done'}, 'USEFUL WORK')
        state = {'schema_version': 1, 'iteration': 1, 'iterations': [{'n': 1}],
                 'goal': {'text': 'Build the product'}}
        self.w.write(self.w.loop, 'execution/roles/worker/reference.md', 'USEFUL REFERENCE')
        _, sess, trusted = self.stage_inputs(shaper=True, state=state)
        self.assertIn('USEFUL REFERENCE', read(os.path.join(sess, 'role/reference.md')))
        self.assertIn('Build the product', read(os.path.join(sess, 'loop-state.json')))
        catalog = json.loads(read(os.path.join(sess, 'capabilities.json')))
        self.assertEqual([x['role'] for x in catalog], ['worker'])
        self.assertNotIn('tools', json.dumps(catalog))
        self.assertIn('capabilities.json', read(os.path.join(trusted, 'prompt.md')))
        self.assertIn('USEFUL WORK', read(os.path.join(sess, 'reports/%s.json' % worker)))

    def test_an_execution_role_is_staged_with_policy_sessions_and_asked_not_to_read_policy(self):
        policy = self.started_issue('audit')
        c.update_report(policy, {'status': 'done'}, 'AUDIT FINDINGS')
        requests = [{'source': 'reports', 'sessions': [policy]}]
        _, sess, trusted = self.stage_inputs(body='<!-- exports: %s -->' % json.dumps(requests))
        self.assertIn('AUDIT FINDINGS', read(os.path.join(sess, 'exports/reports/%s.json' % policy)))
        self.assertIn('Do not read `policy/`', read(os.path.join(trusted, 'contract.md')))
        _, _, trusted = self.stage_inputs('audit')
        self.assertNotIn('Do not read `policy/`', read(os.path.join(trusted, 'contract.md')))

    def test_shaper_input_lists_every_alias_and_effort_validate_tasks_accepts(self):
        _, sess, _ = self.stage_inputs(shaper=True, state={'iteration': 1})
        options = json.loads(read(os.path.join(sess, 'loop-state.json')))['task_options']
        self.assertIn('opus', options['models'])
        self.assertEqual(set(options['models']), set(c.params()['models']))
        self.assertNotIn('claude-opus-5-5', json.dumps(options))
        for alias in options['models']:
            self.assertEqual(it.validate_tasks({'tasks': [task('a', model=alias)]}), [])
        for effort in options['efforts']:
            self.assertEqual(it.validate_tasks({'tasks': [task('a', effort=effort)]}), [])
        self.assertTrue(it.validate_tasks({'tasks': [task('a', model='sonnet')]}))
        self.assertIn('max_turns', options['ceilings'])
        catalog = json.loads(read(os.path.join(sess, 'capabilities.json')))
        self.assertEqual([x['description'] for x in catalog], ['fixture'])

    def test_a_session_without_a_shell_is_not_told_to_branch_commit_or_run_commands(self):
        # The real starting template and the real contract, staged for a role whose tools have no Bash.
        template = open(os.path.join(fakes.ENGINE, 'execution/prompts/role.md'), encoding='utf-8').read()
        self.w.write(self.w.loop, 'execution/prompts/role.md', template)
        _, sess, trusted = self.stage_inputs(shaper=True)
        prompt = read(os.path.join(trusted, 'prompt.md'))
        self.assertNotIn('branch', prompt)
        self.assertNotIn('verification command', prompt)
        contract = read(os.path.join(trusted, 'contract.md'))
        self.assertNotIn('Run it yourself before `done`', contract)
        self.assertIn('You have no shell', contract)
        self.assertIn('the engine imports the files you wrote', contract)

    def test_contract_scope_follows_role_and_shaper_duty(self):
        for role, shaper in [('worker', False), ('worker', True), ('audit', False), ('policymaker', False)]:
            with self.subTest(role=role, shaper=shaper):
                _, sess, trusted = self.stage_inputs(role, shaper=shaper)
                contract = read(os.path.join(trusted, 'contract.md'))
                prompt = read(os.path.join(trusted, 'prompt.md'))
                self.assertFalse(os.path.exists(os.path.join(sess, 'protocol.md')))
                self.assertNotIn('.session/protocol.md', prompt + contract)
                self.assertIn('You have no `gh`, no GitHub token', contract)
                self.assertIn('.session/requests.json', contract)
                self.assertIn('.session/report.md', contract)
                self.assertEqual('## The task graph' in contract, shaper or role == 'policymaker')
                self.assertEqual('## Historical reports' in contract, shaper or role == 'policymaker')
                self.assertEqual('## Suggestion decisions' in contract, role == 'policymaker')
                self.assertNotIn('# Session protocol', prompt)

    def test_policy_contract_imports_files_and_asks_for_no_commit(self):
        for role in ('audit', 'policymaker'):
            with self.subTest(role=role):
                _, _, trusted = self.stage_inputs(role)
                contract = read(os.path.join(trusted, 'contract.md'))
                self.assertIn('the engine imports the files you wrote, not your commits', contract)
                self.assertNotIn('Work on branch', contract)
                self.assertNotIn('Commit before', contract)
                self.assertNotIn('publishes the files you leave', contract)

    def test_shell_verification_and_git_delivery_are_independent(self):
        self.w.write(self.w.loop, 'engine.yml', 'resources:\n  format: layered\nlimits:\n  tools: [Read, Write, Bash]\n')
        self.w.write(self.w.loop, 'execution/profiles.yml', 'worker:\n  tools: [Read, Write, Bash]\n  write: [src/**]\n')
        self.w.write(self.w.loop, 'policy/profiles.yml', 'audit:\n  tools: [Read, Bash]\n  write: [audit/**]\n')
        for role in ('worker', 'audit'):
            with self.subTest(role=role):
                _, _, trusted = self.stage_inputs(role)
                contract = read(os.path.join(trusted, 'contract.md'))
                self.assertIn("Run the task's verification command", contract)
                self.assertNotIn('Commit before', contract)
                self.assertIn('the engine imports the files you wrote', contract)

    def test_restaging_a_policy_workspace_removes_stale_private_views(self):
        _, sess, _ = self.stage_inputs('policymaker', state={'audit_report': 'PRIVATE POLICY'})
        self.w.write(sess, 'reports/stale.json', 'PRIVATE POLICY')
        _, sess, _ = self.stage_inputs(work=os.path.dirname(sess))
        self.assertFalse(os.path.exists(os.path.join(sess, 'policymaker.md')))
        self.assertFalse(os.path.exists(os.path.join(sess, 'reports/stale.json')))
        self.assertFalse(os.path.exists(os.path.join(sess, 'loop-state.json')))

    def test_catalog_supports_a_valid_dependency_graph_without_policy_roles(self):
        _, sess, _ = self.stage_inputs(shaper=True, state={'iteration': 1})
        catalog = json.loads(read(os.path.join(sess, 'capabilities.json')))
        role = catalog[0]['role']
        graph = {'tasks': [task('build', role=role), task('check', role=role, blocked_by=['build'])]}
        self.assertEqual(it.validate_tasks(graph), [])
        self.assertEqual(it.topological({t['id']: t.get('blocked_by', []) for t in graph['tasks']}), ['build', 'check'])
        self.assertNotIn('audit', [entry['role'] for entry in catalog])

    def test_layered_suggestion_reaches_policy_and_its_decision_is_applied(self):
        worker = self.started_issue()
        raw = json.dumps([PROPOSAL])
        self.w.session(worker, files={'src/result.txt': 'useful'}, session_files={'suggestions.json': raw})
        self.assertEqual(self.w.origin_file('iterations/suggestions/%s.json' % worker), raw)
        pol = it.start_policymaker(1, 'new session suggestions')
        policy_work = self.work()
        loop.stage(pol, policy_work, os.path.join(self.w.tmp, 'pol-trusted'))
        staged = json.loads(read(os.path.join(policy_work, '.session/loop-state.json')))
        self.assertEqual(staged['suggestions'][0]['what'], 'Reduce worker context')
        self.assertEqual(staged['suggestions'][0]['source']['issue'], worker)
        evidence = read(os.path.join(policy_work, '.session/history/sessions/%s/%s.md' %
                                     (worker, staged['suggestions'][0]['source']['snapshot'])))
        self.assertIn('Prose of', evidence)
        decision = {staged['suggestions'][0]['id']: {'status': 'handled', 'reason': 'PRIVATE POLICY rationale'}}
        self.w.session(pol, files={'execution/roles/worker/reference.md': 'new guidance',
                                   'policy/suggestion-decisions.json': json.dumps(decision)},
                       report_extra='PRIVATE POLICY')
        loop.apply_policy()
