# backend/app/schema.py
"""Pipeline data models. Conventions are documented in backend/CONVENTIONS.md."""
import datetime as dt
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, Field


def _round_money(value: float) -> float:
    # Go through Decimal(str(...)) so 2.675 rounds to 2.68, not float round()'s 2.67.
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# Signed money: balances, nets, differences.
Money = Annotated[float, AfterValidator(_round_money)]
# Unsigned money: transaction amounts and totals of them.
PositiveMoney = Annotated[float, Field(ge=0), AfterValidator(_round_money)]

BALANCE_TOLERANCE = 0.01

Direction = Literal["debit", "credit"]
Severity = Literal["low", "medium", "high"]


class Category(StrEnum):
    INCOME = "income"
    TRANSFER = "transfer"
    BILLS = "bills"
    FOOD = "food"
    SHOPPING = "shopping"
    TRANSPORT = "transport"
    CASH = "cash"
    OTHER = "other"


class Transaction(BaseModel):
    date: dt.date
    description: str
    amount: PositiveMoney = Field(gt=0)
    direction: Direction
    balance: Money | None = None
    category: Category = Category.OTHER
    reference: str | None = None
    page: int | None = Field(default=None, ge=1, description="Source PDF page")

    @property
    def signed_amount(self) -> float:
        """For arithmetic only; never serialized."""
        return self.amount if self.direction == "credit" else -self.amount


class StatementInfo(BaseModel):
    bank_name: str | None = None
    account_holder: str | None = None
    account_number: str | None = Field(default=None, description="Masked, e.g. XXXX1234")
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$", description="ISO 4217, e.g. INR")
    period_start: dt.date | None = None
    period_end: dt.date | None = None
    opening_balance: Money | None = None
    closing_balance: Money | None = None


class ValidationResult(BaseModel):
    is_valid: bool
    computed_closing_balance: Money | None = None
    reported_closing_balance: Money | None = None
    difference: Money | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MonthlyTotal(BaseModel):
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="ISO year-month, YYYY-MM")
    credits: PositiveMoney
    debits: PositiveMoney
    net: Money


class Analytics(BaseModel):
    transaction_count: int = Field(ge=0)
    total_credits: PositiveMoney
    total_debits: PositiveMoney
    net_flow: Money
    average_balance: Money | None = None
    largest_credit: PositiveMoney | None = None
    largest_debit: PositiveMoney | None = None
    spending_by_category: dict[Category, PositiveMoney] = Field(
        default_factory=dict, description="Sum of debit amounts per category"
    )
    monthly: list[MonthlyTotal] = Field(default_factory=list)


class Anomaly(BaseModel):
    type: str = Field(description="e.g. large_transaction, duplicate, balance_mismatch")
    severity: Severity
    description: str
    transaction_index: int | None = Field(default=None, ge=0, description="Index into PipelinePayload.transactions")
    date: dt.date | None = None
    amount: PositiveMoney | None = None
    direction: Direction | None = None


class PipelinePayload(BaseModel):
    statement: StatementInfo
    transactions: list[Transaction]
    # None means the stage hasn't run; an empty list/result means it ran and found nothing.
    validation: ValidationResult | None = None
    analytics: Analytics | None = None
    anomalies: list[Anomaly] | None = None
    summary: str | None = None
    meta: dict[str, Any] | None = None
