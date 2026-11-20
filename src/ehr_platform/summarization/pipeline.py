"""Summarize every patient Bundle, using the cache when the records have not changed."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from .cache import SummaryCache
from .client import LLMResult, QuotaExhausted, SummaryError
from .context import PatientContext, build_context
from .prompt import PROMPT_VERSION, WORD_LIMIT, shorten_message, user_message
from .quality import run_checks, word_count
from .schema import ClinicalSummary, PatientSummary, Usage

NO_RECORDS_MODEL = "rule-based"


class _Skipped(Exception):
    """A task that did not run because the quota ran out first."""


def no_records_summary() -> ClinicalSummary:
    return ClinicalSummary(
        overview="No clinical notes, imaging or lab reports are on file for this patient. Only demographic data exists.",
        chief_concern="No clinical records",
        key_diagnoses=[], recent_media=[], anomalies=[],
        confidence="low", confidence_reason="There is no clinical content to summarize.",
    )


def generate(ctx: PatientContext, call: Callable[[str], LLMResult]) -> tuple[ClinicalSummary, Usage]:
    """One API call, plus one retry if the summary is over the word limit."""
    usage = Usage()
    result = call(user_message(ctx.text))
    usage.input_tokens, usage.output_tokens, usage.attempts = result.input_tokens, result.output_tokens, 1
    best = result.summary
    words = word_count(best)
    if words > WORD_LIMIT:
        retry = call(shorten_message(ctx.text, words))
        usage.input_tokens += retry.input_tokens
        usage.output_tokens += retry.output_tokens
        usage.attempts = 2
        if word_count(retry.summary) < words:
            best = retry.summary
    return best, usage


def _finish(ctx: PatientContext, summary: ClinicalSummary, model: str, usage: Usage) -> PatientSummary:
    return PatientSummary(
        patient_id=ctx.patient_id, record_hash=ctx.record_hash, model=model, prompt_version=PROMPT_VERSION,
        generated_at=datetime.now(timezone.utc), summary=summary, word_count=word_count(summary),
        quality=run_checks(summary, ctx), usage=usage,
    )


@dataclass
class RunResult:
    summaries: dict[str, PatientSummary] = field(default_factory=dict)
    from_cache: list[str] = field(default_factory=list)
    generated: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    pending: list[str] = field(default_factory=list)  # not summarized yet: no calls allowed, or the quota ran out
    stopped: str | None = None  # why the run ended early, e.g. the daily quota


def summarize_all(bundles: dict[str, dict], cache: SummaryCache, model: str,
                  call: Callable[[str], LLMResult] | None, force: bool = False, workers: int = 3,
                  on_done: Callable[[str, str], None] | None = None, cached_only: bool = False) -> RunResult:
    """Summarize each Bundle. `call` sends one prompt to the LLM; it is only used on a cache miss.

    With cached_only, nothing is sent: patients without a cached summary are listed as pending.
    """
    run = RunResult()
    todo: list[PatientContext] = []
    for pid, bundle in bundles.items():
        ctx = build_context(bundle)
        ctx.patient_id = pid  # the store key, also for bundles with no Patient entry
        use_model = model if ctx.has_records else NO_RECORDS_MODEL
        cached = None if force else cache.get(pid, ctx.record_hash, use_model, PROMPT_VERSION)
        if cached is None and cached_only and ctx.has_records:
            cached = cache.latest(pid, ctx.record_hash)  # any model, as long as it summarized these exact records
        if cached:
            fresh = run_checks(cached.summary, ctx)  # checks are cheap, keep them current
            if fresh != cached.quality:
                cached.quality = fresh
                cache.put(cached)
            run.summaries[pid] = cached
            run.from_cache.append(pid)
        elif not ctx.has_records:
            run.summaries[pid] = _finish(ctx, no_records_summary(), NO_RECORDS_MODEL, Usage())
            cache.put(run.summaries[pid])
            run.generated.append(pid)
        else:
            todo.append(ctx)

    if cached_only:
        run.pending = [ctx.patient_id for ctx in todo]
        return run
    if todo and call is None:
        raise SummaryError(f"{len(todo)} patients need a summary but no LLM client was given")
    stop = threading.Event()  # set once the quota is gone, so no task sends another request

    def task(ctx: PatientContext):
        if stop.is_set():
            raise _Skipped()
        try:
            return generate(ctx, call)
        except QuotaExhausted:
            stop.set()
            raise

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(task, ctx): ctx for ctx in todo}
        for future in as_completed(futures):
            ctx = futures[future]
            try:
                summary, usage = future.result()
            except _Skipped:
                run.pending.append(ctx.patient_id)
                continue
            except QuotaExhausted as exc:  # the rest would fail too: leave them for the next quota
                run.stopped = str(exc)
                run.pending.append(ctx.patient_id)
                continue
            except Exception as exc:  # one failed patient must not stop the run
                run.failed[ctx.patient_id] = f"{type(exc).__name__}: {exc}"
                if on_done:
                    on_done(ctx.patient_id, "failed")
                continue
            run.summaries[ctx.patient_id] = _finish(ctx, summary, model, usage)
            cache.put(run.summaries[ctx.patient_id])
            run.generated.append(ctx.patient_id)
            if on_done:
                on_done(ctx.patient_id, "done")
    return run
