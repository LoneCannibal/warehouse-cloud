output "state_bucket" {
  description = "Goes into infra/runtime/backend.hcl."
  value       = google_storage_bucket.state.name
}

output "artifacts_bucket" {
  description = "Holds the Flink JAR and the function sources."
  value       = google_storage_bucket.artifacts.name
}

output "image_registry" {
  description = "Where CI pushes images."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "ci_workload_identity_provider" {
  description = "Set as the GCP_WORKLOAD_IDENTITY_PROVIDER GitHub repository variable."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "ci_service_account" {
  description = "Set as the GCP_CI_SERVICE_ACCOUNT GitHub repository variable."
  value       = google_service_account.ci.email
}

output "firestore_database" {
  description = "Firestore database the services use."
  value       = google_firestore_database.warehouse.name
}

output "budget_topic" {
  description = "Pub/Sub topic the budget publishes to. `make killswitch-check` sends a self-test here."
  value       = google_pubsub_topic.budget.name
}
