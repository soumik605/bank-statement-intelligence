"""The one shape every bank format gets converted into.

Sign convention: `amount` is always positive. Whether money came in or went out
lives only in `type`. Never store a negative amount, and never keep a raw
"Dr"/"Cr" flag; parsers translate those into `type` at the boundary.
"""
import datetime as dt
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator


def _round_money(value: float) -> float:
    # Go through Decimal(str(...)) so 2.675 rounds to 2.68, not float round()'s 2.67.
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# Signed money, e.g. a balance that can go overdrawn.
Money = Annotated[float, AfterValidator(_round_money)]

TransactionType = Literal["credit", "debit"]
CategorySource = Literal["rule", "llm"]


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
    model_config = ConfigDict(extra="forbid")

    date: dt.date = Field(description="Transaction date, ISO 8601 (YYYY-MM-DD)")
    description: str = Field(min_length=1, description="Narration as printed on the statement")
    amount: Money = Field(gt=0, description="Always positive, rounded to 2 decimals; direction is in `type`")
    type: TransactionType = Field(description="credit = money in, debit = money out")
    balance: Money | None = Field(default=None, description="Running balance after this transaction; signed")
    category: Category | None = Field(default=None, description="None until categorized")
    category_source: CategorySource | None = Field(
        default=None, description="What assigned `category`; set if and only if `category` is set"
    )
    is_unusual: bool = False
    unusual_reason: str | None = Field(default=None, description="Required when is_unusual is true, else null")

    @model_validator(mode="after")
    def _check_pairs(self) -> Self:
        if (self.category is None) != (self.category_source is None):
            raise ValueError("category and category_source must be set together")
        if self.is_unusual and not self.unusual_reason:
            raise ValueError("unusual_reason is required when is_unusual is true")
        if not self.is_unusual and self.unusual_reason is not None:
            raise ValueError("unusual_reason must be null when is_unusual is false")
        return self

    @property
    def signed_amount(self) -> float:
        """For arithmetic only; never serialized."""
        return self.amount if self.type == "credit" else -self.amount


class StatementInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bank_name: str = Field(min_length=1)
    account_number: str = Field(description="Masked, at most the last 4 digits visible, e.g. XXXXXXXX1234")
    holder_name: str = Field(min_length=1)
    period_start: dt.date
    period_end: dt.date

    @field_validator("account_number")
    @classmethod
    def _must_be_masked(cls, value: str) -> str:
        if sum(c.isdigit() for c in value) > 4:
            raise ValueError("account_number must be masked; at most 4 digits may be visible")
        return value

    @model_validator(mode="after")
    def _check_period(self) -> Self:
        if self.period_start > self.period_end:
            raise ValueError("period_start must be on or before period_end")
        return self
