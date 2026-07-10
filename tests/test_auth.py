"""Authentication boundary tests independent of proxy headers."""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from memory_palace.api.auth import require_read_auth, require_remote_auth, require_write_auth
from memory_palace.api.endpoints.oauth import create_access_token
from memory_palace.core.config import Environment, settings


def _request(client_host: str, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/memory/recall",
            "headers": headers or [],
            "server": ("localhost", 8000),
            "client": (client_host, 1234),
        }
    )


async def test_missing_proxy_headers_do_not_make_remote_client_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", Environment.DEVELOPMENT)

    with pytest.raises(HTTPException) as exc_info:
        await require_remote_auth(_request("192.0.2.10"))

    assert exc_info.value.status_code == 401


async def test_direct_loopback_is_allowed_only_outside_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", Environment.DEVELOPMENT)
    await require_remote_auth(_request("127.0.0.1"))

    monkeypatch.setattr(settings, "environment", Environment.PRODUCTION)
    with pytest.raises(HTTPException) as exc_info:
        await require_remote_auth(_request("127.0.0.1"))

    assert exc_info.value.status_code == 401


async def test_forged_tunnel_header_never_grants_local_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", Environment.DEVELOPMENT)

    with pytest.raises(HTTPException) as exc_info:
        await require_remote_auth(_request("127.0.0.1", [(b"cf-ray", b"forged")]))

    assert exc_info.value.status_code == 401


async def test_read_scope_cannot_cross_write_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", Environment.PRODUCTION)
    token = create_access_token("client_read", ("read",))
    request = _request("192.0.2.10", [(b"authorization", f"Bearer {token}".encode())])

    await require_read_auth(request)
    with pytest.raises(HTTPException) as exc_info:
        await require_write_auth(request)

    assert exc_info.value.status_code == 403


async def test_write_scope_cannot_cross_read_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", Environment.PRODUCTION)
    token = create_access_token("client_write", ("write",))
    request = _request("192.0.2.10", [(b"authorization", f"Bearer {token}".encode())])

    await require_write_auth(request)
    with pytest.raises(HTTPException) as exc_info:
        await require_read_auth(request)

    assert exc_info.value.status_code == 403
