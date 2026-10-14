#!/usr/bin/env python3
"""
Usage:
    python scripts/backfill/fetch_data.py

Requirements:
    - IB Gateway or TWS running on 127.0.0.1:7497
    - ib_insync installed
"""

import csv
import os
import time
from datetime import datetime, timedelta

from ib_insync import IB, Future, util

try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    util.patchAsyncio()

CSV_PATH = os.path.join(os.path.dirname(__file__), '../../database/snapshots/GC_FUTURES_1MIN.csv')
CSV_PATH = os.path.abspath(CSV_PATH)

REQUESTS_PER_WINDOW = 55
WINDOW_SECONDS = 600


DEFAULT_START_MONTHS_BACK = 6


def get_last_timestamp(path):
    if not os.path.exists(path):
        # Create CSV with header, start from N months back
        start = datetime.utcnow().replace(hour=0, minute=0, second=0)
        for _ in range(DEFAULT_START_MONTHS_BACK * 30):
            start = start - timedelta(days=1)
        with open(path, 'w', newline='') as f:
            f.write('timestamp,open,high,low,close,volume\n')
        print(f"Created new CSV. Fetching from {start.date()}")
        return start - timedelta(days=1)  # script adds 1 day, so subtract here

    with open(path, 'r') as f:
        last_line = None
        for line in f:
            if line.startswith('timestamp'):
                continue
            last_line = line
    if last_line is None:
        start = datetime.utcnow() - timedelta(days=DEFAULT_START_MONTHS_BACK * 30)
        return start
    ts_str = last_line.split(',')[0]
    return datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')


def main():
    last_ts = get_last_timestamp(CSV_PATH)
    # Start fetching from the day after last data
    start_dt = last_ts.replace(hour=0, minute=0, second=0) + timedelta(days=1)
    end_dt   = datetime.utcnow().replace(hour=22, minute=0, second=0)

    total_days = (end_dt.date() - start_dt.date()).days
    if total_days <= 0:
        print("Data is already up to date.")
        return

    print(f"Last data:  {last_ts}")
    print(f"Fetching:   {start_dt.date()} → {end_dt.date()} ({total_days} days)")
    print(f"Output:     {CSV_PATH}")
    print()

    ib = IB()
    ib.connect('127.0.0.1', 7497, clientId=1, timeout=30)  # 7497=TWS/IBKR Desktop, 4002=IB Gateway
    print(f"Connected: {ib.isConnected()}")

    # Work backwards from end_dt to start_dt (IB requires endDateTime)
    requests_made = 0
    window_start  = time.time()
    new_bars      = {}

    current = end_dt
    days_done = 0

    while current.date() >= start_dt.date():
        # Pacing
        elapsed = time.time() - window_start
        if elapsed >= WINDOW_SECONDS:
            requests_made = 0
            window_start  = time.time()
        if requests_made >= REQUESTS_PER_WINDOW:
            wait = WINDOW_SECONDS - elapsed + 5
            print(f"\n*** Pacing limit — waiting {wait:.0f}s ***\n")
            time.sleep(wait)
            requests_made = 0
            window_start  = time.time()

        # GC front month: active months are Feb,Apr,Jun,Aug,Oct,Dec
        # Roll ~15 days before expiry, so look 15 days ahead to pick the right contract
        active_months = [2, 4, 6, 8, 10, 12]
        check = current + timedelta(days=15)
        expiry_month = next((m for m in active_months if m >= check.month), active_months[0])
        expiry_year  = check.year if expiry_month >= check.month else check.year + 1
        expiry = f"{expiry_year}{expiry_month:02d}"
        contract = Future('GC', exchange='COMEX', currency='USD', lastTradeDateOrContractMonth=expiry)
        contract.includeExpired = True

        print(f"[{days_done+1}/{total_days}] {current.strftime('%Y-%m-%d')} GC{expiry} ...", end=" ", flush=True)

        try:
            bars = ib.reqHistoricalData(
                contract,
                endDateTime=current,
                durationStr='1 D',
                barSizeSetting='1 min',
                whatToShow='TRADES',
                useRTH=False,
                formatDate=1,
                keepUpToDate=False,
                timeout=60,
            )
            requests_made += 1
        except Exception as e:
            print(f"ERROR: {e}")
            time.sleep(10)
            current -= timedelta(days=1)
            continue

        if bars:
            for bar in bars:
                ts = bar.date if isinstance(bar.date, datetime) else datetime.strptime(str(bar.date), '%Y-%m-%d %H:%M:%S')
                if hasattr(ts, 'tzinfo') and ts.tzinfo is not None:
                    ts = ts.replace(tzinfo=None)
                if ts > last_ts:
                    new_bars[ts] = bar
            print(f"{len(bars)} bars")
        else:
            print("no data")

        days_done += 1
        current -= timedelta(days=1)
        time.sleep(1)

    ib.disconnect()

    if not new_bars:
        print("\nNo new bars to append.")
        return

    sorted_bars = sorted(new_bars.items())
    print(f"\nAppending {len(sorted_bars):,} new bars to CSV...")

    with open(CSV_PATH, 'a', newline='') as f:
        writer = csv.writer(f)
        for ts, bar in sorted_bars:
            writer.writerow([
                ts.strftime('%Y-%m-%d %H:%M:%S'),
                bar.open, bar.high, bar.low, bar.close, bar.volume,
            ])

    print(f"Done. CSV now contains data up to {sorted_bars[-1][0]}")


if __name__ == '__main__':
    main()
