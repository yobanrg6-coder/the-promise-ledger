"""
GemmaAuditorAgent - an independent second opinion on the same content draft,
using a different model family entirely (Gemma, not Gemini).

Same discipline as everywhere else in this system: no single point of
failure gets to decide whether a draft is genuinely viral-ready. The
Virality Auditor Agent (Gemini) already does this adversarial audit - this
agent asks the identical question of a genuinely different model, so a
weak sub-score or a real rejection reason that Gemini's read missed still
has a real chance of being caught (agents/scoring.py::reconcile_virality_audit
combines both reads conservatively, never trusting either model's read
alone).
"""

import os
import sys

from google.adk.agents import LlmAgent
from google.adk.models import Gemini

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from schemas import CriticAuditEvaluation
from virality_auditor import SYSTEM_INSTRUCTION

DEFAULT_GEMMA_MODEL = "gemma-4-26b-a4b-it"


def create_gemma_auditor_agent(model_name: str | None = None, api_key: str | None = None) -> LlmAgent:
    model = model_name or os.getenv("GEMMA_MODEL", DEFAULT_GEMMA_MODEL)
    # api_key is bound directly into this agent's own genai Client via
    # client_kwargs, never through the process-wide GEMINI_API_KEY env var -
    # that would be shared, mutable state across every concurrent request
    # this async server handles, so two callers with different keys (e.g. a
    # judge pasting their own) could race and end up using each other's key.
    gemma_kwargs: dict = {"model": model}
    if api_key:
        gemma_kwargs["client_kwargs"] = {"api_key": api_key}
    return LlmAgent(
        name="gemma_auditor_agent",
        description="Independently re-audits the same content draft for virality score, hook power, and "
        "algorithmic compliance, on a different model family than the Virality Auditor Agent.",
        model=Gemini(**gemma_kwargs),
        # Deliberately the exact same instruction as the Virality Auditor
        # Agent - this has to be a genuine independent replication of the
        # same adversarial audit, not a differently-tuned critic, or
        # agreement/disagreement between the two would not mean anything.
        instruction=SYSTEM_INSTRUCTION,
        output_schema=CriticAuditEvaluation,
    )
