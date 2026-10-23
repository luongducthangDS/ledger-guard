# Defect log

Every defect below was found by a test in this repo, reproduced, fixed, and is now guarded by a regression test.
Severity: **Critical** = money is created, destroyed or moved wrongly · **Major** = wrong behaviour or outage, no money lost · **Minor** = contract or usability issue.

| ID | Severity | Found by | Summary | Evidence (before fix) | Fix | Regression test |
|---|---|---|---|---|---|---|
| LG-01 | Critical | Concurrency test | **Lost update:** 20 parallel incoming transfers of 10,000 VND credited the target with 20,000 instead of 200,000. 180,000 VND disappeared. | `docs/evidence/v0.1_test_run.txt` | `SELECT … FOR UPDATE` on both accounts | `test_no_lost_update_when_many_senders_pay_one_account` |
| LG-02 | Critical | Concurrency test | **Double-spend:** an account holding 100,000 sent 20 × 10,000 in parallel and all 20 succeeded (10 expected). | same | Row lock on the source before the balance check | `test_no_double_spend_under_parallel_transfers` |
| LG-03 | Critical | Concurrency test | **Idempotency race:** 20 retries with one `Idempotency-Key` created 14 transfers. | same | Unique index on the key; on `IntegrityError` replay the winner | `test_retry_storm_with_same_idempotency_key_creates_one_transfer` |
| LG-04 | Major | Concurrency test | **Daily-limit bypass:** with a 100,000 limit, 20 parallel transfers of 10,000 all passed. | same | Limit check runs while the source row is locked | `test_daily_limit_holds_under_parallel_transfers` |
| LG-05 | Critical | Concurrency test | **Double reversal:** 5 of 20 parallel reverse calls succeeded, paying the sender back 5 times. | same | Lock the transfer row before checking its status | `test_parallel_reversal_happens_once` |
| LG-06 | Minor | State-transition test | Rejected transfers are stored as `FAILED`, but the 409 response did not return their id, so a client could never look them up. | same | `transfer_id` added to the error body | `test_failed_transfer_cannot_be_reversed` |
| LG-07 | Major | Idempotency test | Retrying a rejected request returned **200 OK**, so a client could believe a failed payment succeeded. | same | A replay returns the original 409 and code | `test_replay_of_failed_transfer_returns_the_same_failure` |
| LG-08 | Major | Schemathesis | A NUL byte in an id or owner name caused **HTTP 500** (Postgres cannot store `\x00`). | `docs/evidence/schemathesis_v1.0_run.txt` | Ids must match `^[A-Za-z0-9-]{1,36}$`; text fields reject control characters | `test_malformed_account_ids_are_422_not_500`, `test_owner_with_control_characters_rejected` |
| LG-09 | Major | Schemathesis | A `daily_limit` larger than `BIGINT` caused **HTTP 500**. | same | Upper bound of 100 bn VND | `test_daily_limit_above_max_is_422_not_bigint_overflow` |
| LG-10 | Minor | Schemathesis | 422 and 400 bodies did not match the documented error schema (`detail` was a list, `code` missing). | same | One error contract for every 4xx | `test_validation_errors_follow_the_documented_error_contract` |
| LG-11 | Minor | Schemathesis | The fix for LG-10 dropped the `Allow` header from 405 responses. This was a regression introduced by the fix itself. | run during fix | Keep exception headers | Schemathesis `unsupported_method` check in CI |
| LG-12 | Major | k6 + `EXPLAIN ANALYZE` | The daily-limit query did a full scan: **298 ms per transfer** at 2M rows. At 20 req/s p95 was **57.9 s** and 3.9 % of requests timed out. | `docs/evidence/perf/hot50acc_before_index_20rps.json` | Partial index `(source_id, created_at) WHERE status='COMPLETED'` → 0.12 ms | k6 SLO gate in CI |

## Accepted deviation

- Schemathesis `ST202` reports that `{"source_id":"0","target_id":"0"}` is schema-valid but rejected. The rule
  "source differs from target" cannot be expressed in JSON Schema, so it is documented in the model description,
  tested by `test_same_source_and_target_rejected`, and that single check is excluded in CI.
