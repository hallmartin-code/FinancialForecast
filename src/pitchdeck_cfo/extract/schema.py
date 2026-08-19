"""What the extraction passes are allowed to return.

Two rules are enforced by the types themselves rather than by prompt wording:

1. **Every extracted value carries a citation and a verbatim quote.** `Cited[T]` has
   no default for either, so the JSON schema marks them required and the model cannot
   omit them. The quote is later checked against the actual deck text
   (`extract.grounding`), which turns "cite your source" from an instruction into
   something verifiable.

2. **Absence is expressed, never implied.** Optional fields have no Python default, so
   they are `required` in the JSON schema with `null` a permitted value. The model
   must actively say "not in the deck" rather than quietly dropping a key, which is
   how missing data silently becomes invented data.

Nothing here can express a benchmark or an inference. Those enter in `assume/`.
"""

from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pitchdeck_cfo.models import BusinessModelKind, Confidence, DeckValue

T = TypeVar("T")

# Bumped whenever a prompt or this schema changes. Part of the cache key, so an edit
# here invalidates cached extractions instead of serving a stale shape.
PROMPT_VERSION = "2026-08-19.2"


class Evidence(BaseModel):
    """The support any extracted value must carry.

    Shared by `Cited[T]` and `MetricFact` so the grounding checker can verify both
    without knowing which shape it is looking at.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    citation: str = Field(
        description=("Where this appears, exactly as the deck text labels it: slide 7, or p. 12.")
    )
    quote: str = Field(
        description=(
            "Verbatim text copied from that page which states this value. Copy it "
            "character for character. Do not paraphrase, reformat or reconstruct it."
        )
    )
    confidence: Confidence = Field(
        description=(
            "high when the deck states this directly, medium when it is stated "
            "indirectly, low when you are reading between the lines."
        )
    )


class Cited(Evidence, Generic[T]):
    """One fact the deck states, with the evidence for it."""

    value: T

    def to_sourced(self) -> DeckValue[T]:
        """Promote to the pipeline's provenance type."""
        return DeckValue[T](
            value=self.value,
            citation=self.citation,
            confidence=self.confidence,
            note=None,
        )


class PeriodAmount(BaseModel):
    """A figure attached to a named period, such as FY2027 revenue."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    period: str = Field(description="The period label as the deck writes it: 2027, FY26, Q1 2026.")
    amount: Cited[float]


class LineItemSeries(BaseModel):
    """One row of a financial table the founder put in the deck."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(description="The row label exactly as written, such as Revenue or EBITDA.")
    unit: str | None = Field(
        description=("The magnitude the table declares, such as $000s. null when not stated.")
    )
    values: list[PeriodAmount]


class FunctionHeadcount(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    function: str = Field(description="Engineering, Sales, Clinical, G&A, and so on.")
    count: Cited[int]


class Milestone(BaseModel):
    """A dated event the company says it is working toward."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    description: Cited[str]
    target_date: Cited[str] | None
    cost_usd: Cited[float] | None


# --------------------------------------------------------------------------- #
# Pass A -- company profile
# --------------------------------------------------------------------------- #


class CompanyProfile(BaseModel):
    """Who the company is and how it says it makes money."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    company_name: Cited[str] | None
    one_liner: Cited[str] | None
    sector: Cited[str] | None
    geography: Cited[str] | None
    stage: Cited[str] | None

    business_model: Cited[BusinessModelKind] | None
    business_model_rationale: str = Field(
        description=(
            "Why you classified it that way, referring to what the deck says. This is "
            "your reasoning, not a fact from the deck."
        )
    )

    target_customer: Cited[str] | None
    pricing_model: Cited[str] | None
    price_points: list[Cited[str]]
    gtm_motion: Cited[str] | None

    raise_amount_usd: Cited[float] | None
    round_type: Cited[str] | None
    instrument_terms: Cited[str] | None
    pre_money_valuation_usd: Cited[float] | None
    use_of_funds: list[Cited[str]]

    team_size: Cited[int] | None
    named_roles: list[Cited[str]]
    competitors: list[Cited[str]]
    moat_claims: list[Cited[str]]


# --------------------------------------------------------------------------- #
# Pass B -- financial facts
# --------------------------------------------------------------------------- #


class LifeSciencesFacts(BaseModel):
    """Facts that only exist for a pre-revenue, milestone-driven company.

    Every field is nullable. A SaaS deck returns null for all of them, and that is
    the correct answer rather than a reason to invent something.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    development_stage: Cited[str] | None
    regulatory_pathway: Cited[str] | None
    indication: Cited[str] | None
    next_milestone: Milestone | None
    non_dilutive_funding_usd: Cited[float] | None
    partnership_or_licensing_terms: Cited[str] | None
    trial_or_validation_data: list[Cited[str]]


# Every numeric metric the financial pass looks for. The model must account for each
# one, either by reporting it in `stated` or by naming it in `not_stated`.
FinancialMetric = Literal[
    # current state
    "current_arr_usd",
    "current_mrr_usd",
    "current_gmv_usd",
    "customer_count",
    "arpu_usd",
    "acv_usd",
    # rates and ratios
    "growth_rate_pct",
    "gross_margin_pct",
    "logo_churn_pct",
    "net_revenue_retention_pct",
    "gross_revenue_retention_pct",
    "cac_usd",
    "cac_payback_months",
    "sales_cycle_days",
    # cash
    "monthly_burn_usd",
    "cash_on_hand_usd",
    "runway_months",
    # market
    "tam_usd",
    "sam_usd",
    "som_usd",
]

ALL_FINANCIAL_METRICS: frozenset[FinancialMetric] = frozenset(get_args(FinancialMetric))


class MetricFact(Evidence):
    """One numeric metric the deck states.

    This is a list entry rather than a named nullable field for a hard reason: the
    API caps a schema at 16 union-typed parameters, and twenty-odd nullable metrics
    blows straight through it. Carrying them as a list plus an explicit `not_stated`
    roster keeps the "absence must be declared" guarantee while using no unions at
    all -- and it is a stronger guarantee, because the model has to name what it
    looked for and did not find rather than just omitting a key.
    """

    metric: FinancialMetric
    value: float = Field(
        description=(
            "Whole dollars for _usd metrics (2400000, not 2.4), a plain number for "
            "_pct (71.0, not 0.71), and a count for the rest."
        )
    )


class FinancialFacts(BaseModel):
    """What the deck states about money. Nothing computed, nothing assumed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of_date: Cited[str] | None = Field(
        description="The date the figures are current as of, when the deck says."
    )
    currency: Cited[str] | None

    stated: list[MetricFact] = Field(
        description=(
            "Every metric the deck actually states. One entry per metric, at most once each."
        )
    )
    not_stated: list[FinancialMetric] = Field(
        description=(
            "Every metric you looked for and did not find in the deck. Together with "
            "stated this must account for all of them; a metric you leave out of both "
            "lists is treated as not stated and reported as an omission."
        )
    )

    # --- history, and the founder's own forecast kept apart from it ---
    historical_revenue: list[PeriodAmount] = Field(
        description="Revenue for periods that have already happened."
    )
    deck_projections: list[LineItemSeries] = Field(
        description=(
            "The company's own forward projections, one entry per row of any "
            "financial table in the deck. This is what the deck CLAIMS. It is "
            "recorded separately from anything this tool will model, and is never "
            "treated as established fact."
        )
    )

    headcount_by_function: list[FunctionHeadcount]
    life_sciences: LifeSciencesFacts

    # Metrics the model accounted for in neither list. Filled in below rather than
    # raised: treating an unlisted metric as "not stated" is the safe direction, and
    # failing a whole run over a bookkeeping slip is not worth it. The omission is
    # recorded so it can be reported rather than hidden.
    omitted_metrics: tuple[FinancialMetric, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _account_for_every_metric(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        stated_names = {
            entry.get("metric") if isinstance(entry, dict) else getattr(entry, "metric", None)
            for entry in data.get("stated") or []
        }
        declared = stated_names | set(data.get("not_stated") or [])
        missing = sorted(ALL_FINANCIAL_METRICS - declared)
        if missing:
            data["not_stated"] = list(data.get("not_stated") or []) + missing
            data["omitted_metrics"] = tuple(missing)
        return data

    @model_validator(mode="after")
    def _no_duplicate_metrics(self) -> FinancialFacts:
        seen = [fact.metric for fact in self.stated]
        duplicates = sorted({name for name in seen if seen.count(name) > 1})
        if duplicates:
            raise ValueError(
                "each metric may appear at most once in stated; duplicated: "
                + ", ".join(duplicates)
            )
        return self

    def get(self, metric: FinancialMetric) -> MetricFact | None:
        """The stated value for a metric, or None when the deck does not give one."""
        return next((fact for fact in self.stated if fact.metric == metric), None)


# --------------------------------------------------------------------------- #
# combined
# --------------------------------------------------------------------------- #

GroundingReason = Literal["page_not_found", "quote_not_on_page", "quote_not_in_deck"]


class GroundingWarning(BaseModel):
    """A returned value whose quote could not be found where it was cited."""

    model_config = ConfigDict(frozen=True)

    field_path: str
    citation: str
    quote: str
    reason: GroundingReason


class DeckFacts(BaseModel):
    """Everything the deck states, and nothing else."""

    model_config = ConfigDict(frozen=True)

    profile: CompanyProfile
    financials: FinancialFacts

    prompt_version: str = PROMPT_VERSION
    model: str
    deck_sha256: str
    grounding_warnings: tuple[GroundingWarning, ...] = ()

    @property
    def is_grounded(self) -> bool:
        return not self.grounding_warnings
