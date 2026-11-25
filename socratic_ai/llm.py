"""Optional LLM provider abstraction with deterministic offline fallbacks."""

import json
import os
import re
from typing import Any

import httpx


class LLMProvider:
    """Hybrid LLM client using Gemini or OpenAI when configured, else offline."""

    def __init__(self, timeout: float = 15.0) -> None:
        self.timeout = timeout

    @property
    def gemini_key(self) -> str:
        return os.environ.get("GEMINI_API_KEY", "").strip()

    @property
    def openai_key(self) -> str:
        return os.environ.get("OPENAI_API_KEY", "").strip()

    def active_provider(self) -> str | None:
        """Return active external LLM provider name or None if running offline."""
        if self.gemini_key:
            return "gemini"
        if self.openai_key:
            return "openai"
        return None

    def generate_text(self, system_prompt: str, user_prompt: str) -> str | None:
        """Generate text from configured LLM or return None for offline fallback."""
        provider = self.active_provider()
        if provider is None:
            return None
        try:
            if provider == "gemini":
                model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{model}:generateContent?key={self.gemini_key}"
                )
                payload = {
                    "systemInstruction": {"parts": [{"text": system_prompt}]},
                    "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                }
                resp = httpx.post(url, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                data = resp.json()
                parts = data["candidates"][0]["content"]["parts"]
                return "".join(p.get("text", "") for p in parts).strip() or None

            if provider == "openai":
                model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
                base_url = os.environ.get(
                    "OPENAI_BASE_URL", "https://api.openai.com/v1"
                ).rstrip("/")
                resp = httpx.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.openai_key}"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    },
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip() or None
        except Exception:
            return None
        return None

    def generate_json(
        self, system_prompt: str, user_prompt: str
    ) -> dict[str, Any] | None:
        """Request JSON output from configured LLM, returning None on failure."""
        text = self.generate_text(
            f"{system_prompt}\nRespond strictly with valid JSON.",
            user_prompt,
        )
        if not text:
            return None
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        try:
            parsed = json.loads(cleaned)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None


default_llm = LLMProvider()
