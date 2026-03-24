#!/usr/bin/env bash
# Wrapper for terraform commands that injects SOPS-decrypted secrets as
# environment variables. Use this instead of calling terraform directly.
#
# Usage:
#   ./run.sh init -migrate-state
#   ./run.sh plan
#   ./run.sh apply
set -euo pipefail

SECRETS=$(sops -d secrets.sops.yaml)

export AWS_ACCESS_KEY_ID=$(echo "$SECRETS" | grep r2_access_key_id | awk '{print $2}')
export AWS_SECRET_ACCESS_KEY=$(echo "$SECRETS" | grep r2_secret_access_key | awk '{print $2}')

terraform "$@"
