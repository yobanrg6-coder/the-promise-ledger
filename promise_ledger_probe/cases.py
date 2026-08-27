"""
Real, hand-picked cases for the viability probe. Text excerpts are copied
verbatim from the linked announcements; evidence URLs are official pages.
"""

CASES = [
    # ---------------------------------------------------------------- #
    # A) Falsifiable + on-time  -> expect FULFILLED
    # ---------------------------------------------------------------- #
    {
        "name": "Anthropic - Claude 3.5 Haiku 'later this month' (Oct 2024)",
        "announcement_url": "https://www.anthropic.com/news/3-5-models-and-computer-use",
        "published": "2024-10-22",
        "announcement_text": (
            "Today, we're announcing an upgraded Claude 3.5 Sonnet, and a new model, "
            "Claude 3.5 Haiku. The new Claude 3.5 Haiku will be released later this month. "
            "Claude 3.5 Haiku will be made available later this month across our first-party API, "
            "Amazon Bedrock, and Google Cloud's Vertex AI - initially as a text-only model and "
            "with image input to follow."
        ),
        "evidence_url": "https://platform.claude.com/docs/en/about-claude/model-deprecations",
    },

    # ---------------------------------------------------------------- #
    # B) Falsifiable, deadline long passed -> expect DELAYED (or FULFILLED-late)
    # ---------------------------------------------------------------- #
    {
        "name": "Apple - personalized Siri (onscreen awareness / in-app actions), WWDC June 2024",
        "announcement_url": "https://www.apple.com/newsroom/2024/06/introducing-apple-intelligence-for-iphone-ipad-and-mac/",
        "published": "2024-06-10",
        "announcement_text": (
            "With Apple Intelligence, Siri will be able to take hundreds of new actions in and "
            "across Apple and third-party apps. With onscreen awareness, Siri will be able to "
            "understand and take action with users' content in more apps over time. Siri will be "
            "able to deliver intelligence that's tailored to the user and their on-device information. "
            "Apple Intelligence features will be available in beta starting in fall 2024 as part of "
            "iOS 18, iPadOS 18, and macOS Sequoia in U.S. English only. Some features, software "
            "platforms, and additional languages will come over the course of the next year."
        ),
        "evidence_url": "https://www.apple.com/newsroom/2024/10/apple-intelligence-is-available-today-on-iphone-ipad-and-mac/",
    },

    # ---------------------------------------------------------------- #
    # C) Vague / aspirational -> expect REJECT at the falsifiability gate
    # ---------------------------------------------------------------- #
    {
        "name": "Vague commitment (control case) -> must be rejected",
        "announcement_url": "https://example.com/blog/our-vision",
        "published": "2026-01-15",
        "announcement_text": (
            "As we build the future of AI, we remain deeply committed to making our tools more "
            "helpful, more accessible, and safer for everyone. We believe agents will transform how "
            "people work, and we're excited to share more soon. This is just the beginning of our "
            "journey toward truly capable AI that benefits all of humanity."
        ),
        "evidence_url": "https://example.com/",
    },
]
