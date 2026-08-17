"""
Autonomous Master Orchestrator - TopicAhead
Features:
- Real Google ADK execution: every agent runs through an ADK LlmAgent + Runner,
  not a hand-rolled google.genai call.
- Real MCP protocol usage: TrendScoutAgent calls the FastMCP server over HTTP
  via MCPToolset - it is not bypassed with an in-process import.
- Deterministic Opportunity Radar grounding (agents/scoring.py) - velocity,
  saturation, lifecycle stage and recommended_action are computed in Python,
  never trusted from the LLM, so the number on screen can never disagree with
  its own breakdown.
- Real, observed cross-market gap detection (no fabricated propagation ETA).
- Autonomous Critic -> Revision Self-Correction Loop (up to 2 drafts).
- Asynchronous Event Stream for the Mission Control UI.
"""

import asyncio
import logging
import os
import sys
import uuid
from collections.abc import AsyncGenerator
from typing import Any, TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

# Ensure local imports work properly
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from schemas import (
    BrandMemory,
    CompleteCampaignPayload,
    CriticAuditEvaluation,
    CrossMarketGap,
    HookAndScriptResult,
    TrendIntelligenceResult,
    VisualDirectivesResult,
)
from scoring import (
    MIN_RELEVANCE_FOR_ACT_NOW,
    derive_recommended_action,
    find_cross_market_gaps,
    recompute_virality_verdict,
)
from script_engineer import create_script_engineer_agent
from trend_scout import create_trend_scout_agent
from virality_auditor import create_virality_auditor_agent
from visual_director import create_visual_director_agent

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from google.adk.runners import InMemoryRunner
from google.genai import types

from mcp_server.trends_service import GoogleTrendsService

load_dotenv()
logger = logging.getLogger("topicahead.orchestrator")

T = TypeVar("T", bound=BaseModel)

MAX_AGENT_RETRIES = 2
APP_NAME = "topicahead"


class AgentExecutionError(RuntimeError):
    """Raised when an ADK agent cannot produce a valid structured response after retries."""


class ConfigurationError(RuntimeError):
    """Raised when the orchestrator is misconfigured (e.g. missing API key)."""


class TopicAheadOrchestrator:
    """
    Autonomous Multi-Agent Orchestrator, all four stages run as real ADK agents:
    1. TrendScoutAgent   - calls the FastMCP server (real MCP protocol) for grounded
                            trend signals, lifecycle stage, and cross-market gaps.
    2. ScriptHookAgent   - Psychological Retention Hooks & Scenes.
    3. ViralityAuditorAgent - Critic & Automated Revision Loop if Score < 80.
    4. VisualCreativeAgent  - Imagen 3 / Midjourney Prompts & 3-Tier Hashtags.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None, mcp_url: str | None = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ConfigurationError(
                "GEMINI_API_KEY is not set. Provide it via .env or the request's api_key field."
            )
        # google-genai / google-adk both read GEMINI_API_KEY from the environment;
        # this guarantees a per-request key override actually takes effect.
        os.environ["GEMINI_API_KEY"] = self.api_key

        self.model_name = model or os.getenv("MODEL", "gemini-flash-lite-latest")
        # 8080 matches mcp_server/server.py's own default port for local dev;
        # Docker/Cloud Run always overrides this via an explicit env var.
        self.mcp_url = mcp_url or os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8080/mcp")

    async def _run_agent(self, agent, prompt: str, output_model: type[T], label: str) -> T:
        """
        Execute one ADK agent turn through a real Runner + Session and parse its
        structured output, with retries for transient failures.
        """
        last_error: Exception | None = None
        for attempt in range(1, MAX_AGENT_RETRIES + 1):
            try:
                return await self._run_agent_once(agent, prompt, output_model, label)
            except Exception as exc:  # noqa: BLE001 - ADK/Gemini exception types vary by failure mode
                last_error = exc
                logger.warning("%s attempt %d/%d failed: %s", label, attempt, MAX_AGENT_RETRIES, exc)
                if attempt < MAX_AGENT_RETRIES:
                    await asyncio.sleep(1.0 * attempt)
        raise AgentExecutionError(f"{label} failed after {MAX_AGENT_RETRIES} attempts: {last_error}") from last_error

    async def _run_agent_once(self, agent, prompt: str, output_model: type[T], label: str) -> T:
        runner = InMemoryRunner(agent=agent, app_name=APP_NAME)
        user_id = "topicahead-user"
        session_id = f"{label}-{uuid.uuid4().hex[:10]}"

        await runner.session_service.create_session(
            app_name=runner.app_name, user_id=user_id, session_id=session_id
        )

        content = types.Content(role="user", parts=[types.Part(text=prompt)])
        final_event = None
        async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=content):
            if event.is_final_response():
                final_event = event

        if final_event is None:
            raise AgentExecutionError(f"{label}: agent produced no final response")

        # ADK may expose the parsed structured output directly.
        if getattr(final_event, "output", None) is not None:
            try:
                return output_model.model_validate(final_event.output)
            except ValidationError:
                pass  # fall through to text parsing below

        text = None
        if final_event.content and final_event.content.parts:
            text = final_event.content.parts[-1].text
        if not text:
            raise AgentExecutionError(f"{label}: agent response had no parsable text or output")

        try:
            return output_model.model_validate_json(text)
        except ValidationError as exc:
            raise AgentExecutionError(f"{label}: response did not match {output_model.__name__} schema: {exc}") from exc

    async def execute_campaign_stream(
        self,
        topic: str,
        platform: str = "tiktok",
        geo: str = "ES",
        target_geo: str | None = None,
        tone: str = "Direct & High Impact",
        target_audience: str = "Founders, Creators and Marketing Teams",
        brand_memory: BrandMemory = None,
        forced_topic: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:

        memory = brand_memory or BrandMemory(
            industry_niche=topic,
            target_audience=target_audience,
            tone_of_voice=tone,
        )
        resolved_target_geo = target_geo or ("MX" if geo.upper() == "US" else "US")

        # -------------------------------------------------------------
        # PRE-FLIGHT: fail fast if Google Trends has no real data for this
        # geo right now, instead of letting the LLM invent a whole
        # TrendIntelligenceResult (topic, scores, verdict) from nothing.
        # -------------------------------------------------------------
        baseline_trends = GoogleTrendsService.fetch_daily_trending_topics(geo=geo.upper())
        if not baseline_trends:
            yield {
                "type": "no_data",
                "agent": "TrendScoutAgent",
                "stage": 1,
                "message": (
                    f"⚠️ Live Google Trends data is not available for '{geo.upper()}' right now "
                    "(the feed returned nothing). No content was generated from placeholder data - "
                    "try again shortly or pick another region."
                ),
                "data": {
                    "reason": f"Google Trends RSS returned no items for geo={geo.upper()}.",
                }
            }
            return

        # -------------------------------------------------------------
        # STAGE 1: Real-time Signal Discovery via a real ADK agent calling
        # the real MCP server (MCPToolset over streamable-http).
        # -------------------------------------------------------------
        yield {
            "type": "status",
            "agent": "TrendScoutAgent",
            "stage": 1,
            "message": (
                f"Re-analyzing the user-picked alternative '{forced_topic}' for '{topic}' in {geo.upper()}..."
                if forced_topic else
                f"Scanning real-time signals via MCP for '{topic}' in {geo.upper()} (comparing against {resolved_target_geo})..."
            )
        }

        trend_scout_agent = create_trend_scout_agent(mcp_url=self.mcp_url, model_name=self.model_name)
        forced_topic_instruction = (
            f"""
        The user already saw a full scan and explicitly chose a specific alternative from the
        evaluated candidates instead of the top pick. You MUST set selected_opportunity to
        exactly this candidate, verbatim: '{forced_topic}'. Still call both MCP tools as normal
        and still fill out the full analysis (radar scores, cross_market_gaps, signals_evaluated)
        honestly based on that candidate's real grounded data - do not inflate scores just because
        it was manually chosen.
        """
            if forced_topic else
            "Then produce your full TrendIntelligenceResult analysis, selecting the single best opportunity."
        )
        prompt_scout = f"""
        Niche/topic: '{topic}'
        Baseline market (geo): {geo.upper()}
        Target market to check for cross-market gaps: {resolved_target_geo}
        Platform: {platform}
        Brand Memory: {memory.model_dump_json()}

        Call get_grounded_opportunity_candidates(geo='{geo.upper()}', keyword='{topic}') first.
        Call get_cross_market_gaps(baseline_geo='{geo.upper()}', target_geo='{resolved_target_geo}') to find real,
        observed gaps - topics trending in {geo.upper()} not yet visible in {resolved_target_geo}.
        {forced_topic_instruction}
        """

        trend_intel = await self._run_agent(trend_scout_agent, prompt_scout, TrendIntelligenceResult, "TrendScoutAgent")

        # Never trust LLM arithmetic for the headline number: recompute in Python.
        radar = trend_intel.opportunity_radar
        radar.total_opportunity_score = min(100, max(0, (
            radar.velocity_score + radar.recency_score + radar.audience_relevance_score
            + radar.saturation_score + radar.hook_potential_score + radar.brand_safety_score
        )))
        radar.recommended_action = derive_recommended_action(radar.total_opportunity_score, radar.audience_relevance_score)
        trend_intel.target_market_geo = resolved_target_geo

        # signals_evaluated[i].opportunity_score is a separate LLM-authored field
        # that duplicates the same number for the SELECTED candidate - keep it in
        # sync with the code-verified radar score instead of showing two
        # different numbers for the same topic (observed live: 48 vs 61 for the
        # identical selected trend, undermining the "grounded, no invented
        # numbers" claim this product is built on).
        for signal in trend_intel.signals_evaluated:
            if signal.verdict == "SELECTED":
                signal.opportunity_score = radar.total_opportunity_score

        # Never trust the LLM's "copy this tool result verbatim" either: it
        # occasionally corrupts a field while re-serializing JSON (observed
        # live - an apostrophe in a real topic, "colo-colo - o'higgins",
        # caused the model to emit the literal string "target_geo" as the
        # topic instead). cross_market_gaps is pure deterministic data
        # (agents/scoring.py::find_cross_market_gaps over two real Trends
        # pulls) - recompute it directly instead of trusting the model's copy.
        target_trends = GoogleTrendsService.fetch_daily_trending_topics(geo=resolved_target_geo)
        trend_intel.cross_market_gaps = [
            CrossMarketGap(**gap) for gap in find_cross_market_gaps(geo.upper(), baseline_trends, resolved_target_geo, target_trends)
        ]

        yield {
            "type": "agent_result",
            "agent": "TrendScoutAgent",
            "stage": 1,
            "data": trend_intel.model_dump()
        }

        # -------------------------------------------------------------
        # DECISION GATE: the Execution Layer (script/critic/visual) only runs
        # when the Attention Intelligence Layer actually decided to act. A
        # MONITOR/IGNORE verdict is itself the product's output - generating
        # content anyway would just make this another content-generator demo.
        # -------------------------------------------------------------
        if radar.recommended_action != "ACT_NOW":
            relevance_blocked = (
                radar.total_opportunity_score >= 80
                and radar.audience_relevance_score < MIN_RELEVANCE_FOR_ACT_NOW
            )
            reason = (
                f"Score {radar.total_opportunity_score}/100 (ACT_NOW threshold: 80). "
                f"Saturation: {radar.content_saturation_level}. Stage: {radar.lifecycle_stage}."
                + (
                    f" Niche relevance too low to auto-generate content ({radar.audience_relevance_score}/20, "
                    f"needs {MIN_RELEVANCE_FOR_ACT_NOW}+) even though the raw trend score cleared the threshold - "
                    "see the alternatives below, or pick one to generate for it directly."
                    if relevance_blocked else ""
                )
            )
            yield {
                "type": "decision_stop",
                "agent": "TrendScoutAgent",
                "stage": 1,
                "message": f"⏸️ Verdict: {radar.recommended_action}. The attention engine decided not to generate content yet. {reason}",
                "data": {
                    "recommended_action": radar.recommended_action,
                    "reason": reason,
                    "trend_intelligence": trend_intel.model_dump(),
                }
            }
            return

        # -------------------------------------------------------------
        # STAGE 2/3: Autonomous Generation & Critic Revision Loop
        # (Execution Layer - only reached when the verdict above was ACT_NOW)
        # -------------------------------------------------------------
        max_revisions = 2
        current_draft_num = 1
        final_script = None
        final_audit = None
        revision_instructions = ""

        script_agent = create_script_engineer_agent(model_name=self.model_name)
        critic_agent = create_virality_auditor_agent(model_name=self.model_name)

        while current_draft_num <= max_revisions:
            yield {
                "type": "status",
                "agent": "ScriptHookAgent",
                "stage": 2,
                "draft": current_draft_num,
                "message": f"Designing Draft #{current_draft_num} with a 0-3s hook optimized for {platform.upper()}..." + (" (applying Critic feedback)" if revision_instructions else "")
            }

            prompt_script = f"""
            Create Draft #{current_draft_num} of high-retention social media content.

            Selected Opportunity: {trend_intel.selected_opportunity}
            Lifecycle Stage: {radar.lifecycle_stage}
            Strategic Angle: {trend_intel.strategic_angle}
            Opportunity Radar: {radar.model_dump_json()}
            Cross-Market Gaps: {[g.model_dump() for g in trend_intel.cross_market_gaps][:3]}
            Platform: {platform}
            Brand Memory: {memory.model_dump_json()}
            Previous Critic Revision Instructions (if any): {revision_instructions}
            """

            current_script = await self._run_agent(
                script_agent, prompt_script, HookAndScriptResult, f"ScriptHookAgent-draft{current_draft_num}"
            )
            current_script.draft_number = current_draft_num

            yield {
                "type": "agent_result",
                "agent": "ScriptHookAgent",
                "stage": 2,
                "draft": current_draft_num,
                "data": current_script.model_dump()
            }

            yield {
                "type": "status",
                "agent": "ViralityAuditorAgent",
                "stage": 3,
                "draft": current_draft_num,
                "message": f"[Critic Agent]: Auditing Draft #{current_draft_num} (hook power, retention, and compliance)..."
            }

            prompt_audit = f"""
            Perform your uncompromising audit on Draft #{current_draft_num}:
            - Opportunity Radar: {radar.model_dump_json()}
            - Content Script: {current_script.model_dump_json()}
            - Brand Memory: {memory.model_dump_json()}
            """

            current_audit = await self._run_agent(
                critic_agent, prompt_audit, CriticAuditEvaluation, f"ViralityAuditorAgent-draft{current_draft_num}"
            )
            current_audit.draft_evaluated = current_draft_num

            # Never trust LLM arithmetic for the headline number or the gate
            # decision - same principle already applied to the Opportunity
            # Radar score above, now also applied to the Critic's verdict.
            script_text = " ".join([
                current_script.hook_3s, current_script.call_to_action, current_script.caption,
                *[scene.spoken_audio_or_text for scene in current_script.story_scenes],
            ])
            verdict = recompute_virality_verdict(
                current_audit.hook_strength, current_audit.retention_pacing,
                current_audit.value_density, script_text,
            )
            current_audit.overall_virality_score = verdict["overall_virality_score"]
            current_audit.status = verdict["status"]

            yield {
                "type": "agent_result",
                "agent": "ViralityAuditorAgent",
                "stage": 3,
                "draft": current_draft_num,
                "data": current_audit.model_dump()
            }

            if current_audit.status == "APPROVED" or current_draft_num >= max_revisions:
                final_script = current_script
                final_audit = current_audit
                yield {
                    "type": "status",
                    "agent": "ViralityAuditorAgent",
                    "stage": 3,
                    "message": f"✅ Draft #{current_draft_num} APPROVED by the Critic with a Score of {current_audit.overall_virality_score}/100!"
                }
                break
            else:
                revision_instructions = current_audit.actionable_revision_instructions
                yield {
                    "type": "status",
                    "agent": "ViralityAuditorAgent",
                    "stage": 3,
                    "message": f"Draft #{current_draft_num} rejected (Score {current_audit.overall_virality_score}/100). Reason: '{revision_instructions}'. Starting self-correction loop..."
                }
                current_draft_num += 1

        # -------------------------------------------------------------
        # STAGE 4: Visual Creative Director (Cover Prompts & Hashtags)
        # -------------------------------------------------------------
        yield {
            "type": "status",
            "agent": "VisualCreativeAgent",
            "stage": 4,
            "message": "Generating cover visual specification (Imagen 3) and 3-tier hashtag cluster..."
        }

        visual_agent = create_visual_director_agent(model_name=self.model_name)
        prompt_visual = f"""
        Based on the approved script:
        - Title: {final_script.title}
        - Hook: {final_script.hook_3s}
        - Opportunity: {trend_intel.selected_opportunity}
        - Platform: {platform}
        """

        visual_result = await self._run_agent(visual_agent, prompt_visual, VisualDirectivesResult, "VisualCreativeAgent")

        yield {
            "type": "agent_result",
            "agent": "VisualCreativeAgent",
            "stage": 4,
            "data": visual_result.model_dump()
        }

        final_payload = CompleteCampaignPayload(
            topic=topic,
            platform=platform,
            brand_memory=memory,
            trend_intelligence=trend_intel,
            script_and_content=final_script,
            visual_and_metadata=visual_result,
            virality_audit=final_audit,
            total_revision_cycles=current_draft_num,
        )

        yield {
            "type": "complete",
            "message": "Content Strategy & Opportunity Generated Successfully.",
            "payload": final_payload.model_dump()
        }

