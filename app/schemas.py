from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.config import MAX_DEPOSIT_VND, MAX_TRANSFER_VND, MIN_TRANSFER_VND
from app.models import AccountStatus, TransferStatus


class AccountCreate(BaseModel):
    owner: str = Field(min_length=1, max_length=100)
    daily_limit: int | None = Field(default=None, ge=MIN_TRANSFER_VND)


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
    source_id: str = Field(min_length=1, max_length=36)
    target_id: str = Field(min_length=1, max_length=36)
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


class ErrorOut(BaseModel):
    code: str
    detail: str
