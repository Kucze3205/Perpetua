"""Recipient views of engine-passed data. Unknown origins are private.

This boundary does not isolate checkout/host reads (handled by session security).
"""
import common as c
import managed_resources as resources


def restricted(role):
    return resources.layered() and resources.role_layer(role) == resources.EXECUTION


def visible(issue):
    try:
        item = c.issue(issue)
        labels = c.label_names(item)
        roles = c.roles_of(labels)
        return (c.in_loop(labels) and len(roles) == 1
                and resources.role_layer(roles[0]) == resources.EXECUTION)
    except (SystemExit, ValueError, KeyError, TypeError):
        return False


def capabilities():
    # Descriptions are explicit public contracts, not skill bodies or profiles.
    return [{'role': name, 'description': profile.get('description', ''),
             'kind': 'script' if profile.get('agent') is False else 'agent'}
            for name, profile in sorted(resources.role_profiles().items())
            if profile['layer'] == resources.EXECUTION]


def state(value):
    # Allowlist: never forward newly added internal state fields by accident.
    out = {key: value[key] for key in ('schema_version', 'iteration', 'engine_version',
           'goal', 'no_progress', 'first_closed_iteration') if key in value}
    out['iterations'] = [{key: line[key] for key in ('n', 'status', 'improved') if key in line}
                         for line in value.get('iterations', [])]
    return out


def record(value):
    out = {key: value[key] for key in ('schema_version', 'iteration', 'status',
           'started', 'ended', 'engine_version') if key in value}
    out['input_state'] = state(value.get('input_state', {}))
    # Tasks can contain arbitrary export/input references. Only public task data
    # from execution sessions belongs in this view.
    out['tasks'] = [{key: task[key] for key in ('id', 'issue', 'title', 'role', 'body', 'blocked_by') if key in task}
                    for task in value.get('tasks', []) if visible(task.get('issue'))]
    outcome = value.get('outcome', {})
    out['outcome'] = {'sessions': [summary(session) for session in outcome.get('sessions', [])
                                   if visible(session.get('issue'))]}
    shaper = outcome.get('shaper')
    if shaper and visible(shaper.get('issue')):
        out['outcome']['shaper'] = summary(shaper)
    return out


def summary(value):
    # Historical session summaries may gain private engine metadata. Preserve
    # public execution results, never an unrestricted nested object.
    fields = ('issue', 'role', 'model', 'effort', 'status', 'verdict', 'improved',
              'merged', 'branch', 'commit', 'tokens', 'models', 'turns',
              'peak_context', 'attempts', 'agent_s', 'gate_s', 'total_s',
              'parked_s', 'runner', 'artifacts', 'stream')
    return {key: value[key] for key in fields if key in value}
