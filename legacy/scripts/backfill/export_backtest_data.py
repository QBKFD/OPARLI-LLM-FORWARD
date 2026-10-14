#!/usr/bin/env python3
"""
Export data snapshot from remote Oracle server to CSV for backtesting.

Usage:
    python scripts/export_backtest_data.py --start 2026-01-20 --end "2026-01-30 16:00:00"
    python scripts/export_backtest_data.py --start 2026-01-25 --end 2026-01-30
    python scripts/export_backtest_data.py --list  # Show available data range
"""

import os
import sys
import argparse
import subprocess
from datetime import datetime

# Remote server config - from environment
REMOTE_HOST = os.getenv('REMOTE_HOST', 'ubuntu@your-server-ip')
SSH_KEY = os.path.expanduser(os.getenv('SSH_KEY_PATH', '~/.ssh/id_rsa'))
DB_NAME = os.getenv('DB_NAME', 'oparli_database')
DB_USER = os.getenv('DB_USER', 'oparli_admin')
DB_PASS = os.getenv('DB_PASS')


def run_remote_sql(query: str) -> str:
    """Run SQL query on remote server and return output"""
    cmd = [
        "ssh", "-i", SSH_KEY, REMOTE_HOST,
        f'PGPASSWORD={DB_PASS} psql -U {DB_USER} -h localhost -d {DB_NAME} -t -c "{query}"'
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: {result.stderr}")
        return ""
    return result.stdout.strip()


def list_available_data():
    """Show available data range on remote server"""
    print("Checking remote database...")

    query = "SELECT symbol, MIN(timestamp), MAX(timestamp), COUNT(*) FROM ohlcv_1min GROUP BY symbol"
    output = run_remote_sql(query)

    print("\nAvailable data on Oracle server:")
    print("-" * 60)
    for line in output.split('\n'):
        if line.strip():
            parts = [p.strip() for p in line.split('|')]
            if len(parts) == 4:
                symbol, min_ts, max_ts, count = parts
                print(f"  {symbol}: {min_ts} to {max_ts} ({count} bars)")
    print("-" * 60)


def export_data(start_date: str, end_date: str, symbol: str = 'XAUUSD', output_dir: str = 'data/snapshots'):
    """Export OHLCV data from remote server to local CSV"""

    print(f"Exporting {symbol} from {start_date} to {end_date}...")

    # Create output filename
    start_str = start_date.replace('-', '')[:8]
    end_str = end_date.replace('-', '').replace(' ', '_').replace(':', '')[:8]
    filename = f"{symbol}_{start_str}_{end_str}.csv"
    filepath = os.path.join(output_dir, filename)

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # Build COPY query
    query = f"""COPY (
        SELECT timestamp, open, high, low, close, volume
        FROM ohlcv_1min
        WHERE symbol = '{symbol}'
          AND timestamp >= '{start_date}'
          AND timestamp <= '{end_date}'
        ORDER BY timestamp
    ) TO STDOUT WITH CSV HEADER"""

    # Run remote query and save to local file
    cmd = [
        "ssh", "-i", SSH_KEY, REMOTE_HOST,
        f'PGPASSWORD={DB_PASS} psql -U {DB_USER} -h localhost -d {DB_NAME} -c "{query}"'
    ]

    with open(filepath, 'w') as f:
        result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, text=True)

    if result.returncode != 0:
        print(f"Error: {result.stderr}")
        return None

    # Count rows
    with open(filepath, 'r') as f:
        row_count = sum(1 for _ in f) - 1  # Subtract header

    print(f"\n✓ Exported {row_count} bars to {filepath}")

    return filepath


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Export backtest data from Oracle server')
    parser.add_argument('--start', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', help='End date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)')
    parser.add_argument('--symbol', default='XAUUSD', help='Symbol to export')
    parser.add_argument('--output', default='data/snapshots', help='Output directory')
    parser.add_argument('--list', action='store_true', help='List available data range')

    args = parser.parse_args()

    if args.list:
        list_available_data()
    elif args.start and args.end:
        export_data(args.start, args.end, args.symbol, args.output)
    else:
        print("Usage:")
        print("  python scripts/export_backtest_data.py --list")
        print("  python scripts/export_backtest_data.py --start 2026-01-20 --end 2026-01-30")