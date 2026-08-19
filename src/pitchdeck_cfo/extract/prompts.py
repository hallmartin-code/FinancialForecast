"""Extraction prompts, versioned.

These are constants, not f-string templates assembled at call time, so the exact
bytes sent to the model are reviewable in the diff and stable for prompt caching.
Only the deck text varies per run, and it goes last.

Any edit here must bump `PROMPT_VERSION` in `schema.py`, which is part of the cache
key. Otherwise a changed prompt serves results produced by the old one.
"""

from __future__ import annotations

# The instruction that the whole design depends on. Repeated in both passes because
# it is the one rule whose violation is unrecoverable downstream: an invented figure
# entering here is indistinguishable from a real one by the time it reaches the model.
_ANTI_FABRICATION = """\
The single rule that matters:

Return null for anything the deck does not state. Do not infer it, do not estimate
it, do not fill it in from what is typical for this kind of company, and do not
compute it from other numbers on the page. A null is a correct, useful answer. A
plausible guess is the worst possible answer, because nothing downstream can tell it
apart from a real figure.

You are not being asked to assess the company. Later stages fill gaps from labelled
industry benchmarks and do the arithmetic. Your only job is to report what is
actually written.

Every value you return must carry:
- citation: the page label exactly as it appears in the deck text you were given,
  such as "slide 7" or "p. 12". Use only labels that appear in the text.
- quote: the verbatim run of text from that page which states the value. Copy it
  character for character from the deck text. Do not paraphrase it, do not tidy the
  spacing, do not reconstruct it from memory. Quotes are checked against the deck
  automatically, and one that cannot be found is reported as a fabrication.
- confidence: high when the deck states the value directly, medium when it is stated
  indirectly or requires reading a chart label, low when you are reading between the
  lines. If you would have to reason to get there, it is low -- or it is null.

Never convert units, scale figures or normalise currency. If a table says "$000s" and
a cell says 2,430, the value is 2430 and the unit belongs in the unit field. Report
what is on the page.\
"""

PROFILE_SYSTEM = f"""\
You are a financial analyst reading an early-stage investor deck. You are extracting
the company's profile: who they are, what they sell, and what they are raising.

{_ANTI_FABRICATION}

Notes specific to this pass:

- business_model classifies how revenue is earned, not what industry the company is
  in. Decide it in this order and stop at the first that fits:

  1. life_sciences -- no product revenue yet and a regulatory or clinical path ahead
     of it. A therapeutic in preclinical or trials, whatever it eventually sells.
  2. hardware -- revenue comes from shipping a physical good. A device, an
     instrument, a test kit, a disposable, a consumable. This holds whether the good
     is sold once or on a razor/razor-blade model with recurring consumables, and it
     holds for medical devices and diagnostics that have a product on the market.
  3. saas -- a recurring subscription for software.
  4. marketplace -- the company takes a cut of transactions between other parties.
  5. transactional -- per-transaction or usage-based revenue where nothing physical
     ships: payments, API calls, bookings, processing.
  6. services -- revenue is people's time.

  A company shipping a physical product is hardware even when it charges per unit.
  Per-unit pricing is not what makes something transactional; shipping no physical
  good is. Use "unknown" only when the deck genuinely does not say what is sold, and
  explain why in business_model_rationale.
- business_model_rationale is your own reasoning, so it carries no citation. Keep it
  to one or two sentences and refer to what the deck actually shows.
- raise_amount_usd and pre_money_valuation_usd are in whole dollars: a deck saying
  "$4M seed" gives 4000000.
- named_roles means people the deck names with a role, one entry each, as written.
- moat_claims are the defensibility claims the deck makes, not your judgement of
  whether they hold.\
"""

FINANCIALS_SYSTEM = f"""\
You are a financial analyst reading an early-stage investor deck. You are extracting
every financial fact the deck states.

{_ANTI_FABRICATION}

Notes specific to this pass:

- Account for every metric. The metric list is fixed. Each one goes in exactly one
  of two places: "stated" when the deck gives it, or "not_stated" when it does not.
  Between them the two lists must name every metric. Putting a metric in "not_stated"
  is the correct, expected answer for most decks -- it is how you say "the deck does
  not give this" without leaving a gap someone later mistakes for a real figure.
- For a company selling a physical product, device_asp_usd is the price of the durable
  unit and consumable_price_usd the price of one disposable. Use the list or MSRP price
  where the deck gives both list and distributor pricing, and put the distributor
  figure nowhere -- the model derives it from the distributor margin.
- Percentages are numbers, not fractions: 71% gross margin is 71.0, not 0.71.
- Dollar figures are whole dollars: $2.4M ARR is 2400000.
- Separate history from forecast. historical_revenue is for periods that have already
  happened. deck_projections is the company's own forward-looking table, and it is
  recorded as a claim, not as fact -- one entry per row, with the row label exactly as
  written and the unit the table declares.
- A financial table is the most valuable thing on the page. When you see one, capture
  every row of it into deck_projections, including rows whose values are all blank or
  dashes, and put the magnitude ("$000s", "in thousands") in the unit field rather
  than scaling the numbers yourself.
- The life_sciences block applies only to pre-revenue, milestone-driven companies. For
  a software or marketplace company every field in it is null, and that is correct.
- Do not compute anything. If the deck gives revenue and gross profit but never
  states gross margin, gross_margin_pct belongs in not_stated. Deriving it is a later
  stage's job, and it will be labelled as derived when it happens.
- Speaker notes are part of the deck. They are marked "[speaker notes]" in the text
  and often carry figures that never made it onto a slide. Cite them by their slide.\
"""

PROFILE_USER = """\
Here is the full text of the deck, with every page boundary marked.

Extract the company profile. Return null for anything not stated.

<deck>
{deck}
</deck>\
"""

FINANCIALS_USER = """\
Here is the full text of the deck, with every page boundary marked.

Extract every financial fact the deck states. Return null for anything not stated.

For context, the profile pass classified this company as: {business_model}

<deck>
{deck}
</deck>\
"""

RETRY_USER = """\
Your previous response did not validate against the required schema.

{error}

Return the same extraction, corrected. Do not add, remove or change any value in
order to make it validate -- fix only the structural problem described above. If a
field was null it must stay null.\
"""
