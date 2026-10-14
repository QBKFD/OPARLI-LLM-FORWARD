#!/usr/bin/env python3
# backend/run_system.py
"""
Main entry point for the trading system

Starts the agent orchestrator and all supporting services.
"""

import sys
import os
import logging
import signal
import time

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)-20s | %(levelname)-8s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('main')

# Reduce noise from some libraries
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)


def main():
    """Main entry point"""
    logger.info("=" * 60)
    logger.info("Starting Trading System")
    logger.info("=" * 60)

    # Initialize database connection
    logger.info("Initializing database connection...")
    from config.database import get_database
    try:
        db = get_database()
        db.create_pool()
        logger.info("✓ Database connected")
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        return 1

    # Initialize LLM provider
    logger.info("Initializing LLM provider...")
    from config.llm_provider import get_llm_client, get_provider_info
    try:
        info = get_provider_info()
        logger.info(f"  Provider: {info['provider']}")
        logger.info(f"  Model: {info['model']}")
        llm = get_llm_client()
        logger.info("✓ LLM provider initialized")
    except Exception as e:
        logger.error(f"Failed to initialize LLM: {e}")
        return 1

    # Initialize orchestrator
    logger.info("Initializing agent orchestrator...")
    from core.agent_orchestrator import get_orchestrator

    config = {
        'agents': {
            'scanner': {
                'symbols': ['XAUUSD'],
                'check_interval': 1.0
            },
            'visual_analyst': {
                'timeframes': ['1min', '5min', '15min']
            },
            'risk_manager': {
                'max_position_size': 0.1,
                'max_daily_loss': 100.0
            }
        }
    }

    orchestrator = get_orchestrator(config)

    # Setup graceful shutdown
    def signal_handler(signum, frame):
        logger.info("\nShutdown signal received...")
        orchestrator.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start the system
    try:
        orchestrator.start()

        logger.info("")
        logger.info("System is running. Press Ctrl+C to stop.")
        logger.info("")

        # Main loop - process messages and keep alive
        while orchestrator.running:
            # Process pending messages for on-demand agents
            orchestrator.process_agent_messages()
            time.sleep(0.1)

    except KeyboardInterrupt:
        logger.info("\nKeyboard interrupt received...")
    except Exception as e:
        logger.error(f"System error: {e}", exc_info=True)
        return 1
    finally:
        orchestrator.stop()
        logger.info("System stopped.")

    return 0


if __name__ == '__main__':
    sys.exit(main())
