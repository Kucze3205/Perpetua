"""One-time setup of local layered roles; never modifies source installations."""
import sys
# Validation must not dirty the accepted setup with engine bytecode caches.
sys.dont_write_bytecode = True

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import yaml
import common as c
import managed_resources as resources


def write_yaml(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding='utf-8')


def prepare(repo):
    """Deactivate copied defaults before setup commits or selects any role."""
    root = Path(repo).resolve()
    if (root / 'templates').exists() or resources.layered(root):
        raise ValueError('setup already prepared; no reconfiguration mode')
    profiles = c.load_yaml(root / 'policy/profiles.yml')
    for role, profile in profiles.items():
        layer = 'execution' if role == 'orchestrator' else 'policy'
        dest = root / 'templates' / layer
        source = root / 'policy/roles' / role
        if source.exists():
            (dest / 'roles').mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(dest / 'roles' / role))
        catalog = c.load_yaml(dest / 'profiles.yml')
        catalog[role] = profile
        write_yaml(dest / 'profiles.yml', catalog)
    role_template = root / 'policy/roles/ROLE-TEMPLATE.md'
    if role_template.exists():
        shutil.move(str(role_template), str(root / 'templates/ROLE-TEMPLATE.md'))
    shutil.rmtree(root / 'policy/roles', ignore_errors=True)
    pm = root / 'templates/policy/roles/policymaker/SKILL.md'
    pm.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / '.github/loop/policymaker.md', pm)
    for layer in resources.LAYERS:
        for rel in ('protocol.md', 'prompts/role.md'):
            path = root / layer / rel
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / 'policy' / rel, path)
        write_yaml(root / layer / 'profiles.yml', {})
    params = c.load_yaml(root / 'policy/params.yml')
    params['shaper'] = None
    write_yaml(root / 'policy/params.yml', params)
    config = c.load_yaml(root / 'engine.yml')
    config['resources'] = {'format': 'layered'}
    config.setdefault('policymaker', {})['write'] = ['execution/', 'policy/']
    config['protected'] = list(dict.fromkeys(config.get('protected', c.DEFAULTS['protected']) + ['templates/']))
    write_yaml(root / 'engine.yml', config)


def install(repo, role, layer, source, profile=None):
    root, source = Path(repo).resolve(), Path(source).resolve()
    if layer not in resources.LAYERS or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]*', role):
        raise ValueError('invalid role or layer')
    if not resources.layered(root):
        raise ValueError('prepare layered setup first')
    if role == 'policymaker' and layer != 'policy':
        raise ValueError('policymaker belongs to policy')
    script = isinstance(profile, dict) and profile.get('agent') is False and role != 'policymaker'
    if not source.is_dir() or (not script and not (source / 'SKILL.md').is_file()):
        raise ValueError('source must contain a complete SKILL.md for model roles')
    if script and not profile.get('command'):
        raise ValueError('script command required')
    # A complete copy must not retain aliases to global/project installations.
    for path in [source, *source.rglob('*')]:
        if path.is_symlink() or path.resolve() != path.absolute():
            raise ValueError('source references must be local files, not redirects')
    for name in resources.LAYERS:
        if (root / name / 'roles' / role).exists() or role in c.load_yaml(root / name / 'profiles.yml'):
            raise ValueError('role already installed: ' + role)
    if role != 'policymaker' and (not isinstance(profile, dict) or 'layer' in profile):
        raise ValueError('explicit profile mapping required, without layer')
    dest = root / layer / 'roles' / role
    if source == dest or dest in source.parents or source in dest.parents:
        raise ValueError('source and installation overlap')
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, dest)
    skill = dest / 'SKILL.md'
    if skill.is_file():
        text = skill.read_text(encoding='utf-8')
        if text.startswith('---\n'):
            parts = text.split('---\n', 2)
            if len(parts) == 3:
                metadata = yaml.safe_load(parts[1])
                if isinstance(metadata, dict):
                    metadata['name'] = role
                    skill.write_text('---\n' + yaml.safe_dump(metadata, sort_keys=False) + '---\n' + parts[2], encoding='utf-8')
    if role != 'policymaker':
        path = root / layer / 'profiles.yml'
        catalog = c.load_yaml(path)
        catalog[role] = profile
        write_yaml(path, catalog)


def validate(repo, baseline=None):
    """Fail closed before arming, optionally checking the accepted baseline commit."""
    root = Path(repo).resolve()
    previous = os.getcwd()
    try:
        os.chdir(root)
        if not resources.layered():
            raise ValueError('setup requires layered resources')
        params = c.load_yaml('policy/params.yml')
        if not isinstance(params.get('shaper'), str) or not params['shaper']:
            raise ValueError('select a shaper before arming')
        resources.validate_activation('.')
        profiles = resources.role_profiles()
        required = ['audit', 'evaluator', c.cfg()['verifier_role']]
        for role in required:
            if role not in profiles or profiles[role]['layer'] != 'policy':
                raise ValueError('required policy role missing: ' + role)
        import loop
        import session_security
        import iteration
        pm_profile = dict(loop.profile_of('policymaker'), layer='policy')
        for role, profile in dict(profiles, policymaker=pm_profile).items():
            error = session_security.profile_error(profile, c.cfg()['cli_version'])
            if error:
                raise ValueError('invalid profile ' + role + ': ' + error)
            if role != 'policymaker' and iteration.limits_exceeded(profile):
                raise ValueError('profile exceeds accepted limits: ' + role)
            if profile.get('agent', True) and not Path(profile['layer'], 'roles', role, 'SKILL.md').is_file():
                raise ValueError('complete local skill missing: ' + role)
            if not isinstance(profile.get('tools', []), list) or not isinstance(profile.get('write', []), list):
                raise ValueError('invalid profile: ' + role)
            if not profile.get('agent', True) and not profile.get('command'):
                raise ValueError('script command missing: ' + role)
            try:
                options = loop.session_options({'labels': [], 'body': ''}, role, profile)
                if min(options['max_turns'], options['agent_timeout']) <= 0:
                    raise ValueError('nonpositive session limits')
            except (TypeError, KeyError, AttributeError, ValueError) as error:
                raise ValueError('invalid session configuration for ' + role + ': ' + str(error)) from error
        for layer in resources.LAYERS:
            for rel in ('protocol.md', 'prompts/role.md'):
                if not Path(layer, rel).is_file():
                    raise ValueError('missing layer instructions: ' + layer + '/' + rel)
        if not Path('policy/prompts/policymaker.md').is_file():
            raise ValueError('missing Policymaker prompt')
        if baseline:
            def git(*args):
                return subprocess.check_output(['git', *args], text=True).strip()
            if git('status', '--porcelain'):
                raise ValueError('setup baseline requires clean committed configuration')
            if git('rev-parse', baseline + '^{commit}') != git('rev-parse', 'HEAD'):
                raise ValueError('setup baseline does not match accepted setup commit')
            # Status omits ignored files and can hide skip-worktree changes. Compare
            # every local managed file (including skill references) with the commit.
            roots = list(dict.fromkeys(resources.restore_paths() + ['engine.yml']))
            entries = git('ls-tree', '-r', '-z', baseline, '--', *roots)
            committed = {}
            for entry in entries.split('\0'):
                if entry:
                    header, path = entry.split('\t', 1)
                    mode, kind, digest = header.split()
                    committed[path] = (mode, kind, digest)
            local = {}
            for name in roots:
                path = Path(name)
                files = [path] if path.is_file() else path.rglob('*')
                for file in files:
                    if file.is_file():
                        local[file.as_posix()] = file
            if set(local) != set(committed):
                mismatch = sorted(set(local) ^ set(committed))
                raise ValueError('setup baseline does not contain matching managed definitions: ' + ', '.join(mismatch))
            for name, file in local.items():
                mode, kind, digest = committed[name]
                # Apply Git clean filters just as committing does (e.g. CRLF).
                actual = git('hash-object', '--path=' + name, '--', name)
                if kind != 'blob' or mode not in ('100644', '100755') or digest != actual:
                    raise ValueError('setup baseline differs from managed definition: ' + name)
            return resources.revision(baseline)
    finally:
        os.chdir(previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('prepare')
    ins = sub.add_parser('install')
    ins.add_argument('role')
    ins.add_argument('layer', choices=resources.LAYERS)
    ins.add_argument('source')
    ins.add_argument('--profile', help='JSON profile; mandatory except for policymaker')
    val = sub.add_parser('validate')
    val.add_argument('--baseline', required=True)
    args = parser.parse_args()
    try:
        if args.action == 'prepare': prepare('.')
        elif args.action == 'install': install('.', args.role, args.layer, args.source, json.loads(args.profile) if args.profile else None)
        else: print(validate('.', args.baseline))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, str(error) + '\n')

if __name__ == '__main__':
    main()
