"""Typed OAuth persistence records."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

OAuthScope = Literal["read", "write"]
OAuthGrantType = Literal["authorization_code", "refresh_token"]
OAuthApplicationType = Literal["native", "web"]


class OAuthClient(BaseModel):
    """A registered public OAuth client."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    client_id: str = Field(min_length=8, max_length=128)
    client_name: str = Field(min_length=1, max_length=128)
    redirect_uris: tuple[str, ...] = Field(min_length=1, max_length=4)
    grant_types: tuple[OAuthGrantType, ...] = ("authorization_code",)
    response_types: tuple[Literal["code"], ...] = ("code",)
    scopes: tuple[OAuthScope, ...] = ("read", "write")
    application_type: OAuthApplicationType | None = None
    token_endpoint_auth_method: Literal["none"] = "none"  # noqa: S105 - OAuth method name


class AuthorizationCode(BaseModel):
    """Security-relevant data bound to one short-lived authorization code."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    client_id: str = Field(min_length=8, max_length=128)
    redirect_uri: str = Field(min_length=1, max_length=2_048)
    scopes: tuple[OAuthScope, ...]
    code_challenge: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    code_challenge_method: Literal["S256"] = "S256"


class RefreshTokenState(BaseModel):
    """Server-side binding for one rotating, single-use refresh token."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    client_id: str = Field(min_length=8, max_length=128)
    scopes: tuple[OAuthScope, ...]
    family_id: str = Field(min_length=16, max_length=128)
