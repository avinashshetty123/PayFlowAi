import json
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqUnavailableError(Exception):
    """Groq is not configured, unreachable, timed out or returned unusable output."""


class GroqClient:
    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: float | None = None):
        self.api_key = api_key if api_key is not None else settings.GROQ_API_KEY
        self.model = model or settings.GROQ_MODEL
        self.timeout = timeout or settings.GROQ_TIMEOUT_SECONDS

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def chat_json(self, system: str, user: str) -> dict:
        if not self.configured:
            raise GroqUnavailableError("GROQ_API_KEY not configured")
        body = {
            "model": self.model,
            "temperature": 0.1,
            "max_tokens": 700,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(GROQ_CHAT_URL, json=body, headers=headers)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except httpx.TimeoutException as exc:
            raise GroqUnavailableError(f"Groq timed out after {self.timeout}s") from exc
        except httpx.HTTPStatusError as exc:
            raise GroqUnavailableError(f"Groq HTTP {exc.response.status_code}") from exc
        except (httpx.HTTPError, KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
            raise GroqUnavailableError(f"Groq response unusable: {exc.__class__.__name__}") from exc
