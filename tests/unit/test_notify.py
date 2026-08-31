"""Email delivery. The transport is substituted, so nothing here sends anything.

Two properties matter more than the formatting: a failed email must never take a
successful build down with it, and nothing may be promised that is not configured.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from pitchdeck_cfo import notify
from pitchdeck_cfo.config import Settings, load_settings
from pitchdeck_cfo.model import build
from tests import model_factories as mf


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "resend_api_key": "re_test_key",
        "email_to": "Info@tencapital.group",
        "email_from": "pitchdeck-cfo@tencapital.group",
    }
    base.update(overrides)
    return load_settings(**base)


@pytest.fixture
def model() -> Any:
    return build(
        mf.assumptions(
            "saas",
            revenue=mf.saas_revenue(new_logo_growth_monthly_pct=mf.s(3.0)),
            financing=mf.financing(cash_on_hand=mf.s(1_000_000)),
            opex=mf.opex(ga_fixed_annual=mf.s(180_000)),
        )
    )


@pytest.fixture
def artifacts(tmp_path: Path) -> list[Path]:
    pdf = tmp_path / "Testco_financial_onepager.pdf"
    xlsx = tmp_path / "Testco_financial_model.xlsx"
    pdf.write_bytes(b"%PDF-1.4 one-pager")
    xlsx.write_bytes(b"PK workbook")
    return [pdf, xlsx]


class Recorder:
    """Stands in for the network and keeps what it was asked to send."""

    def __init__(self, outcome: notify.Delivery | None = None) -> None:
        self.body: dict[str, Any] | None = None
        self.key: str | None = None
        self.outcome = outcome or notify.Delivery(True, "sent", "msg_1")

    def __call__(self, body: dict[str, Any], key: str) -> notify.Delivery:
        self.body, self.key = body, key
        return self.outcome


class TestConfigurationGate:
    """Nothing is promised that is not configured."""

    def test_no_key_means_no_email_and_no_claim(self, model: Any, artifacts: list[Path]) -> None:
        settings = _settings(resend_api_key=None)
        assert settings.email_enabled is False
        delivery = notify.send(model, artifacts, settings=settings, transport=Recorder())
        assert delivery.sent is False
        assert "not configured" in delivery.detail

    def test_no_recipient_means_no_email(self, model: Any, artifacts: list[Path]) -> None:
        settings = Settings(resend_api_key="re_test", email_to="")
        assert settings.email_enabled is False
        assert notify.send(model, artifacts, settings=settings).sent is False

    def test_a_key_and_a_recipient_enable_it(self) -> None:
        assert _settings().email_enabled is True

    def test_resend_variables_are_read_without_the_project_prefix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # These are the names Resend's docs and every hosting dashboard use.
        monkeypatch.setenv("RESEND_API_KEY", "re_from_env")
        monkeypatch.setenv("EMAIL_TO", "someone@example.com")
        settings = load_settings()
        assert settings.resend_api_key == "re_from_env"
        assert settings.email_to == "someone@example.com"

    def test_an_explicit_argument_still_wins_over_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EMAIL_TO", "env@example.com")
        assert load_settings(email_to="explicit@example.com").email_to == "explicit@example.com"


class TestMessage:
    def test_the_subject_names_the_company_and_the_coverage(self, model: Any) -> None:
        line = notify.subject(model)
        assert "Testco" in line
        assert "%" in line
        assert "from the deck" in line

    def test_both_artifacts_are_attached_base64(self, model: Any, artifacts: list[Path]) -> None:
        recorder = Recorder()
        notify.send(model, artifacts, settings=_settings(), transport=recorder)
        assert recorder.body is not None

        names = [a["filename"] for a in recorder.body["attachments"]]
        assert names == ["Testco_financial_onepager.pdf", "Testco_financial_model.xlsx"]
        decoded = base64.b64decode(recorder.body["attachments"][0]["content"])
        assert decoded == b"%PDF-1.4 one-pager"

    def test_the_recipient_list_is_split_on_commas(self, model: Any, artifacts: list[Path]) -> None:
        recorder = Recorder()
        notify.send(
            model,
            artifacts,
            settings=_settings(email_to="a@x.com, b@y.com"),
            transport=recorder,
        )
        assert recorder.body is not None
        assert recorder.body["to"] == ["a@x.com", "b@y.com"]

    def test_a_plain_text_alternative_is_always_included(
        self, model: Any, artifacts: list[Path]
    ) -> None:
        recorder = Recorder()
        notify.send(model, artifacts, settings=_settings(), transport=recorder)
        assert recorder.body is not None
        assert recorder.body["text"].startswith("Testco")
        assert "Revenue" in recorder.body["text"]

    def test_the_body_carries_the_numbers_and_the_disclaimer(self, model: Any) -> None:
        html = notify.html_body(model)
        assert "Testco" in html
        assert "EBITDA" in html
        assert "from the deck itself" in html
        assert "Not audited financial information" in html

    def test_the_body_uses_inline_styles_only(self, model: Any) -> None:
        # Email clients strip stylesheets and block web fonts.
        html = notify.html_body(model)
        assert "<style" not in html
        assert "fonts.googleapis" not in html

    def test_a_company_name_with_markup_in_it_is_escaped(self, tmp_path: Path) -> None:
        model = build(
            mf.assumptions("saas", company=mf.company("saas", name="<script>alert(1)</script> Ltd"))
        )
        assert "<script>" not in notify.html_body(model)
        assert "&lt;script&gt;" in notify.html_body(model)

    def test_the_deck_versus_model_gap_appears_when_it_is_large(self) -> None:
        base = mf.assumptions("saas")
        model = build(
            base.model_copy(update={"deck_revenue_projection": (("2030", 500_000_000.0),)})
        )
        assert "Deck vs model" in notify.html_body(model)

    def test_no_gap_note_when_the_projection_is_close(self) -> None:
        plain = build(mf.assumptions("saas"))
        model = build(
            plain.assumptions.model_copy(
                update={"deck_revenue_projection": (("2030", plain.final_year_revenue),)}
            )
        )
        assert "Deck vs model" not in notify.html_body(model)


class TestFailureNeverCostsTheBuild:
    """The artifacts already exist by the time this runs."""

    def test_a_transport_failure_is_reported_not_raised(
        self, model: Any, artifacts: list[Path]
    ) -> None:
        failing = Recorder(notify.Delivery(False, "Resend returned 422: bad from address"))
        delivery = notify.send(model, artifacts, settings=_settings(), transport=failing)
        assert delivery.sent is False
        assert "422" in delivery.detail

    def test_an_unexpected_exception_is_swallowed_and_described(
        self, model: Any, artifacts: list[Path]
    ) -> None:
        def explode(body: dict[str, Any], key: str) -> notify.Delivery:
            raise RuntimeError("the network is on fire")

        delivery = notify.send(model, artifacts, settings=_settings(), transport=explode)
        assert delivery.sent is False
        assert "the network is on fire" in delivery.detail

    def test_a_missing_artifact_is_skipped_rather_than_fatal(
        self, model: Any, tmp_path: Path
    ) -> None:
        recorder = Recorder()
        present = tmp_path / "there.pdf"
        present.write_bytes(b"%PDF")
        delivery = notify.send(
            model,
            [present, tmp_path / "gone.xlsx"],
            settings=_settings(),
            transport=recorder,
        )
        assert delivery.sent is True
        assert recorder.body is not None
        assert [a["filename"] for a in recorder.body["attachments"]] == ["there.pdf"]

    def test_oversized_attachments_are_refused_without_sending(
        self, model: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(notify, "MAX_ATTACHMENT_BYTES", 10)
        big = tmp_path / "big.pdf"
        big.write_bytes(b"x" * 100)
        recorder = Recorder()
        delivery = notify.send(model, [big], settings=_settings(), transport=recorder)
        assert delivery.sent is False
        assert "above the" in delivery.detail
        assert recorder.body is None, "nothing should have been sent"


class TestPipelineWiring:
    """The pipeline emails last, and only when asked and configured."""

    def _run(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **kwargs: Any) -> Any:
        from pitchdeck_cfo import pipeline

        model = build(mf.assumptions("saas"))
        sent: list[Any] = []

        monkeypatch.setattr(pipeline, "load_deck", lambda *a, **k: object())
        monkeypatch.setattr(pipeline, "extract_facts", lambda *a, **k: object())
        monkeypatch.setattr(pipeline, "resolve", lambda *a, **k: model.assumptions)
        monkeypatch.setattr(pipeline, "build", lambda *a, **k: model)

        def stub_write(payload: bytes) -> Any:
            # The real renderers create the output directory; the stubs must too.
            def write(_model: Any, path: Path) -> Path:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                return path

            return write

        monkeypatch.setattr(pipeline.workbook, "write", stub_write(b"PK"))
        monkeypatch.setattr(pipeline.onepager, "write", stub_write(b"%PDF"))

        def record(model_arg: Any, paths: list[Path], **kw: Any) -> notify.Delivery:
            sent.append(paths)
            return notify.Delivery(True, "sent", "msg_1")

        monkeypatch.setattr(pipeline, "send", record)
        deck = tmp_path / "deck.pptx"
        deck.write_bytes(b"x")
        result = pipeline.run(deck, tmp_path / "out", settings=_settings(), **kwargs)
        return result, sent

    def test_a_configured_build_is_emailed_with_both_artifacts(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        result, sent = self._run(monkeypatch, tmp_path)
        assert result.delivery is not None and result.delivery.sent
        # Emailed only after both files exist on disk.
        assert len(sent) == 1
        assert all(path.exists() for path in sent[0])

    def test_no_email_flag_suppresses_it(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        result, sent = self._run(monkeypatch, tmp_path, email=False)
        assert result.delivery is None
        assert sent == []
