"""
state.py — Shared Pydantic models for the News Aggregator workflow.

Every handoff boundary in the pipeline is a typed Pydantic model:

  WorkflowInput                    — resolved user input (company + dates)
  ──────────────────────────────────────────────────────────────────────
  PlannerInput      → ResearchPlan          (Stage 1: Planner)
  BrowserInput      → RawArticleList        (Stage 2: Browser)
  ExtractionInput   → EnrichedArticleList   (Stage 3: Extraction)
  CompilerInput     → FinalReport           (Stage 4: Compiler)
  ──────────────────────────────────────────────────────────────────────
  WorkflowEvent (discriminated union)       — typed items yielded by run()
    ProgressEvent   — progress message
    ErrorEvent      — hard or soft failure notice
    ReportEvent     — final report + file paths
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class SentimentLabel(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL  = "neutral"


class EventType(str, Enum):
    EARNINGS        = "earnings"
    LEADERSHIP      = "leadership"
    LEGAL_REGULATORY = "legal_regulatory"
    PRODUCT         = "product"
    PARTNERSHIP     = "partnership"
    OTHER           = "other"


class ContentStatus(str, Enum):
    AVAILABLE   = "available"
    UNAVAILABLE = "unavailable"


class WorkflowStatus(str, Enum):
    SUCCESS  = "success"
    DEGRADED = "degraded"
    ERROR    = "error"


# ---------------------------------------------------------------------------
# Pipeline Stage 0 — Resolved workflow input
# ---------------------------------------------------------------------------

class WorkflowInput(BaseModel):
    """Validated, anchor-resolved input that is stored in session_state."""
    company_name: str  = Field(..., description="Company name as provided by the user")
    time_period:  str  = Field(..., description="Raw time period string from user input")
    start_date:   date = Field(..., description="Resolved start date")
    end_date:     date = Field(..., description="Resolved end date")


# ---------------------------------------------------------------------------
# Stage 1 — Planner: input + output
# ---------------------------------------------------------------------------

class PlannerInput(BaseModel):
    """Typed prompt handed to the Planner Agent."""
    company_name: str  = Field(..., description="Company name to research")
    start_date:   date = Field(..., description="Coverage window start")
    end_date:     date = Field(..., description="Coverage window end")


class SearchQuery(BaseModel):
    query:    str = Field(..., description="Search query string optimised for news search")
    category: str = Field(
        ...,
        description=(
            "Category this query targets. One of: general, earnings, leadership, "
            "legal_regulatory, product, partnership"
        ),
    )


class ResearchPlan(BaseModel):
    """Structured research plan produced by the Planner Agent."""
    company_name:          str               = Field(..., description="Canonical company name")
    ticker:                Optional[str]     = Field(None, description="Stock ticker if applicable")
    aliases:               List[str]         = Field(default_factory=list)
    queries:               List[SearchQuery] = Field(..., description="Ordered list of search queries")
    start_date:            date              = Field(..., description="Start of the coverage window")
    end_date:              date              = Field(..., description="End of the coverage window")
    max_articles_per_query: int              = Field(default=4)


# ---------------------------------------------------------------------------
# Stage 2 — Browser: input + output
# ---------------------------------------------------------------------------

class BrowserInput(BaseModel):
    """Typed prompt handed to the Browser Agent."""
    plan: ResearchPlan = Field(..., description="The research plan to execute")


class RawArticle(BaseModel):
    """One article as collected by the Browser Agent — no enrichment yet."""
    headline:       str                      = Field(..., description="Article headline or title")
    publisher:      str                      = Field(..., description="Publishing outlet or domain")
    timestamp_raw:  str                      = Field(..., description="Publication time as found on the page")
    url:            str                      = Field(..., description="Canonical article URL")
    body_text:      Optional[str]            = Field(None, description="Body text; null if unavailable")
    content_status: ContentStatus            = Field(default=ContentStatus.AVAILABLE)
    query_source:   str                      = Field(..., description="Search query that surfaced this article")


class RawArticleList(BaseModel):
    """Output of the Browser Agent."""
    articles:               List[RawArticle] = Field(default_factory=list)
    total_queries_executed: int              = Field(default=0)


# ---------------------------------------------------------------------------
# Stage 3 — Extraction: input + output
# ---------------------------------------------------------------------------

class ExtractionInput(BaseModel):
    """Typed prompt handed to the Extraction Agent."""
    start_date:   date            = Field(..., description="Coverage window start (for date filtering)")
    end_date:     date            = Field(..., description="Coverage window end (for date filtering)")
    anchor_date:  date            = Field(..., description="Today's date — used to resolve relative timestamps")
    articles:     List[RawArticle] = Field(..., description="Raw articles from the Browser Agent")


class EnrichedArticle(BaseModel):
    """RawArticle fields plus date normalisation, sentiment, and event tags."""
    # Preserved from RawArticle
    headline:       str
    publisher:      str
    timestamp_raw:  str
    url:            str
    body_text:      Optional[str]
    content_status: ContentStatus
    query_source:   str

    # Enriched fields
    normalized_date:       date           = Field(..., description="Absolute publication date")
    sentiment:             SentimentLabel
    sentiment_confidence:  float          = Field(..., ge=0.0, le=1.0)
    sentiment_rationale:   str            = Field(..., description="Evidence-based explanation of sentiment label")
    event_types:           List[EventType] = Field(default_factory=list)


class EnrichedArticleList(BaseModel):
    """Output of the Extraction Agent."""
    articles:        List[EnrichedArticle] = Field(default_factory=list)
    discarded_count: int                   = Field(default=0, description="Articles dropped outside date window")


# ---------------------------------------------------------------------------
# Stage 4 — Compiler: input + output
# ---------------------------------------------------------------------------

class CompilerInput(BaseModel):
    """Typed prompt handed to the Compiler Agent."""
    company_name:    str                    = Field(..., description="Subject company name")
    analysis_period: str                    = Field(..., description="Human-readable date range string")
    generated_at:    str                    = Field(..., description="ISO-8601 UTC generation timestamp")
    articles:        List[EnrichedArticle]  = Field(..., description="Fully enriched articles")


class AggregateStats(BaseModel):
    total_articles:        int
    sentiment_breakdown:   Dict[str, int] = Field(..., description="{'positive': N, 'negative': N, 'neutral': N}")
    event_type_breakdown:  Dict[str, int] = Field(..., description="Count per EventType value")
    content_coverage_pct:  float          = Field(..., description="% of articles with full body text")
    date_range_actual:     str            = Field(..., description="'YYYY-MM-DD to YYYY-MM-DD' actual span")


class FinalReport(BaseModel):
    """The single-page analytical output delivered to the user."""
    company_name:      str
    analysis_period:   str  = Field(..., description="Human-readable date range string")
    generated_at:      str  = Field(..., description="ISO-8601 UTC timestamp of generation")
    aggregate_stats:   AggregateStats
    executive_summary: str  = Field(..., description="2–4 sentence overview of the most important developments")
    key_events:        str  = Field(..., description="Narrative synthesis of significant events from body text")
    sentiment_analysis: str = Field(..., description="Sentiment trend with content-level evidence")
    notable_headlines: str  = Field(..., description="3–5 most impactful articles with body-text distillations")
    warnings:          List[str]       = Field(default_factory=list)
    status:            WorkflowStatus  = WorkflowStatus.SUCCESS


# ---------------------------------------------------------------------------
# Workflow events — typed items yielded by NewsAggregatorWorkflow.run()
# ---------------------------------------------------------------------------

class ProgressEvent(BaseModel):
    """A progress message yielded during pipeline execution."""
    kind:    Literal["progress"] = "progress"
    stage:   int                 = Field(..., description="Pipeline stage index (0=init, 1–4=agents)")
    message: str                 = Field(..., description="Human-readable progress description")


class ErrorEvent(BaseModel):
    """A failure notice — fatal aborts the pipeline, non-fatal continues in degraded mode."""
    kind:    Literal["error"] = "error"
    stage:   int              = Field(..., description="Pipeline stage where the error occurred")
    message: str
    fatal:   bool             = Field(default=True, description="True = pipeline aborted")


class ReportEvent(BaseModel):
    """The final pipeline output — carries the report and file paths."""
    kind:        Literal["report"] = "report"
    report:      FinalReport
    report_path: str = Field(..., description="Path to the saved JSON report")
    obs_path:    str = Field(..., description="Path to the observability log")


# Discriminated union — callers can match on event.kind
WorkflowEvent = Union[ProgressEvent, ErrorEvent, ReportEvent]
