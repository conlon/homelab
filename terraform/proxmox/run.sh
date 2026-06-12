#!/usr/bin/env bash
# Thin wrapper — keeps the entry point consistent with other terraform modules.
#
# Usage:
#   ./run.sh init
#   ./run.sh plan
#   ./run.sh apply
set -euo pipefail

terraform "$@"
