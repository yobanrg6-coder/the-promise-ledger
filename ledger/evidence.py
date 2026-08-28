"""
Evidence fetching for The Promise Ledger - pure HTTP + text normalization,
NO LLM. The verifier's job is only as trustworthy as the page it reads, so
this module deliberately:

  - fetches raw HTML and strips it to visible text (no JS execution)
  - flags when a fetch looks like an empty SPA shell (so the verifier can
    return UNVERIFIABLE instead of a false "not shipped")
  - is meant to be pointed at STATIC, DATED, machine-checkable sources
    (changelogs, release-notes, pricing pages, docs) - not JS marketing
    landing pages, which the probe (27-ago-2026) showed produce false
    negatives.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

_UA = "Mozilla/5.0 (compatible; PromiseLedgerBot/1.0; +https://github.com/yobanrg6-coder/the-promise-ledger)"
_TAG_RE = re.compile(r"(?s)<[^>]+>")
# Drop executable / non-visible blocks. The `(?:</\1>|\Z)` tail means an
# unclosed <script> (broken markup) still gets stripped to end-of-document
# instead of leaking its JS source into evidence.text as fake "content".
_DROP_RE = re.compile(r"(?is)<(script|style|noscript|template|svg)\b.*?(?:</\1>|\Z)")
_WS_RE = re.compile(r"\s+")

# Below this many characters of extracted text, a 200 response is almost
# certainly a JS shell / nav chrome only - not real content to judge against.
_SHELL_TEXT_THRESHOLD = 600


@dataclass
class Evidence:
    url: str
    ok: bool
    text: str = ""
    looks_like_spa_shell: bool = False
    error: str = ""

    def excerpt_around(self, needle: str, radius: int = 180) -> str:
        i = self.text.lower().find(needle.lower())
        if i < 0:
            return self.text[:2 * radius].strip()
        return self.text[max(0, i - radius): i + radius].strip()


def fetch_evidence(url: str, timeout: float = 25.0) -> Evidence:
    if not url:
        return Evidence(url=url, ok=False, error="no evidence url")
    try:
        r = httpx.get(url, follow_redirects=True, timeout=timeout, headers={"User-Agent": _UA})
    except Exception as e:  # noqa: BLE001 - any transport error is just "couldn't fetch"
        return Evidence(url=url, ok=False, error=f"{type(e).__name__}: {e}")

    if r.status_code != 200:
        return Evidence(url=str(r.url), ok=False, error=f"HTTP {r.status_code}")

    html = _DROP_RE.sub(" ", r.text)
    text = _WS_RE.sub(" ", _TAG_RE.sub(" ", html)).strip()
    return Evidence(
        url=str(r.url),
        ok=True,
        text=text,
        looks_like_spa_shell=len(text) < _SHELL_TEXT_THRESHOLD,
    )


def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    """Keywords that appear in `text` as whole tokens.

    Uses non-word-char lookarounds instead of a raw substring test so short
    tokens ("GA", "Pro", "iOS", "v2") don't get false hits inside ordinary
    prose ("navigation", "improving", "studios", "revamp"). A keyword whose
    own edge is a non-word char (e.g. ".NET", "C++") falls back to a plain
    substring check, since a lookaround there would never anchor.
    """
    hits: list[str] = []
    for k in keywords:
        needle = k.strip()
        if not needle:
            continue
        left = r"(?<!\w)" if needle[0].isalnum() or needle[0] == "_" else ""
        right = r"(?!\w)" if needle[-1].isalnum() or needle[-1] == "_" else ""
        if re.search(left + re.escape(needle) + right, text, re.IGNORECASE):
            hits.append(k)
    return hits
