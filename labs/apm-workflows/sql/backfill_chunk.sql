-- One backfill chunk: the generator over [chunk_start, chunk_start + chunk_minutes), faults off
-- (no fault_events exist for this period), every resource tagged apm.backfill = true; then the
-- derived logs and metrics over the same window. Parameters: chunk_start, chunk_minutes.
INSERT INTO otel_traces
SELECT * FROM gen_traces(start_minute = {chunk_start:DateTime}, n_minutes = {chunk_minutes:UInt32}, backfill = 1);

INSERT INTO otel_logs
SELECT * FROM gen_logs(start_minute = {chunk_start:DateTime}, n_minutes = {chunk_minutes:UInt32});

INSERT INTO otel_metrics_histogram
SELECT * FROM gen_metrics_histogram(start_minute = {chunk_start:DateTime}, n_minutes = {chunk_minutes:UInt32});

INSERT INTO otel_metrics_sum
SELECT * FROM gen_metrics_sum(start_minute = {chunk_start:DateTime}, n_minutes = {chunk_minutes:UInt32});

INSERT INTO otel_metrics_gauge
SELECT * FROM gen_metrics_gauge(start_minute = {chunk_start:DateTime}, n_minutes = {chunk_minutes:UInt32});
