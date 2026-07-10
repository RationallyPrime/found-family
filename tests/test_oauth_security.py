"""OAuth boundary regression tests."""

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.security import HTTPBasicCredentials
from pydantic import SecretStr, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from memory_palace.api.endpoints.oauth import (
    ClientRegistrationRequest,
    ClientRegistrationResponse,
    RequestRateLimiter,
    TokenResponse,
    authorize,
    create_access_token,
    create_refresh_token,
    oauth_metadata,
    register_client,
    require_owner_auth,
    token,
    verify_token,
)
from memory_palace.api.endpoints.oauth import (
    router as oauth_router,
)
from memory_palace.core.config import settings
from memory_palace.infrastructure.oauth.models import AuthorizationCode, OAuthClient, RefreshTokenState


class InMemoryOAuthStateStore:
    """Small protocol fake; security behavior belongs to the endpoint layer."""

    def __init__(self) -> None:
        self.clients: dict[str, OAuthClient] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.refresh_tokens: dict[str, RefreshTokenState] = {}

    async def get_client(self, client_id: str) -> OAuthClient | None:
        return self.clients.get(client_id)

    async def save_client(self, client: OAuthClient) -> None:
        self.clients[client.client_id] = client

    async def save_auth_code(self, code: str, data: AuthorizationCode, ttl_seconds: int) -> None:
        assert ttl_seconds > 0
        self.codes[code] = data

    async def get_auth_code(self, code: str) -> AuthorizationCode | None:
        return self.codes.get(code)

    async def consume_auth_code(self, code: str) -> AuthorizationCode | None:
        return self.codes.pop(code, None)

    async def save_refresh_token(self, token: str, data: RefreshTokenState, ttl_seconds: int) -> None:
        assert ttl_seconds > 0
        self.refresh_tokens[token] = data

    async def rotate_refresh_token(
        self,
        presented_token: str,
        replacement_token: str,
        data: RefreshTokenState,
        ttl_seconds: int,
    ) -> bool:
        assert ttl_seconds > 0
        current = self.refresh_tokens.get(presented_token)
        if current != data:
            self.refresh_tokens = {
                token: state for token, state in self.refresh_tokens.items() if state.family_id != data.family_id
            }
            return False
        del self.refresh_tokens[presented_token]
        self.refresh_tokens[replacement_token] = data
        return True


def _registration(**overrides: object) -> ClientRegistrationRequest:
    values: dict[str, object] = {
        "client_name": "Claude",
        "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "scope": "read write",
        "token_endpoint_auth_method": "none",
    }
    values.update(overrides)
    return ClientRegistrationRequest.model_validate(values)


def test_codex_native_registration_contract_is_accepted() -> None:
    registration = _registration(
        client_name="Codex",
        grant_types=["authorization_code", "refresh_token"],
        application_type="native",
    )

    assert registration.grant_types == ("authorization_code", "refresh_token")
    assert registration.application_type == "native"


def test_registration_ignores_non_policy_extension_metadata() -> None:
    registration = _registration(
        client_uri="https://client.example.com",
        software_id="client-software-id",
    )

    assert registration.model_extra is None


def test_registration_allows_omitted_display_name() -> None:
    registration = ClientRegistrationRequest.model_validate(
        {
            "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
            "scope": "read write",
        }
    )

    assert registration.client_name is None


async def test_registration_rejects_attacker_controlled_redirect() -> None:
    store = InMemoryOAuthStateStore()
    request = _registration(redirect_uris=["https://attacker.invalid/callback"])

    with pytest.raises(HTTPException, match="redirect") as exc_info:
        await register_client(request, store)

    assert exc_info.value.status_code == 400
    assert store.clients == {}


async def test_registration_is_bounded_and_idempotent() -> None:
    store = InMemoryOAuthStateStore()
    request = _registration(client_name="Attacker seed", application_type="native")

    first = await register_client(request, store)
    second = await register_client(_registration(), store)

    assert first.client_id == second.client_id
    assert "client_secret" not in first.model_dump()
    assert first.grant_types == ["authorization_code", "refresh_token"]
    assert first.application_type == "web"
    assert first.client_name == "Claude"
    assert len(store.clients) == 1

    renamed = await register_client(_registration(client_name="Attacker rename"), store)
    assert renamed.client_name == "Claude"
    assert store.clients[first.client_id].scopes == ("read", "write")


def test_registration_wire_omits_public_client_secret_and_returns_created() -> None:
    registration_route = next(
        route for route in oauth_router.routes if isinstance(route, APIRoute) and route.path == "/oauth/register"
    )

    assert registration_route.status_code == 201
    assert registration_route.response_model is ClientRegistrationResponse
    assert "client_secret" not in ClientRegistrationResponse.model_fields


async def test_native_client_can_register_and_authorize_ephemeral_loopback_callback() -> None:
    store = InMemoryOAuthStateStore()
    redirect_uri = "http://127.0.0.1:43119/callback/DSMTT-6Ywhkd"
    registration = await register_client(
        _registration(
            client_name="Codex",
            redirect_uris=[redirect_uri],
            grant_types=["authorization_code", "refresh_token"],
            application_type="native",
        ),
        store,
    )
    later_registration = await register_client(
        _registration(
            client_name=None,
            redirect_uris=["http://127.0.0.1:53220/callback/a-different-session"],
            grant_types=["authorization_code", "refresh_token"],
            application_type="native",
        ),
        store,
    )
    verifier = "v" * 43
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()

    response = await authorize(
        response_type="code",
        client_id=registration.client_id,
        redirect_uri=redirect_uri,
        _owner="owner",
        code_challenge=challenge,
        code_challenge_method="S256",
        store=store,
    )

    assert registration.application_type == "native"
    assert later_registration.client_id == registration.client_id
    assert store.clients == {}
    assert response.status_code == 307
    code = parse_qs(urlparse(response.headers["location"]).query)["code"][0]
    tokens = await token(
        grant_type="authorization_code",
        client_id=registration.client_id,
        response=Response(),
        code=code,
        redirect_uri=redirect_uri,
        code_verifier=verifier,
        refresh_token=None,
        store=store,
    )
    assert isinstance(tokens, TokenResponse)
    assert verify_token(tokens.access_token) is not None


@pytest.mark.parametrize(
    "redirect_uri",
    [
        f"http://127.0.0.1:43119/{'x' * 2_026}",
        "http://127.0.0.1:43119/call\nback",
    ],
)
async def test_shared_native_client_revalidates_redirect_on_authorization(redirect_uri: str) -> None:
    store = InMemoryOAuthStateStore()
    registration = await register_client(
        _registration(
            redirect_uris=["http://127.0.0.1:43119/callback/valid"],
            grant_types=["authorization_code", "refresh_token"],
            application_type="native",
        ),
        store,
    )

    with pytest.raises(HTTPException, match="redirect") as exc_info:
        await authorize(
            response_type="code",
            client_id=registration.client_id,
            redirect_uri=redirect_uri,
            _owner="owner",
            code_challenge="A" * 43,
            code_challenge_method="S256",
            store=store,
        )

    assert exc_info.value.status_code == 400
    assert store.codes == {}


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "http://localhost:43119/callback/DSMTT-6Ywhkd",
        "http://192.168.1.2:43119/callback/DSMTT-6Ywhkd",
    ],
)
async def test_native_registration_rejects_non_literal_or_non_loopback_http_callback(redirect_uri: str) -> None:
    with pytest.raises(HTTPException, match="redirect"):
        await register_client(
            _registration(
                redirect_uris=[redirect_uri],
                grant_types=["authorization_code", "refresh_token"],
                application_type="native",
            ),
            InMemoryOAuthStateStore(),
        )


async def test_registration_cannot_downgrade_canonical_scopes() -> None:
    store = InMemoryOAuthStateStore()

    with pytest.raises(HTTPException, match="canonical scopes"):
        await register_client(_registration(scope="read"), store)

    assert store.clients == {}


@pytest.mark.parametrize(
    "field,value",
    [
        ("client_name", "x" * 129),
        ("redirect_uris", []),
        ("grant_types", ["client_credentials"]),
        ("grant_types", ["refresh_token"]),
        ("grant_types", ["authorization_code", "authorization_code"]),
        ("response_types", ["token"]),
        ("scope", "read admin"),
        ("token_endpoint_auth_method", "client_secret_post"),
    ],
)
def test_registration_schema_rejects_unsupported_or_unbounded_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _registration(**{field: value})


@pytest.mark.parametrize(
    "redirect_uri",
    [
        f"http://127.0.0.1:43119/{'x' * 2_026}",
        "http://127.0.0.1:43119/call\nback",
        "http://127.0.0.1:43119/call\x7fback",
        "http://127.0.0.1:43119/call\x85back",
    ],
)
def test_registration_rejects_oversized_or_control_character_redirects(redirect_uri: str) -> None:
    with pytest.raises(ValidationError):
        _registration(
            redirect_uris=[redirect_uri],
            application_type="native",
        )


async def test_authorize_requires_s256_pkce() -> None:
    store = InMemoryOAuthStateStore()
    registration = await register_client(_registration(), store)

    with pytest.raises(HTTPException, match="PKCE") as exc_info:
        await authorize(
            response_type="code",
            client_id=registration.client_id,
            redirect_uri="https://claude.ai/api/mcp/auth_callback",
            _owner="owner",
            scope="read write",
            state="state",
            code_challenge=None,
            code_challenge_method=None,
            store=store,
        )

    assert exc_info.value.status_code == 400


async def test_authorize_encodes_redirect_parameters() -> None:
    store = InMemoryOAuthStateStore()
    registration = await register_client(_registration(), store)
    state = "opaque&code=attacker-value"

    response = await authorize(
        response_type="code",
        client_id=registration.client_id,
        redirect_uri="https://claude.ai/api/mcp/auth_callback",
        _owner="owner",
        scope="read write",
        state=state,
        code_challenge="A" * 43,
        code_challenge_method="S256",
        store=store,
    )

    parsed = urlparse(response.headers["location"])
    params = parse_qs(parsed.query)
    assert params["state"] == [state]
    assert len(params["code"]) == 1


async def test_authorize_preserves_configured_redirect_query() -> None:
    store = InMemoryOAuthStateStore()
    redirect_uri = "https://claude.ai/api/mcp/auth_callback?tenant=palace"
    client = OAuthClient(
        client_id="client_with_query",
        client_name="Claude",
        redirect_uris=(redirect_uri,),
    )
    store.clients[client.client_id] = client

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(settings, "oauth_allowed_redirect_uris", [redirect_uri])
        response = await authorize(
            response_type="code",
            client_id=client.client_id,
            redirect_uri=redirect_uri,
            _owner="owner",
            state="state",
            code_challenge="A" * 43,
            code_challenge_method="S256",
            store=store,
        )

    params = parse_qs(urlparse(response.headers["location"]).query)
    assert params["tenant"] == ["palace"]
    assert params["state"] == ["state"]
    assert len(params["code"]) == 1


async def test_code_exchange_is_bound_and_single_use() -> None:
    store = InMemoryOAuthStateStore()
    registration = await register_client(_registration(), store)
    verifier = "v" * 43
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    authorization = await authorize(
        response_type="code",
        client_id=registration.client_id,
        redirect_uri="https://claude.ai/api/mcp/auth_callback",
        _owner="owner",
        code_challenge=challenge,
        code_challenge_method="S256",
        store=store,
    )
    code = parse_qs(urlparse(authorization.headers["location"]).query)["code"][0]

    token_response = Response()
    tokens = await token(
        grant_type="authorization_code",
        client_id=registration.client_id,
        response=token_response,
        code=code,
        redirect_uri="https://claude.ai/api/mcp/auth_callback",
        code_verifier=verifier,
        refresh_token=None,
        store=store,
    )
    assert isinstance(tokens, TokenResponse)
    assert verify_token(tokens.access_token) is not None
    assert verify_token(tokens.refresh_token) is None
    assert token_response.headers["cache-control"] == "no-store"
    assert token_response.headers["pragma"] == "no-cache"

    rotated = await token(
        grant_type="refresh_token",
        client_id=registration.client_id,
        response=Response(),
        code=None,
        redirect_uri=None,
        code_verifier=None,
        refresh_token=tokens.refresh_token,
        store=store,
    )
    assert isinstance(rotated, TokenResponse)
    assert rotated.refresh_token != tokens.refresh_token
    assert verify_token(rotated.access_token) is not None

    with pytest.raises(HTTPException, match="replayed"):
        await token(
            grant_type="refresh_token",
            client_id=registration.client_id,
            response=Response(),
            code=None,
            redirect_uri=None,
            code_verifier=None,
            refresh_token=tokens.refresh_token,
            store=store,
        )
    assert rotated.refresh_token not in store.refresh_tokens

    with pytest.raises(HTTPException, match="Invalid or expired"):
        await token(
            grant_type="authorization_code",
            client_id=registration.client_id,
            response=Response(),
            code=code,
            redirect_uri="https://claude.ai/api/mcp/auth_callback",
            code_verifier=verifier,
            refresh_token=None,
            store=store,
        )


async def test_unknown_client_returns_oauth_invalid_client_wire_error() -> None:
    result = await token(
        grant_type="authorization_code",
        client_id="client_unknown",
        response=Response(),
        code="unused",
        redirect_uri="https://claude.ai/api/mcp/auth_callback",
        code_verifier="v" * 43,
        refresh_token=None,
        store=InMemoryOAuthStateStore(),
    )

    assert isinstance(result, JSONResponse)
    assert result.status_code == 401
    assert json.loads(result.body) == {"error": "invalid_client"}
    assert result.headers["cache-control"] == "no-store"


def test_refresh_token_is_not_valid_bearer_token() -> None:
    refresh_token = create_refresh_token("client_123")

    assert verify_token(refresh_token) is None


def test_authorization_requires_the_configured_resource_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "oauth_owner_username", "palace-owner")
    monkeypatch.setattr(settings, "oauth_owner_password", SecretStr("owner-password-with-entropy"))

    with pytest.raises(HTTPException) as missing_error:
        require_owner_auth(None)
    assert missing_error.value.status_code == 401

    with pytest.raises(HTTPException) as invalid_error:
        require_owner_auth(
            HTTPBasicCredentials(username="palace-owner", password="wrong-password")  # noqa: S106 - test fixture
        )
    assert invalid_error.value.status_code == 401

    identity = require_owner_auth(
        HTTPBasicCredentials(
            username="palace-owner",
            password="owner-password-with-entropy",  # noqa: S106 - test fixture
        )
    )
    assert identity == "palace-owner"


def test_access_token_preserves_client_identity() -> None:
    access_token = create_access_token("client_123", ("read", "write"))

    token_data = verify_token(access_token)
    assert token_data is not None
    assert token_data.client_id == "client_123"
    assert token_data.scopes == ["read", "write"]


async def test_metadata_ignores_untrusted_host_headers() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/.well-known/oauth-authorization-server",
            "headers": [(b"host", b"attacker.invalid"), (b"x-forwarded-proto", b"http")],
            "server": ("attacker.invalid", 443),
            "client": ("203.0.113.10", 1234),
        }
    )

    metadata = await oauth_metadata(request)

    assert metadata["issuer"] != "http://attacker.invalid"
    assert urlparse(metadata["issuer"]).hostname in {"localhost", "memory-palace.sokrates.is"}


async def test_oauth_rate_limiter_bounds_each_client() -> None:
    limiter = RequestRateLimiter(requests=2, window_seconds=60.0, max_clients=2)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "path": "/oauth/token",
            "headers": [],
            "server": ("memory.example.com", 443),
            "client": ("203.0.113.10", 1234),
        }
    )

    await limiter(request)
    await limiter(request)
    with pytest.raises(HTTPException) as error:
        await limiter(request)

    assert error.value.status_code == 429
    assert error.value.headers == {"Retry-After": "60"}
