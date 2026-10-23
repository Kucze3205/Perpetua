"""Recipient views of engine-passed data. Unknown origins are private.

This boundary does not isolate checkout/host reads (handled by session security).
"""
import json

import common as c
import managed_resources as resources


def restricted(role):
    return resources.layered() and resources.role_layer(role) == resources.EXECUTION


ORIGIN_MARK = '<!-- session-origin -->'


def origins(issue):
    """Only standalone engine envelopes count; published agent reports have another marker."""
    found = []
    for comment in c.comments(issue):
        if (comment.get('user', {}).get('login') != c.BOT
                or not comment.get('body', '').startswith(ORIGIN_MARK + '\n')):
            continue
        try:
            value = json.loads(comment['body'].split('\n', 1)[1])
            if (not isinstance(value, dict)
                    or set(value) != {'schema_version', 'issue', 'role', 'layer'}
                    or value['schema_version'] != 1 or type(value['issue']) is not int
                    or value['issue'] != issue or not isinstance(value['role'], str)
                    or value['layer'] not in resources.LAYERS):
                raise ValueError('invalid session origin')
        except (ValueError, TypeError):
            return [None]
        found.append(value)
    return found


def capture_origin(issue, role):
    """Pin the engine-selected layer before any agent output; retries cannot reclassify it."""
    value = {'schema_version': 1, 'issue': issue, 'role': role,
             'layer': resources.role_layer(role)}
    found = origins(issue)
    if found:
        if any(item != value for item in found):
            raise SystemExit('session origin conflicts with the selected role or layer')
        return
    if c.find_report(issue) is not None:
        raise SystemExit('session with historical output has no trusted origin')
    c.post_comment(issue, ORIGIN_MARK + '\n' + json.dumps(value, sort_keys=True))


def visible(issue):
    if type(issue) is not int:
        return False
    try:
        found = origins(issue)
        return (bool(found) and found[0] is not None
                and all(item == found[0] for item in found)
                and found[0]['layer'] == resources.EXECUTION)
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
           'goal', 'no_progress') if key in value}
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
