variable "project_id" {
  description = "Google Cloud project that holds everything."
  type        = string
}

variable "region" {
  description = "Region for everything in this project."
  type        = string
  default     = "asia-south1"
}

variable "billing_account" {
  description = "Billing account id, such as 0X0X0X-0X0X0X-0X0X0X. Run `gcloud billing accounts list`."
  type        = string
}

variable "github_repo" {
  description = "Repository allowed to deploy, as owner/name."
  type        = string
}

variable "team_members" {
  description = "Teammates' Google account emails. They can see and operate the project, but have no billing access."
  type        = list(string)
  default     = []
}

variable "team_roles" {
  description = "Project roles each teammate gets. None of them can touch billing."
  type        = list(string)
  default = [
    "roles/viewer",
    "roles/container.developer",
    "roles/dataproc.editor",
    "roles/logging.viewer",
    "roles/monitoring.viewer",
  ]
}

variable "budget_amount" {
  description = <<-EOT
    Spending at which the kill switch turns billing off, in the billing account's
    currency, counted across every project on the account from budget_start_date. Set
    it well below the credit left: cost reports lag usage by hours, so spending
    continues for a while after the switch fires.
  EOT
  type        = number

  validation {
    condition     = var.budget_amount > 0
    error_message = "budget_amount must be positive."
  }
}

variable "budget_currency" {
  description = "Currency of the billing account, shown on the console's Billing overview. Indian accounts are often INR."
  type        = string
  default     = "USD"
}

variable "budget_start_date" {
  description = "Day to start counting from, as YYYY-MM-DD. Use the day you first apply."
  type        = string

  validation {
    condition     = can(regex("^\\d{4}-\\d{2}-\\d{2}$", var.budget_start_date))
    error_message = "budget_start_date must look like 2026-11-05."
  }
}

variable "enable_kill_switch" {
  description = "Unlink billing from the project when spending reaches budget_amount. This stops every resource in it."
  type        = bool
  default     = true
}
