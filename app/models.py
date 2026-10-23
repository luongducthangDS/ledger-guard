import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.config import DEFAULT_DAILY_LIMIT_VND
from app.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class AccountStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"
    CLOSED = "CLOSED"


class TransferStatus(str, enum.Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REVERSED = "REVERSED"


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner: Mapped[str] = mapped_column(String(100))
    balance: Mapped[int] = mapped_column(BigInteger, default=0)
    daily_limit: Mapped[int] = mapped_column(BigInteger, default=DEFAULT_DAILY_LIMIT_VND)
    status: Mapped[AccountStatus] = mapped_column(Enum(AccountStatus), default=AccountStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Transfer(Base):
    __tablename__ = "transfers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    target_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    amount: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[TransferStatus] = mapped_column(Enum(TransferStatus))
    failure_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (CheckConstraint("amount > 0", name="ck_transfer_amount_positive"),)
