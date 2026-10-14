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


class SkippedRowOut(BaseModel):
    row: dict[str, str] = Field(description="The row as extracted, column name to text")
    reason: str = Field(description='Why it is not a transaction, e.g. "no date" for a totals row')


class AISummary(BaseModel):
    text: str
    source: Literal["llm", "template"] = Field(
        description="template when the LLM failed or used numbers that aren't in the computed figures"
    )
    unsupported_numbers: list[str] = Field(
        default_factory=list, description="Numbers the LLM invented on its last attempt, if any"
    )


class AnalyzeResponse(BaseModel):
    """Everything POST /analyze returns for one statement."""

    analysis_id: int | None = Field(
        default=None, description="ID in the local history database; null if saving failed"
    )
    analyzed_at: dt.datetime | None = Field(default=None, description="When the analysis ran, UTC")
    source_file: str
    account: StatementInfo
    transactions: list[Transaction]
    summary: Analytics
    unusual: list[Transaction] = Field(description="The transactions with is_unusual set, in statement order")
    ai_summary: AISummary
    skipped_rows: list[SkippedRowOut] = Field(
        default_factory=list, description="Table rows that weren't transactions: totals, opening balance, footers"
    )


class HistoryItem(BaseModel):
    """One past analysis, as listed in GET /history."""

    id: int
    analyzed_at: dt.datetime
    source_file: str
    file_sha256: str = Field(description="Fingerprint of the uploaded file; equal values mean the same file")
    bank_name: str | None
    account_number: str | None
    period_start: dt.date
    period_end: dt.date
    transaction_count: int
    total_credits: Money
    total_debits: Money
    net_flow: Money
    unusual_count: int
    ai_summary_source: Literal["llm", "template"]


class HistoryPage(BaseModel):
    items: list[HistoryItem] = Field(description="Newest first")
    total: int = Field(description="All saved analyses, not just this page")


class FailureItem(BaseModel):
    """A failed upload, kept so the parser can be improved against the real file."""

    id: int
    failed_at: dt.datetime
    source_file: str
    file_sha256: str | None
    file_size: int
    file_kept: bool = Field(description="Whether the uploaded file is stored and can be downloaded")
    http_status: int
    error_code: str
    message: str = Field(description="What the user was shown")
    exception_type: str | None
    traceback: str | None
    password_supplied: bool = Field(description="A protected PDF; the stored copy needs its password, which isn't kept")
    retry_count: int = 0
    last_retry_at: dt.datetime | None = None
    resolved_at: dt.datetime | None = Field(default=None, description="When a retry succeeded")
    resolved_analysis_id: int | None = Field(
        default=None, description="The analysis the successful retry produced; null if not resolved or since deleted"
    )


class FailurePage(BaseModel):
    items: list[FailureItem] = Field(description="Newest first")
    total: int
