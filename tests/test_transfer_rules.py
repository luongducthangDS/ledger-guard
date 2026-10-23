"""Business rules on transfers, designed with a decision table.

Conditions (in the order the API must evaluate them):
  C1 source ACTIVE?  C2 target ACTIVE?  C3 balance >= amount?  C4 sent_today + amount <= daily_limit?
Exactly one outcome per combination; the first failing condition wins.
"""
import itertools

import pytest

AMOUNT = 50_000


def _expected(src_active, tgt_active, funds_ok, limit_ok):
    if not src_active:
        return 409, "SOURCE_NOT_ACTIVE"
    if not tgt_active:
        return 409, "TARGET_NOT_ACTIVE"
    if not funds_ok:
        return 409, "INSUFFICIENT_FUNDS"
    if not limit_ok:
        return 409, "DAILY_LIMIT_EXCEEDED"
    return 201, None


DECISION_TABLE = list(itertools.product([True, False], repeat=4))


@pytest.mark.parametrize(
    "src_active, tgt_active, funds_ok, limit_ok",
    DECISION_TABLE,
    ids=[f"src={'A' if s else 'F'}-tgt={'A' if t else 'F'}-funds={'ok' if f else 'low'}-limit={'ok' if l else 'over'}"
         for s, t, f, l in DECISION_TABLE],
)
def test_transfer_decision_table(ledger, src_active, tgt_active, funds_ok, limit_ok):
    balance = AMOUNT if funds_ok else AMOUNT - 1
    daily_limit = AMOUNT if limit_ok else AMOUNT - 1
    src = ledger.open_account(balance=balance, daily_limit=max(daily_limit, 10_000))
    tgt = ledger.open_account()
    if not src_active:
        ledger.set_status(src["id"], "freeze")
    if not tgt_active:
        ledger.set_status(tgt["id"], "freeze")

    r = ledger.transfer(src["id"], tgt["id"], AMOUNT)

    status, code = _expected(src_active, tgt_active, funds_ok, limit_ok)
    assert r.status_code == status, r.text
    if code:
        assert r.json()["code"] == code
        assert ledger.balance(src["id"]) == balance  # rejected transfer moves nothing
        assert ledger.balance(tgt["id"]) == 0
    else:
        assert ledger.balance(src["id"]) == balance - AMOUNT
        assert ledger.balance(tgt["id"]) == AMOUNT


def test_closed_target_rejected(ledger):
    src = ledger.open_account(balance=100_000)
    tgt = ledger.open_account()
    assert ledger.set_status(tgt["id"], "close").status_code == 200
    r = ledger.transfer(src["id"], tgt["id"], 10_000)
    assert r.json()["code"] == "TARGET_NOT_ACTIVE"


class TestDailyLimit:
    LIMIT = 100_000

    def test_limit_reached_exactly_then_exceeded(self, ledger):
        src = ledger.open_account(balance=1_000_000, daily_limit=self.LIMIT)
        tgt = ledger.open_account()
        assert ledger.transfer(src["id"], tgt["id"], 60_000).status_code == 201
        assert ledger.transfer(src["id"], tgt["id"], 40_000).status_code == 201   # sum == limit
        r = ledger.transfer(src["id"], tgt["id"], 10_000)                         # sum == limit + 10k
        assert r.json()["code"] == "DAILY_LIMIT_EXCEEDED"

    def test_failed_transfers_do_not_consume_limit(self, ledger):
        src = ledger.open_account(balance=50_000, daily_limit=self.LIMIT)
        tgt = ledger.open_account()
        assert ledger.transfer(src["id"], tgt["id"], 90_000).json()["code"] == "INSUFFICIENT_FUNDS"
        assert ledger.transfer(src["id"], tgt["id"], 50_000).status_code == 201

    def test_reversed_transfers_release_limit(self, ledger):
        src = ledger.open_account(balance=1_000_000, daily_limit=self.LIMIT)
        tgt = ledger.open_account()
        t = ledger.transfer(src["id"], tgt["id"], 100_000).json()
        assert ledger.reverse(t["id"]).status_code == 200
        assert ledger.transfer(src["id"], tgt["id"], 100_000).status_code == 201

    def test_incoming_money_does_not_count(self, ledger):
        src = ledger.open_account(balance=1_000_000, daily_limit=self.LIMIT)
        other = ledger.open_account(balance=1_000_000)
        assert ledger.transfer(other["id"], src["id"], 500_000).status_code == 201
        assert ledger.transfer(src["id"], other["id"], 100_000).status_code == 201


class TestIdempotency:
    def test_replay_returns_original_and_moves_money_once(self, ledger):
        a = ledger.open_account(balance=100_000)
        b = ledger.open_account()
        first = ledger.transfer(a["id"], b["id"], 30_000, key="order-0001")
        again = ledger.transfer(a["id"], b["id"], 30_000, key="order-0001")
        assert first.status_code == 201
        assert again.status_code == 200
        assert again.json()["id"] == first.json()["id"]
        assert ledger.balance(a["id"]) == 70_000

    def test_same_key_different_payload_rejected(self, ledger):
        a = ledger.open_account(balance=100_000)
        b = ledger.open_account()
        ledger.transfer(a["id"], b["id"], 30_000, key="order-0002")
        r = ledger.transfer(a["id"], b["id"], 40_000, key="order-0002")
        assert r.status_code == 422
        assert r.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
        assert ledger.balance(a["id"]) == 70_000

    def test_replay_of_failed_transfer_returns_the_same_failure(self, ledger):
        """A retried request must get the same answer as the original (409), not a 200."""
        a = ledger.open_account(balance=10_000)
        b = ledger.open_account()
        first = ledger.transfer(a["id"], b["id"], 50_000, key="order-0003")
        again = ledger.transfer(a["id"], b["id"], 50_000, key="order-0003")
        assert first.status_code == 409
        assert again.status_code == 409
        assert again.json()["code"] == first.json()["code"] == "INSUFFICIENT_FUNDS"
