"""The API client and the two-pass orchestrator, against a stub -- never the network."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from pitchdeck_cfo.config import load_settings
from pitchdeck_cfo.errors import ExtractionValidationError, MissingCredentialError
from pitchdeck_cfo.extract import extract_facts
from pitchdeck_cfo.extract.cache import ExtractionCache, cache_key, file_sha256
from pitchdeck_cfo.extract.client import ExtractionClient
from pitchdeck_cfo.extract.schema import ALL_FINANCIAL_METRICS, CompanyProfile, FinancialFacts
from pitchdeck_cfo.ingest.base import DeckDocument, TextBlock
from tests import factories


@dataclass
class StubUsage:
    input_tokens: int = 100
    output_tokens: int = 50
    cache_read_input_tokens: int = 0


class StubResponse:
    def __init__(self, parsed: Any) -> None:
        self.parsed_output = parsed
        self.usage = StubUsage()


class StubMessages:
    """Returns queued results in order. A result may be an exception to raise."""

    def __init__(self, results: list[Any]) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> StubResponse:
        self.calls.append(kwargs)
        if not self.results:
            raise AssertionError("the client made more calls than the test queued")
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return StubResponse(result)


class StubAnthropic:
    def __init__(self, results: list[Any]) -> None:
        self.messages = StubMessages(results)


def _client(results: list[Any]) -> tuple[ExtractionClient, StubAnthropic]:
    stub = StubAnthropic(results)
    return ExtractionClient(settings=load_settings(), _client=stub), stub


def _validation_error() -> ValidationError:
    try:
        CompanyProfile.model_validate({})
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a validation error")


class TestRequestShape:
    def test_sends_the_configured_model_and_effort(self) -> None:
        client, stub = _client([factories.profile()])
        client.extract(system="S", user="U", output_format=CompanyProfile)

        (call,) = stub.messages.calls
        assert call["model"] == "claude-opus-5"
        assert call["output_config"] == {"effort": "high"}
        assert call["output_format"] is CompanyProfile

    def test_uses_adaptive_thinking_not_a_token_budget(self) -> None:
        # budget_tokens is rejected outright on this model generation.
        client, stub = _client([factories.profile()])
        client.extract(system="S", user="U", output_format=CompanyProfile)

        thinking = stub.messages.calls[0]["thinking"]
        assert thinking == {"type": "adaptive"}
        assert "budget_tokens" not in thinking

    def test_caches_the_system_prefix(self) -> None:
        # The deck is a long prefix reused by both passes; caching it is most of the
        # saving on the second call.
        client, stub = _client([factories.profile()])
        client.extract(system="S", user="U", output_format=CompanyProfile)

        system = stub.messages.calls[0]["system"]
        assert system[0]["cache_control"] == {"type": "ephemeral"}

    def test_does_not_prefill_the_assistant_turn(self) -> None:
        # A trailing assistant message is rejected on current models.
        client, stub = _client([factories.profile()])
        client.extract(system="S", user="U", output_format=CompanyProfile)

        assert stub.messages.calls[0]["messages"][-1]["role"] == "user"


class TestRetry:
    def test_retries_once_with_the_error_fed_back(self) -> None:
        client, stub = _client([_validation_error(), factories.profile()])
        client.extract(system="S", user="U", output_format=CompanyProfile)

        assert len(stub.messages.calls) == 2
        retry_messages = stub.messages.calls[1]["messages"]
        assert retry_messages[-1]["role"] == "user"
        assert "did not validate" in retry_messages[-1]["content"]
        assert client.usage.retries == 1

    def test_retry_forbids_inventing_a_value_to_satisfy_the_schema(self) -> None:
        client, stub = _client([_validation_error(), factories.profile()])
        client.extract(system="S", user="U", output_format=CompanyProfile)

        # The prompt is hard-wrapped, so compare on collapsed whitespace.
        instruction = " ".join(stub.messages.calls[1]["messages"][-1]["content"].split())
        assert "If a field was null it must stay null" in instruction
        assert "fix only the structural problem" in instruction

    def test_a_second_failure_raises_rather_than_returning_partial_data(self) -> None:
        client, _ = _client([_validation_error(), _validation_error()])
        with pytest.raises(ExtractionValidationError) as exc:
            client.extract(system="S", user="U", output_format=CompanyProfile)
        assert "--verbose" in exc.value.remedy

    def test_a_null_parsed_output_counts_as_a_failure(self) -> None:
        client, _ = _client([None, None])
        with pytest.raises(ExtractionValidationError):
            client.extract(system="S", user="U", output_format=CompanyProfile)


class TestUsageAccounting:
    def test_accumulates_across_calls_including_the_retry(self) -> None:
        client, _ = _client([_validation_error(), factories.profile()])
        client.extract(system="S", user="U", output_format=CompanyProfile)

        assert client.usage.calls == 1  # only the successful call reports usage
        assert client.usage.retries == 1
        assert client.usage.output_tokens == 50
        assert "retry" in client.usage.summary()


class TestCredentialCheck:
    def test_missing_credential_is_actionable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.setattr(Path, "is_dir", lambda self: False)

        with pytest.raises(MissingCredentialError) as exc:
            ExtractionClient(settings=load_settings())
        assert "ANTHROPIC_API_KEY" in exc.value.remedy


class TestCache:
    def test_key_changes_with_model_and_prompt_version(self) -> None:
        base = {"deck_sha256": "abc", "prompt_version": "1", "model": "m", "pass_name": "profile"}
        assert cache_key(**base) != cache_key(**{**base, "model": "other"})
        assert cache_key(**base) != cache_key(**{**base, "prompt_version": "2"})
        assert cache_key(**base) != cache_key(**{**base, "pass_name": "financials"})

    def test_hash_follows_content_not_filename(self, tmp_path: Path) -> None:
        a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
        a.write_bytes(b"same"), b.write_bytes(b"same")
        assert file_sha256(a) == file_sha256(b)

    def test_round_trip(self, tmp_path: Path) -> None:
        cache = ExtractionCache(tmp_path)
        cache.store("k", factories.profile(company_name=factories.cited("Acme")))
        loaded = cache.load("k", CompanyProfile)
        assert loaded is not None
        assert loaded.company_name is not None
        assert loaded.company_name.value == "Acme"

    def test_corrupt_entry_is_a_miss_not_an_error(self, tmp_path: Path) -> None:
        # The cache is an optimisation. Failing a run because of one is worse than
        # paying for the call again.
        (tmp_path / "k.json").write_text("{not json", encoding="utf-8")
        assert ExtractionCache(tmp_path).load("k", CompanyProfile) is None

    def test_schema_incompatible_entry_is_a_miss(self, tmp_path: Path) -> None:
        (tmp_path / "k.json").write_text('{"stale": true}', encoding="utf-8")
        assert ExtractionCache(tmp_path).load("k", CompanyProfile) is None

    def test_disabled_cache_neither_reads_nor_writes(self, tmp_path: Path) -> None:
        cache = ExtractionCache(tmp_path, enabled=False)
        cache.store("k", factories.profile())
        assert not list(tmp_path.glob("*.json"))
        assert cache.load("k", CompanyProfile) is None


def _deck(tmp_path: Path) -> DeckDocument:
    path = tmp_path / "deck.pptx"
    path.write_bytes(b"deck bytes")
    return DeckDocument(
        path=path,
        kind="pptx",
        page_count=1,
        blocks=(TextBlock(index=0, page=1, text="Acme raises a seed round of four million"),),
    )


class TestTwoPassOrchestration:
    def test_runs_profile_then_financials(self, tmp_path: Path) -> None:
        client, stub = _client([factories.profile(), factories.financials()])
        facts = extract_facts(
            _deck(tmp_path),
            settings=load_settings(),
            client=client,
            cache=ExtractionCache(tmp_path / "cache", enabled=False),
        )
        assert len(stub.messages.calls) == 2
        assert stub.messages.calls[0]["output_format"] is CompanyProfile
        assert stub.messages.calls[1]["output_format"] is FinancialFacts
        assert facts.model == "claude-opus-5"

    def test_business_model_from_pass_a_is_given_to_pass_b(self, tmp_path: Path) -> None:
        client, stub = _client(
            [
                factories.profile(business_model=factories.cited("saas")),
                factories.financials(),
            ]
        )
        extract_facts(
            _deck(tmp_path),
            settings=load_settings(),
            client=client,
            cache=ExtractionCache(tmp_path / "cache", enabled=False),
        )
        assert "saas" in stub.messages.calls[1]["messages"][0]["content"]

    def test_a_cached_pass_makes_no_call(self, tmp_path: Path) -> None:
        cache = ExtractionCache(tmp_path / "cache")
        document = _deck(tmp_path)
        settings = load_settings()

        client, stub = _client([factories.profile(), factories.financials()])
        extract_facts(document, settings=settings, client=client, cache=cache)
        assert len(stub.messages.calls) == 2

        client2, stub2 = _client([])
        extract_facts(document, settings=settings, client=client2, cache=cache)
        assert stub2.messages.calls == []

    def test_grounding_warnings_travel_with_the_facts(self, tmp_path: Path) -> None:
        client, _ = _client(
            [
                factories.profile(
                    company_name=factories.cited(
                        "Acme", citation="slide 1", quote="a figure that is not in this deck"
                    )
                ),
                factories.financials(),
            ]
        )
        facts = extract_facts(
            _deck(tmp_path),
            settings=load_settings(),
            client=client,
            cache=ExtractionCache(tmp_path / "cache", enabled=False),
        )
        assert not facts.is_grounded
        assert facts.grounding_warnings[0].reason == "quote_not_in_deck"

    def test_a_clean_extraction_reports_as_grounded(self, tmp_path: Path) -> None:
        client, _ = _client(
            [
                factories.profile(
                    company_name=factories.cited(
                        "Acme", citation="slide 1", quote="raises a seed round of four million"
                    )
                ),
                factories.financials(),
            ]
        )
        facts = extract_facts(
            _deck(tmp_path),
            settings=load_settings(),
            client=client,
            cache=ExtractionCache(tmp_path / "cache", enabled=False),
        )
        assert facts.is_grounded


class TestMetricCoverage:
    """Every metric must be accounted for -- the guarantee that replaced nullable fields."""

    def test_a_metric_left_out_of_both_lists_becomes_not_stated_and_is_recorded(self) -> None:
        facts = factories.financials(
            stated=[],
            not_stated=sorted(ALL_FINANCIAL_METRICS - {"cac_usd"}),
        )
        # Treating an unlisted metric as "not stated" is the safe direction, but the
        # omission is surfaced rather than hidden.
        assert "cac_usd" in facts.not_stated
        assert facts.omitted_metrics == ("cac_usd",)

    def test_a_fully_accounted_extraction_records_no_omission(self) -> None:
        facts = factories.financials(
            stated=[factories.metric("cac_usd", 4200.0)],
            not_stated=sorted(ALL_FINANCIAL_METRICS - {"cac_usd"}),
        )
        assert facts.omitted_metrics == ()

    def test_a_duplicated_metric_is_rejected(self) -> None:
        # Two values for one metric is genuinely ambiguous, so this one does raise.
        with pytest.raises(ValidationError, match="at most once"):
            factories.financials(
                stated=[
                    factories.metric("cac_usd", 4200.0),
                    factories.metric("cac_usd", 5100.0),
                ],
                not_stated=sorted(ALL_FINANCIAL_METRICS - {"cac_usd"}),
            )

    def test_get_returns_the_stated_fact_or_none(self) -> None:
        facts = factories.financials(
            stated=[factories.metric("gross_margin_pct", 71.0)],
            not_stated=sorted(ALL_FINANCIAL_METRICS - {"gross_margin_pct"}),
        )
        found = facts.get("gross_margin_pct")
        assert found is not None and found.value == 71.0
        assert facts.get("cac_usd") is None

    def test_metric_facts_are_grounded_like_any_other_value(self) -> None:
        from pitchdeck_cfo.extract import grounding

        facts = factories.financials(
            stated=[
                factories.metric(
                    "cac_usd", 4200.0, citation="slide 1", quote="a claim found nowhere in here"
                )
            ],
            not_stated=sorted(ALL_FINANCIAL_METRICS - {"cac_usd"}),
        )
        document = DeckDocument(
            path=Path("d.pptx"),
            kind="pptx",
            page_count=1,
            blocks=(TextBlock(index=0, page=1, text="something else entirely on this slide"),),
        )
        (warning,) = grounding.check(facts, document)
        assert warning.reason == "quote_not_in_deck"
        # The path names the metric rather than a list index.
        assert warning.field_path == "stated[cac_usd]"
