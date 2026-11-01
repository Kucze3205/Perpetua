"""The fixture shaper keeps its state in journal/NNNN.md, so it must not be handed the engine's stale working memory."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine/.github/loop"))
import iteration

SKILL = Path(__file__).resolve().parent / "fixtures" / "orchestrator" / "SKILL.md"


class OrchestratorFixture(unittest.TestCase):
    def test_startup_leaves_out_working_memory(self):
        startup = iteration.frontmatter(SKILL.read_text(encoding="utf-8"))[0].get("startup")
        self.assertIsInstance(startup, list)
        self.assertNotIn("journal/working-memory.md", startup)


if __name__ == "__main__":
    unittest.main()
