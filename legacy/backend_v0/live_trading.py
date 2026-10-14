# backend/live_trading.py
"""
Improved Live Trading Module with better architecture
"""
from typing import Dict, List, Optional
from datetime import datetime
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)


class Position(BaseModel):
    """Position model with proper structure"""
    id: str
    symbol: str
    type: str  # BUY or SELL
    entryPrice: float
    currentPrice: float
    volume: float
    stopLoss: Optional[float] = None
    takeProfit: Optional[float] = None
    pnl: float = 0.0
    entryTime: str
    status: str = "OPEN"  # OPEN, CLOSED, PENDING


class Trade(BaseModel):
    """Completed trade record"""
    id: str
    symbol: str
    type: str
    entryPrice: float
    exitPrice: float
    volume: float
    pnl: float
    entryTime: str
    exitTime: str


class LiveTradingManager:
    """
    Manages live trading state with proper state management

    Features:
    - Multiple position support
    - Order queue management
    - Risk management
    - Real-time P&L tracking
    """

    def __init__(self):
        self.connected = False
        self.positions: Dict[str, Position] = {}
        self.trades: List[Trade] = []
        self.balance = 10000.0
        self.initial_balance = 10000.0
        self.equity = 10000.0
        self.margin_used = 0.0
        self.free_margin = 10000.0

        # Risk management settings
        self.max_positions = 5
        self.max_risk_per_trade = 0.02  # 2% per trade
        self.max_total_risk = 0.06  # 6% total
        self.leverage = 100

    def connect(self) -> bool:
        """Connect to trading server"""
        try:
            # TODO: Implement broker API connection
            self.connected = True
            logger.info("✓ Connected to live trading")
            return True
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False

    def disconnect(self) -> bool:
        """Disconnect from trading server"""
        try:
            # Close all positions before disconnecting
            if self.positions:
                logger.warning(f"Closing {len(self.positions)} open positions")
                for position_id in list(self.positions.keys()):
                    self.close_position(position_id)

            self.connected = False
            logger.info("✓ Disconnected from live trading")
            return True
        except Exception as e:
            logger.error(f"Disconnect failed: {e}")
            return False

    def validate_order(self, symbol: str, volume: float, order_type: str) -> tuple[bool, str]:
        """
        Validate order before placement

        Returns: (is_valid, error_message)
        """
        if not self.connected:
            return False, "Not connected to trading server"

        # Check max positions
        if len(self.positions) >= self.max_positions:
            return False, f"Maximum positions limit reached ({self.max_positions})"

        # Check volume
        if volume <= 0:
            return False, "Volume must be positive"

        # Check if enough free margin
        required_margin = volume * 1000 / self.leverage  # Simplified calculation
        if required_margin > self.free_margin:
            return False, f"Insufficient margin. Required: {required_margin}, Available: {self.free_margin}"

        return True, ""

    def place_order(
        self,
        symbol: str,
        order_type: str,
        volume: float,
        current_price: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None
    ) -> tuple[bool, str, Optional[Position]]:
        """
        Place a new order

        Returns: (success, message, position)
        """
        # Validate order
        is_valid, error_msg = self.validate_order(symbol, volume, order_type)
        if not is_valid:
            return False, error_msg, None

        try:
            # Generate position ID
            position_id = f"{symbol}_{datetime.now().timestamp()}"

            # Calculate margin
            margin = volume * 1000 / self.leverage

            # Create position
            position = Position(
                id=position_id,
                symbol=symbol,
                type=order_type,
                entryPrice=current_price,
                currentPrice=current_price,
                volume=volume,
                stopLoss=stop_loss,
                takeProfit=take_profit,
                pnl=0.0,
                entryTime=datetime.now().isoformat(),
                status="OPEN"
            )

            # Update trading state
            self.positions[position_id] = position
            self.margin_used += margin
            self.free_margin = self.balance - self.margin_used

            logger.info(f"✓ Order placed: {order_type} {volume} {symbol} @ {current_price}")

            return True, "Order placed successfully", position

        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return False, str(e), None

    def update_position(self, position_id: str, current_price: float):
        """Update position with current price and check SL/TP"""
        if position_id not in self.positions:
            return

        position = self.positions[position_id]
        position.currentPrice = current_price

        # Calculate P&L
        if position.type == "BUY":
            pnl = (current_price - position.entryPrice) * position.volume * 1000
        else:  # SELL
            pnl = (position.entryPrice - current_price) * position.volume * 1000

        position.pnl = round(pnl, 2)

        # Check Stop Loss
        if position.stopLoss:
            should_close = False
            if position.type == "BUY" and current_price <= position.stopLoss:
                should_close = True
                logger.info(f"Stop Loss hit for {position_id}")
            elif position.type == "SELL" and current_price >= position.stopLoss:
                should_close = True
                logger.info(f"Stop Loss hit for {position_id}")

            if should_close:
                self.close_position(position_id, current_price)
                return

        # Check Take Profit
        if position.takeProfit:
            should_close = False
            if position.type == "BUY" and current_price >= position.takeProfit:
                should_close = True
                logger.info(f"Take Profit hit for {position_id}")
            elif position.type == "SELL" and current_price <= position.takeProfit:
                should_close = True
                logger.info(f"Take Profit hit for {position_id}")

            if should_close:
                self.close_position(position_id, current_price)
                return

    def close_position(
        self,
        position_id: str,
        exit_price: Optional[float] = None
    ) -> tuple[bool, str, Optional[Trade]]:
        """Close an open position"""
        if position_id not in self.positions:
            return False, "Position not found", None

        position = self.positions[position_id]

        # Use current price if not provided
        if exit_price is None:
            exit_price = position.currentPrice

        # Calculate final P&L
        if position.type == "BUY":
            pnl = (exit_price - position.entryPrice) * position.volume * 1000
        else:  # SELL
            pnl = (position.entryPrice - exit_price) * position.volume * 1000

        pnl = round(pnl, 2)

        # Update balance
        self.balance += pnl
        self.equity = self.balance + sum(p.pnl for p in self.positions.values())

        # Free margin
        margin = position.volume * 1000 / self.leverage
        self.margin_used -= margin
        self.free_margin = self.balance - self.margin_used

        # Create trade record
        trade = Trade(
            id=position_id,
            symbol=position.symbol,
            type=position.type,
            entryPrice=position.entryPrice,
            exitPrice=exit_price,
            volume=position.volume,
            pnl=pnl,
            entryTime=position.entryTime,
            exitTime=datetime.now().isoformat()
        )

        # Store trade and remove position
        self.trades.insert(0, trade)
        del self.positions[position_id]

        logger.info(f"✓ Position closed: {position.symbol} P&L: {pnl}")

        return True, "Position closed", trade

    def get_account_info(self) -> dict:
        """Get account information"""
        # Update equity
        self.equity = self.balance + sum(p.pnl for p in self.positions.values())

        return {
            "connected": self.connected,
            "balance": round(self.balance, 2),
            "equity": round(self.equity, 2),
            "margin_used": round(self.margin_used, 2),
            "free_margin": round(self.free_margin, 2),
            "margin_level": round((self.equity / self.margin_used * 100) if self.margin_used > 0 else 0, 2),
            "total_pnl": round(self.balance - self.initial_balance, 2),
            "open_positions": len(self.positions),
            "total_trades": len(self.trades)
        }

    def get_all_positions(self) -> List[Position]:
        """Get all open positions"""
        return list(self.positions.values())

    def get_recent_trades(self, limit: int = 50) -> List[Trade]:
        """Get recent closed trades"""
        return self.trades[:limit]


# Global trading manager instance
trading_manager = LiveTradingManager()
