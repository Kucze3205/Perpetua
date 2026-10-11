# Role template

A role is a skill plus a profile. Build a role from an existing skill first (a plugin's, a personal one, the project's);
write a new one from this template only when nothing fits.

1. The skill: `policy/roles/<role>/SKILL.md`. Claude Code loads it by name in the session.
2. The profile: an entry in `policy/profiles.yml` (see the comments there).
3. Nothing else: the label `role:<role>` on a task picks both, workflows are not edited.

Rules for a role:

- **One responsibility.** A role does one thing. Do not combine skills "just in case".
- **Narrow `write`.** Only the paths the role produces; the engine enforces it at merge.
- **No `internet` together with writing code** beyond what the owner accepted (`limits` in `engine.yml`).
- **No `gh`, no token.** A role works on files; the engine does the rest.

```markdown
---
name: <role>
description: <one sentence: what the role does and when it is loaded (label `role:<role>`)>
---

# <Role>

<What the role does and what it never does.>

Protocol (input, report, trust, git): `.session/protocol.md`. Terms: `docs/perpetua/glossary.md`.

## Method

<How the role works. If it is built on another skill, say which one to call with the Skill tool first.>

## Output

<What the role writes, where, in what shape. What "done" means: checkable items.>

## Never

<The role's hard limits: what it does not touch, what it does not claim.>
```
