"""Model stage: `ModelAssumptions` -> a reconciled `FinancialModel`.

Pure functions throughout -- assumptions in, arrays out, no I/O anywhere. Monthly
internally because break-even, peak cash need and runway are month-level facts that
an annual model cannot state.
"""

from __future__ import annotations

from pitchdeck_cfo.model.build import FinancialModel, IntegrityError, annual_frame, build
from pitchdeck_cfo.model.timeline import Timeline

__all__ = ["FinancialModel", "IntegrityError", "Timeline", "annual_frame", "build"]
