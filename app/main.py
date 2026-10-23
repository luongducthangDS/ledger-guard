from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import services
from app.db import Base, engine, get_session
from app.models import AccountStatus
from app.schemas import ID_PATTERN, AccountCreate, AccountOut, DepositIn, ErrorOut, TransferCreate, TransferOut

AccountId = Annotated[str, Path(pattern=ID_PATTERN)]
TransferId = Annotated[str, Path(pattern=ID_PATTERN)]

@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="LedgerGuard", version="1.0.0", lifespan=lifespan,
              description="Minimal money-transfer API used as a system under test.")

ERRORS = {400: {"model": ErrorOut}, 404: {"model": ErrorOut}, 409: {"model": ErrorOut}, 422: {"model": ErrorOut}}


@app.exception_handler(services.DomainError)
def domain_error_handler(_: Request, exc: services.DomainError) -> JSONResponse:
    body = {"code": exc.code, "detail": exc.detail}
    if exc.transfer is not None:  # rejected transfers are persisted; give the client their id
        body["transfer_id"] = exc.transfer.id
    return JSONResponse(status_code=exc.http_status, content=body)


@app.exception_handler(RequestValidationError)
def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """One error shape for every 4xx, so clients parse a single contract."""
    errors = [{"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"code": "VALIDATION_ERROR",
                                                  "detail": "request validation failed", "errors": errors})


@app.exception_handler(StarletteHTTPException)
def http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Framework-level errors (malformed body, unknown route) use the same error contract."""
    return JSONResponse(status_code=exc.status_code, headers=getattr(exc, "headers", None),
                        content={"code": f"HTTP_{exc.status_code}", "detail": str(exc.detail)})


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/accounts", response_model=AccountOut, status_code=201, responses=ERRORS)
def create_account(body: AccountCreate, session: Session = Depends(get_session)):
    return services.create_account(session, body.owner, body.daily_limit)


@app.get("/accounts/{account_id}", response_model=AccountOut, responses=ERRORS)
def get_account(account_id: AccountId, session: Session = Depends(get_session)):
    return services._get_account(session, account_id)


@app.post("/accounts/{account_id}/deposits", response_model=AccountOut, responses=ERRORS)
def deposit(account_id: AccountId, body: DepositIn, session: Session = Depends(get_session)):
    return services.deposit(session, account_id, body.amount)


@app.post("/accounts/{account_id}/freeze", response_model=AccountOut, responses=ERRORS)
def freeze(account_id: AccountId, session: Session = Depends(get_session)):
    return services.set_status(session, account_id, AccountStatus.FROZEN)


@app.post("/accounts/{account_id}/unfreeze", response_model=AccountOut, responses=ERRORS)
def unfreeze(account_id: AccountId, session: Session = Depends(get_session)):
    return services.set_status(session, account_id, AccountStatus.ACTIVE)


@app.post("/accounts/{account_id}/close", response_model=AccountOut, responses=ERRORS)
def close(account_id: AccountId, session: Session = Depends(get_session)):
    return services.set_status(session, account_id, AccountStatus.CLOSED)


@app.post("/transfers", response_model=TransferOut, status_code=201, responses=ERRORS)
def create_transfer(
    body: TransferCreate,
    idempotency_key: str = Header(min_length=8, max_length=64),
    session: Session = Depends(get_session),
):
    transfer, replayed = services.create_transfer(session, body, idempotency_key)
    if replayed:
        return JSONResponse(status_code=200, content=TransferOut.model_validate(transfer).model_dump(mode="json"))
    return transfer


@app.get("/transfers/{transfer_id}", response_model=TransferOut, responses=ERRORS)
def get_transfer(transfer_id: TransferId, session: Session = Depends(get_session)):
    return services.get_transfer(session, transfer_id)


@app.post("/transfers/{transfer_id}/reverse", response_model=TransferOut, responses=ERRORS)
def reverse(transfer_id: TransferId, session: Session = Depends(get_session)):
    return services.reverse_transfer(session, transfer_id)
