"""Generate AI summaries: uv run python -m ehr_platform.summarization"""

import argparse
import os
import sys

from .. import config
from ..fhir_mapping import FhirStore
from .cache import SummaryCache
from .context import build_context
from .pipeline import summarize_all
from .prompt import PROMPT_VERSION
from .report import PRICES, write_outputs

PROVIDERS = {
    "claude": {"model": config.CLAUDE_MODEL, "key": "ANTHROPIC_API_KEY", "workers": 3},
    "gemini": {"model": config.GEMINI_MODEL, "key": "GEMINI_API_KEY", "workers": 1},  # free tier is rate limited
}


def make_summarizer(provider: str, model: str):
    if provider == "gemini":
        from .gemini import GeminiSummarizer
        return GeminiSummarizer(model=model)
    from .client import ClaudeSummarizer
    return ClaudeSummarizer(model=model)


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize each patient's records with an LLM.")
    parser.add_argument("--provider", choices=sorted(PROVIDERS), default=config.LLM_PROVIDER)
    parser.add_argument("--model", help="model id (default depends on the provider)")
    parser.add_argument("--patient", action="append", help="only this patient id (repeatable)")
    parser.add_argument("--limit", type=int, help="only the first N patients")
    parser.add_argument("--workers", type=int, help="parallel API calls")
    parser.add_argument("--force", action="store_true", help="ignore the cache")
    parser.add_argument("--dry-run", action="store_true", help="show what would be sent and the rough cost")
    parser.add_argument("--cached-only", action="store_true",
                        help="no API calls: report saved summaries, list the rest as pending")
    args = parser.parse_args()
    settings = PROVIDERS[args.provider]
    model = args.model or settings["model"]

    with FhirStore() as store:
        ids = args.patient or store.patient_ids()
        if args.limit:
            ids = ids[: args.limit]
        bundles = {pid: b for pid in ids if (b := store.bundle(pid))}

    with SummaryCache() as cache:
        if args.dry_run:
            contexts = [build_context(b) for b in bundles.values()]
            need = [c for c in contexts if c.has_records
                    and (args.force or not cache.get(c.patient_id, c.record_hash, model, PROMPT_VERSION))]
            tokens_in = sum(len(c.text) for c in need) // 4 + 900 * len(need)
            price_in, price_out = PRICES.get(model, (0, 0))
            estimate = tokens_in / 1e6 * price_in + 2000 * len(need) / 1e6 * price_out
            print(f"Patients: {len(contexts)}  with records: {sum(c.has_records for c in contexts)}  "
                  f"need an API call: {len(need)}")
            print(f"Model: {model}  rough input tokens: {tokens_in:,}  rough cost: ${estimate:.2f}")
            return

        call = None
        if not args.cached_only:
            if not os.environ.get(settings["key"]):
                print(f"{settings['key']} is not set. Add it to .env (see .env.example).", file=sys.stderr)
                sys.exit(1)
            call = make_summarizer(args.provider, model).call

        def progress(pid: str, status: str) -> None:
            print(f"  {status:<6} {pid}", flush=True)

        print(f"Summarizing {len(bundles)} patients with {model}...", flush=True)
        run = summarize_all(bundles, cache, model, call, force=args.force, cached_only=args.cached_only,
                            workers=args.workers or settings["workers"], on_done=progress)

    report = write_outputs(run, config.SUMMARY_DIR)
    print(f"\nGenerated: {len(run.generated)}  from cache: {len(run.from_cache)}  failed: {len(run.failed)}"
          f"  pending: {len(run.pending)}")
    print(f"Summaries in {config.SUMMARY_DIR}\nQuality report: {report}")


if __name__ == "__main__":
    main()
