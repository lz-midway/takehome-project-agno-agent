"""
news_aggregator.py — NewsAggregatorWorkflow

Orchestrates the four-agent pipeline:
  Planner → Browser → Extraction → Compiler

Key responsibilities of this module:
- `parse_time_period()`: resolve natural-language or ISO date range strings into
  concrete (start_date, end_date) tuples.
- `with_retry()`: simple retry wrapper for agent calls with exponential back-off.
- `ObservabilityTracker`: records per-agent latency, token usage, and warnings,
  then writes a structured JSON log to data/logs/.
- `NewsAggregatorWorkflow`: the Agno Workflow subclass whose `run()` method defines
  the linear execution order and all failure-handling logic.

Failure modes handled:
  1. HARD — malformed input (bad date string, missing company name): abort immediately.
  2. HARD — Planner / Browser agent fatal failure after retries: abort with error report.
  3. SOFT — low article count (<3 after extraction): continue in "degraded" mode with
     a warning flag in the final report.
  4. SOFT — individual article fetch failure: recorded as content_status=unavailable,
     pipeline continues.
  5. HARD — Extraction / Compiler agent fatal failure after retries: abort.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator

from agno.agent import Agent
from agno.workflow import Workflow, RunResponse

from app.agents.browser import create_browser_agent
from app.agents.compiler import create_compiler_agent
from app.agents.extraction import create_extraction_agent
from app.agents.planner import create_planner_agent
from app.models.state import (
    EnrichedArticleList,
    FinalReport,
    RawArticleList,
    ResearchPlan,
    WorkflowInput,
    WorkflowStatus,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------

class ObservabilityTracker:
    """Lightweight per-run telemetry collector."""

    def __init__(self, company_name: str, run_id: str):
        self.company_name = company_name
        self.run_id = run_id
        self._wall_start = time.time()
        self._agent_timers: dict[str, float] = {}
        self.agent_latencies: dict[str, float] = {}
        self.agent_success: dict[str, bool] = {}
        self.token_usage: dict[str, dict] = {}
        self.warnings: list[str] = []
        self.article_counts: dict[str, int] = {}

    def start_agent(self, name: str) -> None:
        self._agent_timers[name] = time.time()
        logger.info(f"[{name}] started")

    def end_agent(self, name: str, *, success: bool, tokens: dict | None = None) -> None:
        elapsed = time.time() - self._agent_timers.get(name, time.time())
        self.agent_latencies[name] = round(elapsed, 2)
        self.agent_success[name] = success
        self.token_usage[name] = tokens or {}
        status = "OK" if success else "FAILED"
        logger.info(f"[{name}] {status} in {elapsed:.1f}s")

    def add_warning(self, msg: str) -> None:
        logger.warning(msg)
        self.warnings.append(msg)

    def set_article_count(self, stage: str, count: int) -> None:
        self.article_counts[stage] = count

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "company": self.company_name,
            "timestamp_utc": datetime.utcnow().isoformat(),
            "total_latency_s": round(time.time() - self._wall_start, 2),
            "agent_latencies_s": self.agent_latencies,
            "agent_success": self.agent_success,
            "token_usage": self.token_usage,
            "article_counts": self.article_counts,
            "warnings": self.warnings,
        }

    def save(self, directory: str = "data/logs") -> str:
        Path(directory).mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^a-z0-9_-]", "_", self.company_name.lower())
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = Path(directory) / f"{safe_name}_{ts}.json"
        with open(path, "w") as fh:
            json.dump(self.to_dict(), fh, indent=2)
        logger.info(f"Observability log saved → {path}")
        return str(path)


# ---------------------------------------------------------------------------
# Utility — date parsing
# ---------------------------------------------------------------------------

def parse_time_period(time_period: str, anchor: date | None = None) -> tuple[date, date]:
    """
    Convert a natural-language or ISO time period string into (start_date, end_date).

    Supported formats:
      "last N days / weeks / months / years"
      "past N days / weeks / months / years"
      "YYYY-MM-DD to YYYY-MM-DD"
      "Q1 YYYY" / "Q2 YYYY" etc.

    Raises ValueError on unrecognised input.
    """
    anchor = anchor or date.today()
    period = time_period.strip().lower()

    # --- ISO range: "2024-01-01 to 2024-03-31" ---
    iso_range = re.match(
        r"(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})", period
    )
    if iso_range:
        start = date.fromisoformat(iso_range.group(1))
        end = date.fromisoformat(iso_range.group(2))
        if start > end:
            raise ValueError(f"start_date {start} is after end_date {end}")
        return start, end

    # --- "last/past N unit" ---
    relative = re.match(r"(?:last|past)\s+(\d+)\s+(day|week|month|year)s?", period)
    if relative:
        n = int(relative.group(1))
        unit = relative.group(2)
        if unit == "day":
            start = anchor - timedelta(days=n)
        elif unit == "week":
            start = anchor - timedelta(weeks=n)
        elif unit == "month":
            # Approximate: 30 days per month
            start = anchor - timedelta(days=30 * n)
        elif unit == "year":
            start = anchor - timedelta(days=365 * n)
        else:
            raise ValueError(f"Unknown unit: {unit}")
        return start, anchor

    # --- Quarter: "Q1 2024" ---
    quarter_match = re.match(r"q([1-4])\s+(\d{4})", period)
    if quarter_match:
        q = int(quarter_match.group(1))
        yr = int(quarter_match.group(2))
        quarter_starts = {1: (1, 1), 2: (4, 1), 3: (7, 1), 4: (10, 1)}
        quarter_ends = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
        sm, sd = quarter_starts[q]
        em, ed = quarter_ends[q]
        return date(yr, sm, sd), date(yr, em, ed)

    raise ValueError(
        f"Unrecognised time period format: '{time_period}'. "
        "Use 'last N months', 'YYYY-MM-DD to YYYY-MM-DD', or 'QN YYYY'."
    )


# ---------------------------------------------------------------------------
# Utility — retry wrapper
# ---------------------------------------------------------------------------

def with_retry(fn, *, max_attempts: int = 3, base_delay: float = 2.0):
    """
    Call fn() up to max_attempts times, with exponential back-off on failure.
    Re-raises the last exception if all attempts are exhausted.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt < max_attempts:
                wait = base_delay * (2 ** (attempt - 1))
                logger.warning(
                    f"Attempt {attempt}/{max_attempts} failed: {exc}. "
                    f"Retrying in {wait:.0f}s…"
                )
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

MIN_ARTICLES_FOR_FULL_REPORT = 3  # below this → degraded mode
OUTPUT_DIR = "data"


class NewsAggregatorWorkflow(Workflow):
    """
    Linear four-agent news aggregation pipeline.

    Agents are defined as class attributes so Agno automatically links their
    session_id to the workflow session_id.
    """

    description: str = "Multi-agent news aggregator: Planner → Browser → Extraction → Compiler"

    # Agents — instantiated once at class definition time.
    # Each agent is stateless (no DB / memory), so sharing instances is safe.
    planner_agent: Agent = None   # type: ignore[assignment]
    browser_agent: Agent = None   # type: ignore[assignment]
    extraction_agent: Agent = None  # type: ignore[assignment]
    compiler_agent: Agent = None  # type: ignore[assignment]

    def model_post_init(self, __context) -> None:  # noqa: D401
        """Lazily initialise agents after Pydantic construction."""
        self.planner_agent = create_planner_agent()
        self.browser_agent = create_browser_agent()
        self.extraction_agent = create_extraction_agent()
        self.compiler_agent = create_compiler_agent()

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self, company_name: str, time_period: str) -> Iterator[RunResponse]:  # type: ignore[override]
        """
        Execute the pipeline and yield progress RunResponse objects.
        The final RunResponse carries the FinalReport as its content.
        """
        company_name = company_name.strip()
        tracker = ObservabilityTracker(
            company_name=company_name, run_id=self.run_id or "local"
        )
        warnings: list[str] = []

        # ----------------------------------------------------------------
        # Input validation  [HARD failure mode 1]
        # ----------------------------------------------------------------
        if not company_name:
            yield RunResponse(
                run_id=self.run_id,
                content="ERROR: company_name must not be empty.",
            )
            return

        try:
            start_date, end_date = parse_time_period(time_period)
        except ValueError as exc:
            yield RunResponse(
                run_id=self.run_id,
                content=f"ERROR: Invalid time period — {exc}",
            )
            return

        workflow_input = WorkflowInput(
            company_name=company_name,
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
        )
        self.session_state["workflow_input"] = workflow_input.model_dump(mode="json")
        logger.info(
            f"Workflow started: company={company_name}, "
            f"window={start_date} → {end_date}"
        )
        yield RunResponse(
            run_id=self.run_id,
            content=(
                f"Starting news aggregation for **{company_name}** "
                f"({start_date} to {end_date})…"
            ),
        )

        # ----------------------------------------------------------------
        # Stage 1: Planner  [HARD failure mode 2]
        # ----------------------------------------------------------------
        yield RunResponse(run_id=self.run_id, content="[1/4] Running Planner Agent…")
        tracker.start_agent("planner")
        try:
            plan_response = with_retry(
                lambda: self.planner_agent.run(
                    (
                        f"Create a research plan for '{company_name}' "
                        f"covering {start_date} to {end_date}."
                    ),
                    stream=False,
                )
            )
            research_plan: ResearchPlan = plan_response.content
            if not isinstance(research_plan, ResearchPlan):
                raise TypeError(
                    f"Planner returned unexpected type: {type(research_plan)}"
                )
            tokens = self._extract_tokens(plan_response)
            tracker.end_agent("planner", success=True, tokens=tokens)
        except Exception as exc:
            tracker.end_agent("planner", success=False)
            tracker.save()
            yield RunResponse(
                run_id=self.run_id,
                content=f"FATAL: Planner Agent failed after retries — {exc}",
            )
            return

        self.session_state["research_plan"] = research_plan.model_dump(mode="json")
        n_queries = len(research_plan.queries)
        yield RunResponse(
            run_id=self.run_id,
            content=f"  ✓ Plan created: {n_queries} queries for '{research_plan.company_name}'",
        )

        # ----------------------------------------------------------------
        # Stage 2: Browser  [HARD failure mode 2 continued]
        # ----------------------------------------------------------------
        yield RunResponse(run_id=self.run_id, content="[2/4] Running Browser Agent…")
        tracker.start_agent("browser")
        browser_prompt = (
            "Execute the following research plan and return all collected articles.\n\n"
            + json.dumps(research_plan.model_dump(mode="json"), indent=2)
        )
        try:
            browser_response = with_retry(
                lambda: self.browser_agent.run(browser_prompt, stream=False)
            )
            raw_list: RawArticleList = browser_response.content
            if not isinstance(raw_list, RawArticleList):
                raise TypeError(
                    f"Browser Agent returned unexpected type: {type(raw_list)}"
                )
            tokens = self._extract_tokens(browser_response)
            tracker.end_agent("browser", success=True, tokens=tokens)
        except Exception as exc:
            tracker.end_agent("browser", success=False)
            tracker.save()
            yield RunResponse(
                run_id=self.run_id,
                content=f"FATAL: Browser Agent failed after retries — {exc}",
            )
            return

        n_raw = len(raw_list.articles)
        tracker.set_article_count("raw", n_raw)
        self.session_state["raw_articles"] = raw_list.model_dump(mode="json")
        yield RunResponse(
            run_id=self.run_id,
            content=f"  ✓ Collected {n_raw} articles ({raw_list.total_queries_executed} queries executed)",
        )

        if n_raw == 0:
            tracker.save()
            yield RunResponse(
                run_id=self.run_id,
                content=(
                    "FATAL: Browser Agent returned 0 articles. "
                    "Check query terms or try a broader time period."
                ),
            )
            return

        # ----------------------------------------------------------------
        # Stage 3: Extraction  [HARD failure, SOFT low-article mode 3]
        # ----------------------------------------------------------------
        yield RunResponse(run_id=self.run_id, content="[3/4] Running Extraction Agent…")
        tracker.start_agent("extraction")
        anchor_date = date.today()
        extraction_prompt = json.dumps(
            {
                "start_date": str(start_date),
                "end_date": str(end_date),
                "anchor_date": str(anchor_date),
                "articles": raw_list.model_dump(mode="json")["articles"],
            },
            indent=2,
        )
        try:
            extraction_response = with_retry(
                lambda: self.extraction_agent.run(extraction_prompt, stream=False)
            )
            enriched_list: EnrichedArticleList = extraction_response.content
            if not isinstance(enriched_list, EnrichedArticleList):
                raise TypeError(
                    f"Extraction Agent returned unexpected type: {type(enriched_list)}"
                )
            tokens = self._extract_tokens(extraction_response)
            tracker.end_agent("extraction", success=True, tokens=tokens)
        except Exception as exc:
            tracker.end_agent("extraction", success=False)
            tracker.save()
            yield RunResponse(
                run_id=self.run_id,
                content=f"FATAL: Extraction Agent failed after retries — {exc}",
            )
            return

        n_enriched = len(enriched_list.articles)
        tracker.set_article_count("enriched", n_enriched)
        self.session_state["enriched_articles"] = enriched_list.model_dump(mode="json")

        # Soft failure: low article count  [failure mode 3]
        if n_enriched < MIN_ARTICLES_FOR_FULL_REPORT:
            msg = (
                f"Only {n_enriched} article(s) passed the date filter "
                f"(minimum recommended: {MIN_ARTICLES_FOR_FULL_REPORT}). "
                "Report will be produced but may lack depth."
            )
            tracker.add_warning(msg)
            warnings.append(msg)

        yield RunResponse(
            run_id=self.run_id,
            content=(
                f"  ✓ {n_enriched} articles enriched "
                f"({enriched_list.discarded_count} discarded outside window)"
            ),
        )

        # ----------------------------------------------------------------
        # Stage 4: Compiler  [HARD failure mode 2 final]
        # ----------------------------------------------------------------
        yield RunResponse(run_id=self.run_id, content="[4/4] Running Compiler Agent…")
        tracker.start_agent("compiler")
        period_str = f"{start_date.strftime('%b %-d, %Y')} – {end_date.strftime('%b %-d, %Y')}"
        compiler_prompt = json.dumps(
            {
                "company_name": company_name,
                "analysis_period": period_str,
                "generated_at": datetime.utcnow().isoformat() + "Z",
                "articles": enriched_list.model_dump(mode="json")["articles"],
            },
            indent=2,
        )
        try:
            compiler_response = with_retry(
                lambda: self.compiler_agent.run(compiler_prompt, stream=False)
            )
            final_report: FinalReport = compiler_response.content
            if not isinstance(final_report, FinalReport):
                raise TypeError(
                    f"Compiler Agent returned unexpected type: {type(final_report)}"
                )
            tokens = self._extract_tokens(compiler_response)
            tracker.end_agent("compiler", success=True, tokens=tokens)
        except Exception as exc:
            tracker.end_agent("compiler", success=False)
            tracker.save()
            yield RunResponse(
                run_id=self.run_id,
                content=f"FATAL: Compiler Agent failed after retries — {exc}",
            )
            return

        # Attach any pipeline-level warnings and status
        final_report.warnings.extend(warnings)
        if warnings:
            final_report.status = WorkflowStatus.DEGRADED

        # ----------------------------------------------------------------
        # Save outputs
        # ----------------------------------------------------------------
        self.session_state["final_report"] = final_report.model_dump(mode="json")
        obs_path = tracker.save()

        report_path = self._save_report(final_report, company_name)
        yield RunResponse(
            run_id=self.run_id,
            content=(
                f"  ✓ Report compiled\n"
                f"  📄 Report saved → {report_path}\n"
                f"  📊 Observability log → {obs_path}"
            ),
        )

        # Final RunResponse carries the full structured report as content
        yield RunResponse(run_id=self.run_id, content=final_report)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_tokens(response) -> dict:
        """Best-effort extraction of token counts from an agent RunResponse."""
        try:
            metrics = getattr(response, "metrics", None) or {}
            return {
                "input_tokens": metrics.get("input_tokens", 0),
                "output_tokens": metrics.get("output_tokens", 0),
            }
        except Exception:
            return {}

    @staticmethod
    def _save_report(report: FinalReport, company_name: str) -> str:
        """Persist the final report as a JSON file under data/."""
        Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^a-z0-9_-]", "_", company_name.lower())
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = Path(OUTPUT_DIR) / f"{safe}_report_{ts}.json"
        with open(path, "w") as fh:
            json.dump(report.model_dump(mode="json"), fh, indent=2)
        return str(path)
