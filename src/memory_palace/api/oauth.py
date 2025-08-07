"""OAuth endpoints for Claude.ai MCP authentication."""

import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jose import JWTError, jwt
from pydantic import BaseModel

router = APIRouter(prefix="/oauth", tags=["oauth"])

# OAuth configuration
CLIENT_ID = "claude"
CLIENT_SECRET = os.getenv("CLAUDE_API_KEY", secrets.token_urlsafe(32))
REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"
SECRET_KEY = os.getenv("JWT_SECRET_KEY", secrets.token_urlsafe(32))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30


class TokenResponse(BaseModel):
    """OAuth token response."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: Optional[str] = None


@router.get("/.well-known/oauth-authorization-server")
async def oauth_metadata(request: Request):
    """OAuth 2.0 Authorization Server Metadata (RFC 8414)."""
    base_url = str(request.base_url).rstrip("/")
    
    return {
        "issuer": base_url,
        "authorization_endpoint": f"{base_url}/oauth/authorize",
        "token_endpoint": f"{base_url}/oauth/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["client_secret_post"],
        "scopes_supported": ["read", "write"],
        "code_challenge_methods_supported": ["S256"],
    }


@router.get("/authorize")
async def authorize(
    response_type: str,
    client_id: str,
    redirect_uri: str,
    scope: str = "read write",
    state: Optional[str] = None,
    code_challenge: Optional[str] = None,
    code_challenge_method: Optional[str] = None,
):
    """OAuth authorization endpoint."""
    
    # Validate client
    if client_id != CLIENT_ID:
        raise HTTPException(status_code=400, detail="Invalid client_id")
    
    if response_type != "code":
        raise HTTPException(status_code=400, detail="Unsupported response_type")
    
    # For simplicity, auto-approve for Claude
    # In production, you might want a consent screen
    auth_code = secrets.token_urlsafe(32)
    
    # Store auth code (in production, use Redis or database)
    # For now, we'll encode it in the code itself
    code_data = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "exp": datetime.utcnow() + timedelta(minutes=10)
    }
    
    # Build redirect URL
    params = {"code": auth_code}
    if state:
        params["state"] = state
    
    redirect_url = f"{redirect_uri}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
    return RedirectResponse(url=redirect_url)


@router.post("/token", response_model=TokenResponse)
async def token(
    grant_type: str = Form(...),
    code: Optional[str] = Form(None),
    client_id: str = Form(...),
    client_secret: str = Form(...),
    refresh_token: Optional[str] = Form(None),
):
    """OAuth token endpoint."""
    
    # Validate client credentials
    if client_id != CLIENT_ID or not secrets.compare_digest(client_secret, CLIENT_SECRET):
        raise HTTPException(status_code=401, detail="Invalid client credentials")
    
    if grant_type == "authorization_code":
        if not code:
            raise HTTPException(status_code=400, detail="Missing authorization code")
        
        # In production, validate the code properly
        # For now, accept any code
        
    elif grant_type == "refresh_token":
        if not refresh_token:
            raise HTTPException(status_code=400, detail="Missing refresh token")
        
        # Validate refresh token
        try:
            payload = jwt.decode(refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
            if payload.get("type") != "refresh":
                raise HTTPException(status_code=401, detail="Invalid refresh token")
        except JWTError:
            raise HTTPException(status_code=401, detail="Invalid refresh token")
    
    else:
        raise HTTPException(status_code=400, detail="Unsupported grant type")
    
    # Create tokens
    access_token_expires = timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    access_token = create_access_token(
        data={"sub": CLIENT_ID, "scopes": ["read", "write"]},
        expires_delta=access_token_expires
    )
    
    refresh_token = create_refresh_token(
        data={"sub": CLIENT_ID, "type": "refresh"}
    )
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=int(access_token_expires.total_seconds())
    )


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(data: dict):
    """Create a JWT refresh token."""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=90)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)