"""
The Promise Ledger - 4-6h viability probe (vertical slice).

Proves the round-trip on ONE real case, then a second that resolves the other way:

  announcement text + URL  ->  [Extractor: Gemini, structured]  ->  PromiseExtraction
       -> [Falsifiability gate: pure Python, no LLM]  -> accepted / rejected
       -> [Verifier: fetch evidence page, keyword + deadline check, NO LLM]
       -> FULFILLED / DELAYED / PENDING / UNVERIFIABLE  (+ evidence excerpt + URL)

Nothing here touches Firestore yet - a dict "ledger" is printed. If this slice
works on 2 cases, the pivot is viable and we wire it into the real ledger/agents.
Run:  venv\Scripts\python.exe -m promise_ledger_probe.probe
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

MODEL = os.getenv("MODEL", "gemini-flash-lite-latest")
TODAY = dt.date(2026, 8, 27)  # scenario "today"


# --------------------------------------------------------------------------- #
# Schema (mirrors agents/schemas.py style: structured, self-documenting)
# --------------------------------------------------------------------------- #
class PromiseExtraction(BaseModel):
    is_falsifiable: bool = Field(description="True only if this is a specific, dated, observable product promise")
    company: str = Field(default="", description="Company or project making the promise")
    promise_text: str = Field(default="", description="One-line normalized restatement of the promise")
    source_quote: str = Field(default="", description="Verbatim sentence from the announcement that states the promise")
    observable_outcome: str = Field(default="", description="The concrete thing that must become true for this to be FULFILLED")
    check_keywords: list[str] = Field(default_factory=list, description="2-6 short phrases whose presence on an official docs/changelog/pricing page would confirm the promise shipped")
    deadline_raw: str = Field(default="", description="Deadline exactly as stated ('Q2 2026', 'by end of 2025', 'in the coming weeks')")
    deadline_date_iso: str = Field(default="", description="Best-effort normalized deadline as YYYY-MM-DD, using the LAST day of the stated period")
    rejection_reason: str = Field(default="", description="If not falsifiable, why (vague / no deadline / not observable / aspirational)")


# --------------------------------------------------------------------------- #
# Stage 1 - Extractor (Gemini, structured output)
# --------------------------------------------------------------------------- #
EXTRACTOR_INSTRUCTION = """You extract falsifiable product promises from company announcements for an accountability ledger.

A promise is FALSIFIABLE only if ALL of these hold:
  - it names a specific capability, product, feature, price, availability, or artifact (not a vibe)
  - it has a stated or clearly implied deadline (a date, a quarter, "by end of year", "in the coming weeks")
  - its outcome is observable from a public source later (docs, changelog, pricing page, release notes, a downloadable file)

FALSIFIABLE examples:
  "The API will support a 1M token context window in Q2 2026."
  "Open weights will be released by the end of 2025."
  "This feature will be generally available to all paid users next month."

NOT falsifiable (reject these):
  "We're making AI more accessible to everyone."
  "We believe agents are the future."
  "Soon you'll be able to do more."  (no observable outcome / no real deadline)

Given the announcement text, its URL, and its publication date, return the extraction.
If falsifiable: fill company, promise_text, source_quote (verbatim), observable_outcome,
check_keywords (2-6 short phrases that would literally appear on the official docs/changelog/
release-notes page once shipped - for a model or API feature ALWAYS include the concrete API
identifier string if it is knowable, e.g. "claude-3-5-haiku", plus an OS/version string like
"iOS 18.1" when relevant - prefer exact machine-checkable tokens over prose),
deadline_raw, and deadline_date_iso (normalize to the LAST day of the stated period).
If NOT falsifiable: set is_falsifiable=false and give rejection_reason. Do not invent a deadline."""


def extract_promise(announcement_text: str, url: str, published: str) -> PromiseExtraction:
    from google import genai

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    prompt = (
        f"{EXTRACTOR_INSTRUCTION}\n\n"
        f"--- ANNOUNCEMENT URL ---\n{url}\n"
        f"--- PUBLISHED ---\n{published}\n"
        f"--- ANNOUNCEMENT TEXT ---\n{announcement_text[:12000]}\n"
    )
    resp = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_schema": PromiseExtraction,
            "temperature": 0,
        },
    )
    return PromiseExtraction.model_validate_json(resp.text)


# --------------------------------------------------------------------------- #
# Stage 2 - Falsifiability gate (pure Python, no LLM)
# --------------------------------------------------------------------------- #
def gate(p: PromiseExtraction) -> tuple[bool, str]:
    if not p.is_falsifiable:
        return False, f"extractor rejected: {p.rejection_reason or 'not falsifiable'}"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.deadline_date_iso or ""):
        return False, f"no normalized deadline (got {p.deadline_date_iso!r})"
    try:
        dt.date.fromisoformat(p.deadline_date_iso)
    except ValueError:
        return False, f"unparseable deadline {p.deadline_date_iso!r}"
    if len(p.observable_outcome.split()) < 3:
        return False, "observable_outcome too thin"
    if len([k for k in p.check_keywords if len(k.strip()) >= 3]) < 2:
        return False, "need >=2 usable check_keywords"
    return True, "accepted"


# --------------------------------------------------------------------------- #
# Stage 3 - Verifier (fetch evidence page, keyword + deadline check, NO LLM)
# --------------------------------------------------------------------------- #
def _fetch_text(url: str) -> str | None:
    try:
        r = httpx.get(url, follow_redirects=True, timeout=25,
                      headers={"User-Agent": "Mozilla/5.0 (PromiseLedgerProbe)"})
        if r.status_code != 200:
            return None
        html = r.text
        html = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html)
        text = re.sub(r"(?s)<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)
        return text
    except Exception:
        return None


def verify(p: PromiseExtraction, evidence_url: str) -> dict:
    deadline = dt.date.fromisoformat(p.deadline_date_iso)
    text = _fetch_text(evidence_url)
    if not text:
        return {"status": "UNVERIFIABLE", "reason": f"could not fetch {evidence_url}", "evidence_url": evidence_url}

    low = text.lower()
    hits = [k for k in p.check_keywords if k.strip().lower() in low]
    shipped = len(hits) >= max(2, (len(p.check_keywords) + 1) // 2)

    idx = low.find(hits[0].strip().lower()) if hits else -1
    excerpt = text[max(0, idx - 160): idx + 200].strip() if idx >= 0 else text[:300].strip()

    if shipped:
        status = "FULFILLED"
        reason = f"{len(hits)}/{len(p.check_keywords)} check keywords present on official page: {hits}"
    elif TODAY > deadline:
        status = "DELAYED"
        reason = (f"deadline {deadline} passed {(TODAY - deadline).days}d ago; "
                  f"only {len(hits)}/{len(p.check_keywords)} keywords found: {hits or 'none'}")
    else:
        status = "PENDING"
        reason = f"deadline {deadline} not yet reached; {len(hits)}/{len(p.check_keywords)} keywords so far"

    return {"status": status, "reason": reason, "evidence_url": evidence_url,
            "keyword_hits": hits, "evidence_excerpt": excerpt}


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def run_case(name: str, announcement_text: str, announcement_url: str, published: str, evidence_url: str):
    print("=" * 90)
    print(f"CASE: {name}")
    print("=" * 90)

    p = extract_promise(announcement_text, announcement_url, published)
    print("\n[1] EXTRACTION")
    print(json.dumps(p.model_dump(), indent=2, ensure_ascii=False))

    ok, msg = gate(p)
    print(f"\n[2] FALSIFIABILITY GATE -> {'ACCEPTED' if ok else 'REJECTED'} ({msg})")
    if not ok:
        print("\nLEDGER ENTRY: (none - rejected at gate)")
        return

    v = verify(p, evidence_url)
    print("\n[3] ZERO-LLM VERIFIER")
    print(json.dumps(v, indent=2, ensure_ascii=False))

    ledger_entry = {
        "company": p.company,
        "promise": p.promise_text,
        "source_quote": p.source_quote,
        "source_url": announcement_url,
        "announced": published,
        "deadline_raw": p.deadline_raw,
        "deadline_date": p.deadline_date_iso,
        "status": v["status"],
        "status_reason": v["reason"],
        "evidence_url": v["evidence_url"],
        "checked_at": TODAY.isoformat(),
    }
    print("\nLEDGER ENTRY:")
    print(json.dumps(ledger_entry, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    from promise_ledger_probe.cases import CASES

    for c in CASES:
        run_case(**c)
        print("\n")
