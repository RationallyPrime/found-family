"""Focused contract tests for the HTTP boundary middleware."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Sequence
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from starlette.datastructures import Headers
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Scope
from structlog.contextvars import get_contextvars

from memory_palace.api.middleware import SECURITY_RESPONSE_HEADERS, HTTPBoundaryMiddleware

BODY_LIMIT = 8


def _make_app(*, calls: list[bytes] | None = None) -> FastAPI:
    app = FastAPI()
    app.add_middleware(HTTPBoundaryMiddleware, max_request_body_bytes=BODY_LIMIT)

    @app.post("/echo")
    async def echo(request: Request) -> Response:
        body = await request.body()
        if calls is not None:
            calls.append(body)
        return Response(
            body,
            media_type="application/octet-stream",
            headers={"x-frame-options": "SAMEORIGIN"},
        )

    @app.get("/identity")
    async def identity(request: Request) -> JSONResponse:
        log_context = get_contextvars()
        return JSONResponse(
            {
                "request_id": request.state.request_id,
                "correlation_id": request.state.correlation_id,
                "context_request_id": log_context.get("request_id"),
                "context_correlation_id": log_context.get("correlation_id"),
            }
        )

    return app


async def _invoke_http(
    app: ASGIApp,
    *,
    method: str = "POST",
    path: str = "/echo",
    chunks: Sequence[bytes] = (),
    headers: Sequence[tuple[bytes, bytes]] = (),
) -> tuple[int, Headers, bytes]:
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": list(headers),
        "client": ("192.0.2.1", 12345),
        "server": ("testserver", 443),
        "state": {},
    }

    request_messages: deque[Message] = deque()
    if chunks:
        for index, chunk in enumerate(chunks):
            request_messages.append(
                {
                    "type": "http.request",
                    "body": chunk,
                    "more_body": index < len(chunks) - 1,
                }
            )
    else:
        request_messages.append({"type": "http.request", "body": b"", "more_body": False})

    sent_messages: list[Message] = []

    async def receive() -> Message:
        if request_messages:
            return request_messages.popleft()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent_messages.append(message)

    await app(scope, receive, send)

    start = next(message for message in sent_messages if message["type"] == "http.response.start")
    status_code = int(start["status"])
    response_headers = Headers(raw=list(start.get("headers", [])))
    body = b"".join(message.get("body", b"") for message in sent_messages if message["type"] == "http.response.body")
    return status_code, response_headers, body


def _assert_boundary_headers(headers: Headers) -> None:
    UUID(headers["x-request-id"])
    assert headers["x-correlation-id"] == headers["x-request-id"]
    for name, value in SECURITY_RESPONSE_HEADERS:
        if name == "x-frame-options":
            continue
        assert headers[name] == value


@pytest.mark.asyncio
async def test_declared_oversized_body_is_rejected_before_dispatch() -> None:
    calls: list[bytes] = []
    status, headers, body = await _invoke_http(
        _make_app(calls=calls),
        chunks=[b"123456789"],
        headers=[(b"content-length", b"9")],
    )

    assert status == 413
    assert json.loads(body) == {"detail": "Request body too large"}
    assert calls == []
    _assert_boundary_headers(headers)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        [(b"content-length", b"invalid")],
        [(b"content-length", b"7"), (b"content-length", b"8")],
    ],
)
async def test_invalid_content_length_is_rejected_without_detail(headers: list[tuple[bytes, bytes]]) -> None:
    status, response_headers, body = await _invoke_http(_make_app(), chunks=[b"x"], headers=headers)

    assert status == 400
    assert json.loads(body) == {"detail": "Invalid request boundary"}
    _assert_boundary_headers(response_headers)


@pytest.mark.asyncio
async def test_chunked_body_is_counted_and_rejected() -> None:
    status, headers, body = await _invoke_http(_make_app(), chunks=[b"123", b"456", b"789"])

    assert status == 413
    assert json.loads(body) == {"detail": "Request body too large"}
    _assert_boundary_headers(headers)


@pytest.mark.asyncio
async def test_actual_body_is_limited_even_when_content_length_is_understated() -> None:
    status, _, body = await _invoke_http(
        _make_app(),
        chunks=[b"1234", b"56789"],
        headers=[(b"content-length", b"4")],
    )

    assert status == 413
    assert json.loads(body) == {"detail": "Request body too large"}


@pytest.mark.asyncio
async def test_body_at_limit_and_existing_response_headers_are_preserved() -> None:
    body = b"12345678"
    status, headers, response_body = await _invoke_http(
        _make_app(),
        chunks=[body[:3], body[3:]],
        headers=[(b"content-length", b"8")],
    )

    assert status == 200
    assert response_body == body
    assert headers["x-frame-options"] == "SAMEORIGIN"
    _assert_boundary_headers(headers)


@pytest.mark.asyncio
async def test_valid_request_and_correlation_ids_reach_state_and_response() -> None:
    context_before_request = get_contextvars()
    status, headers, body = await _invoke_http(
        _make_app(),
        method="GET",
        path="/identity",
        headers=[
            (b"x-request-id", b"request-123"),
            (b"x-correlation-id", b"correlation:456"),
        ],
    )

    assert status == 200
    assert json.loads(body) == {
        "request_id": "request-123",
        "correlation_id": "correlation:456",
        "context_request_id": "request-123",
        "context_correlation_id": "correlation:456",
    }
    assert headers["x-request-id"] == "request-123"
    assert headers["x-correlation-id"] == "correlation:456"
    assert get_contextvars() == context_before_request


@pytest.mark.asyncio
async def test_unsafe_identifiers_are_replaced() -> None:
    status, headers, body = await _invoke_http(
        _make_app(),
        method="GET",
        path="/identity",
        headers=[
            (b"x-request-id", b"../../not-safe"),
            (b"x-correlation-id", b"contains a space"),
        ],
    )

    payload = json.loads(body)
    generated_request_id = payload["request_id"]

    assert status == 200
    UUID(generated_request_id)
    assert payload["correlation_id"] == generated_request_id
    assert payload["context_request_id"] == generated_request_id
    assert payload["context_correlation_id"] == generated_request_id
    assert headers["x-request-id"] == generated_request_id
    assert headers["x-correlation-id"] == generated_request_id


@pytest.mark.asyncio
async def test_duplicate_identifiers_are_not_trusted() -> None:
    status, headers, body = await _invoke_http(
        _make_app(),
        method="GET",
        path="/identity",
        headers=[
            (b"x-request-id", b"first"),
            (b"x-request-id", b"second"),
            (b"x-correlation-id", b"first-correlation"),
            (b"x-correlation-id", b"second-correlation"),
        ],
    )

    payload = json.loads(body)

    assert status == 200
    UUID(payload["request_id"])
    assert payload["correlation_id"] == payload["request_id"]
    assert headers["x-request-id"] == payload["request_id"]
    assert headers["x-correlation-id"] == payload["request_id"]


def test_negative_or_boolean_limit_is_rejected() -> None:
    app = _make_app()

    with pytest.raises(ValueError, match="non-negative integer"):
        HTTPBoundaryMiddleware(app, max_request_body_bytes=-1)
    with pytest.raises(ValueError, match="non-negative integer"):
        HTTPBoundaryMiddleware(app, max_request_body_bytes=True)
