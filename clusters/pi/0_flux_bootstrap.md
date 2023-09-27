# To bootstrap a fresh cluster:
``` bash
flux bootstrap github \
  --token-auth \
  --owner=conlon \
  --repository=fluxcd \
  --branch=main \
  --path=clusters/pi \
  --personal
```

<!-- # with ssh:
``` bash
flux bootstrap git \
  --url=ssh://git@github.com/conlon/fluxcd \
  --branch=main \
  --private-key-file=~/.ssh/id_ed25519 \
  --password= \
  --path=clusters/pi
``` -->
