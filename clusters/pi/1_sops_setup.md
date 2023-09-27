# To setup sops encryption on a fresh cluster
https://fluxcd.io/flux/guides/mozilla-sops/

```
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

```
export KEY_FP=77B6...

gpg --export-secret-keys --armor "${KEY_FP}" |
kubectl create secret generic sops-gpg \
--namespace=flux-system \
--from-file=sops.asc=/dev/stdin
```

<!-- 
already completed this during bootstrap
```
flux create source git flux-system \
--url=https://github.com/conlon/fluxcd \
--branch=main
``` -->

```
flux create kustomization pi \
--source=flux-system \
--path=./clusters/pi \
--prune=true \
--interval=10m \
--decryption-provider=sops \
--decryption-secret=sops-gpg
```

---
## Optional:

```
gpg --export --armor "${KEY_FP}" > ./.sops.pub.asc
```

- Edit [.sops.yaml](.sops.yaml) to include the new KEY_FP.
```
cat <<EOF > ./.sops.yaml
creation_rules:
  - path_regex: .*.yaml
    encrypted_regex: ^(data|stringData)$
    pgp: ${KEY_FP}
EOF
```


# To create new secrets:
```
gpg --import ./.sops.pub.asc
```
> The public key is sufficient for creating brand new files. The secret key is required for decrypting and editing existing files because SOPS computes a MAC on all values. When using solely the public key to add or remove a field, the whole file should be deleted and recreated.