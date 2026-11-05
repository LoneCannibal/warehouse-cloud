# One Google service account per workload that calls a Google API, each with only the
# roles it needs. Pods reach theirs through Workload Identity: the Kubernetes service
# account in kubernetes.tf names it, so no key file exists anywhere.

locals {
  workloads = {
    "order-intake"    = { namespace = "warehouse", roles = ["roles/managedkafka.client"] }
    "job-scheduler"   = { namespace = "warehouse", roles = ["roles/managedkafka.client"] }
    "picking"         = { namespace = "warehouse", roles = ["roles/managedkafka.client"] }
    "packing"         = { namespace = "warehouse", roles = ["roles/managedkafka.client"] }
    "worker-registry" = { namespace = "warehouse", roles = ["roles/datastore.user"] }
    "analytics-api"   = { namespace = "warehouse", roles = ["roles/managedkafka.client", "roles/datastore.user"] }
    "grafana"         = { namespace = "monitoring", roles = ["roles/monitoring.viewer", "roles/logging.viewer"] }
  }

  workload_roles = {
    for pair in flatten([
      for name, workload in local.workloads : [for role in workload.roles : { name = name, role = role }]
    ]) : "${pair.name} ${pair.role}" => pair
  }
}

resource "google_service_account" "workload" {
  for_each = local.workloads

  account_id   = "wh-${each.key}"
  display_name = "${each.key} on GKE"
}

resource "google_project_iam_member" "workload" {
  for_each = local.workload_roles

  project = var.project_id
  role    = each.value.role
  member  = "serviceAccount:${google_service_account.workload[each.value.name].email}"
}

resource "google_service_account_iam_member" "workload_identity" {
  for_each = local.workloads

  service_account_id = google_service_account.workload[each.key].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${each.value.namespace}/${each.key}]"

  # The identity pool only exists once the cluster does.
  depends_on = [google_container_cluster.this]
}

# Packing writes manifests. A retried task rewrites the same object, which needs
# delete as well as create, so objectUser rather than objectCreator.
resource "google_storage_bucket_iam_member" "packing_writes_manifests" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.workload["packing"].email}"
}

# ---- Dataproc -------------------------------------------------------------------------

resource "google_service_account" "dataproc" {
  account_id   = "dataproc-flink"
  display_name = "Dataproc node running Flink"
}

resource "google_project_iam_member" "dataproc" {
  for_each = toset(["roles/dataproc.worker", "roles/managedkafka.client"])

  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.dataproc.email}"
}

resource "google_storage_bucket_iam_member" "dataproc_reads_the_jar" {
  bucket = local.artifacts_bucket
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.dataproc.email}"
}

# Staging files, the submit script and the Flink checkpoints.
resource "google_storage_bucket_iam_member" "dataproc_bucket" {
  bucket = google_storage_bucket.dataproc.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.dataproc.email}"
}

# ---- Dispatch function ----------------------------------------------------------------

resource "google_service_account" "dispatch" {
  account_id   = "dispatch-function"
  display_name = "Dispatch function"
}

resource "google_project_iam_member" "dispatch" {
  for_each = toset(["roles/managedkafka.client", "roles/datastore.user"])

  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.dispatch.email}"
}

resource "google_storage_bucket_iam_member" "dispatch_reads_manifests_and_writes_labels" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.dispatch.email}"
}

# Pub/Sub signs each push request as this account, and only it may invoke the function.
resource "google_service_account" "dispatch_push" {
  account_id   = "dispatch-push"
  display_name = "Pub/Sub push to the Dispatch function"
}
