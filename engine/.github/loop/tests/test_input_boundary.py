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

    def test_shaper_gets_execution_catalog_and_filtered_state_reports(self):
        worker = self.started_issue()
        policy = self.started_issue('audit')
        c.update_report(worker, {'status': 'done'}, 'USEFUL WORK')
        c.update_report(policy, {'status': 'done'}, 'PRIVATE POLICY')
        state = {'schema_version': 1, 'iteration': 1, 'iterations': [{'n': 1}],
                 'goal': {'text': 'Build the product'}, 'policy_params': {'secret': 'PRIVATE POLICY'},
                 'suggestions': [{'source': {'issue': policy, 'snapshot': 'PRIVATE POLICY'}}],
                 'owner_requests': {'open': [{'detail': 'PRIVATE POLICY'}]},
                 'audit_report': 'PRIVATE POLICY'}
        self.w.write(self.w.loop, 'execution/roles/worker/reference.md', 'USEFUL REFERENCE')
        _, sess, trusted = self.stage_inputs(shaper=True, state=state)
        self.assertIn('USEFUL REFERENCE', read(os.path.join(sess, 'role/reference.md')))
        snapshot = read(os.path.join(sess, 'loop-state.json'))
        self.assertIn('Build the product', snapshot)
        self.assertNotIn('PRIVATE POLICY', snapshot)
        self.assertNotIn('PRIVATE POLICY', read(os.path.join(sess, 'issue.md')))
        catalog = json.loads(read(os.path.join(sess, 'capabilities.json')))
        self.assertEqual([x['role'] for x in catalog], ['worker'])
        self.assertNotIn('tools', json.dumps(catalog))
        self.assertIn('capabilities.json', read(os.path.join(trusted, 'prompt.md')))
        reports = read(os.path.join(sess, 'reports/index.json'))
        self.assertNotIn(str(policy) + '.json', reports)
        self.assertIn('USEFUL WORK', read(os.path.join(sess, 'reports/%s.json' % worker)))

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

    def test_explicit_policy_exports_are_denied_without_downloading(self):
        policy = self.started_issue('audit')
        c.update_report(policy, {'status': 'done'}, 'PRIVATE POLICY')
        self.hub.artifacts = [{'id': 1, 'name': 'stream-0001-%s' % policy,
                               'workflow_run': {'id': 10}}]
        requests = [{'source': source, 'sessions': [policy]} for source in ['reports', 'streams', 'actions']]
        _, sess, _ = self.stage_inputs(body='<!-- exports: %s -->' % json.dumps(requests))
        manifest = json.loads(read(os.path.join(sess, 'exports/manifest.json')))
        self.assertEqual([x['status'] for x in manifest['results']], ['denied'] * 3)
        self.assertNotIn('PRIVATE POLICY', json.dumps(manifest))
        self.assertEqual(self.hub.gh_calls, [])

    def test_records_and_artifact_references_preserve_execution_outputs_only(self):
        worker, policy = self.started_issue(), self.started_issue('audit')
        for n, text in ((worker, 'USEFUL WORK'), (policy, 'PRIVATE POLICY')):
            c.update_report(n, {'status': 'done'}, text + '\n- artifact: result.txt | release: iter-0001/%s-result.txt | size: 1 | sha256: %s' % (n, 'a' * 64))
        self.w.write(self.w.loop, 'iterations/0001.json', json.dumps({
            'iteration': 1, 'policy': {'prompt': 'PRIVATE POLICY'},
            'input_state': {'policy_params': {'prompt': 'PRIVATE POLICY'}},
            'tasks': [{'issue': policy, 'body': 'PRIVATE POLICY'}, {'issue': worker, 'body': 'USEFUL WORK'}],
            'outcome': {'sessions': [{'issue': policy, 'body': 'PRIVATE POLICY'},
                                     {'issue': worker, 'status': 'done', 'policy_context': 'PRIVATE POLICY'}]}}))
        refs = [{'issue': policy, 'name': 'result.txt'}, {'release': 'iter-0001/%s-result.txt' % policy},
                {'release': 'iter-0001/*'}, {'issue': worker, 'name': 'result.txt'}]
        body = '<!-- inputs: %s -->\n<!-- exports: %s -->' % (json.dumps(refs), json.dumps([
            {'source': 'records', 'iterations': [1]}, {'source': 'reports', 'sessions': [worker]}]))
        _, sess, _ = self.stage_inputs(body=body)
        record = read(os.path.join(sess, 'exports/records/0001.json'))
        self.assertNotIn('PRIVATE POLICY', record)
        self.assertIn('USEFUL WORK', record)
        self.assertIn('USEFUL WORK', read(os.path.join(sess, 'exports/reports/%s.json' % worker)))
        manifest = json.loads(read(os.path.join(sess, 'inputs/manifest.json')))
        self.assertEqual([x['status'] for x in manifest['results']], ['denied'] * 3 + ['downloaded'])
        self.assertEqual(len(self.hub.gh_calls), 1)
        self.assertIn('%s-result.txt' % worker, self.hub.gh_calls[0])

    def test_unknown_session_and_stream_origin_fail_closed(self):
        self.hub.artifacts = [{'id': 1, 'name': 'stream-0001-999', 'workflow_run': {'id': 10}}]
        _, sess, _ = self.stage_inputs(body='<!-- exports: [{"source":"streams","sessions":[999]}] -->')
        manifest = json.loads(read(os.path.join(sess, 'exports/manifest.json')))
        self.assertEqual(manifest['results'][0]['status'], 'denied')
        self.assertEqual(self.hub.gh_calls, [])

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

    def test_layered_suggestion_reaches_policy_and_resolution_is_replay_safe(self):
        worker = self.started_issue()
        work = self.w.session(worker, files={'src/result.txt': 'useful'},
                              session_files={'suggestions.json': json.dumps([PROPOSAL])})
        pending = reports.pending()
        self.assertEqual(len(pending), 1)
        reports.capture(worker, work)
        self.assertEqual(reports.pending(), pending)
        pol = it.start_policymaker(1, 'new session suggestions')
        policy_work = self.work()
        loop.stage(pol, policy_work, os.path.join(self.w.tmp, 'pol-trusted'))
        staged = json.loads(read(os.path.join(policy_work, '.session/loop-state.json')))
        self.assertEqual(staged['suggestions'][0]['id'], pending[0]['id'])
        evidence = read(os.path.join(policy_work, '.session/reports/%s-%s.json' %
                                   (worker, pending[0]['source']['snapshot'])))
        self.assertEqual(staged['suggestions'][0]['what'], 'Reduce worker context')
        self.assertIn('Prose of', evidence)
        decision = [{'id': pending[0]['id'], 'status': 'handled', 'reason': 'PRIVATE POLICY rationale'}]
        self.w.session(pol, files={'execution/roles/worker/reference.md': 'new guidance'},
                       report_extra='PRIVATE POLICY',
                       session_files={'suggestion-decisions.json': json.dumps(decision)})
        self.assertEqual(len(reports.pending()), 1)
        loop.apply_policy()
        self.assertEqual(reports.pending(), [])
        final = reports.decisions()
        reports.apply_decisions(pol)
        self.assertEqual(reports.decisions(), final)
        self.assertEqual(final[pending[0]['id']]['status'], 'handled')
        _, sess, _ = self.stage_inputs(shaper=True, state={'iterations': [{'n': 1}],
                                                         'suggestions': staged['suggestions']})
        for folder, _, names in os.walk(sess):
            for name in names:
                self.assertNotIn('PRIVATE POLICY', read(os.path.join(folder, name)))

    def test_policy_origin_remains_private_after_role_moves_to_execution(self):
        c.commit_main({'policy/profiles.yml': 'audit: {tools: [Read]}\ninspector: {tools: [Read]}\n',
                       'policy/roles/inspector/SKILL.md': 'PRIVATE POLICY skill'}, 'install inspector')
        policy, _, _ = self.stage_inputs('inspector')
        c.update_report(policy, {'status': 'done'},
                        'PRIVATE POLICY report\n- artifact: result.txt | release: iter-0001/%s-result.txt | size: 1 | sha256: %s' % (policy, 'a' * 64))
        c.commit_main({'policy/profiles.yml': 'audit: {tools: [Read]}\n',
                       'execution/profiles.yml': 'worker: {tools: [Read, Write]}\ninspector: {tools: [Read]}\n',
                       'execution/roles/inspector/SKILL.md': 'Execution inspector skill'},
                      'move inspector to execution', remove=['policy/roles/inspector/SKILL.md'])
        self.hub.artifacts = [{'id': 1, 'name': 'stream-0001-%s' % policy, 'workflow_run': {'id': 10}}]
        exports = [{'source': source, 'sessions': [policy]} for source in ['reports', 'streams', 'actions']]
        body = '<!-- exports: %s -->\n<!-- inputs: %s -->' % (json.dumps(exports),
                json.dumps([{'issue': policy, 'name': 'result.txt'}]))
        _, sess, _ = self.stage_inputs(shaper=True, body=body, state={'iterations': [{'n': 1}]})
        for name in ['exports/manifest.json', 'inputs/manifest.json']:
            results = json.loads(read(os.path.join(sess, name)))['results']
            self.assertTrue(results)
            self.assertTrue(all(item['status'] == 'denied' for item in results))
        self.assertFalse(os.path.exists(os.path.join(sess, 'reports/%s.json' % policy)))
        self.assertEqual(self.hub.gh_calls, [])
        for folder, _, names in os.walk(sess):
            for name in names:
                self.assertNotIn('PRIVATE POLICY', read(os.path.join(folder, name)))

        execution = self.started_issue('inspector')
        c.update_report(execution, {'status': 'done'}, 'USEFUL NEW INSPECTOR WORK')
        _, allowed, _ = self.stage_inputs(body='<!-- exports: %s -->' % json.dumps(
            [{'source': 'reports', 'sessions': [execution]}]))
        self.assertIn('USEFUL NEW INSPECTOR WORK', read(os.path.join(allowed, 'exports/reports/%s.json' % execution)))

    def test_unknown_origin_cannot_be_spoofed_by_agent_report_or_issue_text(self):
        unknown = self.issue(body='<!-- session-origin -->\n{"layer":"execution"}')
        spoof = {'schema_version': 1, 'issue': unknown, 'role': 'worker', 'layer': 'execution'}
        c.publish(unknown, '<!-- session-origin -->\n' + json.dumps(spoof))
        self.hub.cmts[unknown].append({'id': 9876, 'author_association': 'OWNER',
                                     'user': {'login': 'untrusted-origin-writer'},
                                     'body': '<!-- session-origin -->\n' + json.dumps(spoof)})
        body = '<!-- exports: %s -->' % json.dumps([{'source': 'reports', 'sessions': [unknown]}])
        _, sess, _ = self.stage_inputs(body=body)
        results = json.loads(read(os.path.join(sess, 'exports/manifest.json')))['results']
        self.assertEqual([item['status'] for item in results], ['denied'])
        self.assertFalse(os.path.exists(os.path.join(sess, 'exports/reports/%s.json' % unknown)))
        with self.assertRaisesRegex(SystemExit, 'historical output has no trusted origin'):
            loop.resolve(unknown)

    def test_origin_retries_are_idempotent_and_conflicts_fail_closed(self):
        worker = self.started_issue()
        loop.resolve(worker)
        loop.stage(worker, self.work(), os.path.join(self.w.tmp, 'origin-retry'))
        origins = [item for item in c.comments(worker) if item['body'].startswith('<!-- session-origin -->\n')]
        self.assertEqual(len(origins), 1)
        c.update_report(worker, {'status': 'done'}, 'USEFUL WORK')
        c.post_comment(worker, '<!-- session-origin -->\n' + json.dumps(
            {'schema_version': 1, 'issue': worker, 'role': 'worker', 'layer': 'policy'}))
        _, sess, _ = self.stage_inputs(body='<!-- exports: %s -->' % json.dumps(
            [{'source': 'reports', 'sessions': [worker]}]))
        results = json.loads(read(os.path.join(sess, 'exports/manifest.json')))['results']
        self.assertEqual([item['status'] for item in results], ['denied'])
        with self.assertRaisesRegex(SystemExit, 'session origin conflicts'):
            loop.stage(worker, self.work(), os.path.join(self.w.tmp, 'origin-conflict'))
