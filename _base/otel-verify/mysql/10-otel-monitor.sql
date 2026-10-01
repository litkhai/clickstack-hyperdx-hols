-- The image creates the user itself from MYSQL_USER / MYSQL_PASSWORD in
-- docker-compose.otel-verify.yml, as
--   CREATE USER 'otel_monitor'@'%' IDENTIFIED BY '<password>';
-- (docker-entrypoint.sh, "Creating user"), which is the first statement of the
-- Prerequisites in otel-profiles/profiles/mysql/README.md. These are the rest
-- of that block, verbatim.
--
-- A .sql file rather than a .sh one on purpose. With a .sh version of this on a
-- Docker Desktop bind mount, the entrypoint logged "running <file>" (its
-- executable branch; it only sources a file that fails `[ -x ]`) for a mode-644
-- file and then failed with "Permission denied", so the user was never created.
GRANT SELECT ON performance_schema.* TO 'otel_monitor'@'%';
FLUSH PRIVILEGES;
