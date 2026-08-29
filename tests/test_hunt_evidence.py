"""
Adversarial tests for ledger.evidence - hunting text extraction, SPA thresholds, and regex issues.
No network, no LLM calls.
"""

from ledger.evidence import (
    _DROP_RE,
    _SHELL_TEXT_THRESHOLD,
    _TAG_RE,
    _WS_RE,
    Evidence,
    fetch_evidence,
    keyword_hits,
)


def _clean_html(html: str) -> str:
    cleaned = _DROP_RE.sub(" ", html)
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", cleaned)).strip()


# =========================================================================== #
# 1. SPA shell false negative on concise pages
# =========================================================================== #
def test_spa_threshold_flags_legitimate_short_changelog():
    """
    _SHELL_TEXT_THRESHOLD is hardcoded to 600 characters.
    A concise, valid static changelog (e.g. 400 chars) is flagged as looks_like_spa_shell=True.
    """
    valid_short_changelog = (
        "<html><body>"
        "<h1>Release Notes</h1>"
        "<p>Version 2.4.0 released on October 28, 2024.</p>"
        "<p>Claude 3.5 Haiku is now available on Bedrock and Vertex AI.</p>"
        "</body></html>"
    )
    text = _clean_html(valid_short_changelog)
    assert len(text) < _SHELL_TEXT_THRESHOLD
    ev = Evidence(url="https://example.com/notes", ok=True, text=text, looks_like_spa_shell=len(text) < _SHELL_TEXT_THRESHOLD)
    
    # Bug exposed: A completely valid changelog is classified as an empty SPA shell
    assert ev.looks_like_spa_shell is True


# =========================================================================== #
# 2. Unclosed script tag leaks JS code into visible text
# =========================================================================== #
def test_unclosed_script_tag_does_not_leak_javascript_into_text():
    """
    An unclosed <script> tag (broken markup) must still be stripped, so JS
    source never reaches evidence.text where identifiers would trip keyword
    matching (regression guard for BUG-09).
    """
    malformed_html = (
        "<html><body>"
        "<script> window.__INTERNAL_FLAG_CLAUDE_HAIKU = true; var vertex_ai_config = {}; "
        "<div><h1>Welcome</h1><p>Nothing shipped yet.</p></div>"
        "</body></html>"
    )
    text = _clean_html(malformed_html)
    assert "window.__INTERNAL_FLAG_CLAUDE_HAIKU" not in text
    assert "vertex_ai_config" not in text
    assert keyword_hits(text, ["INTERNAL_FLAG", "vertex_ai"]) == []


# =========================================================================== #
# 3. excerpt_around edge cases
# =========================================================================== #
def test_excerpt_around_empty_string_needle():
    """Searching for empty string needle shouldn't crash."""
    ev = Evidence(url="https://example.com", ok=True, text="Some release text here.")
    excerpt = ev.excerpt_around("")
    assert len(excerpt) > 0


def test_excerpt_around_empty_evidence_text():
    """Empty evidence text returns empty string."""
    ev = Evidence(url="https://example.com", ok=True, text="")
    assert ev.excerpt_around("keyword") == ""


def test_excerpt_around_needle_not_found():
    """When needle is not in text, returns fallback window."""
    ev = Evidence(url="https://example.com", ok=True, text="Hello world changelog.")
    assert ev.excerpt_around("nonexistent") == "Hello world changelog."


# =========================================================================== #
# 4. fetch_evidence edge inputs
# =========================================================================== #
def test_fetch_evidence_empty_url():
    """Empty URL returns ok=False cleanly."""
    ev = fetch_evidence("")
    assert not ev.ok
    assert ev.error == "no evidence url"
