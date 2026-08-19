"""Accounting integrity. A model that does not reconcile is not shippable.

These run over synthetic assumptions and over the three real decks. The synthetic
cases prove the invariants hold under deliberately awkward inputs; the real ones
prove they hold on the numbers that will actually be shipped.
"""

from __future__ import annotations

import numpy as np
import pytest

from pitchdeck_cfo.model import build
from pitchdeck_cfo.model.build import IntegrityError, check_integrity
from pitchdeck_cfo.model.timeline import MONTHS_PER_YEAR
from tests import model_factories as mf

# Deliberately awkward: revenue, costs, working capital, tax and a raise all active,
# so no invariant can pass by everything being zero.
FULL = dict(
    revenue=mf.saas_revenue(
        new_logo_growth_monthly_pct=mf.s(4.0),
        growth_decay_annual_pct=mf.s(30.0),
        terminal_growth_monthly_pct=mf.s(1.0),
        logo_churn_annual_pct=mf.s(10.0),
        net_revenue_retention_pct=mf.s(112.0),
        annual_prepay_mix_pct=mf.s(60.0),
    ),
    cogs=mf.cogs(
        hosting_pct_of_revenue=mf.s(9.0),
        hosting_scale_exponent=mf.s(0.85),
        support_pct_of_revenue=mf.s(4.0),
        payment_processing_pct=mf.s(2.9),
    ),
    headcount=mf.headcount(
        starting={"Engineering": 8, "Sales": 3, "G&A": 2},
        loaded_multiplier=mf.s(1.28),
        annual_attrition_pct=mf.s(15.0),
        rep_quota_annual=mf.s(600_000),
        rep_ramp_months=mf.s(6.0),
        quota_attainment_pct=mf.s(75.0),
        accounts_per_csm=mf.s(40.0),
        engineers_per_product_line=mf.s(6.0),
        product_lines=mf.s(1.0),
        scale_exponent=mf.s(0.75),
    ),
    opex=mf.opex(
        marketing_pct_of_new_revenue=mf.s(40.0),
        rd_tooling_per_engineer=mf.s(6_000),
        rent_per_fte=mf.s(6_000),
        software_per_fte=mf.s(3_600),
        ga_fixed_annual=mf.s(180_000),
    ),
    working_capital=mf.working_capital(
        dso_days=mf.s(45.0),
        dpo_days=mf.s(30.0),
        capex_pct_of_revenue=mf.s(2.0),
    ),
    financing=mf.financing(cash_on_hand=mf.s(1_000_000), raise_amount=mf.s(8_000_000)),
    tax=mf.tax(blended_rate_pct=mf.s(25.0)),
)


@pytest.fixture(scope="module")
def full_model() -> object:
    return build(mf.assumptions("saas", **FULL))


ALL_MODELS = ["saas", "hardware", "life_sciences"]


class TestStatementTies:
    @pytest.mark.parametrize("model", ALL_MODELS)
    def test_every_engine_reconciles(self, model: str) -> None:
        # build() calls check_integrity internally and raises if it does not tie.
        assert build(mf.assumptions(model)) is not None  # type: ignore[arg-type]

    def test_revenue_less_cogs_equals_gross_profit(self, full_model) -> None:  # type: ignore[no-untyped-def]
        p = full_model.pnl_monthly
        assert np.allclose(
            np.array(p["Revenue"]) - np.array(p["COGS"]), np.array(p["Gross Profit"])
        )

    def test_gross_profit_less_opex_equals_ebitda(self, full_model) -> None:  # type: ignore[no-untyped-def]
        p = full_model.pnl_monthly
        assert np.allclose(
            np.array(p["Gross Profit"]) - np.array(p["Total Opex"]), np.array(p["EBITDA"])
        )

    def test_opex_lines_sum_to_total_opex(self, full_model) -> None:  # type: ignore[no-untyped-def]
        p = full_model.pnl_monthly
        parts = np.array(p["R&D"]) + np.array(p["S&M"]) + np.array(p["G&A"])
        assert np.allclose(parts, np.array(p["Total Opex"]))

    def test_ebitda_through_to_net_income(self, full_model) -> None:  # type: ignore[no-untyped-def]
        p = full_model.pnl_monthly
        ebit = np.array(p["EBITDA"]) - np.array(p["Depreciation"])
        assert np.allclose(ebit, np.array(p["EBIT"]))
        pretax = ebit - np.array(p["Interest"])
        assert np.allclose(pretax, np.array(p["Pretax Income"]))
        assert np.allclose(pretax - np.array(p["Tax"]), np.array(p["Net Income"]))

    def test_cash_flow_ties_to_the_change_in_cash_balance(self, full_model) -> None:  # type: ignore[no-untyped-def]
        cash = full_model.cash_monthly
        opening = full_model.assumptions.financing.cash_on_hand.value
        expected = opening + np.cumsum(np.array(cash["Net Change in Cash"]))
        assert np.allclose(expected, np.array(cash["Ending Cash"]))

    def test_cash_statement_lines_sum_to_the_net_change(self, full_model) -> None:  # type: ignore[no-untyped-def]
        cash = full_model.cash_monthly
        parts = (
            np.array(cash["EBITDA"])
            + np.array(cash["Change in AR"])
            + np.array(cash["Change in Deferred Revenue"])
            + np.array(cash["Change in AP"])
            + np.array(cash["Change in Inventory"])
            + np.array(cash["Capex"])
            + np.array(cash["Tax Paid"])
            + np.array(cash["Financing"])
        )
        assert np.allclose(parts, np.array(cash["Net Change in Cash"]))


class TestMonthlyAnnualConsistency:
    @pytest.mark.parametrize(
        "line",
        [
            "Revenue",
            "COGS",
            "Gross Profit",
            "R&D",
            "S&M",
            "G&A",
            "Total Opex",
            "EBITDA",
            "Depreciation",
            "Tax",
            "Net Income",
        ],
    )
    def test_sum_of_monthly_equals_annual_for_every_flow(self, full_model, line: str) -> None:  # type: ignore[no-untyped-def]
        monthly = np.array(full_model.pnl_monthly[line])
        annual = np.array(full_model.pnl_annual[line])
        assert np.allclose(monthly.reshape(-1, MONTHS_PER_YEAR).sum(axis=1), annual)

    def test_balances_take_the_closing_value_rather_than_summing(self, full_model) -> None:  # type: ignore[no-untyped-def]
        # Summing a cash balance over twelve months produces a meaningless number.
        monthly = np.array(full_model.cash_monthly["Ending Cash"])
        annual = np.array(full_model.cash_annual["Ending Cash"])
        assert np.allclose(monthly.reshape(-1, MONTHS_PER_YEAR)[:, -1], annual)

    def test_annual_margins_are_recomputed_not_averaged(self, full_model) -> None:  # type: ignore[no-untyped-def]
        # The average of twelve monthly margins is not the annual margin.
        annual = full_model.pnl_annual
        revenue = np.array(annual["Revenue"])
        expected = (
            np.divide(
                np.array(annual["Gross Profit"]),
                revenue,
                out=np.zeros_like(revenue),
                where=revenue > 0,
            )
            * 100.0
        )
        assert np.allclose(np.array(annual["Gross Margin %"]), expected)


class TestTaxAndLosses:
    def test_no_tax_is_charged_while_losses_remain(self, full_model) -> None:  # type: ignore[no-untyped-def]
        tax = np.array(full_model.pnl_monthly["Tax"])
        pretax = np.array(full_model.pnl_monthly["Pretax Income"])
        assert np.all(tax[pretax <= 0] == 0)

    def test_tax_is_never_negative(self, full_model) -> None:  # type: ignore[no-untyped-def]
        # A negative tax line would be the model refunding cash it never paid.
        assert np.all(np.array(full_model.pnl_monthly["Tax"]) >= 0)

    def test_a_company_that_never_profits_never_pays_tax(self) -> None:
        model = build(
            mf.assumptions(
                "life_sciences",
                opex=mf.opex(ga_fixed_annual=mf.s(600_000)),
                tax=mf.tax(blended_rate_pct=mf.s(25.0)),
            )
        )
        assert sum(model.pnl_annual["Tax"]) == 0


class TestIntegrityCheckActuallyFires:
    """The guard has to be capable of failing, or it is decoration.

    These also pin the tolerance: the ties are checked in absolute dollars, because
    numpy's default relative tolerance accepts a discrepancy that grows with the size
    of the balance -- $300 on a $30M cash position, which is not a rounding error.
    """

    def test_a_broken_gross_profit_is_caught(self) -> None:
        model = build(mf.assumptions("saas", **FULL))
        from dataclasses import replace

        from pitchdeck_cfo.model import cashflow, cogs, opex, pnl, revenue
        from pitchdeck_cfo.model.timeline import Timeline

        timeline = Timeline(start_month="2026-01", horizon_years=5)
        assumptions = model.assumptions
        rev = revenue.build(assumptions, timeline)
        cg = cogs.build(assumptions, rev, timeline)
        op = opex.build(assumptions, mock_headcount(assumptions, rev, timeline), rev, timeline)
        result = pnl.build(assumptions, rev, cg, op, timeline)
        cash = cashflow.build(assumptions, rev, result, timeline)

        # $100 is immaterial to the business and must still fail the tie.
        broken = replace(result, gross_profit=result.gross_profit + 100.0)
        with pytest.raises(IntegrityError, match="gross profit"):
            check_integrity(broken, cash, 0.0)

    def test_a_broken_cash_tie_is_caught(self) -> None:
        from dataclasses import replace

        from pitchdeck_cfo.model import cashflow, cogs, opex, pnl, revenue
        from pitchdeck_cfo.model.timeline import Timeline

        timeline = Timeline(start_month="2026-01", horizon_years=5)
        assumptions = mf.assumptions("saas", **FULL)
        rev = revenue.build(assumptions, timeline)
        cg = cogs.build(assumptions, rev, timeline)
        op = opex.build(assumptions, mock_headcount(assumptions, rev, timeline), rev, timeline)
        result = pnl.build(assumptions, rev, cg, op, timeline)
        cash = cashflow.build(assumptions, rev, result, timeline)

        broken = replace(cash, ending_cash=cash.ending_cash + 100.0)
        with pytest.raises(IntegrityError, match="does not tie"):
            check_integrity(result, broken, assumptions.financing.cash_on_hand.value)


def mock_headcount(assumptions, revenue_result, timeline):  # type: ignore[no-untyped-def]
    from pitchdeck_cfo.model import headcount

    return headcount.build(assumptions, revenue_result, timeline)


class TestRealDecks:
    """The invariants must hold on the numbers that actually ship, not just fixtures."""

    @pytest.mark.smoke
    @pytest.mark.parametrize(
        "deck",
        [
            "tests/fixtures/decks/saas_meridian.pptx",
            "tests/fixtures/decks/lifesci_helion.pptx",
        ],
    )
    def test_real_deck_models_reconcile(self, deck: str) -> None:
        from pathlib import Path

        from pitchdeck_cfo.assume import resolve
        from pitchdeck_cfo.config import load_settings
        from pitchdeck_cfo.extract import default_cache, extract_facts
        from pitchdeck_cfo.ingest import load_deck

        settings = load_settings()
        cache = default_cache(settings)
        document = load_deck(Path(deck))
        facts = extract_facts(document, settings=settings, cache=cache)
        model = build(resolve(facts, horizon_years=5))

        p = model.pnl_monthly
        assert np.allclose(
            np.array(p["Revenue"]) - np.array(p["COGS"]), np.array(p["Gross Profit"])
        )
        cash = model.cash_monthly
        opening = model.assumptions.financing.cash_on_hand.value
        assert np.allclose(
            opening + np.cumsum(np.array(cash["Net Change in Cash"])),
            np.array(cash["Ending Cash"]),
        )
