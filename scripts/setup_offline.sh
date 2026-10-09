#!/usr/bin/env bash
# POSIX convenience wrapper around `dwarv setup-offline` for Linux/macOS/WSL2 users
# who prefer a script. Windows users: just run `dwarv setup-offline` directly.
set -euo pipefail

exec dwarv setup-offline "$@"
