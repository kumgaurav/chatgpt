CREATE TABLE `earnings_history` (
  `ticker` varchar(10) COLLATE utf8mb4_unicode_ci NOT NULL,
  `earnings_date` date NOT NULL,
  `reported_eps` double DEFAULT NULL,
  `estimate_eps` double DEFAULT NULL,
  `surprise_percentage` double DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE stocksdb.earnings_history
ADD PRIMARY KEY (ticker, earnings_date);