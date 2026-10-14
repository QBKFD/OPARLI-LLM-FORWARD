# backend/config/database.py
"""
Database Configuration Module

Manages PostgreSQL connection pool and configuration
"""

import os
import logging
from typing import Optional
from contextlib import contextmanager
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)


class DatabaseConfig:
    """Database configuration and connection pool manager"""

    def __init__(self, database_url: Optional[str] = None):
        """
        Initialize database configuration

        Args:
            database_url: PostgreSQL connection URL (defaults to env variable)
        """
        self.database_url = database_url or os.getenv('DATABASE_URL')
        if not self.database_url:
            raise ValueError("DATABASE_URL environment variable is required")
        self.connection_pool: Optional[pool.SimpleConnectionPool] = None
        self.min_connections = 2
        self.max_connections = 10

    def create_pool(self) -> bool:
        """
        Create connection pool

        Returns:
            bool: True if successful
        """
        try:
            logger.info(f"Creating database connection pool (min={self.min_connections}, max={self.max_connections})")

            self.connection_pool = psycopg2.pool.SimpleConnectionPool(
                self.min_connections,
                self.max_connections,
                self.database_url
            )

            logger.info("✓ Database connection pool created")
            return True

        except Exception as e:
            logger.error(f"Failed to create database connection pool: {e}")
            return False

    @contextmanager
    def get_connection(self):
        """
        Get database connection from pool (context manager)

        Usage:
            with db_config.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM table")
        """
        if not self.connection_pool:
            raise RuntimeError("Connection pool not initialized. Call create_pool() first.")

        conn = None
        try:
            conn = self.connection_pool.getconn()
            yield conn
            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            if conn:
                self.connection_pool.putconn(conn)

    @contextmanager
    def get_cursor(self, dict_cursor=True):
        """
        Get database cursor (context manager)

        Args:
            dict_cursor: If True, returns RealDictCursor (results as dicts)

        Usage:
            with db_config.get_cursor() as cursor:
                cursor.execute("SELECT * FROM table")
                results = cursor.fetchall()
        """
        with self.get_connection() as conn:
            cursor_factory = RealDictCursor if dict_cursor else None
            cursor = conn.cursor(cursor_factory=cursor_factory)
            try:
                yield cursor
            finally:
                cursor.close()

    def test_connection(self) -> bool:
        """
        Test database connection

        Returns:
            bool: True if connection successful
        """
        try:
            with self.get_cursor() as cursor:
                cursor.execute("SELECT 1")
                result = cursor.fetchone()
                logger.info(f"✓ Database connection test successful: {result}")
                return True
        except Exception as e:
            logger.error(f"Database connection test failed: {e}")
            return False

    def close_pool(self):
        """Close all connections in the pool"""
        if self.connection_pool:
            self.connection_pool.closeall()
            logger.info("✓ Database connection pool closed")


# Global database instance
_db_instance: Optional[DatabaseConfig] = None


def get_database() -> DatabaseConfig:
    """Get global database instance (singleton)"""
    global _db_instance
    if _db_instance is None:
        _db_instance = DatabaseConfig()
    return _db_instance


def init_database() -> bool:
    """
    Initialize database connection pool

    Returns:
        bool: True if successful
    """
    db = get_database()
    if not db.create_pool():
        return False

    # Test connection
    return db.test_connection()
