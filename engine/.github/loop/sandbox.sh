#!/usr/bin/env bash
# Sandbox for Claude Code on the runner: bubblewrap and socat, user namespaces unrestricted (ubuntu 24.04 blocks them
# in AppArmor: "bwrap: setting up uid map: Permission denied"), and a probe that fails the job at once instead of
# after three agent sessions without Bash. Every job that runs agent or candidate code calls it first.
set -eu
sudo apt-get install -y -q bubblewrap socat
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0 || true   # older images have no such key
bwrap --unshare-all --ro-bind / / --dev /dev --proc /proc true
echo "sandbox: bwrap works"
