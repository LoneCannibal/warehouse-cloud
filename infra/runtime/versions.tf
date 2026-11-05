# Everything billed by the hour. `make up` creates it and `make down` destroys it, so
# the credit only drains while someone is working or demonstrating.

terraform {
  required_version = ">= 1.10"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.10"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.3"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 3.4"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
  }

  # Filled in by backend.hcl, which `make backend` writes from the bootstrap outputs.
  backend "gcs" {}
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone

  default_labels = {
    project    = "warehouse"
    managed-by = "terraform"
  }
}

data "google_project" "this" {}

data "google_client_config" "current" {}

locals {
  # Names infra/bootstrap gives these. Deriving them avoids passing outputs by hand.
  artifacts_bucket   = "${var.project_id}-artifacts"
  function_builder   = "projects/${var.project_id}/serviceAccounts/function-builder@${var.project_id}.iam.gserviceaccount.com"
  firestore_database = "warehouse"
  image_registry     = "${var.region}-docker.pkg.dev/${var.project_id}/warehouse"

  cluster_host = "https://${google_container_cluster.this.endpoint}"
  # try() only matters to the mocked plan in tests/, where the cluster has no certificate.
  cluster_ca = try(base64decode(google_container_cluster.this.master_auth[0].cluster_ca_certificate), "")
}

provider "kubernetes" {
  host                   = local.cluster_host
  token                  = data.google_client_config.current.access_token
  cluster_ca_certificate = local.cluster_ca
}

provider "helm" {
  kubernetes = {
    host                   = local.cluster_host
    token                  = data.google_client_config.current.access_token
    cluster_ca_certificate = local.cluster_ca
  }
}
