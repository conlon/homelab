# SOPS Encryption

Secrets in this repo are encrypted with [SOPS](https://github.com/getsops/sops) using both an age key and a PGP key (see `.sops.yaml`).

The age key is the primary decryption method for Flux. The PGP key is retained as a secondary recipient.

---

## Bootstrapping the age key (Flux decryption)

This must be done **before** running `flux bootstrap`, or immediately after on a fresh cluster. Flux uses this secret to decrypt all SOPS-encrypted manifests.

```bash
# The age key is stored locally at:
# ~/.config/sops/age/keys.txt

kubectl create namespace flux-system  # if it doesn't exist yet

kubectl create secret generic sops-age \
  --namespace=flux-system \
  --from-file=age.agekey="${HOME}/.config/sops/age/keys.txt"
```

Then run `flux bootstrap` as documented in `bootstrap.md`. Flux will pick up the `sops-age` secret automatically (wired via `flux-system/patches/sops-decryption.yaml`).

---

## Bootstrapping the GPG key (legacy)

The GPG key (`77B66B06D2E06C5E776F4F92C7314867B986373D`) is still a SOPS recipient but is no longer used by Flux for decryption. It can be used locally for editing secrets.

```bash
export KEY_FP="$(gpg -k | grep -B 1 flux | head -1 | tr -d " ")"

gpg --export-secret-keys --armor "${KEY_FP}" |
kubectl create secret generic sops-gpg \
  --namespace=flux-system \
  --from-file=sops.asc=/dev/stdin
```

---

## Creating/editing secrets

Secrets are encrypted per `.sops.yaml` (both age and PGP recipients). Use the public age key for creating new secrets; the private key is required for editing.

```bash
# Encrypt a new secret
sops --encrypt plain-secret.yaml > encrypted-secret.sops.yaml

# Edit an existing encrypted secret
sops encrypted-secret.sops.yaml
```

## Generating a new age key (if lost)

```bash
age-keygen -o ~/.config/sops/age/keys.txt
```

Update `.sops.yaml` with the new public key, re-encrypt all secrets, update the `sops-age` cluster secret, and commit.
