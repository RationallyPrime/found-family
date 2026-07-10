"""Minimal OAuth 2.1-style authorization-code flow for Claude.ai MCP.

This is a single-user authorization server. Dynamic registration is retained
for client compatibility, but policy is deliberately narrow: callbacks are
allowlisted, registrations are deterministic, clients are public, S256 PKCE is
mandatory, and authorization codes are short-lived, hashed, and single-use.
"""

import asyncio
import base64
import hashlib
import re
import secrets
import time
from collections import OrderedDict, deque
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from typing import Annotated, Literal, cast
from unicodedata import category
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import jwt
import logfire
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from jwt import PyJWTError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from memory_palace.core.config import settings
from memory_palace.core.logging import get_logger
from memory_palace.infrastructure.oauth import (
    AuthorizationCode,
    OAuthApplicationType,
    OAuthClient,
    OAuthGrantType,
    OAuthScope,
    OAuthStateStore,
    RefreshTokenState,
)

logger = get_logger(__name__)
router = APIRouter(tags=["oauth"])
owner_basic = HTTPBasic(auto_error=False)

ALGORITHM = "HS256"
AUTH_CODE_TTL_SECONDS = 600
SUPPORTED_SCOPES = frozenset({"read", "write"})
_PKCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_NATIVE_PUBLIC_CLIENT_ID = "client_native_loopback"
_MAX_REDIRECT_URI_CHARS = 2_048
_CLAUDE_CALLBACKS = frozenset(
    {
        "https://claude.ai/api/mcp/auth_callback",
        "https://claude.com/api/mcp/auth_callback",
    }
)


class RequestRateLimiter:
    """Bounded fixed-window limiter for credential and state endpoints."""

    def __init__(self, *, requests: int, window_seconds: float, max_clients: int = 4_096) -> None:
        self._requests = requests
        self._window_seconds = window_seconds
        self._max_clients = max_clients
        self._attempts: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def __call__(self, request: Request) -> None:
        client_host = request.client.host if request.client is not None else "unknown"
        if "cf-ray" in request.headers:
            client_host = request.headers.get("cf-connecting-ip", client_host)
        now = time.monotonic()

        async with self._lock:
            attempts = self._attempts.setdefault(client_host, deque())
            self._attempts.move_to_end(client_host)
            while attempts and now - attempts[0] >= self._window_seconds:
                attempts.popleft()
            if len(attempts) >= self._requests:
                raise HTTPException(
                    status_code=429,
                    detail="Too many OAuth requests",
                    headers={"Retry-After": str(int(self._window_seconds))},
                )
            attempts.append(now)
            while len(self._attempts) > self._max_clients:
                self._attempts.popitem(last=False)


oauth_rate_limit = RequestRateLimiter(requests=30, window_seconds=60.0)
owner_rate_limit = RequestRateLimiter(requests=10, window_seconds=60.0)


def _secret_key() -> str:
    secret = settings.jwt_secret_key_value
    if len(secret) < 32:
        raise RuntimeError(
            "JWT_SECRET_KEY must contain at least 32 characters. Generate one with: "
            'python3 -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    return secret


SECRET_KEY = _secret_key()


def _base_url() -> str:
    """Use configured public origin; request Host headers are untrusted."""
    return settings.public_base_url_value


def _audience() -> str:
    return f"{_base_url()}/mcp"


def _parse_scopes(scope: str) -> tuple[OAuthScope, ...]:
    values = tuple(dict.fromkeys(scope.split()))
    if not values or not set(values).issubset(SUPPORTED_SCOPES):
        raise HTTPException(status_code=400, detail="Unsupported OAuth scope")
    return cast("tuple[OAuthScope, ...]", tuple(values))


def _validate_scope_field(scope: str) -> str:
    values = tuple(dict.fromkeys(scope.split()))
    if not values or not set(values).issubset(SUPPORTED_SCOPES):
        raise ValueError("Unsupported OAuth scope")
    return " ".join(values)


def _redirect_with_parameters(redirect_uri: str, parameters: dict[str, str]) -> str:
    """Append OAuth response parameters without corrupting an existing query."""
    parts = urlsplit(redirect_uri)
    merged_query = urlencode([*parse_qsl(parts.query, keep_blank_values=True), *parameters.items()])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, merged_query, parts.fragment))


def _is_native_loopback_redirect(redirect_uri: str) -> bool:
    """Validate an RFC 8252-style HTTP callback on a literal loopback IP."""
    if not 1 <= len(redirect_uri) <= _MAX_REDIRECT_URI_CHARS or _has_control_characters(redirect_uri):
        return False
    try:
        parts = urlsplit(redirect_uri)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return False
    if (
        parts.scheme != "http"
        or host is None
        or port is None
        or parts.username is not None
        or parts.password is not None
        or bool(parts.fragment)
    ):
        return False
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _has_control_characters(value: str) -> bool:
    """Reject C0, DEL, and C1 characters before URL parsing normalizes them."""
    return any(category(character) == "Cc" for character in value)


def _redirect_allowed(redirect_uri: str, application_type: OAuthApplicationType | None) -> bool:
    """Apply configured web policy plus the OAuth native-loopback exception."""
    return redirect_uri in settings.allowed_redirect_uri_values or (
        application_type == "native" and _is_native_loopback_redirect(redirect_uri)
    )


def get_oauth_store(request: Request) -> OAuthStateStore:
    """Resolve the OAuth state store from app state."""
    store = getattr(request.app.state, "oauth_store", None)
    if not isinstance(store, OAuthStateStore):
        raise HTTPException(status_code=503, detail="OAuth store not initialized")
    return store


def require_owner_auth(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(owner_basic)],
) -> str:
    """Authenticate the human resource owner before issuing an OAuth code."""
    expected_password = settings.oauth_owner_password_value
    if len(expected_password) < 16:
        raise HTTPException(status_code=503, detail="OAuth owner authentication is not configured")

    supplied_username = "" if credentials is None else credentials.username
    supplied_password = "" if credentials is None else credentials.password
    username_matches = secrets.compare_digest(
        hashlib.sha256(supplied_username.encode()).digest(),
        hashlib.sha256(settings.oauth_owner_username.encode()).digest(),
    )
    password_matches = secrets.compare_digest(
        hashlib.sha256(supplied_password.encode()).digest(),
        hashlib.sha256(expected_password.encode()).digest(),
    )
    if not (username_matches and password_matches):
        raise HTTPException(
            status_code=401,
            detail="Resource owner authentication required",
            headers={"WWW-Authenticate": 'Basic realm="Memory Palace authorization", charset="UTF-8"'},
        )
    return supplied_username


class TokenResponse(BaseModel):
    """OAuth token response."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - OAuth token type, not a credential
    expires_in: int
    refresh_token: str


class TokenData(BaseModel):
    """Validated access-token identity."""

    client_id: str
    scopes: list[OAuthScope] = Field(default_factory=list)


class ClientRegistrationRequest(BaseModel):
    """Bounded Dynamic Client Registration request accepted by this service."""

    # RFC 7591 metadata is extensible. Unknown display/discovery fields cannot
    # influence server policy; the security-relevant fields below remain typed.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    client_name: str | None = Field(default=None, min_length=1, max_length=128)
    redirect_uris: list[Annotated[str, Field(min_length=1, max_length=_MAX_REDIRECT_URI_CHARS)]] = Field(
        min_length=1,
        max_length=4,
    )
    grant_types: tuple[OAuthGrantType, ...] = Field(default=("authorization_code",), min_length=1, max_length=2)
    response_types: tuple[Literal["code"], ...] = ("code",)
    scope: str = Field(default="read write", min_length=1, max_length=32)
    application_type: OAuthApplicationType | None = None
    token_endpoint_auth_method: Literal["none"] = "none"  # noqa: S105 - OAuth method name

    @field_validator("redirect_uris")
    @classmethod
    def unique_redirect_uris(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("redirect_uris must be unique")
        if any(_has_control_characters(uri) for uri in value):
            raise ValueError("redirect_uris must not contain control characters")
        return value

    @field_validator("grant_types")
    @classmethod
    def supported_grant_contract(cls, value: tuple[OAuthGrantType, ...]) -> tuple[OAuthGrantType, ...]:
        if len(value) != len(set(value)) or "authorization_code" not in value:
            raise ValueError("grant_types must contain authorization_code without duplicates")
        return value

    @field_validator("scope")
    @classmethod
    def supported_scope(cls, value: str) -> str:
        return _validate_scope_field(value)


class ClientRegistrationResponse(BaseModel):
    """Dynamic Client Registration response for a public client."""

    client_id: str
    client_name: str
    redirect_uris: list[str]
    grant_types: list[OAuthGrantType]
    response_types: list[Literal["code"]]
    scope: str
    application_type: OAuthApplicationType | None = None
    token_endpoint_auth_method: Literal["none"] = "none"  # noqa: S105 - OAuth method name
    client_id_issued_at: int
    client_secret_expires_at: int = 0


@router.get("/.well-known/oauth-authorization-server", operation_id="oauth_metadata")
@router.head("/.well-known/oauth-authorization-server")
@router.get("/.well-known/oauth-authorization-server/mcp", operation_id="oauth_metadata_mcp")
@router.head("/.well-known/oauth-authorization-server/mcp")
async def oauth_metadata(_request: Request) -> dict[str, object]:
    """OAuth Authorization Server Metadata with only implemented features."""
    base_url = _base_url()
    return {
        "issuer": base_url,
        "authorization_endpoint": f"{base_url}/oauth/authorize",
        "token_endpoint": f"{base_url}/oauth/token",
        "registration_endpoint": f"{base_url}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": sorted(SUPPORTED_SCOPES),
        "code_challenge_methods_supported": ["S256"],
    }


@router.get("/.well-known/mcp", operation_id="mcp_discovery")
@router.head("/.well-known/mcp")
async def mcp_discovery(request: Request) -> dict[str, object]:
    """MCP discovery document rooted at the configured public origin."""
    base_url = _base_url()
    requested_version = request.headers.get("mcp-protocol-version", "2024-11-05")
    protocol_version = requested_version if len(requested_version) <= 32 else "2024-11-05"
    user_agent = request.headers.get("user-agent", "")[:100]

    with logfire.span("MCP discovery for {client}", client=user_agent):
        logger.info("MCP discovery request", protocol_version=protocol_version, client_user_agent=user_agent)
        return {
            "protocolVersion": protocol_version,
            "endpoint": f"{base_url}/mcp",
            "protocol": "streamable-http",
            "name": "Memory Palace",
            "description": "Persistent memory system for AI conversations",
            "oauth": {
                "authorization_server": base_url,
                "resource": f"{base_url}/mcp",
                "scopes": sorted(SUPPORTED_SCOPES),
            },
        }


@router.get("/.well-known/oauth-protected-resource", operation_id="oauth_resource")
@router.head("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp", operation_id="oauth_resource_mcp")
@router.head("/.well-known/oauth-protected-resource/mcp")
async def oauth_protected_resource(_request: Request) -> dict[str, object]:
    """OAuth Protected Resource Metadata."""
    base_url = _base_url()
    return {
        "resource": f"{base_url}/mcp",
        "authorization_servers": [base_url],
        "scopes_supported": sorted(SUPPORTED_SCOPES),
        "bearer_methods_supported": ["header"],
    }


@router.post(
    "/oauth/register",
    response_model=ClientRegistrationResponse,
    status_code=201,
    operation_id="register",
    dependencies=[Depends(oauth_rate_limit)],
)
async def register_client(
    request: ClientRegistrationRequest,
    store: OAuthStateStore = Depends(get_oauth_store),
) -> ClientRegistrationResponse:
    """Register one server-canonical public-client shape."""
    configured_redirects = set(request.redirect_uris).issubset(settings.allowed_redirect_uri_values)
    native_loopback_redirects = request.application_type == "native" and all(
        _is_native_loopback_redirect(uri) for uri in request.redirect_uris
    )
    if not configured_redirects and not native_loopback_redirects:
        raise HTTPException(status_code=400, detail="redirect_uri is not approved for this server")

    scopes = _parse_scopes(request.scope)
    if frozenset(scopes) != SUPPORTED_SCOPES:
        raise HTTPException(status_code=400, detail="Client registration must include the server's canonical scopes")

    redirect_set = frozenset(request.redirect_uris)
    if redirect_set.issubset(_CLAUDE_CALLBACKS):
        client_name = "Claude"
    elif native_loopback_redirects:
        client_name = "Native MCP client"
    else:
        client_name = "Approved MCP client"
    application_type: OAuthApplicationType = "native" if native_loopback_redirects else "web"
    if native_loopback_redirects:
        # Native clients are public, and ephemeral callback ports/paths are part
        # of their normal operation. One stateless client identity avoids
        # permanently growing the datastore for every local Codex invocation.
        client_id = _NATIVE_PUBLIC_CLIENT_ID
    else:
        fingerprint = "\n".join(sorted(request.redirect_uris)).encode("utf-8")
        client_id = f"client_{hashlib.sha256(fingerprint).hexdigest()[:32]}"
    client = OAuthClient(
        client_id=client_id,
        client_name=client_name,
        redirect_uris=tuple(request.redirect_uris),
        grant_types=("authorization_code", "refresh_token"),
        response_types=("code",),
        scopes=("read", "write"),
        application_type=application_type,
        token_endpoint_auth_method="none",  # noqa: S106 - OAuth method name
    )
    if not native_loopback_redirects:
        # DCR is unauthenticated. Replacing the complete validated record
        # prevents an attacker from poisoning this deterministic web client id
        # as first writer. Native registrations are deliberately stateless.
        await store.save_client(client)

    return ClientRegistrationResponse(
        client_id=client_id,
        client_name=client.client_name,
        redirect_uris=list(client.redirect_uris),
        grant_types=list(client.grant_types),
        response_types=["code"],
        scope=" ".join(client.scopes),
        application_type=client.application_type,
        client_id_issued_at=int(time.time()),
    )


@router.get("/oauth/authorize", operation_id="authorize_get", dependencies=[Depends(owner_rate_limit)])
@logfire.instrument()
async def authorize(
    response_type: str,
    client_id: str,
    redirect_uri: str,
    _owner: Annotated[str, Depends(require_owner_auth)],
    scope: str = "read write",
    state: str | None = None,
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
    store: OAuthStateStore = Depends(get_oauth_store),
) -> RedirectResponse:
    """Issue a short-lived code to a registered, approved callback."""
    if response_type != "code":
        raise HTTPException(status_code=400, detail="Unsupported response_type")
    if state is not None and len(state) > 1_024:
        raise HTTPException(status_code=400, detail="state is too long")

    if client_id == _NATIVE_PUBLIC_CLIENT_ID:
        if not _is_native_loopback_redirect(redirect_uri):
            raise HTTPException(status_code=400, detail="redirect_uri is not approved for this client")
        client_scopes: tuple[OAuthScope, ...] = ("read", "write")
    else:
        client = await store.get_client(client_id)
        if client is None:
            raise HTTPException(status_code=400, detail="Unknown client_id")
        if redirect_uri not in client.redirect_uris or not _redirect_allowed(redirect_uri, client.application_type):
            raise HTTPException(status_code=400, detail="redirect_uri is not approved for this client")
        client_scopes = client.scopes

    scopes = _parse_scopes(scope)
    if not set(scopes).issubset(client_scopes):
        raise HTTPException(status_code=400, detail="Requested scope exceeds client registration")
    if code_challenge_method != "S256" or code_challenge is None or not _PKCE_PATTERN.fullmatch(code_challenge):
        raise HTTPException(status_code=400, detail="Valid S256 PKCE challenge required")

    auth_code = secrets.token_urlsafe(32)
    await store.save_auth_code(
        auth_code,
        AuthorizationCode(
            client_id=client_id,
            redirect_uri=redirect_uri,
            scopes=scopes,
            code_challenge=code_challenge,
            code_challenge_method="S256",
        ),
        ttl_seconds=AUTH_CODE_TTL_SECONDS,
    )

    params = {"code": auth_code}
    if state is not None:
        params["state"] = state
    return RedirectResponse(url=_redirect_with_parameters(redirect_uri, params))


def _verify_pkce(code_data: AuthorizationCode, code_verifier: str | None) -> None:
    """Verify an RFC 7636 S256 code verifier in constant time."""
    if code_verifier is None or not _PKCE_PATTERN.fullmatch(code_verifier):
        raise HTTPException(status_code=400, detail="Valid code_verifier required")
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    if not secrets.compare_digest(computed, code_data.code_challenge):
        raise HTTPException(status_code=400, detail="PKCE verification failed")


@router.post(
    "/oauth/token",
    response_model=TokenResponse,
    operation_id="token",
    dependencies=[Depends(oauth_rate_limit)],
)
async def token(
    grant_type: Annotated[str, Form()],
    client_id: Annotated[str, Form()],
    response: Response,
    code: Annotated[str | None, Form()] = None,
    redirect_uri: Annotated[str | None, Form()] = None,
    code_verifier: Annotated[str | None, Form()] = None,
    refresh_token: Annotated[str | None, Form()] = None,
    store: OAuthStateStore = Depends(get_oauth_store),
) -> TokenResponse | JSONResponse:
    """Exchange an authorization code or refresh token for a short access token."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    client = None if client_id == _NATIVE_PUBLIC_CLIENT_ID else await store.get_client(client_id)
    if client_id != _NATIVE_PUBLIC_CLIENT_ID and client is None:
        return JSONResponse(
            status_code=401,
            content={"error": "invalid_client"},
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    scopes: tuple[OAuthScope, ...]
    presented_refresh_token: str | None = None
    if grant_type == "authorization_code":
        if code is None or redirect_uri is None:
            raise HTTPException(status_code=400, detail="code and redirect_uri are required")

        code_data = await store.get_auth_code(code)
        if code_data is None:
            raise HTTPException(status_code=400, detail="Invalid or expired authorization code")
        if code_data.client_id != client_id or code_data.redirect_uri != redirect_uri:
            raise HTTPException(status_code=400, detail="Authorization code binding mismatch")
        if client_id == _NATIVE_PUBLIC_CLIENT_ID and not _is_native_loopback_redirect(redirect_uri):
            raise HTTPException(status_code=400, detail="Authorization code binding mismatch")
        _verify_pkce(code_data, code_verifier)

        consumed = await store.consume_auth_code(code)
        if consumed is None:
            raise HTTPException(status_code=400, detail="Authorization code already used")
        scopes = consumed.scopes
        refresh_family_id = secrets.token_urlsafe(24)
    elif grant_type == "refresh_token":
        if refresh_token is None:
            raise HTTPException(status_code=400, detail="refresh_token is required")
        scopes, refresh_family_id = _decode_refresh_token(refresh_token, client_id)
        presented_refresh_token = refresh_token
    else:
        raise HTTPException(status_code=400, detail="Unsupported grant type")

    access_token = create_access_token(client_id, scopes)
    rotated_refresh_token = create_refresh_token(client_id, scopes, refresh_family_id)
    refresh_state = RefreshTokenState(client_id=client_id, scopes=scopes, family_id=refresh_family_id)
    refresh_ttl_seconds = settings.oauth_refresh_token_days * 86_400
    if presented_refresh_token is None:
        await store.save_refresh_token(rotated_refresh_token, refresh_state, ttl_seconds=refresh_ttl_seconds)
    elif not await store.rotate_refresh_token(
        presented_refresh_token,
        rotated_refresh_token,
        refresh_state,
        ttl_seconds=refresh_ttl_seconds,
    ):
        raise HTTPException(status_code=401, detail="Invalid or replayed refresh token")
    return TokenResponse(
        access_token=access_token,
        refresh_token=rotated_refresh_token,
        expires_in=settings.oauth_access_token_minutes * 60,
    )


def _claims(
    client_id: str, scopes: tuple[OAuthScope, ...], token_type: Literal["access", "refresh"], ttl: timedelta
) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "sub": client_id,
        "iss": _base_url(),
        "aud": _audience(),
        "iat": now,
        "exp": now + ttl,
        "jti": secrets.token_urlsafe(16),
        "type": token_type,
        "scopes": list(scopes),
    }


def create_access_token(client_id: str, scopes: tuple[OAuthScope, ...] = ("read", "write")) -> str:
    """Create a signed, audience-bound, short-lived access token."""
    claims = _claims(client_id, scopes, "access", timedelta(minutes=settings.oauth_access_token_minutes))
    return jwt.encode(claims, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(
    client_id: str,
    scopes: tuple[OAuthScope, ...] = ("read", "write"),
    family_id: str | None = None,
) -> str:
    """Create a signed refresh token; it is never accepted as bearer access."""
    claims = _claims(client_id, scopes, "refresh", timedelta(days=settings.oauth_refresh_token_days))
    claims["family"] = family_id or secrets.token_urlsafe(24)
    return jwt.encode(claims, SECRET_KEY, algorithm=ALGORITHM)


def _decode_claims(token_value: str) -> dict[str, object]:
    return jwt.decode(
        token_value,
        SECRET_KEY,
        algorithms=[ALGORITHM],
        audience=_audience(),
        issuer=_base_url(),
    )


def _validated_scopes(payload: dict[str, object]) -> tuple[OAuthScope, ...] | None:
    raw_scopes = payload.get("scopes")
    if not isinstance(raw_scopes, list) or not all(isinstance(scope, str) for scope in raw_scopes):
        return None
    if not set(raw_scopes).issubset(SUPPORTED_SCOPES):
        return None
    return cast("tuple[OAuthScope, ...]", tuple(raw_scopes))


def _decode_refresh_token(token_value: str, expected_client_id: str) -> tuple[tuple[OAuthScope, ...], str]:
    try:
        payload = _decode_claims(token_value)
    except PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid refresh token") from exc
    scopes = _validated_scopes(payload)
    family_id = payload.get("family")
    if (
        payload.get("type") != "refresh"
        or payload.get("sub") != expected_client_id
        or scopes is None
        or not isinstance(family_id, str)
        or len(family_id) < 16
    ):
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    return scopes, family_id


def verify_token(token_value: str) -> TokenData | None:
    """Validate an access token. Refresh tokens fail closed here."""
    try:
        payload = _decode_claims(token_value)
    except PyJWTError:
        return None
    scopes = _validated_scopes(payload)
    client_id = payload.get("sub")
    if payload.get("type") != "access" or not isinstance(client_id, str) or scopes is None:
        return None
    return TokenData(client_id=client_id, scopes=list(scopes))
