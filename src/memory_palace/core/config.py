"""Typed configuration with explicit production invariants."""

from enum import StrEnum
from ipaddress import ip_address

from pydantic import AnyHttpUrl, BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Runtime environment; production enables fail-closed checks."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


def _is_secure_redirect_uri(redirect_uri: AnyHttpUrl) -> bool:
    """Accept HTTPS clients and RFC 8252 loopback-IP callbacks only."""
    if redirect_uri.username is not None or redirect_uri.password is not None or redirect_uri.fragment is not None:
        return False
    if redirect_uri.scheme == "https":
        return True
    if redirect_uri.scheme != "http" or redirect_uri.host is None:
        return False
    try:
        return ip_address(redirect_uri.host.strip("[]")).is_loopback
    except ValueError:
        return False


class FriendConfig(BaseModel):
    """Configuration for the friend using the Memory Palace."""

    name: str = Field(default="Friend", description="The name of the person I'm talking with")
    pronouns: str | None = Field(
        default=None, description="Preferred pronouns (e.g., 'they/them', 'she/her', 'he/him')"
    )
    relationship: str = Field(default="friend", description="How we relate (e.g., 'friend', 'collaborator', 'partner')")

    @property
    def possessive(self) -> str:
        """Get possessive form of the name (e.g., 'Hákon's')."""
        if self.name.endswith("s"):
            return f"{self.name}'"
        return f"{self.name}'s"

    @property
    def utterance_label(self) -> str:
        """Get the label for this person's utterances."""
        return f"{self.name}Utterance"


class Settings(BaseSettings):
    # API Keys
    voyage_api_key: SecretStr = SecretStr("")
    anthropic_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    logfire_token: SecretStr = SecretStr("")

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("password")

    # App config
    environment: Environment = Environment.DEVELOPMENT
    debug: bool = True
    public_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8000")
    cors_allowed_origins: list[AnyHttpUrl] = Field(default_factory=lambda: [AnyHttpUrl("http://localhost:3000")])
    max_request_body_bytes: int = Field(default=1_048_576, ge=16_384, le=10_485_760)

    # OAuth token signing. REQUIRED for the API app: without a stable key,
    # every issued token silently dies on restart. Scripts don't need it.
    jwt_secret_key: SecretStr = SecretStr("")
    oauth_allowed_redirect_uris: list[AnyHttpUrl] = Field(
        default_factory=lambda: [
            AnyHttpUrl("https://claude.ai/api/mcp/auth_callback"),
            AnyHttpUrl("https://claude.com/api/mcp/auth_callback"),
        ]
    )
    oauth_owner_username: str = Field(default="owner", min_length=1, max_length=128)
    oauth_owner_password: SecretStr = SecretStr("")
    oauth_access_token_minutes: int = Field(default=60, ge=5, le=1_440)
    oauth_refresh_token_days: int = Field(default=30, ge=1, le=90)

    # Embeddings
    voyage_model: str = "voyage-4-large"
    voyage_timeout_seconds: float = Field(default=30.0, ge=5.0, le=120.0)

    # Consolidation dream job (episodic -> semantic distillation)
    consolidation_model: str = "openai-responses:gpt-5-mini"

    # Personalization
    friend_name: str = Field(default="Hákon", description="Name of the person using this Memory Palace")
    friend_pronouns: str | None = Field(default=None, description="Friend's pronouns")
    friend_relationship: str = Field(default="friend", description="Our relationship")
    claude_name: str = Field(default="Claude", description="My name in this context")
    palace_name: str = Field(default="Found Family Memory Palace", description="Name of this memory palace instance")

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",  # Ignore extra fields in .env file
        env_nested_delimiter="__",  # Allows MEMORY_PALACE__FRIEND__NAME=Hákon
    )

    @property
    def friend(self) -> FriendConfig:
        """Get friend configuration."""
        return FriendConfig(name=self.friend_name, pronouns=self.friend_pronouns, relationship=self.friend_relationship)

    @property
    def neo4j_password_value(self) -> str:
        """Return the Neo4j secret only at the driver boundary."""
        return self.neo4j_password.get_secret_value()

    @property
    def voyage_api_key_value(self) -> str:
        """Return the Voyage secret only at the provider boundary."""
        return self.voyage_api_key.get_secret_value()

    @property
    def anthropic_api_key_value(self) -> str:
        """Return the Anthropic secret only at the provider boundary."""
        return self.anthropic_api_key.get_secret_value()

    @property
    def openai_api_key_value(self) -> str:
        """Return the OpenAI secret only at the provider boundary."""
        return self.openai_api_key.get_secret_value()

    @property
    def consolidation_provider(self) -> str:
        """Return the supported provider selected by CONSOLIDATION_MODEL."""
        model_provider, separator, model_name = self.consolidation_model.partition(":")
        model_provider = model_provider.lower()
        provider_aliases = {
            "anthropic": "anthropic",
            "openai": "openai",
            "openai-chat": "openai",
            "openai-responses": "openai",
        }
        if not separator or not model_name or model_provider not in provider_aliases:
            raise ValueError("CONSOLIDATION_MODEL must use a supported Anthropic or OpenAI provider prefix")
        return provider_aliases[model_provider]

    @property
    def consolidation_api_key_value(self) -> str:
        """Return the credential for the selected consolidation provider."""
        if self.consolidation_provider == "openai":
            return self.openai_api_key_value
        return self.anthropic_api_key_value

    @property
    def jwt_secret_key_value(self) -> str:
        """Return the signing secret only at the JOSE boundary."""
        return self.jwt_secret_key.get_secret_value()

    @property
    def oauth_owner_password_value(self) -> str:
        """Return the owner credential only at the authorization boundary."""
        return self.oauth_owner_password.get_secret_value()

    @property
    def public_base_url_value(self) -> str:
        """Canonical externally visible origin, without a trailing slash."""
        return str(self.public_base_url).rstrip("/")

    @property
    def allowed_redirect_uri_values(self) -> frozenset[str]:
        """Canonical OAuth callback allowlist."""
        return frozenset(str(uri) for uri in self.oauth_allowed_redirect_uris)

    @property
    def cors_origin_values(self) -> list[str]:
        """CORS origins in Starlette's expected representation."""
        return [str(origin).rstrip("/") for origin in self.cors_allowed_origins]

    @property
    def trusted_hosts(self) -> list[str]:
        """Host header allowlist for public, local, and in-process traffic."""
        public_host = self.public_base_url.host
        # fastapi-mcp's in-process ASGI transport deliberately uses this fixed
        # authority; it never opens a network listener for it.
        internal_mcp_host = "apiserver"
        hosts = [public_host, "localhost", "127.0.0.1", "[::1]", "testserver", internal_mcp_host]
        return list(dict.fromkeys(host for host in hosts if host is not None))

    def validate_runtime(self) -> None:
        """Fail startup when deployment invariants are unsafe or incomplete."""
        problems: list[str] = []
        if len(self.jwt_secret_key_value) < 32:
            problems.append("JWT_SECRET_KEY must contain at least 32 characters")
        if not self.voyage_api_key_value:
            problems.append("VOYAGE_API_KEY is required")
        if (
            self.public_base_url.username is not None
            or self.public_base_url.password is not None
            or self.public_base_url.path not in {"", "/"}
            or self.public_base_url.query is not None
            or self.public_base_url.fragment is not None
        ):
            problems.append("PUBLIC_BASE_URL must be an origin without credentials, path, query, or fragment")
        for redirect_uri in self.oauth_allowed_redirect_uris:
            if (
                redirect_uri.username is not None
                or redirect_uri.password is not None
                or redirect_uri.fragment is not None
            ):
                problems.append("OAUTH_ALLOWED_REDIRECT_URIS must not contain credentials or fragments")
                break

        if self.environment is Environment.PRODUCTION:
            if self.debug:
                problems.append("DEBUG must be false in production")
            if self.neo4j_password_value in {"", "password", "neo4j"}:
                problems.append("NEO4J_PASSWORD must not use a default value in production")
            if len(self.oauth_owner_password_value) < 16:
                problems.append("OAUTH_OWNER_PASSWORD must contain at least 16 characters in production")
            if self.public_base_url.scheme != "https":
                problems.append("PUBLIC_BASE_URL must use https in production")
            for redirect_uri in self.oauth_allowed_redirect_uris:
                if not _is_secure_redirect_uri(redirect_uri):
                    problems.append(
                        "OAUTH_ALLOWED_REDIRECT_URIS must use https or exact HTTP loopback IP callbacks "
                        "without credentials or fragments in production"
                    )
                    break

        if problems:
            raise ValueError("Invalid runtime configuration: " + "; ".join(problems))


settings = Settings()
