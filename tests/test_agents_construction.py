"""
Construction smoke tests for the 5 Google ADK agents.
No network call is made here (that needs a real GEMINI_API_KEY and lives outside
CI) - this only proves every agent + a Runner wires together without raising,
which is exactly the check that would have caught the broken
`from schemas import ViralityAuditResult` import before it shipped.
"""

import os
import sys

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agents"))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("GEMINI_API_KEY", "dummy-for-construction-check")

from google.adk.runners import InMemoryRunner

from gemma_auditor_agent import create_gemma_auditor_agent
from trend_scout import create_trend_scout_agent
from script_engineer import create_script_engineer_agent
from virality_auditor import create_virality_auditor_agent
from visual_director import create_visual_director_agent


def test_all_five_agents_construct_and_run_with_a_runner():
    agents = [
        create_trend_scout_agent(mcp_url="http://127.0.0.1:8081/mcp"),
        create_script_engineer_agent(),
        create_virality_auditor_agent(),
        create_gemma_auditor_agent(),
        create_visual_director_agent(),
    ]
    for agent in agents:
        runner = InMemoryRunner(agent=agent, app_name="topicahead-test")
        assert runner.agent is agent
        assert runner.session_service is not None
        assert agent.model is not None
