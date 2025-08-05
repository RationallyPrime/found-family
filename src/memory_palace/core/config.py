"""Configuration management."""

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",  # Ignore extra fields in .env file
    )


settings = Settings()
