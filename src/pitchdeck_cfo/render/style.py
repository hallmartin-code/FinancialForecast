"""Workbook formatting vocabulary.

One place for the conventions in `WORKBOOK_FORMAT.md`, so a change to the house
style is a change to this file rather than a search across the renderer.

The colour system is the standard corporate-finance one, and it is load-bearing
rather than decorative: an analyst opening the file can tell at a glance which cells
are safe to change and which are computed. Nothing else in the workbook uses colour.
"""

from __future__ import annotations

from openpyxl.styles import Alignment, Font, PatternFill

# --- semantic colours ------------------------------------------------------- #

BLUE = "0000FF"
"""A hard-coded input. Safe to change."""

GREEN = "008000"
"""A link to another sheet. Traceable, not editable."""

BLACK = "000000"
"""A formula computed on this sheet."""

FLAG_FILL = "FFF2CC"
"""An input needing attention: conditional, unsupported, or a tool-supplied stand-in."""

# --- structural palette ----------------------------------------------------- #

TITLE_BG = "17365D"
SECTION_BG = "1F4E78"
YEAR_BG = "D9EAF7"
OBSERVATION_BG = "DDEBF7"
NOTE_INK = "666666"
WHITE = "FFFFFF"

BODY_SIZE = 10
TITLE_SIZE = 14

FACE = "Calibri"

# --- fonts ------------------------------------------------------------------ #

TITLE_FONT = Font(bold=True, size=TITLE_SIZE, color=WHITE, name=FACE)
SECTION_FONT = Font(bold=True, size=BODY_SIZE, color=WHITE, name=FACE)
YEAR_FONT = Font(bold=True, size=BODY_SIZE, name=FACE)
LABEL_FONT = Font(size=BODY_SIZE, name=FACE)
BOLD_LABEL = Font(bold=True, size=BODY_SIZE, name=FACE)
NOTE_FONT = Font(italic=True, size=BODY_SIZE, color=NOTE_INK, name=FACE)
OBSERVATION_FONT = Font(bold=True, size=BODY_SIZE, color=TITLE_BG, name=FACE)

INPUT_FONT = Font(size=BODY_SIZE, color=BLUE, name=FACE)
LINK_FONT = Font(size=BODY_SIZE, color=GREEN, name=FACE)
FORMULA_FONT = Font(size=BODY_SIZE, color=BLACK, name=FACE)
FORMULA_BOLD = Font(bold=True, size=BODY_SIZE, color=BLACK, name=FACE)

# --- fills ------------------------------------------------------------------ #

TITLE_FILL = PatternFill("solid", fgColor=TITLE_BG)
SECTION_FILL = PatternFill("solid", fgColor=SECTION_BG)
YEAR_FILL = PatternFill("solid", fgColor=YEAR_BG)
OBSERVATION_FILL = PatternFill("solid", fgColor=OBSERVATION_BG)
FLAG = PatternFill("solid", fgColor=FLAG_FILL)

WRAP_TOP = Alignment(wrap_text=True, vertical="top")
CENTRE = Alignment(horizontal="center")

# --- number formats --------------------------------------------------------- #
#
# Negatives are red and in parentheses, and zero renders as a dash rather than a
# distracting 0. A plain "#,##0" does neither, and both matter on a page a partner
# scans rather than reads.

MONEY = "$#,##0;[Red]($#,##0);-"
COUNT = "#,##0;[Red](#,##0);-"
PERCENT = "0.0%"
RATIO = "0.00"

# --- geometry --------------------------------------------------------------- #

LABEL_COL = 1
FIRST_YEAR_COL = 2
"""Column B. Years run B, C, D, ... one per fiscal year."""

TITLE_ROW = 1
NOTE_ROW = 3
YEAR_ROW = 4

LABEL_WIDTH = 35
WIDE_LABEL_WIDTH = 42
YEAR_WIDTH = 14

UNITS_NOTE = "Units: $000s unless otherwise noted"
THOUSANDS = 1_000.0
"""Every money figure in the workbook is in thousands."""
