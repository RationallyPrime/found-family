"""Consolidation preserves lifecycle values and selects provider credentials."""

from pydantic import SecretStr
from pytest import MonkeyPatch

from memory_palace.services.consolidation import ConsolidationService, _build_agent, _lifecycle_value, settings


def test_explicit_zero_is_not_replaced_by_the_missing_value_default() -> None:
    assert _lifecycle_value({"salience": 0.0}, "salience", 0.3) == 0.0
    assert _lifecycle_value({"salience": None}, "salience", 0.3) == 0.3


def test_availability_uses_the_selected_openai_credential(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "consolidation_model", "openai-responses:gpt-5-mini")
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("openai-secret"))
    monkeypatch.setattr(settings, "anthropic_api_key", SecretStr(""))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "wrong-provider-secret")

    assert ConsolidationService.available() is True


def test_availability_ignores_unselected_provider_credentials(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "consolidation_model", "openai-responses:gpt-5-mini")
    monkeypatch.setattr(settings, "openai_api_key", SecretStr(""))
    monkeypatch.setattr(settings, "anthropic_api_key", SecretStr("anthropic-secret"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-environment-secret")

    assert ConsolidationService.available() is False


def test_build_agent_uses_the_explicit_openai_responses_transport(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "consolidation_model", "openai-responses:gpt-5-mini")
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("openai-secret"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    agent = _build_agent()

    assert type(agent.model).__name__ == "OpenAIResponsesModel"
