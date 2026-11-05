# Dispatch, the serverless service: Cloud Storage -> Pub/Sub -> push -> Cloud Run function.
#
# The push subscription is created here rather than left to an event trigger, because
# only an explicit subscription can carry a dead-letter policy.

locals {
  dispatch_dir = "${path.module}/../../services/dispatch-function"
  pubsub_agent = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

data "archive_file" "dispatch" {
  type        = "zip"
  output_path = "${path.module}/.build/dispatch.zip"

  source {
    content  = file("${local.dispatch_dir}/main.py")
    filename = "main.py"
  }

  source {
    content  = file("${local.dispatch_dir}/kafka_auth.py")
    filename = "kafka_auth.py"
  }

  source {
    content  = file("${local.dispatch_dir}/requirements.txt")
    filename = "requirements.txt"
  }
}

# The hash in the name makes a code change redeploy the function on the next apply.
resource "google_storage_bucket_object" "dispatch_source" {
  name   = "functions/dispatch-${data.archive_file.dispatch.output_md5}.zip"
  bucket = local.artifacts_bucket
  source = data.archive_file.dispatch.output_path
}

resource "google_cloudfunctions2_function" "dispatch" {
  name     = "warehouse-dispatch"
  location = var.region

  build_config {
    runtime         = "python312"
    entry_point     = "dispatch"
    service_account = local.function_builder

    source {
      storage_source {
        bucket = local.artifacts_bucket
        object = google_storage_bucket_object.dispatch_source.name
      }
    }
  }

  service_config {
    min_instance_count    = 0
    max_instance_count    = 3
    available_memory      = "512M"
    timeout_seconds       = 60
    service_account_email = google_service_account.dispatch.email
    # Pub/Sub push must reach the function. IAM still rejects any caller other than
    # the push account, because nobody else holds run.invoker.
    ingress_settings = "ALLOW_ALL"

    environment_variables = {
      GCP_PROJECT          = var.project_id
      FIRESTORE_DATABASE   = local.firestore_database
      SHIPMENTS_COLLECTION = "shipments"
      KAFKA_BOOTSTRAP      = google_managed_kafka_cluster.this.bootstrap_address
      KAFKA_AUTH           = "google"
    }

    # Kafka has only private addresses, so the function joins the VPC. Direct VPC
    # egress needs no connector VMs, which would count against the vCPU quota.
    direct_vpc_network_interface {
      network    = google_compute_network.this.name
      subnetwork = google_compute_subnetwork.serverless.name
    }
    direct_vpc_egress = "VPC_EGRESS_ALL_TRAFFIC"
  }

  depends_on = [google_project_iam_member.dispatch, google_storage_bucket_iam_member.dispatch_reads_manifests_and_writes_labels]
}

resource "google_cloud_run_service_iam_member" "push_invokes_dispatch" {
  location = var.region
  service  = google_cloudfunctions2_function.dispatch.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.dispatch_push.email}"
}

resource "google_pubsub_subscription" "dispatch" {
  name                 = "warehouse-dispatch"
  topic                = google_pubsub_topic.manifests.id
  ack_deadline_seconds = 60

  push_config {
    push_endpoint = google_cloudfunctions2_function.dispatch.service_config[0].uri

    oidc_token {
      service_account_email = google_service_account.dispatch_push.email
    }
  }

  retry_policy {
    minimum_backoff = "5s"
    maximum_backoff = "300s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dispatch_dead_letter.id
    max_delivery_attempts = 5
  }

  expiration_policy {
    ttl = ""
  }

  depends_on = [google_cloud_run_service_iam_member.push_invokes_dispatch]
}

# Pub/Sub's own service agent forwards dead letters and signs push requests.
resource "google_pubsub_topic_iam_member" "dead_letter_publisher" {
  topic  = google_pubsub_topic.dispatch_dead_letter.name
  role   = "roles/pubsub.publisher"
  member = local.pubsub_agent
}

resource "google_pubsub_subscription_iam_member" "dead_letter_subscriber" {
  subscription = google_pubsub_subscription.dispatch.name
  role         = "roles/pubsub.subscriber"
  member       = local.pubsub_agent
}

resource "google_service_account_iam_member" "pubsub_signs_push_requests" {
  service_account_id = google_service_account.dispatch_push.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = local.pubsub_agent
}
