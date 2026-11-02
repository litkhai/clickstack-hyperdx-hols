-- labs/korean-log-search — checks. Every search sits in WHERE, as ClickStack's search page puts it.
-- Ground truth comes from logs_plain by plain string functions (no index involved).
-- Each row: the check, what it shows, the expected and actual value, PASS or FAIL.
WITH
    (SELECT countIf(positionCaseInsensitiveUTF8(Body, '결제') > 0)             FROM korean_log_search.logs_plain) AS t_kr,
    (SELECT countIf(positionUTF8(Body, '환불요청') > 0)                        FROM korean_log_search.logs_plain) AS t_rare,
    (SELECT countIf(positionUTF8(Body, '환불 처리 불요 재요청') > 0)            FROM korean_log_search.logs_plain) AS t_bait,
    (SELECT countIf(positionCaseInsensitiveUTF8(Body, 'error') > 0)            FROM korean_log_search.logs_plain) AS t_err_sub,
    (SELECT countIf(match(lower(Body), '(^|[^a-z])error([^a-z]|$)'))           FROM korean_log_search.logs_plain) AS t_err_word,
    (SELECT countIf(positionUTF8(Body, '환') > 0)                              FROM korean_log_search.logs_plain) AS t_one,
    (SELECT count() FROM korean_log_search.logs_word  WHERE hasAllTokens(lower(Body), lower('결제')))     AS w_kr,
    (SELECT count() FROM korean_log_search.logs_ngram WHERE hasAllTokens(lower(Body), lower('결제')))     AS n_kr,
    (SELECT count() FROM korean_log_search.logs_ngram WHERE hasToken(lower(Body), lower('결제')))         AS n_kr_hastoken,
    (SELECT count() FROM korean_log_search.logs_word  WHERE hasAllTokens(lower(Body), lower('환불요청'))) AS w_rare,
    (SELECT count() FROM korean_log_search.logs_ngram WHERE hasAllTokens(lower(Body), lower('환불요청'))) AS n_rare,
    (SELECT count() FROM korean_log_search.logs_word  WHERE lower(Body) LIKE lower('%환불요청%'))         AS w_like,
    (SELECT count() FROM korean_log_search.logs_ngram WHERE lower(Body) LIKE lower('%환불요청%'))         AS n_like,
    (SELECT count() FROM korean_log_search.logs_word  WHERE hasAllTokens(lower(Body), lower('error')))    AS w_err,
    (SELECT count() FROM korean_log_search.logs_ngram WHERE hasAllTokens(lower(Body), lower('error')))    AS n_err,
    (SELECT count() FROM korean_log_search.logs_ngram WHERE hasAllTokens(lower(Body), lower('환')))       AS n_one,
    (SELECT count() FROM korean_log_search.logs_ngram WHERE lower(Body) LIKE lower('%환%'))               AS n_one_like
SELECT check, shows, expected, actual, if(expected = actual, 'PASS', 'FAIL') AS result
FROM
(
    SELECT 'K1' AS check, 'word index: 결제 (particles attached) finds nothing' AS shows, 0 AS expected, w_kr AS actual
    UNION ALL SELECT 'K1+', 'control: the rows exist (ground truth > 0)', 1, toUInt64(t_kr > 0)
    UNION ALL SELECT 'K2', 'ngram index, hasAllTokens (Force enable): 결제 finds every row', t_kr, n_kr
    UNION ALL SELECT 'K3', 'ngram index, hasToken (Auto falls back to it): 결제 finds nothing', 0, n_kr_hastoken
    UNION ALL SELECT 'K4', 'word index: a standalone word (환불요청) is exact', t_rare, w_rare
    UNION ALL SELECT 'K5', 'ngram index: 환불요청 also matches rows holding its 2-grams apart (false matches = bait rows)', t_rare + t_bait, n_rare
    UNION ALL SELECT 'K6', 'wildcard *환불요청* (LIKE) is exact on both tables: word', t_rare, w_like
    UNION ALL SELECT 'K6b', 'wildcard *환불요청* (LIKE) is exact on both tables: ngram', t_rare, n_like
    UNION ALL SELECT 'K7', 'word index: error matches the word only (not errors)', t_err_word, w_err
    UNION ALL SELECT 'K8', 'ngram index: error matches as a substring (errors too)', t_err_sub, n_err
    UNION ALL SELECT 'K9', 'ngram index: a one-character term (환) finds nothing', 0, n_one
    UNION ALL SELECT 'K9+', 'control: the same character as a wildcard (LIKE) finds the rows', t_one, n_one_like
)
ORDER BY check
FORMAT PrettyCompactMonoBlock;
