"""Business rules and runtime settings. All money is integer VND (no floats)."""
import os

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/ledger"
)

MIN_TRANSFER_VND = 10_000            # smallest allowed transfer
MAX_TRANSFER_VND = 500_000_000       # largest single transfer
DEFAULT_DAILY_LIMIT_VND = 1_000_000_000
MAX_DAILY_LIMIT_VND = 100_000_000_000
MAX_DEPOSIT_VND = 2_000_000_000
