output "cluster_name" {
  description = "GKE cluster. scripts/up.sh fetches its credentials."
  value       = google_container_cluster.this.name
}

output "cluster_zone" {
  description = "Zone of the GKE cluster."
  value       = google_container_cluster.this.location
}

output "kafka_bootstrap" {
  description = "Managed Kafka bootstrap address, SASL on port 9092."
  value       = google_managed_kafka_cluster.this.bootstrap_address
}

output "sql_private_ip" {
  description = "Cloud SQL's private address."
  value       = google_sql_database_instance.this.private_ip_address
}

output "data_bucket" {
  description = "Manifests and labels."
  value       = google_storage_bucket.data.name
}

output "dataproc_cluster" {
  description = "Dataproc cluster running Flink."
  value       = google_dataproc_cluster.analytics.name
}

output "dataproc_bucket" {
  description = "Holds the Flink submit script and checkpoints."
  value       = google_storage_bucket.dataproc.name
}

output "dispatch_function" {
  description = "Name of the Dispatch function."
  value       = google_cloudfunctions2_function.dispatch.name
}

output "planned_vcpus" {
  description = "Compute Engine vCPUs this stack runs at once."
  value       = local.planned_vcpus
}
