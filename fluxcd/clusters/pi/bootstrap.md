# Quick bootstrap after first time setup:
``` bash
export GITHUB_TOKEN=...

kubectl create namespace flux-system

# SOPS age key (used by Flux for decryption — required before bootstrap)
kubectl create secret generic sops-age \
  --namespace=flux-system \
  --from-file=age.agekey="${HOME}/.config/sops/age/keys.txt"

flux bootstrap github \
  --token-auth \
  --owner=conlon \
  --repository=fluxcd \
  --branch=main \
  --path=clusters/pi \
  --personal
```

# To bootstrap a fresh cluster from scratch:
``` bash
flux bootstrap github \
  --token-auth \
  --owner=conlon \
  --repository=fluxcd \
  --branch=main \
  --path=clusters/pi \
  --personal
```

## Sops usage
See [sops.md](./sops.md)