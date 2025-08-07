"""Configuration management."""


from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class FriendConfig(BaseModel):
    """Configuration for the friend using the Memory Palace."""

    name: str = Field(default="Friend", description="The name of the person I'm talking with")
    pronouns: str | None = Field(default=None, description="Preferred pronouns (e.g., 'they/them', 'she/her', 'he/him')")  # noqa: E501
    relationship: str = Field(default="friend", description="How we relate (e.g., 'friend', 'collaborator', 'partner')")  # noqa: E501

    @property
    def possessive(self) -> str:
        """Get possessive form of the name (e.g., 'Hákon's')."""
        if self.name.endswith('s'):
            return f"{self.name}'"
        return f"{self.name}'s"

    @property
    def utterance_label(self) -> str:
        """Get the label for this person's utterances."""
        return f"{self.name}Utterance"


class Settings(BaseSettings):
    # API Keys
    voyage_api_key: str = ""
    anthropic_api_key: str = ""

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"

    # App config
    debug: bool = True

    # Personalization
    friend_name: str = Field(default="Hákon", description="Name of the person using this Memory Palace")  # noqa: E501
    friend_pronouns: str | None = Field(default=None, description="Friend's pronouns")
    friend_relationship: str = Field(default="friend", description="Our relationship")
    claude_name: str = Field(default="Claude", description="My name in this context")
    palace_name: str = Field(default="Found Family Memory Palace", description="Name of this memory palace instance")  # noqa: E501

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",  # Ignore extra fields in .env file
        env_nested_delimiter="__",  # Allows MEMORY_PALACE__FRIEND__NAME=Hákon
    )

    @property
    def friend(self) -> FriendConfig:
        """Get friend configuration."""
        return FriendConfig(
            name=self.friend_name,
            pronouns=self.friend_pronouns,
            relationship=self.friend_relationship
        )


settings = Settings()
