"""
app/os_app.py — AgentOS entry point

Wraps the NewsAggregatorWorkflow in an AgentOS instance so it can be:
  1. Served as a FastAPI application
  2. Discovered and managed via the AgentOS control plane at os.agno.com

Run:
    fastapi dev app/os_app.py          # development (auto-reload)
    fastapi run app/os_app.py          # production
    uvicorn app.os_app:app --reload    # alternative

Then connect at https://os.agno.com → "Add new OS" → http://localhost:8000
"""

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Environment setup — must happen before any Agno / Anthropic imports
# ---------------------------------------------------------------------------
# Load .env from the repo root regardless of where this file is run from
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_claude_key = os.environ.get("CLAUDE_API_KEY")
if _claude_key and not os.environ.get("ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = _claude_key

if not os.environ.get("ANTHROPIC_API_KEY"):
    print(
        "ERROR: No API key found. Set CLAUDE_API_KEY in your .env file.",
        file=sys.stderr,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
import logging

from agno.db.sqlite import SqliteDb
from agno.os import AgentOS
from agno.os.config import AgentOSConfig, ChatConfig

from app.workflows.news_aggregator import NewsAggregatorWorkflow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

# ---------------------------------------------------------------------------
# Shared SQLite database (sessions + tracing stored here)
# ---------------------------------------------------------------------------
db = SqliteDb(db_file="data/agno.db")

# ---------------------------------------------------------------------------
# Workflow — give it a fixed ID so we can reference it in config
# ---------------------------------------------------------------------------
WORKFLOW_ID = "news-aggregator"

news_workflow = NewsAggregatorWorkflow(
    id=WORKFLOW_ID,
    name="News Aggregator",
    description=(
        "Multi-agent news aggregation pipeline. "
        "Searches, extracts, and analyses news coverage for any company "
        "over a specified time window."
    ),
    db=db,
)

# ---------------------------------------------------------------------------
# AgentOS configuration — quick prompts shown in the chat UI
# ---------------------------------------------------------------------------
config = AgentOSConfig(
    chat=ChatConfig(
        quick_prompts={
            WORKFLOW_ID: [
                "Analyze Apple for the last 3 months",
                "Nvidia news, last 6 months",
                "TSMC Q1 2024",
            ]
        }
    )
)

# ---------------------------------------------------------------------------
# AgentOS app
# ---------------------------------------------------------------------------
agent_os = AgentOS(
    name="News Aggregator OS",
    description="Multi-agent news aggregation and analysis system",
    workflows=[news_workflow],
    db=db,
    tracing=True,
    config=config,
    cors_allowed_origins=["*"],  # tighten for production
)

app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Allow direct execution: python app/os_app.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent_os.serve(app="app.os_app:app", host="localhost", port=8000, reload=True)
