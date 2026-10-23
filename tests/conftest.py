import os
import uuid

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/ledger_test"
)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import Base, engine
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def _clean_tables():
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE transfers, accounts CASCADE"))
    yield


class LedgerClient:
    """Thin typed wrapper so tests read as business steps, not HTTP plumbing."""

    def __init__(self, http: TestClient):
        self.http = http

    def open_account(self, owner="test-user", balance=0, daily_limit=None) -> dict:
        body = {"owner": owner}
        if daily_limit is not None:
            body["daily_limit"] = daily_limit
        r = self.http.post("/accounts", json=body)
        assert r.status_code == 201, r.text
        acc = r.json()
        if balance:
            r = self.http.post(f"/accounts/{acc['id']}/deposits", json={"amount": balance})
            assert r.status_code == 200, r.text
            acc = r.json()
        return acc

    def balance(self, account_id: str) -> int:
        return self.http.get(f"/accounts/{account_id}").json()["balance"]

    def transfer(self, source: str, target: str, amount: int, key: str | None = None):
        return self.http.post(
            "/transfers",
            json={"source_id": source, "target_id": target, "amount": amount},
            headers={"Idempotency-Key": key or uuid.uuid4().hex},
        )

    def reverse(self, transfer_id: str):
        return self.http.post(f"/transfers/{transfer_id}/reverse")

    def set_status(self, account_id: str, action: str):
        return self.http.post(f"/accounts/{account_id}/{action}")


@pytest.fixture
def client():
    with TestClient(app) as http:
        yield http


@pytest.fixture
def ledger(client) -> LedgerClient:
    return LedgerClient(client)
