"""Start the API: uv run python -m ehr_platform.api"""

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the search API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run("ehr_platform.api.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
