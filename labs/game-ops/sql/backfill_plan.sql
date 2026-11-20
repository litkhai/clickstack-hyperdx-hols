-- What the backfill would write: exact span and trace counts of the generator over the window
-- (logs and metrics are derived from those spans afterwards). Parameters: window_start, window_minutes.
SELECT count() AS spans, uniqExact(TraceId) AS traces
FROM gen_traces(start_minute = {window_start:DateTime}, n_minutes = {window_minutes:UInt32}, backfill = 1);
