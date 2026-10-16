"""Policy-owned session tuning constrained by owner configuration."""
import common as c


def profile():
    cfg = c.cfg()
    base = dict(cfg["policy"], agent=True)
    # Old installations use their existing engine profile as the owner ceiling.
    ceilings = cfg.get("policy_session_ceilings") or {
        "models": [base["model"]], "efforts": ["low", "medium", base["effort"]],
        "max_turns": base["max_turns"], "timeout_minutes": base["timeout_minutes"],
    }
    tune = c.params().get("policy_session") or {}
    if not isinstance(tune, dict) or set(tune) - {"model", "effort", "max_turns", "timeout_minutes"}:
        raise ValueError("policy_session supports only model, effort, max_turns and timeout_minutes")
    for key, allowed in (("model", "models"), ("effort", "efforts")):
        value = tune.get(key, base[key])
        if value not in ceilings[allowed]:
            raise ValueError("policy_session %s exceeds the owner's allowed %s" % (key, allowed))
        base[key] = value
    for key in ("max_turns", "timeout_minutes"):
        value, ceiling = tune.get(key, base[key]), ceilings[key]
        if type(value) is not int or type(ceiling) is not int or min(value, ceiling) <= 0:
            raise ValueError("policy_session %s and its owner ceiling must be positive integers" % key)
        base[key] = min(value, ceiling)
    return base
