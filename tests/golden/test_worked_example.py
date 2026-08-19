"""A full worked example, verified by hand.

The company below is deliberately simple enough that every line of the five-year
statement can be computed on paper. If any of these assertions break, the engine has
changed its arithmetic and someone has to justify why.

    Revenue      $1.2M ARR, flat: no growth, no churn, NRR 100%, no new logos
                 -> $100,000 a month, $1,200,000 a year, every year

    COGS         hosting at 10% of revenue, linear (exponent 1.0)
                 -> $10,000 a month, $120,000 a year
    Gross profit -> $1,080,000 a year, a 90% margin

    Headcount    5 engineers, $120,000 base, loaded 1.0x
                 -> R&D personnel $600,000 a year
                 G&A triggers at 1 per 10 FTE: ceil(5/10) = 1 person, $120,000
    Opex         R&D $600,000 + G&A ($120,000 + $180,000 fixed) = $900,000

    EBITDA       $1,080,000 - $900,000              = $180,000 a year
    Tax          25%, no opening losses, profitable
                 from month one                     =  $45,000 a year
    Net income                                      = $135,000 a year

    Cash         opens at $500,000, no working capital, no capex
                 -> +$135,000 a year
                 -> 635,000 / 770,000 / 905,000 / 1,040,000 / 1,175,000
"""

from __future__ import annotations

import numpy as np
import pytest

from pitchdeck_cfo.model import build
from tests import model_factories as mf

REVENUE_PER_YEAR = 1_200_000.0
COGS_PER_YEAR = 120_000.0
GROSS_PER_YEAR = 1_080_000.0
RD_PER_YEAR = 600_000.0
GA_PER_YEAR = 300_000.0
OPEX_PER_YEAR = 900_000.0
EBITDA_PER_YEAR = 180_000.0
TAX_PER_YEAR = 45_000.0
NET_INCOME_PER_YEAR = 135_000.0
OPENING_CASH = 500_000.0


@pytest.fixture(scope="module")
def worked():  # type: ignore[no-untyped-def]
    return build(
        mf.assumptions(
            "saas",
            revenue=mf.saas_revenue(new_logos_month_1=mf.s(0)),
            cogs=mf.cogs(hosting_pct_of_revenue=mf.s(10.0), hosting_scale_exponent=mf.s(1.0)),
            headcount=mf.headcount(
                starting={"Engineering": 5},
                ftes_per_ga_hire=mf.s(10.0),
            ),
            opex=mf.opex(ga_fixed_annual=mf.s(180_000)),
            financing=mf.financing(cash_on_hand=mf.s(OPENING_CASH)),
            tax=mf.tax(blended_rate_pct=mf.s(25.0)),
        )
    )


class TestTheWholeStatement:
    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("Revenue", REVENUE_PER_YEAR),
            ("COGS", COGS_PER_YEAR),
            ("Gross Profit", GROSS_PER_YEAR),
            ("R&D", RD_PER_YEAR),
            ("S&M", 0.0),
            ("G&A", GA_PER_YEAR),
            ("Total Opex", OPEX_PER_YEAR),
            ("EBITDA", EBITDA_PER_YEAR),
            ("Tax", TAX_PER_YEAR),
            ("Net Income", NET_INCOME_PER_YEAR),
        ],
    )
    def test_every_line_matches_the_hand_calculation_in_every_year(
        self,
        worked,  # type: ignore[no-untyped-def]
        line: str,
        expected: float,
    ) -> None:
        assert worked.pnl_annual[line] == pytest.approx([expected] * 5)

    def test_margins(self, worked) -> None:  # type: ignore[no-untyped-def]
        assert worked.pnl_annual["Gross Margin %"] == pytest.approx([90.0] * 5)
        assert worked.pnl_annual["EBITDA Margin %"] == pytest.approx([15.0] * 5)

    def test_monthly_revenue_is_exactly_one_twelfth(self, worked) -> None:  # type: ignore[no-untyped-def]
        assert worked.pnl_monthly["Revenue"] == pytest.approx([100_000.0] * 60)

    def test_headcount(self, worked) -> None:  # type: ignore[no-untyped-def]
        assert worked.headcount_annual["Engineering"] == pytest.approx([5.0] * 5)
        assert worked.headcount_annual["G&A"] == pytest.approx([1.0] * 5)
        assert worked.headcount_annual["Total"] == pytest.approx([6.0] * 5)


class TestCash:
    def test_ending_cash_walks_forward_by_net_income(self, worked) -> None:  # type: ignore[no-untyped-def]
        # No working capital, no capex, no depreciation: cash moves by EBITDA less tax.
        assert worked.ending_cash == pytest.approx(
            [635_000.0, 770_000.0, 905_000.0, 1_040_000.0, 1_175_000.0]
        )

    def test_the_company_never_needs_cash(self, worked) -> None:  # type: ignore[no-untyped-def]
        assert worked.peak_cash_need == 0.0
        assert worked.runs_out_of_cash_month is None

    def test_it_is_profitable_from_the_first_month(self, worked) -> None:  # type: ignore[no-untyped-def]
        assert worked.ebitda_positive_month == "2026-01"
        assert worked.cash_positive_month == "2026-01"


class TestMetrics:
    def test_revenue_per_fte(self, worked) -> None:  # type: ignore[no-untyped-def]
        # $1.2M over 6 people.
        assert worked.metrics[0].revenue_per_fte == pytest.approx(200_000.0)

    def test_growth_is_zero_and_stated_as_such(self, worked) -> None:  # type: ignore[no-untyped-def]
        assert worked.metrics[0].revenue_growth_pct is None  # no prior year
        assert worked.metrics[1].revenue_growth_pct == pytest.approx(0.0)

    def test_rule_of_40_is_growth_plus_margin(self, worked) -> None:  # type: ignore[no-untyped-def]
        assert worked.metrics[1].rule_of_40 == pytest.approx(0.0 + 15.0)

    def test_cac_is_undefined_when_no_customers_are_won(self, worked) -> None:  # type: ignore[no-untyped-def]
        # Printing zero here would read as "free customer acquisition".
        assert worked.metrics[0].cac is None
        assert worked.metrics[0].ltv_to_cac is None

    def test_ending_cash_matches_the_statement(self, worked) -> None:  # type: ignore[no-untyped-def]
        assert [m.ending_cash for m in worked.metrics] == pytest.approx(list(worked.ending_cash))


class TestOneChangeAtATime:
    """Each mechanism must move the statement by exactly the amount it should."""

    def _base(self, **overrides):  # type: ignore[no-untyped-def]
        settings = dict(
            revenue=mf.saas_revenue(new_logos_month_1=mf.s(0)),
            cogs=mf.cogs(hosting_pct_of_revenue=mf.s(10.0), hosting_scale_exponent=mf.s(1.0)),
            headcount=mf.headcount(starting={"Engineering": 5}, ftes_per_ga_hire=mf.s(10.0)),
            opex=mf.opex(ga_fixed_annual=mf.s(180_000)),
            financing=mf.financing(cash_on_hand=mf.s(OPENING_CASH)),
            tax=mf.tax(blended_rate_pct=mf.s(25.0)),
        )
        settings.update(overrides)
        return build(mf.assumptions("saas", **settings))

    def test_doubling_the_loaded_multiplier_adds_exactly_the_payroll(self) -> None:
        loaded = self._base(
            headcount=mf.headcount(
                starting={"Engineering": 5},
                ftes_per_ga_hire=mf.s(10.0),
                loaded_multiplier=mf.s(2.0),
            )
        )
        # 6 people at $120k, doubled, adds $720k of cost a year.
        assert loaded.pnl_annual["Total Opex"][0] == pytest.approx(OPEX_PER_YEAR + 720_000.0)
        assert loaded.pnl_annual["EBITDA"][0] == pytest.approx(EBITDA_PER_YEAR - 720_000.0)

    def test_capex_reduces_cash_and_creates_depreciation(self) -> None:
        with_capex = self._base(
            working_capital=mf.working_capital(
                capex_pct_of_revenue=mf.s(10.0), capex_depreciation_years=mf.s(1.0)
            )
        )
        # 10% of $1.2M is $10k of capex a month, each tranche depreciated over 12
        # months. Capex spent in the last eleven months of the horizon has not
        # finished depreciating when the model ends, and that tail is not counted:
        #   months 1-49 depreciate in full        49 x 10,000 = 490,000
        #   months 50-60 depreciate 11/12 .. 1/12  10,000 x 66/12 =  55,000
        assert with_capex.pnl_annual["EBITDA"][0] == pytest.approx(EBITDA_PER_YEAR)
        assert sum(with_capex.pnl_annual["Depreciation"]) == pytest.approx(545_000.0)
        # Cash, unlike the P&L, feels the whole $120k in year one.
        assert with_capex.ending_cash[0] == pytest.approx(635_000.0 - 120_000.0, abs=20_000)

    def test_a_raise_lands_in_cash_and_nowhere_else(self) -> None:
        funded = self._base(
            financing=mf.financing(cash_on_hand=mf.s(OPENING_CASH), raise_amount=mf.s(5_000_000))
        )
        assert funded.pnl_annual["Revenue"][0] == pytest.approx(REVENUE_PER_YEAR)
        assert funded.pnl_annual["EBITDA"][0] == pytest.approx(EBITDA_PER_YEAR)
        assert funded.ending_cash[0] == pytest.approx(635_000.0 + 5_000_000.0)

    def test_dso_consumes_cash_without_touching_revenue(self) -> None:
        slow_paying = self._base(working_capital=mf.working_capital(dso_days=mf.s(30.0)))
        assert slow_paying.pnl_annual["Revenue"][0] == pytest.approx(REVENUE_PER_YEAR)
        # One month of revenue is sitting in receivables and not in the bank.
        assert slow_paying.ending_cash[0] == pytest.approx(635_000.0 - 100_000.0)

    def test_a_sub_linear_hosting_curve_widens_the_margin(self) -> None:
        growing = self._base(
            revenue=mf.saas_revenue(new_logo_growth_monthly_pct=mf.s(3.0)),
            cogs=mf.cogs(hosting_pct_of_revenue=mf.s(10.0), hosting_scale_exponent=mf.s(0.7)),
        )
        margins = growing.pnl_annual["Gross Margin %"]
        assert margins[-1] > margins[0]
        assert np.all(np.diff(np.array(margins)) > 0)
