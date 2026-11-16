#!/usr/bin/env python3
"""Pinning an artifact by digest (ADR-0006).

Any job of a run can upload an artifact: a job that ran code the engine did not write can read the run's artifact token
from the runner's memory, and the token stays valid after the job ends. So a job relies on an artifact only when its digest
reached it through the outputs of the job that made it: only that job's runner sets those.

The digest of an artifact is the sha256 of `sha256sum *` run in its directory (dotfiles included, plain files only), so a
swapped, added or removed file changes it. A job that has no engine yet checks it in the shell:

    (cd "$dir" && shopt -s dotglob && LC_ALL=C sha256sum -- * | sha256sum | cut -d' ' -f1)

Usage: artifact_pin.py digest DIR | verify DIR DIGEST
"""
import hashlib
import os
import sys


def digest(directory):
    lines = []
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if os.path.islink(path) or not os.path.isfile(path):
            raise ValueError("%s is not a plain file" % name)
        with open(path, "rb") as fh:
            lines.append("%s  %s\n" % (hashlib.sha256(fh.read()).hexdigest(), name))
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def verify(directory, expected):
    """ValueError unless the artifact in `directory` is the one `expected` names."""
    if not expected or digest(directory) != expected:
        raise ValueError("the artifact does not match the digest its job pinned")


if __name__ == "__main__":
    if sys.argv[1] == "digest":
        print(digest(sys.argv[2]))
    else:
        try:
            verify(sys.argv[2], sys.argv[3])
        except (OSError, ValueError) as error:
            sys.exit("%s: %s" % (sys.argv[2], error))
