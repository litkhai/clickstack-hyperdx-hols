terraform {
  required_version = ">= 1.5.0"

  required_providers {
    # hashicorp/aws 6.68.0 is the latest release on the registry (read 2026-10-14);
    # resource arguments below were read in that tag's website/docs.
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    # ClickHouse/clickhouse 3.38.0 is the latest release (read 2026-10-14). Since 3.2.0 the endpoint
    # service id comes from clickhouse_service.<x>.private_endpoint_config.endpoint_service_id and the
    # endpoint is attached with clickhouse_service_private_endpoints_attachment
    # (provider README, "Upgrading to version >= 3.2.0", tag v3.38.0).
    clickhouse = {
      source  = "ClickHouse/clickhouse"
      version = ">= 3.38.0, < 4.0.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "aws" {
  # Credentials and profile come from the environment (AWS_PROFILE); nothing is set here.
  region = var.region
}

provider "clickhouse" {
  # API key and secret come from CLICKHOUSE_CLOUD_API_KEY and CLICKHOUSE_CLOUD_API_SECRET
  # (provider docs, tag v3.38.0: token_key / token_secret).
  organization_id = var.clickhouse_org_id
}
