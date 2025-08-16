CREATE TABLE `stock_earnings` (
  `ticker` varchar(10) COLLATE utf8mb4_unicode_ci NOT NULL,
  `short_name` text,
  `sector` text,
  `industry` text,
  `market_cap` bigint DEFAULT NULL,
  `revenue` bigint DEFAULT NULL,
  `gross_profit` bigint DEFAULT NULL,
  `ebitda` bigint DEFAULT NULL,
  `earnings_date` date NOT NULL,
  `fifty_two_week_high` double DEFAULT NULL,
  `fifty_two_week_low` double DEFAULT NULL,
  `dividend_yield` double DEFAULT NULL,
  `pe_ratio` double DEFAULT NULL,
  `forward_pe` double DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE stocksdb.stock_earnings
ADD PRIMARY KEY (ticker, earnings_date);

SELECT * FROM stocksdb.stock_earnings;
