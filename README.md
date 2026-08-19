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

## Deploying to Railway

The app is a thin FastAPI front end over the same `pipeline.run` the CLI uses, so
the web build and the command-line build cannot drift apart.

1. **Rotate the key first if it has ever been pasted anywhere.** The service spends
   it on every build.
2. Push this directory to a Git repo and create a Railway service from it. Nixpacks
   detects `requirements.txt` and `nixpacks.toml`; `railway.json` sets the start
   command and points the health check at `/healthz`.
3. Set service variables in Railway:

   | Variable | Required | Notes |
   |---|---|---|
   | `ANTHROPIC_API_KEY` | **yes** | Never commit it. Railway variables only. |
   | `APP_PASSWORD` | strongly recommended | Without it the service is open and anyone with the URL can spend your key. |
   | `APP_USERNAME` | no | Defaults to `ten`. |
   | `PDCFO_MODEL` | no | Defaults to `claude-opus-5`. |
   | `MAX_UPLOAD_MB` | no | Defaults to 40. |
   | `JOB_TTL_MINUTES` | no | Uploaded decks and artifacts are deleted after this; defaults to 120. |
   | `MAX_CONCURRENT_JOBS` | no | Defaults to 2. Each build costs money. |

4. Deploy. `/healthz` reports whether a credential was found and whether the service
   is password-protected, so you can confirm both without uploading anything.

Run it locally the same way:

```bash
.venv/Scripts/python -m uvicorn app:app --reload --port 8000
```

### Things worth knowing before you point people at it

- **OCR is not installed in the container.** Tesseract and poppler add hundreds of
  megabytes and are only needed for scanned decks. A scanned PDF gets a clear error
  rather than a wrong answer.
- **Uploaded decks are confidential** and are deleted on a timer (`JOB_TTL_MINUTES`).
  Jobs live in memory, so a restart clears them.
- **Railway's filesystem is ephemeral.** Artifacts are meant to be downloaded, not
  stored; nothing here is a system of record.
