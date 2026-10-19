import os, shutil, tempfile, unittest, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import setup_roles

class SetupRolesTest(unittest.TestCase):
    def test_copy_is_inactive_then_explicit_source_is_local_and_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, 'loop')
            shutil.copytree(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')), repo)
            setup_roles.prepare(repo)
            with self.assertRaisesRegex(ValueError, 'shaper'):
                setup_roles.validate(repo)
            for kind in ('global', 'project', 'template'):
                source = os.path.join(tmp, kind)
                os.makedirs(os.path.join(source, 'references'))
                for name, text in [('SKILL.md', 'selected '+kind), ('references/detail.md', 'complete reference')]:
                    with open(os.path.join(source, name), 'w') as f: f.write(text)
                setup_roles.install(repo, kind, 'execution', source, {'tools':['Read']})
                with open(os.path.join(repo, 'execution/roles', kind, 'references/detail.md')) as f:
                    self.assertEqual(f.read(), 'complete reference')
                with open(os.path.join(source, 'SKILL.md')) as f: self.assertEqual(f.read(), 'selected '+kind)
            self.assertFalse(os.path.exists(os.path.join(repo, 'execution/roles/orchestrator')))
            self.assertTrue(os.path.isfile(os.path.join(repo, 'templates/execution/roles/orchestrator/SKILL.md')))

    def test_required_selection_and_committed_baseline_guard_arming(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, 'loop')
            shutil.copytree(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')), repo)
            setup_roles.prepare(repo)
            for role, layer in [('orchestrator','execution'), ('audit','policy'), ('evaluator','policy'), ('evaluator-verifier','policy'), ('policymaker','policy')]:
                source = os.path.join(repo, 'templates', layer, 'roles', role)
                catalog = setup_roles.c.load_yaml(os.path.join(repo, 'templates', layer, 'profiles.yml'))
                setup_roles.install(repo, role, layer, source, catalog.get(role))
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

    def test_same_source_has_independent_layers_and_incomplete_setup_is_rejected(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, 'loop')
            shutil.copytree(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')), repo)
            setup_roles.prepare(repo)
            source = os.path.join(repo, 'templates/execution/roles/orchestrator')
            setup_roles.install(repo, 'planner', 'execution', source, {'tools':['Read'], 'write':['journal/**']})
            setup_roles.install(repo, 'auditor-helper', 'policy', source, {'tools':['Read'], 'write':['audit/**']})
            params_path = Path(repo, 'policy/params.yml')
            params = setup_roles.c.load_yaml(params_path)
            params['shaper'] = 'planner'
            setup_roles.write_yaml(params_path, params)
            with self.assertRaisesRegex(ValueError, 'required policy role missing: audit'):
                setup_roles.validate(repo)
            with self.assertRaisesRegex(ValueError, 'already installed'):
                setup_roles.install(repo, 'planner', 'policy', source, {})
            with self.assertRaisesRegex(ValueError, 'belongs to policy'):
                setup_roles.install(repo, 'policymaker', 'execution', source)
            with self.assertRaisesRegex(ValueError, 'already prepared'):
                setup_roles.prepare(repo)
            profiles = setup_roles.resources.role_profiles(repo)
            self.assertIn('name: planner', Path(repo, 'execution/roles/planner/SKILL.md').read_text())
            self.assertIn('name: orchestrator', Path(source, 'SKILL.md').read_text())
            self.assertEqual(profiles['planner']['write'], ['journal/**'])
            self.assertEqual(profiles['auditor-helper']['write'], ['audit/**'])

    def test_script_shaper_installs_local_command_without_model_skill(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, 'loop')
            shutil.copytree(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')), repo)
            setup_roles.prepare(repo)
            source = Path(tmp, 'script-source')
            source.mkdir()
            (source / 'plan.py').write_text('print("plan")')
            setup_roles.install(repo, 'rule', 'execution', source, {'agent':False, 'command':'python3 execution/roles/rule/plan.py'})
            self.assertEqual(Path(repo, 'execution/roles/rule/plan.py').read_text(), 'print("plan")')
            params_path = Path(repo, 'policy/params.yml')
            params = setup_roles.c.load_yaml(params_path)
            params['shaper'] = 'rule'
            setup_roles.write_yaml(params_path, params)
            for role in ('audit','evaluator','evaluator-verifier','policymaker'):
                template = Path(repo, 'templates/policy/roles', role)
                catalog = setup_roles.c.load_yaml(Path(repo, 'templates/policy/profiles.yml'))
                setup_roles.install(repo, role, 'policy', template, catalog.get(role))
            setup_roles.validate(repo)
