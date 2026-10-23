from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.config import MAX_DAILY_LIMIT_VND, MAX_DEPOSIT_VND, MAX_TRANSFER_VND, MIN_TRANSFER_VND

# ids are UUIDs we generate; owner names are printable text. Rejecting control characters here keeps
# NUL bytes (which Postgres cannot store) from ever reaching the database.
ID_PATTERN = r"^[A-Za-z0-9-]{1,36}$"
TEXT_PATTERN = r"^[^\x00-\x1f\x7f]+$"
from app.models import AccountStatus, TransferStatus


class AccountCreate(BaseModel):
    owner: str = Field(min_length=1, max_length=100, pattern=TEXT_PATTERN)
    daily_limit: int | None = Field(default=None, ge=MIN_TRANSFER_VND, le=MAX_DAILY_LIMIT_VND)


class AccountOut(BaseModel):
    id: str
    owner: str
    balance: int
    daily_limit: int
    status: AccountStatus
    created_at: datetime

    model_config = {"from_attributes": True}


class DepositIn(BaseModel):
    amount: int = Field(gt=0, le=MAX_DEPOSIT_VND)


class TransferCreate(BaseModel):
    """source_id and target_id must be different accounts (checked server-side, returns 422)."""

    source_id: str = Field(pattern=ID_PATTERN)
    target_id: str = Field(pattern=ID_PATTERN)
    amount: int = Field(ge=MIN_TRANSFER_VND, le=MAX_TRANSFER_VND)

    @model_validator(mode="after")
    def different_accounts(self):
        if self.source_id == self.target_id:
            raise ValueError("source_id and target_id must differ")
        return self


class TransferOut(BaseModel):
    id: str
    idempotency_key: str
    source_id: str
    target_id: str
    amount: int
    status: TransferStatus
    failure_reason: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class FieldError(BaseModel):
    field: str
    message: str


class ErrorOut(BaseModel):
    code: str
    detail: str
    transfer_id: str | None = None
    errors: list[FieldError] | None = None
