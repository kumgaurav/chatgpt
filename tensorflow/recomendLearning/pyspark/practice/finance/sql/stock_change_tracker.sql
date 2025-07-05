select * from stocksdb.stock_change_tracker
where symbol='APPS'

UPDATE stocksdb.stock_change_tracker c
JOIN stocksdb.stocksinfp s
ON s.symbol = c.symbol AND s.date = '2025-01-02'
SET c.date_added = '2025-01-02',
    c.price_when_added = s.close
WHERE s.date = '2025-01-02'
#and c.symbol = 'AAPL';

selected_features = correlation_matrix[abs(correlation_matrix) > correlation_threshold].index
# Filter future_features to keep only highly correlated features
future_features = future_features[selected_features]
# Apply statistical preparation and tests
stats_df = prepare_and_test_features(future_features, tickers)
as we are droping the features, its going into key error - KeyError: "['MCK_Return'] not in index"

CREATE TABLE `stock_change_tracker` (
  `symbol` VARCHAR(10) NOT NULL,
  `date_added` DATE DEFAULT NULL,
  `price_when_added` DOUBLE DEFAULT NULL,
  `current_price` DOUBLE DEFAULT NULL,
  `is_active` BIT(1) DEFAULT NULL,
  `change_since_added` DOUBLE DEFAULT NULL,
  `change_in_percent` DOUBLE DEFAULT NULL,
  `is_positive_earning` BIT(1) NOT NULL,
  `updated_date` DATE DEFAULT NULL,
  PRIMARY KEY (`symbol`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

UPDATE `stocksdb`.`stock_change_tracker`
SET
`is_active` = false
WHERE symbol in ('REGI')
