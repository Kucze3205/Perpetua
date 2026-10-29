#!/usr/bin/env python3
"""Build the kucze:perpetua-setup skill into a kucze-skills checkout: python3 build.py PATH/TO/kucze-skills

The skill is skills/perpetua-setup/ plus engine/ and CONTEXT.md, in one directory. The target is replaced."""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", "test_engine_update.py")


def build(kucze):
    out = os.path.join(kucze, "skills", "perpetua-setup")
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "skills", "perpetua-setup"), out, ignore=SKIP)
    shutil.copytree(os.path.join(ROOT, "engine"), os.path.join(out, "engine"), ignore=SKIP)
    shutil.copy(os.path.join(ROOT, "CONTEXT.md"), out)
    with open(os.path.join(out, "engine", "ENGINE_VERSION"), encoding="utf-8") as fh:
        return out, fh.read().strip()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print("built %s (engine %s)" % build(sys.argv[1]))
