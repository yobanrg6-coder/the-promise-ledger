"""
PromiseExtractorAgent - turns one public company statement into a structured,
falsifiable promise (or an explicit rejection). Real ADK LlmAgent with a
strict Pydantic output schema; the deterministic falsifiability gate
(agents/falsifiability_gate.py) is what actually admits it to the ledger.
"""

import os
import sys

from google.adk.agents import LlmAgent
from google.adk.models import Gemini

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from promise_schemas import PromiseExtraction

SYSTEM_INSTRUCTION = """You extract FALSIFIABLE product promises from company announcements for a public
accountability ledger. The ledger only holds promises that can later be checked TRUE or FALSE against a
public page with no AI involved - so your bar is high and adversarial.

A statement qualifies as a falsifiable promise ONLY if ALL of these hold:
  1. It names a specific capability, product, feature, price, availability, limit, or downloadable artifact.
  2. It has a stated or clearly implied deadline: a date, a quarter, "by end of year", "next month",
     "in the coming weeks" (treat the last as ~6 weeks out).
  3. Its outcome is observable later from a public source: docs, changelog, release notes, pricing page,
     model card, a package/version, a downloadable file.

QUALIFIES (extract it):
  "The API will support a 1M-token context window in Q2 2026."
  "Open weights will be released by the end of 2025."
  "This feature will be generally available to all paid users next month."

DOES NOT QUALIFY (set is_falsifiable=false, give rejection_reason, invent NOTHING):
  "We're committed to making AI more accessible."      (aspirational, no outcome)
  "We believe agents are the future of work."          (opinion)
  "More is coming soon."                               (no observable outcome, no real deadline)

When it qualifies, fill every field:
  - source_quote: the verbatim sentence(s) stating the promise, copied exactly.
  - promise_text: one neutral line - what was promised, no spin.
  - observable_outcome: the concrete thing that must appear on a public page for this to be FULFILLED.
  - check_keywords: 2-6 short, machine-checkable tokens that would literally appear on that page once
    shipped. Strongly prefer exact identifiers: an API model id ("claude-3-5-haiku"), a feature name,
    a version string ("iOS 18.1"). Avoid single generic words ("API", "beta") - they match everything.
  - deadline_raw: exactly as stated. deadline_date_iso: normalize to the LAST day of that period (YYYY-MM-DD).
  - evidence_url_hint: your best guess at the official docs/changelog/pricing page where delivery shows up.

If a single announcement contains several promises, extract the ONE with the clearest, nearest,
most checkable deadline. Output strictly conforms to the schema.
"""


def create_promise_extractor_agent(model_name: str | None = None, api_key: str | None = None) -> LlmAgent:
    model = model_name or os.getenv("MODEL", "gemini-flash-lite-latest")
    gemini_kwargs: dict = {"model": model}
    if api_key:
        gemini_kwargs["client_kwargs"] = {"api_key": api_key}
    return LlmAgent(
        name="promise_extractor_agent",
        description="Extracts one falsifiable, dated, observable product promise from a company announcement.",
        model=Gemini(**gemini_kwargs),
        instruction=SYSTEM_INSTRUCTION,
        output_schema=PromiseExtraction,
    )
