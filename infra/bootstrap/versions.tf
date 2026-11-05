# Run once per project. Holds what must outlive `make down`: the Terraform state
# bucket, container images, the Flink JAR, Firestore, CI access, team access, and the
# budget with its kill switch. Nothing here bills by the hour, so it stays up between
# work sessions.
#
# State stays local, in this folder, because this stack creates the state bucket.

terraform {
  required_version = ">= 1.10"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.10"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region

  # The Billing Budget API needs a quota project when it is called with your own
  # login rather than a service account.
  user_project_override = true
  billing_project       = var.project_id

  default_labels = {
    project    = "warehouse"
    managed-by = "terraform"
  }
}
