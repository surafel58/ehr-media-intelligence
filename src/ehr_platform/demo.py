"""Build everything from the raw data and start the app, in one command.

    uv run python -m ehr_platform.demo

No API key is needed: AI summaries are loaded from data/summaries.json.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time

from . import config

STEPS = [
    ("Step 1 of 4: cleaning the raw EHR data", ["ehr_platform.ingestion"]),
    ("Step 2 of 4: mapping to FHIR R4 and validating", ["ehr_platform.fhir_mapping"]),
    ("Step 3 of 4: loading the saved AI summaries (no API calls)", ["ehr_platform.summarization", "--cached-only"]),
    ("Step 4 of 4: building the search index", ["ehr_platform.search"]),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the data and start the app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-build", action="store_true", help="start the server on the data already built")
    args = parser.parse_args()

    if not args.no_build:
        for title, module in STEPS:
            print(f"\n{title}", flush=True)
            started = time.perf_counter()
            if subprocess.run([sys.executable, "-m", *module], cwd=config.ROOT).returncode != 0:
                sys.exit(f"\n{title} failed. See the messages above.")
            print(f"Done in {time.perf_counter() - started:.0f} s", flush=True)

    url = f"http://{args.host}:{args.port}"
    if (config.ROOT / "frontend" / "dist" / "index.html").exists():
        print(f"\nOpen {url} for the app, or {url}/docs for the API.\n", flush=True)
    else:
        print(f"\nThe UI is not built yet. Build it with:\n    cd frontend && pnpm install && pnpm build\n"
              f"Meanwhile the API runs at {url}/docs\n", flush=True)

    import uvicorn

    uvicorn.run("ehr_platform.api.app:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
