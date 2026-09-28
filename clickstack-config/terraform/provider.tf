# ClickStack resources (clickhouse_clickstack_*) are beta as of provider
# v3.25 -- pin a version constraint so a provider upgrade cannot change their
# behaviour out from under this configuration. See ../README.md for how
# "beta" shows up in practice (a warning on apply, not on plan).
terraform {
  required_version = ">= 1.11" # webhooks.tf's write-only headers need it

  required_providers {
    clickhouse = {
      source  = "ClickHouse/clickhouse"
      version = ">= 3.25.0, < 4.0.0"
    }
  }
}

# Deliberately empty. Every clickhouse_clickstack_* credential is optional on
# this block and env-settable, and which ones are set selects the mode:
#
#   self-hosted (oss)  CLICKSTACK_ENDPOINT + CLICKSTACK_API_KEY
#   Cloud              CLICKSTACK_SERVICE_ID + CLICKHOUSE_ORG_ID +
#                       CLICKHOUSE_CLOUD_API_KEY + CLICKHOUSE_CLOUD_API_SECRET
#
# One root module for both; see envs/oss.env.example and envs/cloud.env.example.
# CLICKHOUSE_SUPPRESS_BETA_WARNINGS=true in either env silences the beta
# notice this provider emits on every create/update/import of a
# clickhouse_clickstack_* resource.
provider "clickhouse" {}
