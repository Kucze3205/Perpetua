"""Accepted-tree change gate shared by real-git test worlds."""

GATE_SH = """if grep -q BAD "$CANDIDATE_DIR/src/a.txt" 2>/dev/null; then
  echo '{"verdict": "rejected", "detail": "the candidate says BAD"}'
elif [ -f "$CANDIDATE_DIR/src/silent.txt" ]; then
  echo "no verdict printed"
else
  echo "{\\"verdict\\": \\"accepted\\", \\"improved\\": true, \\"detail\\": \\"changed: $(tr '\\n' ' ' < "$CHANGED_FILES_FILE")\\"}"
fi
"""
SCRIPT_GATE = "gate:\n  kind: script\n  command: bash policy/evaluator/gate.sh\n"
