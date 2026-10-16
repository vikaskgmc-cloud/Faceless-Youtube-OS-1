#!/usr/bin/env bash
set -euo pipefail

# The application uses only the Python standard library; validate syntax after merges.
python3 -m compileall -q studio tests
