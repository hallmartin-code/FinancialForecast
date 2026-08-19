# Financial Model Workbook — Output Document Structure

The structure `pitchdeck-cfo` emits for `<company>_financial_model.xlsx`, derived from
a reference CFO model. This is the contract the renderer implements and the tests
enforce; it carries no company-specific content.

---

## 0. Why these conventions

They are the ones a financial analyst reads without being told. The colour system in
particular is standard across investment banking and corporate finance: **an analyst
opening the file can tell in one glance which cells are safe to change and which are
computed.** Departing from it makes the model look amateur regardless of how good the
arithmetic is.

---

## 1. Sheet order

Order is the reading order: conclusion first, then the drivers, then the audit trail.

| # | Sheet | Purpose |
|---|---|---|
| 1 | `Summary` | Everything a partner needs without scrolling. Headline financials, operating stats, and the CFO observations. |
| 2 | `Assumptions` | Every input, one cell each. The only sheet with hard-coded numbers. |
| 3 | `Revenue Build` | Units × price → revenue, by stream. |
| 4 | `Headcount` | Headcount by function, then loaded cost by function. |
| 5 | `P&L` | Revenue → COGS → gross profit → opex → EBITDA. |
| 6 | `Cash Flow` | EBITDA → working capital → capex → financing → ending cash. |
| 7 | `Sources & Notes` | Where every figure came from and how far it can be trusted. |

---

## 2. Grid

Identical on every sheet, so the eye lands in the same place each time.

| Element | Rule |
|---|---|
| Row 1 | Title band. Merged `A1:G1`. |
| Row 2 | Blank. |
| Row 3 | Units / subtitle note. Italic. |
| Row 4 | Year header row (`Summary` uses row 5). |
| Row 5+ | Content, in sections. |
| Column A | Line labels. Width 35 (42 where labels run long). |
| Columns B…F | One fiscal year each. Width 14. |
| Column G | Spare, width 14, so the merged bands have somewhere to end. |
| Freeze panes | `B5` (`B6` on `Summary`) — labels and years always visible. |
| Section header | Merged `A:G`, one blank row above it. |
| Body font | 10pt throughout. Titles 14pt. |

**Years are columns, not months.** A five-year model is five columns. The engine
works monthly internally — break-even month and peak cash need are month-level facts
that an annual grid cannot produce — but the workbook a reader opens is annual,
because sixty columns is not a document anyone reads.

---

## 3. Colour — the load-bearing convention

Four meanings, and nothing else uses colour.

| Colour | Hex | Meaning | Where |
|---|---|---|---|
| **Blue** | `0000FF` | A hard-coded input. **Safe to change.** | `Assumptions` only |
| **Green** | `008000` | A link to another sheet. Traceable, not editable. | Every calculation sheet |
| **Black** | `000000` | A formula computed on this sheet. | Every calculation sheet |
| **Yellow fill** | `FFF2CC` | An input needing attention — conditional, unsupported by the source, or a placeholder. | `Assumptions` |

The green/black split is the part most models omit and the part that makes a workbook
auditable: it tells you whether a number was calculated *here* or arrived from
somewhere else, so you know which sheet to go to when it looks wrong.

`pitchdeck-cfo` maps its provenance onto this directly:

| Provenance | Rendering |
|---|---|
| `deck` | Blue input |
| `user` | Blue input |
| `benchmark` | Blue input on **yellow fill** — supplied by the tool, not the company, and therefore the line to replace |
| `derived` | Blue input, yellow fill where it stands in for something the deck never stated |

Structural palette:

| Element | Hex |
|---|---|
| Title band background | `17365D` (white text) |
| Section band background | `1F4E78` (white text) |
| Year header background | `D9EAF7` |
| Observations block background | `DDEBF7` (text `17365D`) |
| Subtitle / note text | `666666` |

---

## 4. Numbers

| Kind | Format | Renders |
|---|---|---|
| Money | `$#,##0;[Red]($#,##0);-` | `$1,234` · `($1,234)` in red · `-` for zero |
| Count | `#,##0;[Red](#,##0);-` | `47` · `(47)` · `-` |
| Percent | `0.0%` | `71.4%` |

Three things this format does that a plain `#,##0` does not: negatives are **red and
in parentheses**, zero renders as a **dash** rather than a distracting `0`, and the
currency symbol appears on money and nowhere else.

**Units are `$000s` throughout**, stated in the row 3 note. Every money figure — inputs,
formulas, outputs — is in thousands. Percentages, counts and ratios are unscaled.

**Costs are negative.** COGS, opex and capex are stored as negative numbers, so
subtotals *add*:

```
Gross profit = Total revenue + Total COGS      (not revenue − COGS)
EBITDA       = Gross profit  + Total opex
```

This is the convention that lets a reader check a column by summing it, and it makes
a sign error visible instead of silent.

**Every division is wrapped:** `=IFERROR(numerator/denominator,0)`. A pre-revenue year
divides by zero, and `#DIV/0!` propagating into the Summary makes the whole model look
broken.

---

## 5. Per-sheet structure

### 5.1 Summary

```
A1   {{COMPANY}} — {{CASE_NAME}} Financial Model            [title band]
A3   {{PROVENANCE_NOTE}}                                    [italic]
B5   {{YEAR}} …                                             [year header]

A7   Financial summary ($000)                               [section band]
     Revenue · Gross profit · Gross margin · EBITDA ·
     EBITDA margin · Ending cash · Incremental financing required

A16  Operating summary                                      [section band]
     Total headcount · {{UNIT_DRIVER}} … one line per revenue driver

A22  Key CFO observations                                   [observations band]
     {{#EACH OBSERVATION}} {{N}}. {{TEXT}} {{/EACH}}
```

Every figure on this sheet is a **green link** to the sheet that computes it. The
Summary calculates nothing; if it did, it could disagree with the statement it
summarises.

**Key CFO observations** is the analytical payload and the reason the sheet is first.
Each is one sentence, and each says something a reader could act on. Generated from:

| Trigger | Observation |
|---|---|
| Revenue concentrated in one contract or stream | Name it and say what happens without it. |
| A steep ramp in the outer years | Say it is a pipeline case, not backlog. |
| A conditional inflow in the plan | Name the condition and say to remove it if not secured. |
| A core input the source never stated | Say it was set to zero or benchmarked, and to replace it. |
| Heavy benchmark reliance | Say which areas are tool estimates rather than company figures. |
| Model materially below the deck's own projection | State the gap and the multiple. |

### 5.2 Assumptions

The only sheet with typed numbers. Sections in the order the model consumes them:

```
Revenue assumptions
Cost of goods sold assumptions
Headcount & compensation assumptions
Non-personnel operating expense assumptions
Cash flow & financing assumptions
```

Each line: label in column A, one value per year in B…F. Assumptions that do not vary
by year repeat across the row rather than occupying a single cell, so a user can taper
any of them without restructuring the sheet.

### 5.3 Revenue Build

One section per revenue stream, each ending in that stream's revenue line, then a
total section:

```
{{STREAM}} section
  {{DRIVER_UNITS}}          green link to Assumptions
  {{DRIVER_PRICE}}          green link to Assumptions
  {{STREAM}} revenue        black formula: units × price

Total revenue
  Total revenue             black formula: sum of the streams
  YoY growth                black formula, "-" in year 1
```

### 5.4 Headcount

```
Headcount by function
  one line per function      green link to Assumptions
  Total headcount            black formula: SUM

Annual cash compensation by function ($000)
  one line per function      black formula: headcount × comp
  Total cash compensation    black formula: SUM
```

Payroll burden is applied in the **P&L**, not here, so this sheet shows cash
compensation and the P&L shows fully loaded cost. Mixing the two is how a model ends
up double-counting benefits.

### 5.5 P&L

```
Revenue              one line per stream (green), Total revenue (black)
Cost of goods sold   one line per cost (black, negative), Total COGS, Gross profit,
                     Gross margin
Operating expenses   personnel and non-personnel per function, Total operating
                     expenses, EBITDA, EBITDA margin
```

Personnel lines carry the burden multiplier: `=-Headcount!B15*(1+Assumptions!B29)`.

### 5.6 Cash Flow

```
Operating cash flow
  EBITDA                            green link
  Accounts receivable               black, from DSO
  Inventory                         black, from inventory days
  Accounts payable                  black, from DPO
  Net working capital               black
  Change in NWC                     black, first year = the balance itself
  Operating cash flow               black

Investing & financing
  Capital expenditures              green link, negative
  Free cash flow before financing   black
  {{FINANCING_LINE}}                green link, one per source
  Net cash change                   black

Liquidity
  Beginning cash                    year 1 links Assumptions; later years = prior ending
  Ending cash                       black
  Incremental financing required
    to stay ≥ {{MINIMUM_CASH}}      black: MAX(0, minimum − ending cash)
```

The last line is the one a founder should read first: it converts a cash trough into
the number they have to go and raise.

### 5.7 Sources & Notes

A five-column table. This is where `pitchdeck-cfo`'s provenance becomes a document
a human reads.

| Column | Content |
|---|---|
| `Source` | The document or system the figures came from |
| `Location` | Page, slide, or "this workbook" |
| `Model use` | What in the model rests on it |
| `Confidence` | `High` · `Medium` · `Low/Medium` · `Low` |
| `Notes` | The caveat — what to verify, what it is not |

Row 3 states the **source hierarchy**, highest authority first:

```
signed contract > pitch deck > existing financial model > CFO assumptions
```

Rows are generated from the extraction and the benchmark pack:

| Provenance | Source | Confidence |
|---|---|---|
| Deck-stated facts | the deck filename | High |
| The company's own projections | the deck, with the page | Medium — a claim, not contracted |
| Benchmark values, published | the publication, with a URL | Medium |
| Benchmark values, convention | "Planning convention" | Low/Medium |
| Derived values | "This workbook" | Medium |
| User overrides | `assumptions.yaml` | as the user states |

Every row's `Notes` says what would have to happen for the figure to be trusted more
than it currently is.

---

## 6. Rules

1. **`Assumptions` is the only sheet with a typed number.** Everything else is a
   formula or a link. A hard-coded value elsewhere is a bug: it will not respond when
   the assumption above it changes.
2. **Colour carries meaning, so nothing else may use it.** No decorative fills, no
   highlighted rows.
3. **Costs are negative and subtotals add.** Never mix the two conventions inside one
   statement.
4. **Every division is wrapped in `IFERROR`.**
5. **The Summary calculates nothing.** It links, so it cannot disagree.
6. **Money is `$000s` everywhere**, and row 3 says so on every sheet.
7. **A benchmark-supplied input is visibly flagged**, because it is the company's
   number that is missing, not the tool's that is present.
8. **Every sheet freezes its labels and its year header.**
9. **Sections are separated by one blank row** and introduced by a merged band.
10. **`Sources & Notes` accounts for every input.** A figure with no row there has no
    provenance, which is the failure this whole tool exists to prevent.
