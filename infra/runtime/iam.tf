# The Google service accounts, and their project roles, live in infra/bootstrap
# (identities.tf), because Google limits how many accounts a project may create per
# minute. This stack only looks them up, then adds the bindings that depend on
# resources it creates: Workload Identity, buckets, and the Dispatch function.
#
# Pods reach their account through Workload Identity: the Kubernetes service account
# in kubernetes.tf names it, so no key file exists anywhere.

locals {
  workloads = {
    "order-intake"    = { namespace = "warehouse" }
    "job-scheduler"   = { namespace = "warehouse" }
    "picking"         = { namespace = "warehouse" }
    "packing"         = { namespace = "warehouse" }
    "worker-registry" = { namespace = "warehouse" }
    "analytics-api"   = { namespace = "warehouse" }
    "grafana"         = { namespace = "monitoring" }
  }
}

data "google_service_account" "workload" {
  for_each = local.workloads

  account_id = "wh-${each.key}"
}

data "google_service_account" "gke_nodes" {
  account_id = "gke-nodes"
}

data "google_service_account" "dataproc" {
  account_id = "dataproc-flink"
}

data "google_service_account" "dispatch" {
  account_id = "dispatch-function"
}

data "google_service_account" "dispatch_push" {
  account_id = "dispatch-push"
}

resource "google_service_account_iam_member" "workload_identity" {
  for_each = local.workloads

  service_account_id = data.google_service_account.workload[each.key].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${each.value.namespace}/${each.key}]"

  # The identity pool only exists once a cluster does.
  depends_on = [google_container_cluster.this]
}

# Packing writes manifests. A retried task rewrites the same object, which needs
# delete as well as create, so objectUser rather than objectCreator.
resource "google_storage_bucket_iam_member" "packing_writes_manifests" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${data.google_service_account.workload["packing"].email}"
}

# Staging files, the submit script and the Flink checkpoints.
resource "google_storage_bucket_iam_member" "dataproc_bucket" {
  bucket = google_storage_bucket.dataproc.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${data.google_service_account.dataproc.email}"
}

resource "google_storage_bucket_iam_member" "dispatch_reads_manifests_and_writes_labels" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${data.google_service_account.dispatch.email}"
}
