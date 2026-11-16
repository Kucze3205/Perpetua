"""What the engine tells the shaper about the loop: its roles and the options a task may use."""
import common as c
import managed_resources as resources


def description(role):
    """The role's own one-line `description` (frontmatter of its SKILL.md); profiles carry none."""
    import iteration as it
    try:
        text = c.safe_read('.', resources.role_directory(role) + '/SKILL.md')
        return str((it.frontmatter(text)[0]).get('description') or '').strip()
    except Exception:
        return ''


def task_options():
    """What a task's `model`/`effort`/`limits` may use: exactly what `validate_tasks` checks, from the same source."""
    p = c.params()
    return {'models': sorted(p['models']), 'efforts': list(p['efforts']),
            'ceilings': {k: p['ceilings'][k] for k in ('job_minutes', 'max_turns')}}


def capabilities():
    # Descriptions are explicit public contracts, not skill bodies or profiles.
    return [{'role': name, 'description': description(name),
             'kind': 'script' if profile.get('agent') is False else 'agent'}
            for name, profile in sorted(resources.role_profiles().items())
            if profile['layer'] == resources.EXECUTION]
