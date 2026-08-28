# ledger/ - the verified store and the zero-LLM verifier

No model call anywhere in this package. A promise's status is only ever set by
deterministic code reading a public page.

## Files

| File | What it does |
|---|---|
| `promises.py` | The store. `admit_promise`, `apply_verification`, `get_promise`, `list_promises`, `due_for_check`, `get_scorecard`. Pluggable backend chosen by `LEDGER_BACKEND`: **`json`** (default, `data/ledger.json`), `firestore`, `memory`. |
| `evidence.py` | Pure HTTP + HTML-to-text. Fetches a page, strips scripts/markup, flags an empty SPA shell, and does whole-token keyword matching. No JS execution. |
| `verifier.py` | `verify_promise(promise, check_date)` -> a `PromiseStatus` from keyword presence + deadline arithmetic + a best-effort ship-date read. This is the decision table. |
| `run_cycle.py` | One verification pass: for every promise past its deadline and not yet final, re-fetch and re-decide. `python -m ledger.run_cycle`. Safe to run unattended - no API key. |
| `seed.py` / `seed_data.py` | Load the hand-curated demo promises through the gate, admit them, and verify them live. `python -m ledger.seed --fresh`. |

## Status decision table (deadline D, check date T)

```
fetch failed / SPA shell ....................... UNVERIFIABLE
majority of check_keywords present:
    a dated ship signal <= D on the page ....... FULFILLED
    a dated ship signal >  D ................... FULFILLED_LATE
    no dateable ship signal .................... FULFILLED   (reason notes date not established)
some (>=1) but not a majority present:
    T <= D .................................... PENDING
    T >  D .................................... PARTIALLY_FULFILLED
none present:
    T <= D .................................... PENDING
    D < T <= D + 180d ......................... DELAYED
    T >  D + 180d ............................. ABANDONED
```

"Majority" is a strict majority of the keyword count (min 2). `UNVERIFIABLE`
promises are re-checked each cycle until 180 days past the deadline, then they
stop being re-queued (a permanently dead URL would otherwise be re-fetched
forever).

## Storage

`JsonFileBackend` writes the whole ledger to one JSON file under a lock. The
seeded demo ships as `data/ledger.json` in the repo, so the app and the cycle
have real data with no cloud credentials. Cloud Run's per-instance disk is
ephemeral, so live writes there don't survive a cold start - the committed
file is the baseline. Set `LEDGER_BACKEND=firestore` for durable writes.
