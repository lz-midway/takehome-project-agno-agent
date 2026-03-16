"""
demo/scenario_apple.py — Pre-configured demo scenario

Runs the full News Aggregator pipeline for Apple Inc over the last 3 months.
This file can be executed directly without CLI arguments.

Run:
    python demo/scenario_apple.py
"""

import os
import sys
from pathlib import Path

# Allow running from the repo root or from demo/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

_key = os.environ.get("CLAUDE_API_KEY")
if _key and not os.environ.get("ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = _key

if not os.environ.get("ANTHROPIC_API_KEY"):
    print("ERROR: Set CLAUDE_API_KEY in .env", file=sys.stderr)
    sys.exit(1)

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)

from main import run_aggregator  # noqa: E402 — import after path setup

SCENARIO = {
    "company_name": "Apple Inc",
    "time_period": "last 3 months",
}

if __name__ == "__main__":
    print("Demo scenario: Apple Inc — last 3 months")
    result = run_aggregator(**SCENARIO)
    if result:
        print("\nDemo completed successfully.")
    else:
        print("\nDemo ended with errors.", file=sys.stderr)
        sys.exit(1)
