# Keeps spending inside the credit on the billing account.
#
# The billing account is a paid account, so once the credit is used up Google charges
# the card. Two layers stop that from happening:
#
# 1. This budget emails the billing administrators at 50%, 75%, 90% and 100%.
# 2. The kill switch unlinks billing from this project when spending reaches the
#    budget, which stops everything in it.
#
# Budgets only alert; they never stop anything themselves. Cost data also lags usage
# by hours, so budget_amount must sit well below the credit actually left.

resource "google_pubsub_topic" "budget" {
  name = "budget-alerts"

  depends_on = [google_project_service.this]
}

# Cloud Billing publishes budget updates as this Google-owned account.
resource "google_pubsub_topic_iam_member" "budget_publisher" {
  topic  = google_pubsub_topic.budget.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:billing-budget-alert@system.gserviceaccount.com"
}

resource "google_billing_budget" "credits" {
  billing_account = var.billing_account
  display_name    = "warehouse credit guard"

  budget_filter {
    # No project filter: the credit belongs to the billing account, and every project
    # on it draws from the same credit, so the budget must count them all.
    #
    # Gross cost, before the credit is subtracted. With credits included the cost
    # would read as zero until the credit ran out, and nothing would fire.
    credit_types_treatment = "EXCLUDE_ALL_CREDITS"

    # One running total for the whole project, rather than a total that resets
    # every month while the credit keeps draining.
    custom_period {
      start_date {
        year  = tonumber(split("-", var.budget_start_date)[0])
        month = tonumber(split("-", var.budget_start_date)[1])
        day   = tonumber(split("-", var.budget_start_date)[2])
      }
    }
  }

  amount {
    specified_amount {
      currency_code = var.budget_currency
      units         = tostring(floor(var.budget_amount))
    }
  }

  dynamic "threshold_rules" {
    for_each = [0.5, 0.75, 0.9, 1.0]

    content {
      threshold_percent = threshold_rules.value
    }
  }

  all_updates_rule {
    pubsub_topic   = google_pubsub_topic.budget.id
    schema_version = "1.0"
  }

  depends_on = [google_pubsub_topic_iam_member.budget_publisher]
}

# ---- Kill switch ----------------------------------------------------------------------

data "archive_file" "kill_switch" {
  type        = "zip"
  source_dir  = "${path.module}/killswitch"
  output_path = "${path.module}/.build/kill-switch.zip"
  excludes    = ["__pycache__"]
}

resource "google_storage_bucket_object" "kill_switch" {
  name   = "functions/kill-switch-${data.archive_file.kill_switch.output_md5}.zip"
  bucket = google_storage_bucket.artifacts.name
  source = data.archive_file.kill_switch.output_path
}

resource "google_service_account" "kill_switch" {
  account_id   = "billing-kill-switch"
  display_name = "Unlinks billing when the budget is reached"

  depends_on = [google_project_service.this]
}

# Project Billing Manager can unlink this project's billing, and nothing else. It
# grants no access to the billing account itself.
resource "google_project_iam_member" "kill_switch" {
  for_each = toset([
    "roles/billing.projectManager",
    "roles/eventarc.eventReceiver",
    "roles/run.invoker",
  ])

  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.kill_switch.email}"
}

resource "google_cloudfunctions2_function" "kill_switch" {
  count = var.enable_kill_switch ? 1 : 0

  name     = "billing-kill-switch"
  location = var.region

  build_config {
    runtime         = "python312"
    entry_point     = "stop_billing"
    service_account = google_service_account.function_builder.id

    source {
      storage_source {
        bucket = google_storage_bucket.artifacts.name
        object = google_storage_bucket_object.kill_switch.name
      }
    }
  }

  service_config {
    max_instance_count    = 1
    available_memory      = "256M"
    timeout_seconds       = 60
    ingress_settings      = "ALLOW_INTERNAL_ONLY"
    service_account_email = google_service_account.kill_switch.email

    environment_variables = {
      PROJECT_ID = var.project_id
    }
  }

  event_trigger {
    trigger_region        = var.region
    event_type            = "google.cloud.pubsub.topic.v1.messagePublished"
    pubsub_topic          = google_pubsub_topic.budget.id
    retry_policy          = "RETRY_POLICY_RETRY"
    service_account_email = google_service_account.kill_switch.email
  }

  depends_on = [google_project_iam_member.function_builder, google_project_iam_member.kill_switch]
}
