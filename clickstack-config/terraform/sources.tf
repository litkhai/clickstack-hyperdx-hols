# Log, trace and metric sources over the otel_* tables the collector
# creates (see _base/README.md -- sql/ only creates the database, not the
# tables). Byte-identical between oss and cloud: both take local.connection_id
# from connection.tf.

resource "clickhouse_clickstack_source" "logs" {
  name          = "Logs"
  kind          = "log"
  connection_id = local.connection_id
  team          = var.team != "" ? var.team : null

  from = {
    database_name = var.clickhouse_database
    table_name    = "otel_logs"
  }

  timestamp_value_expression      = "Timestamp"
  default_table_select_expression = "Timestamp, ServiceName, SeverityText, Body"

  service_name_expression        = "ServiceName"
  severity_text_expression       = "SeverityText"
  body_expression                = "Body"
  resource_attributes_expression = "ResourceAttributes"
  event_attributes_expression    = "LogAttributes"
}

resource "clickhouse_clickstack_source" "traces" {
  name          = "Traces"
  kind          = "trace"
  connection_id = local.connection_id
  team          = var.team != "" ? var.team : null

  from = {
    database_name = var.clickhouse_database
    table_name    = "otel_traces"
  }

  timestamp_value_expression      = "Timestamp"
  default_table_select_expression = "Timestamp, ServiceName, SpanName, Duration"

  duration_expression       = "Duration"
  duration_precision        = 9 # nanoseconds, as ClickStack's own collector writes it
  trace_id_expression       = "TraceId"
  span_id_expression        = "SpanId"
  parent_span_id_expression = "ParentSpanId"
  span_name_expression      = "SpanName"
  span_kind_expression      = "SpanKind"

  # Correlates trace-to-log navigation in the UI.
  log_source_id = clickhouse_clickstack_source.logs.id
}

resource "clickhouse_clickstack_source" "metrics" {
  name          = "Metrics"
  kind          = "metric"
  connection_id = local.connection_id
  team          = var.team != "" ? var.team : null

  # Metric sources locate their tables via metric_tables, not from.table_name.
  from = {
    database_name = var.clickhouse_database
  }

  timestamp_value_expression     = "TimeUnix"
  resource_attributes_expression = "ResourceAttributes"

  metric_tables = {
    gauge     = "otel_metrics_gauge"
    sum       = "otel_metrics_sum"
    histogram = "otel_metrics_histogram"
  }
}
