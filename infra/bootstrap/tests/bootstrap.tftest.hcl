# Plans the stack against a mocked provider, so it needs no Google Cloud project, no
# credentials, and creates nothing. These checks guard the settings that keep spending
# inside the credit.
#
#   terraform -chdir=infra/bootstrap init -backend=false
#   terraform -chdir=infra/bootstrap test

mock_provider "google" {
  mock_resource "google_service_account" {
    override_during = plan
    defaults = {
      email = "mock-account@group18-warehouse.iam.gserviceaccount.com"
      id    = "projects/group18-warehouse/serviceAccounts/mock-account@group18-warehouse.iam.gserviceaccount.com"
      name  = "projects/group18-warehouse/serviceAccounts/mock-account@group18-warehouse.iam.gserviceaccount.com"
    }
  }

  mock_resource "google_pubsub_topic" {
    override_during = plan
    defaults = {
      id = "projects/group18-warehouse/topics/budget-alerts"
    }
  }
}

variables {
  project_id        = "group18-warehouse"
  billing_account   = "000000-000000-000000"
  github_repo       = "group18/warehouse-cloud"
  team_members      = ["aman@example.com", "pranjal@example.com"]
  budget_amount     = 250
  budget_start_date = "2026-11-05"
}

run "budget_counts_spend_before_credits_across_the_whole_billing_account" {
  command = plan

  assert {
    condition     = length(coalesce(google_billing_budget.credits.budget_filter[0].projects, [])) == 0
    error_message = "Every project on the billing account draws from the same credit, so the budget must count them all."
  }

  assert {
    condition     = google_billing_budget.credits.budget_filter[0].credit_types_treatment == "EXCLUDE_ALL_CREDITS"
    error_message = "With credits included, cost reads as zero until the credit is gone, and no alert fires in time."
  }

  assert {
    condition     = google_billing_budget.credits.budget_filter[0].custom_period[0].start_date[0].day == 5
    error_message = "A monthly budget would reset each month while the credit keeps draining."
  }

  assert {
    condition     = google_billing_budget.credits.amount[0].specified_amount[0].units == "250"
    error_message = "The budget amount did not come from budget_amount."
  }

  assert {
    condition     = length(google_billing_budget.credits.threshold_rules) == 4
    error_message = "Expected alerts at 50%, 75%, 90% and 100%."
  }
}

run "kill_switch_listens_to_the_budget" {
  command = plan

  assert {
    condition     = google_cloudfunctions2_function.kill_switch[0].event_trigger[0].pubsub_topic == google_pubsub_topic.budget.id
    error_message = "The kill switch must be triggered by the budget's topic."
  }

  assert {
    condition     = google_billing_budget.credits.all_updates_rule[0].pubsub_topic == google_pubsub_topic.budget.id
    error_message = "The budget must publish to the kill switch's topic."
  }

  assert {
    condition     = contains(keys(google_project_iam_member.kill_switch), "roles/billing.projectManager")
    error_message = "Without Project Billing Manager the kill switch cannot unlink billing."
  }
}

run "kill_switch_can_be_turned_off" {
  command = plan

  variables {
    enable_kill_switch = false
  }

  assert {
    condition     = length(google_cloudfunctions2_function.kill_switch) == 0
    error_message = "enable_kill_switch = false must remove the function."
  }
}

run "teammates_get_no_billing_role" {
  command = plan

  assert {
    condition     = length(google_project_iam_member.team) == 10
    error_message = "Each teammate should get each of the five team roles."
  }

  assert {
    condition     = alltrue([for binding in google_project_iam_member.team : !strcontains(binding.role, "billing")])
    error_message = "A billing role would let a teammate change how the project is paid for."
  }
}

run "only_this_repository_can_use_the_ci_identity" {
  command = plan

  assert {
    condition     = strcontains(google_iam_workload_identity_pool_provider.github.attribute_condition, "group18/warehouse-cloud")
    error_message = "Without the condition any GitHub repository could deploy."
  }
}

run "a_malformed_start_date_is_rejected" {
  command = plan

  variables {
    budget_start_date = "5 Nov 2026"
  }

  expect_failures = [var.budget_start_date]
}
