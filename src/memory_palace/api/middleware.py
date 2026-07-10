"""HTTP boundary middleware for request limits and response hardening."""

from __future__ import annotations

import re
from typing import Final, cast
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.status import HTTP_400_BAD_REQUEST, HTTP_413_CONTENT_TOO_LARGE
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.contextvars import bind_contextvars, reset_contextvars

REQUEST_ID_HEADER: Final = "x-request-id"
CORRELATION_ID_HEADER: Final = "x-correlation-id"

SECURITY_RESPONSE_HEADERS: Final[tuple[tuple[str, str], ...]] = (
    ("content-security-policy", "frame-ancestors 'none'"),
    ("permissions-policy", "camera=(), geolocation=(), microphone=()"),
    ("referrer-policy", "no-referrer"),
    ("strict-transport-security", "max-age=31536000"),
    ("x-content-type-options", "nosniff"),
    ("x-frame-options", "DENY"),
    ("x-permitted-cross-domain-policies", "none"),
    ("x-xss-protection", "0"),
)

_BOUNDARY_ERROR_BODY: Final = {"detail": "Invalid request boundary"}
_BODY_TOO_LARGE_BODY: Final = {"detail": "Request body too large"}
_SAFE_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class RequestBodyTooLarge(HTTPException):
    """Signal that the received request body crossed the configured limit."""

    def __init__(self) -> None:
        super().__init__(status_code=HTTP_413_CONTENT_TOO_LARGE, detail=_BODY_TOO_LARGE_BODY["detail"])


class _InvalidContentLength(ValueError):
    """Signal a malformed or conflicting Content-Length boundary."""


class _LimitedReceive:
    """Count body bytes without buffering or changing ASGI message boundaries."""

    def __init__(self, receive: Receive, max_body_bytes: int) -> None:
        self._receive = receive
        self._max_body_bytes = max_body_bytes
        self._received_body_bytes = 0

    async def __call__(self) -> Message:
        message = await self._receive()
        if message["type"] != "http.request":
            return message

        body = cast(bytes, message.get("body", b""))
        self._received_body_bytes += len(body)
        if self._received_body_bytes > self._max_body_bytes:
            raise RequestBodyTooLarge
        return message


class HTTPBoundaryMiddleware:
    """Enforce HTTP byte limits and attach boundary metadata to responses.

    The body limit is checked twice: first against every declared
    ``Content-Length`` value, then against the bytes actually delivered by the
    ASGI server. The second check protects chunked requests and clients that
    understate their body size.
    """

    def __init__(self, app: ASGIApp, *, max_request_body_bytes: int) -> None:
        if (
            not isinstance(max_request_body_bytes, int)
            or isinstance(max_request_body_bytes, bool)
            or max_request_body_bytes < 0
        ):
            raise ValueError("max_request_body_bytes must be a non-negative integer")
        self._app = app
        self._max_request_body_bytes = max_request_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request_id = _request_identifier(scope, REQUEST_ID_HEADER) or str(uuid4())
        correlation_id = _request_identifier(scope, CORRELATION_ID_HEADER) or request_id

        state = cast(dict[str, object], scope.setdefault("state", {}))
        state["request_id"] = request_id
        state["correlation_id"] = correlation_id

        response_started = False

        async def send_with_boundary_headers(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                raw_headers = list(cast(list[tuple[bytes, bytes]], message.get("headers", [])))
                headers = MutableHeaders(raw=raw_headers)
                _set_if_absent(headers, REQUEST_ID_HEADER, request_id)
                _set_if_absent(headers, CORRELATION_ID_HEADER, correlation_id)
                for name, value in SECURITY_RESPONSE_HEADERS:
                    _set_if_absent(headers, name, value)
                message["headers"] = raw_headers
            await send(message)

        context_tokens = bind_contextvars(request_id=request_id, correlation_id=correlation_id)
        try:
            try:
                if _declared_body_exceeds_limit(scope, self._max_request_body_bytes):
                    await _send_boundary_error(
                        scope,
                        receive,
                        send_with_boundary_headers,
                        status_code=HTTP_413_CONTENT_TOO_LARGE,
                        body=_BODY_TOO_LARGE_BODY,
                    )
                    return
            except _InvalidContentLength:
                await _send_boundary_error(
                    scope,
                    receive,
                    send_with_boundary_headers,
                    status_code=HTTP_400_BAD_REQUEST,
                    body=_BOUNDARY_ERROR_BODY,
                )
                return

            limited_receive = _LimitedReceive(receive, self._max_request_body_bytes)
            try:
                await self._app(scope, limited_receive, send_with_boundary_headers)
            except RequestBodyTooLarge:
                if response_started:
                    raise
                await _send_boundary_error(
                    scope,
                    receive,
                    send_with_boundary_headers,
                    status_code=HTTP_413_CONTENT_TOO_LARGE,
                    body=_BODY_TOO_LARGE_BODY,
                )
        finally:
            reset_contextvars(**context_tokens)


def _request_identifier(scope: Scope, header_name: str) -> str | None:
    encoded_name = header_name.encode("ascii")
    raw_values = [value for name, value in scope.get("headers", []) if name.lower() == encoded_name]
    if len(raw_values) != 1 or not raw_values[0].isascii():
        return None
    value = raw_values[0].decode("ascii")
    if _SAFE_ID_PATTERN.fullmatch(value) is None:
        return None
    return value


def _set_if_absent(headers: MutableHeaders, name: str, value: str) -> None:
    if name not in headers:
        headers[name] = value


def _declared_body_exceeds_limit(scope: Scope, max_body_bytes: int) -> bool:
    raw_values = [value.strip() for name, value in scope.get("headers", []) if name.lower() == b"content-length"]
    if not raw_values:
        return False

    canonical_values: set[bytes] = set()
    for raw_value in raw_values:
        if not raw_value or not raw_value.isdigit():
            raise _InvalidContentLength
        canonical_values.add(raw_value.lstrip(b"0") or b"0")

    if len(canonical_values) != 1:
        raise _InvalidContentLength

    canonical_value = canonical_values.pop()
    max_value = str(max_body_bytes).encode("ascii")
    if len(canonical_value) != len(max_value):
        return len(canonical_value) > len(max_value)
    return canonical_value > max_value


async def _send_boundary_error(
    scope: Scope,
    receive: Receive,
    send: Send,
    *,
    status_code: int,
    body: dict[str, str],
) -> None:
    response = JSONResponse(status_code=status_code, content=body)
    await response(scope, receive, send)
