#!/usr/bin/env python3
"""
Fetch XAUUSD 1-min MIDPOINT data from TWS and append to XAUUSD_COMPLETE.csv.

Automatically detects last date in the CSV and fetches up to END_DATE.

Usage:
    python tools/data/fetch_data_xauusd.py

Requirements:
    - IB Gateway or TWS running on 127.0.0.1:7497
    - ib_insync installed
"""

import csv
import os
import time
from datetime import datetime, timedelta

from ib_insync import IB, Commodity, util

try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    util.patchAsyncio()

CSV_PATH = os.path.join(os.path.dirname(__file__), '../../database/snapshots/XAUUSD_COMPLETE.csv')
CSV_PATH = os.path.abspath(CSV_PATH)

END_DATE = datetime(2026, 6, 12, 22, 0, 0)  # fetch up to June 12

REQUESTS_PER_WINDOW = 55
WINDOW_SECONDS = 600


def get_last_timestamp(path):
    with open(path, 'r') as f:
        last_line = None
        for line in f:
            if line.startswith('timestamp'):
                continue
            last_line = line
    ts_str = last_line.split(',')[0]
    return datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')


def main():
    last_ts  = get_last_timestamp(CSV_PATH)
    start_dt = last_ts.replace(hour=0, minute=0, second=0) + timedelta(days=1)
    end_dt   = END_DATE

    total_days = (end_dt.date() - start_dt.date()).days
    if total_days <= 0:
        print("XAUUSD data is already up to date.")
        return

    print(f"Last data:  {last_ts}")
    print(f"Missing:    {start_dt.date()} → {end_dt.date()} ({total_days} days)")
    print(f"Output:     {CSV_PATH}")
    print()

    ib = IB()
    ib.connect('127.0.0.1', 7497, clientId=2, timeout=30)
    print(f"Connected: {ib.isConnected()}")

    contract = Commodity('XAUUSD', 'SMART', 'USD')
    qualified = ib.qualifyContracts(contract)
    contract  = qualified[0]
    print(f"Contract:  {contract}\n")

    requests_made = 0
    window_start  = time.time()
    new_bars      = {}

    current   = end_dt
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

        print(f"[{days_done+1}/{total_days}] {current.strftime('%Y-%m-%d')} ...", end=" ", flush=True)

        try:
            bars = ib.reqHistoricalData(
                contract,
                endDateTime=current,
                durationStr='1 D',
                barSizeSetting='1 min',
                whatToShow='MIDPOINT',
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

    print(f"\nDone. XAUUSD_COMPLETE.csv now contains data up to {sorted_bars[-1][0]}")
    print(f"Total new bars appended: {len(sorted_bars):,}")


if __name__ == '__main__':
    main()
