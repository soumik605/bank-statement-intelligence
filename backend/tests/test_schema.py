import datetime as dt

import pytest
from pydantic import ValidationError

from app.schema import StatementInfo, Transaction

BASE = {"date": "2026-01-05", "description": "UPI/SWIGGY", "amount": 250.5, "type": "debit"}


def test_minimal_transaction():
    txn = Transaction(**BASE)
    assert txn.date == dt.date(2026, 1, 5)
    assert txn.category is None and not txn.is_unusual
    assert txn.signed_amount == -250.5


@pytest.mark.parametrize("amount", [-250.5, 0])
def test_amount_must_be_positive(amount):
    with pytest.raises(ValidationError):
        Transaction(**{**BASE, "amount": amount})


def test_amount_rounds_half_up():
    assert Transaction(**{**BASE, "amount": 2.675}).amount == 2.68


def test_dr_flag_is_rejected():
    with pytest.raises(ValidationError):
        Transaction(**{**BASE, "type": "Dr"})


def test_category_and_source_go_together():
    Transaction(**BASE, category="food", category_source="llm")
    with pytest.raises(ValidationError):
        Transaction(**BASE, category="food")
    with pytest.raises(ValidationError):
        Transaction(**BASE, category_source="rule")


def test_unusual_reason_matches_flag():
    Transaction(**BASE, is_unusual=True, unusual_reason="3x usual spend")
    with pytest.raises(ValidationError):
        Transaction(**BASE, is_unusual=True)
    with pytest.raises(ValidationError):
        Transaction(**BASE, unusual_reason="stray reason")


INFO = {
    "bank_name": "HDFC Bank",
    "account_number": "XXXXXXXX1234",
    "holder_name": "A Sharma",
    "period_start": "2026-01-01",
    "period_end": "2026-01-31",
}


def test_statement_info():
    assert StatementInfo(**INFO).period_end == dt.date(2026, 1, 31)


def test_unmasked_account_number_rejected():
    with pytest.raises(ValidationError):
        StatementInfo(**{**INFO, "account_number": "50100123451234"})


def test_period_order():
    with pytest.raises(ValidationError):
        StatementInfo(**{**INFO, "period_start": "2026-02-01"})
