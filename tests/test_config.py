"""Runtime configuration fails closed without exposing secret values."""

import pytest

from memory_palace.core.config import Environment, Settings


def test_secrets_are_redacted_from_settings_repr() -> None:
    config = Settings(
        _env_file=None,
        voyage_api_key="voyage-secret",
        openai_api_key="openai-secret",
        neo4j_password="database-secret",  # noqa: S106 - isolated test fixture
        jwt_secret_key="j" * 48,
    )

    rendered = repr(config)
    assert "voyage-secret" not in rendered
    assert "openai-secret" not in rendered
    assert "database-secret" not in rendered
    assert "j" * 48 not in rendered


@pytest.mark.parametrize(
    "model,provider,key_field,key_value",
    [
        ("openai-responses:gpt-5-mini", "openai", "openai_api_key", "openai-secret"),
        ("openai-chat:gpt-5-mini", "openai", "openai_api_key", "openai-secret"),
        ("anthropic:claude-sonnet-5", "anthropic", "anthropic_api_key", "anthropic-secret"),
    ],
)
def test_consolidation_provider_selects_matching_credential(
    model: str, provider: str, key_field: str, key_value: str
) -> None:
    config = Settings(_env_file=None, consolidation_model=model, **{key_field: key_value})

    assert config.consolidation_provider == provider
    assert config.consolidation_api_key_value == key_value


@pytest.mark.parametrize("model", ["gpt-5-mini", "gemini:model", "openai-responses:"])
def test_consolidation_provider_rejects_unsupported_model_specifier(model: str) -> None:
    config = Settings(_env_file=None, consolidation_model=model)

    with pytest.raises(ValueError, match="CONSOLIDATION_MODEL"):
        _ = config.consolidation_provider


def test_trusted_hosts_include_only_explicit_and_internal_authorities() -> None:
    config = Settings(_env_file=None, public_base_url="https://memory.example.com")

    assert config.trusted_hosts == ["memory.example.com", "localhost", "127.0.0.1", "[::1]", "testserver", "apiserver"]


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"debug": True}, "DEBUG"),
        ({"neo4j_password": "password"}, "NEO4J_PASSWORD"),
        ({"public_base_url": "http://example.com"}, "PUBLIC_BASE_URL"),
    ],
)
def test_production_rejects_unsafe_defaults(overrides: dict[str, object], expected: str) -> None:
    values: dict[str, object] = {
        "_env_file": None,
        "environment": Environment.PRODUCTION,
        "debug": False,
        "public_base_url": "https://memory.example.com",
        "voyage_api_key": "voyage-secret",
        "neo4j_password": "strong-database-secret",
        "jwt_secret_key": "j" * 48,
        "oauth_owner_password": "strong-owner-password",
    }
    values.update(overrides)
    config = Settings(**values)

    with pytest.raises(ValueError, match=expected):
        config.validate_runtime()


def test_production_requires_owner_authentication_secret() -> None:
    config = Settings(
        _env_file=None,
        environment=Environment.PRODUCTION,
        debug=False,
        public_base_url="https://memory.example.com",
        voyage_api_key="voyage-secret",
        neo4j_password="strong-database-secret",  # noqa: S106 - isolated test fixture
        jwt_secret_key="j" * 48,
        oauth_owner_password="short",  # noqa: S106 - deliberately invalid test fixture
    )

    with pytest.raises(ValueError, match="OAUTH_OWNER_PASSWORD"):
        config.validate_runtime()


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@memory.example.com",
        "https://memory.example.com/oauth",
        "https://memory.example.com?tenant=other",
        "https://memory.example.com#fragment",
    ],
)
def test_public_base_url_must_be_an_origin(url: str) -> None:
    config = Settings(
        _env_file=None,
        environment=Environment.PRODUCTION,
        debug=False,
        public_base_url=url,
        voyage_api_key="voyage-secret",
        neo4j_password="strong-database-secret",  # noqa: S106 - isolated test fixture
        jwt_secret_key="j" * 48,
        oauth_owner_password="strong-owner-password",  # noqa: S106 - isolated test fixture
    )

    with pytest.raises(ValueError, match="origin"):
        config.validate_runtime()


def test_production_redirect_uri_rejects_credentials_and_fragments() -> None:
    config = Settings(
        _env_file=None,
        environment=Environment.PRODUCTION,
        debug=False,
        public_base_url="https://memory.example.com",
        voyage_api_key="voyage-secret",
        neo4j_password="strong-database-secret",  # noqa: S106 - isolated test fixture
        jwt_secret_key="j" * 48,
        oauth_owner_password="strong-owner-password",  # noqa: S106 - isolated test fixture
        oauth_allowed_redirect_uris=["https://user:password@client.example/callback"],
    )

    with pytest.raises(ValueError, match="OAUTH_ALLOWED_REDIRECT_URIS"):
        config.validate_runtime()


def test_production_accepts_exact_http_loopback_ip_callback() -> None:
    config = Settings(
        _env_file=None,
        environment=Environment.PRODUCTION,
        debug=False,
        public_base_url="https://memory.example.com",
        voyage_api_key="voyage-secret",
        neo4j_password="strong-database-secret",  # noqa: S106 - isolated test fixture
        jwt_secret_key="j" * 48,
        oauth_owner_password="strong-owner-password",  # noqa: S106 - isolated test fixture
        oauth_allowed_redirect_uris=["http://127.0.0.1:8765/callback/codex"],
    )

    config.validate_runtime()


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "http://localhost:8765/callback/codex",
        "http://192.168.1.10:8765/callback/codex",
    ],
)
def test_production_rejects_non_ip_or_non_loopback_http_callback(redirect_uri: str) -> None:
    config = Settings(
        _env_file=None,
        environment=Environment.PRODUCTION,
        debug=False,
        public_base_url="https://memory.example.com",
        voyage_api_key="voyage-secret",
        neo4j_password="strong-database-secret",  # noqa: S106 - isolated test fixture
        jwt_secret_key="j" * 48,
        oauth_owner_password="strong-owner-password",  # noqa: S106 - isolated test fixture
        oauth_allowed_redirect_uris=[redirect_uri],
    )

    with pytest.raises(ValueError, match="OAUTH_ALLOWED_REDIRECT_URIS"):
        config.validate_runtime()
