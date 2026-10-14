#!/usr/bin/env python3
"""
Import XAUUSD CSV data into PostgreSQL ohlcv_historical_1min table.

Restores historical data that was lost during disk-full incident.
Run this on the Oracle server after uploading the CSV.

Usage:
    # Import all data from CSV:
    python import_csv_to_postgres.py data.csv

    # Import only January 2026:
    python import_csv_to_postgres.py data.csv --start 2026-01-01 --end 2026-01-31

    # Import last 6 months:
    python import_csv_to_postgres.py data.csv --start 2025-08-01

    # Dry run (count rows, don't insert):
    python import_csv_to_postgres.py data.csv --dry-run
"""

import csv
import sys
import argparse
import os
from datetime import datetime

try:
    import psycopg2
except ImportError:
    print("Installing psycopg2-binary...")
    os.system(f"{sys.executable} -m pip install psycopg2-binary")
    import psycopg2


def get_db_connection():
    """Connect to PostgreSQL using DATABASE_URL env variable or default"""
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is required")
    print(f"Connecting to: {database_url.split('@')[1] if '@' in database_url else database_url}")
    return psycopg2.connect(database_url)


def import_csv(csv_path, symbol='XAUUSD', start_date=None, end_date=None,
               batch_size=5000, dry_run=False):
    """
    Import CSV data into ohlcv_historical_1min table.

    Args:
        csv_path: Path to CSV file (timestamp,open,high,low,close,volume)
        symbol: Symbol name to store as
        start_date: Only import rows >= this date (YYYY-MM-DD)
        end_date: Only import rows <= this date (YYYY-MM-DD)
        batch_size: Number of rows per INSERT batch
        dry_run: If True, just count rows without inserting
    """
    if not os.path.exists(csv_path):
        print(f"ERROR: File not found: {csv_path}")
        sys.exit(1)

    # Parse date filters
    start_dt = datetime.strptime(start_date, '%Y-%m-%d') if start_date else None
    end_dt = datetime.strptime(end_date + ' 23:59:59', '%Y-%m-%d %H:%M:%S') if end_date else None

    print(f"Reading CSV: {csv_path}")
    print(f"Symbol: {symbol}")
    if start_dt:
        print(f"Start filter: {start_date}")
    if end_dt:
        print(f"End filter: {end_date}")

    # Read and filter CSV
    rows = []
    total_read = 0
    skipped = 0

    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)

        # Check column names
        expected = {'timestamp', 'open', 'high', 'low', 'close', 'volume'}
        if not expected.issubset(set(reader.fieldnames)):
            print(f"ERROR: CSV must have columns: {expected}")
            print(f"Found: {reader.fieldnames}")
            sys.exit(1)

        for row in reader:
            total_read += 1

            # Parse timestamp
            ts_str = row['timestamp']
            try:
                ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00').replace('+00:00', ''))
            except ValueError:
                try:
                    ts = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    ts = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S%z').replace(tzinfo=None)

            # Apply date filters
            if start_dt and ts < start_dt:
                skipped += 1
                continue
            if end_dt and ts > end_dt:
                skipped += 1
                continue

            o = float(row['open'])
            h = float(row['high'])
            l = float(row['low'])
            c = float(row['close'])
            v = int(float(row['volume']))

            # Basic validation (same as database_writer)
            if o <= 0 or h <= 0 or l <= 0 or c <= 0:
                skipped += 1
                continue
            if h < l:
                skipped += 1
                continue

            rows.append((symbol, ts, o, h, l, c, v))

            if total_read % 500000 == 0:
                print(f"  Read {total_read:,} rows, kept {len(rows):,}...")

    print(f"\nTotal read: {total_read:,}")
    print(f"Skipped (filtered/invalid): {skipped:,}")
    print(f"Rows to import: {len(rows):,}")

    if not rows:
        print("No rows to import.")
        return

    if rows:
        print(f"Date range: {rows[0][1]} to {rows[-1][1]}")

    if dry_run:
        print("\n[DRY RUN] No data inserted.")
        return

    # Connect to PostgreSQL
    print(f"\nConnecting to PostgreSQL...")
    conn = get_db_connection()
    cursor = conn.cursor()

    # Check existing data
    cursor.execute(
        "SELECT COUNT(*) FROM ohlcv_historical_1min WHERE symbol = %s",
        (symbol,)
    )
    existing = cursor.fetchone()[0]
    print(f"Existing rows in ohlcv_historical_1min for {symbol}: {existing:,}")

    # Insert in batches using executemany with ON CONFLICT
    insert_sql = """
        INSERT INTO ohlcv_historical_1min
            (symbol, timestamp, open, high, low, close, volume)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (symbol, timestamp) DO NOTHING
    """

    imported = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        cursor.executemany(insert_sql, batch)
        conn.commit()
        imported += len(batch)
        pct = (imported / len(rows)) * 100
        print(f"  Imported {imported:,}/{len(rows):,} ({pct:.1f}%)")

    # Check final count
    cursor.execute(
        "SELECT COUNT(*) FROM ohlcv_historical_1min WHERE symbol = %s",
        (symbol,)
    )
    final_count = cursor.fetchone()[0]

    cursor.execute(
        "SELECT MIN(timestamp), MAX(timestamp) FROM ohlcv_historical_1min WHERE symbol = %s",
        (symbol,)
    )
    min_ts, max_ts = cursor.fetchone()

    print(f"\nImport complete!")
    print(f"Total rows in ohlcv_historical_1min: {final_count:,}")
    print(f"Date range: {min_ts} to {max_ts}")

    # Refresh materialized views
    print("\nRefreshing materialized views...")
    try:
        cursor.execute("SELECT refresh_all_timeframes()")
        conn.commit()
        print("Materialized views refreshed successfully!")
    except Exception as e:
        print(f"Warning: Could not refresh views: {e}")
        print("Views will auto-refresh within 5 minutes.")
        conn.rollback()

    cursor.close()
    conn.close()
    print("\nDone! The chart should now show historical data.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Import CSV data into PostgreSQL')
    parser.add_argument('csv_file', help='Path to CSV file')
    parser.add_argument('--symbol', default='XAUUSD', help='Symbol name (default: XAUUSD)')
    parser.add_argument('--start', help='Start date filter (YYYY-MM-DD)')
    parser.add_argument('--end', help='End date filter (YYYY-MM-DD)')
    parser.add_argument('--batch-size', type=int, default=5000, help='Batch size for inserts')
    parser.add_argument('--dry-run', action='store_true', help='Count rows without inserting')

    args = parser.parse_args()

    import_csv(
        csv_path=args.csv_file,
        symbol=args.symbol,
        start_date=args.start,
        end_date=args.end,
        batch_size=args.batch_size,
        dry_run=args.dry_run
    )
