# Plans the whole stack against mocked providers, so it needs no Google Cloud project,
# no credentials, and creates nothing. It proves the configuration is internally
# consistent, and pins the settings that decide what the credit pays for. It cannot
# prove that Google Cloud would accept every value.
#
#   terraform -chdir=infra/runtime init -backend=false
#   terraform -chdir=infra/runtime test

mock_provider "google" {
  mock_data "google_project" {
    defaults = {
      number = "123456789012"
    }
  }

  mock_data "google_client_config" {
    defaults = {
      access_token = "mock-token"
    }
  }

  mock_data "google_storage_project_service_account" {
    defaults = {
      email_address = "service-123456789012@gs-project-accounts.iam.gserviceaccount.com"
    }
  }

  mock_resource "google_container_cluster" {
    override_during = plan
    defaults = {
      endpoint = "203.0.113.10"
    }
  }

  mock_resource "google_service_account" {
    override_during = plan
    defaults = {
      email = "mock-account@group18-warehouse.iam.gserviceaccount.com"
      name  = "projects/group18-warehouse/serviceAccounts/mock-account@group18-warehouse.iam.gserviceaccount.com"
      id    = "projects/group18-warehouse/serviceAccounts/mock-account@group18-warehouse.iam.gserviceaccount.com"
    }
  }

  mock_resource "google_managed_kafka_cluster" {
    override_during = plan
    defaults = {
      bootstrap_address = "bootstrap.warehouse.asia-south1.managedkafka.group18-warehouse.cloud.goog:9092"
    }
  }
}

mock_provider "kubernetes" {}
mock_provider "helm" {}
mock_provider "random" {}
mock_provider "archive" {}

variables {
  project_id   = "group18-warehouse"
  git_repo_url = "https://github.com/group18/warehouse-cloud.git"
}

run "the_stack_stays_within_its_vcpu_cap" {
  command = plan

  assert {
    condition     = local.planned_vcpus == 8
    error_message = "Three n2-standard-2 nodes and one n2-highmem-2 node should use exactly 8 vCPUs."
  }

  assert {
    condition     = google_container_node_pool.workers.upgrade_settings[0].max_surge == 0
    error_message = "A surge upgrade adds a node first, which the vCPU cap cannot hold."
  }

  assert {
    condition     = google_dataproc_cluster.analytics.cluster_config[0].worker_config[0].num_instances == 0
    error_message = "Dataproc should be a single node."
  }
}

run "a_plan_above_the_cap_is_refused" {
  command = plan

  variables {
    gke_node_count = 4
  }

  expect_failures = [google_container_node_pool.workers]
}

run "nothing_bills_more_than_it_has_to" {
  command = plan

  assert {
    condition     = google_sql_database_instance.this.settings[0].edition == "ENTERPRISE"
    error_message = "PostgreSQL 16 otherwise defaults to Enterprise Plus, which costs several times more."
  }

  assert {
    condition     = google_sql_database_instance.this.settings[0].availability_type == "ZONAL"
    error_message = "A regional instance doubles the database bill."
  }

  assert {
    condition     = google_container_cluster.this.location == "asia-south1-a"
    error_message = "Only a zonal cluster's management fee is covered by the GKE free tier."
  }

  assert {
    condition     = google_container_cluster.this.monitoring_config[0].managed_prometheus[0].enabled == false
    error_message = "Managed Prometheus bills per sample; our own Prometheus does the job."
  }

  assert {
    condition     = tonumber(google_managed_kafka_cluster.this.capacity_config[0].vcpu_count) == 3
    error_message = "Managed Kafka should stay at its minimum size."
  }

  assert {
    # The deletion time comes from timestamp(), which is only known at apply.
    condition     = length(google_dataproc_cluster.analytics.cluster_config[0].lifecycle_config) == 1 && var.dataproc_max_hours <= 24
    error_message = "Dataproc must delete itself if make down is forgotten."
  }
}

run "dispatch_is_event_driven_with_retries_and_a_dead_letter_topic" {
  command = plan

  assert {
    condition     = google_storage_notification.manifests.object_name_prefix == "manifests/"
    error_message = "Only manifests should trigger Dispatch, not the labels it writes."
  }

  assert {
    condition     = google_pubsub_subscription.dispatch.dead_letter_policy[0].max_delivery_attempts == 5
    error_message = "A manifest that keeps failing must end up in the dead-letter topic."
  }

  assert {
    condition     = google_cloudfunctions2_function.dispatch.service_config[0].direct_vpc_egress == "VPC_EGRESS_ALL_TRAFFIC"
    error_message = "The function must join the VPC to reach Kafka's private addresses."
  }

  assert {
    condition     = google_cloudfunctions2_function.dispatch.service_config[0].environment_variables["KAFKA_AUTH"] == "google"
    error_message = "Managed Kafka accepts only Google-authenticated clients."
  }
}

run "the_flink_job_is_started_on_the_cluster" {
  command = plan

  assert {
    condition     = google_dataproc_job.start_flink.placement[0].cluster_name == "warehouse-analytics"
    error_message = "The job must run on the analytics cluster."
  }

  assert {
    condition     = strcontains(google_storage_bucket_object.flink_env.content, "KAFKA_BOOTSTRAP=bootstrap.warehouse")
    error_message = "flink.env must carry the Kafka bootstrap address."
  }

  assert {
    condition     = contains(google_dataproc_cluster.analytics.cluster_config[0].software_config[0].optional_components, "FLINK")
    error_message = "The Flink component must be installed."
  }
}

run "pods_get_google_identities_without_keys" {
  command = plan

  assert {
    condition     = kubernetes_service_account_v1.workload["packing"].metadata[0].annotations["iam.gke.io/gcp-service-account"] != ""
    error_message = "Packing needs a Workload Identity service account to write manifests."
  }

  assert {
    condition     = kubernetes_config_map_v1.infra_outputs.data["KAFKA_AUTH"] == "google"
    error_message = "Pods must use Google sign-in for Kafka."
  }

  assert {
    condition     = google_project_iam_member.workload["grafana roles/logging.viewer"].role == "roles/logging.viewer"
    error_message = "Grafana needs to read Cloud Logging for the central logs dashboard."
  }
}
