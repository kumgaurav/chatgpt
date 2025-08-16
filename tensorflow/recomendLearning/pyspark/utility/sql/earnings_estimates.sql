drop table if exists earnings_estimates;
CREATE TABLE `earnings_estimates` (
  `ticker` varchar(10) COLLATE utf8mb4_unicode_ci NOT NULL,
  `period` VARCHAR(25),
  `earnings_date` date NOT NULL,
  `average_estimate` double DEFAULT NULL,
  `low_estimate` double DEFAULT NULL,
  `high_estimate` double DEFAULT NULL,
  `number_of_analysts` double DEFAULT NULL,
  `year_ago_eps` double DEFAULT NULL,
  `growth` double DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE stocksdb.earnings_estimates
ADD PRIMARY KEY (ticker, period, earnings_date);

