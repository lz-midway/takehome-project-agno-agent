"""
state.py — Shared Pydantic models for the News Aggregator workflow.

These models define the typed contract at every handoff boundary:
  WorkflowInput → ResearchPlan → RawArticleList → EnrichedArticleList → FinalReport

All agent output_schema values live here so each agent can import exactly what it needs.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class SentimentLabel(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class EventType(str, Enum):
    EARNINGS = "earnings"
    LEADERSHIP = "leadership"
    LEGAL_REGULATORY = "legal_regulatory"
    PRODUCT = "product"
    PARTNERSHIP = "partnership"
    OTHER = "other"


class ContentStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class WorkflowStatus(str, Enum):
    SUCCESS = "success"
    DEGRADED = "degraded"   # completed but with warnings (e.g. low article count)
    ERROR = "error"


# ---------------------------------------------------------------------------
# Pipeline Stage 0 — Workflow entry
# ---------------------------------------------------------------------------

class WorkflowInput(BaseModel):
    """Validated, resolved input that anchors the whole pipeline."""
    company_name: str = Field(..., description="Company name as provided by the user")
    time_period: str = Field(..., description="Raw time period string from user input")
    start_date: date = Field(..., description="Resolved start date")
    end_date: date = Field(..., description="Resolved end date")


# ---------------------------------------------------------------------------
# Pipeline Stage 1 — Planner output
# ---------------------------------------------------------------------------

class SearchQuery(BaseModel):
    query: str = Field(..., description="Search query string optimised for news search")
    category: str = Field(
        ...,
        description=(
            "Category this query targets. One of: general, earnings, leadership, "
            "legal_regulatory, product, partnership"
        ),
    )


class ResearchPlan(BaseModel):
    """Structured research plan produced by the Planner Agent."""
    company_name: str = Field(..., description="Canonical company name")
    ticker: Optional[str] = Field(None, description="Stock ticker symbol if applicable")
    aliases: List[str] = Field(
        default_factory=list,
        description="Alternative names / abbreviations for the company",
    )
    queries: List[SearchQuery] = Field(
        ..., description="Ordered list of search queries to execute"
    )
    start_date: date = Field(..., description="Start of the coverage window")
    end_date: date = Field(..., description="End of the coverage window")
    max_articles_per_query: int = Field(
        default=4,
        description="Maximum articles to collect per individual query",
    )


# ---------------------------------------------------------------------------
# Pipeline Stage 2 — Browser Agent output
# ---------------------------------------------------------------------------

class RawArticle(BaseModel):
    """One article as collected by the Browser Agent — no enrichment yet."""
    headline: str = Field(..., description="Article headline or title")
    publisher: str = Field(..., description="Publishing outlet or domain")
    timestamp_raw: str = Field(
        ...,
        description=(
            "Publication time exactly as found on the page, e.g. '3 days ago' "
            "or '2024-03-10'"
        ),
    )
    url: str = Field(..., description="Canonical article URL")
    body_text: Optional[str] = Field(
        None,
        description="Main editorial body text stripped of ads/nav; null if unavailable",
    )
    content_status: ContentStatus = Field(
        default=ContentStatus.AVAILABLE,
        description="Whether full body text was extractable",
    )
    query_source: str = Field(
        ..., description="The search query string that surfaced this article"
    )


class RawArticleList(BaseModel):
    """Container so the Browser Agent can return a typed list via output_schema."""
    articles: List[RawArticle] = Field(default_factory=list)
    total_queries_executed: int = Field(
        default=0, description="How many queries were actually run"
    )


# ---------------------------------------------------------------------------
# Pipeline Stage 3 — Extraction Agent output
# ---------------------------------------------------------------------------

class EnrichedArticle(BaseModel):
    """Raw article fields plus date normalisation, sentiment, and event tags."""

    # Preserved from RawArticle
    headline: str
    publisher: str
    timestamp_raw: str
    url: str
    body_text: Optional[str]
    content_status: ContentStatus
    query_source: str

    # Enriched fields
    normalized_date: date = Field(..., description="Absolute publication date (YYYY-MM-DD)")
    sentiment: SentimentLabel
    sentiment_confidence: float = Field(
        ..., ge=0.0, le=1.0, description="0.0–1.0 confidence in the sentiment label"
    )
    sentiment_rationale: str = Field(
        ...,
        description=(
            "Brief explanation citing specific content that justified the sentiment label"
        ),
    )
    event_types: List[EventType] = Field(
        default_factory=list,
        description="One or more event categories this article describes",
    )


class EnrichedArticleList(BaseModel):
    """Container for Extraction Agent structured output."""
    articles: List[EnrichedArticle] = Field(default_factory=list)
    discarded_count: int = Field(
        default=0, description="Articles dropped for being outside the date window"
    )


# ---------------------------------------------------------------------------
# Pipeline Stage 4 — Compiler Agent output (final report)
# ---------------------------------------------------------------------------

class AggregateStats(BaseModel):
    total_articles: int
    sentiment_breakdown: Dict[str, int] = Field(
        ..., description="{'positive': N, 'negative': N, 'neutral': N}"
    )
    event_type_breakdown: Dict[str, int] = Field(
        ..., description="Count per EventType value"
    )
    content_coverage_pct: float = Field(
        ...,
        description=(
            "Percentage of articles where full body text was available. "
            "Relevant for assessing sentiment confidence."
        ),
    )
    date_range_actual: str = Field(
        ..., description="Actual earliest–latest article dates found in the dataset"
    )


class FinalReport(BaseModel):
    """The single-page analytical output delivered to the user."""
    company_name: str
    analysis_period: str = Field(..., description="Human-readable date range string")
    generated_at: str = Field(..., description="ISO-8601 UTC timestamp of generation")
    aggregate_stats: AggregateStats
    executive_summary: str = Field(
        ...,
        description="2–4 sentence overview of the most important developments",
    )
    key_events: str = Field(
        ...,
        description=(
            "Narrative synthesis of significant events drawing on article body content, "
            "not just headline restating"
        ),
    )
    sentiment_analysis: str = Field(
        ...,
        description=(
            "Analysis of overall sentiment trend with content-level evidence "
            "(e.g. specific language used by management, analyst tone)"
        ),
    )
    notable_headlines: str = Field(
        ...,
        description=(
            "3–5 most impactful articles with concise distillations of their "
            "substantive content drawn from body text"
        ),
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="Non-fatal warnings accumulated during the pipeline run",
    )
    status: WorkflowStatus = WorkflowStatus.SUCCESS
