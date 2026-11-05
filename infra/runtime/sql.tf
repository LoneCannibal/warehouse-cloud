# One PostgreSQL instance with a schema per service, reachable only on a private address.

resource "random_password" "database" {
  length  = 24
  special = false
}

# A deleted instance's name stays reserved for about a week, so each `make up` takes a
# fresh suffix.
resource "random_id" "sql" {
  byte_length = 3
}

resource "google_sql_database_instance" "this" {
  name                = "warehouse-${random_id.sql.hex}"
  database_version    = "POSTGRES_16"
  region              = var.region
  deletion_protection = false

  settings {
    # PostgreSQL 16 defaults to the Enterprise Plus edition, which costs far more and
    # has no shared-core machines. Naming the edition keeps the small machine.
    edition           = "ENTERPRISE"
    tier              = var.cloudsql_tier
    availability_type = "ZONAL"
    disk_type         = "PD_HDD"
    disk_size         = 10
    disk_autoresize   = false

    location_preference {
      zone = var.zone
    }

    backup_configuration {
      enabled = false
    }

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.this.id
      ssl_mode        = "ENCRYPTED_ONLY"
    }

    # Pools are capped at 3 connections per pod, 6 for the scheduler. At full HPA
    # scale-out that comes to about 60, above the small machine's default.
    database_flags {
      name  = "max_connections"
      value = "100"
    }
  }

  depends_on = [google_service_networking_connection.sql]
}

resource "google_sql_database" "warehouse" {
  name     = "warehouse"
  instance = google_sql_database_instance.this.name
}

resource "google_sql_user" "warehouse" {
  name     = "warehouse"
  instance = google_sql_database_instance.this.name
  password = random_password.database.result
}
