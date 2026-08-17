"""
Enhanced Pydantic Schemas for TopicAhead
Includes Opportunity Radar, Brand Memory, Critic Revision Loops, and Deterministic Scoring Breakdown.
"""


from pydantic import BaseModel, Field


# ---------------- Brand Memory ----------------
class BrandMemory(BaseModel):
    brand_name: str = Field(default="Personal Brand", description="Name of the brand or creator")
    industry_niche: str = Field(default="Tech & AI", description="Core industry")
    target_audience: str = Field(default="Founders, Developers & Creators", description="Target persona")
    tone_of_voice: str = Field(default="Direct, Visionary & Analytical", description="Brand voice guidelines")
    topics_to_avoid: list[str] = Field(default=["Politics", "Sensationalist Fake News"], description="Excluded themes")
    winning_hook_archetypes: list[str] = Field(
        default=["Contrarian / Industry Myth Buster", "0-to-1 Step-by-Step Proof", "Curiosity Loop / Secret Revealed"],
        description="Historically high-converting hook patterns"
    )

# ---------------- Opportunity Radar ----------------
class OpportunityRadar(BaseModel):
    search_velocity_percentage: str = Field(description="Search volume acceleration (e.g. '+420%')")
    content_saturation_level: str = Field(description="'LOW', 'MEDIUM', or 'SATURATED'")
    freshness_score: int = Field(description="Score from 0 to 100 for topic novelty", ge=0, le=100)
    estimated_viral_window_hours: str = Field(description="Estimated window before saturation (e.g. '6-12 hours')")
    recommended_action: str = Field(description="'ACT_NOW', 'MONITOR', or 'IGNORE'")
    lifecycle_stage: str = Field(
        default="EMERGING",
        description="Single-snapshot lifecycle classification: 'EMERGING', 'ACCELERATING', 'BREAKOUT', or 'SATURATED'. "
                    "Computed deterministically from real signals (rank, traffic, coverage) - never DECAYING, which "
                    "would require historical data this system does not persist."
    )

    # Mathematical score breakdown (Total 100)
    velocity_score: int = Field(description="Points (max 25)", ge=0, le=25)
    recency_score: int = Field(description="Points (max 20)", ge=0, le=20)
    audience_relevance_score: int = Field(description="Points (max 20)", ge=0, le=20)
    saturation_score: int = Field(description="Points (max 15)", ge=0, le=15)
    hook_potential_score: int = Field(description="Points (max 10)", ge=0, le=10)
    brand_safety_score: int = Field(description="Points (max 10)", ge=0, le=10)
    total_opportunity_score: int = Field(description="Sum of all score components (0-100)", ge=0, le=100)

class CrossMarketGap(BaseModel):
    topic: str = Field(description="Topic trending in the baseline market")
    baseline_geo: str = Field(description="Market where the topic is already trending")
    baseline_rank: int = Field(description="Its rank position in the baseline market's trending list")
    baseline_search_volume: str = Field(description="Approximate traffic in the baseline market")
    target_geo: str = Field(description="Market where the topic is not yet visible")
    status: str = Field(default="NOT_YET_VISIBLE", description="Always 'NOT_YET_VISIBLE' - an observed absence, never a predicted arrival time")


class FilteredSignal(BaseModel):
    query_or_topic: str = Field(description="Search query identified")
    opportunity_score: int = Field(description="Score from 0-100")
    verdict: str = Field(description="'SELECTED', 'BACKLOG', or 'DISCARDED'")
    reasoning: str = Field(description="Strategic reason for selection or rejection")

class TrendIntelligenceResult(BaseModel):
    niche: str = Field(description="Target niche analyzed")
    selected_opportunity: str = Field(description="The #1 winning breakout topic chosen by the agent")
    opportunity_radar: OpportunityRadar = Field(description="Full radar breakdown with transparent scores")
    signals_evaluated: list[FilteredSignal] = Field(description="List of top evaluated opportunities")
    strategic_angle: str = Field(description="Strategic editorial angle to bridge trend with brand goals")
    cross_market_gaps: list[CrossMarketGap] = Field(
        default_factory=list,
        description="Topics trending in the baseline market but not yet visible in target markets - real, observed gaps, not predicted arrival times."
    )
    target_market_geo: str = Field(default="", description="Target market checked for cross-market gaps, if any")

# ---------------- Script & Media ----------------
class ContentSlideOrScene(BaseModel):
    timestamp_or_slide_num: str = Field(description="e.g. '0-3s' or 'Slide 1'")
    spoken_audio_or_text: str = Field(description="Spoken voiceover or on-screen text")
    visual_action: str = Field(description="Visual action, b-roll, camera movement or on-screen graphics")

class HookAndScriptResult(BaseModel):
    draft_number: int = Field(default=1, description="Draft iteration number (1, 2, 3)")
    title: str = Field(description="High-converting title")
    platform: str = Field(description="Target platform ('tiktok', 'instagram_reels', 'instagram_carousel', 'linkedin')")
    hook_3s: str = Field(description="Pattern-interrupt 0-3s opening hook")
    story_scenes: list[ContentSlideOrScene] = Field(description="Scene or carousel slides breakdown")
    call_to_action: str = Field(description="High-converting CTA")
    caption: str = Field(description="Complete ready-to-post caption with linebreaks")

class HashtagStrategy(BaseModel):
    broad_reach_tags: list[str] = Field(description="Broad discovery tags (e.g. #IA #Tech)")
    niche_targeted_tags: list[str] = Field(description="Targeted community tags (e.g. #AutomatizacionAI)")
    trend_specific_tags: list[str] = Field(description="Breakout trend tags from Google")

class VisualDirectivesResult(BaseModel):
    cover_image_prompt: str = Field(description="Prompt for Imagen 3 or Midjourney")
    color_palette_mood: str = Field(description="Aesthetic mood & color palette")
    hashtags: HashtagStrategy = Field(description="Tiered hashtag cluster")
    best_posting_time_window: str = Field(description="Optimal posting time")

# ---------------- Critic Audit & Revision Loop ----------------
class CriticAuditEvaluation(BaseModel):
    draft_evaluated: int = Field(description="Draft number evaluated")
    hook_strength: int = Field(description="0-100 Hook score", ge=0, le=100)
    retention_pacing: int = Field(description="0-100 Pacing score", ge=0, le=100)
    value_density: int = Field(description="0-100 Value score", ge=0, le=100)
    overall_virality_score: int = Field(description="0-100 Calculated Virality Score", ge=0, le=100)
    status: str = Field(description="'APPROVED' if score >= 80, otherwise 'NEEDS_REVISION'")
    rejection_reasons: list[str] = Field(default=[], description="Reasons if rejected")
    strengths: list[str] = Field(description="Strongest elements")
    actionable_revision_instructions: str = Field(description="Exact instructions for ScriptHookAgent if revision is needed")

class CompleteCampaignPayload(BaseModel):
    topic: str
    platform: str
    brand_memory: BrandMemory
    trend_intelligence: TrendIntelligenceResult
    script_and_content: HookAndScriptResult
    visual_and_metadata: VisualDirectivesResult
    virality_audit: CriticAuditEvaluation
    total_revision_cycles: int = Field(default=1, description="How many draft loops were executed")
