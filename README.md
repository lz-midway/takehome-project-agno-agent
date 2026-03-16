# Multi-Agent News Aggregator

An Agno Workflow that accepts a company name and time window, searches for news coverage
via DuckDuckGo, extracts article body text, classifies sentiment, and compiles an
analytical single-page report.

```
Planner → Browser → Extraction → Compiler
```

---

## Repository Structure

```
.
├── app/
│   ├── agents/
│   │   ├── planner.py        # Generates search queries & research plan
│   │   ├── browser.py        # Searches DuckDuckGo + fetches article bodies
│   │   ├── extraction.py     # Normalises dates, classifies sentiment & events
│   │   └── compiler.py       # Synthesises enriched articles → final report
│   ├── models/
│   │   └── state.py          # All shared Pydantic models (state schema)
│   └── workflows/
│       └── news_aggregator.py  # Workflow orchestration, retry, observability
├── tests/
│   ├── test_state.py         # Unit tests: parse_time_period + Pydantic models
│   └── test_workflow.py      # Unit tests: workflow paths (LLM calls mocked)
├── data/                     # Output reports + observability logs (auto-created)
├── demo/
│   └── scenario_apple.py     # Pre-configured Apple Inc demo
├── main.py                   # CLI entry point
├── requirements.txt
└── .env                      # API key (create from .env.example)
```

---

## Setup

### 1. Clone / navigate to the repo root

```bash
cd news_aggregator
```

### 2. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure the API key

Create a `.env` file in the repo root:

```
CLAUDE_API_KEY=sk-ant-...
```

> The system automatically maps `CLAUDE_API_KEY` → `ANTHROPIC_API_KEY` at startup,
> which is what the Agno SDK expects internally.

---

## Running

### Default demo (Apple Inc, last 3 months)

```bash
python main.py
```

### Custom company and period

```bash
python main.py "Nvidia" "last 3 months"
python main.py "TSMC" "2024-01-01 to 2024-06-30"
python main.py "Microsoft" "Q1 2024"
```

### Pre-configured demo scenario

```bash
python demo/scenario_apple.py
```

---

## Supported Time Period Formats

| Format | Example |
|---|---|
| `last N days/weeks/months/years` | `"last 3 months"` |
| `past N days/weeks/months/years` | `"past 6 months"` |
| ISO range | `"2024-01-01 to 2024-06-30"` |
| Quarter | `"Q1 2024"`, `"Q4 2023"` |

---

## Running Tests

```bash
python -m pytest tests/ -v
```

The tests do **not** make real LLM calls — all agent interactions are mocked.

---

## Output

After a successful run you will find two files written to `data/`:

| File | Description |
|---|---|
| `data/<company>_report_<timestamp>.json` | Final structured report (FinalReport model) |
| `data/logs/<company>_<timestamp>.json` | Observability log (latency, token counts, warnings) |

---

## Pipeline Detail

### Planner Agent
- Receives: company name, start/end dates
- Produces: `ResearchPlan` — 5-6 DuckDuckGo search queries across categories
  (general, earnings, leadership, legal/regulatory, product, partnership)
- Tools: none (pure reasoning)

### Browser Agent
- Receives: `ResearchPlan`
- Produces: `RawArticleList` — deduplicated articles with headline, publisher,
  timestamp, URL, and body text where extractable
- Tools: `DuckDuckGoTools` (news search), `Newspaper4kTools` (body extraction)
- Paywalled/error articles are kept with `content_status=unavailable`

### Extraction Agent
- Receives: raw articles + date window
- Produces: `EnrichedArticleList` — filtered to window, with normalised dates,
  sentiment (positive/negative/neutral with confidence + rationale), event type tags
- Tools: none (pure reasoning)
- Articles outside the date window are discarded and counted in `discarded_count`

### Compiler Agent
- Receives: enriched articles
- Produces: `FinalReport` — aggregate stats + four narrative sections
  (executive summary, key events, sentiment analysis, notable headlines)
- Tools: none (pure reasoning)
- Narrative sections draw on article body text, not just headlines

---

## Failure Handling

| Scenario | Behaviour |
|---|---|
| Empty company name | Hard error — pipeline aborts before any LLM call |
| Unparseable time period | Hard error — pipeline aborts before any LLM call |
| Planner / Browser / Extraction / Compiler failure | 3 retries with exponential back-off, then FATAL error response |
| 0 articles collected | Hard error — pipeline aborts after Browser stage |
| < 3 articles after extraction | Soft failure — pipeline continues in "degraded" mode, warning added to report |
| Paywalled / unreadable article | Soft — kept as `content_status=unavailable`, headline + metadata preserved |

---

## Observability Log Format

```json
{
  "run_id": "...",
  "company": "Apple Inc",
  "timestamp_utc": "2024-03-15T10:30:00",
  "total_latency_s": 47.2,
  "agent_latencies_s": {
    "planner": 3.1,
    "browser": 32.4,
    "extraction": 6.8,
    "compiler": 4.9
  },
  "agent_success": {
    "planner": true,
    "browser": true,
    "extraction": true,
    "compiler": true
  },
  "token_usage": { "planner": {...}, "browser": {...}, ... },
  "article_counts": { "raw": 18, "enriched": 14 },
  "warnings": []
}
```
