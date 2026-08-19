# pitchdeck-cfo — architecture & working notes

Deck in, auditable financial model out. This file records the decisions, the data
contract between stages, and how to run things. It is updated at every phase boundary.

## The constraint everything else serves

Fabricated financials are the worst failure mode of this project. The design makes
fabrication structurally difficult rather than merely discouraged:

1. **`Sourced[T]`** (`models.py`) wraps every leaf value with `source` /
   `citation` / `confidence` / `note`. A `deck`, `benchmark` or `user` value without a
   citation fails validation at construction.
2. **`DeckValue[T]`** narrows `source` to the literal `"deck"`. The entire extraction
   schema is built from these, so the extraction pass *cannot* emit a benchmark or an
   inference — it would have to violate the type to do so. Anything the deck does not
   state comes back `null`.
3. **Gap-filling is quarantined in `assume/`.** That package is the only place allowed
   to mint `source="benchmark"`, and only from `assume/benchmarks.py`, where every
   constant carries its published source.
4. **Coverage is reported, not hidden.** `CoverageReport` counts how many `CORE_INPUTS`
   came from each source. It is printed by the CLI, stamped in the one-pager footer,
   written to the workbook README, and is the gate `--strict` checks.

Provenance precedence when several sources offer the same input:
`user > deck > derived > benchmark`.

Render marks: deck values are unmarked, `†` derived, `‡` benchmark, `§` user override.

## Pipeline

Strictly one-way. Each stage is independently testable and hands off a frozen
Pydantic model.

```
DeckDocument → DeckFacts → ModelAssumptions → FinancialModel → { PDF, XLSX }
   ingest        extract         assume            model          render
```

- `ingest/` — `.pdf`/`.pptx` → ordered text blocks and tables, each tagged with its
  page/slide number and block type. PPTX speaker notes are preserved; founders hide
  real numbers there. A PDF yielding under 200 characters is treated as a scan.
- `extract/` — two Claude passes (company profile, financial facts), each returning
  strict JSON validated against the schema, retried once with the validation error fed
  back. What the deck *claims* is captured separately from what we will *model*.
- `assume/` — merges facts with a stage/sector benchmark pack into `ModelAssumptions`.
  User overrides from `assumptions.yaml` sit at the top of the precedence chain.
- `model/` — pure functions, no I/O. Monthly internally, aggregated to fiscal years.
- `render/` — the workbook is the source of truth; the PDF is its executive rendering.
  Both are produced from one `FinancialModel`, so they cannot disagree.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Extraction model | `claude-opus-5` (configurable) | Extraction is the fabrication-sensitive stage. It does not default to the cheap model. The original brief named `claude-sonnet-4-5`, which is prior-generation. |
| Revenue engines at v1 | **life sciences** and **SaaS** | Matches TEN Capital's actual deal mix. `marketplace` / `transactional` / `hardware` raise `UnsupportedBusinessModelError` — a clear refusal beats a marketplace modelled as if it were SaaS. |
| One-pager branding | TEN Capital logo in the header band | The footer is reserved for the provenance coverage ratio and disclaimer, which is the more load-bearing content. |
| Fixture decks | Real decks from the sibling projects | Committed: three small real `.pptx` decks plus a small image-only PDF derived from a real scanned deck. The 18.7 MB AccuBreath deck and the 50 MB scanned deck stay out of git and are wired as `smoke`-marked tests that skip when absent. |
| Python | 3.11+ target, developed on 3.14.6 | Only 3.14 is installed on this machine. All wheels resolve. |
| Packaging | hatchling, `src/` layout, plain `venv` + pip | `uv` is not installed here. Matches sibling TEN Capital projects. |
| OCR | optional extra, hard-fails with install instructions | `tesseract` and `poppler` are binaries pip cannot install; the error says exactly where to get them. |
| Fiscal calendar | Year 1 = 12 months starting the month after the deck's stated "as of" date (else the run date) | Stated explicitly on both artifacts rather than assumed silently. |
| Cache | `~/.cache/pitchdeck-cfo/<sha256(deck)+prompt_version+model>.json` | Iterating on rendering costs no API calls. `--no-cache` bypasses. |

## Layout

```
src/pitchdeck_cfo/
  cli.py       config.py    errors.py    models.py
  ingest/      extract/     assume/      model/       render/
tests/
  unit/  integrity/  golden/  render/  fixtures/decks/
```

`model/` adds a `pnl.py` beyond the original brief: the P&L roll-up plus the
D&A / interest / NOL-carryforward tax reconciliation is enough logic to own a module
rather than hide inside `cashflow.py`.

## Running things

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"

.venv/Scripts/pitchdeck-cfo --help
.venv/Scripts/pitchdeck-cfo build tests/fixtures/decks/saas_meridian.pptx --out ./output

.venv/Scripts/ruff check . && .venv/Scripts/ruff format --check .
.venv/Scripts/mypy --strict src/
.venv/Scripts/python -m pytest -q
```

Markers: `llm` (needs a live API key) and `smoke` (needs the large external decks) are
deselected in CI with `-m "not llm and not smoke"`.

## Build sequence

| Phase | Deliverable | Status |
|---|---|---|
| 0 | Scaffold, config, CLI skeleton, CI, fixtures | **done** |
| 1 | Ingest layer | |
| 2 | Extraction schema, prompts, client, cache | |
| 3 | Assumption engine + benchmarks | |
| 4 | Modelling engine + accounting-integrity tests | |
| 5 | Excel workbook renderer | |
| 6 | One-pager PDF renderer + charts | |
| 7 | Polish: caching, `--strict`, README | |

Unbuilt stages raise `NotImplementedError`. There are no placeholder returns anywhere
in this repo, by policy — a plausible-looking fake number is worse than a crash.
