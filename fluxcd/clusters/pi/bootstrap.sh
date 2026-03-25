#!/bin/bash -e

# export GITHUB_TOKEN= # set in shell rc

kubectl create namespace flux-system 2> /dev/null || true

kubectl create secret generic sops-age \
  --namespace=flux-system \
  --from-file=age.agekey="${HOME}/.config/sops/age/keys.txt" \
  --dry-run=client -o yaml | kubectl apply -f -

flux bootstrap github \
  --token-auth \
  --owner=conlon \
  --repository=homelab \
  --branch=main \
  --path=fluxcd/clusters/pi \
  --personal
