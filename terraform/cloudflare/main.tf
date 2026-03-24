locals {
  secrets        = data.sops_file.secrets.data
  account_id     = local.secrets["cloudflare_account_id"]
  tunnel_id      = local.secrets["cloudflare_tunnel_id"]
  allowed_emails = jsondecode(local.secrets["allowed_emails"])
}

provider "cloudflare" {
  api_token = local.secrets["cloudflare_api_token"]
}

data "sops_file" "secrets" {
  source_file = "secrets.sops.yaml"
}

# ---------------------------------------------------------------------------
# Identity provider — Google
# ---------------------------------------------------------------------------

resource "cloudflare_zero_trust_access_identity_provider" "google" {
  account_id = local.account_id
  name       = "Google"
  type       = "google"

  config {
    client_id     = local.secrets["google_client_id"]
    client_secret = local.secrets["google_client_secret"]
  }
}

# ---------------------------------------------------------------------------
# Access application — Requests (overseerr)
# ---------------------------------------------------------------------------

resource "cloudflare_zero_trust_access_application" "requests" {
  account_id       = local.account_id
  name             = "requests"
  domain           = "requests.fellowfreak.dev"
  type             = "self_hosted"
  session_duration = "730h"

  allowed_idps              = [cloudflare_zero_trust_access_identity_provider.google.id]
  auto_redirect_to_identity = true
}

# ---------------------------------------------------------------------------
# Access policy — Plex users allowlist
# ---------------------------------------------------------------------------

resource "cloudflare_zero_trust_access_policy" "requests" {
  account_id     = local.account_id
  application_id = cloudflare_zero_trust_access_application.requests.id
  name           = "requests"
  precedence     = 1
  decision       = "allow"

  include {
    email = local.allowed_emails
  }
}

# ---------------------------------------------------------------------------
# Tunnel config — public hostname routing
# ---------------------------------------------------------------------------

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "homelab" {
  account_id = local.account_id
  tunnel_id  = local.tunnel_id

  config {
    ingress_rule {
      hostname = "requests.fellowfreak.dev"
      service  = "https://traefik.traefik.svc.cluster.local"
      origin_request {
        origin_server_name     = "requests.fellowfreak.dev"
        connect_timeout        = "30s"
        keep_alive_connections = 100
        keep_alive_timeout     = "1m30s"
        proxy_address          = "127.0.0.1"
        tcp_keep_alive         = "30s"
        tls_timeout            = "10s"
      }
    }

    # Catch-all — required by Cloudflare
    ingress_rule {
      service = "http_status:404"
    }
  }
}
