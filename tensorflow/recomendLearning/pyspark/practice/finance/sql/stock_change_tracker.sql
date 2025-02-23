select * from stocksdb.stock_change_tracker
where symbol='APPS'

UPDATE stocksdb.stock_change_tracker c
JOIN stocksdb.stocksinfp s
ON s.symbol = c.symbol AND s.date = '2025-01-02'
SET c.date_added = '2025-01-02',
    c.price_when_added = s.close
WHERE s.date = '2025-01-02'
#and c.symbol = 'AAPL';
