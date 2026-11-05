locals {
  # Every API the two stacks use. Enabling an API is free.
  services = toset([
    "artifactregistry.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudbilling.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudfunctions.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "dataproc.googleapis.com",
    "eventarc.googleapis.com",
    "firestore.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "logging.googleapis.com",
    "managedkafka.googleapis.com",
    "monitoring.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "servicenetworking.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
    "sts.googleapis.com",
  ])

  team = { for pair in setproduct(var.team_members, var.team_roles) : "${pair[0]} ${pair[1]}" => pair }
}

resource "google_project_service" "this" {
  for_each = local.services

  service = each.key
  # Turning an API off on destroy would break anything still using it.
  disable_on_destroy = false
}

# ---- Terraform state and build artifacts ----------------------------------------

resource "google_storage_bucket" "state" {
  name                        = "${var.project_id}-tfstate"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  depends_on = [google_project_service.this]
}

# The Flink JAR and the Cloud Run function sources.
resource "google_storage_bucket" "artifacts" {
  name                        = "${var.project_id}-artifacts"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true

  depends_on = [google_project_service.this]
}

resource "google_artifact_registry_repository" "images" {
  repository_id = "warehouse"
  location      = var.region
  format        = "DOCKER"

  # Only 0.5 GB of storage is free, so old images are cleaned up.
  cleanup_policy_dry_run = false

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"

    most_recent_versions {
      keep_count = 3
    }
  }

  cleanup_policies {
    id     = "delete-the-rest"
    action = "DELETE"

    condition {
      tag_state  = "ANY"
      older_than = "86400s"
    }
  }

  depends_on = [google_project_service.this]
}

# ---- Firestore --------------------------------------------------------------------

# Here rather than in infra/runtime: it costs nothing while idle, and a database id
# cannot always be reused straight after deletion.
resource "google_firestore_database" "warehouse" {
  name                    = "warehouse"
  location_id             = var.region
  type                    = "FIRESTORE_NATIVE"
  deletion_policy         = "DELETE"
  delete_protection_state = "DELETE_PROTECTION_DISABLED"

  depends_on = [google_project_service.this]
}

# ---- Team access --------------------------------------------------------------------

# Each teammate signs in with their own Google account. No role here touches billing,
# so nobody but the billing account's administrator can change how the project is paid for.
resource "google_project_iam_member" "team" {
  for_each = local.team

  project = var.project_id
  role    = each.value[1]
  member  = "user:${each.value[0]}"
}

# ---- Builds for Cloud Run functions ------------------------------------------------

# New projects no longer give Cloud Build broad default rights, so function builds
# run as this account. infra/runtime uses it for the Dispatch function too.
resource "google_service_account" "function_builder" {
  account_id   = "function-builder"
  display_name = "Builds Cloud Run functions"

  depends_on = [google_project_service.this]
}

resource "google_project_iam_member" "function_builder" {
  for_each = toset([
    "roles/artifactregistry.writer",
    "roles/logging.logWriter",
    "roles/storage.objectViewer",
  ])

  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.function_builder.email}"
}
