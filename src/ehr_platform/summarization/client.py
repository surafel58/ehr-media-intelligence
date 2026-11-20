"""Claude API call that returns a ClinicalSummary."""

from __future__ import annotations

from dataclasses import dataclass

import anthropic

from .. import config
from .prompt import SYSTEM_PROMPT
from .schema import ClinicalSummary

# Models that accept the server-side refusal fallback (falls back to another model on a policy decline).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-fable-5-1"}


class SummaryError(Exception):
    """The model did not return a usable summary."""


class QuotaExhausted(SummaryError):
    """The provider's daily quota is used up. The run should stop, not retry for hours."""


@dataclass
class LLMResult:
    summary: ClinicalSummary
    input_tokens: int
    output_tokens: int


class ClaudeSummarizer:
    def __init__(self, model: str = config.CLAUDE_MODEL, client: anthropic.Anthropic | None = None,
                 effort: str = "medium"):
        self.model = model
        self.effort = effort
        self.client = client or anthropic.Anthropic(max_retries=5)

    def call(self, user_text: str) -> LLMResult:
        kwargs = dict(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_text}],
            output_format=ClinicalSummary,
            output_config={"effort": self.effort},
        )
        if self.model in FALLBACK_MODELS:
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        response = self.client.beta.messages.parse(**kwargs)

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise SummaryError(f"declined by the model ({getattr(details, 'category', None) or 'no category'})")
        if response.stop_reason == "max_tokens":
            raise SummaryError("output hit max_tokens before the summary was complete")
        if response.parsed_output is None:
            raise SummaryError(f"no structured output (stop_reason={response.stop_reason})")
        return LLMResult(summary=response.parsed_output,
                         input_tokens=response.usage.input_tokens,
                         output_tokens=response.usage.output_tokens)
