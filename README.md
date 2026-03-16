# Multi-Agent News Aggregator

**Track: Option A — Browser Automation Multi-Agent**

An Agno Workflow that accepts a company name and time window, searches for news via
DuckDuckGo, extracts article body text, classifies sentiment, and compiles a single-page
analytical report. Fully functional via the CLI; AgentOS UI integration is present but
not yet stable (see Known Limitations).

---

## Agent Architecture

The system is implemented as a single Agno `Workflow` with four sequential agents. No
agent calls another directly — the Workflow orchestrates all handoffs using typed Pydantic
models at every boundary.

```
User Input (company + time period)
        │
        ▼
┌───────────────┐   PlannerInput        ┌───────────────┐
│               │ ─────────────────────▶│               │
│    Planner    │                       │    Browser    │
│     Agent     │◀─ ResearchPlan ───────│     Agent     │
│               │                       │               │
└───────────────┘                       └───────────────┘
                                               │
                                         RawArticleList
                                               │
                                               ▼
                                        ┌───────────────┐
                                        │  Extraction   │
                                        │    Agent      │
                                        └───────────────┘
                                               │
                                        EnrichedArticleList
                                               │
                                               ▼
                                        ┌───────────────┐
                                        │   Compiler    │
                                        │    Agent      │
                                        └───────────────┘
                                               │
                                          FinalReport
                                        (JSON + stdout)
```

Every handoff object is a Pydantic model defined in `app/models/state.py`. The workflow
yields typed `WorkflowEvent` objects (`ProgressEvent`, `ErrorEvent`, `ReportEvent`) so
callers never parse raw strings.

---

## What Each Agent Does

### Planner Agent
Receives a `PlannerInput` (company name + date range) and produces a `ResearchPlan`
containing 5–6 DuckDuckGo search queries across six categories: general, earnings,
leadership, legal/regulatory, product, and partnership. Identifies the company's canonical
name, ticker symbol, and common aliases so queries cover different terminology for the same
entity. No tools — pure LLM reasoning.

### Browser Agent
The only agent with external tool access. Receives a `BrowserInput` wrapping the full
`ResearchPlan`, executes every query via `DuckDuckGoTools`, deduplicates results by URL,
then fetches each article's body text via `Newspaper4kTools`. Paywalled or unreachable
articles are kept with `content_status=unavailable` rather than discarded — their headline
and metadata still carry signal for downstream sentiment and event tagging.

### Extraction Agent
Receives an `ExtractionInput` containing all raw articles plus the date window and an
anchor date. Performs three tasks: normalises relative timestamps ("3 days ago") to
absolute ISO dates using the anchor, discards articles outside the date window, and
classifies each article's sentiment (positive / negative / neutral) with a 0–1 confidence
score and a rationale that cites specific content. When body text is available it is the
primary signal; headline-only articles receive a discounted confidence score. Also tags
each article with one or more event types from a fixed taxonomy. No tools — pure LLM
reasoning.

### Compiler Agent
Receives a `CompilerInput` with the full enriched article array. Pass 1 computes aggregate
statistics: sentiment breakdown, event type counts, body coverage percentage, and actual
date range. Pass 2 writes four narrative sections — executive summary, key events,
sentiment analysis, notable headlines — drawing on article body content rather than just
restating headlines. No tools — pure LLM reasoning.

---

## Tools Used

### AI-Assisted Coding — Claude (via Claude.ai)
The majority of this project was built with Claude as a coding assistant:

- **Greenfield framework setup**
- **Bug research and diagnosis**
- **Refactoring**
- **Test scaffolding**

### Runtime Tools (within the agentic pipeline)
- **`DuckDuckGoTools`** (agno built-in) — news search; used exclusively by the Browser Agent
- **`Newspaper4kTools`** (agno built-in) — article body extraction; used exclusively by the
  Browser Agent
- **Claude Sonnet (`claude-sonnet-4-5`)** — the LLM backing all four agents

---

## Repository Structure

```
.
├── app/
│   ├── os_app.py               # AgentOS server entry point (experimental)
│   ├── agents/
│   │   ├── planner.py          # Planner Agent definition
│   │   ├── browser.py          # Browser Agent + tool configuration
│   │   ├── extraction.py       # Extraction Agent definition
│   │   └── compiler.py         # Compiler Agent definition
│   ├── models/
│   │   └── state.py            # All Pydantic models: inputs, outputs, events
│   └── workflows/
│       └── news_aggregator.py  # Workflow orchestration, retry, observability
├── tests/
│   ├── test_state.py           # Unit tests: date parsing + model validation
│   └── test_workflow.py        # Unit tests: workflow paths (LLM calls mocked)
├── data/                       # Output reports + observability logs (auto-created)
├── main.py                     # CLI entry point
├── conftest.py                 # pytest sys.path configuration
├── pytest.ini                  # pytest rootdir + pythonpath settings
├── requirements.txt
├── .env.example                # API key template
└── .env                        # Your API key (not committed)
```

---

## Setup

### 1. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure your API key

```bash
cp .env.example .env
```

Edit `.env`:

```
CLAUDE_API_KEY=sk-ant-your-key-here
```

> `CLAUDE_API_KEY` is automatically bridged to `ANTHROPIC_API_KEY` at startup.

---

## Running (CLI)

### Default run (Apple Inc, last 3 months)

```bash
python main.py
```

### Custom company and time period

```bash
python main.py "Nvidia" "last 3 months"
python main.py "TSMC" "2024-01-01 to 2024-06-30"
python main.py "Microsoft" "Q1 2024"
```

### Supported time period formats

| Format | Example |
|---|---|
| `last N days / weeks / months / years` | `last 3 months` |
| `past N days / weeks / months / years` | `past 6 months` |
| ISO date range | `2024-01-01 to 2024-06-30` |
| Quarter | `Q1 2024`, `Q4 2023` |

### Expected output

```
============================================================
  News Aggregator
  Company : Apple Inc
  Period  : last 3 months
============================================================

Starting news aggregation for Apple Inc (2025-12-15 to 2026-03-15)…
[1/4] Running Planner Agent…
  ✓ Plan created: 6 queries for 'Apple Inc'
[2/4] Running Browser Agent…
  ✓ Collected 18 articles (6 queries executed)
[3/4] Running Extraction Agent…
  ✓ 14 articles enriched (4 discarded outside window)
[4/4] Running Compiler Agent…
  ✓ Report compiled
  📄 Report saved → data/apple_inc_report_20260315_103045.json
  📊 Observability log → data/logs/apple_inc_20260315_103045.json

──────────────────────────────────────────────────────────────
  ✅  FINAL REPORT — APPLE INC
...
```

---

## Running Tests

```bash
python -m pytest tests/ -v
```

57 tests covering: date parsing, all Pydantic model constraints, workflow orchestration
(happy path, 3 hard failure modes, 1 soft/degraded mode), retry logic, observability
tracker, message parsing, and all typed event models. No real LLM calls are made.

---

## Output Files

| File | Description |
|---|---|
| `data/<company>_report_<timestamp>.json` | Full `FinalReport` with all narrative sections |
| `data/logs/<company>_<timestamp>.json` | Per-agent latency, token counts, warnings |

---

## Tradeoffs and Known Limitations

### AgentOS UI — not currently functional
`app/os_app.py` exists and starts a FastAPI server (`fastapi dev app/os_app.py`), but
connecting it to [os.agno.com](https://os.agno.com) does not work reliably. The agno 2.5.9
`Workflow` API differs from what the AgentOS control plane expects — specifically, the
`run()` override signature and the `WorkflowExecutionInput` contract are not yet aligned.
The CLI is the recommended way to run the system.

### DuckDuckGo rate limiting
DuckDuckGo does not require an API key but will rate-limit or block aggressive request
patterns. Running many queries in quick succession (6 queries × N articles each) can
trigger this. If the Browser Agent returns fewer articles than expected, wait a few minutes
and retry.

### Paywalled articles
Major outlets (WSJ, FT, Bloomberg) are frequently paywalled. These articles are preserved
as `content_status=unavailable` so their headline and metadata still contribute to event
tagging, but sentiment analysis for those articles is headline-only with a discounted
confidence score.

### Output schema enforcement
Agent outputs are structured via `output_schema=` in the Agno `Agent` constructor. If the
LLM produces malformed JSON that fails Pydantic validation, the `with_retry` wrapper will
retry up to 3 times. Persistent failures surface as a fatal `ErrorEvent` with the
validation error message.

### No persistent memory between runs
Each workflow run is stateless. There is no cross-run memory or deduplication — running the
same company twice will collect and analyse articles independently both times.

### Date window accuracy
Relative timestamps from DuckDuckGo ("3 days ago", "last week") are resolved by the
Extraction Agent using Claude's reasoning, not deterministic parsing. Edge cases near the
date window boundary may be inconsistently included or excluded.
