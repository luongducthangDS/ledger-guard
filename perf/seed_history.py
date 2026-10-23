"""Seed realistic transfer history so the daily-limit query runs against a production-sized table.

Usage: DATABASE_URL=... python perf/seed_history.py 500000
Rows are COMPLETED transfers spread over the last 90 days between 2,000 accounts.
"""
import os
import sys

from sqlalchemy import create_engine, text

N = int(sys.argv[1]) if len(sys.argv) > 1 else 500_000
engine = create_engine(os.getenv("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/ledger"))

with engine.begin() as conn:
    conn.execute(text("TRUNCATE transfers, accounts CASCADE"))
    conn.execute(text("""
        INSERT INTO accounts (id, owner, balance, daily_limit, status, created_at)
        SELECT gen_random_uuid()::text, 'seed-' || g, 1000000000, 1000000000, 'ACTIVE', now()
        FROM generate_series(1, 2000) g"""))
    conn.execute(text("""
        WITH ids AS (SELECT array_agg(id) a FROM accounts)
        INSERT INTO transfers (id, idempotency_key, source_id, target_id, amount, status, created_at)
        SELECT gen_random_uuid()::text, 'seed-' || g,
               ids.a[1 + (g % 2000)], ids.a[1 + ((g * 7 + 1) % 2000)],
               10000 + (g % 100) * 1000, 'COMPLETED',
               now() - (random() * interval '90 days')
        FROM generate_series(1, :n) g, ids"""), {"n": N})
    conn.execute(text("ANALYZE"))
print(f"seeded 2000 accounts and {N} transfers")
