"""State-transition testing for accounts and transfers (valid AND invalid transitions)."""
import pytest

# account state machine: (start state, action) -> expected resulting state or None if forbidden
ACCOUNT_TRANSITIONS = [
    ("ACTIVE", "freeze", "FROZEN"),
    ("ACTIVE", "unfreeze", None),
    ("ACTIVE", "close", "CLOSED"),
    ("FROZEN", "unfreeze", "ACTIVE"),
    ("FROZEN", "freeze", None),
    ("FROZEN", "close", "CLOSED"),
    ("CLOSED", "freeze", None),
    ("CLOSED", "unfreeze", None),
    ("CLOSED", "close", None),
]

_SETUP = {"ACTIVE": [], "FROZEN": ["freeze"], "CLOSED": ["close"]}


@pytest.mark.parametrize("start, action, end", ACCOUNT_TRANSITIONS,
                         ids=[f"{s}-{a}" for s, a, _ in ACCOUNT_TRANSITIONS])
def test_account_transitions(ledger, start, action, end):
    acc = ledger.open_account()
    for step in _SETUP[start]:
        assert ledger.set_status(acc["id"], step).status_code == 200
    r = ledger.set_status(acc["id"], action)
    if end is None:
        assert r.status_code == 409
        assert r.json()["code"] == "INVALID_STATUS_TRANSITION"
    else:
        assert r.status_code == 200
        assert r.json()["status"] == end


def test_cannot_close_account_with_money(ledger):
    acc = ledger.open_account(balance=10_000)
    r = ledger.set_status(acc["id"], "close")
    assert r.json()["code"] == "BALANCE_NOT_ZERO"
    assert ledger.http.get(f"/accounts/{acc['id']}").json()["status"] == "ACTIVE"


@pytest.mark.parametrize("state", ["FROZEN", "CLOSED"])
def test_no_deposit_into_inactive_account(ledger, state):
    acc = ledger.open_account()
    for step in _SETUP[state]:
        ledger.set_status(acc["id"], step)
    r = ledger.http.post(f"/accounts/{acc['id']}/deposits", json={"amount": 10_000})
    assert r.status_code == 409
    assert ledger.balance(acc["id"]) == 0


class TestTransferTransitions:
    def test_completed_to_reversed_restores_both_balances(self, ledger):
        a = ledger.open_account(balance=100_000)
        b = ledger.open_account()
        t = ledger.transfer(a["id"], b["id"], 40_000).json()
        r = ledger.reverse(t["id"])
        assert r.status_code == 200
        assert r.json()["status"] == "REVERSED"
        assert ledger.balance(a["id"]) == 100_000
        assert ledger.balance(b["id"]) == 0

    def test_reversed_cannot_be_reversed_again(self, ledger):
        a = ledger.open_account(balance=100_000)
        b = ledger.open_account()
        t = ledger.transfer(a["id"], b["id"], 40_000).json()
        ledger.reverse(t["id"])
        r = ledger.reverse(t["id"])
        assert r.status_code == 409
        assert ledger.balance(a["id"]) == 100_000

    def test_failed_transfer_cannot_be_reversed(self, ledger):
        a = ledger.open_account(balance=10_000)
        b = ledger.open_account(balance=500_000)
        failed = ledger.transfer(a["id"], b["id"], 50_000)
        assert failed.status_code == 409
        # rejected transfers are stored for audit, so the client must get their id back
        transfer_id = failed.json().get("transfer_id")
        assert transfer_id, "409 response does not expose the id of the stored FAILED transfer"
        assert ledger.http.get(f"/transfers/{transfer_id}").json()["status"] == "FAILED"
        r = ledger.reverse(transfer_id)
        assert r.status_code == 409
        assert ledger.balance(a["id"]) == 10_000
        assert ledger.balance(b["id"]) == 500_000

    def test_reverse_rejected_when_target_already_spent_money(self, ledger):
        a = ledger.open_account(balance=100_000)
        b = ledger.open_account()
        c = ledger.open_account()
        t = ledger.transfer(a["id"], b["id"], 40_000).json()
        ledger.transfer(b["id"], c["id"], 30_000)
        r = ledger.reverse(t["id"])
        assert r.status_code == 409
        assert r.json()["code"] == "INSUFFICIENT_FUNDS"
        assert ledger.balance(b["id"]) == 10_000

    def test_reverse_unknown_transfer_404(self, ledger):
        assert ledger.reverse("no-such-transfer").status_code == 404
