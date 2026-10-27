"""Iteration must activate Policy without an import-time installation by loop."""
import os
import subprocess
import sys
import unittest


class IterationImportTest(unittest.TestCase):
    def test_fresh_iteration_uses_real_activation(self):
        # The shared fake environment imports loop. Reload iteration afterwards
        # so loop cannot install a hook into this copy of the lifecycle code.
        # Use a child process to keep module reloads out of the other tests.
        result = subprocess.run([sys.executable, "-c", """
import importlib
import unittest
import test_flow
importlib.reload(test_flow.it)
suite = unittest.TestSuite([
    test_flow.FirstPolicymakerTest(
        'test_the_first_iteration_starts_with_the_policymaker_and_its_orders_become_its_tasks'),
    test_flow.FirstIterationTest(
        'test_script_entry_begins_work_without_a_due_policymaker'),
])
result = unittest.TextTestRunner().run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
"""], cwd=os.path.dirname(__file__), capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
