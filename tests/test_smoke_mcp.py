"""Smoke tooling never leaks bearer credentials onto plaintext remote links."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "smoke_mcp.py"
SPEC = spec_from_file_location("smoke_mcp", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
smoke_mcp = module_from_spec(SPEC)
SPEC.loader.exec_module(smoke_mcp)


def test_token_transport_requires_tls_or_loopback() -> None:
    assert smoke_mcp._token_transport_is_safe("https://memory.example.com") is True
    assert smoke_mcp._token_transport_is_safe("http://127.0.0.1:8000") is True
    assert smoke_mcp._token_transport_is_safe("http://[::1]:8000") is True
    assert smoke_mcp._token_transport_is_safe("http://memory.example.com") is False
