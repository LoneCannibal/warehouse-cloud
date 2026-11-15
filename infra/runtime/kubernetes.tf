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
    KAFKA_BOOTSTRAP    = local.kafka_bootstrap
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

# Grafana is reachable from the internet, so it gets a password chosen here rather than
# the chart's own. The chart makes a new random one each time it renders, which under
# Argo CD means on every sync.
resource "random_password" "grafana" {
  length  = 20
  special = false
}

locals {
  grafana_admin_password = var.grafana_admin_password != "" ? var.grafana_admin_password : random_password.grafana.result
}

resource "kubernetes_secret_v1" "grafana_admin" {
  metadata {
    name      = "grafana-admin"
    namespace = kubernetes_namespace_v1.this["monitoring"].metadata[0].name
  }

  data = {
    admin-user     = "admin"
    admin-password = local.grafana_admin_password
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
      "iam.gke.io/gcp-service-account" = data.google_service_account.workload[each.key].email
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
      cm = {
        # Argo CD does not assess an Application's health by default, so the sync
        # waves in the root app would set only the order the children are created in,
        # never wait for one to be ready. This is the check Argo CD's documentation
        # gives for app-of-apps. It makes the operator wait for its CRDs.
        "resource.customizations.health.argoproj.io_Application" = <<-EOT
          hs = {}
          hs.status = "Progressing"
          hs.message = ""
          if obj.status ~= nil and obj.status.health ~= nil then
            hs.status = obj.status.health.status
            if obj.status.health.message ~= nil then
              hs.message = obj.status.health.message
            end
          end
          return hs
        EOT
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
    kubernetes_secret_v1.grafana_admin,
    kubernetes_service_account_v1.workload,
  ]
}
