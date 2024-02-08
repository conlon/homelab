# homelab
Infrastructure as code for my home servers and services.

Recently merged multiple repos into here, so there's still some cruft that needs to be tidied up, especially in ansible dirs.

## Tech stack
- Use ansible to configure hosts and setup k3s
- Run mostly on a cluster of raspberry pi 4b and 5 with ssd's
- Use fluxcd to define and maintain k8s resources
- Use renovate-bot to keep an eye on image updates and automatically open PRs when appropriate (wip)
- mozilla sops encrypts sensitive values in ansible and k8s secrets, allowing this repo to be public

## Setup steps
This can be run anytime a host is added or other ansible configuration has changed.

Once flux is setup after cluster creation, it automatically updates k8s resources.
```bash
(cd ansible/host-config/ && ansible-playbook playbook.yml)
(cd ansible/k3s-config/ && ansible-playbook site.yml)

# required first-time only
bash clusters/pi/bootstrap.sh
```
