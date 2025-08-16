#Table 1
spark-submit \
  --jars /Users/gaurav/.m2/repository/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar \
  csv_to_table.py \
  --csv ../data/earnings_estimates_2024-06-01.csv \
  --table earnings_estimates

#Table 2
spark-submit \
  --jars /Users/gaurav/.m2/repository/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar \
  csv_to_table.py \
  --csv ../data/earnings_history_2024-06-01.csv \
  --table earnings_history

#Table 3
spark-submit \
  --jars /Users/gaurav/.m2/repository/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar \
  csv_to_table.py \
  --csv ../data/growth_estimates_2024-06-01.csv \
  --table growth_estimates

#Table 4
spark-submit \
  --jars /Users/gaurav/.m2/repository/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar \
  csv_to_table.py \
  --csv ../data/quarterly_income_2024-06-01.csv \
  --table quarterly_income

#Table 4
spark-submit \
  --jars /Users/gaurav/.m2/repository/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar \
  csv_to_table.py \
  --csv ../data/quarterly_revenue_2024-06-01.csv \
  --table quarterly_revenue

#Table 6
spark-submit \
  --jars /Users/gaurav/.m2/repository/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar \
  csv_to_table.py \
  --csv ../data/revenue_estimates_2024-06-01.csv \
  --table revenue_estimates 

#Table 7
spark-submit \
  --jars /Users/gaurav/.m2/repository/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar \
  csv_to_table.py \
  --csv ../data/stock_earnings_2024-06-01.csv \
  --table stock_earnings  
