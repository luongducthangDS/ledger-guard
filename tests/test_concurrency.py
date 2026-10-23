"""Concurrency tests: real uvicorn server, real Postgres, parallel HTTP requests.

These target the bugs single-threaded tests cannot see: lost updates, double-spend,
duplicate transfers under a retry storm, double reversal.
"""
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
import uvicorn

from app.main import app

pytestmark = pytest.mark.concurrency
WORKERS = 20


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base_url():
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(f"{url}/health")
            break
        except httpx.TransportError:
            time.sleep(0.05)
    yield url
    server.should_exit = True
    thread.join(timeout=5)


def _open(url, balance=0, daily_limit=None) -> str:
    body = {"owner": "conc"} | ({"daily_limit": daily_limit} if daily_limit else {})
    acc = httpx.post(f"{url}/accounts", json=body).json()
    if balance:
        httpx.post(f"{url}/accounts/{acc['id']}/deposits", json={"amount": balance})
    return acc["id"]


def _balance(url, acc) -> int:
    return httpx.get(f"{url}/accounts/{acc}").json()["balance"]


def _parallel(fn, n=WORKERS):
    barrier = threading.Barrier(n)

    def run(i):
        barrier.wait()  # release all requests at the same instant
        return fn(i)

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(run, range(n)))


def test_no_double_spend_under_parallel_transfers(base_url):
    """100k balance, 20 parallel transfers of 10k: exactly 10 may succeed."""
    src = _open(base_url, balance=100_000)
    targets = [_open(base_url) for _ in range(WORKERS)]

    def send(i):
        return httpx.post(f"{base_url}/transfers", timeout=30,
                          json={"source_id": src, "target_id": targets[i], "amount": 10_000},
                          headers={"Idempotency-Key": uuid.uuid4().hex}).status_code

    codes = _parallel(send)
    received = sum(_balance(base_url, t) for t in targets)
    assert codes.count(201) == 10, codes
    assert _balance(base_url, src) == 0
    assert received == 100_000  # money conserved


def test_no_lost_update_when_many_senders_pay_one_account(base_url):
    target = _open(base_url)
    sources = [_open(base_url, balance=10_000) for _ in range(WORKERS)]

    def send(i):
        return httpx.post(f"{base_url}/transfers", timeout=30,
                          json={"source_id": sources[i], "target_id": target, "amount": 10_000},
                          headers={"Idempotency-Key": uuid.uuid4().hex}).status_code

    codes = _parallel(send)
    assert codes.count(201) == WORKERS, codes
    assert _balance(base_url, target) == WORKERS * 10_000


def test_retry_storm_with_same_idempotency_key_creates_one_transfer(base_url):
    src = _open(base_url, balance=1_000_000)
    tgt = _open(base_url)
    key = uuid.uuid4().hex

    def send(_):
        return httpx.post(f"{base_url}/transfers", timeout=30,
                          json={"source_id": src, "target_id": tgt, "amount": 10_000},
                          headers={"Idempotency-Key": key})

    responses = _parallel(send)
    ids = {r.json().get("id") for r in responses if r.status_code in (200, 201)}
    assert all(r.status_code in (200, 201) for r in responses), [r.status_code for r in responses]
    assert len(ids) == 1, f"{len(ids)} transfers created for one key"
    assert _balance(base_url, tgt) == 10_000


def test_daily_limit_holds_under_parallel_transfers(base_url):
    src = _open(base_url, balance=10_000_000, daily_limit=100_000)
    tgt = _open(base_url)

    def send(_):
        return httpx.post(f"{base_url}/transfers", timeout=30,
                          json={"source_id": src, "target_id": tgt, "amount": 10_000},
                          headers={"Idempotency-Key": uuid.uuid4().hex}).status_code

    codes = _parallel(send)
    assert codes.count(201) == 10, codes
    assert _balance(base_url, tgt) == 100_000


def test_parallel_reversal_happens_once(base_url):
    src = _open(base_url, balance=100_000)
    tgt = _open(base_url, balance=1_000_000)
    t = httpx.post(f"{base_url}/transfers", json={"source_id": src, "target_id": tgt, "amount": 50_000},
                   headers={"Idempotency-Key": uuid.uuid4().hex}).json()

    codes = _parallel(lambda _: httpx.post(f"{base_url}/transfers/{t['id']}/reverse", timeout=30).status_code)
    assert codes.count(200) == 1, codes
    assert _balance(base_url, src) == 100_000
    assert _balance(base_url, tgt) == 1_000_000
