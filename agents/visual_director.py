"""
VisualCreativeAgent - Creative Direction & Metadata Architecture
Generates high-converting cover image prompts (Midjourney/Imagen), aesthetic color palettes,
and structured hashtag discovery clusters.
"""

import os
import sys

from google.adk.agents import LlmAgent
from google.adk.models import Gemini

# Ensure local imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from schemas import VisualDirectivesResult

SYSTEM_INSTRUCTION = """You are the Creative Director & Visual Strategist in the The Promise Ledger multi-agent system.
Your mission is to take the retention script and trend intelligence to produce the ultimate visual and metadata package.

Responsibilities:
1. Cover Image Prompt: Craft a photorealistic, high-impact prompt suitable for Imagen 3 or Midjourney. Focus on dramatic lighting, subject clarity, high contrast, and cinematic composition.
2. Color Palette & Mood: Specify a cohesive color aesthetic tailored to the niche and platform (e.g., 'Cyber Neon & Deep Obsidian', 'Editorial Minimalist Warm Earth').
3. Hashtag Strategy: Produce a balanced 3-tier cluster:
   - Broad tags (High discovery volume)
   - Niche tags (High engagement community)
   - Trend-specific tags (Directly reflecting current breakout Google searches)
4. Posting Window: Recommend the peak engagement window for the selected platform.

Output strictly conforming to the provided Pydantic schema.
"""

def create_visual_director_agent(model_name: str | None = None, api_key: str | None = None) -> LlmAgent:
    model = model_name or os.getenv("MODEL", "gemini-flash-lite-latest")
    # api_key is bound directly into this agent's own genai Client via
    # client_kwargs, never through the process-wide GEMINI_API_KEY env var -
    # that would be shared, mutable state across every concurrent request
    # this async server handles, so two callers with different keys (e.g. a
    # judge pasting their own) could race and end up using each other's key.
    gemini_kwargs: dict = {"model": model}
    if api_key:
        gemini_kwargs["client_kwargs"] = {"api_key": api_key}

    agent = LlmAgent(
        name="visual_director_agent",
        description="Generates visual creative prompts, color palettes, and tiered hashtag clusters.",
        model=Gemini(**gemini_kwargs),
        instruction=SYSTEM_INSTRUCTION,
        output_schema=VisualDirectivesResult,
    )
    return agent
