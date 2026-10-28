"""#87: a missing session stream (artifact quota) never fails the session and is never named in the record."""
import os
import unittest

import yaml

from fakes import common as c, iteration as it
from test_session import SessionCase

SESSION_YML = os.path.join(os.path.dirname(__file__), "..", "..", "workflows", "session.yml")


class StreamWithoutQuotaTest(SessionCase):
    def test_stream_upload_cannot_fail_the_session_job(self):
        with open(SESSION_YML, encoding="utf-8") as fh:
            steps = [s for job in yaml.safe_load(fh)["jobs"].values() for s in job.get("steps", [])]
        uploads = [s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact")
                   and str(s["with"]["name"]).startswith("stream-")]       # the session's own results must not be lost quietly
        self.assertTrue(uploads)
        for s in uploads:
            self.assertIs(s.get("continue-on-error"), True)

    def test_record_names_a_stream_only_when_it_exists(self):
        n = self.issue()
        self.assertNotIn("stream", it.session_summary(c.issue(n)))
        name = "stream-%04d-%s" % (c.iteration_of(c.label_names(c.issue(n))) or 0, n)
        self.assertEqual(it.session_summary(c.issue(n), {name})["stream"], name)


if __name__ == "__main__":
    unittest.main()
