#!/usr/bin/env bash
# Sandbox for Claude Code on the runner: bubblewrap and socat, user namespaces unrestricted (ubuntu 24.04 blocks them
# in AppArmor: "bwrap: setting up uid map: Permission denied"), and a probe that fails the job at once instead of
# after three agent sessions without Bash. Every job that runs agent or candidate code calls it first.
set -eu
sudo apt-get install -y -q bubblewrap socat
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0 || true   # older images have no such key
bwrap --unshare-all --ro-bind / / --dev /dev --proc /proc true
# Check the actual script enforcement before starting work; socket failures must survive exec.
python3 -c 'import socket; socket.socket().close()'
bwrap --unshare-net --ro-bind / / --dev /dev --proc /proc \
  python3 "$(dirname "$0")/offline_exec.py" python3 -c '
import socket
for family in (socket.AF_INET, socket.AF_INET6, socket.AF_UNIX):
    try:
        socket.socket(family)
    except PermissionError:
        continue
    raise SystemExit("offline socket enforcement failed")
'
echo "sandbox: bwrap works"
