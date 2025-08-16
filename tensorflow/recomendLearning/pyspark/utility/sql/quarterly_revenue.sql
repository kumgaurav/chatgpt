CREATE TABLE `quarterly_revenue` (
  `ticker` varchar(10) COLLATE utf8mb4_unicode_ci NOT NULL,
  `report_date` date NOT NULL,
  `revenue` double DEFAULT NULL,
  `gross_profit` double DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE stocksdb.quarterly_revenue
ADD PRIMARY KEY (ticker, report_date);