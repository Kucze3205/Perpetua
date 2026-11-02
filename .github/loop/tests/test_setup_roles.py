import os, shutil, tempfile, unittest, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import setup_roles

def write_skill(repo, layer, role, text=None, **files):
    """What the setup map does: author a complete local skill in the role's layer."""
    base = os.path.join(repo, layer, 'roles', role)
    os.makedirs(base, exist_ok=True)
    default = '---\nname: %s\ndescription: test role\n---\n# %s\n' % (role, role)
    for name, body in {'SKILL.md': text or default, **files}.items():
        os.makedirs(os.path.dirname(os.path.join(base, name)), exist_ok=True)
        with open(os.path.join(base, name), 'w') as f: f.write(body)
    return base


class SetupRolesTest(unittest.TestCase):
    def test_old_format_is_rejected_without_conversion(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'engine.yml').write_text('resources: {format: legacy}\n')
            with self.assertRaisesRegex(ValueError, 'unknown managed resources format'):
                setup_roles.prepare(tmp)

    def test_fresh_engine_has_policy_roles_and_install_registers_a_written_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, 'loop')
            shutil.copytree(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')), repo)
            setup_roles.prepare(repo)
            setup_roles.validate(repo)
            with self.assertRaisesRegex(ValueError, 'write the complete skill first'):
                setup_roles.install(repo, 'planner', 'execution', {'tools': ['Read']})
            write_skill(repo, 'execution', 'planner', **{'references/detail.md': 'complete reference'})
            setup_roles.install(repo, 'planner', 'execution', {'tools': ['Read']})
            self.assertIn('planner', setup_roles.resources.role_profiles(repo))
            with self.assertRaisesRegex(ValueError, 'already installed'):
                setup_roles.install(repo, 'planner', 'execution', {'tools': ['Read']})
            with self.assertRaisesRegex(ValueError, 'policymaker is shipped'):
                setup_roles.install(repo, 'policymaker', 'policy', {})
            with self.assertRaisesRegex(ValueError, 'already prepared'):
                setup_roles.prepare(repo)

    def test_required_selection_and_committed_baseline_guard_arming(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, 'loop')
            shutil.copytree(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')), repo)
            setup_roles.prepare(repo)
            write_skill(repo, 'execution', 'orchestrator')
            setup_roles.install(repo, 'orchestrator', 'execution', {'tools': ['Read']})
            params_path = os.path.join(repo, 'policy/params.yml')
            params = setup_roles.c.load_yaml(params_path)
            params['shaper'] = 'orchestrator'
            setup_roles.write_yaml(__import__('pathlib').Path(params_path), params)
            setup_roles.validate(repo)
            params['ceilings']['max_turns'] = -1
            setup_roles.write_yaml(__import__('pathlib').Path(params_path), params)
            with self.assertRaisesRegex(ValueError, 'invalid session configuration'):
                setup_roles.validate(repo)
            params['ceilings']['max_turns'] = 150
            setup_roles.write_yaml(__import__('pathlib').Path(params_path), params)
            def git(*args):
                return subprocess.check_output(['git', '-C', repo, *args], text=True).strip()
            git('init', '-q')
            git('config','core.autocrlf','false')
            git('config','user.name','Setup Test')
            git('config','user.email','setup@example.test')
            for cache in Path(repo).rglob('__pycache__'):
                shutil.rmtree(cache)
            git('add','.')
            git('commit','-qm','accepted setup')
            git('tag','setup-baseline')
            self.assertTrue(setup_roles.validate(repo, 'setup-baseline').startswith('layered-v1:'))
            result = subprocess.check_output([sys.executable, '.github/loop/setup_roles.py', 'validate', '--baseline', 'setup-baseline'], cwd=repo, text=True)
            self.assertTrue(result.startswith('layered-v1:'))
            self.assertEqual(git('status', '--porcelain'), '')
            accepted = git('rev-parse', 'HEAD')
            for ignored in ('execution/roles/orchestrator/SKILL.md', 'execution/roles/orchestrator/references/required.md'):
                with self.subTest(ignored=ignored):
                    path = Path(repo, ignored)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if not path.exists():
                        path.write_text('required local reference', encoding='utf-8')
                    else:
                        git('rm', '--cached', ignored)
                    Path(repo, '.gitignore').write_text('/' + ignored + '\n/ignored-work-product.txt\n', encoding='utf-8')
                    Path(repo, 'ignored-work-product.txt').write_text('legitimate work', encoding='utf-8')
                    git('add', '.gitignore')
                    git('commit', '-qm', 'ignored selected definition')
                    git('tag', '-f', 'setup-baseline')
                    self.assertEqual(git('status', '--porcelain'), '')
                    with self.assertRaisesRegex(ValueError, 'baseline.*definition'):
                        setup_roles.validate(repo, 'setup-baseline')
                    git('reset', '--hard', accepted)
                    if ignored.endswith('required.md'):
                        path.unlink()
                    Path(repo, 'ignored-work-product.txt').unlink()
                    git('tag', '-f', 'setup-baseline')
            Path(repo, '.gitignore').write_text('/ignored-work-product.txt\n', encoding='utf-8')
            git('add', '.gitignore')
            git('commit', '-qm', 'ignore legitimate work product')
            git('tag', '-f', 'setup-baseline')
            Path(repo, 'ignored-work-product.txt').write_text('legitimate work', encoding='utf-8')
            self.assertTrue(setup_roles.validate(repo, 'setup-baseline').startswith('layered-v1:'))
            with open(os.path.join(repo,'execution/roles/orchestrator/SKILL.md'),'a') as f: f.write('\nchanged')
            with self.assertRaisesRegex(ValueError,'clean'):
                setup_roles.validate(repo, 'setup-baseline')
            git('add','.')
            git('commit','-qm','later setup')
            with self.assertRaisesRegex(ValueError,'does not match'):
                setup_roles.validate(repo, 'setup-baseline')
            # Engine fix under .github/loop/ keeps the baseline; definition changes do not.
            git('reset','-q','--hard','setup-baseline')
            Path(repo,'.github/loop/common.py').open('a').write('# engine fix' + chr(10))
            git('commit','-qam','engine fix')
            self.assertTrue(setup_roles.validate(repo, 'setup-baseline').startswith('layered-v1:'))
            for changed in ('execution/protocol.md', 'policy/protocol.md', 'engine.yml', 'goal/goal.md'):
                with self.subTest(changed=changed):
                    Path(repo,changed).parent.mkdir(parents=True, exist_ok=True)
                    Path(repo,changed).open('a').write('# changed' + chr(10))
                    git('add','-A')
                    git('commit','-qm','definition change')
                    with self.assertRaisesRegex(ValueError,'baseline'):
                        setup_roles.validate(repo, 'setup-baseline')
                    git('reset','-q','--hard','HEAD~1')

    def test_execution_and_policy_roles_are_independent_and_script_shaper_needs_no_skill(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, 'loop')
            shutil.copytree(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')), repo)
            setup_roles.prepare(repo)
            write_skill(repo, 'execution', 'planner')
            write_skill(repo, 'policy', 'auditor-helper')
            setup_roles.install(repo, 'planner', 'execution', {'tools':['Read'], 'write':['journal/**']})
            setup_roles.install(repo, 'auditor-helper', 'policy', {'tools':['Read'], 'write':['audit/**']})
            with self.assertRaisesRegex(ValueError, 'already installed'):
                setup_roles.install(repo, 'planner', 'policy', {'tools': ['Read']})
            profiles = setup_roles.resources.role_profiles(repo)
            self.assertEqual(profiles['planner']['write'], ['journal/**'])
            self.assertEqual(profiles['auditor-helper']['write'], ['audit/**'])
            Path(repo, 'execution/roles/rule').mkdir()
            Path(repo, 'execution/roles/rule/plan.py').write_text('print("plan")')
            setup_roles.install(repo, 'rule', 'execution', {'agent':False, 'command':'python3 execution/roles/rule/plan.py'})
            params_path = Path(repo, 'policy/params.yml')
            params = setup_roles.c.load_yaml(params_path)
            params['shaper'] = 'rule'
            setup_roles.write_yaml(params_path, params)
            setup_roles.validate(repo)
