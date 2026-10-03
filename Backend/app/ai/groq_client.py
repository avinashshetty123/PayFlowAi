import json
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
# Reasoning models emit hidden "thinking" tokens; without these controls they routinely
# break JSON mode (HTTP 400 "Failed to validate JSON") and PayFlow fell back silently.
REASONING_MODEL_MARKERS = ("gpt-oss", "qwen3", "deepseek-r1", "magistral")


class GroqUnavailableError(Exception):
    """Groq is not configured, unreachable, timed out or returned unusable output."""


class GroqClient:
    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: float | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport
        self.api_key = api_key if api_key is not None else settings.GROQ_API_KEY
        self.model = model or settings.GROQ_MODEL
        self.timeout = timeout or settings.GROQ_TIMEOUT_SECONDS

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def reasoning_model(self) -> bool:
        return any(marker in self.model.lower() for marker in REASONING_MODEL_MARKERS)

    def _body(self, system: str, user: str, attempt: int) -> dict:
        body: dict = {
            "model": self.model,
            "temperature": 0.1 if attempt == 0 else 0.0,
            "max_tokens": 1600,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self.reasoning_model:
            body["reasoning_effort"] = "low"
            body["include_reasoning"] = False  # never request (or store) chain-of-thought
        return body

    async def chat_json(self, system: str, user: str) -> dict:
        """JSON-mode completion with one repair retry on malformed JSON."""
        if not self.configured:
            raise GroqUnavailableError("GROQ_API_KEY not configured")
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last_error = "unknown"
        for attempt in range(2):
            prompt = user if attempt == 0 else user + "\n\nYour previous reply was not valid JSON. Reply with ONE valid JSON object only."
            try:
                async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                    response = await client.post(GROQ_CHAT_URL, json=self._body(system, prompt, attempt), headers=headers)
            except httpx.TimeoutException as exc:
                raise GroqUnavailableError(f"Groq timed out after {self.timeout}s") from exc
            except httpx.HTTPError as exc:
                raise GroqUnavailableError(f"Groq unreachable: {exc.__class__.__name__}") from exc

            if response.status_code == 400 and "json" in response.text.lower():
                last_error = "Groq rejected malformed JSON generation"
                continue
            if response.status_code >= 400:
                detail = ""
                try:
                    detail = response.json().get("error", {}).get("message", "")[:160]
                except ValueError:
                    pass
                raise GroqUnavailableError(f"Groq HTTP {response.status_code} {detail}".strip())
            try:
                content = response.json()["choices"][0]["message"]["content"] or ""
                return json.loads(content)
            except (KeyError, IndexError, ValueError, json.JSONDecodeError):
                last_error = "Groq returned content that is not JSON"
                continue
        raise GroqUnavailableError(last_error)
