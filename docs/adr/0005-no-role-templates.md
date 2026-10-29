# No role templates: policy roles ship with the engine, execution roles are written per loop

The engine no longer carries inactive role candidates under `templates/`. The four policy-layer roles (Policymaker, audit, evaluator maintenance, evaluator verifier) are active default definitions in `policy/roles/` and `policy/profiles.yml`: they follow contracts every loop shares, so they are the same at the start and diverge only as the Policymaker revises them. Execution-layer roles (the orchestrator and goal-directed workers) depend on the goal, so the setup map writes them in the loop's repository. `setup_roles.py install` only registers a skill already written there, with its profile.

Matt Pocock's skills are the reference library for that writing, not a source to copy. After deciding that a new or recruited role will do some tasks, the setup map searches the installed `mattpocock-skills` plugin for how such a role should behave, and records what it found and used, or that nothing fit. The Policymaker chooses whether research would help and may reuse existing findings or order research from a role whose profile lists WebFetch or WebSearch. Research and source reporting are optional for the Policymaker; it writes skills suited to its loop.

Issue #95 revises the original requirement to research and report sources for every role the Policymaker creates or recruits. The owner chose discretion to avoid unnecessary sessions and token use in the running loop. Setup's research requirement remains unchanged.

We rejected keeping shipped templates as candidates: a copied template makes every loop start from the same text whether or not it fits the goal, and the copying machinery (source checks, name rewriting) existed only to carry it. We rejected vendoring a snapshot of the reference library into each loop: it redistributes another author's work and needs updating, while research already reaches the original.

Supersedes the part of ADR 0004 that supplies role templates and selects the starting orchestrator from them.
