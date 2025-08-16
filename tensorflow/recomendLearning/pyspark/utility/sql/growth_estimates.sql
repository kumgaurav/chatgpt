drop table if exists growth_estimates;
CREATE TABLE `growth_estimates` (
  `estimate_fetch_date` date NOT NULL,
  `ticker` varchar(10) COLLATE utf8mb4_unicode_ci NOT NULL,
  `period` VARCHAR(25),
  `stock_growth` double DEFAULT NULL,
  `index_growth` double DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE stocksdb.growth_estimates
ADD PRIMARY KEY (estimate_fetch_date, ticker, period);