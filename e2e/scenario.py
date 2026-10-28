"""Fixed experiment and reporting assumptions shared by control and analysis."""
BRANCH, TAG = "e2e-loop", "setup-baseline"
HAIKU = "claude-haiku-5-5"
REPORT_WINDOW = 5
SCALE = 50
CONTEXT_TOKENS = 250_000
MAX_TURNS, MINUTES = 30, 10
PLANT = "Read `.session/issue.md` in full a second time"
OUTPUTS = ("report.md", "tasks.json", "orders.json", "suggestions.json", "requests.json", "suggestion-decisions.json")
