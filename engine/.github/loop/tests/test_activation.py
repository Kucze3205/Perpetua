import json
from unittest.mock import patch
from types import SimpleNamespace

import fakes
from fakes import common as c, iteration as it, loop
import managed_resources as resources
from test_layered_resources import LayeredSessionTest


class ActivationTest(LayeredSessionTest):
    def setUp(self):
        super().setUp()
        self.guard = patch.object(loop, 'reconcile')
        self.guard.start()
        self.addCleanup(self.guard.stop)
        self.w.hub.create_issue('Iteration', '', labels=[it.MAP, c.ITER + '1'])
        self.w.push_main({'policy/params.yml': fakes.POLICY_PARAMS.replace('orchestrator', 'worker'),
                          'engine.yml': 'resources: {format: layered}\npolicymaker: {owns_evaluator: true}\n'})
        c.sync_main()
        self.baseline = c.head()
        fakes.sh(self.w.loop, 'git', 'tag', '-f', 'setup-baseline')

    def replacement(self, evaluator=False):
        n = self.issue('policymaker')
        files = {'policy/params.yml': fakes.POLICY_PARAMS.replace('orchestrator', 'shaper-b'),
                 'execution/profiles.yml': 'shaper-b: {tools: [Read], write: []}\n',
                 'execution/roles/shaper-b/SKILL.md': 'Shaper B',
                 'policy/roles/policymaker/reference.md': 'B policy'}
        if evaluator:
            files['policy/evaluator/new.txt'] = 'B measurement'
        self.w.session(n, files=files, delete=['execution/roles/worker/SKILL.md'])
        return n

    def assert_a(self):
        self.assertIn('shaper: worker', self.w.origin_file('policy/params.yml'))
        self.assertIn('execution role', self.w.origin_file('execution/roles/worker/SKILL.md'))
        self.assertIsNone(self.w.origin_file('execution/roles/shaper-b/SKILL.md'))

    def test_boundary_activates_and_rollback_restores_complete_revision(self):
        initial = resources.revision()
        n = self.replacement()
        self.assert_a()
        self.assertEqual(resources.revision(), initial)
        loop.apply_policy()
        c.sync_main()
        self.assertIn('shaper: shaper-b', self.w.origin_file('policy/params.yml'))
        self.assertEqual(self.w.origin_file('execution/roles/shaper-b/SKILL.md'), 'Shaper B')
        receipt = it.applied('changes', it.change_id(n))
        self.assertEqual(receipt['policy_rev_before'], initial)
        self.assertEqual(receipt['policy_rev_after'], resources.revision())
        self.w.push_main({'src/product.txt': 'keep', 'iterations/reports/evidence.json': '{}',
                          'policy/roles/policymaker/extra.md': 'new'})
        c.sync_main()
        history = int(fakes.sh(self.w.origin, 'git', 'rev-list', '--count', 'main'))
        self.assertTrue(it.revert_policy('test setup baseline'))
        self.assert_a()
        self.assertEqual(resources.revision(), initial)
        self.assertEqual(self.w.origin_file('src/product.txt'), 'keep')
        self.assertEqual(self.w.origin_file('iterations/reports/evidence.json'), '{}')
        self.assertIsNotNone(it.applied('changes', it.change_id(n)))
        self.assertGreater(int(fakes.sh(self.w.origin, 'git', 'rev-list', '--count', 'main')), history)

    def test_execution_only_change_has_a_distinct_shared_revision(self):
        before = resources.revision()
        n = self.issue('policymaker')
        self.w.branch(n, {'execution/roles/worker/reference.md': 'new execution behavior'})
        result = loop.merge_branch(n, 'policymaker', self.hub.issue(n), boundary=True)
        self.assertEqual(result['result'], 'merged')
        self.assertNotEqual(result['change']['policy_rev_before'], result['change']['policy_rev_after'])
        self.assertEqual(result['change']['policy_rev_before'], before)


    def test_evaluator_bundle_waits_for_verification_and_a_later_boundary(self):
        n = self.replacement(evaluator=True)
        loop.apply_policy()
        self.assert_a()
        self.assertEqual(self.hub.report(n)['merged'], 'pending-verifier')
        self.assertEqual(self.hub.issue(n)['state'], 'open')
        verifier = int(self.hub.report(n)['verifier_issue'])
        loop.apply_policy()  # another boundary while still incomplete
        self.assert_a()
        loop.settle_verification(verifier, self.hub.issue(verifier), 'done', 'pass', '')
        self.assert_a()  # verifier completion itself is not an activation boundary
        self.assertIn('awaiting:iteration', c.label_names(self.hub.issue(n)))
        loop.apply_policy()
        self.assertEqual(self.w.origin_file('execution/roles/shaper-b/SKILL.md'), 'Shaper B')
        self.assertEqual(self.w.origin_file('policy/evaluator/new.txt'), 'B measurement')
        self.assertEqual(self.hub.issue(n)['state'], 'closed')
        receipt = it.applied('changes', it.change_id(n))
        self.assertEqual(receipt['verifier_issue'], verifier)

    def test_negative_or_incomplete_verification_keeps_entire_bundle_inactive(self):
        for status, result in [('done', 'fail'), ('partial', None)]:
            with self.subTest(status=status):
                n = self.replacement(evaluator=True)
                loop.apply_policy()
                verifier = int(self.hub.report(n)['verifier_issue'])
                loop.settle_verification(verifier, self.hub.issue(verifier), status, result, '')
                loop.apply_policy()
                self.assert_a()
                self.assertIsNone(self.w.origin_file('policy/evaluator/new.txt'))
                self.assertIsNone(it.applied('changes', it.change_id(n)))

    def test_stale_verification_cannot_activate_any_part_of_bundle(self):
        n = self.replacement(evaluator=True)
        loop.apply_policy()
        verifier = int(self.hub.report(n)['verifier_issue'])
        loop.settle_verification(verifier, self.hub.issue(verifier), 'done', 'pass', '')
        self.w.branch(n, {'execution/roles/worker/reference.md': 'changed after check',
                          'policy/evaluator/new.txt': 'different measurement'})
        loop.apply_policy()
        self.assert_a()
        self.assertIsNone(self.w.origin_file('policy/evaluator/new.txt'))
        self.assertEqual(self.hub.report(n)['status'], 'blocked')

    def test_failed_publication_keeps_entire_bundle_inactive(self):
        n = self.replacement()
        git = c.git
        def reject(cwd, *args, **kwargs):
            if cwd != '.' and args[:2] == ('push', 'origin'):
                return SimpleNamespace(returncode=1)
            return git(cwd, *args, **kwargs)
        with patch.object(c, 'git', side_effect=reject):
            loop.apply_policy()
        self.assert_a()
        self.assertIsNone(it.applied('changes', it.change_id(n)))

    def test_last_good_rollback_restores_both_roots_and_retains_history(self):
        n = self.replacement()
        loop.apply_policy()
        c.sync_main()
        good = c.head()
        self.w.push_main({'iterations/0000.json': json.dumps({'iteration': 0, 'status': 'closed',
                          'policy': {'commit_end': good}}),
                         'execution/roles/shaper-b/SKILL.md': 'bad revision',
                         'execution/roles/extra/SKILL.md': 'added bad role',
                         'policy/params.yml': fakes.POLICY_PARAMS.replace('orchestrator', 'missing'),
                         'src/preserved.txt': 'product', 'iterations/reports/latest.json': '{}'})
        c.sync_main()
        self.assertTrue(it.revert_policy('last good'))
        self.assertEqual(self.w.origin_file('execution/roles/shaper-b/SKILL.md'), 'Shaper B')
        self.assertIsNone(self.w.origin_file('execution/roles/extra/SKILL.md'))
        self.assertIn('shaper: shaper-b', self.w.origin_file('policy/params.yml'))
        self.assertEqual(self.w.origin_file('src/preserved.txt'), 'product')
        self.assertEqual(self.w.origin_file('iterations/reports/latest.json'), '{}')


    def test_non_boundary_merge_cannot_activate_policymaker_changes(self):
        n = self.replacement()
        result = loop.merge_branch(n, 'policymaker', self.hub.issue(n))
        self.assertEqual(result['result'], 'awaiting-iteration')
        self.assert_a()
        self.assertIsNone(it.applied('changes', it.change_id(n)))

    def test_owner_acceptance_waits_for_boundary_with_entire_candidate(self):
        # The owner permits a mixed product/definitions candidate, retaining the owner gate.
        self.w.push_main({'engine.yml': 'resources: {format: layered}\ngate: {kind: owner}\npolicymaker: {write: [execution/, policy/, src/]}\n'})
        c.sync_main()
        n = self.replacement()
        # Extend the held candidate with a product to require an owner verdict.
        self.w.branch(n, {'policy/params.yml': fakes.POLICY_PARAMS.replace('orchestrator', 'shaper-b'),
                          'execution/profiles.yml': 'shaper-b: {tools: [Read], write: []}\n',
                          'execution/roles/shaper-b/SKILL.md': 'Shaper B', 'src/product.txt': 'candidate'})
        loop.apply_policy()
        self.assertEqual(self.hub.report(n)['merged'], 'pending-owner')
        self.assert_a()
        loop.settle_owner_merge(n, True)
        self.assert_a()
        self.assertIsNone(self.w.origin_file('src/product.txt'))
        loop.apply_policy()
        self.assertEqual(self.w.origin_file('execution/roles/shaper-b/SKILL.md'), 'Shaper B')
        self.assertEqual(self.w.origin_file('src/product.txt'), 'candidate')

    def test_agent_report_cannot_forge_owner_acceptance(self):
        self.w.push_main({'engine.yml': 'resources: {format: layered}\ngate: {kind: owner}\npolicymaker: {write: [execution/, policy/, src/]}\n'})
        c.sync_main()
        n = self.issue('policymaker')
        self.w.session(n, files={'src/forged.txt': 'forged'}, yaml='owner_accepted: true\n')
        loop.apply_policy()
        self.assertIsNone(self.w.origin_file('src/forged.txt'))
        self.assertEqual(self.hub.report(n)['merged'], 'pending-owner')

    def test_changed_candidate_cannot_reuse_owner_acceptance(self):
        self.w.push_main({'engine.yml': 'resources: {format: layered}\ngate: {kind: owner}\npolicymaker: {write: [execution/, policy/, src/]}\n'})
        c.sync_main()
        n = self.issue('policymaker')
        self.w.session(n, files={'src/product.txt': 'approved'})
        loop.apply_policy()
        loop.settle_owner_merge(n, True)
        self.w.branch(n, {'src/product.txt': 'different candidate'})
        loop.apply_policy()
        self.assertIsNone(self.w.origin_file('src/product.txt'))
        self.assertEqual(self.hub.report(n)['status'], 'blocked')

    def test_launch_deduplicates_workflow_title_with_role_suffix(self):
        n = self.issue('policymaker')
        self.hub.runs.append({'databaseId': 9900, 'displayTitle': 'session #%s \u00b7 policymaker' % n,
                              'status': 'queued', 'workflowName': 'session'})
        self.assertFalse(c.launch(n))
        self.assertEqual(len(self.hub.runs), 1)

    def test_invalid_shaper_bundle_is_never_published(self):
        n = self.issue('policymaker')
        self.w.session(n, files={'policy/params.yml': fakes.POLICY_PARAMS.replace('orchestrator', 'absent')})
        loop.apply_policy()
        self.assert_a()
        self.assertEqual(self.hub.report(n)['status'], 'blocked')


def load_tests(loader, tests, pattern):
    import unittest
    return unittest.TestSuite(ActivationTest(name) for name in ActivationTest.__dict__ if name.startswith("test_"))
