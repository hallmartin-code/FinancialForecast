"""pitchdeck-cfo: investor deck -> auditable financial model -> one-page summary."""

__version__ = "0.1.0"

# Stamped into the one-pager footer and the workbook README so any artifact can be
# traced back to the code that produced it. Bump when model logic changes in a way
# that would alter output for an unchanged deck.
MODEL_VERSION = "0.1.0"

__all__ = ["MODEL_VERSION", "__version__"]
