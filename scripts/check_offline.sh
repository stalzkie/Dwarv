#!/usr/bin/env bash
# POSIX convenience wrapper around `dwarv check-offline` for Linux/macOS/WSL2 users
# who prefer a script. Windows users: just run `dwarv check-offline` directly.
set -euo pipefail

exec dwarv check-offline "$@"
