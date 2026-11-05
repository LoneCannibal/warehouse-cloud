locals {
  machine_vcpus = {
    "n2-standard-2" = 2
    "n2-highmem-2"  = 2
    "n2-standard-4" = 4
    "n2-highmem-4"  = 4
    "e2-standard-2" = 2
    "e2-highmem-2"  = 2
  }

  # Every Compute Engine vCPU this stack runs at once: the GKE nodes plus the Dataproc node.
  planned_vcpus = var.gke_node_count * lookup(local.machine_vcpus, var.gke_machine_type, 99) + lookup(local.machine_vcpus, var.dataproc_machine_type, 99)
}

resource "google_service_account" "gke_nodes" {
  account_id   = "gke-nodes"
  display_name = "GKE nodes"
}

resource "google_project_iam_member" "gke_nodes" {
  for_each = toset([
    "roles/artifactregistry.reader",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.viewer",
    "roles/stackdriver.resourceMetadata.writer",
  ])

  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.gke_nodes.email}"
}

# Zonal, because the GKE free tier pays the management fee of one zonal cluster.
resource "google_container_cluster" "this" {
  name     = "warehouse"
  location = var.zone

  network         = google_compute_network.this.id
  subnetwork      = google_compute_subnetwork.gke.id
  networking_mode = "VPC_NATIVE"

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  # GKE insists on a default pool while it creates the cluster. One node, deleted
  # straight away, keeps the vCPU count inside the cap during creation.
  remove_default_node_pool = true
  initial_node_count       = 1

  node_config {
    machine_type    = var.gke_machine_type
    disk_size_gb    = 20
    disk_type       = "pd-standard"
    service_account = google_service_account.gke_nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
    master_ipv4_cidr_block  = "172.16.0.0/28"
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  release_channel {
    channel = "REGULAR"
  }

  # Container logs go to Cloud Logging without any agent of ours.
  logging_config {
    enable_components = ["SYSTEM_COMPONENTS", "WORKLOADS"]
  }

  # Our own Prometheus collects the metrics. Managed Prometheus would bill per sample.
  monitoring_config {
    enable_components = ["SYSTEM_COMPONENTS"]

    managed_prometheus {
      enabled = false
    }
  }

  deletion_protection = false

  depends_on = [google_project_iam_member.gke_nodes]
}

resource "google_container_node_pool" "workers" {
  name       = "workers"
  cluster    = google_container_cluster.this.id
  location   = var.zone
  node_count = var.gke_node_count

  node_config {
    machine_type    = var.gke_machine_type
    disk_size_gb    = 30
    disk_type       = "pd-standard"
    service_account = google_service_account.gke_nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  # A surge upgrade adds a node first, which would break the vCPU cap. This replaces
  # one node at a time instead.
  upgrade_settings {
    max_surge       = 0
    max_unavailable = 1
  }

  lifecycle {
    precondition {
      condition     = local.planned_vcpus <= var.max_vcpus
      error_message = "The GKE nodes and the Dataproc node need ${local.planned_vcpus} vCPUs, above the cap of ${var.max_vcpus}. Use fewer or smaller nodes, or raise max_vcpus knowing it raises the hourly cost."
    }
  }
}
