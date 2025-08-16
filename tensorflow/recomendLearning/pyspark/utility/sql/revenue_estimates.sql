CREATE TABLE `revenue_estimates` (
  `ticker` VARCHAR(10) NOT NULL,
  `period` VARCHAR(25),
  `next_earnings_date` DATE NOT NULL,
  `average_forecast` DOUBLE DEFAULT NULL,
  `low_forecast` DOUBLE DEFAULT NULL,
  `high_forecast` DOUBLE DEFAULT NULL,
  `number_of_analysts` DOUBLE DEFAULT NULL,
  `year_ago_revenue` DOUBLE DEFAULT NULL,
  `growth` DOUBLE DEFAULT NULL,
  PRIMARY KEY (`ticker`, `next_earnings_date`, `period`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE stocksdb.revenue_estimates
ADD PRIMARY KEY (ticker, period,next_earnings_date);