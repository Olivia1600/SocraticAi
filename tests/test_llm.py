"""Tests for the hybrid LLM provider fallback behavior."""

from socratic_ai.llm import LLMProvider


def test_offline_provider_returns_none(monkeypatch) -> None:
    """Verify LLMProvider cleanly stays offline when no API keys are set."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    provider = LLMProvider()
    assert provider.active_provider() is None
    assert provider.generate_text("sys", "user") is None
    assert provider.generate_json("sys", "user") is None
