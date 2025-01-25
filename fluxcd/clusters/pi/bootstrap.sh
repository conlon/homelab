#!/bin/bash -e

# export GITHUB_TOKEN= # set in shell rc
export KEY_FP="$(gpg -k | grep -B 1 flux | head -1 | tr -d " ")"

kubectl -n flux-system delete secret sops-gpg || true

kubectl create namespace flux-system 2> /dev/null || true

gpg --export-secret-keys --armor "${KEY_FP}" |
kubectl create secret generic sops-gpg \
--namespace=flux-system \
--from-file=sops.asc=/dev/stdin

flux bootstrap github \
  --token-auth \
  --owner=conlon \
  --repository=homelab \
  --branch=main \
  --path=fluxcd/clusters/pi \
  --personal
