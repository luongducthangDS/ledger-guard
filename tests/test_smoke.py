"""Baseline happy-path tests: the suite a team typically starts with."""


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_open_account_starts_empty_and_active(ledger):
    acc = ledger.open_account()
    assert acc["balance"] == 0
    assert acc["status"] == "ACTIVE"


def test_deposit_increases_balance(ledger):
    acc = ledger.open_account(balance=100_000)
    assert ledger.balance(acc["id"]) == 100_000


def test_transfer_moves_money(ledger):
    a = ledger.open_account(balance=100_000)
    b = ledger.open_account()
    r = ledger.transfer(a["id"], b["id"], 40_000)
    assert r.status_code == 201
    assert r.json()["status"] == "COMPLETED"
    assert ledger.balance(a["id"]) == 60_000
    assert ledger.balance(b["id"]) == 40_000


def test_transfer_insufficient_funds_rejected(ledger):
    a = ledger.open_account(balance=10_000)
    b = ledger.open_account()
    r = ledger.transfer(a["id"], b["id"], 50_000)
    assert r.status_code == 409
    assert r.json()["code"] == "INSUFFICIENT_FUNDS"


def test_unknown_account_returns_404(client):
    assert client.get("/accounts/does-not-exist").status_code == 404
