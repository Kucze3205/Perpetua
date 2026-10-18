import os
import shutil
import unittest

import fakes
from fakes import common as c, loop
import test_session as sessions
from test_session import read


class LayeredSessionTest(sessions.SessionCase):
    work = sessions.StageTest.work
    def setUp(self):
        super().setUp()
        # A controlled owner-selected layout, leaving unrelated inactive skills.
        shutil.rmtree(os.path.join(self.w.loop, "policy/roles/worker"))
        self.w.write(self.w.loop, 'engine.yml', 'resources:\n  format: layered\n')
        self.w.write(self.w.loop, 'execution/profiles.yml', 'worker:\n  tools: [Read, Write]\n  write: [src/**, execution/**, policy/**]\n')
        self.w.write(self.w.loop, 'policy/profiles.yml', 'audit:\n  tools: [Read]\n  write: [audit/**]\n')
        for layer, role in (('execution', 'worker'), ('policy', 'audit'), ('policy', 'policymaker')):
            self.w.write(self.w.loop, '%s/roles/%s/SKILL.md' % (layer, role), '---\nname: %s\ndescription: fixture\n---\n%s role\n' % (role, layer))
        self.w.write(self.w.loop, 'execution/prompts/role.md', 'Execution prompt')
        self.w.write(self.w.loop, 'execution/protocol.md', 'Execution rules')
        fakes.sh(self.w.loop, 'git', 'add', '-A')
        fakes.sh(self.w.loop, 'git', 'commit', '-qm', 'layered setup')
        fakes.sh(self.w.loop, 'git', 'push', '-q', 'origin', 'HEAD:main')

    def test_layered_roles_resolve_stage_and_admit_their_work(self):
        for role, expected, files in (('worker', 'execution role', {'src/result.txt': 'work'}),
                                      ('audit', 'policy role', {'audit/result.txt': 'audit'})):
            with self.subTest(role=role):
                n = self.issue(role)
                loop.resolve(n)
                work = self.work()
                trusted = os.path.join(self.w.tmp, 'trusted-' + role)
                loop.stage(n, work, trusted)
                self.assertIn(expected, read(os.path.join(work, '.session/role/SKILL.md')))
                if role == 'worker':
                    self.assertIn('Execution rules', read(os.path.join(work, '.session/protocol.md')))
                    self.assertIn('Execution prompt', read(os.path.join(trusted, 'prompt.md')))
                self.w.branch(n, files)
                result = loop.merge_branch(n, role, self.hub.issue(n), override={'verdict': 'accepted'})
                self.assertEqual(result['result'], 'merged')
                for path, content in files.items():
                    self.assertEqual(self.w.origin_file(path), content)

    def test_policymaker_stages_own_local_skill_and_manages_both_layers(self):
        n = self.issue('policymaker')
        loop.resolve(n)
        work = self.work()
        trusted = os.path.join(self.w.tmp, 'trusted-policymaker')
        loop.stage(n, work, trusted)
        self.assertIn('policy role', read(os.path.join(work, '.session/role/SKILL.md')))
        self.assertIn('.session/role/SKILL.md', read(os.path.join(trusted, 'prompt.md')))
        global_skill = os.path.join(self.home, '.claude/skills/policymaker/SKILL.md')
        self.assertFalse(os.path.exists(global_skill))
        files = {'execution/roles/worker/reference.md': 'worker reference',
                 'execution/roles/new-role/SKILL.md': 'new role',
                 'policy/roles/audit/reference.md': 'audit reference',
                 'policy/roles/policymaker/SKILL.md': 'revised own skill'}
        self.w.branch(n, files)
        result = loop.merge_branch(n, 'policymaker', self.hub.issue(n))
        self.assertEqual((result['result'], result['verdict']), ('merged', None))
        for path, content in files.items():
            self.assertEqual(self.w.origin_file(path), content)

    def test_executor_cannot_admit_managed_definitions_even_with_broad_write(self):
        for path in ('execution/roles/worker/SKILL.md', 'execution/profiles.yml',
                     'policy/roles/policymaker/SKILL.md', 'policy/evaluator/new.py'):
            with self.subTest(path=path):
                n = self.issue()
                self.w.branch(n, {path: 'proposed'})
                result = loop.merge_branch(n, 'worker', self.hub.issue(n))
                self.assertEqual(result['result'], 'scope')

    def test_policymaker_cannot_change_engine_or_bypass_evaluator_verification(self):
        for path, expected in (('engine.yml', 'protected'),
                               ('../global/skill.md', 'scope'),
                               ('policy/evaluator/new.py', 'scope')):
            if path.startswith('../'):
                # Git cannot carry host writes: scope also excludes source installations.
                self.assertFalse(loop.may_write(loop.profile_of('policymaker'), '/global/skill.md'))
                continue
            with self.subTest(path=path):
                n = self.issue('policymaker')
                self.w.branch(n, {path: 'proposed'})
                self.assertEqual(loop.merge_branch(n, 'policymaker', self.hub.issue(n))['result'], expected)
        self.w.write(self.w.loop, 'engine.yml', 'resources: {format: layered}\npolicymaker: {owns_evaluator: true}\n')
        fakes.sh(self.w.loop, 'git', 'add', 'engine.yml')
        fakes.sh(self.w.loop, 'git', 'commit', '-qm', 'owner permits evaluator maintenance')
        fakes.sh(self.w.loop, 'git', 'push', '-q', 'origin', 'HEAD:main')
        n = self.issue('policymaker')
        self.w.branch(n, {'policy/evaluator/new.py': 'proposed'})
        self.assertEqual(loop.merge_branch(n, 'policymaker', self.hub.issue(n))['result'], 'awaiting-verifier')
        self.assertIsNone(self.w.origin_file('policy/evaluator/new.py'))

    def test_staging_rejects_conflicting_or_ambiguous_layer_assignments(self):
        original = read('execution/profiles.yml')
        cases = [
            ('policy/profiles.yml', 'worker: {tools: [Read]}\n', 'both execution and policy'),
            ('execution/profiles.yml', original + 'audit: {}\n', 'must belong to the policy'),
            ('execution/profiles.yml', 'worker: {layer: policy}\n', 'assigned by its profiles.yml'),
            ('execution/profiles.yml', 'worker: {}\nworker: {tools: [Read]}\n', 'duplicate'),
            ('policy/roles/worker/SKILL.md', 'conflicting copy', 'wrong or both layers'),
        ]
        for path, content, reason in cases:
            with self.subTest(path=path, content=content):
                old = read(path) if os.path.exists(path) else None
                self.w.write(self.w.loop, path, content)
                try:
                    n = self.issue()
                    with self.assertRaisesRegex(SystemExit, reason):
                        loop.stage(n, self.work(), os.path.join(self.w.tmp, 'invalid-trusted'))
                finally:
                    if old is None:
                        shutil.rmtree(os.path.dirname(path))
                    else:
                        self.w.write(self.w.loop, path, old)

    def test_profiles_keep_independent_tools_network_and_owner_limits(self):
        n = self.issue()
        loop.resolve(n)
        self.assertEqual(self.outputs()['tools'], 'Read,Write,Skill')
        audit = self.issue('audit')
        loop.resolve(audit)
        self.assertEqual(self.outputs()['tools'], 'Read,Skill')
        # The policy layer does not itself grant internet or extra tools.
        self.w.write(self.w.loop, 'policy/profiles.yml',
                     'audit: {tools: [Read, WebSearch], internet: true, write: [audit/**]}\n')
        with self.assertRaisesRegex(SystemExit, 'beyond the accepted limits'):
            loop.stage(audit, self.work(), os.path.join(self.w.tmp, 'beyond-limits'))
        # The execution role still has its own unchanged offline profile.
        loop.stage(n, self.work(), os.path.join(self.w.tmp, 'worker-after-audit-change'))
        self.assertEqual(read(os.path.join(self.w.tmp, 'worker-after-audit-change/network-mode')), 'offline\n')

    def test_invalid_candidate_catalog_is_not_admitted(self):
        n = self.issue('policymaker')
        self.w.branch(n, {'policy/profiles.yml': 'worker: {tools: [Read]}\n'})
        result = loop.merge_branch(n, 'policymaker', self.hub.issue(n))
        self.assertEqual(result['result'], 'invalid-resources')
        self.assertIn('both execution and policy', result['detail'])
        self.assertNotIn('worker:', self.w.origin_file('policy/profiles.yml'))

    def test_layered_online_staging_preserves_global_source_installation(self):
        global_skill = os.path.join(self.home, '.claude/skills/worker/SKILL.md')
        self.w.write(self.home, '.claude/skills/worker/SKILL.md', 'global source remains')
        self.w.write(self.w.loop, 'execution/profiles.yml',
                     'worker: {tools: [Read, Write], internet: true, write: []}\n')
        n = self.issue()
        trusted = os.path.join(self.w.tmp, 'online-trusted')
        work = self.work()
        loop.stage(n, work, trusted)
        self.assertEqual(read(global_skill), 'global source remains')
        self.assertIn('execution role', read(os.path.join(work, '.session/role/SKILL.md')))
        self.assertEqual(read(os.path.join(trusted, 'network-mode')), 'online\n')
        self.assertIn('.session/role/SKILL.md', read(os.path.join(trusted, 'prompt.md')))

    def test_layered_script_role_stages_command_and_admits_product(self):
        self.w.write(self.w.loop, 'execution/profiles.yml',
                     'worker: {agent: false, command: "echo product", tools: [], write: [src/**]}\n')
        fakes.sh(self.w.loop, 'git', 'add', 'execution/profiles.yml')
        fakes.sh(self.w.loop, 'git', 'commit', '-qm', 'script profile')
        fakes.sh(self.w.loop, 'git', 'push', '-q', 'origin', 'HEAD:main')
        n = self.issue()
        loop.resolve(n)
        self.assertEqual(self.outputs()['kind'], 'script')
        work, trusted = self.work(), os.path.join(self.w.tmp, 'script-trusted')
        loop.stage(n, work, trusted)
        self.assertEqual(read(os.path.join(trusted, 'command.sh')), 'echo product\n')
        self.assertFalse(os.path.exists(os.path.join(work, '.session/role')))
        self.w.branch(n, {'src/script.txt': 'product'})
        result = loop.merge_branch(n, 'worker', self.hub.issue(n), override={'verdict': 'accepted'})
        self.assertEqual(result['result'], 'merged')

    @unittest.skipIf(os.name == 'nt', 'symlink cases run on Linux')
    def test_staging_rejects_redirected_skills_and_references(self):
        targets = ['execution/roles/worker', 'execution/roles/worker/SKILL.md',
                   'execution/roles/worker/reference.md']
        for rel in targets:
            with self.subTest(path=rel):
                location = os.path.join(self.w.loop, rel)
                backup = location + '.saved'
                exists = os.path.exists(location)
                if exists:
                    os.rename(location, backup)
                os.symlink(os.path.join(self.w.loop, 'policy/roles/audit') if rel.endswith('worker')
                           else os.path.join(self.w.loop, 'policy/roles/audit/SKILL.md'), location)
                try:
                    n = self.issue()
                    with self.assertRaisesRegex(SystemExit, 'redirected managed'):
                        loop.stage(n, self.work(), os.path.join(self.w.tmp, 'redirect-trusted'))
                finally:
                    os.unlink(location)
                    if exists:
                        os.rename(backup, location)
