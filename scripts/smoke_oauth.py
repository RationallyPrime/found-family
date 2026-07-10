#!/usr/bin/env python3
"""Exercise the complete OAuth authorization-code and MCP authentication flow.

The owner password is read only from MEMORY_PALACE_SMOKE_OWNER_PASSWORD. The
script never prints credentials or tokens. Use --cleanup-local-state only when
the target uses the Neo4j instance configured by the local `.env`.
"""

import argparse
import asyncio
import base64
import hashlib
import os
import secrets
from ipaddress import ip_address
from urllib.parse import parse_qs, urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from neo4j import AsyncGraphDatabase
from pydantic import BaseModel, Field

from memory_palace.core.config import settings

EXPECTED_TOOLS = frozenset({"remember", "remember_batch", "recall", "awaken", "forget", "health"})


class RegistrationResponse(BaseModel):
    """DCR fields needed by the smoke flow."""

    client_id: str = Field(min_length=8)
    grant_types: list[str]
    application_type: str | None = None


class TokenResponse(BaseModel):
    """OAuth token fields needed by the smoke flow."""

    access_token: str = Field(min_length=1)
    refresh_token: str = Field(min_length=1)
    token_type: str
    expires_in: int = Field(gt=0)


def _target_is_safe(target: str) -> bool:
    parts = urlsplit(target)
    if parts.scheme == "https":
        return True
    if parts.scheme != "http" or parts.hostname is None:
        return False
    try:
        return ip_address(parts.hostname).is_loopback
    except ValueError:
        return parts.hostname == "localhost"


async def _cleanup_client(client_id: str) -> int:
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password_value),
    )
    try:
        result = await driver.execute_query(
            """
            MATCH (node)
            WHERE node.client_id = $client_id
              AND (node:OAuthClient OR node:OAuthCode OR node:OAuthRefreshToken)
            WITH collect(node) AS nodes
            FOREACH (node IN nodes | DETACH DELETE node)
            RETURN size(nodes) AS deleted
            """,
            client_id=client_id,
        )
        return int(result.records[0]["deleted"])
    finally:
        await driver.close()


async def run(
    *,
    target: str,
    redirect_uri: str,
    owner_username: str,
    cleanup_local_state: bool,
) -> bool:
    """Run DCR, PKCE, token rotation, replay rejection, and MCP initialization."""
    if not _target_is_safe(target):
        raise ValueError("OAuth smoke target must be HTTPS or loopback HTTP")

    owner_password = os.getenv("MEMORY_PALACE_SMOKE_OWNER_PASSWORD", "")
    if not owner_password:
        raise RuntimeError("MEMORY_PALACE_SMOKE_OWNER_PASSWORD is required")

    target = target.rstrip("/")
    timeout = httpx.Timeout(10.0, connect=5.0)
    client_id: str | None = None
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            registration_response = await client.post(
                f"{target}/oauth/register",
                json={
                    "client_name": "Codex OAuth smoke",
                    "redirect_uris": [redirect_uri],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "scope": "read write",
                    "token_endpoint_auth_method": "none",
                    "application_type": "native",
                },
            )
            registration_response.raise_for_status()
            registration_payload = registration_response.json()
            if "client_secret" in registration_payload:
                raise RuntimeError("Public-client DCR response must omit client_secret")
            registration = RegistrationResponse.model_validate(registration_payload)
            client_id = registration.client_id
            if registration.grant_types != ["authorization_code", "refresh_token"]:
                raise RuntimeError("DCR response did not preserve the requested grant contract")
            if registration.application_type != "native":
                raise RuntimeError("DCR response did not preserve native application type")
            print("PASS dynamic client registration")

            verifier = secrets.token_urlsafe(64)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            challenge = challenge.rstrip(b"=").decode("ascii")
            state = secrets.token_urlsafe(24)
            authorization = await client.get(
                f"{target}/oauth/authorize",
                params={
                    "response_type": "code",
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                    "scope": "read write",
                    "state": state,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "resource": f"{target}/mcp",
                },
                auth=(owner_username, owner_password),
            )
            if authorization.status_code not in {302, 303, 307}:
                raise RuntimeError(f"Authorization returned HTTP {authorization.status_code}")
            parameters = parse_qs(urlsplit(authorization.headers["location"]).query)
            if parameters.get("state") != [state] or len(parameters.get("code", [])) != 1:
                raise RuntimeError("Authorization redirect did not preserve state and one code")
            code = parameters["code"][0]
            print("PASS owner authorization and S256 code issuance")

            token_response = await client.post(
                f"{target}/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": verifier,
                    "resource": f"{target}/mcp",
                },
            )
            token_response.raise_for_status()
            tokens = TokenResponse.model_validate(token_response.json())
            if tokens.token_type.casefold() != "bearer":
                raise RuntimeError("Token endpoint did not issue a bearer token")
            print("PASS authorization-code token exchange")

            async with (
                httpx.AsyncClient(
                    timeout=timeout,
                    headers={"Authorization": f"Bearer {tokens.access_token}"},
                ) as authorized_client,
                streamable_http_client(f"{target}/mcp", http_client=authorized_client) as (
                    read_stream,
                    write_stream,
                    _get_session_id,
                ),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            if tool_names != EXPECTED_TOOLS:
                raise RuntimeError(f"Unexpected MCP tool contract: {sorted(tool_names)}")
            print("PASS bearer-authenticated MCP initialize/tools-list")

            refresh_response = await client.post(
                f"{target}/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": tokens.refresh_token,
                    "resource": f"{target}/mcp",
                },
            )
            refresh_response.raise_for_status()
            rotated = TokenResponse.model_validate(refresh_response.json())
            if rotated.refresh_token == tokens.refresh_token:
                raise RuntimeError("Refresh token was not rotated")
            print("PASS refresh-token rotation")

            replay_response = await client.post(
                f"{target}/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": tokens.refresh_token,
                    "resource": f"{target}/mcp",
                },
            )
            if replay_response.status_code != 401:
                raise RuntimeError(f"Refresh replay returned HTTP {replay_response.status_code}")

            revoked_family_response = await client.post(
                f"{target}/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": rotated.refresh_token,
                    "resource": f"{target}/mcp",
                },
            )
            if revoked_family_response.status_code != 401:
                raise RuntimeError(
                    f"Compromised refresh family remained usable: HTTP {revoked_family_response.status_code}"
                )
            print("PASS refresh-token replay rejection and family revocation")
            return True
    finally:
        if cleanup_local_state and client_id is not None:
            deleted = await _cleanup_client(client_id)
            print(f"PASS local OAuth smoke-state cleanup ({deleted} records)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="http://127.0.0.1:8000")
    parser.add_argument("--redirect-uri", default="http://127.0.0.1:43119/callback/codex-smoke")
    parser.add_argument("--owner-username", default="owner")
    parser.add_argument("--cleanup-local-state", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    raise SystemExit(
        0
        if asyncio.run(
            run(
                target=arguments.target,
                redirect_uri=arguments.redirect_uri,
                owner_username=arguments.owner_username,
                cleanup_local_state=arguments.cleanup_local_state,
            )
        )
        else 1
    )
