# LedgerGuard: risk-based testing of a money-transfer API

A small banking-style transfer API (FastAPI + PostgreSQL) and the test suite that broke it.
The point of the project is the **testing**: how to find the defects that matter in a payment system, prove they
are fixed, and measure whether the tests are any good.

## Results (all measured, evidence in `docs/evidence/`)

| What | Before | After |
|---|---|---|
| Automated tests | 6 happy-path tests | **89** (BVA, decision table, state transitions, idempotency, Hypothesis stateful, concurrency, contract regressions) |
| Defects found and fixed | — | **12**: 4 critical money-integrity bugs, 6 major, 2 minor ([defect log](docs/defect-log.md)) |
| Mutation score (mutmut) | **48.0 %** (123 / 256 killed) | **98.7 %** (313 / 317 killed; the 4 survivors only change error-message wording) |
| Branch coverage | — | **100 %** |
| HTTP 500s found by Schemathesis (1,000+ generated requests) | 2 kinds | **0** |
| Daily-limit query at 2M transfers | 298 ms (full scan) | **0.12 ms** (partial index) |
| p95 latency at 20 req/s, 50 hot accounts | **57.9 s**, 3.9 % errors | **245 ms**, 0 % errors |
| Capacity at SLO p95 < 300 ms, 500 accounts, 2 vCPU | — | **100 req/s at p95 16.8 ms**; saturates at ~135 req/s |

The git history is the real order of events: `v0.1 + baseline tests` → `risk-based suite (7 failing)` →
`fix 7 defects` → `fix 4 contract defects`.

## The system under test

| Endpoint | Rule being tested |
|---|---|
| `POST /accounts`, `/deposits` | Integer VND only, deposit 1 – 2bn, owner without control characters |
| `POST /transfers` | 10,000 ≤ amount ≤ 500,000,000 · source ≠ target · both accounts ACTIVE · enough balance · daily outgoing limit · mandatory `Idempotency-Key` |
| `POST /transfers/{id}/reverse` | Only `COMPLETED → REVERSED`, once, and only if the receiver still holds the money |
| `POST /accounts/{id}/freeze · unfreeze · close` | State machine `ACTIVE ⇄ FROZEN → CLOSED`; close only at zero balance |

Rejected transfers are stored as `FAILED` for audit. Every error uses one contract: `{code, detail, transfer_id?, errors?}`.

## Test strategy

The test design starts from risk: what would hurt a bank most?

| Risk | Technique | File |
|---|---|---|
| Wrong amounts slip through | Boundary values + equivalence partitions (min−1, min, max, max+1, zero, negative, wrong types) | `test_transfer_validation.py` |
| Rules evaluated in the wrong order | Decision table: 4 conditions → 16 combinations, first failing rule wins | `test_transfer_rules.py` |
| Illegal state changes | State-transition tables for accounts (9 transitions) and transfers | `test_state_transitions.py` |
| Duplicate payment on client retry | Idempotency: replay, different payload, failed replay | `test_transfer_rules.py` |
| Rare sequences nobody thinks of | Hypothesis stateful machine: random sequences of 25 operations, checking after every step that money is conserved, no balance goes negative, and balances match a model | `test_properties.py` |
| Race conditions | 20 requests released at once by a barrier, against a real uvicorn server and Postgres | `test_concurrency.py` |
| API breaks on unexpected input | Schemathesis OpenAPI fuzzing (coverage, fuzzing and stateful phases) | CI |
| Slow under load | k6 with SLO thresholds, `EXPLAIN ANALYZE` to find the cause | `perf/` |
| Weak tests that pass anyway | Mutation testing with mutmut | CI (nightly) |

## Run it

```bash
docker compose up -d                       # Postgres 16 with databases ledger + ledger_test
pip install -r requirements-dev.txt
pytest                                     # 89 tests, ~90 s (concurrency tests start a real server)
pytest -m "not concurrency"                # fast loop, ~60 s
pytest --cov=app --cov-branch              # coverage
mutmut run && mutmut results               # mutation testing (~10 min)

uvicorn app.main:app --port 8000           # API + docs at http://localhost:8000/docs
schemathesis run http://localhost:8000/openapi.json --checks all -H "Idempotency-Key: local-fuzz-key"
python perf/seed_history.py 2000000        # production-sized history
k6 run -e RATE=100 -e ACCOUNTS=500 perf/transfer_load.js
```

## Design decisions (and why)

- **Integer VND, never float.** `0.1 + 0.2 != 0.3` is a classic money bug; VND has no minor unit.
- **Row locks taken in id order.** `SELECT … FOR UPDATE` serialises writers on the same account. Locking in a
  fixed order means A→B and B→A in parallel cannot deadlock.
- **Unique index for idempotency, not "check then insert".** The check-then-insert version created 14 transfers
  for one key (LG-03). Only the database can make "exactly one" true under concurrency.
- **Store rejected transfers.** Auditors and support staff need to see failed attempts. The test for LG-06 asked
  "how would a client find this record?" and showed that it couldn't.
- **Tests hit real Postgres, not SQLite.** SQLite has no row-level locking, so none of LG-01…LG-05 would have
  been found.
- **Mutation score over coverage.** The baseline had high line coverage on the happy path but a mutation score of
  48 %: tests ran the code without checking it.

## Known limits

- Hot accounts serialise: with 50 accounts sharing all traffic, p95 at 50 req/s is 724 ms. That is the cost of
  correct locking. A real bank would shard hot merchants or batch postings.
- Numbers come from one 2-vCPU machine shared by Postgres, the API and k6. Treat them as relative, not absolute.
- No authentication. It is out of scope for this test-focused project.

## Interview questions this project prepares for

1. Why did the lost-update bug lose money only on the *receiving* account?
2. What is the difference between coverage and mutation score, and why was one 100 % while the other was 48 %?
3. Why does locking in id order prevent deadlocks?
4. Why is a check-then-insert idempotency check not enough, even inside a transaction?
5. Why was p95 bimodal (median 11 ms, p90 201 ms) at 20 req/s with 50 accounts?
6. Which Schemathesis finding would you push back on, and why?
