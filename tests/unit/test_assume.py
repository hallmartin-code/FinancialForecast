"""The assumption engine: precedence, coverage, and the benchmark pack's honesty."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from pitchdeck_cfo.assume import benchmarks as bm
from pitchdeck_cfo.assume import dump, load, resolve, resolved_names
from pitchdeck_cfo.assume.engine import CORE_INPUTS, Resolver, _annual_to_monthly_growth
from pitchdeck_cfo.assume.overrides import AssumptionsFileError
from pitchdeck_cfo.errors import UnsupportedBusinessModelError
from pitchdeck_cfo.extract.schema import ALL_FINANCIAL_METRICS, DeckFacts
from pitchdeck_cfo.models import SUPPORTED_BUSINESS_MODELS
from tests import factories


def _facts(profile_kwargs: dict[str, object], **financial_kwargs: object) -> DeckFacts:
    return DeckFacts(
        profile=factories.profile(**profile_kwargs),
        financials=factories.financials(**financial_kwargs),
        model="claude-opus-5",
        deck_sha256="0" * 64,
    )


def _saas_facts(*metrics: object, **profile: object) -> DeckFacts:
    stated = list(metrics)
    names = {m.metric for m in stated}  # type: ignore[attr-defined]
    return _facts(
        {"business_model": factories.cited("saas"), **profile},
        stated=stated,
        not_stated=sorted(ALL_FINANCIAL_METRICS - names),
    )


class TestBenchmarkHonesty:
    def test_every_value_names_a_source_and_a_date(self) -> None:
        for model in SUPPORTED_BUSINESS_MODELS:
            for name, value in bm.pack_for(model).items():
                assert value.source_name, f"{model}.{name} has no source"
                assert value.as_of, f"{model}.{name} has no as_of"

    def test_a_convention_is_never_dressed_up_as_a_citation(self) -> None:
        # The distinction is the point: a model built on conventions is a template.
        convention = bm.SAAS["logo_churn_annual_pct"]
        assert convention.basis == "convention"
        assert "planning convention" in convention.citation

    def test_a_published_value_cites_cleanly_and_carries_a_url(self) -> None:
        published = bm.LIFE_SCIENCES["capitalised_rd_cost_per_approval_usd"]
        assert published.basis == "published"
        assert "planning convention" not in published.citation
        assert published.url is not None

    def test_published_values_are_more_confident_than_conventions(self) -> None:
        assert bm.COMMON["blended_tax_rate_pct"].to_sourced().confidence == "medium"
        assert bm.SAAS["logo_churn_annual_pct"].to_sourced().confidence == "low"

    def test_benchmarks_always_produce_a_benchmark_sourced_value(self) -> None:
        for model in SUPPORTED_BUSINESS_MODELS:
            for value in bm.pack_for(model).values():
                assert value.to_sourced().source == "benchmark"
                assert value.to_sourced().citation

    def test_an_unknown_function_falls_back_rather_than_raising(self) -> None:
        assert bm.salary_for("Astrology").value == bm.BASE_SALARY_USD["G&A"].value


class TestResolverPrecedence:
    def _resolver(self, overrides: dict[str, object] | None = None) -> Resolver:
        return Resolver(bm.pack_for("saas"), overrides or {})

    def test_user_outranks_everything(self) -> None:
        from pitchdeck_cfo.models import Sourced

        r = self._resolver({"logo_churn_annual_pct": 3.0})
        value = r.pick(
            "logo_churn_annual_pct",
            deck=Sourced[float](value=9.0, source="deck", citation="slide 4"),
            benchmark="logo_churn_annual_pct",
        )
        assert (value.value, value.source) == (3.0, "user")

    def test_deck_outranks_derived_and_benchmark(self) -> None:
        from pitchdeck_cfo.models import Sourced

        value = self._resolver().pick(
            "logo_churn_annual_pct",
            deck=Sourced[float](value=9.0, source="deck", citation="slide 4"),
            derived=(7.0, "computed"),
            benchmark="logo_churn_annual_pct",
        )
        assert (value.value, value.source) == (9.0, "deck")

    def test_derived_outranks_benchmark(self) -> None:
        value = self._resolver().pick(
            "logo_churn_annual_pct", derived=(7.0, "computed"), benchmark="logo_churn_annual_pct"
        )
        assert (value.value, value.source, value.note) == (7.0, "derived", "computed")

    def test_benchmark_is_used_only_when_nothing_else_exists(self) -> None:
        value = self._resolver().pick("logo_churn_annual_pct", benchmark="logo_churn_annual_pct")
        assert value.source == "benchmark"
        assert value.citation

    def test_an_unresolvable_input_raises_rather_than_defaulting_to_zero(self) -> None:
        with pytest.raises(KeyError, match="no source available"):
            self._resolver().pick("something_nobody_supplied")

    def test_coverage_reports_what_actually_happened(self) -> None:
        from pitchdeck_cfo.models import Sourced

        r = self._resolver({"gross_margin_pct": 80.0})
        r.pick("starting_arr", deck=Sourced[float](value=1.0, source="deck", citation="s1"))
        r.pick("arpu_annual", derived=(2.0, "computed"))
        r.pick("logo_churn_annual_pct", benchmark="logo_churn_annual_pct")
        r.pick("gross_margin_pct", benchmark="gross_margin_pct")

        coverage = r.coverage(
            ("starting_arr", "arpu_annual", "logo_churn_annual_pct", "gross_margin_pct")
        )
        assert (coverage.from_deck, coverage.from_derived) == (1, 1)
        assert (coverage.from_benchmark, coverage.from_user) == (1, 1)
        assert coverage.deck_ratio == 0.25


class TestGrowthCompounding:
    def test_annual_growth_is_compounded_not_divided(self) -> None:
        # 118% a year is 6.7% a month, not 9.8%. Dividing overstates the plan badly.
        monthly = _annual_to_monthly_growth(118.0)
        assert monthly == pytest.approx(6.72, abs=0.05)
        assert (1 + monthly / 100) ** 12 == pytest.approx(2.18, abs=0.01)

    def test_zero_growth_stays_zero(self) -> None:
        assert _annual_to_monthly_growth(0.0) == pytest.approx(0.0)


class TestSaaSResolution:
    def test_arpu_is_derived_from_arr_and_customers_when_not_stated(self) -> None:
        facts = _saas_facts(
            factories.metric("current_arr_usd", 2_400_000, quote="$2.4M ARR as of Q2 2026"),
            factories.metric("customer_count", 37, quote="37 paying brokerages"),
        )
        assumptions = resolve(facts, today=date(2026, 6, 1))
        arpu = assumptions.revenue.arpu_annual  # type: ignore[union-attr]
        assert arpu.source == "derived"
        assert arpu.value == pytest.approx(2_400_000 / 37)

    def test_a_stated_acv_beats_the_derivation(self) -> None:
        facts = _saas_facts(
            factories.metric("current_arr_usd", 2_400_000),
            factories.metric("customer_count", 37),
            factories.metric("acv_usd", 64_800, quote="Average contract value $64,800"),
        )
        assumptions = resolve(facts, today=date(2026, 6, 1))
        assert assumptions.revenue.arpu_annual.source == "deck"  # type: ignore[union-attr]
        assert assumptions.revenue.arpu_annual.value == 64_800  # type: ignore[union-attr]

    def test_a_deck_with_no_revenue_is_pre_revenue_not_a_benchmark(self) -> None:
        assumptions = resolve(_saas_facts(), today=date(2026, 6, 1))
        starting = assumptions.revenue.starting_arr  # type: ignore[union-attr]
        assert (starting.value, starting.source) == (0.0, "derived")
        assert "pre-revenue" in (starting.note or "")

    def test_mrr_is_annualised_when_arr_is_absent(self) -> None:
        facts = _saas_facts(factories.metric("current_mrr_usd", 100_000))
        assumptions = resolve(facts, today=date(2026, 6, 1))
        starting = assumptions.revenue.starting_arr  # type: ignore[union-attr]
        assert (starting.value, starting.source) == (1_200_000, "derived")


class TestFinancingDerivation:
    def test_burn_is_derived_from_a_stated_raise_and_runway(self) -> None:
        # A deck saying "$4M to buy 18 months" has stated its burn without the word.
        facts = _facts(
            {
                "business_model": factories.cited("saas"),
                "raise_amount_usd": factories.cited(4_000_000.0),
            },
            stated=[factories.metric("runway_months", 18)],
            not_stated=sorted(ALL_FINANCIAL_METRICS - {"runway_months"}),
        )
        burn = resolve(facts, today=date(2026, 6, 1)).financing.monthly_burn_at_start
        assert burn.source == "derived"
        assert burn.value == pytest.approx(4_000_000 / 18)

    def test_a_stated_burn_beats_the_derivation(self) -> None:
        facts = _facts(
            {
                "business_model": factories.cited("saas"),
                "raise_amount_usd": factories.cited(4_000_000.0),
            },
            stated=[
                factories.metric("runway_months", 18),
                factories.metric("monthly_burn_usd", 300_000),
            ],
            not_stated=sorted(ALL_FINANCIAL_METRICS - {"runway_months", "monthly_burn_usd"}),
        )
        burn = resolve(facts, today=date(2026, 6, 1)).financing.monthly_burn_at_start
        assert (burn.source, burn.value) == ("deck", 300_000)


class TestCalendar:
    def test_year_one_starts_after_the_decks_stated_as_of_date(self) -> None:
        facts = _facts(
            {"business_model": factories.cited("saas")},
            as_of_date=factories.cited("Q2 2026"),
        )
        assumptions = resolve(facts, today=date(2030, 1, 1))
        assert assumptions.company.start_month == "2026-07"
        assert assumptions.company.as_of_basis.source == "deck"

    def test_a_named_month_is_read(self) -> None:
        facts = _facts(
            {"business_model": factories.cited("saas")},
            as_of_date=factories.cited("March 2026"),
        )
        assert resolve(facts, today=date(2030, 1, 1)).company.start_month == "2026-04"

    def test_falls_back_to_the_run_date_and_says_so(self) -> None:
        facts = _facts({"business_model": factories.cited("saas")})
        assumptions = resolve(facts, today=date(2026, 8, 19))
        assert assumptions.company.start_month == "2026-09"
        assert assumptions.company.as_of_basis.source == "derived"
        assert "run date" in assumptions.company.as_of_basis.value

    def test_december_rolls_into_the_next_year(self) -> None:
        facts = _facts(
            {"business_model": factories.cited("saas")},
            as_of_date=factories.cited("December 2026"),
        )
        assert resolve(facts, today=date(2030, 1, 1)).company.start_month == "2027-01"


class TestUnsupportedBusinessModel:
    def test_a_marketplace_is_refused_rather_than_modelled_as_saas(self) -> None:
        facts = _facts({"business_model": factories.cited("marketplace")})
        with pytest.raises(UnsupportedBusinessModelError) as exc:
            resolve(facts)
        assert "life_sciences" in exc.value.remedy

    def test_an_unclassified_deck_is_refused(self) -> None:
        with pytest.raises(UnsupportedBusinessModelError):
            resolve(_facts({}))

    def test_an_override_can_correct_a_misread_classification(self) -> None:
        facts = _facts({"business_model": factories.cited("marketplace")})
        assumptions = resolve(facts, overrides={"business_model": "saas"})
        assert assumptions.company.business_model == "saas"


class TestCoverageAcrossEngines:
    @pytest.mark.parametrize("model", SUPPORTED_BUSINESS_MODELS)
    def test_every_core_input_is_actually_resolved(self, model: str) -> None:
        """A core input that never gets picked would silently shrink the denominator."""
        facts = _facts({"business_model": factories.cited(model)})
        coverage = resolve(facts, today=date(2026, 6, 1)).coverage
        accounted = sum(len(v) for v in coverage.by_source.values())
        assert accounted == coverage.total, f"{model}: unresolved core inputs"

    @pytest.mark.parametrize("model", SUPPORTED_BUSINESS_MODELS)
    def test_a_deck_with_nothing_in_it_scores_near_zero(self, model: str) -> None:
        facts = _facts({"business_model": factories.cited(model)})
        coverage = resolve(facts, today=date(2026, 6, 1)).coverage
        assert coverage.deck_ratio == 0.0, "an empty deck must not look well-sourced"

    def test_core_inputs_are_declared_for_every_supported_model(self) -> None:
        assert set(CORE_INPUTS) == set(SUPPORTED_BUSINESS_MODELS)


class TestAssumptionsFile:
    def _assumptions(self) -> object:
        return resolve(_saas_facts(), today=date(2026, 6, 1))

    def test_dump_round_trips_through_load(self, tmp_path: Path) -> None:
        assumptions = self._assumptions()
        text = dump(assumptions, resolved_names(assumptions))  # type: ignore[arg-type]
        path = tmp_path / "assumptions.yaml"
        path.write_text(text, encoding="utf-8")

        values = load(path)
        assert values["business_model"] == "saas"
        assert any(name.startswith("revenue.") for name in values)

    def test_the_dump_explains_which_lines_are_worth_editing(self, tmp_path: Path) -> None:
        assumptions = self._assumptions()
        text = dump(assumptions, resolved_names(assumptions))  # type: ignore[arg-type]
        assert "benchmark  supplied by this tool because the deck did not state it" in text
        assert "--assumptions assumptions.yaml" in text

    def test_a_plain_name_to_number_mapping_is_accepted(self, tmp_path: Path) -> None:
        # Editing by hand tends to produce this rather than the annotated form.
        path = tmp_path / "a.yaml"
        path.write_text("logo_churn_annual_pct: 4.5\narpu_annual: 90000\n", encoding="utf-8")
        assert load(path) == {"logo_churn_annual_pct": 4.5, "arpu_annual": 90000}

    def test_overrides_from_a_file_reach_the_model(self, tmp_path: Path) -> None:
        assumptions = resolve(
            _saas_facts(), today=date(2026, 6, 1), overrides={"logo_churn_annual_pct": 4.5}
        )
        churn = assumptions.revenue.logo_churn_annual_pct  # type: ignore[union-attr]
        assert (churn.value, churn.source, churn.citation) == (4.5, "user", "assumptions.yaml")

    def test_invalid_yaml_is_actionable(self, tmp_path: Path) -> None:
        path = tmp_path / "a.yaml"
        path.write_text("key: [unclosed\n", encoding="utf-8")
        with pytest.raises(AssumptionsFileError) as exc:
            load(path)
        assert "init-assumptions" in exc.value.remedy

    def test_an_entry_with_no_value_key_is_rejected_not_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "a.yaml"
        path.write_text("assumptions:\n  arpu_annual:\n    source: deck\n", encoding="utf-8")
        with pytest.raises(AssumptionsFileError, match="no `value:` key"):
            load(path)

    def test_a_missing_file_is_actionable(self, tmp_path: Path) -> None:
        with pytest.raises(AssumptionsFileError, match="no such file"):
            load(tmp_path / "nope.yaml")
