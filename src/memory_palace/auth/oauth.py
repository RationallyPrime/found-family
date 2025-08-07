"""Simple OAuth implementation for MCP remote access.

Since Claude is the only user, we use a simplified OAuth flow with a single API key.
"""

import secrets
from datetime import datetime, timedelta

from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

# Configuration
SECRET_KEY = secrets.token_urlsafe(32)  # In production, load from env
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 30  # 30 days for Claude

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class Token(BaseModel):
    """OAuth token response."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class TokenData(BaseModel):
    """Token payload data."""
    client_id: str
    scopes: list[str] = []


def create_access_token(data: dict, expires_delta: timedelta | None = None):
    """Create a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_token(token: str) -> TokenData | None:
    """Verify and decode a JWT token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        client_id: str = payload.get("sub")
        if client_id is None:
            return None
        scopes = payload.get("scopes", [])
        return TokenData(client_id=client_id, scopes=scopes)
    except JWTError:
        return None


def authenticate_claude(api_key: str) -> bool:
    """Authenticate Claude with the configured API key.
    
    In production, this would check against a secure store.
    For now, we use an environment variable.
    """
    import os
    expected_key = os.getenv("CLAUDE_API_KEY", "")
    return secrets.compare_digest(api_key, expected_key) if expected_key else False