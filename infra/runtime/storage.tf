# Cloud Storage holds manifests and labels. Writing a manifest notifies a Pub/Sub
# topic, and function.tf pushes each notification to the Dispatch function.

resource "google_storage_bucket" "data" {
  name                        = "${var.project_id}-warehouse-data"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true

  lifecycle_rule {
    condition {
      age = 30
    }

    action {
      type = "Delete"
    }
  }
}

# Dataproc staging and temporary files, the Flink submit script, and checkpoints.
# Named here so Dataproc does not create buckets that `make down` would leave behind.
resource "google_storage_bucket" "dataproc" {
  name                        = "${var.project_id}-dataproc"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
}

data "google_storage_project_service_account" "gcs" {}

resource "google_pubsub_topic" "manifests" {
  name = "warehouse-manifests"
}

resource "google_pubsub_topic_iam_member" "storage_publishes_manifests" {
  topic  = google_pubsub_topic.manifests.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${data.google_storage_project_service_account.gcs.email_address}"
}

resource "google_storage_notification" "manifests" {
  bucket             = google_storage_bucket.data.name
  topic              = google_pubsub_topic.manifests.id
  payload_format     = "JSON_API_V1"
  event_types        = ["OBJECT_FINALIZE"]
  object_name_prefix = "manifests/"

  depends_on = [google_pubsub_topic_iam_member.storage_publishes_manifests]
}

# A manifest that fails five deliveries lands here, kept for a week for inspection.
resource "google_pubsub_topic" "dispatch_dead_letter" {
  name = "warehouse-dispatch-dead-letter"
}

resource "google_pubsub_subscription" "dispatch_dead_letters" {
  name                       = "warehouse-dispatch-dead-letters"
  topic                      = google_pubsub_topic.dispatch_dead_letter.id
  message_retention_duration = "604800s"
}
