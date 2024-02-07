# Quick bootstrap after first time setup:
``` bash
export GITHUB_TOKEN=...
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

## To setup sops encryption:
https://fluxcd.io/flux/guides/mozilla-sops/
``` bash
gpg --batch --full-generate-key <<EOF
%no-protection
Key-Type: 1
Key-Length: 4096
Subkey-Type: 1
Subkey-Length: 4096
Expire-Date: 0
Name-Comment: "flux secrets"
Name-Real: cluster0.fellowfreak.dev
EOF
```

``` bash
export KEY_FP="$(gpg -k | grep -B 1 flux | head -1 | tr -d " ")"

gpg --export-secret-keys --armor "${KEY_FP}" |
kubectl create secret generic sops-gpg \
--namespace=flux-system \
--from-file=sops.asc=/dev/stdin
```

Either run this flux command
``` bash
flux create kustomization pi \
--source=flux-system \
--path=./clusters/pi \
--prune=true \
--interval=10m \
--decryption-provider=sops \
--decryption-secret=sops-gpg
```

Or add and commit this code in `flux-system/gotk-sync.yaml`:
``` yaml
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: flux-system
  namespace: flux-system
spec:
  interval: 10m0s
  path: ./clusters/pi
  prune: true
  sourceRef:
    kind: GitRepository
    name: flux-system

# ADD THIS SECTION
  # Enable decryption
  decryption:
    # Use the sops provider
    provider: sops
    secretRef:
      # Reference the new 'sops-gpg' secret
      name: sops-gpg
```


---
## Optional:

``` bash
gpg --export --armor "${KEY_FP}" > ./.sops.pub.asc
```

- Edit [.sops.yaml](.sops.yaml) to include the new KEY_FP.
``` bash
cat <<EOF > ./.sops.yaml
creation_rules:
  - path_regex: .*.yaml
    encrypted_regex: ^(data|stringData)$
    pgp: ${KEY_FP}
EOF
```


# To create new secrets:
``` bash
gpg --import ./.sops.pub.asc
```
> The public key is sufficient for creating brand new files. The secret key is required for decrypting and editing existing files because SOPS computes a MAC on all values. When using solely the public key to add or remove a field, the whole file should be deleted and recreated.