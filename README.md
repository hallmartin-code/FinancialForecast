# pitchdeck-cfo

Turns an early-stage investor deck (`.pdf` / `.pptx`) into two artifacts:

- `<company>_financial_model.xlsx` — a formula-driven, auditable 3–5 year model. **Source of truth.**
- `<company>_financial_onepager.pdf` — a single-page investor-grade rendering of that model.

They are generated from one `FinancialModel` object, so they cannot disagree.

## The rule this tool is built around

Every number carries its origin: **deck**, **derived**, **benchmark**, or **user**.
The extraction pass is structurally incapable of inventing a figure — its schema only
accepts deck-sourced values and returns `null` for anything the deck does not state.
Gap-filling happens later and is labelled wherever it appears. The one-pager footer
states the coverage ratio; `--strict` refuses to emit a model that is mostly benchmark.

## Install

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Windows
cp .env.example .env                              # then set ANTHROPIC_API_KEY
```

## Use

```bash
pitchdeck-cfo build deck.pdf --out ./output --years 5 --strict
pitchdeck-cfo extract deck.pptx --json facts.json
pitchdeck-cfo init-assumptions deck.pdf > assumptions.yaml
pitchdeck-cfo build deck.pdf --assumptions assumptions.yaml
```

Status: **Phase 0** — scaffold. Unbuilt stages raise `NotImplementedError` rather than
returning placeholder data. See `CLAUDE.md` for architecture and the build sequence.
