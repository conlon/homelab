# Upgrading flux
```bash
brew upgrade fluxcd/tap/flux
flux install --export > gotk-components.yaml
```

#### Update fluxcd-github personal access token
To rotate the bootstrap key, be it a token or a deploy key:

- update github PAT locally (e.g. ~/.zshrc)
- rerun flux bootstrap with the same args as before (`./bootstrap.sh`). Try twice if first time errors!
- flux will generate a new secret and will update the deploy key if you’re using SSH deploy keys

via https://github.com/fluxcd/flux2/discussions/2161#discussioncomment-1726813
