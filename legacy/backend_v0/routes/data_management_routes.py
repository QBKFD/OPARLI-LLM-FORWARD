# backend/routes/data_management_routes.py
"""
Data Management API Routes

Endpoints for gap detection and historical data backfill
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, Dict, List
from datetime import datetime, timedelta
import logging

from services.gap_detector import get_gap_detector
from services.historical_backfill import get_backfiller

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/data", tags=["data-management"])


# Request/Response Models
class GapScanRequest(BaseModel):
    symbol: Optional[str] = None  # If None, scan all symbols
    days: int = 7  # How many days back to scan
    min_gap_minutes: int = 5  # Minimum gap size to report


class BackfillRequest(BaseModel):
    symbol: str
    sec_type: str = 'CMDTY'  # STK, FUT, CMDTY
    exchange: str = 'SMART'
    currency: str = 'USD'
    min_gap_minutes: int = 5


class BackfillRangeRequest(BaseModel):
    symbol: str
    start_date: str  # YYYY-MM-DD
    end_date: str    # YYYY-MM-DD
    sec_type: str = 'CMDTY'
    exchange: str = 'SMART'
    currency: str = 'USD'


class GapScanResponse(BaseModel):
    symbol: str
    gaps: List[Dict]
    total_gaps: int
    total_missing_bars: int


class BackfillResponse(BaseModel):
    symbol: str
    status: str
    gaps_found: int
    gaps_filled: int
    bars_backfilled: int
    message: str


# Endpoints
@router.get("/gaps/scan")
async def scan_for_gaps(
    symbol: Optional[str] = None,
    days: int = 7,
    min_gap_minutes: int = 5
):
    """
    Scan for data gaps

    Query params:
    - symbol: Specific symbol to scan (optional, scans all if not provided)
    - days: Number of days to look back (default: 7)
    - min_gap_minutes: Minimum gap size to report (default: 5)

    Returns:
        List of gaps found per symbol
    """
    try:
        detector = get_gap_detector()

        if symbol:
            # Scan specific symbol
            end_time = datetime.now()
            start_time = end_time - timedelta(days=days)
            gaps = detector.find_gaps(symbol, start_time, end_time, min_gap_minutes)

            total_missing = sum(gap['bars_missing'] for gap in gaps)

            return {
                "symbol": symbol,
                "gaps": gaps,
                "total_gaps": len(gaps),
                "total_missing_bars": total_missing,
                "scan_range": {
                    "start": start_time.isoformat(),
                    "end": end_time.isoformat(),
                    "days": days
                }
            }
        else:
            # Scan all symbols
            all_gaps = detector.scan_all_symbols(min_gap_minutes)

            results = []
            for sym, sym_gaps in all_gaps.items():
                total_missing = sum(gap['bars_missing'] for gap in sym_gaps)
                results.append({
                    "symbol": sym,
                    "gaps": sym_gaps,
                    "total_gaps": len(sym_gaps),
                    "total_missing_bars": total_missing
                })

            return {
                "symbols_scanned": len(results),
                "symbols_with_gaps": len(results),
                "gaps": results,
                "scan_range_days": days
            }

    except Exception as e:
        logger.error(f"Error scanning for gaps: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/coverage/{symbol}")
async def get_data_coverage(symbol: str, days: int = 7):
    """
    Get data coverage statistics for a symbol

    Path params:
    - symbol: Trading symbol

    Query params:
    - days: Number of days to analyze (default: 7)

    Returns:
        Coverage statistics (percentage, bar counts, etc.)
    """
    try:
        detector = get_gap_detector()
        coverage = detector.get_data_coverage(symbol, days)

        if not coverage or coverage.get('bar_count', 0) == 0:
            raise HTTPException(
                status_code=404,
                detail=f"No data found for symbol {symbol}"
            )

        return coverage

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting coverage for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/backfill")
def backfill_gaps(request: BackfillRequest):
    """
    Backfill data gaps for a symbol

    This connects to TWS and requests historical data to fill gaps.

    Request body:
    - symbol: Trading symbol (e.g., 'XAUUSD')
    - sec_type: Security type (STK, FUT, CMDTY)
    - exchange: Exchange
    - currency: Currency
    - min_gap_minutes: Minimum gap size to backfill

    Returns:
        Backfill results (gaps filled, bars added)
    """
    try:
        logger.info(f"Starting backfill for {request.symbol}")

        backfiller = get_backfiller()

        # Connect to TWS
        if not backfiller.connect():
            raise HTTPException(
                status_code=503,
                detail="Could not connect to TWS. Make sure TWS/IB Gateway is running."
            )

        try:
            # Perform backfill
            result = backfiller.backfill_symbol(
                symbol=request.symbol,
                sec_type=request.sec_type,
                exchange=request.exchange,
                currency=request.currency,
                min_gap_minutes=request.min_gap_minutes
            )

            # Disconnect
            backfiller.disconnect()

            if 'error' in result:
                raise HTTPException(status_code=500, detail=result['error'])

            return {
                "symbol": request.symbol,
                "status": "completed",
                "gaps_found": result['gaps_found'],
                "gaps_filled": result['gaps_filled'],
                "bars_backfilled": result['bars_backfilled'],
                "message": f"Successfully backfilled {result['bars_backfilled']} bars for {request.symbol}"
            }

        finally:
            # Always disconnect
            backfiller.disconnect()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during backfill: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cleanup/invalid-bars")
async def cleanup_invalid_bars():
    """
    Remove bars with invalid data (zero/negative/unrealistic prices)

    This will delete:
    - Bars with zero or negative OHLC values
    - Bars with unrealistic gold prices (< $1000 or > $10000)
    - Bars with invalid OHLC relationships (high < low, etc.)

    Returns:
        Number of bars deleted and remaining bars
    """
    try:
        from config.database import get_database
        db = get_database()

        with db.get_cursor() as cursor:
            # Count bad bars before deletion
            count_query = """
                SELECT COUNT(*) as count FROM ohlcv_realtime_1min
                WHERE
                    open <= 0 OR high <= 0 OR low <= 0 OR close <= 0
                    OR open < 1000 OR open > 10000
                    OR high < 1000 OR high > 10000
                    OR low < 1000 OR low > 10000
                    OR close < 1000 OR close > 10000
                    OR high < low
                    OR high < open
                    OR high < close
                    OR low > open
                    OR low > close
            """
            cursor.execute(count_query)
            bad_count = cursor.fetchone()['count']

            if bad_count == 0:
                logger.info("No invalid bars found")
                cursor.execute("SELECT COUNT(*) as total FROM ohlcv_realtime_1min")
                total = cursor.fetchone()['total']
                return {
                    "status": "clean",
                    "deleted": 0,
                    "remaining": total,
                    "message": "No invalid bars found"
                }

            # Get examples of bad data before deleting
            example_query = """
                SELECT symbol, timestamp, open, high, low, close
                FROM ohlcv_realtime_1min
                WHERE
                    open <= 0 OR high <= 0 OR low <= 0 OR close <= 0
                    OR open < 1000 OR open > 10000
                    OR high < 1000 OR high > 10000
                    OR low < 1000 OR low > 10000
                    OR close < 1000 OR close > 10000
                LIMIT 3
            """
            cursor.execute(example_query)
            examples = cursor.fetchall()

            logger.warning(f"Found {bad_count} invalid bars. Examples:")
            for row in examples:
                logger.warning(f"  {row['symbol']} @ {row['timestamp']}: O={row['open']} H={row['high']} L={row['low']} C={row['close']}")

            # Delete invalid bars
            delete_query = """
                DELETE FROM ohlcv_realtime_1min
                WHERE
                    open <= 0 OR high <= 0 OR low <= 0 OR close <= 0
                    OR open < 1000 OR open > 10000
                    OR high < 1000 OR high > 10000
                    OR low < 1000 OR low > 10000
                    OR close < 1000 OR close > 10000
                    OR high < low
                    OR high < open
                    OR high < close
                    OR low > open
                    OR low > close
            """
            cursor.execute(delete_query)

            # Count remaining bars
            cursor.execute("SELECT COUNT(*) as total FROM ohlcv_realtime_1min")
            remaining = cursor.fetchone()['total']

            logger.info(f"✅ Deleted {bad_count} invalid bars. {remaining} valid bars remain.")

            return {
                "status": "cleaned",
                "deleted": bad_count,
                "remaining": remaining,
                "examples": examples,
                "message": f"Successfully deleted {bad_count} invalid bars"
            }

    except Exception as e:
        logger.error(f"Error cleaning up invalid bars: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/backfill/range")
def backfill_date_range(request: BackfillRangeRequest):
    """
    Fetch historical data for a specific date range from IB Gateway.

    Runs alongside the real-time streamer (uses separate client ID).

    Request body:
    - symbol: Trading symbol (e.g. 'XAUUSD')
    - start_date: Start date 'YYYY-MM-DD'
    - end_date: End date 'YYYY-MM-DD'
    - sec_type: Security type (default: CMDTY)
    - exchange: Exchange (default: SMART)
    """
    try:
        logger.info(f"Starting range backfill for {request.symbol}: {request.start_date} to {request.end_date}")

        backfiller = get_backfiller()

        result = backfiller.backfill_range(
            symbol=request.symbol,
            start_date=request.start_date,
            end_date=request.end_date,
            sec_type=request.sec_type,
            exchange=request.exchange,
            currency=request.currency
        )

        # Disconnect after done
        backfiller.disconnect()

        if 'error' in result:
            raise HTTPException(status_code=500, detail=result['error'])

        return {
            "status": "completed",
            **result,
            "message": f"Backfilled {result['bars_backfilled']} bars for {request.symbol} ({request.start_date} to {request.end_date})"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during range backfill: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_database_stats():
    """
    Get overall database statistics

    Returns:
        - Total bars stored
        - Symbols in database
        - Date range
        - Storage size
    """
    try:
        from config.database import get_database
        db = get_database()

        with db.get_cursor() as cursor:
            # Total bars
            cursor.execute("SELECT COUNT(*) as total FROM ohlcv_realtime_1min")
            total_bars = cursor.fetchone()['total']

            # Symbols
            cursor.execute("SELECT COUNT(DISTINCT symbol) as count FROM ohlcv_realtime_1min")
            symbol_count = cursor.fetchone()['count']

            # Date range
            cursor.execute("""
                SELECT
                    MIN(timestamp) as earliest,
                    MAX(timestamp) as latest
                FROM ohlcv_realtime_1min
            """)
            date_range = cursor.fetchone()

            # Per-symbol stats
            cursor.execute("""
                SELECT
                    symbol,
                    COUNT(*) as bar_count,
                    MIN(timestamp) as first_bar,
                    MAX(timestamp) as last_bar
                FROM ohlcv_realtime_1min
                GROUP BY symbol
                ORDER BY bar_count DESC
            """)
            symbols = cursor.fetchall()

        return {
            "total_bars": total_bars,
            "symbol_count": symbol_count,
            "date_range": {
                "earliest": date_range['earliest'],
                "latest": date_range['latest']
            },
            "symbols": symbols
        }

    except Exception as e:
        logger.error(f"Error getting database stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))
