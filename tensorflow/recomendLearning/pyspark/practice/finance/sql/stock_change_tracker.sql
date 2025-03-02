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
