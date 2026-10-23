"""Property-based (stateful) testing with Hypothesis.

Hypothesis generates random sequences of operations and checks ledger invariants after every step:
  I1  money is conserved: sum(balances) == sum(deposits)
  I2  no account balance is ever negative
  I3  every balance equals a simple in-memory model of the ledger
When an invariant breaks, Hypothesis shrinks the sequence to the shortest reproducing example.
"""
import uuid

import hypothesis.strategies as st
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, settings
from hypothesis.stateful import Bundle, RuleBasedStateMachine, initialize, invariant, rule
from sqlalchemy import text

from app.db import engine
from app.main import app

amounts = st.integers(min_value=10_000, max_value=600_000)


class LedgerMachine(RuleBasedStateMachine):
    accounts = Bundle("accounts")

    @initialize()
    def reset(self):
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE transfers, accounts CASCADE"))
        self.http = TestClient(app)
        self.model: dict[str, int] = {}
        self.status: dict[str, str] = {}
        self.completed: dict[str, tuple[str, str, int]] = {}
        self.deposited = 0

    @rule(target=accounts)
    def open_account(self):
        acc = self.http.post("/accounts", json={"owner": "prop"}).json()
        self.model[acc["id"]] = 0
        self.status[acc["id"]] = "ACTIVE"
        return acc["id"]

    @rule(acc=accounts, amount=amounts)
    def deposit(self, acc, amount):
        r = self.http.post(f"/accounts/{acc}/deposits", json={"amount": amount})
        if self.status[acc] == "ACTIVE":
            assert r.status_code == 200
            self.model[acc] += amount
            self.deposited += amount
        else:
            assert r.status_code == 409

    @rule(src=accounts, tgt=accounts, amount=amounts)
    def transfer(self, src, tgt, amount):
        r = self.http.post("/transfers", json={"source_id": src, "target_id": tgt, "amount": amount},
                           headers={"Idempotency-Key": uuid.uuid4().hex})
        if src == tgt:
            assert r.status_code == 422
            return
        ok = (self.status[src] == "ACTIVE" and self.status[tgt] == "ACTIVE"
              and self.model[src] >= amount)
        # daily limit (1bn default) is far above what this machine can move, so it never triggers
        if ok:
            assert r.status_code == 201, r.text
            self.model[src] -= amount
            self.model[tgt] += amount
            self.completed[r.json()["id"]] = (src, tgt, amount)
        else:
            assert r.status_code == 409, r.text

    @rule(data=st.data())
    def reverse(self, data):
        if not self.completed:
            return
        tid = data.draw(st.sampled_from(sorted(self.completed)))
        src, tgt, amount = self.completed[tid]
        r = self.http.post(f"/transfers/{tid}/reverse")
        if self.model[tgt] >= amount:
            assert r.status_code == 200, r.text
            self.model[tgt] -= amount
            self.model[src] += amount
            del self.completed[tid]
        else:
            assert r.status_code == 409

    @rule(acc=accounts, action=st.sampled_from(["freeze", "unfreeze"]))
    def toggle_freeze(self, acc, action):
        r = self.http.post(f"/accounts/{acc}/{action}")
        if r.status_code == 200:
            self.status[acc] = r.json()["status"]

    def _balances(self) -> dict[str, int]:
        return {a: self.http.get(f"/accounts/{a}").json()["balance"] for a in self.model}

    @invariant()
    def money_is_conserved(self):
        if self.model:
            assert sum(self._balances().values()) == self.deposited

    @invariant()
    def no_negative_balance_and_matches_model(self):
        balances = self._balances()
        assert all(b >= 0 for b in balances.values()), balances
        assert balances == self.model


LedgerMachine.TestCase.settings = settings(
    max_examples=60, stateful_step_count=25, deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
TestLedgerProperties = LedgerMachine.TestCase
