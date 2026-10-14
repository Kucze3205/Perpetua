#!/usr/bin/env bash
# One session. Started from $RUNNER_TEMP/trusted: the engine checkout is not on the disk while it runs.
# Input: $WORK/.session/ (put there by `loop.py stage`). Output: $WORK/.session/report.md, published by the epilogue.
# The agent has no `gh`, no push credentials and no GitHub token; its Claude credential lives only in the claude process.
set -u
TRUSTED="$(cd "$(dirname "$0")" && pwd)"
OUT="${RUNNER_TEMP:-/tmp}"
cd "$WORK" || exit 1
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git checkout -q -B "task/$ISSUE"
date +%s > "$OUT/agent-start"

sandbox() {   # a PID namespace for what the session starts, so it cannot read its parent's environment
  if [ "${LOOP_SANDBOX:-}" = 1 ] && command -v bwrap >/dev/null; then
    bwrap --unshare-pid --die-with-parent --ro-bind / / --dev /dev --proc /proc \
      --bind "$WORK" "$WORK" --bind "$OUT" "$OUT" --bind "$HOME" "$HOME" --bind /tmp /tmp "$@"
  else
    "$@"
  fi
}

if [ "$KIND" = script ]; then
  # a deterministic role: no model, no credential
  timeout "${AGENT_TIMEOUT}m" sandbox bash "$TRUSTED/command.sh" > "$OUT/script-output.txt" 2>&1
  echo $? > "$OUT/agent-exit"
  cat "$OUT/script-output.txt"
  date +%s > "$OUT/agent-end"
  exit 0
fi

if [ "$ROLE" = policy ]; then
  PROMPT="You are the Policy of this loop, iteration $ITERATION. Read .session/policy.md and follow it. Input: .session/loop-state.json and .session/issue.md. Protocol: .session/protocol.md. Write your report to .session/report.md. Work on the branch task/$ISSUE and commit often."
else
  PROMPT="You are the role \`$ROLE\` in a loop. Call the Skill tool with \`$ROLE\` and do the task of issue #$ISSUE. Input: .session/issue.md. Protocol: .session/protocol.md. Write your report to .session/report.md. Work on the branch task/$ISSUE and commit often."
fi

# the end of a turn in -p is the end of the session: background work and Monitor die with the runner
export CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1
# the credential stays in the claude process, gone from Bash and hooks; this forces permission mode `default`,
# so every tool the role needs must be in --allowedTools (acceptEdits is ignored)
export CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1
# API overload (429/529) is retried instead of ending the session; stream_filter.py ends it on a rejected limit event
export CLAUDE_CODE_RETRY_WATCHDOG=1
# long work in the foreground: by default Bash cuts off after 2 min
export BASH_DEFAULT_TIMEOUT_MS=$((AGENT_TIMEOUT * 60000)) BASH_MAX_TIMEOUT_MS=$((AGENT_TIMEOUT * 60000))
export SESSION_END=$(( $(date +%s) + AGENT_TIMEOUT * 60 ))
TOKEN="${CLAUDE_CODE_OAUTH_TOKEN:-}"
unset CLAUDE_CODE_OAUTH_TOKEN GH_TOKEN GITHUB_TOKEN

CLAUDE_CODE_OAUTH_TOKEN="$TOKEN" timeout "${AGENT_TIMEOUT}m" claude -p "$PROMPT" \
  --model "$MODEL" --effort "$EFFORT" \
  --permission-mode acceptEdits --allowedTools "$TOOLS" --disallowedTools Monitor \
  --settings "$TRUSTED/settings.json" \
  --max-turns "$MAX_TURNS" --output-format stream-json --verbose \
  2> "$OUT/claude-stderr.txt" \
  | python3 "$TRUSTED/stream_filter.py" "$OUT"
echo "${PIPESTATUS[0]}" > "$OUT/agent-exit"
date +%s > "$OUT/agent-end"
exit 0
