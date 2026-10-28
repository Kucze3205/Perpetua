#!/usr/bin/env python3
"""Real-loop E2E commands and offline analysis. Procedure: e2e/README.md."""
import argparse
import sys

import experiment
from analysis import analyze_command as analyze
from collection import collect
from experiment import build, start, wait, probe, finish, iterations


def run_all(args):
    start(args)
    wait(args)
    probe(args)
    n, out = iterations(), args.out
    for k in range(n):                          # one directory per iteration: OUT, or OUT-1, OUT-2... when there are several
        experiment.SHIFT, args.out = k, out if n == 1 else "%s-%d" % (out, k + 1)
        collect(args)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="action", required=True)
    for name, fn in (("start", start), ("wait", wait), ("probe", probe), ("finish", finish)):
        q = sub.add_parser(name)
        if name == "start":
            q.add_argument("--iterations", type=int, default=1)
        q.set_defaults(fn=fn)
    b = sub.add_parser("build")
    b.add_argument("--iterations", type=int, default=1, help="how many iterations the cadence allows (the Policymaker of the 2nd sees the 1st)")
    b.add_argument("--no-push", action="store_true", help="build the tree and commit locally, push nothing")
    b.set_defaults(fn=build)
    for name, fn in (("collect", collect), ("run", run_all), ("analyze", analyze)):
        p = sub.add_parser(name)
        p.add_argument("out")
        if name == "run":
            p.add_argument("--iterations", type=int, default=1)
        p.set_defaults(fn=fn)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
