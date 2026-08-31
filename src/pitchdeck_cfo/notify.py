"""Email the finished model, via Resend.

Two rules shape this module.

**A failed email never fails a build.** The artifacts are already written by the time
this runs. Losing a model because a mail API was briefly unreachable would be absurd,
so every path here returns a `Delivery` describing what happened and nothing raises.

**Nothing is promised that is not configured.** Delivery only happens when a key and a
recipient are both set. The web page and the CLI read the same `Settings`, so what the
page tells someone uploading a confidential deck is derived from what the service will
actually do with it, rather than written by hand and left to drift.

The transport is injectable, so the tests exercise the whole body-and-attachment build
without sending anything.
"""

from __future__ import annotations

import base64
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from pitchdeck_cfo.config import Settings
from pitchdeck_cfo.model.build import FinancialModel
from pitchdeck_cfo.render.plan import observations

log = logging.getLogger("pitchdeck-cfo.notify")

RESEND_ENDPOINT = "https://api.resend.com/emails"
TIMEOUT_SECONDS = 30.0

# Resend caps total attachment size at 40 MB. A one-pager and a workbook come to a few
# hundred kilobytes, so this only ever guards against something having gone wrong.
MAX_ATTACHMENT_BYTES = 35_000_000

Transport = Callable[[dict[str, Any], str], "Delivery"]


@dataclass(frozen=True)
class Delivery:
    """What happened. `sent` false is a normal outcome, not an exception."""

    sent: bool
    detail: str
    message_id: str | None = None

    @classmethod
    def skipped(cls, why: str) -> Delivery:
        return cls(False, why)


# --------------------------------------------------------------------------- #
# formatting
# --------------------------------------------------------------------------- #

_INK = "#101E33"
_MUTED = "#5C6E86"
_RULE = "#DCE3EC"
_CORAL = "#C7452F"
_TEAL = "#1E8C8A"


def _money(value: float) -> str:
    """Whole dollars, negatives in parentheses, as the documents render them."""
    text = f"{abs(value):,.0f}"
    return f"({text})" if value < 0 else text


def _compact(value: float) -> str:
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if magnitude >= 1_000_000:
        return f"{sign}${magnitude / 1_000_000:,.1f}M"
    if magnitude >= 1_000:
        return f"{sign}${magnitude / 1_000:,.0f}K"
    return f"{sign}${magnitude:,.0f}"


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def subject(model: FinancialModel) -> str:
    company = model.assumptions.company.name
    coverage = model.assumptions.coverage
    return f"Financial model — {company} · {coverage.deck_ratio:.0%} of core inputs from the deck"


def _table(model: FinancialModel) -> str:
    years = model.year_labels
    lines = [
        ("Revenue", model.pnl_annual["Revenue"], True),
        ("Gross profit", model.pnl_annual["Gross Profit"], False),
        ("EBITDA", model.pnl_annual["EBITDA"], True),
        ("Net income", model.pnl_annual["Net Income"], False),
        ("Ending cash", model.ending_cash, False),
    ]
    header = "".join(
        f'<th align="right" style="padding:6px 8px;border-bottom:1px solid {_RULE};'
        f"font:600 11px/1.4 Helvetica,Arial,sans-serif;color:{_MUTED};"
        f'text-transform:uppercase;letter-spacing:.08em">{year}</th>'
        for year in years
    )
    body = ""
    for label, values, strong in lines:
        weight = "600" if strong else "400"
        cells = "".join(
            f'<td align="right" style="padding:6px 8px;border-bottom:1px solid {_RULE};'
            f"font:{weight} 13px/1.4 Helvetica,Arial,sans-serif;"
            f'color:{_CORAL if v < 0 else _INK}">{_money(v)}</td>'
            for v in values
        )
        body += (
            f'<tr><td style="padding:6px 8px;border-bottom:1px solid {_RULE};'
            f'font:{weight} 13px/1.4 Helvetica,Arial,sans-serif;color:{_INK}">'
            f"{_escape(label)}</td>{cells}</tr>"
        )
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" '
        'style="width:100%;border-collapse:collapse;margin:18px 0 4px">'
        f'<tr><th align="left" style="padding:6px 8px;border-bottom:1px solid {_RULE}"></th>'
        f"{header}</tr>{body}</table>"
    )


def html_body(model: FinancialModel) -> str:
    """The email a partner reads in their inbox.

    Inline styles and table layout throughout: email clients strip stylesheets, and a
    web font that fails to load is worse than one that was never asked for.
    """
    assumptions = model.assumptions
    company = assumptions.company
    coverage = assumptions.coverage

    gap = ""
    claim = model.deck_final_year_revenue
    if claim and model.final_year_revenue > 0:
        ratio = claim / model.final_year_revenue
        if ratio > 1.6 or ratio < 0.6:
            gap = (
                f'<div style="margin:18px 0;padding:14px 16px;border-radius:8px;'
                f'background:#FDF0ED;border:1px solid #F3C9C0">'
                f'<div style="font:600 11px/1.4 Helvetica,Arial,sans-serif;color:{_CORAL};'
                f'text-transform:uppercase;letter-spacing:.08em;margin-bottom:5px">'
                f"Deck vs model</div>"
                f'<div style="font:400 13px/1.6 Helvetica,Arial,sans-serif;color:{_INK}">'
                f"The company's own peak revenue projection of {_compact(claim)} is "
                f"{ratio:,.0f}× this model's {_compact(model.final_year_revenue)}. "  # noqa: RUF001 - a multiplication sign is what a reader expects
                f"The model uses labelled benchmarks wherever the deck stated no "
                f"driver — the gap is the question to put to the founder.</div></div>"
            )

    notes = ""
    found = observations(model)
    if found:
        items = "".join(f'<li style="margin-bottom:7px">{_escape(note)}</li>' for note in found)
        notes = (
            f'<div style="font:600 11px/1.4 Helvetica,Arial,sans-serif;color:{_MUTED};'
            f'text-transform:uppercase;letter-spacing:.08em;margin:22px 0 8px">'
            f"Key CFO observations</div>"
            f'<ol style="margin:0;padding-left:20px;font:400 13px/1.6 '
            f'Helvetica,Arial,sans-serif;color:{_INK}">{items}</ol>'
        )

    turns = "".join(
        f'<tr><td style="padding:3px 12px 3px 0;font:400 13px/1.5 '
        f'Helvetica,Arial,sans-serif;color:{_MUTED}">{_escape(label)}</td>'
        f'<td style="padding:3px 0;font:400 13px/1.5 Helvetica,Arial,sans-serif;'
        f'color:{_INK}">{_escape(value)}</td></tr>'
        for label, value in model.break_even.items()
    )

    warning = ""
    if assumptions.grounding_warning_count:
        warning = (
            f'<div style="margin-top:8px;font:400 12px/1.6 Helvetica,Arial,sans-serif;'
            f'color:{_CORAL}">{assumptions.grounding_warning_count} extracted quote(s) '
            f"could not be confirmed on the page cited.</div>"
        )

    return f"""\
<!doctype html>
<html><body style="margin:0;padding:0;background:#F4F6F9">
<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;background:#F4F6F9">
<tr><td align="center" style="padding:28px 12px">
<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;max-width:640px;background:#FFFFFF;border:1px solid {_RULE};border-radius:10px;overflow:hidden">

  <tr><td style="height:3px;background:linear-gradient(90deg,#EE5A4E,#F3A22A,#35BEBB);font-size:0;line-height:0">&nbsp;</td></tr>

  <tr><td style="padding:26px 28px 0">
    <div style="font:600 11px/1.4 Helvetica,Arial,sans-serif;color:{_TEAL};text-transform:uppercase;letter-spacing:.12em">
      Deck analyzer
    </div>
    <h1 style="margin:8px 0 4px;font:700 21px/1.3 Helvetica,Arial,sans-serif;color:{_INK}">
      {_escape(company.name)}
    </h1>
    <div style="font:400 13px/1.5 Helvetica,Arial,sans-serif;color:{_MUTED}">
      {_escape(company.business_model.replace("_", " "))} ·
      {len(model.year_labels)}-year model from {_escape(company.start_month)}
    </div>
  </td></tr>

  <tr><td style="padding:0 28px">
    {_table(model)}
    <div style="font:400 11px/1.4 Helvetica,Arial,sans-serif;color:{_MUTED}">
      Whole dollars by fiscal year. Negatives in parentheses.
    </div>
  </td></tr>

  <tr><td style="padding:18px 28px 0">
    <table role="presentation" cellpadding="0" cellspacing="0">{turns}</table>
  </td></tr>

  <tr><td style="padding:18px 28px 0">
    <div style="padding:14px 16px;border-radius:8px;background:#F7F9FC;border:1px solid {_RULE}">
      <div style="font:700 20px/1 Helvetica,Arial,sans-serif;color:{_INK}">
        {coverage.deck_ratio:.0%}
        <span style="font:600 11px/1.4 Helvetica,Arial,sans-serif;color:{_MUTED};text-transform:uppercase;letter-spacing:.08em">
          &nbsp;from the deck itself
        </span>
      </div>
      <div style="margin-top:7px;font:400 12px/1.6 Helvetica,Arial,sans-serif;color:{_MUTED}">
        {_escape(coverage.summary_line())}
      </div>
      {warning}
    </div>
  </td></tr>

  <tr><td style="padding:0 28px">{gap}{notes}</td></tr>

  <tr><td style="padding:20px 28px 26px">
    <div style="padding-top:16px;border-top:1px solid {_RULE};font:400 11px/1.6 Helvetica,Arial,sans-serif;color:{_MUTED}">
      The one-pager and the full model workbook are attached. Benchmark-supplied values
      are marked in both, so you can see which parts of the model the company did not
      give you.<br><br>
      Built from figures stated in an investor deck and filled out with labelled
      benchmarks. Not audited financial information, and not a forecast the company
      has endorsed.
    </div>
  </td></tr>

</table>
</td></tr></table>
</body></html>"""


def text_body(model: FinancialModel) -> str:
    """Plain-text alternative. Some readers prefer it and some clients demand it."""
    coverage = model.assumptions.coverage
    lines = [
        f"{model.assumptions.company.name} — financial model",
        f"{model.assumptions.company.business_model.replace('_', ' ')} · "
        f"{len(model.year_labels)} years from {model.assumptions.company.start_month}",
        "",
        "Whole dollars by fiscal year:",
    ]
    for label, key in (
        ("Revenue", "Revenue"),
        ("Gross profit", "Gross Profit"),
        ("EBITDA", "EBITDA"),
        ("Net income", "Net Income"),
    ):
        values = "  ".join(f"{v:>14,.0f}" for v in model.pnl_annual[key])
        lines.append(f"  {label:<14}{values}")
    lines.append(
        "  " + f"{'Ending cash':<14}" + "  ".join(f"{v:>14,.0f}" for v in model.ending_cash)
    )
    lines += ["", *(f"  {k}: {v}" for k, v in model.break_even.items()), ""]
    lines.append(coverage.summary_line())
    found = observations(model)
    if found:
        lines += ["", "Key CFO observations:"]
        lines += [f"  {i}. {note}" for i, note in enumerate(found, start=1)]
    lines += [
        "",
        "The one-pager and the model workbook are attached. Not audited financial "
        "information, and not a forecast the company has endorsed.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# sending
# --------------------------------------------------------------------------- #


def _attachments(paths: list[Path]) -> tuple[list[dict[str, str]], int]:
    payload: list[dict[str, str]] = []
    total = 0
    for path in paths:
        if not path.exists():
            continue
        raw = path.read_bytes()
        total += len(raw)
        payload.append({"filename": path.name, "content": base64.b64encode(raw).decode("ascii")})
    return payload, total


def _post(body: dict[str, Any], api_key: str) -> Delivery:
    """The real transport. The only place this module touches the network."""
    try:
        response = httpx.post(
            RESEND_ENDPOINT,
            json=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        return Delivery(False, f"could not reach Resend: {exc}")

    if response.status_code >= 400:
        # Resend puts a usable reason in the body; the status alone is not actionable.
        return Delivery(False, f"Resend returned {response.status_code}: {response.text[:300]}")

    identifier = None
    with contextlib.suppress(ValueError):
        identifier = response.json().get("id")
    return Delivery(True, f"sent to Resend (id {identifier})", identifier)


def send(
    model: FinancialModel,
    attachments: list[Path],
    *,
    settings: Settings,
    transport: Transport | None = None,
) -> Delivery:
    """Email the finished model. Returns what happened; never raises."""
    if not settings.email_enabled:
        return Delivery.skipped(
            "email not configured; set RESEND_API_KEY and EMAIL_TO to enable it"
        )

    try:
        files, total = _attachments(attachments)
        if total > MAX_ATTACHMENT_BYTES:
            return Delivery.skipped(
                f"attachments total {total / 1e6:.1f} MB, above the "
                f"{MAX_ATTACHMENT_BYTES / 1e6:.0f} MB limit; email not sent"
            )

        body: dict[str, Any] = {
            "from": settings.email_from,
            "to": [address.strip() for address in settings.email_to.split(",") if address.strip()],
            "subject": subject(model),
            "html": html_body(model),
            "text": text_body(model),
        }
        if files:
            body["attachments"] = files

        delivery = (transport or _post)(body, settings.resend_api_key or "")
    except Exception as exc:  # a build already succeeded; do not lose it over an email
        log.exception("email failed")
        return Delivery(False, f"email failed unexpectedly: {type(exc).__name__}: {exc}")

    if delivery.sent:
        log.info("emailed %s to %s", model.assumptions.company.name, settings.email_to)
    else:
        log.warning("email not sent: %s", delivery.detail)
    return delivery
