"""Input validation: boundary value analysis + equivalence partitions on /transfers.

Rule: MIN_TRANSFER_VND (10,000) <= amount <= MAX_TRANSFER_VND (500,000,000), integer VND only.
"""
import pytest

from app.config import MAX_TRANSFER_VND, MIN_TRANSFER_VND

RICH = 2_000_000_000


@pytest.mark.parametrize(
    "amount, expected_status",
    [
        (MIN_TRANSFER_VND - 1, 422),   # just below min
        (MIN_TRANSFER_VND, 201),       # min
        (MIN_TRANSFER_VND + 1, 201),   # just above min
        (MAX_TRANSFER_VND - 1, 201),   # just below max
        (MAX_TRANSFER_VND, 201),       # max
        (MAX_TRANSFER_VND + 1, 422),   # just above max
        (0, 422),                      # zero partition
        (-MIN_TRANSFER_VND, 422),      # negative partition
    ],
    ids=["min-1", "min", "min+1", "max-1", "max", "max+1", "zero", "negative"],
)
def test_amount_boundaries(ledger, amount, expected_status):
    a = ledger.open_account(balance=RICH)
    b = ledger.open_account()
    r = ledger.transfer(a["id"], b["id"], amount)
    assert r.status_code == expected_status, r.text
    if expected_status == 422:  # rejected input must not move money
        assert ledger.balance(a["id"]) == RICH
        assert ledger.balance(b["id"]) == 0


@pytest.mark.parametrize("amount", ["10000", 10_000.5, None, True, "abc"])
def test_amount_must_be_integer_vnd(client, ledger, amount):
    a = ledger.open_account(balance=RICH)
    b = ledger.open_account()
    r = client.post("/transfers", json={"source_id": a["id"], "target_id": b["id"], "amount": amount},
                    headers={"Idempotency-Key": "k-type-check-01"})
    # pydantic lax mode accepts the numeric string "10000"; everything else must be rejected
    if amount == "10000":
        assert r.status_code == 201
    else:
        assert r.status_code == 422, f"{amount!r} accepted: {r.text}"


def test_same_source_and_target_rejected(ledger):
    a = ledger.open_account(balance=100_000)
    r = ledger.transfer(a["id"], a["id"], 10_000)
    assert r.status_code == 422
    assert ledger.balance(a["id"]) == 100_000


@pytest.mark.parametrize("missing", ["source", "target"])
def test_unknown_account_returns_404_and_moves_nothing(ledger, missing):
    a = ledger.open_account(balance=100_000)
    src, tgt = (("ghost", a["id"]) if missing == "source" else (a["id"], "ghost"))
    r = ledger.transfer(src, tgt, 10_000)
    assert r.status_code == 404
    assert r.json()["code"] == "ACCOUNT_NOT_FOUND"
    assert ledger.balance(a["id"]) == 100_000


@pytest.mark.parametrize("key, expected", [(None, 422), ("short", 422), ("k" * 8, 201), ("k" * 64, 201), ("k" * 65, 422)],
                         ids=["missing", "7-chars", "8-chars", "64-chars", "65-chars"])
def test_idempotency_key_header_boundaries(client, ledger, key, expected):
    a = ledger.open_account(balance=100_000)
    b = ledger.open_account()
    headers = {} if key is None else {"Idempotency-Key": key}
    r = client.post("/transfers", json={"source_id": a["id"], "target_id": b["id"], "amount": 10_000},
                    headers=headers)
    assert r.status_code == expected, r.text


@pytest.mark.parametrize("amount, expected", [(0, 422), (1, 200), (2_000_000_000, 200), (2_000_000_001, 422)])
def test_deposit_boundaries(client, ledger, amount, expected):
    a = ledger.open_account()
    r = client.post(f"/accounts/{a['id']}/deposits", json={"amount": amount})
    assert r.status_code == expected


# --- regressions for defects found by Schemathesis fuzzing -------------------------------------

@pytest.mark.parametrize("bad_id", ["g\x008", "a\x00", "../etc", "x" * 37])
def test_malformed_account_ids_are_422_not_500(ledger, bad_id):
    a = ledger.open_account(balance=100_000)
    r = ledger.transfer(bad_id, a["id"], 10_000)
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("owner", ["nul\x00byte", "tab\tname", ""])
def test_owner_with_control_characters_rejected(client, owner):
    r = client.post("/accounts", json={"owner": owner})
    assert r.status_code == 422


def test_daily_limit_above_max_is_422_not_bigint_overflow(client):
    r = client.post("/accounts", json={"owner": "x", "daily_limit": 10**19})
    assert r.status_code == 422


def test_validation_errors_follow_the_documented_error_contract(client):
    r = client.post("/transfers", json={}, headers={"Idempotency-Key": "contract-check-1"})
    body = r.json()
    assert r.status_code == 422
    assert set(body) >= {"code", "detail", "errors"}
    assert isinstance(body["detail"], str)
    assert {e["field"] for e in body["errors"]} >= {"body.source_id", "body.target_id", "body.amount"}


def test_invalid_json_body_is_a_documented_client_error(client):
    r = client.post("/accounts", content=b"\xff", headers={"Content-Type": "application/json"})
    assert r.status_code in (400, 422)
    assert set(r.json()) >= {"code", "detail"}
