# backend/main.py
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from apscheduler.schedulers.background import BackgroundScheduler
from typing import Dict, List, Optional, Annotated
from pydantic import BaseModel, Field, field_validator
from datetime import datetime
import logging
import json
import io
import os

# Import database connection
from config.database import get_database

# Import routes
from routes.live_trading_routes import router as live_trading_router
from routes.data_management_routes import router as data_management_router
from routes.auth_routes import router as auth_router
from routes.dashboard_routes import router as dashboard_router
from routes.backtest_routes import router as backtest_router
from routes.regime_routes import router as regime_router
from routes.validation_routes import router as validation_router

# Import streaming service
from services.market_data_streamer import get_streamer

# Import security middleware
from middleware.security import (
    APIKeyMiddleware,
    SecurityHeadersMiddleware,
    RateLimitMiddleware,
    validate_symbol,
    validate_limit,
    validate_timeframe
)






# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(title="algo_bot_api", version="1.0.0")

# Include routers
app.include_router(live_trading_router)
app.include_router(data_management_router)
app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(backtest_router)
app.include_router(regime_router)
app.include_router(validation_router)






# ============================================================
# Security Middleware (order matters - applied in reverse)
# ============================================================

# 1. Security Headers (applied last, adds headers to all responses)
app.add_middleware(SecurityHeadersMiddleware)

# 2. Rate Limiting (100 req/min regular, 10 req/min for admin)
app.add_middleware(RateLimitMiddleware, requests_per_minute=100, admin_requests_per_minute=10)

# 3. API Key Authentication (checks all requests)
app.add_middleware(APIKeyMiddleware)

# 4. CORS (Allow frontend to call API - restricted to production domain)
ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS', 'https://oparli.com').split(',')

# Add localhost for development
if os.getenv('ENV', 'production') == 'development':
    ALLOWED_ORIGINS.extend([
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
    ])

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization"],
)

# ============================================================
# Global Variables
# ============================================================
active_websockets: Dict[str, List[WebSocket]] = {}
scheduler = BackgroundScheduler()

# Trading state
live_trading_state = {
    "connected": False,
    "position": None,
    "balance": 10000.0,
    "trades": []
}

# ============================================================
# Pydantic Models
# ============================================================
class OrderRequest(BaseModel):
    symbol: str
    type: str  # BUY or SELL
    volume: float
    stopLoss: Optional[float] = None
    takeProfit: Optional[float] = None

class BacktestRequest(BaseModel):
    symbol: str
    strategy: str
    startDate: Optional[str] = None
    endDate: Optional[str] = None
    initialBalance: float = 10000.0
    riskPerTrade: float = 2.0



@app.on_event("startup")
async def startup_event():
    logger.info(" Starting API...")

    # Initialize database connection pool
    from config.database import init_database
    if not init_database():
        logger.error("Failed to initialize database connection pool")
    else:
        logger.info("✓ Database connection pool initialized")

    # Start scheduler for refreshing materialized views
    scheduler.add_job(
        func=refresh_materialized_views,
        trigger='interval',
        minutes=5,
        id='refresh_views',
        replace_existing=True
    )
    scheduler.start()
    logger.info("✓ Scheduler started (refreshing views every 5 min)")

    # Auto-start market data streamer for live WebSocket updates
    try:
        streamer = get_streamer()
        if streamer.start():
            logger.info("✓ Market data streamer started")
            # Subscribe to XAUUSD commodity (London Gold)
            if streamer.subscribe_symbol(
                symbol='XAUUSD',
                exchange='SMART',
                sec_type='CMDTY',
                currency='USD',
                bar_size=5
            ):
                logger.info("✓ Subscribed to XAUUSD")
            else:
                logger.warning("Failed to subscribe to XAUUSD")
        else:
            logger.warning("Failed to start market data streamer (TWS may not be available)")
    except Exception as e:
        logger.warning(f"Market data streamer not available: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down...")

    # Close database connection pool
    from config.database import get_database
    db = get_database()
    db.close_pool()

    scheduler.shutdown()
    logger.info("✓ Scheduler stopped")

# ============================================================
# Helper Functions
# ============================================================
def refresh_materialized_views():
    """Refresh all materialized views"""
    try:
        db = get_database()
        logger.info(" Refreshing materialized views...")
        with db.get_cursor() as cursor:
            cursor.execute("SELECT refresh_all_timeframes()")
        logger.info("✓ Materialized views refreshed")
    except Exception as e:
        logger.error(f" Error refreshing views: {e}")

# ============================================================
# API Endpoints
# ============================================================

@app.get("/")
async def root():
    """Health check"""
    return {
        "status": "running",
        "message": "API is online",
        "version": "1.0.0"
    }

@app.get("/api/chart-data/{symbol}/{timeframe}")
async def get_chart_data(
    symbol: str,
    timeframe: str,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
    before: Annotated[Optional[int], Query(ge=0)] = None
):
    """
    Fetch OHLCV data for chart

    Parameters:
    - symbol: XAUUSD, ES, NQ, etc. (max 20 chars, alphanumeric)
    - timeframe: 1min, 5min, 15min, 30min, 1H, 4H, 1D
    - limit: Number of bars (1-5000, default 1000)
    - before: unix seconds (UTC); return only bars strictly BEFORE this time.
      Enables infinite scroll-back: pass the oldest loaded bar's time to page older history.
    """
    try:
        # Validate inputs
        try:
            symbol = validate_symbol(symbol)
            timeframe = validate_timeframe(timeframe)
            limit = validate_limit(limit, max_limit=5000)
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))

        db = get_database()

        # Map timeframe to view (whitelist approach - safe from SQL injection)
        view_map = {
            '1min': 'ohlcv_1min',
            '5min': 'ohlcv_5min',
            '15min': 'ohlcv_15min',
            '30min': 'ohlcv_30min',
            '1H': 'ohlcv_1h',
            '4H': 'ohlcv_4h',
            '1D': 'ohlcv_1d'
        }

        view_name = view_map[timeframe]  # Safe - timeframe already validated

        # Query database using parameterized query; optional `before` cursor for paging
        before_clause = "AND timestamp < to_timestamp(%s) AT TIME ZONE 'UTC'" if before is not None else ""
        query = f"""
            SELECT
                EXTRACT(EPOCH FROM timestamp AT TIME ZONE 'UTC')::INTEGER as time,
                open::FLOAT as open,
                high::FLOAT as high,
                low::FLOAT as low,
                close::FLOAT as close,
                volume::BIGINT as volume
            FROM {view_name}
            WHERE symbol = %s
            {before_clause}
            ORDER BY timestamp DESC
            LIMIT %s
        """

        params = (symbol, before, limit) if before is not None else (symbol, limit)
        with db.get_cursor() as cursor:
            cursor.execute(query, params)
            result = cursor.fetchall()

        # Convert to list
        bars = []
        for row in result:
            bars.append({
                'time': row['time'],
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': int(row['volume'])
            })

        # Reverse to chronological order
        bars.reverse()

        logger.info(f"Fetched {len(bars)} {timeframe} bars for {symbol}")

        return {
            'symbol': symbol,
            'timeframe': timeframe,
            'count': len(bars),
            'bars': bars
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Database error fetching chart data: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch chart data")

@app.get("/api/symbols")
async def get_symbols():
    """Get available symbols"""
    try:
        db = get_database()
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT symbol
                FROM ohlcv_1min
                ORDER BY symbol
            """)
            result = cursor.fetchall()
        symbols = [row['symbol'] for row in result]
        return {"symbols": symbols}
    except Exception as e:
        logger.error(f"Database error fetching symbols: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch symbols")

@app.get("/api/latest/{symbol}")
async def get_latest_bar(symbol: str):
    """Get latest bar for a symbol"""
    try:
        # Validate symbol
        try:
            symbol = validate_symbol(symbol)
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))

        db = get_database()
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    EXTRACT(EPOCH FROM timestamp)::INTEGER as time,
                    open::FLOAT, high::FLOAT, low::FLOAT,
                    close::FLOAT, volume::BIGINT
                FROM ohlcv_1min
                WHERE symbol = %s
                ORDER BY timestamp DESC LIMIT 1
            """, (symbol,))

            row = cursor.fetchone()

        if row:
            return {
                'symbol': symbol,
                'time': row['time'],
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': int(row['volume'])
            }
        raise HTTPException(status_code=404, detail=f"No data for symbol: {symbol}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Database error fetching latest bar: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch latest bar")

@app.get("/api/stats")
async def get_stats():
    """Database statistics"""
    try:
        db = get_database()
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    symbol,
                    COUNT(*) as bar_count,
                    MIN(timestamp) as first_bar,
                    MAX(timestamp) as last_bar
                FROM ohlcv_historical_1min
                GROUP BY symbol
                ORDER BY symbol
            """)
            result = cursor.fetchall()

        stats = []
        for row in result:
            stats.append({
                'symbol': row['symbol'],
                'bars': row['bar_count'],
                'first': str(row['first_bar']),
                'last': str(row['last_bar'])
            })

        return {'stats': stats}
    except Exception as e:
        logger.error(f"Database error fetching stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch statistics")

# ============================================================
# WebSocket
# ============================================================

@app.websocket("/ws/live-data/{symbol}")
async def websocket_live_data(websocket: WebSocket, symbol: str):
    """
    WebSocket for real-time market data

    Receives data from the market data streamer (TWS -> PostgreSQL) and
    broadcasts it to connected clients.
    """
    await websocket.accept()

    symbol = symbol.upper()
    if symbol not in active_websockets:
        active_websockets[symbol] = []
    active_websockets[symbol].append(websocket)

    logger.info(f"✓ WebSocket connected for {symbol}")

    # Create callback for this websocket
    async def broadcast_to_client(sym: str, data: dict):
        """Broadcast data to this WebSocket client"""
        if sym == symbol:
            try:
                await websocket.send_json(data)
            except Exception as e:
                logger.error(f"Error sending to WebSocket: {e}")

    # Register callback with streamer
    streamer = get_streamer()
    streamer.register_websocket_callback(broadcast_to_client)

    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        logger.info(f"✗ WebSocket disconnected for {symbol}")
    finally:
        # Unregister callback
        streamer.unregister_websocket_callback(broadcast_to_client)

        if symbol in active_websockets:
            active_websockets[symbol].remove(websocket)

# ============================================================
# Streaming Control
# ============================================================

@app.post("/api/streaming/start")
async def start_streaming():
    """Start the market data streaming service"""
    try:
        streamer = get_streamer()

        # Check if already running
        if streamer.is_running:
            logger.info("Streaming service already running")
            return {
                "status": "success",
                "message": "Streaming service already running",
                "details": streamer.get_status()
            }

        success = streamer.start()

        if success:
            return {
                "status": "success",
                "message": "Streaming service started",
                "details": streamer.get_status()
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to start streaming")

    except Exception as e:
        logger.error(f"Error starting streaming: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/streaming/stop")
async def stop_streaming():
    """Stop the market data streaming service"""
    try:
        streamer = get_streamer()
        streamer.stop()
        return {"status": "success", "message": "Streaming service stopped"}
    except Exception as e:
        logger.error(f"Error stopping streaming: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/streaming/subscribe/{symbol}")
async def subscribe_symbol(
    symbol: str,
    bar_size: int = 5,
    exchange: str = 'SMART',
    sec_type: str = 'STK',
    currency: str = 'USD'
):
    """
    Subscribe to real-time data for a symbol

    Args:
        symbol: Trading symbol (e.g., AAPL, XAUUSD)
        bar_size: Bar size in seconds (default 5)
        exchange: Exchange (default SMART)
        sec_type: Security type - STK, CMDTY, FUT (default STK)
        currency: Currency (default USD)
    """
    try:
        streamer = get_streamer()

        if not streamer.is_running:
            raise HTTPException(
                status_code=400,
                detail="Streaming service not running. Start it first with /api/streaming/start"
            )

        success = streamer.subscribe_symbol(
            symbol=symbol.upper(),
            exchange=exchange,
            sec_type=sec_type,
            currency=currency,
            bar_size=bar_size
        )

        if success:
            return {
                "status": "success",
                "message": f"Subscribed to {symbol.upper()}",
                "symbol": symbol.upper(),
                "bar_size": bar_size
            }
        else:
            raise HTTPException(status_code=400, detail=f"Failed to subscribe to {symbol}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error subscribing to {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/streaming/unsubscribe/{symbol}")
async def unsubscribe_symbol(symbol: str):
    """Unsubscribe from a symbol"""
    try:
        streamer = get_streamer()
        success = streamer.unsubscribe_symbol(symbol.upper())

        if success:
            return {
                "status": "success",
                "message": f"Unsubscribed from {symbol.upper()}"
            }
        else:
            raise HTTPException(status_code=400, detail=f"Not subscribed to {symbol}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error unsubscribing from {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/streaming/status")
async def get_streaming_status():
    """Get streaming service status"""
    try:
        streamer = get_streamer()
        return streamer.get_status()
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Admin
# ============================================================

@app.post("/api/admin/refresh-views")
async def manual_refresh():
    """Manually refresh materialized views (admin only)"""
    try:
        refresh_materialized_views()
        return {"status": "success", "message": "Views refreshed"}
    except Exception as e:
        logger.error(f"Error refreshing views: {e}")
        raise HTTPException(status_code=500, detail="Failed to refresh views")

# ============================================================
# Run
# ============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8001,
        reload=True,
        log_level="info"
    )