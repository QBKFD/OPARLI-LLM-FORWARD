# backend/services/chart_generator.py
"""
Chart Generation Service

Generates trading charts with indicators for AI agent analysis.
Fully configurable - no hardcoded indicators or timeframes.
"""

import logging
import base64
from io import BytesIO
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import pandas as pd
import mplfinance as mpf
try:
    import pandas_ta as ta
    HAS_PANDAS_TA = True
except ImportError:
    HAS_PANDAS_TA = False
    ta = None

from config.database import get_database
from services.session_tracker import get_session_tracker, SESSIONS

logger = logging.getLogger(__name__)


class ChartConfig:
    """Configuration for chart generation"""

    def __init__(
        self,
        timeframes: List[str] = None,
        lookback_bars: int = 200,
        indicators: List[Dict] = None,
        chart_style: str = 'charles',
        width: int = 1920,
        height: int = 1080,
        volume: bool = True,
        save_to_file: bool = False,
        output_dir: str = '/tmp',
        show_session_levels: bool = True
    ):
        """
        Initialize chart configuration

        Args:
            timeframes: List of timeframes to generate (e.g., ['1min', '5min', '15min'])
            lookback_bars: Number of bars to include in chart
            indicators: List of indicator configs, each with:
                {
                    'name': 'bollinger_bands',
                    'params': {'length': 20, 'std': 2},
                    'plot': True
                }
            chart_style: mplfinance style ('charles', 'blueskies', 'yahoo', etc.)
            width: Chart width in pixels
            height: Chart height in pixels
            volume: Show volume bars
            save_to_file: Save charts to disk for inspection
            output_dir: Directory to save charts if save_to_file=True
        """
        self.timeframes = timeframes or ['1min', '5min', '15min', '1H']
        self.lookback_bars = lookback_bars
        self.indicators = indicators or self._default_indicators()
        self.chart_style = chart_style
        self.width = width
        self.height = height
        self.volume = volume
        self.save_to_file = save_to_file
        self.output_dir = output_dir
        self.show_session_levels = show_session_levels

    @staticmethod
    def _default_indicators() -> List[Dict]:
        """Default indicator configuration"""
        return [
            {
                'name': 'ema',
                'params': {'length': 20},
                'plot': True,
                'color': 'blue'
            },
            {
                'name': 'ema',
                'params': {'length': 50},
                'plot': True,
                'color': 'orange'
            },
            {
                'name': 'bbands',
                'params': {'length': 20, 'std': 2},
                'plot': True,
                'color': 'gray'
            },
            {
                'name': 'rsi',
                'params': {'length': 14},
                'plot': False  # Don't plot, but include in data
            }
        ]


class ChartGenerator:
    """Generates trading charts with configurable indicators"""

    def __init__(self, config: Optional[ChartConfig] = None):
        """
        Initialize chart generator

        Args:
            config: ChartConfig instance or None for defaults
        """
        self.config = config or ChartConfig()
        logger.info(f"✓ Chart generator initialized with {len(self.config.timeframes)} timeframes")

    def generate_charts(
        self,
        symbol: str,
        timestamp: Optional[datetime] = None
    ) -> Dict[str, Dict]:
        """
        Generate charts for all configured timeframes

        Args:
            symbol: Trading symbol (e.g., 'XAUUSD')
            timestamp: Reference timestamp (defaults to now)

        Returns:
            Dictionary mapping timeframe to chart data:
            {
                '5min': {
                    'image_base64': '<base64 string>',
                    'indicators': {...},
                    'metadata': {...}
                },
                ...
            }
        """
        timestamp = timestamp or datetime.now()
        results = {}

        for timeframe in self.config.timeframes:
            try:
                chart_data = self._generate_single_chart(symbol, timeframe, timestamp)
                results[timeframe] = chart_data
                logger.info(f"✓ Generated {timeframe} chart for {symbol}")
            except Exception as e:
                logger.error(f"✗ Failed to generate {timeframe} chart: {e}")
                results[timeframe] = {'error': str(e)}

        return results

    def _generate_single_chart(
        self,
        symbol: str,
        timeframe: str,
        timestamp: datetime
    ) -> Dict:
        """
        Generate chart for single timeframe

        Args:
            symbol: Trading symbol
            timeframe: Timeframe (e.g., '5min', '1H')
            timestamp: Reference timestamp

        Returns:
            Dictionary with image_base64, indicators, and metadata
        """
        # Step 1: Fetch data from database
        df = self._fetch_ohlcv_data(symbol, timeframe, self.config.lookback_bars)

        if df.empty:
            raise ValueError(f"No data available for {symbol} {timeframe}")

        # Step 2: Calculate indicators
        indicator_data = self._calculate_indicators(df)

        # Step 3: Prepare addplot for mplfinance
        addplots = self._prepare_addplots(df, indicator_data)

        # Step 4: Get session levels (if enabled)
        session_levels = {}
        if self.config.show_session_levels:
            session_levels = self._get_session_levels(symbol, df)

        # Step 5: Generate chart image
        image_base64 = self._render_chart(df, addplots, symbol, timeframe, session_levels)

        # Step 5: Extract key indicator values (for text context)
        latest_values = self._extract_latest_values(df, indicator_data)

        return {
            'image_base64': image_base64,
            'indicators': latest_values,
            'metadata': {
                'symbol': symbol,
                'timeframe': timeframe,
                'bars_count': len(df),
                'timestamp': timestamp.isoformat(),
                'latest_close': float(df['Close'].iloc[-1]),
                'latest_timestamp': df.index[-1].isoformat()
            }
        }

    def _fetch_ohlcv_data(
        self,
        symbol: str,
        timeframe: str,
        limit: int
    ) -> pd.DataFrame:
        """
        Fetch OHLCV data from database

        Args:
            symbol: Trading symbol
            timeframe: Timeframe
            limit: Number of bars to fetch

        Returns:
            DataFrame with OHLCV data
        """
        # Map timeframe to database view name
        view_name = f"ohlcv_{timeframe.lower()}"

        query = f"""
            SELECT
                timestamp,
                open,
                high,
                low,
                close,
                volume
            FROM {view_name}
            WHERE symbol = %s
            ORDER BY timestamp DESC
            LIMIT %s
        """

        db = get_database()
        with db.get_cursor() as cur:
            cur.execute(query, (symbol, limit))
            rows = cur.fetchall()

        if not rows:
            return pd.DataFrame()

        # Convert to DataFrame
        df = pd.DataFrame(rows, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df = df.sort_values('timestamp')  # Sort ascending for chart
        df.set_index('timestamp', inplace=True)

        # Convert OHLCV columns to float (mplfinance requires float/int)
        df['open'] = df['open'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['close'] = df['close'].astype(float)
        df['volume'] = df['volume'].astype(float)

        # Capitalize columns for mplfinance
        df.columns = ['Open', 'High', 'Low', 'Close', 'Volume']

        return df

    def _calculate_indicators(self, df: pd.DataFrame) -> Dict[str, pd.Series]:
        """
        Calculate all configured indicators

        Args:
            df: OHLCV DataFrame

        Returns:
            Dictionary mapping indicator names to calculated series
        """
        indicator_data = {}

        # Ensure Close column is float64 for pandas-ta
        if 'Close' in df.columns:
            df['Close'] = df['Close'].astype('float64')

        for indicator_config in self.config.indicators:
            name = indicator_config['name']
            params = indicator_config.get('params', {})

            try:
                # Calculate indicator using pandas-ta
                if name == 'ema':
                    result = ta.ema(df['Close'], **params)
                    key = f"ema_{params.get('length', 20)}"
                    indicator_data[key] = result

                elif name == 'sma':
                    result = ta.sma(df['Close'], **params)
                    key = f"sma_{params.get('length', 20)}"
                    indicator_data[key] = result

                elif name == 'bbands':
                    result = ta.bbands(df['Close'], **params)
                    indicator_data['bb_upper'] = result[f"BBU_{params.get('length', 20)}_{params.get('std', 2)}.0"]
                    indicator_data['bb_middle'] = result[f"BBM_{params.get('length', 20)}_{params.get('std', 2)}.0"]
                    indicator_data['bb_lower'] = result[f"BBL_{params.get('length', 20)}_{params.get('std', 2)}.0"]

                elif name == 'rsi':
                    result = ta.rsi(df['Close'], **params)
                    indicator_data['rsi'] = result

                elif name == 'macd':
                    result = ta.macd(df['Close'], **params)
                    indicator_data['macd'] = result['MACD_12_26_9']
                    indicator_data['macd_signal'] = result['MACDs_12_26_9']
                    indicator_data['macd_hist'] = result['MACDh_12_26_9']

                else:
                    logger.warning(f"Unknown indicator: {name}")

            except Exception as e:
                logger.error(f"Error calculating {name}: {e}")

        return indicator_data

    def _prepare_addplots(
        self,
        df: pd.DataFrame,
        indicator_data: Dict[str, pd.Series]
    ) -> List:
        """
        Prepare addplot list for mplfinance

        Args:
            df: OHLCV DataFrame
            indicator_data: Calculated indicators

        Returns:
            List of mpf.make_addplot() objects
        """
        addplots = []

        for indicator_config in self.config.indicators:
            if not indicator_config.get('plot', False):
                continue

            name = indicator_config['name']
            color = indicator_config.get('color', 'blue')
            params = indicator_config.get('params', {})

            try:
                if name == 'ema':
                    key = f"ema_{params.get('length', 20)}"
                    if key in indicator_data:
                        addplots.append(mpf.make_addplot(indicator_data[key], color=color))

                elif name == 'sma':
                    key = f"sma_{params.get('length', 20)}"
                    if key in indicator_data:
                        addplots.append(mpf.make_addplot(indicator_data[key], color=color))

                elif name == 'bbands':
                    if 'bb_upper' in indicator_data:
                        addplots.append(mpf.make_addplot(indicator_data['bb_upper'], color=color, linestyle='dashed'))
                        addplots.append(mpf.make_addplot(indicator_data['bb_middle'], color=color))
                        addplots.append(mpf.make_addplot(indicator_data['bb_lower'], color=color, linestyle='dashed'))

            except Exception as e:
                logger.error(f"Error adding {name} to plot: {e}")

        return addplots

    def _get_session_levels(self, symbol: str, df: pd.DataFrame) -> Dict:
        """
        Get session levels for chart overlay

        Args:
            symbol: Trading symbol
            df: OHLCV DataFrame (to get reference date)

        Returns:
            Dictionary with session levels
        """
        try:
            # Use the last timestamp in the data as reference
            reference_date = df.index[-1]
            tracker = get_session_tracker()
            return tracker.get_current_levels(symbol, reference_date)
        except Exception as e:
            logger.warning(f"Could not get session levels: {e}")
            return {}

    def _prepare_hlines(
        self,
        df: pd.DataFrame,
        session_levels: Dict,
        ylim_lower: float,
        ylim_upper: float
    ) -> Tuple[List[float], List[str], List[float]]:
        """
        Prepare horizontal lines for session levels

        Args:
            df: OHLCV DataFrame
            session_levels: Session level data
            ylim_lower: Chart Y-axis lower bound
            ylim_upper: Chart Y-axis upper bound

        Returns:
            Tuple of (prices, colors, linewidths)
        """
        hlines_prices = []
        hlines_colors = []
        hlines_widths = []
        hlines_styles = []

        # Level color definitions
        level_colors = {
            'asian': {'high': '#00CED1', 'low': '#00CED1'},      # Cyan
            'london': {'high': '#FFA500', 'low': '#FFA500'},      # Orange
            'newyork': {'high': '#4169E1', 'low': '#4169E1'},     # Royal Blue
            'daily': {'high': '#FF4444', 'low': '#44FF44'},       # Red/Green
            'prev_daily': {'high': '#AA0000', 'low': '#00AA00'},  # Dark Red/Green
            'weekly': {'high': '#9932CC', 'low': '#9932CC'},      # Purple
            'prev_weekly': {'high': '#6B238E', 'low': '#6B238E'}  # Dark Purple
        }

        level_widths = {
            'asian': 1.0,
            'london': 1.0,
            'newyork': 1.0,
            'daily': 1.5,
            'prev_daily': 2.0,
            'weekly': 1.5,
            'prev_weekly': 2.0
        }

        # Add each level if within visible range
        for level_name, level_data in session_levels.items():
            if level_data is None:
                continue

            high = level_data.get('high')
            low = level_data.get('low')
            colors = level_colors.get(level_name, {'high': 'gray', 'low': 'gray'})
            width = level_widths.get(level_name, 1.0)

            # Add high level if visible
            if high and ylim_lower <= high <= ylim_upper:
                hlines_prices.append(high)
                hlines_colors.append(colors['high'])
                hlines_widths.append(width)

            # Add low level if visible
            if low and ylim_lower <= low <= ylim_upper:
                hlines_prices.append(low)
                hlines_colors.append(colors['low'])
                hlines_widths.append(width)

        return hlines_prices, hlines_colors, hlines_widths

    def _render_chart(
        self,
        df: pd.DataFrame,
        addplots: List,
        symbol: str,
        timeframe: str,
        session_levels: Optional[Dict] = None
    ) -> str:
        """
        Render chart to base64 image

        Args:
            df: OHLCV DataFrame
            addplots: List of additional plots
            symbol: Trading symbol
            timeframe: Timeframe

        Returns:
            Base64-encoded PNG image
        """
        # Calculate Y-axis range from data (TradingView-style auto-zoom)
        high_max = df['High'].max()
        low_min = df['Low'].min()
        price_range = high_max - low_min
        padding = price_range * 0.05  # 5% padding top/bottom

        ylim_lower = low_min - padding
        ylim_upper = high_max + padding

        # Configure chart appearance
        # Calculate candle width based on number of bars (fewer bars = fatter candles)
        num_bars = len(df)
        if num_bars <= 100:
            candle_width = 2.0
        elif num_bars <= 200:
            candle_width = 1.8
        else:
            candle_width = 1.5

        kwargs = {
            'type': 'candle',
            'style': self.config.chart_style,
            'volume': False,  # Disable volume (spot gold has no volume data)
            'title': f'{symbol} - {timeframe}',
            'figsize': (self.config.width / 100, self.config.height / 100),
            'returnfig': True,
            'tight_layout': True,  # Remove white space
            'ylim': (ylim_lower, ylim_upper),  # Auto-zoom to visible data
            'scale_width_adjustment': {'candle': candle_width, 'volume': 0.7}  # Fatter candles
        }

        if addplots:
            kwargs['addplot'] = addplots

        # Add session level horizontal lines
        if session_levels and self.config.show_session_levels:
            hlines_prices, hlines_colors, hlines_widths = self._prepare_hlines(
                df, session_levels, ylim_lower, ylim_upper
            )
            if hlines_prices:
                kwargs['hlines'] = dict(
                    hlines=hlines_prices,
                    colors=hlines_colors,
                    linewidths=hlines_widths,
                    linestyle='--'
                )

        # Generate chart
        fig, axes = mpf.plot(df, **kwargs)

        # Remove all padding and margins for TradingView-style fullscreen chart
        fig.subplots_adjust(left=0, right=1, top=1, bottom=0, hspace=0, wspace=0)

        # Save to file if requested (for development/testing)
        if self.config.save_to_file:
            file_path = f"{self.config.output_dir}/chart_{symbol}_{timeframe}.png"
            fig.savefig(file_path, bbox_inches='tight', pad_inches=0, dpi=100)
            logger.info(f"✓ Chart saved to {file_path}")

        # Convert to base64
        buf = BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight', pad_inches=0, dpi=100)
        buf.seek(0)
        image_base64 = base64.b64encode(buf.read()).decode('utf-8')
        buf.close()

        return image_base64

    def _extract_latest_values(
        self,
        df: pd.DataFrame,
        indicator_data: Dict[str, pd.Series]
    ) -> Dict:
        """
        Extract latest indicator values for text context

        Args:
            df: OHLCV DataFrame
            indicator_data: Calculated indicators

        Returns:
            Dictionary of latest values
        """
        latest = {
            'close': float(df['Close'].iloc[-1]),
            'open': float(df['Open'].iloc[-1]),
            'high': float(df['High'].iloc[-1]),
            'low': float(df['Low'].iloc[-1]),
            'volume': float(df['Volume'].iloc[-1])
        }

        # Add indicator values
        for key, series in indicator_data.items():
            if not series.empty:
                try:
                    latest[key] = float(series.iloc[-1])
                except:
                    latest[key] = None

        return latest


# Example usage
if __name__ == '__main__':
    # Configure with custom settings
    config = ChartConfig(
        timeframes=['5min', '15min', '1H'],
        lookback_bars=100,
        indicators=[
            {'name': 'ema', 'params': {'length': 20}, 'plot': True, 'color': 'blue'},
            {'name': 'ema', 'params': {'length': 50}, 'plot': True, 'color': 'orange'},
            {'name': 'bbands', 'params': {'length': 20, 'std': 2}, 'plot': True, 'color': 'gray'},
            {'name': 'rsi', 'params': {'length': 14}, 'plot': False}
        ],
        save_to_file=True  # Save to /tmp for inspection
    )

    generator = ChartGenerator(config)
    charts = generator.generate_charts('XAUUSD')

    for tf, data in charts.items():
        print(f"\n{tf} Chart:")
        print(f"  Bars: {data['metadata']['bars_count']}")
        print(f"  Latest Close: {data['metadata']['latest_close']}")
        print(f"  Image size: {len(data['image_base64'])} characters")
