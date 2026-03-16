"""
news_aggregator.py — NewsAggregatorWorkflow

Orchestrates the four-agent pipeline:
  Planner → Browser → Extraction → Compiler

Every agent handoff uses a typed Pydantic model — no raw strings or dicts
are passed between stages. The workflow yields typed WorkflowEvent objects:
  ProgressEvent — pipeline progress messages
  ErrorEvent    — hard (fatal) or soft (degraded) failures
  ReportEvent   — final structured output with file paths

Failure modes:
  1. HARD — unparseable message / empty company  → ErrorEvent(fatal=True), abort
  2. HARD — any agent fails after 3 retries      → ErrorEvent(fatal=True), abort
  3. HARD — browser returns 0 articles           → ErrorEvent(fatal=True), abort
  4. SOFT — < 3 articles after extraction        → ErrorEvent(fatal=False), continue degraded
  5. SOFT — individual article fetch failure     → content_status=unavailable, keep article
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from agno.agent import Agent
from agno.workflow import Workflow

from app.agents.browser import create_browser_agent
from app.agents.compiler import create_compiler_agent
from app.agents.extraction import create_extraction_agent
from app.agents.planner import create_planner_agent
from app.models.state import (
    BrowserInput,
    CompilerInput,
    EnrichedArticleList,
    ErrorEvent,
    ExtractionInput,
    FinalReport,
    PlannerInput,
    ProgressEvent,
    RawArticleList,
    ReportEvent,
    ResearchPlan,
    WorkflowEvent,
    WorkflowInput,
    WorkflowStatus,
)

logger = logging.getLogger(__name__)

MIN_ARTICLES_FOR_FULL_REPORT = 3
OUTPUT_DIR = "data"


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
        logger.info(f"[{name}] {'OK' if success else 'FAILED'} in {elapsed:.1f}s")

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
        safe = re.sub(r"[^a-z0-9_-]", "_", self.company_name.lower())
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = Path(directory) / f"{safe}_{ts}.json"
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

    iso_range = re.match(r"(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})", period)
    if iso_range:
        start = date.fromisoformat(iso_range.group(1))
        end   = date.fromisoformat(iso_range.group(2))
        if start > end:
            raise ValueError(f"start_date {start} is after end_date {end}")
        return start, end

    relative = re.match(r"(?:last|past)\s+(\d+)\s+(day|week|month|year)s?", period)
    if relative:
        n, unit = int(relative.group(1)), relative.group(2)
        delta = {
            "day":   timedelta(days=n),
            "week":  timedelta(weeks=n),
            "month": timedelta(days=30 * n),
            "year":  timedelta(days=365 * n),
        }.get(unit)
        if delta is None:
            raise ValueError(f"Unknown unit: {unit}")
        return anchor - delta, anchor

    quarter_match = re.match(r"q([1-4])\s+(\d{4})", period)
    if quarter_match:
        q, yr = int(quarter_match.group(1)), int(quarter_match.group(2))
        starts = {1: (1, 1),  2: (4, 1),  3: (7, 1),  4: (10, 1)}
        ends   = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
        return date(yr, *starts[q]), date(yr, *ends[q])

    raise ValueError(
        f"Unrecognised time period format: '{time_period}'. "
        "Use 'last N months', 'YYYY-MM-DD to YYYY-MM-DD', or 'QN YYYY'."
    )


# ---------------------------------------------------------------------------
# Utility — retry wrapper
# ---------------------------------------------------------------------------

def with_retry(fn, *, max_attempts: int = 3, base_delay: float = 2.0):
    """Exponential back-off retry. Re-raises after max_attempts."""
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt < max_attempts:
                wait = base_delay * (2 ** (attempt - 1))
                logger.warning(f"Attempt {attempt}/{max_attempts} failed: {exc}. Retrying in {wait:.0f}s…")
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

class NewsAggregatorWorkflow(Workflow):
    """
    Linear four-agent news aggregation pipeline.

    Every inter-agent handoff uses a typed Pydantic model.
    run() yields WorkflowEvent objects (ProgressEvent | ErrorEvent | ReportEvent).
    """

    description: str = (
        "Multi-agent news aggregator: Planner → Browser → Extraction → Compiler. "
        "Send a message like 'Analyze Apple for the last 3 months'."
    )

    planner_agent:    Agent = None  # type: ignore[assignment]
    browser_agent:    Agent = None  # type: ignore[assignment]
    extraction_agent: Agent = None  # type: ignore[assignment]
    compiler_agent:   Agent = None  # type: ignore[assignment]

    def _ensure_agents(self) -> None:
        """Lazily initialise agents on first run() call.

        We cannot rely on model_post_init because Agno's Workflow base class
        does not guarantee it is called in all versions. Lazy init here is
        version-agnostic and avoids import-time side-effects.
        """
        if self.planner_agent is None:
            self.planner_agent = create_planner_agent()
        if self.browser_agent is None:
            self.browser_agent = create_browser_agent()
        if self.extraction_agent is None:
            self.extraction_agent = create_extraction_agent()
        if self.compiler_agent is None:
            self.compiler_agent = create_compiler_agent()

    # ------------------------------------------------------------------
    # run()
    # ------------------------------------------------------------------

    def run(self, message: str) -> Iterator[WorkflowEvent]:  # type: ignore[override]
        """
        Execute the pipeline for the given chat message.
        Yields WorkflowEvent objects; the final one is always a ReportEvent
        (on success) or a fatal ErrorEvent (on hard failure).
        """
        run_id = getattr(self, "run_id", None) or "local"
        self._ensure_agents()

        # ── Parse message ──────────────────────────────────────────────
        try:
            company_name, time_period = self._parse_user_message(message)
        except ValueError as exc:
            yield ErrorEvent(
                stage=0,
                message=(
                    f"Could not understand your request: {exc}\n\n"
                    "Use a format like:\n"
                    "  • Analyze Apple for the last 3 months\n"
                    "  • Nvidia news, Q1 2024\n"
                    "  • TSMC | 2024-01-01 to 2024-06-30"
                ),
                fatal=True,
            )
            return

        company_name = company_name.strip()
        if not company_name:
            yield ErrorEvent(stage=0, message="Company name must not be empty.", fatal=True)
            return

        # ── Resolve dates ──────────────────────────────────────────────
        try:
            start_date, end_date = parse_time_period(time_period)
        except ValueError as exc:
            yield ErrorEvent(stage=0, message=f"Invalid time period — {exc}", fatal=True)
            return

        tracker  = ObservabilityTracker(company_name=company_name, run_id=run_id)
        warnings: list[str] = []

        # session_state is None when no DB is attached (e.g. in tests)
        _state: dict = self.session_state if self.session_state is not None else {}

        workflow_input = WorkflowInput(
            company_name=company_name,
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
        )
        _state["workflow_input"] = workflow_input.model_dump(mode="json")
        logger.info(f"Workflow started: company={company_name}, window={start_date}→{end_date}")

        yield ProgressEvent(
            stage=0,
            message=f"Starting news aggregation for **{company_name}** ({start_date} to {end_date})…",
        )

        # ── Stage 1: Planner ───────────────────────────────────────────
        yield ProgressEvent(stage=1, message="[1/4] Running Planner Agent…")
        tracker.start_agent("planner")

        planner_input = PlannerInput(
            company_name=company_name,
            start_date=start_date,
            end_date=end_date,
        )

        try:
            plan_response = with_retry(
                lambda: self.planner_agent.run(
                    planner_input.model_dump_json(indent=2),
                    stream=False,
                )
            )
            research_plan: ResearchPlan = plan_response.content
            if not isinstance(research_plan, ResearchPlan):
                raise TypeError(f"Planner returned unexpected type: {type(research_plan)}")
            tracker.end_agent("planner", success=True, tokens=self._extract_tokens(plan_response))
        except Exception as exc:
            tracker.end_agent("planner", success=False)
            tracker.save()
            yield ErrorEvent(stage=1, message=f"Planner Agent failed after retries — {exc}", fatal=True)
            return

        _state["research_plan"] = research_plan.model_dump(mode="json")
        yield ProgressEvent(
            stage=1,
            message=f"  ✓ Plan created: {len(research_plan.queries)} queries for '{research_plan.company_name}'",
        )

        # ── Stage 2: Browser ───────────────────────────────────────────
        yield ProgressEvent(stage=2, message="[2/4] Running Browser Agent…")
        tracker.start_agent("browser")

        browser_input = BrowserInput(plan=research_plan)

        try:
            browser_response = with_retry(
                lambda: self.browser_agent.run(
                    browser_input.model_dump_json(indent=2),
                    stream=False,
                )
            )
            raw_list: RawArticleList = browser_response.content
            if not isinstance(raw_list, RawArticleList):
                raise TypeError(f"Browser Agent returned unexpected type: {type(raw_list)}")
            tracker.end_agent("browser", success=True, tokens=self._extract_tokens(browser_response))
        except Exception as exc:
            tracker.end_agent("browser", success=False)
            tracker.save()
            yield ErrorEvent(stage=2, message=f"Browser Agent failed after retries — {exc}", fatal=True)
            return

        n_raw = len(raw_list.articles)
        tracker.set_article_count("raw", n_raw)
        _state["raw_articles"] = raw_list.model_dump(mode="json")
        yield ProgressEvent(
            stage=2,
            message=f"  ✓ Collected {n_raw} articles ({raw_list.total_queries_executed} queries executed)",
        )

        if n_raw == 0:
            tracker.save()
            yield ErrorEvent(
                stage=2,
                message="Browser Agent returned 0 articles. Try a broader time period or different company name.",
                fatal=True,
            )
            return

        # ── Stage 3: Extraction ────────────────────────────────────────
        yield ProgressEvent(stage=3, message="[3/4] Running Extraction Agent…")
        tracker.start_agent("extraction")

        extraction_input = ExtractionInput(
            start_date=start_date,
            end_date=end_date,
            anchor_date=date.today(),
            articles=raw_list.articles,
        )

        try:
            extraction_response = with_retry(
                lambda: self.extraction_agent.run(
                    extraction_input.model_dump_json(indent=2),
                    stream=False,
                )
            )
            enriched_list: EnrichedArticleList = extraction_response.content
            if not isinstance(enriched_list, EnrichedArticleList):
                raise TypeError(f"Extraction Agent returned unexpected type: {type(enriched_list)}")
            tracker.end_agent("extraction", success=True, tokens=self._extract_tokens(extraction_response))
        except Exception as exc:
            tracker.end_agent("extraction", success=False)
            tracker.save()
            yield ErrorEvent(stage=3, message=f"Extraction Agent failed after retries — {exc}", fatal=True)
            return

        n_enriched = len(enriched_list.articles)
        tracker.set_article_count("enriched", n_enriched)
        _state["enriched_articles"] = enriched_list.model_dump(mode="json")

        # Soft failure — low article count
        if n_enriched < MIN_ARTICLES_FOR_FULL_REPORT:
            msg = (
                f"Only {n_enriched} article(s) passed the date filter "
                f"(minimum recommended: {MIN_ARTICLES_FOR_FULL_REPORT}). "
                "Report will be produced but may lack depth."
            )
            tracker.add_warning(msg)
            warnings.append(msg)
            yield ErrorEvent(stage=3, message=msg, fatal=False)

        yield ProgressEvent(
            stage=3,
            message=(
                f"  ✓ {n_enriched} articles enriched "
                f"({enriched_list.discarded_count} discarded outside window)"
            ),
        )

        # ── Stage 4: Compiler ──────────────────────────────────────────
        yield ProgressEvent(stage=4, message="[4/4] Running Compiler Agent…")
        tracker.start_agent("compiler")

        period_str = (
            f"{start_date.strftime('%b %d, %Y').replace(' 0', ' ')} – "
            f"{end_date.strftime('%b %d, %Y').replace(' 0', ' ')}"
        )
        compiler_input = CompilerInput(
            company_name=company_name,
            analysis_period=period_str,
            generated_at=datetime.utcnow().isoformat() + "Z",
            articles=enriched_list.articles,
        )

        try:
            compiler_response = with_retry(
                lambda: self.compiler_agent.run(
                    compiler_input.model_dump_json(indent=2),
                    stream=False,
                )
            )
            final_report: FinalReport = compiler_response.content
            if not isinstance(final_report, FinalReport):
                raise TypeError(f"Compiler Agent returned unexpected type: {type(final_report)}")
            tracker.end_agent("compiler", success=True, tokens=self._extract_tokens(compiler_response))
        except Exception as exc:
            tracker.end_agent("compiler", success=False)
            tracker.save()
            yield ErrorEvent(stage=4, message=f"Compiler Agent failed after retries — {exc}", fatal=True)
            return

        # Attach pipeline-level warnings and status
        final_report.warnings.extend(warnings)
        if warnings:
            final_report.status = WorkflowStatus.DEGRADED

        _state["final_report"] = final_report.model_dump(mode="json")
        obs_path    = tracker.save()
        report_path = self._save_report(final_report, company_name)

        yield ProgressEvent(
            stage=4,
            message=(
                f"  ✓ Report compiled\n"
                f"  📄 Report saved → {report_path}\n"
                f"  📊 Observability log → {obs_path}"
            ),
        )

        # Final yield — typed report event
        yield ReportEvent(
            report=final_report,
            report_path=report_path,
            obs_path=obs_path,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_user_message(message: str) -> tuple[str, str]:
        """
        Extract (company_name, time_period) from a natural-language chat message.

        Supported patterns:
          "Analyze Apple for the last 3 months"
          "Apple news, last 3 months"
          "TSMC | 2024-01-01 to 2024-06-30"
          "Nvidia Q1 2024"
        """
        msg = message.strip()
        TIME_PATTERNS = [
            r"\d{4}-\d{2}-\d{2}\s+to\s+\d{4}-\d{2}-\d{2}",
            r"(?:last|past)\s+\d+\s+(?:day|week|month|year)s?",
            r"[Qq][1-4]\s+\d{4}",
        ]
        for pat in TIME_PATTERNS:
            m = re.search(pat, msg, re.IGNORECASE)
            if m:
                time_period  = m.group(0).strip()
                company_raw  = msg[: m.start()].strip(" ,|–-")
                # Strip leading action verbs
                company_raw = re.sub(
                    r"^(?:analyze|analyse|research|get|show|find|"
                    r"news\s+for|news\s+about|about)\s+",
                    "",
                    company_raw,
                    flags=re.IGNORECASE,
                )
                # Strip trailing connectors like "for the", "for", ","
                company_name = re.sub(
                    r"\s+(?:for\s+the|for)\s*$",
                    "",
                    company_raw,
                    flags=re.IGNORECASE,
                ).strip(" ,|")
                if not company_name:
                    raise ValueError("Could not extract a company name from the message.")
                return company_name, time_period

        raise ValueError(
            "No recognisable time period found. "
            "Include something like 'last 3 months', 'Q1 2024', "
            "or '2024-01-01 to 2024-06-30'."
        )

    @staticmethod
    def _extract_tokens(response: Any) -> dict:
        """Best-effort token count extraction from an agent response."""
        try:
            metrics = getattr(response, "metrics", None) or {}
            return {
                "input_tokens":  metrics.get("input_tokens", 0),
                "output_tokens": metrics.get("output_tokens", 0),
            }
        except Exception:
            return {}

    @staticmethod
    def _save_report(report: FinalReport, company_name: str) -> str:
        """Persist the FinalReport as JSON under data/."""
        Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^a-z0-9_-]", "_", company_name.lower())
        ts   = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = Path(OUTPUT_DIR) / f"{safe}_report_{ts}.json"
        with open(path, "w") as fh:
            json.dump(report.model_dump(mode="json"), fh, indent=2)
        return str(path)
