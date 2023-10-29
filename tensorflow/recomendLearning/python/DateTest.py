import datetime
from datetime import date, timedelta

#calculate date range
today = date.today()
d1 = today.strftime("%Y-%m-%d")
end_date = d1
print(end_date)
d2= date.today() - timedelta(days=5000)
d2 = d2.strftime("%Y-%m-%d")
start_date = d2
print(start_date)
