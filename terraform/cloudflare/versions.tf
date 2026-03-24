terraform {
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
    sops = {
      source  = "carlpett/sops"
      version = "~> 1.0"
    }
  }

  backend "s3" {
    bucket = "terraform-state"
    key    = "cloudflare/terraform.tfstate"
    region = "auto"

    endpoints = {
      s3 = "https://02eda483e3190ae19518e619b4dc7f25.r2.cloudflarestorage.com"
    }

    # Credentials are passed via AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
    # environment variables — see run.sh
    skip_credentials_validation  = true
    skip_metadata_api_check      = true
    skip_region_validation       = true
    skip_requesting_account_id   = true
    force_path_style             = true
  }
}
