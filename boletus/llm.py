"""LLM abstraction layer — protocol + backends."""

import logging
import os
import threading
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared exceptions
# ---------------------------------------------------------------------------

class BoletusError(Exception):
    """Base exception for boletus errors."""


class LLMTimeoutError(BoletusError):
    """LLM call timed out."""


class LLMCLIError(BoletusError):
    """Claude CLI returned a non-zero exit code."""


class LLMNotFoundError(BoletusError):
    """Claude CLI binary not found."""


# ---------------------------------------------------------------------------
# Protocol — any LLM backend must implement this
# ---------------------------------------------------------------------------

@runtime_checkable
class LLMRunner(Protocol):
    """Interface every LLM backend must satisfy."""

    def call(
        self,
        system_prompt: str,
        user_message: str,
        model: str = "sonnet",
        allowed_tools: str | None = None,
        cwd: str | None = None,
        env_overrides: dict | None = None,
    ) -> str: ...


# ---------------------------------------------------------------------------
# Anthropic API backend
# ---------------------------------------------------------------------------

MODEL_MAP: dict[str, str] = {
    "opus": "claude-opus-4-20250514",
    "sonnet": "claude-sonnet-4-20250514",
    "haiku": "claude-haiku-4-5-20251001",
}


class AnthropicAPIRunner:
    """LLM backend that calls the Anthropic Messages API via the Python SDK."""

    def __init__(
        self,
        api_key: str | None = None,
        max_concurrent: int = 4,
        timeout: int = 600,
    ):
        try:
            import anthropic  # noqa: F401
        except ImportError:
            raise BoletusError(
                "The 'anthropic' package is required for the API backend. "
                "Install it with: pip install anthropic"
            )

        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self._api_key:
            raise BoletusError(
                "No Anthropic API key found. Set ANTHROPIC_API_KEY or pass api_key."
            )

        self._semaphore = threading.Semaphore(max_concurrent)
        self.timeout = timeout

    def _resolve_model(self, model: str) -> str:
        """Map short aliases to full model IDs."""
        return MODEL_MAP.get(model, model)

    def call(
        self,
        system_prompt: str,
        user_message: str,
        model: str = "sonnet",
        allowed_tools: str | None = None,
        cwd: str | None = None,
        env_overrides: dict | None = None,
    ) -> str:
        """Call the Anthropic Messages API and return the response text.

        Args:
            system_prompt: Agent's system prompt.
            user_message: Full assembled prompt with context.
            model: Claude model to use (opus/sonnet/haiku or full ID).
            allowed_tools: NOT supported in API mode — logged as warning.
            cwd: NOT supported in API mode — ignored silently.
            env_overrides: NOT supported in API mode — ignored silently.

        Returns:
            Claude's response text.

        Raises:
            LLMTimeoutError: If the API call times out.
            BoletusError: On any other API error.
        """
        import anthropic

        if allowed_tools:
            logger.warning(
                "allowed_tools is not supported in API mode — tools will be ignored. "
                "Use llm_backend: cli if you need tool use."
            )

        resolved_model = self._resolve_model(model)

        try:
            client = anthropic.Anthropic(api_key=self._api_key)

            logger.debug("Waiting for Anthropic API semaphore...")
            with self._semaphore:
                response = client.messages.create(
                    model=resolved_model,
                    max_tokens=16384,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_message}],
                    timeout=self.timeout,
                )

            # Extract text from response content blocks
            parts = []
            for block in response.content:
                if block.type == "text":
                    parts.append(block.text)
            return "\n".join(parts).strip()

        except anthropic.APITimeoutError:
            raise LLMTimeoutError(
                f"Anthropic API didn't respond within {self.timeout} seconds"
            )
        except anthropic.APIError as e:
            raise BoletusError(f"Anthropic API error: {e}")
