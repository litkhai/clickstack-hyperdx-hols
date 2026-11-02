-- 3,000,000 rows over the last three hours, ending now, so ClickStack's default time ranges show them.
-- Body: ten message templates (Korean with particles attached, English with case variants) and two rare markers:
--   n % 1000003 = 7   ->  ' 환불요청 RefundReq'      the term the checks look for (true matches)
--   n % 1000033 = 11  ->  ' 환불 처리 불요 재요청'    holds every 2-gram of 환불요청 but not the word (false-match bait)
INSERT INTO korean_log_search.logs_plain
WITH
    ['결제가 실패했습니다 order_id=', '결제를 완료했습니다 order_id=', '로그인에 성공했습니다 uid=',
     '아이템을 구매했습니다 item=', '세션이 만료되었습니다 sid=', 'Payment failed: card declined ref=',
     'upstream errors while calling inventory req=', 'request completed in 12 ms req=',
     'ERROR connection reset by peer conn=', '보상을 수령했습니다 reward='] AS templates
SELECT
    now64(9) - toIntervalMillisecond(intDiv(number * 36, 10))                        AS Timestamp,
    ['payment', 'order', 'auth', 'game', 'chat', 'gateway'][1 + number % 6]          AS ServiceName,
    ['INFO', 'INFO', 'INFO', 'WARN', 'ERROR'][1 + cityHash64(number, 1) % 5]         AS SeverityText,
    concat(templates[1 + cityHash64(number, 2) % 10], toString(number % 100000),
           if(number % 1000003 = 7, ' 환불요청 RefundReq', ''),
           if(number % 1000033 = 11, ' 환불 처리 불요 재요청', ''))                  AS Body
FROM numbers(3000000);

-- The three copies, each one INSERT … SELECT from the same rows. bin/run.sh tags each with a log_comment so
-- 05_results.sql can compare their CPU in system.query_log.
