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
    # Money in
    SALARY = "Salary"
    INCOME = "Income"  # interest, dividends, maturity proceeds; not salary
    REFUND = "Refund"  # money back from a purchase; never counts as spending
    # Money out
    RENT = "Rent"
    UTILITIES = "Utilities"
    FOOD = "Food"
    TRAVEL = "Travel"
    SHOPPING = "Shopping"
    # Either direction
    TRANSFER = "Transfer"  # own accounts, people, card bill payments
    CASH = "Cash"
    OTHER = "Other"


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

    # None when the statement doesn't print it; many CSV exports carry no account details.
    bank_name: str | None = Field(default=None, min_length=1)
    account_number: str | None = Field(default=None, description="Masked, last 4 digits only, e.g. XXXX1234")
    holder_name: str | None = Field(default=None, min_length=1)
    period_start: dt.date
    period_end: dt.date

    @field_validator("account_number")
    @classmethod
    def _must_be_masked(cls, value: str | None) -> str | None:
        if value is not None and sum(c.isdigit() for c in value) > 4:
            raise ValueError("account_number must be masked; at most 4 digits may be visible")
        return value

    @model_validator(mode="after")
    def _check_period(self) -> Self:
        if self.period_start > self.period_end:
            raise ValueError("period_start must be on or before period_end")
        return self


# Categories that are never counted as spending. Transfers move money to people
# or other accounts (including the holder's own), so counting them would double
# count or overstate spending. Money-in categories never apply to debits anyway.
NON_SPENDING_CATEGORIES = frozenset({Category.TRANSFER})


class Analytics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_count: int = Field(ge=0)
    total_credits: Money = Field(ge=0, description="Sum of all credit amounts")
    total_debits: Money = Field(ge=0, description="Sum of all debit amounts")
    net_flow: Money = Field(description="total_credits - total_debits; negative means more went out than came in")
    total_spending: Money = Field(
        ge=0,
        description="Debits except Transfer; uncategorized debits count as Other. "
        "Refunds are credits, so they are not netted against spending",
    )
    spending_by_category: dict[Category, Money] = Field(description="total_spending split by category")
    top_spending_categories: list[Category] = Field(max_length=3, description="Highest spending first")
