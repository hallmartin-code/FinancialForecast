"""The web app. No network: the pipeline is substituted, since it is tested elsewhere.

What matters here is the surface a browser touches -- rejection of bad uploads, the
auth gate, that a failure produces a remedy rather than a 500, and that uploaded
decks do not outlive their job.
"""

from __future__ import annotations

import importlib
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pitchdeck_cfo.errors import InsufficientCoverageError
from pitchdeck_cfo.model import build
from tests import model_factories as mf


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A fresh app module with the pipeline stubbed out."""
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    module = importlib.import_module("app")
    importlib.reload(module)

    def fake_run(deck: Path, out_dir: Path, **kwargs: Any) -> Any:
        from pitchdeck_cfo.pipeline import BuildResult

        model = build(mf.assumptions("saas"))
        out_dir.mkdir(parents=True, exist_ok=True)
        onepager = out_dir / "Testco_financial_onepager.pdf"
        workbook = out_dir / "Testco_financial_model.xlsx"
        onepager.write_bytes(b"%PDF-1.4 stub")
        workbook.write_bytes(b"PK stub")
        return BuildResult(
            facts=None,  # type: ignore[arg-type]
            assumptions=model.assumptions,
            model=model,
            onepager_path=onepager,
            workbook_path=workbook,
        )

    monkeypatch.setattr(module.pipeline, "run", fake_run)
    return module


@pytest.fixture
def client(web: Any) -> TestClient:
    return TestClient(web.app)


def _deck(name: str = "deck.pptx", payload: bytes = b"x" * 500) -> dict[str, Any]:
    return {"deck": (name, payload)}


def _finish(client: TestClient, job_id: str) -> dict[str, Any]:
    """TestClient runs background tasks synchronously, so one poll is enough."""
    response = client.get(f"/status/{job_id}")
    assert response.status_code == 200
    return dict(response.json())


class TestHealth:
    def test_healthz_is_cheap_and_unauthenticated(self, client: TestClient) -> None:
        # Railway hits this before the service is reachable; auth would fail the deploy.
        body = client.get("/healthz").json()
        assert body["status"] == "ok"
        assert "version" in body

    def test_healthz_reports_whether_a_credential_is_present(self, client: TestClient) -> None:
        assert "credential" in client.get("/healthz").json()


class TestUploadValidation:
    def test_a_non_deck_is_rejected_by_extension(self, client: TestClient) -> None:
        response = client.post("/build", files=_deck("notes.txt"))
        assert response.status_code == 400
        assert ".txt" in response.json()["error"]

    def test_an_empty_file_is_rejected(self, client: TestClient) -> None:
        response = client.post("/build", files=_deck("d.pptx", b""))
        assert response.status_code == 400
        assert "empty" in response.json()["error"]

    def test_an_oversized_upload_is_rejected(
        self, client: TestClient, web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(web, "MAX_UPLOAD_MB", 1)
        response = client.post("/build", files=_deck("d.pdf", b"x" * 2_000_000))
        assert response.status_code == 413
        assert "larger than" in response.json()["error"]

    @pytest.mark.parametrize("name", ["deck.pdf", "deck.PDF", "deck.pptx", "deck.PPTX"])
    def test_supported_extensions_are_accepted_case_insensitively(
        self, client: TestClient, name: str
    ) -> None:
        assert client.post("/build", files=_deck(name)).status_code == 200


class TestBuildFlow:
    def test_a_build_returns_the_summary_and_both_artifacts(self, client: TestClient) -> None:
        job_id = client.post("/build", files=_deck()).json()["id"]
        job = _finish(client, job_id)

        assert job["status"] == "done"
        assert job["company"] == "Testco"
        assert "core inputs sourced from the deck" in job["coverage"]
        assert [row["line"] for row in job["summary"]][:2] == ["Revenue", "Gross Profit"]
        assert job["break_even"]

        for url in (job["onepager_url"], job["workbook_url"]):
            download = client.get(url)
            assert download.status_code == 200
            assert "attachment" in download.headers["content-disposition"]

    def test_the_horizon_is_clamped_to_a_supported_range(
        self, client: TestClient, web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def capture(deck: Path, out_dir: Path, **kwargs: Any) -> Any:
            seen["years"] = kwargs["settings"].years
            raise InsufficientCoverageError(1, 8, 0.4)

        monkeypatch.setattr(web.pipeline, "run", capture)
        job_id = client.post("/build", files=_deck(), data={"years": "99"}).json()["id"]
        _finish(client, job_id)
        assert seen["years"] == 7

    def test_a_pipeline_failure_becomes_a_remedy_not_a_500(
        self, client: TestClient, web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(deck: Path, out_dir: Path, **kwargs: Any) -> Any:
            raise InsufficientCoverageError(1, 8, 0.4)

        monkeypatch.setattr(web.pipeline, "run", refuse)
        job_id = client.post("/build", files=_deck(), data={"strict": "on"}).json()["id"]
        job = _finish(client, job_id)

        assert job["status"] == "failed"
        assert "below the 40% threshold" in job["error"]
        assert "--strict" in job["remedy"] or "re-run" in job["remedy"].lower()

    def test_an_unexpected_crash_still_answers_the_browser(
        self, client: TestClient, web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode(deck: Path, out_dir: Path, **kwargs: Any) -> Any:
            raise RuntimeError("something nobody anticipated")

        monkeypatch.setattr(web.pipeline, "run", explode)
        job = _finish(client, client.post("/build", files=_deck()).json()["id"])
        assert job["status"] == "failed"
        assert "unexpectedly" in job["error"]
        assert "something nobody anticipated" in job["remedy"]

    def test_an_unknown_job_is_a_404(self, client: TestClient) -> None:
        assert client.get("/status/deadbeef").status_code == 404
        assert client.get("/download/deadbeef/onepager").status_code == 404


class TestAuth:
    def test_open_by_default_and_the_page_says_so(self, client: TestClient) -> None:
        # A deployment that quietly spends the owner's key is the failure to avoid.
        page = client.get("/")
        assert page.status_code == 200
        assert "OPEN" in page.text

    def test_a_password_closes_every_route_except_health(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("APP_PASSWORD", "hunter2")
        monkeypatch.setenv("APP_USERNAME", "ten")
        module = importlib.import_module("app")
        importlib.reload(module)
        guarded = TestClient(module.app)

        assert guarded.get("/healthz").status_code == 200
        assert guarded.get("/").status_code == 401
        assert guarded.post("/build", files=_deck()).status_code == 401

        page = guarded.get("/", auth=("ten", "hunter2"))
        assert page.status_code == 200
        assert "password-protected" in page.text

        assert guarded.get("/", auth=("ten", "wrong")).status_code == 401
        assert guarded.get("/", auth=("someone", "hunter2")).status_code == 401


class TestRetention:
    def test_expired_jobs_and_their_uploads_are_deleted(
        self, client: TestClient, web: Any, tmp_path: Path
    ) -> None:
        """An uploaded deck is confidential and must not linger on the server."""
        directory = tmp_path / "old"
        directory.mkdir()
        (directory / "deck.pptx").write_bytes(b"confidential")

        stale = web.Job(id="stale", directory=directory)
        stale.created = datetime.now(UTC) - timedelta(minutes=web.JOB_TTL_MINUTES + 5)
        web.JOBS["stale"] = stale

        client.post("/build", files=_deck())  # any request sweeps

        assert "stale" not in web.JOBS
        assert not directory.exists()

    def test_a_fresh_job_survives_the_sweep(self, client: TestClient, web: Any) -> None:
        job_id = client.post("/build", files=_deck()).json()["id"]
        client.post("/build", files=_deck())
        assert job_id in web.JOBS
        shutil.rmtree(web.JOBS[job_id].directory, ignore_errors=True)
