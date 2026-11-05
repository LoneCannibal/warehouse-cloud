# Every service account infra/runtime uses, with its project roles.
#
# They live here rather than in infra/runtime because Google limits how many service
# accounts a project may create per minute. Creating eleven on every `make up` hit that
# limit and stopped the apply. Created once here, they cost nothing, and `make up`
# only looks them up. If `make bootstrap` itself stops on that limit, wait a minute
# and run it again.

locals {
  # Pods that call a Google API, each as its own account, through Workload Identity.
  workloads = {
    "order-intake"    = ["roles/managedkafka.client"]
    "job-scheduler"   = ["roles/managedkafka.client"]
    "picking"         = ["roles/managedkafka.client"]
    "packing"         = ["roles/managedkafka.client"]
    "worker-registry" = ["roles/datastore.user"]
    "analytics-api"   = ["roles/managedkafka.client", "roles/datastore.user"]
    "grafana"         = ["roles/monitoring.viewer", "roles/logging.viewer"]
  }

  runtime_accounts = merge(
    {
      "gke-nodes" = {
        display = "GKE nodes"
        roles = [
          "roles/artifactregistry.reader",
          "roles/logging.logWriter",
          "roles/monitoring.metricWriter",
          "roles/monitoring.viewer",
          "roles/stackdriver.resourceMetadata.writer",
        ]
      }
      "dataproc-flink"    = { display = "Dataproc node running Flink", roles = ["roles/dataproc.worker", "roles/managedkafka.client"] }
      "dispatch-function" = { display = "Dispatch function", roles = ["roles/managedkafka.client", "roles/datastore.user"] }
      # Pub/Sub signs each push request as this account, and only it may invoke the function.
      "dispatch-push" = { display = "Pub/Sub push to the Dispatch function", roles = [] }
    },
    { for name, roles in local.workloads : "wh-${name}" => { display = "${name} on GKE", roles = roles } },
  )

  runtime_account_roles = {
    for pair in flatten([
      for id, account in local.runtime_accounts : [for role in account.roles : { id = id, role = role }]
    ]) : "${pair.id} ${pair.role}" => pair
  }
}

resource "google_service_account" "runtime" {
  for_each = local.runtime_accounts

  account_id   = each.key
  display_name = each.value.display

  depends_on = [google_project_service.this]
}

resource "google_project_iam_member" "runtime" {
  for_each = local.runtime_account_roles

  project = var.project_id
  role    = each.value.role
  member  = "serviceAccount:${google_service_account.runtime[each.value.id].email}"
}

resource "google_storage_bucket_iam_member" "dataproc_reads_the_jar" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.runtime["dataproc-flink"].email}"
}
