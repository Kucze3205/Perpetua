"""After a wave: if a ticket hit the usage limit, sleep until the reset (capped by the job), then dispatch the next wave."""
import glob
import json
import os
import sys
import time

from common import gh

statuses = []
for path in glob.glob(os.path.join(sys.argv[1], "*", "status.json")):
    with open(path) as fh:
        statuses.append(json.load(fh))
limited = [s for s in statuses if s.get("status") == "limited"]
if limited:
    resets = [float(s["resets_at"]) for s in limited if s.get("resets_at")]
    wait = (min(resets) - time.time() + 120) if resets else 3600
    wait = max(300, min(wait, 330 * 60))
    print("usage limit: waiting %d minutes" % (wait // 60), flush=True)
    time.sleep(wait)
wave = int(os.environ["WAVE"]) + 1
gh("workflow", "run", "build-spec.yml", "--ref", os.environ.get("GITHUB_REF_NAME", "main"),
   "-f", "parent=%s" % os.environ["PARENT"], "-f", "branch=%s" % os.environ["BRANCH"], "-f", "wave=%d" % wave)
print("dispatched wave", wave)
