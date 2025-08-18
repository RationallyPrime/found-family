"""OAuth endpoints for Claude.ai MCP authentication."""

import os
import secrets
from datetime import UTC, datetime, timedelta

import logfire
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from pydantic import BaseModel

from memory_palace.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["oauth"])

# Store for dynamically registered clients (in production, use a database)
registered_clients = {}

# Store for authorization codes (in production, use Redis or database)
auth_codes = {}

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
    refresh_token: str | None = None


class TokenData(BaseModel):
    """Token payload data."""
    client_id: str
    scopes: list[str] = []


class ClientRegistrationRequest(BaseModel):
    """Dynamic Client Registration Request (RFC 7591)."""
    client_name: str
    redirect_uris: list[str]
    grant_types: list[str] | None = ["authorization_code"]
    response_types: list[str] | None = ["code"]
    scope: str | None = "read write"
    token_endpoint_auth_method: str | None = "client_secret_post"


class ClientRegistrationResponse(BaseModel):
    """Dynamic Client Registration Response (RFC 7591)."""
    client_id: str
    client_secret: str
    client_name: str
    redirect_uris: list[str]
    grant_types: list[str]
    response_types: list[str]
    scope: str
    token_endpoint_auth_method: str
    client_id_issued_at: int
    client_secret_expires_at: int = 0  # 0 means never expires


@router.get("/.well-known/oauth-authorization-server", operation_id="oauth_metadata")
@router.head("/.well-known/oauth-authorization-server")
@router.get("/.well-known/oauth-authorization-server/mcp", operation_id="oauth_metadata_mcp")
@router.head("/.well-known/oauth-authorization-server/mcp")
async def oauth_metadata(request: Request):
    """OAuth 2.0 Authorization Server Metadata (RFC 8414) with DCR support."""
    # Detect if request came through HTTPS (Cloudflare)
    proto = request.headers.get("x-forwarded-proto", "http")
    host = request.headers.get("host", "localhost:8000")
    base_url = f"{proto}://{host}"

    return {
        "issuer": base_url,
        "authorization_endpoint": f"{base_url}/oauth/authorize",
        "token_endpoint": f"{base_url}/oauth/token",
        "registration_endpoint": f"{base_url}/oauth/register",  # DCR endpoint
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "none"],
        "scopes_supported": ["read", "write"],
        "code_challenge_methods_supported": ["S256"],
        "introspection_endpoint": f"{base_url}/oauth/introspect",
        "revocation_endpoint": f"{base_url}/oauth/revoke",
        "jwks_uri": f"{base_url}/.well-known/jwks.json",
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["HS256"],
        "userinfo_endpoint": f"{base_url}/oauth/userinfo",
        "claims_supported": ["sub", "name", "email"],
        "response_modes_supported": ["query", "fragment"],
        "token_endpoint_auth_signing_alg_values_supported": ["HS256"]
    }


@router.get("/.well-known/mcp", operation_id="mcp_discovery")
@router.head("/.well-known/mcp")
async def mcp_discovery(request: Request):
    """MCP discovery endpoint for Claude.ai."""
    # Detect if request came through HTTPS (Cloudflare)
    proto = request.headers.get("x-forwarded-proto", "http")
    host = request.headers.get("host", "localhost:8000")
    base_url = f"{proto}://{host}"
    
    # Get protocol version from header or use latest
    protocol_version = request.headers.get("mcp-protocol-version", "2024-11-05")
    user_agent = request.headers.get("user-agent", "")[:100]
    
    with logfire.span("MCP discovery for {client}", client=user_agent) as span:
        # Set span attributes for debugging
        span.set_attributes({
            "mcp.protocol_version": protocol_version,
            "mcp.base_url": base_url,
            "request.proto": proto,
            "request.host": host,
            "request.user_agent": user_agent,
            "cloudflare.forwarded_proto": request.headers.get("x-forwarded-proto"),
            "mcp.transport_protocol": "streamable-http"
        })
        
        # Structured logging for debugging
        logger.info("MCP discovery request", 
                   protocol_version=protocol_version,
                   base_url=base_url,
                   client_user_agent=user_agent,
                   forwarded_proto=proto,
                   transport_protocol="streamable-http")
        
        discovery_response = {
            "protocolVersion": protocol_version,
            "endpoint": f"{base_url}/mcp",
            "protocol": "streamable-http",
            "name": "Memory Palace",
            "description": "Persistent memory system for AI conversations",
            "application_slug": "memory-palace",
            "app": {
                "id": "memory-palace",
                "name": "Memory Palace",
                "slug": "memory-palace"
            },
            "oauth": {
                "authorization_server": base_url,
                "resource": f"{base_url}/mcp",
                "scopes": ["read", "write"]
            }
        }
        
        # Log the response for debugging
        logger.info("MCP discovery response", 
                   endpoint=discovery_response["endpoint"],
                   protocol=discovery_response["protocol"],
                   oauth_server=discovery_response["oauth"]["authorization_server"])
        
        return discovery_response

@router.get("/.well-known/oauth-protected-resource", operation_id="oauth_resource")
@router.head("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp", operation_id="oauth_resource_mcp")
@router.head("/.well-known/oauth-protected-resource/mcp")
async def oauth_protected_resource(request: Request):
    """OAuth 2.0 Protected Resource Metadata (RFC 9728).
    
    This indicates that the MCP resource is protected and requires OAuth.
    """
    # Detect if request came through HTTPS (Cloudflare)
    proto = request.headers.get("x-forwarded-proto", "http")
    host = request.headers.get("host", "localhost:8000")
    base_url = f"{proto}://{host}"
    
    return {
        "resource": f"{base_url}/mcp",
        "authorization_servers": [
            base_url  # We are our own auth server
        ],
        "scopes_supported": ["read", "write"],
        "bearer_methods_supported": ["header"],
        "resource_documentation": f"{base_url}/mcp",
        "resource_signing_alg_values_supported": ["HS256"]
    }


@router.post("/oauth/register", response_model=ClientRegistrationResponse, operation_id="register")
async def register_client(request: ClientRegistrationRequest):
    """Dynamic Client Registration endpoint (RFC 7591)."""
    import time
    
    # Generate unique client credentials
    client_id = f"client_{secrets.token_urlsafe(16)}"
    client_secret = secrets.token_urlsafe(32)
    
    # Store the registered client
    registered_clients[client_id] = {
        "client_secret": client_secret,
        "client_name": request.client_name,
        "redirect_uris": request.redirect_uris,
        "grant_types": request.grant_types or ["authorization_code"],
        "response_types": request.response_types or ["code"],
        "scope": request.scope or "read write",
    }
    
    return ClientRegistrationResponse(
        client_id=client_id,
        client_secret=client_secret,
        client_name=request.client_name,
        redirect_uris=request.redirect_uris,
        grant_types=request.grant_types or ["authorization_code"],
        response_types=request.response_types or ["code"],
        scope=request.scope or "read write",
        token_endpoint_auth_method=request.token_endpoint_auth_method or "client_secret_post",
        client_id_issued_at=int(time.time()),
        client_secret_expires_at=0,  # Never expires
    )


@router.get("/oauth/authorize", operation_id="authorize_get")
@logfire.instrument()
async def authorize(
    response_type: str,
    client_id: str,
    redirect_uri: str,
    scope: str = "read write",
    state: str | None = None,
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,  # noqa: ARG001
):
    """OAuth authorization endpoint - supports dynamic clients."""
    
    # Structured logging for OAuth flow debugging
    logger.info("OAuth authorization request",
               client_id=client_id,
               response_type=response_type,
               redirect_uri=redirect_uri,
               scope=scope,
               has_pkce=bool(code_challenge))

    if response_type != "code":
        raise HTTPException(status_code=400, detail="Unsupported response_type")

    # Validate client exists (either registered or known)
    if client_id not in registered_clients and not client_id.startswith("client_"):
        logger.warning(f"Unknown client_id: {client_id}")
        # Auto-register Claude if needed
        if "claude" in client_id.lower():
            registered_clients[client_id] = {
                "client_secret": secrets.token_urlsafe(32),
                "client_name": "Claude",
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "scope": scope,
            }
            logger.info(f"Auto-registered Claude client: {client_id}")

    # Auto-approve for Claude (no consent screen)
    auth_code = secrets.token_urlsafe(32)

    # Store auth code (in production, use Redis or database)
    code_data = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "exp": datetime.now(UTC) + timedelta(minutes=10)
    }
    auth_codes[auth_code] = code_data
    logger.info(f"Stored auth code for client {client_id}")

    # Build redirect URL
    params = {"code": auth_code}
    if state:
        params["state"] = state

    redirect_url = f"{redirect_uri}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
    return RedirectResponse(url=redirect_url)


@router.post("/oauth/token", response_model=TokenResponse, operation_id="token")
async def token(
    grant_type: str = Form(...),
    code: str | None = Form(None),
    client_id: str = Form(...),
    client_secret: str = Form(None),  # Make optional for open access  # noqa: ARG001
    refresh_token: str | None = Form(None),
):
    """OAuth token endpoint - supports dynamic clients."""

    # Accept any client for now (Claude uses dynamic registration)
    # In production, validate against persistent store
    logger.info(f"OAuth token request from client: {client_id}")

    if grant_type == "authorization_code":
        if not code:
            raise HTTPException(status_code=400, detail="Missing authorization code")

        # Validate the authorization code
        if code not in auth_codes:
            logger.warning(f"Invalid authorization code: {code[:20]}...")
            raise HTTPException(status_code=400, detail="Invalid authorization code")
        
        code_data = auth_codes[code]
        
        # Check if code is expired
        if datetime.now(UTC) > code_data["exp"]:
            logger.warning("Authorization code expired")
            del auth_codes[code]
            raise HTTPException(status_code=400, detail="Authorization code expired")
        
        # Validate client_id matches
        if code_data["client_id"] != client_id:
            logger.warning(f"Client ID mismatch: {client_id} != {code_data['client_id']}")
            raise HTTPException(status_code=400, detail="Client ID mismatch")
        
        # Remove used code
        del auth_codes[code]
        logger.info(f"Authorization code validated and consumed for client {client_id}")

    elif grant_type == "refresh_token":
        if not refresh_token:
            raise HTTPException(status_code=400, detail="Missing refresh token")

        # Validate refresh token
        try:
            payload = jwt.decode(refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
            if payload.get("type") != "refresh":
                raise HTTPException(status_code=401, detail="Invalid refresh token")
        except JWTError:
            raise HTTPException(status_code=401, detail="Invalid refresh token")  # noqa: B904

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


def create_access_token(data: dict, expires_delta: timedelta | None = None):
    """Create a JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(UTC) + expires_delta if expires_delta else datetime.now(UTC) + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)

    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(data: dict):
    """Create a JWT refresh token."""
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(days=90)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> TokenData | None:
    """Verify and decode a JWT token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        client_id: str = payload.get("sub")
        if client_id is None:
            return None
        scopes: list[str] = payload.get("scopes", [])
        return TokenData(client_id=client_id, scopes=scopes)
    except JWTError:
        return None


@router.post("/oauth/introspect", operation_id="introspect")
async def introspect_token(token: str = Form(...)):
    """Token introspection endpoint (RFC 7662)."""
    token_data = verify_token(token)
    if token_data:
        return {
            "active": True,
            "client_id": token_data.client_id,
            "scope": " ".join(token_data.scopes),
            "token_type": "Bearer"
        }
    return {"active": False}


@router.post("/oauth/revoke", operation_id="revoke")
async def revoke_token(token: str = Form(...)):  # noqa: ARG001
    """Token revocation endpoint (RFC 7009)."""
    # For simplicity, just return success (tokens are stateless JWTs)
    return {"revoked": True}


@router.get("/.well-known/jwks.json", operation_id="jwks")
async def jwks():
    """JSON Web Key Set endpoint."""
    # For simplicity, return empty JWKS (we use HMAC)
    return {"keys": []}


@router.get("/oauth/userinfo", operation_id="userinfo")
async def userinfo(authorization: str = ""):
    """UserInfo endpoint (OpenID Connect)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
    
    token = authorization.split(" ")[1]
    token_data = verify_token(token)
    if not token_data:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    return {
        "sub": token_data.client_id,
        "name": "Claude MCP Client",
        "email": "claude@anthropic.com"
    }


