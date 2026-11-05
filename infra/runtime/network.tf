resource "google_compute_network" "this" {
  name                    = "warehouse"
  auto_create_subnetworks = false
}

# GKE nodes, with secondary ranges for pods and Services.
resource "google_compute_subnetwork" "gke" {
  name                     = "gke"
  region                   = var.region
  network                  = google_compute_network.this.id
  ip_cidr_range            = "10.10.0.0/20"
  private_ip_google_access = true

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.20.0.0/16"
  }

  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.30.0.0/20"
  }
}

# Dataproc, and the addresses Managed Kafka gives its brokers.
resource "google_compute_subnetwork" "data" {
  name                     = "data"
  region                   = var.region
  network                  = google_compute_network.this.id
  ip_cidr_range            = "10.10.16.0/24"
  private_ip_google_access = true
}

# Direct VPC egress for the Dispatch function, so it can reach Kafka. Cloud Run asks
# for at least a /26.
resource "google_compute_subnetwork" "serverless" {
  name                     = "serverless"
  region                   = var.region
  network                  = google_compute_network.this.id
  ip_cidr_range            = "10.10.17.0/26"
  private_ip_google_access = true
}

# Nodes have no public addresses. NAT lets them pull public images such as ZooKeeper.
resource "google_compute_router" "this" {
  name    = "warehouse"
  region  = var.region
  network = google_compute_network.this.id
}

resource "google_compute_router_nat" "this" {
  name                               = "warehouse"
  router                             = google_compute_router.this.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

# Dataproc refuses to create a cluster whose nodes cannot talk to each other.
resource "google_compute_firewall" "dataproc_internal" {
  name        = "dataproc-internal"
  network     = google_compute_network.this.id
  source_tags = ["dataproc"]
  target_tags = ["dataproc"]

  allow {
    protocol = "tcp"
  }

  allow {
    protocol = "udp"
  }

  allow {
    protocol = "icmp"
  }
}

# Cloud SQL's private address comes from this peered range.
resource "google_compute_global_address" "sql" {
  name          = "cloud-sql"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 20
  network       = google_compute_network.this.id
}

resource "google_service_networking_connection" "sql" {
  network                 = google_compute_network.this.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.sql.name]
}
