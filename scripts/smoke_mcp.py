#!/usr/bin/env python3
"""Read-only HTTP smoke checks for a Memory Palace deployment."""

import argparse
import asyncio
import os
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    path: str
    required_key: str
    expected_status: int = 200


CHECKS = (
    Check("liveness", "/health", "status"),
    Check("readiness", "/ready", "status"),
    Check("oauth metadata", "/.well-known/oauth-authorization-server", "issuer"),
    Check("protected resource metadata", "/.well-known/oauth-protected-resource", "resource"),
    Check("MCP discovery", "/.well-known/mcp", "endpoint"),
)

EXPECTED_TOOLS = frozenset({"remember", "remember_batch", "recall", "awaken", "forget"})


def _token_transport_is_safe(target: str) -> bool:
    parts = urlsplit(target)
    if parts.scheme == "https":
        return True
    if parts.scheme != "http" or parts.hostname is None:
        return False
    if parts.hostname == "localhost":
        return True
    try:
        return ip_address(parts.hostname).is_loopback
    except ValueError:
        return False


async def run(target: str) -> bool:
    """Run bounded, non-mutating checks and return whether all passed."""
    token = os.getenv("MEMORY_PALACE_TOKEN")
    if token and not _token_transport_is_safe(target):
        print("FAIL transport: refusing to send a bearer token over non-loopback HTTP")
        return False

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    timeout = httpx.Timeout(10.0, connect=5.0)
    all_passed = True

    async with httpx.AsyncClient(base_url=target.rstrip("/"), headers=headers, timeout=timeout) as client:
        for check in CHECKS:
            try:
                response = await client.get(check.path)
                body = response.json()
                passed = response.status_code == check.expected_status and check.required_key in body
                detail = f"HTTP {response.status_code}"
            except (httpx.HTTPError, ValueError) as exc:
                passed = False
                detail = type(exc).__name__
            all_passed &= passed
            print(f"{'PASS' if passed else 'FAIL'} {check.name}: {detail}")

        try:
            async with (
                streamable_http_client(f"{target.rstrip('/')}/mcp", http_client=client) as (
                    read_stream,
                    write_stream,
                    _get_session_id,
                ),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            mcp_passed = EXPECTED_TOOLS.issubset(tool_names)
            mcp_detail = f"{len(tool_names)} tools"
        except Exception as exc:
            mcp_passed = False
            mcp_detail = type(exc).__name__
        all_passed &= mcp_passed
        print(f"{'PASS' if mcp_passed else 'FAIL'} MCP initialize/tools-list: {mcp_detail}")

    return all_passed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        default="http://127.0.0.1:8000",
        help="Deployment origin; defaults to the loopback development server",
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    raise SystemExit(0 if asyncio.run(run(arguments.target)) else 1)
