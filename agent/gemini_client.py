"""
agent/gemini_client.py — Hybrid Gemini client with dynamic model switching.

Implements the Hybrid Cost Strategy (masterplan 2.1):
  - SCANNING / WATCHING / ACTIVE  → Gemini Flash  (cheap)
  - APPROACHING / TRIGGERED       → Gemini Pro    (deep reasoning)

Key API patterns:
  - ``google-genai`` >= 1.64.0
  - Automatic function calling with Python functions as tools.
  - ``ThinkingConfig(thinking_level=...)`` for Pro model variation.
  - ``response_schema=PydanticModel`` for structured output.

FIX F3-07: Added exponential backoff retry (3 attempts, 1s/2s/4s).

Reference: masterplan.md §2.1, §18.1-18.3
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from google import genai
from google.genai import types

from config.settings import (
    GEMINI_API_KEY,
    GEMINI_PRO_MODEL,
    GEMINI_FLASH_MODEL,
    DAILY_BUDGET_USD,
    GEMINI_MAX_RETRIES,
    GEMINI_RETRY_BASE_DELAY,
)
from agent.system_prompt import SYSTEM_PROMPT
from agent.tool_registry import ALL_TOOLS

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = logging.getLogger(__name__)


class BudgetExceededError(RuntimeError):
    """Raised when the daily Gemini API budget is exhausted (FIX H-06)."""
    pass


# ---------------------------------------------------------------------------
# Retry configuration (FIX F3-07 + L-12: config from settings)
# ---------------------------------------------------------------------------
MAX_RETRIES: int = GEMINI_MAX_RETRIES
RETRY_BASE_DELAY: float = GEMINI_RETRY_BASE_DELAY


async def _async_retry(coro_factory, *, max_retries=MAX_RETRIES, base_delay=RETRY_BASE_DELAY):
    """Retry an async callable with exponential backoff.

    ``coro_factory`` is a zero-arg callable that returns a new coroutine each time.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            return await coro_factory()
        except Exception as exc:
            last_exc = exc
            delay = base_delay * (2 ** attempt)
            logger.warning(
                "Gemini API attempt %d/%d failed (%s) — retrying in %.1fs",
                attempt + 1, max_retries, exc, delay,
            )
            await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]


def _sync_retry(call_factory, *, max_retries=MAX_RETRIES, base_delay=RETRY_BASE_DELAY):
    """Retry a sync callable with exponential backoff."""
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            return call_factory()
        except Exception as exc:
            last_exc = exc
            delay = base_delay * (2 ** attempt)
            logger.warning(
                "Gemini API attempt %d/%d failed (%s) — retrying in %.1fs",
                attempt + 1, max_retries, exc, delay,
            )
            time.sleep(delay)
    raise last_exc  # type: ignore[misc]

# ---------------------------------------------------------------------------
# State → Model mapping  (masterplan 2.1)
# ---------------------------------------------------------------------------
_STATE_MODEL_MAP: dict[str, str] = {
    "SCANNING": GEMINI_FLASH_MODEL,
    "WATCHING": GEMINI_FLASH_MODEL,
    "APPROACHING": GEMINI_PRO_MODEL,
    "TRIGGERED": GEMINI_PRO_MODEL,
    "ACTIVE": GEMINI_FLASH_MODEL,
    "CLOSED": GEMINI_FLASH_MODEL,
    "CANCELLED": GEMINI_FLASH_MODEL,
}

# Pro gets thinking_config; Flash does not.
_THINKING_LEVELS: dict[str, str] = {
    "APPROACHING": "high",
    "TRIGGERED": "high",
}


def model_for_state(state: str) -> str:
    """Return the Gemini model name appropriate for *state*.

    FIX M-09: Logs a warning when falling back to Flash for an unknown state.
    """
    model = _STATE_MODEL_MAP.get(state)
    if model is None:
        logger.warning(
            "⚠️ Unknown state '%s' — falling back to Flash model (%s)",
            state, GEMINI_FLASH_MODEL,
        )
        return GEMINI_FLASH_MODEL
    return model


def _build_config(
    state: str,
    *,
    thinking_level: str | None = None,
    response_schema: type[BaseModel] | None = None,
) -> types.GenerateContentConfig:
    """Build ``GenerateContentConfig`` appropriate for *state*.

    Parameters
    ----------
    state:
        Current state-machine state (e.g. ``"APPROACHING"``).
    thinking_level:
        Override the thinking level (``"low"`` | ``"medium"`` | ``"high"``).
        Defaults to the level dictated by the state.
    response_schema:
        If provided, the response will be constrained to this Pydantic model
        (structured output).  Tools are omitted when a schema is set because
        the Gemini API returns JSON directly.
    """
    level = thinking_level or _THINKING_LEVELS.get(state)

    thinking_cfg = (
        types.ThinkingConfig(thinking_level=level) if level else None
    )

    if response_schema is not None:
        # Structured-output mode: no tools, JSON response.
        return types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=response_schema,
            thinking_config=thinking_cfg,
        )

    # Normal tool-calling mode.
    return types.GenerateContentConfig(
        tools=ALL_TOOLS,
        system_instruction=SYSTEM_PROMPT,
        thinking_config=thinking_cfg,
    )


# ---------------------------------------------------------------------------
# Client wrapper
# ---------------------------------------------------------------------------
class GeminiClient:
    """Thin wrapper around ``google.genai.Client`` with hybrid model logic.

    Includes token/cost tracking to enforce DAILY_BUDGET_USD (FIX §7.4).

    Usage::

        client = GeminiClient()
        resp = client.generate("SCANNING", "Analyse XAUUSD M15 data: ...")
        plan = client.generate_structured("TRIGGERED", prompt, TradingPlan)
    """

    # Approximate cost per 1M tokens (USD).
    # Gemini pricing is periodically updated — verify at
    # https://ai.google.dev/pricing when models change.
    # FIX L-14: Token count comes from response.usage_metadata;
    # no local heuristic (÷4) is used — counts are exact from the API.
    _COST_PER_1M: dict[str, dict[str, float]] = {
        "flash": {"input": 0.075, "output": 0.30},
        "pro":   {"input": 1.25,  "output": 5.00},
    }

    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or GEMINI_API_KEY
        if not key:
            raise ValueError(
                "GEMINI_API_KEY not set. Provide via env var or constructor."
            )
        self._client = genai.Client(api_key=key)
        # FIX §7.4: Token & cost tracking
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0
        self._total_cost_usd: float = 0.0
        self._daily_budget_usd: float = DAILY_BUDGET_USD
        self._call_count: int = 0

    # -- Cost accounting (FIX §7.4) -----------------------------------------

    def _account_usage(self, response: types.GenerateContentResponse, model: str) -> None:
        """Extract token counts from response and accumulate cost."""
        try:
            usage = getattr(response, "usage_metadata", None)
            if not usage:
                return

            input_tokens = getattr(usage, "prompt_token_count", 0) or 0
            output_tokens = getattr(usage, "candidates_token_count", 0) or 0

            self._total_input_tokens += input_tokens
            self._total_output_tokens += output_tokens
            self._call_count += 1

            # Determine cost tier
            tier = "pro" if "pro" in model.lower() else "flash"
            costs = self._COST_PER_1M.get(tier, self._COST_PER_1M["flash"])
            call_cost = (
                (input_tokens / 1_000_000) * costs["input"]
                + (output_tokens / 1_000_000) * costs["output"]
            )
            self._total_cost_usd += call_cost

            logger.info(
                "💰 Gemini [%s] in=%d out=%d cost=$%.4f (total=$%.4f/%s)",
                tier, input_tokens, output_tokens,
                call_cost, self._total_cost_usd, self._daily_budget_usd,
            )

            if self._total_cost_usd >= self._daily_budget_usd:
                logger.warning(
                    "⚠️ DAILY BUDGET EXCEEDED: $%.2f ≥ $%.2f — "
                    "consider pausing Gemini calls",
                    self._total_cost_usd, self._daily_budget_usd,
                )
        except Exception as exc:
            logger.debug("Cost accounting error (non-fatal): %s", exc)

    @property
    def cost_summary(self) -> dict:
        """Return current usage summary."""
        return {
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "total_cost_usd": round(self._total_cost_usd, 4),
            "daily_budget_usd": self._daily_budget_usd,
            "budget_remaining_usd": round(self._daily_budget_usd - self._total_cost_usd, 4),
            "call_count": self._call_count,
        }

    def reset_daily_cost(self) -> None:
        """Reset counters (call at start of each trading day)."""
        logger.info(
            "Daily cost reset: was $%.4f (%d calls, %d in + %d out tokens)",
            self._total_cost_usd, self._call_count,
            self._total_input_tokens, self._total_output_tokens,
        )
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_cost_usd = 0.0
        self._call_count = 0

    @property
    def budget_exceeded(self) -> bool:
        """True if daily cost exceeds budget."""
        return self._total_cost_usd >= self._daily_budget_usd

    def _check_budget(self) -> None:
        """Raise BudgetExceededError if daily budget is exhausted (FIX H-06)."""
        if self.budget_exceeded:
            raise BudgetExceededError(
                f"Gemini daily budget exhausted: ${self._total_cost_usd:.4f} "
                f">= ${self._daily_budget_usd:.2f} — call blocked"
            )

    # -- Sync helpers -------------------------------------------------------

    def generate(
        self,
        state: str,
        contents: str,
        *,
        thinking_level: str | None = None,
    ) -> types.GenerateContentResponse:
        """Run a tool-calling generation for the given *state* (with retry)."""
        self._check_budget()
        model = model_for_state(state)
        config = _build_config(state, thinking_level=thinking_level)
        logger.info("Gemini generate  model=%s  state=%s", model, state)
        resp = _sync_retry(
            lambda: self._client.models.generate_content(
                model=model, contents=contents, config=config,
            )
        )
        self._account_usage(resp, model)
        return resp

    def generate_structured(
        self,
        state: str,
        contents: str | list,
        schema: type[BaseModel],
        *,
        thinking_level: str | None = None,
    ) -> types.GenerateContentResponse:
        """Run a structured-output generation returning *schema* JSON (with retry)."""
        self._check_budget()
        model = model_for_state(state)
        config = _build_config(
            state,
            thinking_level=thinking_level,
            response_schema=schema,
        )
        logger.info(
            "Gemini structured  model=%s  state=%s  schema=%s",
            model, state, schema.__name__,
        )
        resp = _sync_retry(
            lambda: self._client.models.generate_content(
                model=model, contents=contents, config=config,
            )
        )
        self._account_usage(resp, model)
        return resp

    # -- Async helpers ------------------------------------------------------

    async def agenerate(
        self,
        state: str,
        contents: str,
        *,
        thinking_level: str | None = None,
    ) -> types.GenerateContentResponse:
        """Async tool-calling generation (with retry)."""
        self._check_budget()
        model = model_for_state(state)
        config = _build_config(state, thinking_level=thinking_level)
        logger.info("Gemini agenerate  model=%s  state=%s", model, state)
        resp = await _async_retry(
            lambda: self._client.aio.models.generate_content(
                model=model, contents=contents, config=config,
            )
        )
        self._account_usage(resp, model)
        return resp

    async def agenerate_structured(
        self,
        state: str,
        contents: str | list,
        schema: type[BaseModel],
        *,
        thinking_level: str | None = None,
    ) -> types.GenerateContentResponse:
        """Async structured-output generation (with retry)."""
        self._check_budget()
        model = model_for_state(state)
        config = _build_config(
            state,
            thinking_level=thinking_level,
            response_schema=schema,
        )
        logger.info(
            "Gemini agenerate_structured  model=%s  state=%s  schema=%s",
            model, state, schema.__name__,
        )
        resp = await _async_retry(
            lambda: self._client.aio.models.generate_content(
                model=model, contents=contents, config=config,
            )
        )
        self._account_usage(resp, model)
        return resp

    def close(self) -> None:
        """Release underlying HTTP resources."""
        self._client.close()
