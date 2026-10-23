"""Ledger business logic. Every money movement happens inside one DB transaction."""
from datetime import datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Account, AccountStatus, Transfer, TransferStatus
from app.schemas import TransferCreate

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


class DomainError(Exception):
    """Business-rule violation. `http_status` lets the API layer map it."""

    def __init__(self, code: str, detail: str, http_status: int = 409, transfer: Transfer | None = None):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.http_status = http_status
        self.transfer = transfer


def _get_account(session: Session, account_id: str) -> Account:
    account = session.get(Account, account_id)
    if account is None:
        raise DomainError("ACCOUNT_NOT_FOUND", f"account {account_id} not found", 404)
    return account


def create_account(session: Session, owner: str, daily_limit: int | None) -> Account:
    account = Account(owner=owner)
    if daily_limit is not None:
        account.daily_limit = daily_limit
    session.add(account)
    session.commit()
    return account


def deposit(session: Session, account_id: str, amount: int) -> Account:
    account = _get_account(session, account_id)
    if account.status != AccountStatus.ACTIVE:
        raise DomainError("ACCOUNT_NOT_ACTIVE", f"account is {account.status.value}")
    account.balance += amount
    session.commit()
    return account


def set_status(session: Session, account_id: str, new_status: AccountStatus) -> Account:
    account = _get_account(session, account_id)
    allowed = {
        AccountStatus.ACTIVE: {AccountStatus.FROZEN, AccountStatus.CLOSED},
        AccountStatus.FROZEN: {AccountStatus.ACTIVE, AccountStatus.CLOSED},
        AccountStatus.CLOSED: set(),
    }
    if new_status not in allowed[account.status]:
        raise DomainError(
            "INVALID_STATUS_TRANSITION", f"{account.status.value} -> {new_status.value} not allowed"
        )
    if new_status == AccountStatus.CLOSED and account.balance != 0:
        raise DomainError("BALANCE_NOT_ZERO", "account must be empty before closing")
    account.status = new_status
    session.commit()
    return account


def _sent_today(session: Session, account_id: str) -> int:
    start_of_day = datetime.combine(datetime.now(VN_TZ).date(), time.min, tzinfo=VN_TZ)
    total = session.scalar(
        select(func.coalesce(func.sum(Transfer.amount), 0)).where(
            Transfer.source_id == account_id,
            Transfer.status == TransferStatus.COMPLETED,
            Transfer.created_at >= start_of_day,
        )
    )
    return int(total)


def _fail(session: Session, transfer: Transfer, code: str, detail: str) -> None:
    """Persist the rejected transfer for audit, then raise."""
    transfer.status = TransferStatus.FAILED
    transfer.failure_reason = code
    session.add(transfer)
    session.commit()
    raise DomainError(code, detail, 409, transfer)


def create_transfer(session: Session, data: TransferCreate, idempotency_key: str) -> tuple[Transfer, bool]:
    """Returns (transfer, replayed). replayed=True when the idempotency key was seen before."""
    existing = session.scalar(select(Transfer).where(Transfer.idempotency_key == idempotency_key))
    if existing is not None:
        same = (existing.source_id, existing.target_id, existing.amount) == (
            data.source_id, data.target_id, data.amount)
        if not same:
            raise DomainError("IDEMPOTENCY_KEY_REUSED", "key already used with a different payload", 422)
        return existing, True

    source = _get_account(session, data.source_id)
    target = _get_account(session, data.target_id)

    transfer = Transfer(idempotency_key=idempotency_key, source_id=source.id,
                        target_id=target.id, amount=data.amount)

    if source.status != AccountStatus.ACTIVE:
        _fail(session, transfer, "SOURCE_NOT_ACTIVE", f"source account is {source.status.value}")
    if target.status != AccountStatus.ACTIVE:
        _fail(session, transfer, "TARGET_NOT_ACTIVE", f"target account is {target.status.value}")
    if source.balance < data.amount:
        _fail(session, transfer, "INSUFFICIENT_FUNDS", "balance lower than amount")
    if _sent_today(session, source.id) + data.amount > source.daily_limit:
        _fail(session, transfer, "DAILY_LIMIT_EXCEEDED", "daily outgoing limit exceeded")

    source.balance -= data.amount
    target.balance += data.amount
    transfer.status = TransferStatus.COMPLETED
    session.add(transfer)
    session.commit()
    return transfer, False


def get_transfer(session: Session, transfer_id: str) -> Transfer:
    transfer = session.get(Transfer, transfer_id)
    if transfer is None:
        raise DomainError("TRANSFER_NOT_FOUND", f"transfer {transfer_id} not found", 404)
    return transfer


def reverse_transfer(session: Session, transfer_id: str) -> Transfer:
    transfer = get_transfer(session, transfer_id)
    if transfer.status != TransferStatus.COMPLETED:
        raise DomainError("INVALID_STATUS_TRANSITION", f"cannot reverse a {transfer.status.value} transfer")
    source = _get_account(session, transfer.source_id)
    target = _get_account(session, transfer.target_id)
    if target.balance < transfer.amount:
        raise DomainError("INSUFFICIENT_FUNDS", "target no longer holds the amount")
    target.balance -= transfer.amount
    source.balance += transfer.amount
    transfer.status = TransferStatus.REVERSED
    session.commit()
    return transfer
