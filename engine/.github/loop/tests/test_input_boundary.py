"""Layer-aware inputs at the public session staging boundary."""
import json
import os

from fakes import common as c, iteration as it, loop, task
import session_reports as reports
from test_reports import PROPOSAL
from test_layered_resources import LayeredSessionTest
from test_session import read


class InputBoundaryTest(LayeredSessionTest):
    def stage_inputs(self, role='worker', body='', shaper=False, state=None, work=None):
        n = self.issue(role, body=body, labels=['loop:shaper'] if shaper else [])
        if state is not None:
            it.store_state(n, state)
        work = work or self.work()
        trusted = os.path.join(self.w.tmp, 'trusted-input-%s' % n)
        loop.stage(n, work, trusted)
        return n, os.path.join(work, '.session'), trusted

    def test_shaper_gets_execution_catalog_and_filtered_state_reports(self):
        worker = self.issue()
        policy = self.issue('audit')
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

    def test_explicit_policy_exports_are_denied_without_downloading(self):
        policy = self.issue('audit')
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
        worker, policy = self.issue(), self.issue('audit')
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
        worker = self.issue()
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
