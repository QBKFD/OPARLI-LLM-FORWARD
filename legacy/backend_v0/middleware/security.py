"""
Security Middleware for API Protection

Includes:
- API Key authentication
- Rate limiting
- Security headers
"""

import os
import time
import hashlib
from collections import defaultdict
from typing import Callable, Dict, List, Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse
from starlette.websockets import WebSocket
import logging

logger = logging.getLogger(__name__)

# Load API key from environment variable
API_KEY = os.getenv('API_KEY')
if not API_KEY:
    raise ValueError("API_KEY environment variable is required")

# Public endpoints that don't require authentication
PUBLIC_ENDPOINTS = [
    '/',
    '/docs',
    '/openapi.json',
    '/redoc',
    '/api/auth/login',      # Dashboard login
    '/api/auth/verify',     # Token verification
    '/api/auth/logout',     # Logout
]


class APIKeyMiddleware(BaseHTTPMiddleware):
    """
    Middleware to validate API key for all requests.

    API key can be provided via:
    - Header: X-API-Key
    - Query parameter: api_key (for WebSocket connections)
    - OR requests from same origin (frontend) are automatically allowed
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # Allow public endpoints
        if path in PUBLIC_ENDPOINTS:
            return await call_next(request)

        # Allow WebSocket connections (they have different header handling)
        if path.startswith('/ws/') or path.startswith('/api/dashboard/ws/'):
            # WebSocket connections from same domain are allowed
            origin = request.headers.get('origin')
            host = request.headers.get('host')

            # Check if origin matches our domain
            if origin and ('oparli.com' in origin or 'localhost' in origin):
                return await call_next(request)

            # Check if host is our domain
            if host and ('oparli.com' in host or 'localhost' in host):
                return await call_next(request)

            # Fall through to API key check for external WebSocket connections

        # Allow requests from same origin (frontend on same domain)
        origin = request.headers.get('origin')
        referer = request.headers.get('referer')

        # If request is from oparli.com (same domain), allow it
        if origin and 'oparli.com' in origin:
            return await call_next(request)
        if referer and 'oparli.com' in referer:
            return await call_next(request)

        # Check for API key in header (for external/API access)
        api_key = request.headers.get('X-API-Key')

        # Also check query params (for WebSocket)
        if not api_key:
            api_key = request.query_params.get('api_key')

        # Validate API key using constant-time comparison
        if not api_key or not self._secure_compare(api_key, API_KEY):
            logger.warning(f"Unauthorized access attempt to {path} from {request.client.host if request.client else 'unknown'}")
            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized", "message": "Invalid or missing API key"}
            )

        return await call_next(request)

    @staticmethod
    def _secure_compare(a: str, b: str) -> bool:
        """Constant-time string comparison to prevent timing attacks"""
        if len(a) != len(b):
            return False
        result = 0
        for x, y in zip(a.encode(), b.encode()):
            result |= x ^ y
        return result == 0


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Rate limiting middleware to prevent abuse.

    Limits:
    - 100 requests per minute per IP for regular endpoints
    - 10 requests per minute per IP for admin endpoints
    """

    def __init__(self, app, requests_per_minute: int = 100, admin_requests_per_minute: int = 10):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.admin_requests_per_minute = admin_requests_per_minute
        self.request_counts: Dict[str, List[float]] = defaultdict(list)
        self.admin_request_counts: Dict[str, List[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        client_ip = self._get_client_ip(request)
        path = request.url.path
        current_time = time.time()

        # Determine rate limit based on endpoint type
        is_admin = '/admin/' in path or '/streaming/' in path

        if is_admin:
            counts = self.admin_request_counts
            limit = self.admin_requests_per_minute
        else:
            counts = self.request_counts
            limit = self.requests_per_minute

        # Clean old entries (older than 1 minute)
        counts[client_ip] = [t for t in counts[client_ip] if current_time - t < 60]

        # Check rate limit
        if len(counts[client_ip]) >= limit:
            logger.warning(f"Rate limit exceeded for {client_ip} on {path}")
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Too Many Requests",
                    "message": f"Rate limit exceeded. Max {limit} requests per minute.",
                    "retry_after": 60
                },
                headers={"Retry-After": "60"}
            )

        # Record this request
        counts[client_ip].append(current_time)

        return await call_next(request)

    def _get_client_ip(self, request: Request) -> str:
        """Get client IP, handling proxies"""
        # Check for forwarded header (from Cloudflare/nginx)
        forwarded = request.headers.get('CF-Connecting-IP')
        if forwarded:
            return forwarded

        forwarded = request.headers.get('X-Forwarded-For')
        if forwarded:
            return forwarded.split(',')[0].strip()

        forwarded = request.headers.get('X-Real-IP')
        if forwarded:
            return forwarded

        return request.client.host if request.client else 'unknown'


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Middleware to add security headers to all responses.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        # Security headers
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'

        # Don't set HSTS here - let Cloudflare handle it
        # response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'

        return response


def validate_symbol(symbol: str) -> str:
    """
    Validate and sanitize symbol input.

    Returns sanitized symbol or raises ValueError.
    """
    if not symbol:
        raise ValueError("Symbol is required")

    # Remove whitespace and convert to uppercase
    symbol = symbol.strip().upper()

    # Check length (max 20 characters)
    if len(symbol) > 20:
        raise ValueError("Symbol too long (max 20 characters)")

    # Check for valid characters (alphanumeric and common separators)
    import re
    if not re.match(r'^[A-Z0-9._-]+$', symbol):
        raise ValueError("Symbol contains invalid characters")

    return symbol


def validate_limit(limit: int, max_limit: int = 5000) -> int:
    """
    Validate and bound the limit parameter.

    Returns bounded limit value.
    """
    if limit < 1:
        return 1
    if limit > max_limit:
        return max_limit
    return limit


def validate_timeframe(timeframe: str) -> str:
    """
    Validate timeframe parameter.

    Returns validated timeframe or raises ValueError.
    """
    valid_timeframes = ['1min', '5min', '15min', '30min', '1H', '4H', '1D']

    if timeframe not in valid_timeframes:
        raise ValueError(f"Invalid timeframe. Valid options: {', '.join(valid_timeframes)}")

    return timeframe
