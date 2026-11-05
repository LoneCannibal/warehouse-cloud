# Managed Service for Apache Kafka. It runs in Google's own project, so its vCPUs do
# not count against this project's Compute Engine quota. It accepts only TLS with
# SASL, and clients log in with their Google identity (roles/managedkafka.client).
# Creation usually takes 20 to 30 minutes.

locals {
  topics = {
    "warehouse.orders"            = 6
    "warehouse.task-events"       = 6
    "warehouse.analytics.results" = 3
  }
}

resource "google_managed_kafka_cluster" "this" {
  cluster_id = "warehouse"
  location   = var.region

  capacity_config {
    vcpu_count   = var.kafka_vcpus
    memory_bytes = var.kafka_memory_gib * 1024 * 1024 * 1024
  }

  gcp_config {
    access_config {
      # Brokers get addresses in this subnet and DNS names inside the VPC, so GKE,
      # Dataproc and the function all reach them privately.
      network_configs {
        subnet = google_compute_subnetwork.data.id
      }
    }
  }
}

resource "google_managed_kafka_topic" "this" {
  for_each = local.topics

  topic_id           = each.key
  cluster            = google_managed_kafka_cluster.this.cluster_id
  location           = var.region
  partition_count    = each.value
  replication_factor = var.kafka_replication_factor
}
