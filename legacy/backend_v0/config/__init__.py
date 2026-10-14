# backend/config/__init__.py
"""Configuration module"""

from .database import DatabaseConfig, get_database, init_database

__all__ = ['DatabaseConfig', 'get_database', 'init_database']
