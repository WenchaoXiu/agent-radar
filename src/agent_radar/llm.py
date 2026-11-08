import json
import logging
import os
import re
import time
from collections import defaultdict
from collections.abc import Callable

import openai
from openai import OpenAI

from .config import ModelConfig

log = logging.getLogger(__name__)

RETRYABLE = (openai.APIConnectionError, openai.APITimeoutError, openai.RateLimitError, openai.InternalServerError)
FATAL = (openai.AuthenticationError, openai.PermissionDeniedError, openai.NotFoundError)


class LLMUnavailable(RuntimeError):
    """The relay could not be reached or kept failing after retries."""


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        max_retries: int = 3,
        timeout: float = 180,
        sleep: Callable[[float], None] = time.sleep,
        client: OpenAI | None = None,
    ) -> None:
        self._client = client or OpenAI(base_url=base_url, api_key=api_key, max_retries=0, timeout=timeout)
        self._max_retries = max_retries
        self._sleep = sleep
        self.usage: dict[str, dict[str, int]] = defaultdict(lambda: {"calls": 0, "prompt": 0, "completion": 0})

    @classmethod
    def from_env(cls) -> "LLMClient | None":
        base_url, api_key = os.environ.get("RELAY_BASE_URL"), os.environ.get("RELAY_API_KEY")
        if not base_url or not api_key:
            return None
        return cls(base_url, api_key)

    def chat(self, cfg: ModelConfig, messages: list[dict], **kwargs):
        """Call chat.completions with retries; kwargs pass through (tools, response_format, max_tokens...)."""
        if cfg.effort:
            kwargs.setdefault("reasoning_effort", cfg.effort)
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._client.chat.completions.create(model=cfg.model, messages=messages, **kwargs)
            except FATAL as exc:
                raise LLMUnavailable(f"{type(exc).__name__} (HTTP {exc.status_code})") from exc
            except RETRYABLE as exc:
                if attempt == self._max_retries:
                    raise LLMUnavailable(f"{type(exc).__name__} after {self._max_retries} retries") from exc
                wait = 2 ** (attempt + 1)
                log.warning("llm %s, retry in %ss", type(exc).__name__, wait)
                self._sleep(wait)
                continue
            self._record(cfg.model, resp)
            return resp
        raise AssertionError("unreachable")

    def _record(self, model: str, resp) -> None:
        u = self.usage[model]
        u["calls"] += 1
        if resp.usage:
            u["prompt"] += resp.usage.prompt_tokens or 0
            u["completion"] += resp.usage.completion_tokens or 0

    def log_usage(self) -> None:
        for model, u in self.usage.items():
            log.info("llm usage %s: calls=%d prompt=%d completion=%d", model, u["calls"], u["prompt"], u["completion"])


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def parse_json(text: str | None):
    """Parse a JSON reply, tolerating markdown code fences and leading/trailing prose."""
    if not text:
        raise ValueError("empty reply")
    text = _FENCE.sub("", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
        end = max(text.rfind("}"), text.rfind("]"))
        if start < 0 or end <= start:
            raise
        return json.loads(text[start : end + 1])
