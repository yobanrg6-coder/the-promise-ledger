"""
End-to-end check through the REAL modules (not the probe's inline slice):
  PromiseLedgerOrchestrator -> falsifiability gate -> InMemory store
  -> ledger.verifier (zero-LLM, real HTTP) -> scorecard

Run: venv\Scripts\python.exe -m promise_ledger_probe.e2e_real_modules
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import sys

from agents.promise_orchestrator import PromiseLedgerOrchestrator
from agents.promise_schemas import LedgerPromise
from ledger import promises as ledger
from ledger.promises import InMemoryBackend
from ledger.verifier import verify_promise
from promise_ledger_probe.cases import CASES

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CHECK_DATE = dt.date(2026, 8, 27)


async def main() -> None:
    be = InMemoryBackend()
    orch = PromiseLedgerOrchestrator()

    for c in CASES:
        print("=" * 92)
        print("CASE:", c["name"])
        print("=" * 92)
        promise_id = None
        async for ev in orch.process_announcement_stream(
            announcement_text=c["announcement_text"],
            source_url=c["announcement_url"],
            announced_date=c["published"],
            backend=be,
        ):
            t = ev["type"]
            if t == "agent_result":
                print(f"  [{ev['stage']}] {ev['agent']}:")
                print("   ", json.dumps(ev["data"], ensure_ascii=False)[:600])
            elif t == "decision_stop":
                print(f"  STOP: {ev['message']}")
            elif t == "complete":
                promise_id = ev["promise_id"]
                print(f"  ADMITTED: {promise_id}")
            else:
                print(f"  - {ev.get('message','')}")

        if not promise_id:
            print("\n  -> not admitted (expected for the vague control case)\n")
            continue

        row = ledger.get_promise(promise_id, backend=be)
        # override evidence_url with the case's curated static source
        row["evidence_url"] = c["evidence_url"]
        result = verify_promise(LedgerPromise(**row), check_date=CHECK_DATE)
        ledger.apply_verification(promise_id, result, backend=be)
        print(f"\n  VERIFIER -> {result.status.value}: {result.reason}")
        print(f"  evidence: {result.evidence_url}\n")

    print("=" * 92)
    print("SCORECARD")
    print("=" * 92)
    print(json.dumps(ledger.get_scorecard(backend=be), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
