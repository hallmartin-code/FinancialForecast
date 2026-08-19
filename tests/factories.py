"""Builders for the extraction schema.

Every field in `CompanyProfile` and `FinancialFacts` is required -- that is the point
of the schema, since it forces the model to say "null" rather than omit a key. It
also makes hand-constructing one in a test tedious, so these helpers default
everything to null and take overrides.
"""

from __future__ import annotations

from typing import Any

from pitchdeck_cfo.extract.schema import (
    ALL_FINANCIAL_METRICS,
    Cited,
    CompanyProfile,
    FinancialFacts,
    LifeSciencesFacts,
    MetricFact,
)


def cited(value: Any, citation: str = "slide 1", quote: str = "a quote from the deck") -> Any:
    return Cited[type(value)](  # type: ignore[misc]
        value=value, citation=citation, quote=quote, confidence="high"
    )


def empty_life_sciences(**overrides: Any) -> LifeSciencesFacts:
    fields: dict[str, Any] = {
        "development_stage": None,
        "regulatory_pathway": None,
        "indication": None,
        "next_milestone": None,
        "non_dilutive_funding_usd": None,
        "partnership_or_licensing_terms": None,
        "trial_or_validation_data": [],
    }
    fields.update(overrides)
    return LifeSciencesFacts(**fields)


def profile(**overrides: Any) -> CompanyProfile:
    fields: dict[str, Any] = dict.fromkeys(
        [
            "company_name",
            "one_liner",
            "sector",
            "geography",
            "stage",
            "business_model",
            "target_customer",
            "pricing_model",
            "gtm_motion",
            "raise_amount_usd",
            "round_type",
            "instrument_terms",
            "pre_money_valuation_usd",
            "team_size",
        ]
    )
    fields |= {
        "business_model_rationale": "",
        "price_points": [],
        "use_of_funds": [],
        "named_roles": [],
        "competitors": [],
        "moat_claims": [],
    }
    fields.update(overrides)
    return CompanyProfile(**fields)


def financials(**overrides: Any) -> FinancialFacts:
    """A FinancialFacts stating nothing: every metric declared as not stated."""
    fields: dict[str, Any] = {
        "as_of_date": None,
        "currency": None,
        "stated": [],
        "not_stated": sorted(ALL_FINANCIAL_METRICS),
        "historical_revenue": [],
        "deck_projections": [],
        "headcount_by_function": [],
        "life_sciences": empty_life_sciences(),
    }
    fields.update(overrides)
    return FinancialFacts(**fields)


def metric(
    name: str,
    value: float,
    citation: str = "slide 1",
    quote: str = "a quote from the deck",
) -> MetricFact:
    return MetricFact(
        metric=name,  # type: ignore[arg-type]
        value=value,
        citation=citation,
        quote=quote,
        confidence="high",
    )
