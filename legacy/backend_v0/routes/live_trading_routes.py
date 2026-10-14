# backend/routes/live_trading_routes.py
"""
Optimized Live Trading API Routes
"""
from fastapi import APIRouter, HTTPException
from typing import Optional
from pydantic import BaseModel
import logging

from config.database import get_database
from live_trading import trading_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/live", tags=["live-trading"])


# ============================================================
# Request Models
# ============================================================

class OrderRequest(BaseModel):
    symbol: str
    type: str  # BUY or SELL
    volume: float
    stopLoss: Optional[float] = None
    takeProfit: Optional[float] = None


class ClosePositionRequest(BaseModel):
    position_id: str
    exit_price: Optional[float] = None


# ============================================================
# Routes
# ============================================================

@router.post("/connect")
async def connect_trading():
    """Connect to live trading server"""
    try:
        success = trading_manager.connect()
        if success:
            account_info = trading_manager.get_account_info()
            return {
                "status": "connected",
                "account": account_info
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to connect")
    except Exception as e:
        logger.error(f"Connection error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/disconnect")
async def disconnect_trading():
    """Disconnect from live trading"""
    try:
        success = trading_manager.disconnect()
        if success:
            return {"status": "disconnected"}
        else:
            raise HTTPException(status_code=500, detail="Failed to disconnect")
    except Exception as e:
        logger.error(f"Disconnect error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/account")
async def get_account():
    """Get account information"""
    try:
        account_info = trading_manager.get_account_info()
        return account_info
    except Exception as e:
        logger.error(f"Error getting account: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/order")
async def place_order(order: OrderRequest):
    """
    Place a new order (optimized version)

    Features:
    - Position validation
    - Margin checks
    - Risk management
    - Multiple concurrent positions
    """
    try:
        # Get current market price from database
        db = get_database()
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT close::FLOAT
                FROM ohlcv_1min
                WHERE symbol = %s
                ORDER BY timestamp DESC LIMIT 1
            """, (order.symbol.upper(),))

            row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail=f"No data for {order.symbol}")

        current_price = float(row['close'])

        # Place order using trading manager
        success, message, position = trading_manager.place_order(
            symbol=order.symbol.upper(),
            order_type=order.type,
            volume=order.volume,
            current_price=current_price,
            stop_loss=order.stopLoss,
            take_profit=order.takeProfit
        )

        if not success:
            raise HTTPException(status_code=400, detail=message)

        return {
            "status": "success",
            "message": message,
            "position": position.dict() if position else None,
            "account": trading_manager.get_account_info()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error placing order: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/positions")
async def get_positions():
    """
    Get all open positions with real-time updates

    Features:
    - Real-time price updates
    - Automatic SL/TP checks
    - Live P&L calculation
    """
    try:
        positions = trading_manager.get_all_positions()

        # Update each position with latest price
        for position in positions:
            result = db.execute(text("""
                SELECT close::FLOAT
                FROM ohlcv_1min
                WHERE symbol = :symbol
                ORDER BY timestamp DESC LIMIT 1
            """), {'symbol': position.symbol})

            row = result.fetchone()
            if row:
                current_price = float(row.close)
                trading_manager.update_position(position.id, current_price)

        # Get updated positions
        positions = trading_manager.get_all_positions()

        return {
            "positions": [p.dict() for p in positions],
            "count": len(positions),
            "account": trading_manager.get_account_info()
        }

    except Exception as e:
        logger.error(f"Error getting positions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/position/{position_id}")
async def get_position(position_id: str):
    """Get specific position by ID"""
    try:
        positions = trading_manager.get_all_positions()
        position = next((p for p in positions if p.id == position_id), None)

        if not position:
            raise HTTPException(status_code=404, detail="Position not found")

        # Update with latest price
        result = db.execute(text("""
            SELECT close::FLOAT
            FROM ohlcv_1min
            WHERE symbol = :symbol
            ORDER BY timestamp DESC LIMIT 1
        """), {'symbol': position.symbol})

        row = result.fetchone()
        if row:
            current_price = float(row.close)
            trading_manager.update_position(position_id, current_price)

        # Get updated position
        positions = trading_manager.get_all_positions()
        position = next((p for p in positions if p.id == position_id), None)

        return {"position": position.dict() if position else None}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting position: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/close-position")
async def close_position(request: ClosePositionRequest):
    """
    Close a position

    Features:
    - Validate position exists
    - Get current market price
    - Calculate final P&L
    - Update account balance
    """
    try:
        # Get current price if not provided
        if request.exit_price is None:
            positions = trading_manager.get_all_positions()
            position = next((p for p in positions if p.id == request.position_id), None)

            if not position:
                raise HTTPException(status_code=404, detail="Position not found")

            result = db.execute(text("""
                SELECT close::FLOAT
                FROM ohlcv_1min
                WHERE symbol = :symbol
                ORDER BY timestamp DESC LIMIT 1
            """), {'symbol': position.symbol})

            row = result.fetchone()
            if row:
                exit_price = float(row.close)
            else:
                exit_price = position.currentPrice
        else:
            exit_price = request.exit_price

        # Close position
        success, message, trade = trading_manager.close_position(
            position_id=request.position_id,
            exit_price=exit_price
        )

        if not success:
            raise HTTPException(status_code=400, detail=message)

        return {
            "status": "success",
            "message": message,
            "trade": trade.dict() if trade else None,
            "account": trading_manager.get_account_info()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error closing position: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/close-all")
async def close_all_positions():
    """Close all open positions"""
    try:
        positions = trading_manager.get_all_positions()
        closed_trades = []

        for position in positions:
            # Get current price
            result = db.execute(text("""
                SELECT close::FLOAT
                FROM ohlcv_1min
                WHERE symbol = :symbol
                ORDER BY timestamp DESC LIMIT 1
            """), {'symbol': position.symbol})

            row = result.fetchone()
            exit_price = float(row.close) if row else position.currentPrice

            # Close position
            success, message, trade = trading_manager.close_position(
                position_id=position.id,
                exit_price=exit_price
            )

            if success and trade:
                closed_trades.append(trade.dict())

        return {
            "status": "success",
            "closed_count": len(closed_trades),
            "trades": closed_trades,
            "account": trading_manager.get_account_info()
        }

    except Exception as e:
        logger.error(f"Error closing all positions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trades")
async def get_trades(limit: int = 50):
    """Get recent closed trades"""
    try:
        trades = trading_manager.get_recent_trades(limit=limit)
        return {
            "trades": [t.dict() for t in trades],
            "count": len(trades)
        }
    except Exception as e:
        logger.error(f"Error getting trades: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_trading_stats():
    """Get trading statistics"""
    try:
        account = trading_manager.get_account_info()
        trades = trading_manager.get_recent_trades(limit=1000)

        winning_trades = [t for t in trades if t.pnl > 0]
        losing_trades = [t for t in trades if t.pnl < 0]

        total_profit = sum(t.pnl for t in winning_trades)
        total_loss = abs(sum(t.pnl for t in losing_trades))

        return {
            "account": account,
            "statistics": {
                "total_trades": len(trades),
                "winning_trades": len(winning_trades),
                "losing_trades": len(losing_trades),
                "win_rate": round((len(winning_trades) / len(trades) * 100) if trades else 0, 2),
                "profit_factor": round(total_profit / total_loss if total_loss > 0 else 0, 2),
                "total_profit": round(total_profit, 2),
                "total_loss": round(total_loss, 2),
                "average_win": round(total_profit / len(winning_trades) if winning_trades else 0, 2),
                "average_loss": round(total_loss / len(losing_trades) if losing_trades else 0, 2),
            }
        }

    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))
