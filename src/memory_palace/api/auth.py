"""Tunnel-gated bearer authentication.

Requests arriving through the Cloudflare tunnel are identified by the
CF-Connecting-IP / CF-Ray headers that cloudflared injects on every
proxied request; those must present a valid Bearer JWT issued by
/oauth/token. Direct local requests (Claude Code on this machine, dev
tooling, container healthchecks) carry no tunnel headers and are trusted.

A remote caller cannot strip the tunnel headers (cloudflared adds them
after the request reaches the edge), and the only remote path to this
service is the tunnel — port 8000 is not internet-exposed directly.
"""

from fastapi import HTTPException, Request

from memory_palace.api.endpoints.oauth import verify_token
from memory_palace.core.logging import get_logger

logger = get_logger(__name__)

TUNNEL_HEADERS = ("cf-connecting-ip", "cf-ray")


def _unauthorized(request: Request) -> HTTPException:
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    resource_metadata = f"{scheme}://{request.url.netloc}/.well-known/oauth-protected-resource"
    return HTTPException(
        status_code=401,
        detail="Bearer token required for remote access",
        headers={"WWW-Authenticate": f'Bearer resource_metadata="{resource_metadata}"'},
    )


async def require_remote_auth(request: Request) -> None:
    """Allow local traffic; demand a valid Bearer JWT from tunnel traffic."""
    if not any(header in request.headers for header in TUNNEL_HEADERS):
        return  # No tunnel fingerprint: local/LAN request, trusted.

    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        logger.warning(
            "Unauthenticated tunnel request rejected",
            path=request.url.path,
            remote=request.headers.get("cf-connecting-ip", "unknown"),
        )
        raise _unauthorized(request)

    token_data = verify_token(authorization[7:].strip())
    if token_data is None:
        logger.warning(
            "Invalid bearer token on tunnel request",
            path=request.url.path,
            remote=request.headers.get("cf-connecting-ip", "unknown"),
        )
        raise _unauthorized(request)
