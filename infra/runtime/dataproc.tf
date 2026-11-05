# Flink on Dataproc: one node running Flink on YARN.
#
# Terraform has no Flink job type for Dataproc, and gcloud's is in private preview.
# Image 2.2 installs Pig, and Google documents running shell commands from a Pig job,
# so a Pig job runs dataproc/submit-flink.sh on the node. CI replaces the running job
# the same way.

resource "google_dataproc_cluster" "analytics" {
  name   = "warehouse-analytics"
  region = var.region

  cluster_config {
    staging_bucket = google_storage_bucket.dataproc.name
    temp_bucket    = google_storage_bucket.dataproc.name

    gce_cluster_config {
      zone = var.zone
      # Written as Google stores them, so a plan finds no difference. Dataproc adds the
      # last three scopes itself, and a difference in scopes would rebuild the cluster.
      subnetwork       = google_compute_subnetwork.data.self_link
      internal_ip_only = true
      service_account  = data.google_service_account.dataproc.email
      service_account_scopes = [
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/cloud.useraccounts.readonly",
        "https://www.googleapis.com/auth/devstorage.read_write",
        "https://www.googleapis.com/auth/logging.write",
      ]
      tags = ["dataproc"]
    }

    master_config {
      num_instances = 1
      machine_type  = var.dataproc_machine_type

      disk_config {
        boot_disk_type    = "pd-standard"
        boot_disk_size_gb = 50
      }
    }

    worker_config {
      num_instances = 0
    }

    software_config {
      # Image 2.2 ships Flink 1.17.0 on Java 11, which the job's pom.xml matches.
      image_version       = "2.2-debian12"
      optional_components = ["FLINK"]

      override_properties = {
        "dataproc:dataproc.allow.zero.workers" = "true"
        # YARN container logs, which include Flink's, reach Cloud Logging as yarn-userlogs.
        "dataproc:dataproc.logging.stackdriver.enable" = "true"
      }
    }

    # The Component Gateway serves the YARN and Flink web UIs through the console,
    # so no SSH tunnel or open port is needed.
    endpoint_config {
      enable_http_port_access = true
    }

    # Deletes the cluster this long after creation, even if `make down` is forgotten.
    lifecycle_config {
      auto_delete_time = timeadd(timestamp(), "${var.dataproc_max_hours}h")
    }
  }

  lifecycle {
    ignore_changes = [cluster_config[0].lifecycle_config[0].auto_delete_time]
  }

  depends_on = [
    google_storage_bucket_iam_member.dataproc_bucket,
    google_compute_firewall.dataproc_internal,
    google_compute_router_nat.this,
  ]
}

# What the submit script needs to know. CI can then redeploy knowing only bucket names.
resource "google_storage_bucket_object" "flink_env" {
  name   = "dataproc/flink.env"
  bucket = google_storage_bucket.dataproc.name
  content = join("\n", [
    "JAR_URI=gs://${local.artifacts_bucket}/${var.flink_jar_object}",
    "KAFKA_BOOTSTRAP=${local.kafka_bootstrap}",
    "CHECKPOINT_DIR=gs://${google_storage_bucket.dataproc.name}/flink-checkpoints",
    "",
  ])
}

resource "google_storage_bucket_object" "submit_flink" {
  name   = "dataproc/submit-flink.sh"
  bucket = google_storage_bucket.dataproc.name
  source = "${path.module}/dataproc/submit-flink.sh"
}

resource "google_dataproc_job" "start_flink" {
  region       = var.region
  force_delete = true

  placement {
    cluster_name = google_dataproc_cluster.analytics.name
  }

  pig_config {
    query_list = [
      "fs -cp -f gs://${google_storage_bucket.dataproc.name}/dataproc/submit-flink.sh file:///tmp/submit-flink.sh",
      "sh chmod 750 /tmp/submit-flink.sh",
      "sh /tmp/submit-flink.sh gs://${google_storage_bucket.dataproc.name}/dataproc/flink.env",
    ]
  }

  depends_on = [
    google_storage_bucket_object.flink_env,
    google_storage_bucket_object.submit_flink,
    google_managed_kafka_topic.this,
  ]
}
