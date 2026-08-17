# 🛸 TopicAhead: Autonomous Attention-Timing Intelligence

[![Google ADK](https://img.shields.io/badge/Framework-Google%20ADK-4285F4?logo=google&logoColor=white)](https://github.com/google/adk)
[![Model](https://img.shields.io/badge/LLM-Gemini-34A853?logo=google-gemini&logoColor=white)](https://aistudio.google.com/)
[![MCP](https://img.shields.io/badge/Protocol-Model%20Context%20Protocol%20(MCP)-blueviolet)](https://modelcontextprotocol.io/)
[![Cloud](https://img.shields.io/badge/Deployment-Google%20Cloud%20Run-FBBC05?logo=google-cloud&logoColor=white)](https://cloud.google.com/run)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> Built for the official **Google Cloud "All Things Agentic" Hackathon** ($180,000 USD Prize Pool) under **The Taskmaster** category.

---

## 🌟 Overview & Problem Statement

Trend tools that just say "this is trending right now, here's a caption" are a solved, crowded market — YoTrends, vidIQ, and Virlo already do niche+region trend detection as mature paid products. The real unsolved problem is **timing and accountability**:

1. **No one tells you *when to act*, only *what's hot*.** Detecting a spike is not the same as knowing whether it's worth acting on before it's saturated.
2. **No one verifies their own calls.** Every trend tool markets confidence; none of them publish a falsifiable record of whether their calls were actually right.
3. **Cross-market timing is invisible.** A topic trending in one market today is often not yet visible in another — a real, observable window that generic trend feeds don't surface.

**TopicAhead** is an **Attention Intelligence Layer**: it scores a topic's lifecycle phase and cross-market visibility gap from real signals, renders a deterministic **ACT_NOW / MONITOR / IGNORE** verdict, and — its core differentiator — logs every prediction it makes to a **Forecast Ledger** that resolves itself against real data later, so its accuracy is a checkable number, not a marketing claim. Content generation (script, critic audit, visual direction) is a secondary **Execution Layer** that only runs when the verdict is `ACT_NOW`.

---

## 🏗️ Architecture

```mermaid
flowchart TD
    User["👤 User"] --> WebUI["🖥️ Interactive Studio (FastAPI + JS)"]
    WebUI --> Orchestrator["🧠 TopicAheadOrchestrator (Google ADK Root)"]

    subgraph MCPLayer ["🔌 FastMCP Server"]
        TrendsTool["📈 get_daily_trends(geo)"]
        IntentTool["🔍 get_rising_search_intent(keyword)"]
        RadarTool["🎯 get_grounded_opportunity_candidates(geo, keyword)"]
        GapsTool["🌍 get_cross_market_gaps(baseline_geo, target_geo)"]
        LedgerTool["🔮 get_forecast_accuracy()"]
        BenchmarkTool["📊 get_virality_benchmarks(platform)"]
    end

    subgraph AttentionLayer ["🎯 Attention Intelligence Layer (deterministic, real signals only)"]
        Orchestrator --> Agent1["1. 📈 TrendScoutAgent (MCP-grounded)"]
        Agent1 --> Scoring["agents/scoring.py: velocity, saturation,\nlifecycle stage, ACT_NOW/MONITOR/IGNORE"]
    end

    subgraph Ledger ["🔮 Forecast Ledger (no LLM — pure deterministic + HTTP)"]
        Predictor["ledger/predictor.py: emits & resolves\nfalsifiable predictions at 1h/4h/12h/24h"]
        Store["ledger/store.py: SQLite (data/ledger.db)"]
        Cycle["ledger/run_cycle.py: scheduled every 6h"]
    end

    subgraph ExecutionLayer ["🤖 Execution Layer (only runs if verdict == ACT_NOW)"]
        Agent2["2. 🎬 ScriptHookAgent (0-3s Hooks)"]
        Agent2 -->|"Draft Script"| Agent3["3. 🛡️ ViralityAuditorAgent (Critic, Score 0-100)"]
        Agent3 -->|"Score < 80: Rejected + Revision"| Agent2
        Agent3 -->|"Score ≥ 80: Approved"| Agent4["4. 🎨 VisualCreativeAgent (Prompts & Hashtags)"]
    end

    Scoring -->|"ACT_NOW"| Agent2
    Scoring -->|"MONITOR / IGNORE"| Stop["⏸️ decision_stop: verdict IS the output"]
    Agent4 --> Orchestrator
    Predictor <--> Store
    Cycle --> Predictor
    Orchestrator --> Output["📦 Structured Pydantic Payload"]
    Output --> WebUI
```

![TopicAhead Architecture Diagram](docs/architecture.jpg)

---

## 🎯 The Attention Intelligence Layer

Computed deterministically in `agents/scoring.py` from real signals — never guessed by the LLM, so the number on screen can never disagree with its own breakdown:

| Signal | What it measures |
| :--- | :--- |
| **Lifecycle stage** | `EMERGING` → `ACCELERATING` → `BREAKOUT` → `SATURATED`, derived from rank, traffic and news-coverage saturation of a single real snapshot. |
| **Cross-market gaps** | Topics trending in a baseline market (e.g. `US`) not yet visible in a target market (e.g. `MX`) — observed directly, never an invented ETA. |
| **Verdict** | `ACT_NOW` / `MONITOR` / `IGNORE` — a hard threshold on the total score, computed in Python (`derive_recommended_action`), not decided by the LLM. |

## 🔮 The Forecast Ledger

The core differentiator: no competitor researched (VyralFlow, Content_Studio.ai, YoTrends, vidIQ, Virlo) publishes this as a product. Every candidate with real velocity and a real cross-market gap generates falsifiable predictions at **1h, 4h, 12h and 24h** horizons simultaneously, stored with a timestamp in `data/ledger.db`. A scheduled cycle (`ledger/run_cycle.py`, every 6 hours) resolves due predictions against a fresh real Trends pull and updates accumulated accuracy stats, exposed live via the `get_forecast_accuracy()` MCP tool. **Zero LLM calls** in this subsystem — pure deterministic computation + HTTP, so it's free, unlimited, and actually falsifiable instead of "the model says it remembers correctly."

---

## 🤖 The Execution Layer (conditional, only on ACT_NOW)

| Agent Name | Role & Responsibility | Core Tools / Engine |
| :--- | :--- | :--- |
| **1. 📈 TrendScoutAgent** | Discovers breakout Google searches, rising search intent, lifecycle stage and cross-market gaps in real time via the FastMCP server (real MCP protocol, `McpToolset`). | FastMCP Server (`get_grounded_opportunity_candidates`, `get_cross_market_gaps`) + `agents/scoring.py` |
| **2. 🎬 ScriptHookAgent** | Converts the ACT_NOW opportunity into a high-retention video/carousel script with a 0-3s hook. | Gemini + Pydantic `HookAndScriptResult` |
| **3. 🛡️ ViralityAuditorAgent** | **Critic Loop:** adversarial audit of retention power, brand safety, anti-cliché rules; rejects with a quoted weak line + rewrite until virality score ≥ 80 (max 2 drafts). | Gemini + Pydantic `CriticAuditEvaluation` |
| **4. 🎨 VisualCreativeAgent** | Generates Imagen/Midjourney cover prompts, color palettes, and tiered hashtag clusters for the **approved** script only. | Gemini + Pydantic `VisualDirectivesResult` |

All four run as real `google.adk` `LlmAgent`s executed through an `InMemoryRunner`, not a hand-rolled `google.genai` call disguised as ADK.

---

## 🚀 Quick Start & Local Setup

### Prerequisites
* Python 3.11+ (this project's own venv runs 3.14.3)
* A free Gemini API key from [Google AI Studio](https://aistudio.google.com/)

### 1. Clone & Install Dependencies
```bash
git clone https://github.com/yobanrg/TopicAhead.git
cd TopicAhead
pip install -r requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
```
Edit `.env` and set your key:
```env
GEMINI_API_KEY=your_actual_gemini_api_key
MODEL=gemini-flash-lite-latest
```
`gemini-flash-lite-latest` is the default because it proved reliable under this project's tool-calling + large structured-output load; `gemini-flash-latest` returned 503 "high demand" repeatedly under the same load in testing.

### 3. Run the Studio (One-Click)
```bash
python run.py
```
Open your browser at **`http://127.0.0.1:8000`**.

### 4. Run the Forecast Ledger cycle manually (optional)
```bash
python ledger/run_cycle.py
```
In normal operation this runs automatically every 6 hours via a scheduled task — see `ledger/run_cycle.bat`.

### 5. Run the test suite
```bash
pip install -r requirements-dev.txt
pytest tests/
```
39 tests: deterministic scoring (`test_scoring.py`), the ledger against a temporary DB with no network (`test_ledger.py`), and ADK agent construction (`test_agents_construction.py`).

---

## ☁️ Google Cloud Run Deployment

```bash
# Build & Deploy to Google Cloud Run
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/topicahead
gcloud run deploy topicahead \
    --image gcr.io/YOUR_PROJECT_ID/topicahead \
    --platform managed \
    --allow-unauthenticated \
    --set-env-vars GEMINI_API_KEY=your_key,MODEL=gemini-flash-lite-latest
```

---

## 🏆 Author & Hackathon Verification
* **Developer:** Jose (Yoban) Rodríguez
* **Google Cloud Public Profile:** [skills.google/public_profiles/6bac5b41-ee95-4a9a-b9ee-d871c4e31106](https://www.skills.google/public_profiles/6bac5b41-ee95-4a9a-b9ee-d871c4e31106) *(12 Official Skill Badges & GEAR Certified)*
* **License:** MIT
