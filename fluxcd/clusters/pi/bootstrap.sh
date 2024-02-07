#!/bin/bash -e

# export GITHUB_TOKEN= # set in shell rc
export KEY_FP="$(gpg -k | grep -B 1 flux | head -1 | tr -d " ")"

kubectl create namespace flux-system

gpg --export-secret-keys --armor "${KEY_FP}" |
kubectl create secret generic sops-gpg \
--namespace=flux-system \
--from-file=sops.asc=/dev/stdin

flux bootstrap github \
  --token-auth \
  --owner=conlon \
  --repository=fluxcd \
  --branch=main \
  --path=clusters/pi \
  --personal
