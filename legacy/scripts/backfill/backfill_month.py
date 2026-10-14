#!/usr/bin/env python3
"""Backfill XAUUSD data for a given month from IB Gateway to PostgreSQL"""

import sys, os
sys.path.insert(0, '/home/ubuntu/algo_project/backend')

from ib_insync import IB, Commodity, util
from datetime import datetime, timedelta
import time, psycopg2, calendar

try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    util.patchAsyncio()

DB_URL = os.getenv('DATABASE_URL')

# Parse args
if len(sys.argv) < 3:
    print("Usage: python backfill_month.py YEAR MONTH")
    print("  e.g. python backfill_month.py 2025 11")
    sys.exit(1)

year = int(sys.argv[1])
month = int(sys.argv[2])
last_day = calendar.monthrange(year, month)[1]

start_dt = datetime(year, month, 1)
end_dt = datetime(year, month, last_day, 22, 0)

# Connect to IB
ib = IB()
ib.connect('127.0.0.1', 4002, clientId=99, timeout=20)
print(f"IB Connected: {ib.isConnected()}")

contract = Commodity('XAUUSD', 'SMART', 'USD')
qualified = ib.qualifyContracts(contract)
print(f"Contract: {qualified[0]}")

# Connect to PostgreSQL
conn = psycopg2.connect(DB_URL)
cursor = conn.cursor()
print(f"DB Connected")

current = end_dt
total_saved = 0
days_processed = 0

print(f"\nBackfilling XAUUSD {year}-{month:02d}")
print(f"{'='*60}")

while current > start_dt:
    print(f"Fetching endDateTime={current.isoformat()}...", end=" ", flush=True)

    try:
        bars = ib.reqHistoricalData(
            qualified[0],
            endDateTime=current,
            durationStr='1 D',
            barSizeSetting='1 min',
            whatToShow='MIDPOINT',
            useRTH=False,
            formatDate=1,
            timeout=60
        )
    except Exception as e:
        print(f"ERROR: {e}")
        time.sleep(10)
        current -= timedelta(days=1)
        continue

    if bars:
        saved = 0
        for bar in bars:
            try:
                cursor.execute("""
                    INSERT INTO ohlcv_realtime_1min
                        (symbol, timestamp, open, high, low, close, volume, bar_count, average)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol, timestamp) DO NOTHING
                """, (
                    'XAUUSD', bar.date,
                    float(bar.open), float(bar.high), float(bar.low), float(bar.close),
                    int(bar.volume),
                    int(bar.barCount) if hasattr(bar, 'barCount') else 0,
                    float(bar.average) if hasattr(bar, 'average') else 0.0
                ))
                saved += 1
            except Exception as e:
                print(f"DB error: {e}")
                conn.rollback()
        conn.commit()
        total_saved += saved
        print(f"{len(bars)} bars ({bars[0].date.strftime('%m/%d %H:%M')} to {bars[-1].date.strftime('%m/%d %H:%M')})")
    else:
        print("no data")

    days_processed += 1
    current -= timedelta(days=1)
    time.sleep(1)

print(f"\n{'='*60}")
print(f"Done! {year}-{month:02d}: {days_processed} days, {total_saved} bars saved")

cursor.execute("""
    SELECT TO_CHAR(timestamp, 'YYYY-MM') as month, COUNT(*) as bars,
           MIN(timestamp)::date as first, MAX(timestamp)::date as last
    FROM ohlcv_realtime_1min WHERE symbol = 'XAUUSD'
    GROUP BY TO_CHAR(timestamp, 'YYYY-MM') ORDER BY month
""")
print(f"\nAll months:")
for r in cursor.fetchall():
    print(f"  {r[0]}: {r[1]:,} bars ({r[2]} to {r[3]})")

cursor.close()
conn.close()
ib.disconnect()
