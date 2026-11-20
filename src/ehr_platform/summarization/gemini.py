"""Gemini API call that returns a ClinicalSummary.

Same prompt and output schema as the Claude client. Used when no Claude credit is available:
the Gemini API has a free tier with rate limits, so rate-limit errors are retried with a wait.
"""

from __future__ import annotations

import os
import re
import time

import httpx2
from google import genai
from google.genai import errors, types

from .. import config
from .client import LLMResult, QuotaExhausted, SummaryError
from .prompt import SYSTEM_PROMPT
from .schema import ClinicalSummary

BLOCKED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}
RETRY_CODES = {429, 500, 503}


MAX_WAIT_S = 120  # a longer wait means the daily quota is used up, not a per-minute limit


def _retry_delay(exc: Exception, attempt: int) -> float:
    m = re.search(r"retry in ([\d.]+)s|retryDelay\W+(\d+)s", str(exc))
    if m:
        return float(m.group(1) or m.group(2)) + 1
    return min(60.0, 5.0 * 2 ** attempt)


def _daily_quota(exc: errors.APIError, attempt: int) -> bool:
    return exc.code == 429 and ("PerDay" in str(exc) or _retry_delay(exc, attempt) > MAX_WAIT_S)


class GeminiSummarizer:
    def __init__(self, model: str = config.GEMINI_MODEL, client: genai.Client | None = None, max_retries: int = 6,
                 sleep=time.sleep, timeout_s: int = 180):
        self.model = model
        self.client = client or genai.Client(
            api_key=os.environ.get("GEMINI_API_KEY"),
            http_options=types.HttpOptions(api_version="v1beta", timeout=timeout_s * 1000,
                                           retry_options=types.HttpRetryOptions(attempts=1)),
        )
        self.max_retries = max_retries
        self.sleep = sleep

    def call(self, user_text: str) -> LLMResult:
        cfg = types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT,
                                          response_mime_type="application/json",
                                          response_schema=ClinicalSummary)
        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.models.generate_content(model=self.model, contents=user_text, config=cfg)
                break
            except errors.APIError as exc:
                if _daily_quota(exc, attempt):  # waiting would take hours: stop and let the run end
                    raise QuotaExhausted(f"Gemini daily quota for {self.model} is used up") from exc
                if exc.code in RETRY_CODES and attempt < self.max_retries:
                    self.sleep(_retry_delay(exc, attempt))
                    continue
                raise SummaryError(f"Gemini error {exc.code}: {exc.message}") from exc
            except (httpx2.TransportError, TimeoutError, OSError) as exc:  # slow or dropped connection
                if attempt < self.max_retries:
                    self.sleep(min(60.0, 5.0 * 2 ** attempt))
                    continue
                raise SummaryError(f"Gemini did not answer: {type(exc).__name__}") from exc

        candidate = (response.candidates or [None])[0]
        finish = getattr(getattr(candidate, "finish_reason", None), "name", None)
        if finish in BLOCKED:
            raise SummaryError(f"blocked by Gemini ({finish})")
        if finish == "MAX_TOKENS":
            raise SummaryError("output hit the token limit before the summary was complete")
        parsed = response.parsed
        if not isinstance(parsed, ClinicalSummary):
            try:
                parsed = ClinicalSummary.model_validate_json(response.text or "")
            except ValueError as exc:
                raise SummaryError(f"no valid structured output (finish={finish})") from exc
        usage = response.usage_metadata
        output = (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
        return LLMResult(summary=parsed, input_tokens=usage.prompt_token_count or 0, output_tokens=output)
