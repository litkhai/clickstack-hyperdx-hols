-- Sizes: rows and bytes on disk per table of the lab (active parts).
SELECT table, sum(rows) AS rows, formatReadableSize(sum(bytes_on_disk)) AS on_disk, formatReadableSize(sum(data_uncompressed_bytes)) AS uncompressed,
       round(sum(data_uncompressed_bytes) / sum(bytes_on_disk), 1) AS ratio
FROM system.parts WHERE database = 'apm_workflows' AND active
GROUP BY table ORDER BY sum(bytes_on_disk) DESC;
