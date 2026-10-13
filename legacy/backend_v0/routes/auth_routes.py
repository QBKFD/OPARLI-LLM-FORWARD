# backend/routes/auth_routes.py
"""
Authentication routes for dashboard access

Simple password-based auth with JWT tokens.
Password is stored in .env file (DASHBOARD_PASSWORD)
"""

from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from datetime import datetime, timedelta
import jwt
import os
import secrets
import hashlib
import logging

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

# Security
security = HTTPBearer(auto_error=False)

# JWT Configuration
JWT_SECRET = os.getenv('JWT_SECRET', secrets.token_hex(32))
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_DAYS = 7

# Get dashboard password from env
DASHBOARD_PASSWORD = os.getenv('DASHBOARD_PASSWORD', 'changeme123')


class LoginRequest(BaseModel):
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


def create_jwt_token(data: dict, expires_delta: timedelta = None) -> str:
    """Create a JWT token"""
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(days=JWT_EXPIRATION_DAYS)

    to_encode.update({"exp": expire, "iat": datetime.utcnow()})

    encoded_jwt = jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return encoded_jwt


def verify_jwt_token(token: str) -> dict:
    """Verify a JWT token and return its payload"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def hash_password(password: str) -> str:
    """Simple hash for comparison (not storing, just comparing)"""
    return hashlib.sha256(password.encode()).hexdigest()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """
    Dependency to verify JWT token and return user info

    Usage in protected routes:
        @router.get("/protected")
        async def protected_route(user: dict = Depends(get_current_user)):
            return {"message": "You are authenticated", "user": user}
    """
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"}
        )

    token = credentials.credentials
    payload = verify_jwt_token(token)

    return {
        "authenticated": True,
        "role": payload.get("role", "admin"),
        "issued_at": payload.get("iat")
    }


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    """
    Login with dashboard password

    Returns JWT token valid for 7 days
    """
    # Verify password
    if request.password != DASHBOARD_PASSWORD:
        logger.warning("Failed login attempt")
        raise HTTPException(
            status_code=401,
            detail="Invalid password"
        )

    # Create token
    expires_delta = timedelta(days=JWT_EXPIRATION_DAYS)
    access_token = create_jwt_token(
        data={"sub": "dashboard_user", "role": "admin"},
        expires_delta=expires_delta
    )

    logger.info("Successful dashboard login")

    return TokenResponse(
        access_token=access_token,
        expires_in=int(expires_delta.total_seconds())
    )


@router.get("/verify")
async def verify_token(user: dict = Depends(get_current_user)):
    """
    Verify if current token is valid

    Used by frontend to check auth status on page load
    """
    return {
        "valid": True,
        "user": user
    }


@router.post("/logout")
async def logout():
    """
    Logout (client-side token removal)

    JWT tokens can't be invalidated server-side without a blacklist,
    so logout is handled client-side by removing the token.
    This endpoint just confirms the logout action.
    """
    return {"message": "Logged out successfully"}


# Optional: Get current password hash (for debugging, remove in production)
@router.get("/debug/password-hint")
async def password_hint():
    """
    Development only - shows first/last char of password
    Remove this in production!
    """
    if os.getenv('ENV', 'production') != 'development':
        raise HTTPException(status_code=404, detail="Not found")

    pwd = DASHBOARD_PASSWORD
    if len(pwd) > 2:
        hint = f"{pwd[0]}{'*' * (len(pwd)-2)}{pwd[-1]}"
    else:
        hint = "**"

    return {"hint": hint, "length": len(pwd)}
