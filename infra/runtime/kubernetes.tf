# The hand-off from Terraform to GitOps.
#
# Terraform creates the namespaces, the values that differ per environment, the
# Workload Identity service accounts, and Argo CD itself. Argo CD deploys everything
# else from gitops/. So manifests in Git hold no project ids or endpoints, and no
# secret ever passes through Git.

locals {
  namespaces = toset(["warehouse", "monitoring", "argocd"])
}

resource "kubernetes_namespace_v1" "this" {
  for_each = local.namespaces

  metadata {
    name = each.key
  }

  depends_on = [google_container_node_pool.workers]
}

resource "kubernetes_config_map_v1" "infra_outputs" {
  metadata {
    name      = "infra-outputs"
    namespace = kubernetes_namespace_v1.this["warehouse"].metadata[0].name
  }

  data = {
    GCP_PROJECT        = var.project_id
    KAFKA_BOOTSTRAP    = google_managed_kafka_cluster.this.bootstrap_address
    KAFKA_AUTH         = "google"
    DATA_BUCKET        = google_storage_bucket.data.name
    FIRESTORE_DATABASE = local.firestore_database
  }
}

resource "kubernetes_secret_v1" "database" {
  metadata {
    name      = "warehouse-database"
    namespace = kubernetes_namespace_v1.this["warehouse"].metadata[0].name
  }

  data = {
    DB_DSN = "postgresql://${google_sql_user.warehouse.name}:${random_password.database.result}@${google_sql_database_instance.this.private_ip_address}:5432/${google_sql_database.warehouse.name}?sslmode=require"
  }
}

# Each names its Google service account from iam.tf, which is how the pod gets that
# identity through Workload Identity.
resource "kubernetes_service_account_v1" "workload" {
  for_each = local.workloads

  metadata {
    name      = each.key
    namespace = kubernetes_namespace_v1.this[each.value.namespace].metadata[0].name
    annotations = {
      "iam.gke.io/gcp-service-account" = google_service_account.workload[each.key].email
    }
  }
}

resource "helm_release" "argocd" {
  name       = "argocd"
  namespace  = kubernetes_namespace_v1.this["argocd"].metadata[0].name
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argo-cd"
  version    = "10.11.6"

  values = [yamlencode({
    dex           = { enabled = false }
    notifications = { enabled = false }
    configs = {
      params = {
        "server.insecure" = true
      }
    }
    # Small requests, because the whole system shares six vCPUs.
    controller     = { resources = { requests = { cpu = "100m", memory = "256Mi" } } }
    repoServer     = { resources = { requests = { cpu = "50m", memory = "128Mi" } } }
    server         = { resources = { requests = { cpu = "50m", memory = "128Mi" } } }
    redis          = { resources = { requests = { cpu = "25m", memory = "64Mi" } } }
    applicationSet = { resources = { requests = { cpu = "25m", memory = "64Mi" } } }
  })]
}

# Only needed for a private repository.
resource "kubernetes_secret_v1" "argocd_repository" {
  count = nonsensitive(var.git_ssh_private_key != "") ? 1 : 0

  metadata {
    name      = "warehouse-repository"
    namespace = kubernetes_namespace_v1.this["argocd"].metadata[0].name
    labels = {
      "argocd.argoproj.io/secret-type" = "repository"
    }
  }

  data = {
    type          = "git"
    url           = var.git_repo_url
    sshPrivateKey = var.git_ssh_private_key
  }
}

# The app-of-apps root. Everything Argo CD deploys descends from this one Application.
resource "helm_release" "root_application" {
  name       = "warehouse-root"
  namespace  = kubernetes_namespace_v1.this["argocd"].metadata[0].name
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argocd-apps"
  version    = "2.0.7"

  values = [yamlencode({
    applications = {
      root = {
        namespace  = "argocd"
        project    = "default"
        finalizers = ["resources-finalizer.argocd.argoproj.io"]
        source = {
          repoURL        = var.git_repo_url
          targetRevision = var.git_revision
          path           = "gitops/apps"
          helm = {
            parameters = [
              { name = "repoURL", value = var.git_repo_url },
              { name = "targetRevision", value = var.git_revision },
            ]
          }
        }
        destination = {
          server    = "https://kubernetes.default.svc"
          namespace = "argocd"
        }
        syncPolicy = {
          automated = {
            prune    = true
            selfHeal = true
          }
        }
      }
    }
  })]

  depends_on = [
    helm_release.argocd,
    kubernetes_secret_v1.argocd_repository,
    kubernetes_config_map_v1.infra_outputs,
    kubernetes_secret_v1.database,
    kubernetes_service_account_v1.workload,
  ]
}
