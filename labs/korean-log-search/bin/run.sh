#!/usr/bin/env bash
# labs/korean-log-search — runs the lab against a ClickHouse Cloud service.
#
#   bin/run.sh            install, check, searches, results
#   bin/run.sh install    database, four tables, 3,000,000 generated rows, the three copies
#   bin/run.sh check      sql/03_checks.sql (K1–K9, PASS/FAIL)
#   bin/run.sh searches   the searches ClickStack's search page sends, each tagged with a log_comment
#   bin/run.sh results    sql/04_results.sql (index size, copy CPU, granules per search)
#   bin/run.sh drop       sql/99_drop.sql
#
# Credentials come from the environment (see .env.example): CH_HOST, CH_USER, CH_PASSWORD.
# Nothing outside the database korean_log_search is created or changed.
set -euo pipefail
LAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${CH_HOST:?set CH_HOST}" "${CH_USER:?set CH_USER}" "${CH_PASSWORD:?set CH_PASSWORD}"

ch() { clickhouse client --secure --host "$CH_HOST" --user "$CH_USER" --password "$CH_PASSWORD" "$@"; }

install() {
  ch -q "SELECT version() AS clickhouse_version"
  ch --multiquery --queries-file "$LAB/sql/01_schema.sql"
  ch -q "TRUNCATE TABLE IF EXISTS korean_log_search.logs_plain"
  ch --queries-file "$LAB/sql/02_load.sql"
  for t in copy word ngram; do
    ch -q "TRUNCATE TABLE IF EXISTS korean_log_search.logs_$t"
    ch --log_comment "korean_log_search:load:$t" \
       -q "INSERT INTO korean_log_search.logs_$t SELECT * FROM korean_log_search.logs_plain"
  done
  # One part per partition, so index sizes are compared on merged data.
  for t in plain copy word ngram; do ch -q "OPTIMIZE TABLE korean_log_search.logs_$t FINAL"; done
}

check() { ch --use_query_condition_cache 0 --queries-file "$LAB/sql/03_checks.sql"; }

# The WHERE clause ClickStack's search page builds for the last 15 minutes: the time range plus the
# 5-minute bucket of the sort key, then the term. Auto on a word index and Force enable both render
# hasAllTokens; Auto on an ngram index renders hasToken; a wildcard renders LIKE.
searches() {
  local w="Timestamp >= now64(9) - INTERVAL 15 MINUTE AND toStartOfFiveMinutes(Timestamp) >= toStartOfFiveMinutes(now64(9) - INTERVAL 15 MINUTE)"
  run() { ch --use_query_condition_cache 0 --log_comment "korean_log_search:search:$1" \
             -q "SELECT Timestamp, Body FROM korean_log_search.$2 WHERE $w AND $3 ORDER BY Timestamp DESC LIMIT 200" >/dev/null; }
  run s0_word_range_only        logs_word  "1"
  run s1_word_auto_결제         logs_word  "hasAllTokens(lower(Body), lower('결제'))"
  run s2_ngram_auto_결제        logs_ngram "hasToken(lower(Body), lower('결제'))"
  run s3_ngram_force_결제       logs_ngram "hasAllTokens(lower(Body), lower('결제'))"
  run s4_word_auto_환불요청     logs_word  "hasAllTokens(lower(Body), lower('환불요청'))"
  run s5_ngram_auto_환불요청    logs_ngram "hasToken(lower(Body), lower('환불요청'))"
  run s6_ngram_force_환불요청   logs_ngram "hasAllTokens(lower(Body), lower('환불요청'))"
  run s7_word_wildcard_환불요청  logs_word  "lower(Body) LIKE lower('%환불요청%')"
  run s8_ngram_wildcard_환불요청 logs_ngram "lower(Body) LIKE lower('%환불요청%')"
  # FLUSH LOGS reaches only the replica that serves it; the others flush on their own interval (7.5 s by default).
  ch -q "SYSTEM FLUSH LOGS"
  sleep 15
}

results() { ch --queries-file "$LAB/sql/04_results.sql"; }

drop() { ch --queries-file "$LAB/sql/99_drop.sql"; }

case "${1:-all}" in
  all) install; check; searches; results ;;
  install|check|searches|results|drop) "$1" ;;
  *) echo "usage: $0 [all|install|check|searches|results|drop]" >&2; exit 2 ;;
esac
