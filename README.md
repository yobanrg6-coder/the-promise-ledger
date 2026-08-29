# The Promise Ledger

[![Google ADK](https://img.shields.io/badge/Framework-Google%20ADK-4285F4?logo=google&logoColor=white)](https://github.com/google/adk)
[![Model](https://img.shields.io/badge/LLM-Gemini%203.5%20%2B%20Gemma-34A853?logo=google-gemini&logoColor=white)](https://aistudio.google.com/)
[![MCP](https://img.shields.io/badge/Protocol-Model%20Context%20Protocol-blueviolet)](https://modelcontextprotocol.io/)
[![Cloud](https://img.shields.io/badge/Deployment-Google%20Cloud%20Run-FBBC05?logo=google-cloud&logoColor=white)](https://cloud.google.com/run)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> Built for the **Google Cloud "All Things Agentic" Hackathon**, category **The Taskmaster**.

**A machine-verifiable record of public product promises.** Companies announce
dated commitments constantly ("open weights by end of year", "GA next quarter",
"available later this month"). Nobody keeps an honest, checkable score of which
ones actually shipped, on time. The Promise Ledger does, and — its whole point —
**no LLM decides an outcome**. A promise's status is set by deterministic code
that fetches the official page and checks it.

---

## What it does

1. **Extract** a falsifiable promise from an announcement — an agent (Gemini)
   proposes a structured promise: verbatim source quote, normalized deadline,
   an observable outcome, and machine-checkable keywords.
2. **Audit** it adversarially on a second model family (Gemma) — an independent
   read on "is this actually checkable?". Rejections trigger up to two
   re-extractions.
3. **Gate** it with pure Python — a real deadline, not absurd, ≥2 distinct
   specific keywords, a substantive outcome. The gate admits, not the model.
4. **Verify** it with **zero LLM**, point-in-time. Two probes: the official
   page **as archived by the Wayback Machine on or before the deadline** (if
   the check keywords are in that capture, the promise was kept on time and
   the capture date is the dated proof — no prose-date guessing, no third
   party but a neutral public archive), then the page **now** (which, combined
   with probe 1, gives late vs delayed vs abandoned). A fixed decision table
   turns the two readings into one status, and every verdict records how it
   was reached (`wayback@deadline`, `live-page`, …).
5. **Score** it — a per-company and overall scorecard: kept on time / undated /
   late / delayed / abandoned / unverifiable, and an on-time rate that is a
   count, not a claim.

### The seven statuses

`PENDING` · `FULFILLED` · `FULFILLED_LATE` · `PARTIALLY_FULFILLED` · `DELAYED` ·
`ABANDONED` · `UNVERIFIABLE`

The decision table lives in `ledger/README.md` and `ledger/verifier.py`. It is
deliberately fixed and public, so "delayed vs abandoned" is never a judgement
call made after the fact.

---

## Architecture

![The Promise Ledger architecture](docs/architecture.png)

```
announcement text ──▶ PromiseExtractorAgent  (Gemini, ADK, structured output)   [1]
                        │
                        ▼
                     PromiseAuditorAgent     (Gemma, ADK, adversarial)          [2]
                        │  reject ─▶ re-extract (max 2)
                        ▼
                     falsifiability gate      (pure Python, no LLM)             [3]
                        │
                        ▼
                     admit_promise ──▶ ledger  (JSON file / Firestore)          [4]

ledger  ◀──▶  FastMCP server        get_scorecard · list_promises · get_promise ·
                                    admit_promise · run_verification_cycle
   │
   ▼
run_cycle  (scheduled, zero LLM)   for each due promise: the official page as
                                   archived on/before the deadline (Wayback),
                                   then the page now → fixed decision table
```

Steps 1–3 stream live in the web app (`/api/extract-stream`). The verification
cycle and every MCP tool are LLM-free.

| Component | Tech |
|---|---|
| `agents/promise_extractor.py` | `google.adk` `LlmAgent`, Gemini, Pydantic `PromiseExtraction` output schema |
| `agents/promise_auditor.py` | `google.adk` `LlmAgent`, Gemma — a genuinely different model family |
| `agents/promise_orchestrator.py` | the extract → audit → re-extract → gate → admit pipeline, as an async event stream |
| `agents/falsifiability_gate.py` | deterministic admission check, no LLM |
| `ledger/evidence.py` | HTTP fetch + HTML→text (+ Wayback toolbar strip, entity decode) + whole-token keyword match, no JS |
| `ledger/archive.py` | Wayback Machine lookup — the official page as captured near a given date |
| `ledger/verifier.py` | the zero-LLM, two-probe (archived-at-deadline / now) decision table |
| `ledger/promises.py` | store + scorecard; backend = JSON file / Firestore / in-memory |
| `mcp_server/server.py` | FastMCP server exposing the ledger over the Model Context Protocol |
| `web_app/` | FastAPI + a single static page: scorecard + live pipeline stream |

---

## Quick start

Python 3.11+.

```bash
git clone https://github.com/yobanrg6-coder/the-promise-ledger.git
cd the-promise-ledger
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                # add GEMINI_API_KEY for the live extraction demo
```

**Seed the ledger** with the curated demo promises (hand-typed real
announcements; the machine verifies them live — no LLM, no key needed):

```bash
python -m ledger.seed --fresh
```

This writes `data/ledger.json` (a copy is committed, so you can skip this and
still have data).

**Run the app:**

```bash
python run.py           # FastMCP server + web app
# open http://127.0.0.1:8000
```

**Run a verification cycle** (re-checks every due promise against its live
evidence page, zero LLM):

```bash
python -m ledger.run_cycle
```

**Tests:**

```bash
pip install -r requirements-dev.txt
pytest tests/            # no network, no LLM
```

---

## Deploy to Cloud Run

```bash
# Cloud Run builds the container from the Dockerfile - no local Docker needed.
# Store the Gemini key in Secret Manager, never as a plain env var.
echo -n "your_gemini_api_key" | gcloud secrets create gemini-api-key --data-file=-

gcloud run deploy the-promise-ledger --source . --region=us-central1 \
    --allow-unauthenticated --min-instances=1 --max-instances=1 \
    --set-env-vars MODEL=gemini-3.5-flash-lite,LEDGER_BACKEND=json \
    --set-secrets GEMINI_API_KEY=gemini-api-key:latest
```

`data/ledger.json` ships in the image as the baseline scorecard. Cloud Run's
disk is ephemeral, so live writes during a session don't survive a cold start —
set `LEDGER_BACKEND=firestore` for durable writes.

Cloud Run exposes one port (the web app). The FastMCP server also runs in the
container but on an internal port — connect an MCP client to it by running the
stack locally (`python run.py`, then point the client at
`http://127.0.0.1:8080/mcp`). The web app talks to the same ledger module
directly, so the public demo is fully functional without it.

---

## Honest limitations

- **The archive doesn't always have a capture near the deadline.** When there
  is no Wayback snapshot on/before the deadline, the verifier falls back to
  reading a date off the *current* page — a weaker signal, biased toward
  `FULFILLED` over `FULFILLED_LATE` because it takes the earliest date near the
  first keyword. In the seed, `xAI` (no 2024 capture of `x.ai/news/grok-os`)
  and `Anthropic` verify this way; their `verification_method` says
  `live-page+date`, so the record shows which verdicts rest on the weaker path.
- **One seed row still leans on a third-party page.** `Anthropic`'s
  `evidence_url` is a dated write-up (`simonwillison.net/.../haiku/`) rather
  than Anthropic's own changelog, which no longer carries the 2024 Haiku
  release and has no useful pre-deadline capture. Choosing that URL is the one
  piece of human curation the pipeline can't remove; it's flagged in
  `ledger/seed_data.py`, and the verdict (`FULFILLED_LATE`) is still the
  zero-LLM rule's.
- **Keyword brittleness.** If a page describes a shipped feature in words that
  don't contain the check keywords, the verifier under-reports it. The seed set
  shows this: `Stability AI` resolves `FULFILLED` but neither its page nor any
  capture pins a date, so it stays `FULFILLED (undated)` — kept out of the
  on-time rate rather than assumed on time.
- **`UNVERIFIABLE` still fires** when the live page is unreachable *and* the
  archive has nothing usable (or the stored record has a broken date). The
  seed happens not to hit that today — `OpenAI`'s bot-blocked download page
  resolves via its 2024-12-28 archived capture — but the status is live in the
  decision table.
- The seed is 8 curated promises across 6 companies — enough to show the
  mechanism, not a census. Four verify point-in-time against the company's own
  official page; one is undated. Treat the headline percentage as illustrative.

---

## Author

Jose (Yoban) Rodríguez · [Google Cloud profile](https://www.skills.google/public_profiles/6bac5b41-ee95-4a9a-b9ee-d871c4e31106) · MIT License
