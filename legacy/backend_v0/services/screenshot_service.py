# backend/services/screenshot_service.py
"""
Screenshot Service

Takes screenshots of trading charts from localhost or remote hosting
Supports multiple methods: Selenium, Playwright, API endpoint
"""

import logging
import base64
import os
from typing import Optional, Dict
from datetime import datetime

logger = logging.getLogger(__name__)


class ScreenshotService:
    """
    Service for capturing chart screenshots

    Supports:
    1. Localhost frontend (Selenium/headless Chrome)
    2. Remote hosting (when deployed)
    3. Direct chart generation (matplotlib/plotly as fallback)
    """

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}

        # Chart URL configuration
        self.chart_url = self.config.get('chart_url', 'http://localhost:5173')
        self.method = self.config.get('method', 'selenium')  # 'selenium', 'playwright', 'api'

        # Screenshot settings
        self.width = self.config.get('width', 1920)
        self.height = self.config.get('height', 1080)
        self.wait_seconds = self.config.get('wait_seconds', 3)  # Wait for chart to load

        logger.info(f"Screenshot Service initialized (method: {self.method}, url: {self.chart_url})")

    def take_screenshot(self, symbol: str, timeframe: str = '5min') -> Optional[str]:
        """
        Take screenshot of chart

        Args:
            symbol: Trading symbol (e.g., 'XAUUSD')
            timeframe: Chart timeframe (e.g., '5min', '15min', '1H')

        Returns:
            Base64 encoded PNG screenshot or None
        """
        if self.method == 'selenium':
            return self._take_screenshot_selenium(symbol, timeframe)
        elif self.method == 'playwright':
            return self._take_screenshot_playwright(symbol, timeframe)
        elif self.method == 'api':
            return self._take_screenshot_api(symbol, timeframe)
        else:
            logger.error(f"Unknown screenshot method: {self.method}")
            return None

    def _take_screenshot_selenium(self, symbol: str, timeframe: str) -> Optional[str]:
        """
        Take screenshot using Selenium with headless Chrome

        This works for localhost and remote hosting

        Args:
            symbol: Trading symbol
            timeframe: Chart timeframe

        Returns:
            Base64 encoded screenshot
        """
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            import time

            # Construct chart URL
            # Adjust based on your frontend routing
            url = f"{self.chart_url}/?symbol={symbol}&timeframe={timeframe}"

            logger.info(f"📸 Taking screenshot: {url}")

            # Setup headless Chrome options
            chrome_options = Options()
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument(f'--window-size={self.width},{self.height}')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--disable-software-rasterizer')
            chrome_options.add_argument('--disable-extensions')

            # Initialize driver
            driver = webdriver.Chrome(options=chrome_options)

            try:
                # Navigate to chart page
                driver.get(url)

                # Wait for chart to load
                # Adjust selector based on your frontend
                wait = WebDriverWait(driver, 10)

                # Wait for canvas element (TradingView Lightweight Charts uses canvas)
                try:
                    wait.until(EC.presence_of_element_located((By.TAG_NAME, 'canvas')))
                    logger.debug("Chart canvas loaded")
                except:
                    logger.warning("Canvas not found, waiting anyway")

                # Additional wait for data to render
                time.sleep(self.wait_seconds)

                # Take screenshot
                screenshot_png = driver.get_screenshot_as_png()

                # Convert to base64
                screenshot_base64 = base64.b64encode(screenshot_png).decode('utf-8')

                logger.info(f"✓ Screenshot captured for {symbol} ({len(screenshot_base64)} bytes)")
                return screenshot_base64

            finally:
                driver.quit()

        except ImportError:
            logger.error("Selenium not installed. Install with: pip install selenium")
            return None
        except Exception as e:
            logger.error(f"Error taking screenshot with Selenium: {e}")
            return None

    def _take_screenshot_playwright(self, symbol: str, timeframe: str) -> Optional[str]:
        """
        Take screenshot using Playwright

        Alternative to Selenium, often faster and more reliable

        Args:
            symbol: Trading symbol
            timeframe: Chart timeframe

        Returns:
            Base64 encoded screenshot
        """
        try:
            from playwright.sync_api import sync_playwright

            url = f"{self.chart_url}/?symbol={symbol}&timeframe={timeframe}"

            logger.info(f"📸 Taking screenshot (Playwright): {url}")

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': self.width, 'height': self.height})

                # Navigate to chart
                page.goto(url)

                # Wait for chart to load
                page.wait_for_selector('canvas', timeout=10000)

                # Additional wait for data
                page.wait_for_timeout(self.wait_seconds * 1000)

                # Take screenshot
                screenshot_bytes = page.screenshot()

                # Convert to base64
                screenshot_base64 = base64.b64encode(screenshot_bytes).decode('utf-8')

                browser.close()

                logger.info(f"✓ Screenshot captured for {symbol}")
                return screenshot_base64

        except ImportError:
            logger.error("Playwright not installed. Install with: pip install playwright && playwright install chromium")
            return None
        except Exception as e:
            logger.error(f"Error taking screenshot with Playwright: {e}")
            return None

    def _take_screenshot_api(self, symbol: str, timeframe: str) -> Optional[str]:
        """
        Get screenshot via API endpoint

        Your frontend could have an endpoint that returns chart as PNG

        Args:
            symbol: Trading symbol
            timeframe: Chart timeframe

        Returns:
            Base64 encoded screenshot
        """
        try:
            import requests

            url = f"{self.chart_url}/api/chart/screenshot"
            params = {'symbol': symbol, 'timeframe': timeframe}

            logger.info(f"📸 Requesting screenshot via API: {url}")

            response = requests.get(url, params=params, timeout=15)

            if response.status_code == 200:
                # Assuming API returns base64 encoded PNG
                data = response.json()
                screenshot_base64 = data.get('screenshot')

                if screenshot_base64:
                    logger.info(f"✓ Screenshot received from API for {symbol}")
                    return screenshot_base64
                else:
                    logger.error("API response missing 'screenshot' field")
                    return None
            else:
                logger.error(f"API returned status {response.status_code}")
                return None

        except ImportError:
            logger.error("Requests not installed. Install with: pip install requests")
            return None
        except Exception as e:
            logger.error(f"Error getting screenshot from API: {e}")
            return None

    def save_screenshot_to_file(self, screenshot_base64: str, symbol: str, directory: str = 'screenshots') -> Optional[str]:
        """
        Save screenshot to file system (for debugging/audit trail)

        Args:
            screenshot_base64: Base64 encoded PNG
            symbol: Trading symbol
            directory: Directory to save screenshots

        Returns:
            File path or None
        """
        try:
            # Create directory if doesn't exist
            os.makedirs(directory, exist_ok=True)

            # Generate filename
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{symbol}_{timestamp}.png"
            filepath = os.path.join(directory, filename)

            # Decode and save
            screenshot_bytes = base64.b64decode(screenshot_base64)
            with open(filepath, 'wb') as f:
                f.write(screenshot_bytes)

            logger.info(f"Screenshot saved to: {filepath}")
            return filepath

        except Exception as e:
            logger.error(f"Error saving screenshot: {e}")
            return None


# Singleton instance
_screenshot_service: Optional[ScreenshotService] = None


def get_screenshot_service(config: Optional[Dict] = None) -> ScreenshotService:
    """
    Get singleton instance of Screenshot Service

    Args:
        config: Optional configuration dict

    Returns:
        ScreenshotService instance
    """
    global _screenshot_service
    if _screenshot_service is None:
        _screenshot_service = ScreenshotService(config)
    return _screenshot_service
