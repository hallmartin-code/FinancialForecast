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

- `ingest/` — `.pdf`/`.pptx` → `DeckDocument`: a flat, ordered tuple of blocks, each
  tagged with its page/slide number and kind. `DeckDocument.citation(page)` produces
  the exact string that later lands in `Sourced.citation` (`"slide 7"` / `"p. 12"`),
  and `as_prompt_text()` labels every page boundary so the model can only cite a
  location it was actually shown. PPTX speaker notes are preserved — founders hide
  real numbers there. A PDF yielding under 200 characters is treated as a scan.
- `extract/` — two Claude passes (company profile, financial facts) via
  `messages.parse`, so the JSON schema is enforced server-side; retried once with the
  validation error fed back. Every value carries a citation **and a verbatim quote**,
  and `extract/grounding.py` checks each quote against the real text of the page it
  names — which is what turns "cite your source" from an instruction into something
  verifiable. What the deck *claims* (`deck_projections`) is captured separately from
  anything this tool will model.
- `assume/` — merges facts with a business-model benchmark pack into `ModelAssumptions`.
  `Resolver.pick` is the single choke point: nothing becomes an assumption without
  passing through it, and it records the provenance it settled on — so the coverage
  ratio is a fact about what happened, not a separate tally that could drift. User
  overrides from `assumptions.yaml` sit at the top of the precedence chain.
- `model/` — pure functions, no I/O. Monthly internally, aggregated to fiscal years.
- `render/` — the workbook is the source of truth; the PDF is its executive rendering.
  Both are produced from one `FinancialModel`, so they cannot disagree.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Extraction model | `claude-opus-5` (configurable) | Extraction is the fabrication-sensitive stage. It does not default to the cheap model. The original brief named `claude-sonnet-4-5`, which is prior-generation. |
| Revenue engines at v1 | **life sciences**, **SaaS**, **hardware** | Matches the actual deal mix. Hardware was added after the real AccuBreath deck extracted as a razor/razor-blade device business — device medtech fits neither of the other two, and forcing it into `life_sciences` would produce a confident, wrong model. `marketplace` / `transactional` / `services` raise `UnsupportedBusinessModelError`: a clear refusal beats a marketplace modelled as if it were SaaS. |
| One-pager branding | TEN Capital logo in the header band | The footer is reserved for the provenance coverage ratio and disclaimer, which is the more load-bearing content. |
| Fixture decks | Real decks from the sibling projects | Committed: three small real `.pptx` decks plus a small image-only PDF derived from a real scanned deck. The 18.7 MB AccuBreath deck and the 50 MB scanned deck stay out of git and are wired as `smoke`-marked tests that skip when absent. |
| Python | 3.11+ target, developed on 3.14.6 | Only 3.14 is installed on this machine. All wheels resolve. |
| Packaging | hatchling, `src/` layout, plain `venv` + pip | `uv` is not installed here. Matches sibling TEN Capital projects. |
| OCR | optional extra, hard-fails with install instructions | `tesseract` and `poppler` are binaries pip cannot install; the error says exactly where to get them. |
| Fiscal calendar | Year 1 = 12 months starting the month after the deck's stated "as of" date (else the run date) | Stated explicitly on both artifacts rather than assumed silently. |
| PDF text extraction | pypdf `extraction_mode="layout"` | The default mode concatenates a slide heading onto the following sentence (`"PROBLEM190,600 patients suffer…"`), which destroys the blank-line paragraph split. Layout mode recovered 53% more text on the real deck (146 blocks vs 33). |
| PDF table filtering | Keep tables with ≥2 rows, ≥2 cols and ≥50% non-empty cells | pdfplumber reports ruled *layout* as a table. Measured on the real deck: genuine financial tables score ~0.82 density, layout artifacts ~0.38. The filter drops two false positives and keeps the founder's 5-year P&L. |
| PDF block `kind` | always `body` | A PDF carries no reliable structural semantics. The PPTX reader identifies titles from real placeholders; the PDF reader declines to guess rather than assert an invented structure. |
| PPTX shape order | sorted by (top, left) | python-pptx yields shapes in z-order, which on a busy slide bears no relation to reading order. Group shapes are flattened recursively so nothing nested is dropped. |
| Extraction call | `client.messages.parse(output_format=…)` + adaptive thinking | Schema enforced server-side. `budget_tokens` is rejected on this model generation; no assistant prefill, which is also rejected. The system prefix is cache-controlled since both passes reuse the same deck text. |
| Numeric metrics as a list, not nullable fields | `stated: list[MetricFact]` + `not_stated: list[FinancialMetric]` | **The API caps a schema at 16 union-typed parameters.** Twenty-odd nullable metrics returned a 400. The list form uses no unions and gives a *stronger* guarantee: the model must name what it looked for and did not find, rather than merely omitting a key. A metric missing from both lists is folded into `not_stated` and recorded in `omitted_metrics` — the safe direction, surfaced rather than hidden. A duplicated metric raises, because two values for one metric is genuinely ambiguous. |
| Quote grounding | verbatim quote required, checked against the deck | Whitespace is stripped entirely before comparison: PDF layout extraction splits words unpredictably (`"70-ye a r-old"`), and a check that tripped over that would report correct extractions as fabrications. Quotes under 12 chars are skipped as too generic to mean anything. |
| Cache key | `sha256(deck) + prompt_version + model + pass_name` | A hit across a prompt or model change would silently mix two extractors' output in one run. Each pass is cached separately, so editing one prompt does not invalidate the other. |
| Grounding match tiers | contiguous → line/cell fragments → evidence-carrying words | A deck's text does not survive extraction in one piece. A competitor matrix quotes as non-adjacent blocks; a three-column roadmap reflows. All three tiers were added in response to real false positives on the AccuBreath deck (5 warnings → 0). The word tier is permissive about *arrangement*, never about *content* — every word and every figure must still be on the cited page, so an invented number is caught exactly as before. |
| Benchmark `basis` field | `published` vs `convention` | A planning midpoint in common use is real and defensible but it is **not a citation**, and presenting it as one would be the same failure this tool exists to prevent. `convention` values render as "(2026, planning convention)" and carry `confidence="low"`. Counts: SaaS 1/28 published, life sciences 2/22, hardware 2/25 — i.e. these packs are mostly convention, and both outputs say so. |
| Benchmarks are starting values | replace them via `init-assumptions` → edit → `--assumptions` | The dump annotates every line with its source so the benchmark lines are visibly the ones worth editing. User values outrank everything. |
| `CORE_INPUTS` per business model | 8–12 named drivers, not every assumption | Coverage measures the inputs that *move* the model. A run can carry thirty benchmark values for rent-per-desk and still be a good model; it cannot if it invented the revenue base. |
| Growth compounding | `(1+annual)^(1/12) − 1` | 118% annual growth is 6.7%/month, not 9.8%. Dividing by twelve overstates a five-year plan badly. |
| Burn derivation | cash + raise ÷ stated runway | A deck saying "$4M buys 18 months" has stated its burn without using the word. Deriving beats reaching for a benchmark. |
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
| 1 | Ingest layer | **done** |
| 2 | Extraction schema, prompts, client, cache | **done** |
| 3 | Assumption engine + benchmarks | **done** |
| 4 | Modelling engine + accounting-integrity tests | |
| 5 | Excel workbook renderer | |
| 6 | One-pager PDF renderer + charts | |
| 7 | Polish: caching, `--strict`, README | |

Unbuilt stages raise `NotImplementedError`. There are no placeholder returns anywhere
in this repo, by policy — a plausible-looking fake number is worse than a crash.
