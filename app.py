"""Web front end: upload a deck, get the one-pager and the workbook.

Deliberately thin. It calls `pipeline.run` -- the same function the CLI calls -- so
there is one implementation of the pipeline and no chance of the web app drifting
from the tool it wraps.

**Read this before deploying.** Every build spends your Anthropic key. Left without
`APP_PASSWORD` the service is open to anyone who has the URL, and the upload page
says so rather than letting you find out from a bill.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import secrets
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

load_dotenv()

from pitchdeck_cfo import MODEL_VERSION, pipeline  # noqa: E402
from pitchdeck_cfo.config import load_settings  # noqa: E402
from pitchdeck_cfo.errors import PitchdeckCFOError  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pitchdeck-cfo.web")

SUPPORTED = {".pdf", ".pptx"}
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "40"))
JOB_TTL_MINUTES = int(os.environ.get("JOB_TTL_MINUTES", "120"))
MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "2"))

APP_USERNAME = os.environ.get("APP_USERNAME", "ten")
APP_PASSWORD = os.environ.get("APP_PASSWORD")

WEB_DIR = Path(__file__).parent / "web"
STATIC_DIR = WEB_DIR / "static"


def _asset_version() -> str:
    """A short hash of the icon, appended to every icon URL.

    Browsers cache favicons in a store of their own that survives an ordinary reload,
    a hard refresh, and often a cache clear -- and they cache the *absence* of one
    just as happily. Any origin visited before the icon existed will keep showing a
    blank tab indefinitely. Versioning the URL makes it a different resource, which
    is the only reliable way to make a favicon change actually appear.
    """
    icon = STATIC_DIR / "favicon.ico"
    if not icon.exists():
        return MODEL_VERSION
    return hashlib.sha256(icon.read_bytes()).hexdigest()[:8]


ASSET_VERSION = _asset_version()

app = FastAPI(title="pitchdeck-cfo", docs_url=None, redoc_url=None)
security = HTTPBasic(auto_error=False)

# Brand assets. Deliberately outside the auth gate: a browser fetches /favicon.ico
# before it has credentials, and a logo is not a secret. Long-lived cache headers
# because these change about as often as the company rebrands.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# One build at a time by default. Each spends real money and holds a deck in memory,
# so unbounded concurrency is a way to be surprised twice.
_slots = asyncio.Semaphore(MAX_CONCURRENT_JOBS)


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #


def require_auth(credentials: HTTPBasicCredentials | None = Depends(security)) -> None:
    """HTTP Basic, when APP_PASSWORD is set. Otherwise open, and the page says so."""
    if not APP_PASSWORD:
        return
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
    # compare_digest on both halves: a plain == leaks length and content by timing.
    ok = secrets.compare_digest(credentials.username, APP_USERNAME) & secrets.compare_digest(
        credentials.password, APP_PASSWORD
    )
    if not ok:
        raise HTTPException(
            status_code=401,
            detail="invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


# --------------------------------------------------------------------------- #
# jobs
# --------------------------------------------------------------------------- #


@dataclass
class Job:
    id: str
    status: str = "queued"
    message: str = "queued"
    company: str | None = None
    coverage: str | None = None
    deck_ratio: float | None = None
    summary: list[dict[str, Any]] = field(default_factory=list)
    break_even: dict[str, str] = field(default_factory=dict)
    warnings: int = 0
    observations: list[str] = field(default_factory=list)
    deck_gap: dict[str, Any] | None = None
    delivery: dict[str, Any] | None = None
    error: str | None = None
    remedy: str | None = None
    onepager: Path | None = None
    workbook: Path | None = None
    directory: Path | None = None
    created: datetime = field(default_factory=lambda: datetime.now(UTC))

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "message": self.message,
            "company": self.company,
            "coverage": self.coverage,
            "deck_ratio": self.deck_ratio,
            "summary": self.summary,
            "break_even": self.break_even,
            "warnings": self.warnings,
            "observations": self.observations,
            "deck_gap": self.deck_gap,
            "delivery": self.delivery,
            "error": self.error,
            "remedy": self.remedy,
            "onepager_url": f"/download/{self.id}/onepager" if self.onepager else None,
            "workbook_url": f"/download/{self.id}/workbook" if self.workbook else None,
        }


JOBS: dict[str, Job] = {}


def _sweep() -> None:
    """Drop expired jobs and their files. Uploaded decks are confidential."""
    cutoff = datetime.now(UTC) - timedelta(minutes=JOB_TTL_MINUTES)
    for job_id, job in list(JOBS.items()):
        if job.created < cutoff:
            if job.directory and job.directory.exists():
                shutil.rmtree(job.directory, ignore_errors=True)
            JOBS.pop(job_id, None)


async def _run_job(job: Job, deck: Path, out_dir: Path, years: int, strict: bool) -> None:
    settings = load_settings(years=years)

    def progress(message: str) -> None:
        job.message = message

    async with _slots:
        job.status = "running"
        try:
            result = await asyncio.to_thread(
                pipeline.run,
                deck,
                out_dir,
                settings=settings,
                strict=strict,
                progress=progress,
            )
        except PitchdeckCFOError as exc:
            job.status = "failed"
            job.error = exc.message
            job.remedy = exc.remedy
            log.warning("job %s failed: %s", job.id, exc.message)
            return
        except Exception as exc:  # the browser needs something useful, not a 500
            job.status = "failed"
            job.error = "The build failed unexpectedly."
            job.remedy = f"{type(exc).__name__}: {exc}"
            log.exception("job %s crashed", job.id)
            return

        model = result.model
        job.company = result.assumptions.company.name
        job.coverage = result.coverage_line
        job.deck_ratio = result.assumptions.coverage.deck_ratio
        job.warnings = result.assumptions.grounding_warning_count
        job.break_even = model.break_even
        job.summary = [
            {
                "line": line,
                "values": [round(v) for v in model.pnl_annual[line]],
            }
            for line in ("Revenue", "Gross Profit", "EBITDA", "Net Income")
        ] + [{"line": "Ending cash", "values": [round(v) for v in model.ending_cash]}]
        # The analytical payload: the same observations the workbook's Summary
        # carries, so the browser and the file say the same thing.
        from pitchdeck_cfo.render.plan import observations

        job.observations = list(observations(model))

        # The single most useful thing on the page when it applies: the company's own
        # forecast against this model's.
        claim = model.deck_final_year_revenue
        if claim and model.final_year_revenue > 0:
            ratio = claim / model.final_year_revenue
            if ratio > 1.6 or ratio < 0.6:
                job.deck_gap = {
                    "deck": round(claim),
                    "model": round(model.final_year_revenue),
                    "ratio": round(ratio, 1),
                }

        if result.delivery is not None:
            job.delivery = {"sent": result.delivery.sent, "detail": result.delivery.detail}

        job.onepager = result.onepager_path
        job.workbook = result.workbook_path
        job.status = "done"
        job.message = "done"
        log.info("job %s built %s", job.id, job.company)


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    """Browsers request this path at the root whatever the HTML says."""
    return FileResponse(
        STATIC_DIR / "favicon.ico",
        media_type="image/x-icon",
        headers={"Cache-Control": "public, max-age=604800"},
    )


@app.get("/site.webmanifest", include_in_schema=False)
async def manifest() -> JSONResponse:
    """Names and colours the app when someone installs or pins it."""
    return JSONResponse(
        {
            "name": "TEN Capital — Deck to Financial Model",
            "short_name": "Deck Analyzer",
            "icons": [
                {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
                {
                    "src": "/static/icon-maskable-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "maskable",
                },
            ],
            "theme_color": "#0B1526",
            "background_color": "#0B1526",
            "display": "standalone",
            "start_url": "/",
        },
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    """Railway's health check. Deliberately unauthenticated and cheap."""
    return {
        "status": "ok",
        "version": MODEL_VERSION,
        "credential": load_settings().has_credential,
        "protected": bool(APP_PASSWORD),
    }


def _delivery_note() -> str:
    """What happens to a build after it is made.

    Derived from configuration rather than written by hand. Someone uploading a
    confidential deck is entitled to know it will be forwarded, and the page must not
    be able to drift out of step with whether it actually is.
    """
    settings = load_settings()
    if not settings.email_enabled:
        return "Results are not emailed anywhere."
    return (
        f"A copy of every completed model, with both documents attached, is emailed "
        f"to {settings.email_to}."
    )


@app.get("/", response_class=HTMLResponse)
async def index(_: None = Depends(require_auth)) -> HTMLResponse:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    # The page states the deployment's actual posture rather than a generic promise.
    disclosure = (
        "This service is password-protected."
        if APP_PASSWORD
        else "⚠ This service is OPEN — anyone with the URL can upload a deck and "
        "spend the API key it runs on. Set APP_PASSWORD to close it."
    )
    return HTMLResponse(
        html.replace("{{V}}", ASSET_VERSION)
        .replace("{{DISCLOSURE}}", disclosure)
        .replace("{{DELIVERY}}", _delivery_note())
        .replace("{{VERSION}}", MODEL_VERSION)
        .replace("{{MAX_MB}}", str(MAX_UPLOAD_MB))
        .replace("{{TTL}}", str(JOB_TTL_MINUTES))
    )


@app.post("/build")
async def start_build(
    background: BackgroundTasks,
    request: Request,
    deck: UploadFile = File(...),
    _: None = Depends(require_auth),
) -> JSONResponse:
    _sweep()

    form = await request.form()
    years = max(3, min(7, int(str(form.get("years", "5")))))
    strict = str(form.get("strict", "")).lower() in ("1", "true", "on", "yes")

    name = Path(deck.filename or "deck")
    if name.suffix.lower() not in SUPPORTED:
        raise HTTPException(400, f"Expected a .pdf or .pptx deck, got '{name.suffix}'.")

    payload = await deck.read()
    if len(payload) > MAX_UPLOAD_MB * 1_000_000:
        raise HTTPException(413, f"That deck is larger than the {MAX_UPLOAD_MB} MB limit.")
    if not payload:
        raise HTTPException(400, "That file is empty.")

    job = Job(id=uuid.uuid4().hex[:12])
    directory = Path(tempfile.mkdtemp(prefix=f"pdcfo_{job.id}_"))
    job.directory = directory
    deck_path = directory / name.name
    deck_path.write_bytes(payload)

    JOBS[job.id] = job
    background.add_task(_run_job, job, deck_path, directory / "out", years, strict)
    return JSONResponse({"id": job.id})


@app.get("/status/{job_id}")
async def status(job_id: str, _: None = Depends(require_auth)) -> JSONResponse:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job. It may have expired.")
    return JSONResponse(job.public())


@app.get("/download/{job_id}/{artifact}")
async def download(job_id: str, artifact: str, _: None = Depends(require_auth)) -> FileResponse:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job. It may have expired.")
    path = {"onepager": job.onepager, "workbook": job.workbook}.get(artifact)
    if path is None or not path.exists():
        raise HTTPException(404, "That artifact is not ready.")
    return FileResponse(path, filename=path.name)


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=exc.headers)
