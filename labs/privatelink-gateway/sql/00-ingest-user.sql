-- One statement per line: bin/setup.sh sends each line as its own request (the HTTP interface runs
-- one statement per request). {{INGEST_PASSWORD}} is replaced from the terraform output.
-- Grants as in the ClickStack OTel collector page (read 2026-10-14): SELECT, INSERT, CREATE DATABASE,
-- CREATE TABLE, CREATE VIEW on the database. CREATE_* are needed because the exporters run with
-- create_schema: true.
CREATE DATABASE IF NOT EXISTS otel_pl;
CREATE DATABASE IF NOT EXISTS otel_replay;
CREATE USER IF NOT EXISTS otel_ingest IDENTIFIED WITH sha256_password BY '{{INGEST_PASSWORD}}';
GRANT SELECT, INSERT, CREATE DATABASE, CREATE TABLE, CREATE VIEW ON otel_pl.* TO otel_ingest;
GRANT SELECT, INSERT, CREATE DATABASE, CREATE TABLE, CREATE VIEW ON otel_replay.* TO otel_ingest;
