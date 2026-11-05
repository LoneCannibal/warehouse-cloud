variable "project_id" {
  description = "Google Cloud project. Must match infra/bootstrap."
  type        = string
}

variable "region" {
  description = "Region. Must match infra/bootstrap."
  type        = string
  default     = "asia-south1"
}

variable "zone" {
  description = "Zone for the GKE cluster, its nodes, Dataproc and Cloud SQL."
  type        = string
  default     = "asia-south1-a"
}

variable "max_vcpus" {
  description = <<-EOT
    Cap on the Compute Engine vCPUs this stack runs at once. It keeps the hourly cost
    predictable; the project's own quota is higher. scripts/preflight.sh checks the quota.
  EOT
  type        = number
  default     = 8
}

variable "gke_machine_type" {
  # The project has no E2 quota in asia-south1, so the stack uses N2.
  description = "Machine type for the GKE nodes."
  type        = string
  default     = "n2-standard-2"
}

variable "gke_node_count" {
  description = "GKE nodes. Three n2-standard-2 nodes leave 2 of the 8 capped vCPUs for Dataproc."
  type        = number
  default     = 3
}

variable "dataproc_machine_type" {
  description = "Machine type for the single Dataproc node that runs Flink."
  type        = string
  default     = "n2-highmem-2"
}

variable "dataproc_max_hours" {
  description = "Dataproc deletes its cluster this many hours after creation, in case `make down` is forgotten."
  type        = number
  default     = 12
}

variable "kafka_vcpus" {
  description = "Managed Kafka capacity. Three is the service minimum."
  type        = number
  default     = 3
}

variable "kafka_memory_gib" {
  description = "Managed Kafka memory, between 1 and 8 GiB per vCPU."
  type        = number
  default     = 3
}

variable "kafka_replication_factor" {
  description = "Replication factor for each topic."
  type        = number
  default     = 3
}

variable "cloudsql_tier" {
  description = "Cloud SQL machine. db-g1-small is a shared-core Enterprise edition machine."
  type        = string
  default     = "db-g1-small"
}

variable "flink_jar_object" {
  description = "Object name of the Flink job JAR in the artifacts bucket. `make flink-jar` uploads it."
  type        = string
  default     = "flink/analytics-flink.jar"
}

variable "git_repo_url" {
  description = "Repository Argo CD tracks, for example https://github.com/owner/warehouse-cloud.git."
  type        = string
}

variable "git_revision" {
  description = "Branch Argo CD tracks."
  type        = string
  default     = "main"
}

variable "git_ssh_private_key" {
  description = "Read-only deploy key for a private repository. Leave empty for a public one."
  type        = string
  default     = ""
  sensitive   = true
}
