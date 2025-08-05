"""OAuth endpoints for remote MCP server."""
from datetime import timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from .oauth_auth import (
    OAUTH_ACCESS_TOKEN_EXPIRE_MINUTES,
    TokenData,
    User,
    authenticate_user,
    create_access_token,
    create_refresh_token,
    get_current_user,
)

oauth_router = APIRouter(prefix="/oauth", tags=["oauth"])


class AuthorizeRequest(BaseModel):
    """OAuth authorization request."""
    response_type: str
    client_id: str
    redirect_uri: str
    scope: str = ""
    state: str = ""


@oauth_router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata(request: Request):
    """OAuth 2.0 Authorization Server Metadata (RFC 8414)."""
    base_url = str(request.base_url).rstrip("/")
    
    return {
        "issuer": base_url,
        "authorization_endpoint": f"{base_url}/oauth/authorize",
        "token_endpoint": f"{base_url}/oauth/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["client_secret_post"],
        "scopes_supported": ["mcp", "read", "write"],
        "code_challenge_methods_supported": ["S256"],  # PKCE support
    }


@oauth_router.get("/authorize")
async def authorize(
    response_type: str,
    client_id: str,
    redirect_uri: str,
    scope: str = "",
    state: str = "",
):
    """OAuth authorization endpoint."""
    if response_type != "code":
        raise HTTPException(
            status_code=400,
            detail="Unsupported response type"
        )
    
    # In production, validate client_id and redirect_uri
    
    # Simple login form (replace with proper UI)
    login_form = f"""
    <html>
    <head><title>Authorize Memory Palace</title></head>
    <body>
        <h2>Authorize Memory Palace MCP Server</h2>
        <p>Client: {client_id}</p>
        <p>Scopes: {scope or 'mcp'}</p>
        <form method="post" action="/oauth/login">
            <input type="hidden" name="client_id" value="{client_id}">
            <input type="hidden" name="redirect_uri" value="{redirect_uri}">
            <input type="hidden" name="state" value="{state}">
            <div>
                <label>Email:</label>
                <input type="email" name="email" required>
            </div>
            <div>
                <label>Password:</label>
                <input type="password" name="password" required>
            </div>
            <button type="submit">Authorize</button>
        </form>
    </body>
    </html>
    """
    
    return HTMLResponse(content=login_form)


# Simple in-memory authorization code store (use Redis in production)
AUTH_CODES = {}


@oauth_router.post("/login")
async def login(
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    state: str = Form(""),
    email: str = Form(...),
    password: str = Form(...),
):
    """Handle login and create authorization code."""
    user = authenticate_user(email, password)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials"
        )
    
    # Generate authorization code
    import secrets
    auth_code = secrets.token_urlsafe(32)
    
    # Store code with user info (expires in 10 minutes)
    AUTH_CODES[auth_code] = {
        "user_id": user["id"],
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "expires_at": (datetime.utcnow() + timedelta(minutes=10)).timestamp(),
    }
    
    # Redirect back to client with authorization code
    params = {"code": auth_code}
    if state:
        params["state"] = state
        
    redirect_url = f"{redirect_uri}?{urlencode(params)}"
    return RedirectResponse(url=redirect_url)


@oauth_router.post("/token")
async def token(
    grant_type: str = Form(...),
    code: str = Form(None),
    refresh_token: str = Form(None),
    client_id: str = Form(...),
    client_secret: str = Form(None),
    redirect_uri: str = Form(None),
):
    """OAuth token endpoint."""
    
    if grant_type == "authorization_code":
        if not code or not redirect_uri:
            raise HTTPException(400, "Missing required parameters")
            
        # Validate authorization code
        auth_data = AUTH_CODES.get(code)
        if not auth_data:
            raise HTTPException(400, "Invalid authorization code")
            
        # Check expiration
        if datetime.utcnow().timestamp() > auth_data["expires_at"]:
            AUTH_CODES.pop(code, None)
            raise HTTPException(400, "Authorization code expired")
            
        # Validate client and redirect URI
        if auth_data["client_id"] != client_id or auth_data["redirect_uri"] != redirect_uri:
            raise HTTPException(400, "Invalid client or redirect URI")
            
        # Remove used code
        AUTH_CODES.pop(code, None)
        
        # Create tokens
        user_id = auth_data["user_id"]
        access_token_expires = timedelta(minutes=OAUTH_ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": user_id}, expires_delta=access_token_expires
        )
        refresh_token = create_refresh_token(data={"sub": user_id})
        
        return TokenData(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=OAUTH_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )
        
    elif grant_type == "refresh_token":
        # Handle refresh token flow
        if not refresh_token:
            raise HTTPException(400, "Missing refresh token")
            
        try:
            from jose import jwt
            from .oauth_auth import OAUTH_SECRET_KEY, OAUTH_ALGORITHM
            
            payload = jwt.decode(refresh_token, OAUTH_SECRET_KEY, algorithms=[OAUTH_ALGORITHM])
            user_id = payload.get("sub")
            token_type = payload.get("type")
            
            if user_id is None or token_type != "refresh":
                raise HTTPException(400, "Invalid refresh token")
                
        except Exception:
            raise HTTPException(400, "Invalid refresh token")
            
        # Create new access token
        access_token_expires = timedelta(minutes=OAUTH_ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": user_id}, expires_delta=access_token_expires
        )
        
        return TokenData(
            access_token=access_token,
            refresh_token=refresh_token,  # Keep same refresh token
            expires_in=OAUTH_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )
    
    else:
        raise HTTPException(400, "Unsupported grant type")


@oauth_router.get("/user")
async def get_user_info(current_user: User = Depends(get_current_user)):
    """Get current user information."""
    return current_user