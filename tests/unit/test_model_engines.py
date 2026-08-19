"""Per-function tests with hand-checked expected values.

Every number asserted here was worked out by hand from the inputs. The factories set
every rate to zero by default so each test can switch on exactly one mechanism and
the arithmetic stays checkable.
"""

from __future__ import annotations

import numpy as np
import pytest

from pitchdeck_cfo.model import cogs, headcount, pnl, revenue
from pitchdeck_cfo.model.timeline import (
    MONTHS_PER_YEAR,
    Timeline,
    annual_to_monthly_rate,
    decaying_growth_curve,
    growth_curve,
)
from tests import model_factories as mf

T5 = Timeline(start_month="2026-01", horizon_years=5)
T1 = Timeline(start_month="2026-01", horizon_years=1)


class TestTimeline:
    def test_month_labels_roll_the_year_over(self) -> None:
        labels = Timeline(start_month="2026-11", horizon_years=1).month_labels()
        assert labels[:4] == ("2026-11", "2026-12", "2027-01", "2027-02")
        assert len(labels) == 12

    def test_flows_sum_and_balances_take_the_closing_value(self) -> None:
        series = np.arange(24, dtype=float)  # 0..23
        t = Timeline(start_month="2026-01", horizon_years=2)
        # 0+1+...+11 = 66; 12+...+23 = 210
        assert list(t.to_annual(series)) == [66.0, 210.0]
        assert list(t.end_of_year(series)) == [11.0, 23.0]

    def test_annual_to_monthly_compounds(self) -> None:
        # 12 months at the monthly rate must return exactly the annual rate.
        monthly = annual_to_monthly_rate(100.0)
        assert (1 + monthly) ** 12 == pytest.approx(2.0)

    def test_growth_curve_starts_at_one(self) -> None:
        curve = growth_curve(4, 10.0)
        assert list(curve) == pytest.approx([1.0, 1.1, 1.21, 1.331])


class TestDecayingGrowth:
    def test_zero_decay_matches_the_plain_curve(self) -> None:
        assert decaying_growth_curve(24, 5.0, 0.0, 0.0) == pytest.approx(growth_curve(24, 5.0))

    def test_the_rate_decays_toward_the_floor(self) -> None:
        # 10%/month decaying 50% a year: month 12 should be growing at ~5%.
        curve = decaying_growth_curve(26, 10.0, 50.0, 0.0)
        early = curve[1] / curve[0] - 1.0
        late = curve[13] / curve[12] - 1.0
        assert early == pytest.approx(0.10, abs=0.001)
        assert late == pytest.approx(0.05, abs=0.002)

    def test_the_floor_holds(self) -> None:
        curve = decaying_growth_curve(60, 10.0, 90.0, 2.0)
        tail = curve[-1] / curve[-2] - 1.0
        assert tail == pytest.approx(0.02, abs=0.0005)

    def test_a_five_year_horizon_is_not_a_fantasy(self) -> None:
        """The reason decay exists: flat extrapolation of a high rate is absurd."""
        flat = growth_curve(60, 6.72)[-1]  # 118%/yr held for five years
        decayed = decaying_growth_curve(60, 6.72, 30.0, 1.5)[-1]
        assert flat > 45  # ~49x
        assert decayed < flat / 3


class TestSaaSRevenue:
    def test_flat_base_with_no_churn_growth_or_new_logos(self) -> None:
        model = mf.saas_revenue(new_logos_month_1=mf.s(0))
        result = revenue.saas(model, T1)
        # $1.2M ARR at 100% NRR = $100k a month, every month.
        assert result.recognised == pytest.approx(np.full(12, 100_000.0))
        assert sum(result.recognised) == pytest.approx(1_200_000.0)

    def test_new_logos_add_arpu_worth_of_arr_each(self) -> None:
        # 10 logos a month at $12k ARPU = $120k of new ARR a month.
        result = revenue.saas(mf.saas_revenue(), T1)
        assert result.new_recurring == pytest.approx(np.full(12, 120_000.0))
        # ARR after month 1: 1.2M + 120k = 1.32M -> $110k recognised.
        assert result.recognised[0] == pytest.approx(1_320_000 / 12)
        # After twelve months: 1.2M + 12 x 120k = 2.64M.
        assert result.recognised[-1] * 12 == pytest.approx(2_640_000.0)

    def test_nrr_above_one_hundred_grows_the_existing_base(self) -> None:
        model = mf.saas_revenue(new_logos_month_1=mf.s(0), net_revenue_retention_pct=mf.s(120.0))
        result = revenue.saas(model, T1)
        # Twelve months of compounding must land exactly on 120% of the opening ARR.
        assert result.recognised[-1] * 12 == pytest.approx(1_200_000 * 1.20)

    def test_logo_churn_shrinks_the_customer_count(self) -> None:
        model = mf.saas_revenue(new_logos_month_1=mf.s(0), logo_churn_annual_pct=mf.s(20.0))
        result = revenue.saas(model, T1)
        assert result.ending_customers[-1] == pytest.approx(100 * 0.80, abs=0.01)

    def test_customers_and_revenue_move_independently(self) -> None:
        """A company can lose logos while growing revenue. Both must be tracked."""
        model = mf.saas_revenue(
            new_logos_month_1=mf.s(0),
            logo_churn_annual_pct=mf.s(20.0),
            net_revenue_retention_pct=mf.s(120.0),
        )
        result = revenue.saas(model, T1)
        assert result.ending_customers[-1] < 100
        assert result.recognised[-1] > result.recognised[0]

    def test_prepayment_creates_deferred_revenue_without_changing_revenue(self) -> None:
        plain = revenue.saas(mf.saas_revenue(), T1)
        prepaid = revenue.saas(mf.saas_revenue(annual_prepay_mix_pct=mf.s(100.0)), T1)
        assert prepaid.recognised == pytest.approx(plain.recognised)
        assert prepaid.deferred_balance[-1] > 0
        # Billings run ahead of revenue while the deferred balance is building.
        assert sum(prepaid.billings) > sum(prepaid.recognised)


class TestHardwareRevenue:
    def test_device_and_consumable_are_both_counted(self) -> None:
        result = revenue.hardware(mf.hardware_revenue(), T1)
        # 10 units x $4,000 = $40,000 of device revenue a month.
        assert result.components["device"] == pytest.approx(np.full(12, 40_000.0))
        # Consumables come off the installed base: month 1 has 10 devices,
        # 12 consumables per device per year = 10 units at $30 = $300.
        assert result.components["consumables"][0] == pytest.approx(300.0)
        assert result.components["consumables"][-1] == pytest.approx(120 * 30.0)

    def test_the_consumable_stream_compounds_off_the_installed_base(self) -> None:
        """The mistake a naive device model makes: costing consumables off new units."""
        result = revenue.hardware(mf.hardware_revenue(), T5)
        consumables = result.components["consumables"]
        assert consumables[-1] / consumables[0] == pytest.approx(60.0, rel=0.01)

    def test_distributor_margin_reduces_realised_revenue(self) -> None:
        direct = revenue.hardware(mf.hardware_revenue(), T1)
        indirect = revenue.hardware(
            mf.hardware_revenue(direct_sales_mix_pct=mf.s(0.0), distributor_margin_pct=mf.s(25.0)),
            T1,
        )
        assert sum(indirect.recognised) == pytest.approx(sum(direct.recognised) * 0.75)

    def test_a_mixed_channel_lands_between_the_two(self) -> None:
        mixed = revenue.hardware(
            mf.hardware_revenue(direct_sales_mix_pct=mf.s(50.0), distributor_margin_pct=mf.s(25.0)),
            T1,
        )
        direct = revenue.hardware(mf.hardware_revenue(), T1)
        assert sum(mixed.recognised) == pytest.approx(sum(direct.recognised) * 0.875)


class TestLifeSciencesRevenue:
    def test_a_preclinical_company_has_no_revenue(self) -> None:
        """Inventing a commercial ramp here would be the worst thing this tool could do."""
        result = revenue.life_sciences(mf.life_sciences_revenue(), T5)
        assert sum(result.recognised) == 0.0

    def test_a_stated_upfront_is_recognised_over_a_year(self) -> None:
        model = mf.life_sciences_revenue(partnership_upfront=mf.s(1_200_000))
        result = revenue.life_sciences(model, T5)
        assert result.recognised[0] == pytest.approx(100_000.0)
        assert sum(result.recognised) == pytest.approx(1_200_000.0)
        assert result.recognised[12] == 0.0

    def test_programme_spend_is_spread_across_its_phase(self) -> None:
        spend = revenue.program_spend(mf.life_sciences_revenue(), T5)
        assert spend[:12] == pytest.approx(np.full(12, 100_000.0))
        assert sum(spend[12:]) == 0.0

    def test_a_phase_beyond_the_horizon_is_not_compressed_into_it(self) -> None:
        from pitchdeck_cfo.assume.schema import ProgramPhase

        model = mf.life_sciences_revenue(
            program_phases=(
                ProgramPhase(
                    name="Phase 2",
                    start_month_offset=mf.s(72),
                    duration_months=mf.s(24),
                    total_cost=mf.s(15_000_000),
                ),
            )
        )
        assert sum(revenue.program_spend(model, T5)) == 0.0


class TestCOGS:
    def test_a_linear_exponent_holds_the_percentage_flat(self) -> None:
        rev = np.array([100_000.0, 200_000.0, 400_000.0])
        scaled = cogs.scaled_cost(rev, 10.0, 1.0)
        assert scaled == pytest.approx(rev * 0.10)

    def test_a_sub_linear_exponent_produces_margin_expansion(self) -> None:
        # This is the only mechanism in the model that widens gross margin.
        rev = np.array([100_000.0, 400_000.0])
        scaled = cogs.scaled_cost(rev, 10.0, 0.5)
        assert scaled[0] == pytest.approx(10_000.0)  # exact at the reference
        assert scaled[1] == pytest.approx(20_000.0)  # 4x revenue, 2x cost
        assert scaled[1] / rev[1] < scaled[0] / rev[0]

    def test_a_pre_revenue_company_has_no_cost_of_revenue(self) -> None:
        assert cogs.scaled_cost(np.zeros(12), 10.0, 0.85) == pytest.approx(np.zeros(12))

    def test_gross_margin_is_zero_rather_than_infinite_without_revenue(self) -> None:
        margin = cogs.gross_margin_pct(np.zeros(3), np.array([0.0, 0.0, 0.0]))
        assert np.all(np.isfinite(margin))
        assert margin == pytest.approx(np.zeros(3))


class TestDepreciationAndTax:
    def test_capex_depreciates_straight_line_over_its_life(self) -> None:
        capex = np.zeros(48)
        capex[0] = 36_000.0
        schedule = pnl.depreciation_schedule(capex, 3.0)
        assert schedule[0] == pytest.approx(1_000.0)
        assert schedule[35] == pytest.approx(1_000.0)
        assert schedule[36] == 0.0
        assert sum(schedule) == pytest.approx(36_000.0)

    def test_losses_shelter_later_profits(self) -> None:
        # Lose 100 for two months, then make 100 for three. The first two profitable
        # months are sheltered; only the third is taxed.
        pretax = np.array([-100.0, -100.0, 100.0, 100.0, 100.0])
        tax, nol = pnl.apply_tax(pretax, 25.0, 0.0)
        assert list(tax) == pytest.approx([0.0, 0.0, 0.0, 0.0, 25.0])
        assert list(nol) == pytest.approx([100.0, 200.0, 100.0, 0.0, 0.0])

    def test_an_opening_loss_carryforward_is_honoured(self) -> None:
        tax, _ = pnl.apply_tax(np.array([100.0, 100.0]), 25.0, opening_nol=100.0)
        assert list(tax) == pytest.approx([0.0, 25.0])

    def test_a_partial_shelter_taxes_only_the_excess(self) -> None:
        tax, _ = pnl.apply_tax(np.array([100.0]), 25.0, opening_nol=40.0)
        assert tax[0] == pytest.approx(15.0)  # 25% of the unsheltered 60


class TestTriggeredHiring:
    def _revenue(self, **kwargs: object) -> revenue.RevenueResult:
        return revenue.saas(mf.saas_revenue(**kwargs), T5)  # type: ignore[arg-type]

    def test_sales_hiring_follows_quota_coverage(self) -> None:
        plan = mf.assumptions(
            "saas",
            headcount=mf.headcount(rep_quota_annual=mf.s(600_000), quota_attainment_pct=mf.s(50.0)),
        )
        result = headcount.build(plan, self._revenue(), T5)
        # $120k of new ARR a month against $300k of effective capacity per rep.
        assert result.by_function["Sales"][0] == 1
        assert "quota" in result.driver_notes["Sales"]

    def test_customer_success_hiring_follows_the_account_count(self) -> None:
        plan = mf.assumptions("saas", headcount=mf.headcount(accounts_per_csm=mf.s(50.0)))
        result = headcount.build(plan, self._revenue(), T5)
        # 100 starting customers plus 10 a month; 110 accounts needs 3 CSMs at 50 each.
        assert result.by_function["Customer Success"][0] == 3

    def test_headcount_never_falls(self) -> None:
        """A plan that fires its way to profitability is not what a founder means."""
        plan = mf.assumptions(
            "saas",
            headcount=mf.headcount(accounts_per_csm=mf.s(10.0)),
        )
        result = headcount.build(plan, self._revenue(logo_churn_annual_pct=mf.s(50.0)), T5)
        counts = result.by_function["Customer Success"]
        assert np.all(np.diff(counts) >= 0)

    def test_a_function_with_no_driver_holds_at_its_stated_level(self) -> None:
        plan = mf.assumptions("saas", headcount=mf.headcount(starting={"Clinical": 4}))
        result = headcount.build(plan, self._revenue(), T5)
        assert np.all(result.by_function["Clinical"] == 4)
        assert "no hiring driver" in result.driver_notes["Clinical"]

    def test_sub_linear_scaling_produces_operating_leverage(self) -> None:
        """Linear scaling is why bottom-up models never reach profitability."""
        growing = self._revenue(new_logo_growth_monthly_pct=mf.s(5.0))

        def plan(exponent: float) -> object:
            return headcount.build(
                mf.assumptions(
                    "saas",
                    headcount=mf.headcount(
                        revenue_per_engineer=mf.s(400_000),
                        scale_exponent=mf.s(exponent),
                    ),
                ),
                growing,
                T5,
            )

        linear = plan(1.0)
        levered = plan(0.75)
        revenue_growth = growing.recognised[-1] / growing.recognised[0]

        def growth(result: object) -> float:
            counts = result.by_function["Engineering"]  # type: ignore[attr-defined]
            return float(counts[-1] / counts[0])

        # The linear plan tracks revenue. It is not exact because people are whole
        # numbers and the count starts small, where rounding up hurts most.
        assert growth(linear) == pytest.approx(revenue_growth, rel=0.20)
        # The levered plan is the point: headcount grows far slower than revenue,
        # which is the only way the model ever reaches profitability.
        assert growth(levered) < revenue_growth * 0.5
        assert levered.by_function["Engineering"][-1] < linear.by_function["Engineering"][-1]

    def test_loaded_cost_applies_the_multiplier(self) -> None:
        plan = mf.assumptions(
            "saas",
            headcount=mf.headcount(starting={"Engineering": 10}, loaded_multiplier=mf.s(1.30)),
        )
        result = headcount.build(plan, self._revenue(), T5)
        # 10 engineers at $120k base, loaded 1.3x, per month.
        expected = 10 * 120_000 / MONTHS_PER_YEAR * 1.30
        assert result.cost_by_function["Engineering"][0] == pytest.approx(expected)
