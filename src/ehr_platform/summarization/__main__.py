"""Generate AI summaries: uv run python -m ehr_platform.summarization"""

import argparse
import os
import sys

from .. import config
from ..fhir_mapping import FhirStore
from .cache import SummaryCache
from .client import ClaudeSummarizer
from .context import build_context
from .pipeline import summarize_all
from .prompt import PROMPT_VERSION
from .report import PRICES, write_outputs


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize each patient's records with Claude.")
    parser.add_argument("--patient", action="append", help="only this patient id (repeatable)")
    parser.add_argument("--limit", type=int, help="only the first N patients")
    parser.add_argument("--model", default=config.CLAUDE_MODEL)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--force", action="store_true", help="ignore the cache")
    parser.add_argument("--dry-run", action="store_true", help="show what would be sent and the rough cost")
    args = parser.parse_args()

    with FhirStore() as store:
        ids = args.patient or store.patient_ids()
        if args.limit:
            ids = ids[: args.limit]
        bundles = {pid: b for pid in ids if (b := store.bundle(pid))}

    with SummaryCache() as cache:
        if args.dry_run:
            contexts = [build_context(b) for b in bundles.values()]
            need = [c for c in contexts if c.has_records
                    and (args.force or not cache.get(c.patient_id, c.record_hash, args.model, PROMPT_VERSION))]
            tokens_in = sum(len(c.text) for c in need) // 4 + 900 * len(need)
            price_in, price_out = PRICES.get(args.model, (0, 0))
            estimate = tokens_in / 1e6 * price_in + 2000 * len(need) / 1e6 * price_out
            print(f"Patients: {len(contexts)}  with records: {sum(c.has_records for c in contexts)}  "
                  f"need an API call: {len(need)}")
            print(f"Model: {args.model}  rough input tokens: {tokens_in:,}  rough cost: ${estimate:.2f}")
            return

        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ANTHROPIC_API_KEY is not set. Add it to .env (see .env.example).", file=sys.stderr)
        summarizer = ClaudeSummarizer(model=args.model)
        total = len(bundles)

        def progress(pid: str, status: str) -> None:
            print(f"  {status:<6} {pid}", flush=True)

        print(f"Summarizing {total} patients with {args.model}...")
        run = summarize_all(bundles, cache, args.model, summarizer.call, force=args.force,
                            workers=args.workers, on_done=progress)

    report = write_outputs(run, config.SUMMARY_DIR)
    print(f"\nGenerated: {len(run.generated)}  from cache: {len(run.from_cache)}  failed: {len(run.failed)}")
    print(f"Summaries in {config.SUMMARY_DIR}\nQuality report: {report}")


if __name__ == "__main__":
    main()
