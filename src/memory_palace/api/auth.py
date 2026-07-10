"""Tunnel-gated bearer authentication.

Production always requires a valid access token on protected routes.
Development retains one ergonomic exception: a direct loopback request
without tunnel headers may call protected routes without OAuth. Header
absence alone is never treated as proof of locality.
"""

from ipaddress import ip_address

from fastapi import HTTPException, Request

from memory_palace.api.endpoints.oauth import OAuthScope, TokenData, verify_token
from memory_palace.core.config import Environment, settings
from memory_palace.core.logging import get_logger

logger = get_logger(__name__)

TUNNEL_HEADERS = ("cf-connecting-ip", "cf-ray")


def _unauthorized() -> HTTPException:
    resource_metadata = f"{settings.public_base_url_value}/.well-known/oauth-protected-resource"
    return HTTPException(
        status_code=401,
        detail="Bearer token required for remote access",
        headers={"WWW-Authenticate": f'Bearer resource_metadata="{resource_metadata}"'},
    )


async def _authenticate(request: Request) -> TokenData | None:
    """Authenticate a request, returning ``None`` only for the dev loopback bypass."""
    authorization = request.headers.get("authorization", "")
    scheme, _, token_value = authorization.partition(" ")
    if scheme.lower() == "bearer" and token_value:
        token_data = verify_token(token_value.strip())
        if token_data is not None:
            return token_data

    client_host = request.client.host if request.client is not None else ""
    direct_request = not any(header in request.headers for header in TUNNEL_HEADERS)
    if settings.environment is not Environment.PRODUCTION and direct_request and _is_loopback(client_host):
        return None

    logger.warning(
        "Protected request rejected",
        path=request.url.path,
        remote=request.headers.get("cf-connecting-ip", client_host or "unknown"),
        tunnel=not direct_request,
        authorization_present=bool(authorization),
    )
    raise _unauthorized()


async def require_remote_auth(request: Request) -> None:
    """Require a valid access token, except direct loopback traffic in development."""
    await _authenticate(request)


async def require_read_auth(request: Request) -> None:
    """Require a read-scoped access token outside the development bypass."""
    await _require_scope(request, "read")


async def require_write_auth(request: Request) -> None:
    """Require a write-scoped access token outside the development bypass."""
    await _require_scope(request, "write")


async def _require_scope(request: Request, required_scope: OAuthScope) -> None:
    token_data = await _authenticate(request)
    if token_data is None:
        return
    if required_scope not in token_data.scopes:
        raise HTTPException(
            status_code=403,
            detail=f"The '{required_scope}' OAuth scope is required",
        )


def _is_loopback(host: str) -> bool:
    """Return false for malformed or non-IP client addresses."""
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False
