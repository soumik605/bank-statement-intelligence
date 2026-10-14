import datetime as dt

import pytest

from app.extract import _is_header, extract_rows
from app.headers import column_role, header_words
from app.normalize import NormalizationError, load_statement, map_columns, normalize


@pytest.mark.parametrize("name, words", [
    ("WithdrawalAmt.", ["withdrawal", "amt"]),
    ("DepositAmt.", ["deposit", "amt"]),
    ("ClosingBalance", ["closing", "balance"]),
    ("ValueDt", ["value", "dt"]),
    ("Chq./Ref.No.", ["chq", "ref", "no"]),
    ("TRANSACTIONDETAILS", ["transaction", "details"]),
    ("Dr/Cr", ["dr", "cr"]),
    ("Transfer", ["transfer"]),  # never split into made-up words like trans + fer
])
def test_header_words(name, words):
    assert header_words(name) == words


@pytest.mark.parametrize("name, role", [
    # money out
    ("WithdrawalAmt.", "debit"), ("Withdrawal Amt.", "debit"), ("WITHDRAWAL AMOUNT (INR)", "debit"),
    ("Withdrawals", "debit"), ("Withdrawal (Dr)", "debit"), ("Debit", "debit"), ("DebitAmount", "debit"),
    ("Debit Amt", "debit"), ("Dr Amount", "debit"), ("Amount (Dr)", "debit"), ("Dr", "debit"),
    ("Paid Out", "debit"), ("Money Out", "debit"), ("Payments", "debit"), ("Debit(₹)", "debit"),
    # money in
    ("DepositAmt.", "credit"), ("Deposit Amt.", "credit"), ("Deposits", "credit"), ("Credit", "credit"),
    ("CreditAmount", "credit"), ("Cr Amount", "credit"), ("Amount(Cr)", "credit"), ("Paid In", "credit"),
    ("Money In", "credit"), ("Receipts", "credit"),
    # one amount column, and its direction marker
    ("Amount", "amount"), ("Amount (INR)", "amount"), ("Transaction Amount", "amount"), ("Amt", "amount"),
    ("Debit/Credit Amount", "amount"), ("Dr/Cr", "marker"), ("Cr/Dr", "marker"), ("Debit/Credit", "marker"),
    # the rest
    ("ClosingBalance", "balance"), ("Balance (INR)", "balance"), ("Running Bal", "balance"),
    ("Narration", "description"), ("Particulars", "description"), ("Transaction Details", "description"),
    ("TxnDate", "date"), ("ValueDt", "date"), ("Posting Date", "date"),
])
def test_column_role(name, role):
    assert column_role(name)[0] == role


@pytest.mark.parametrize("name", ["Chq./Ref.No.", "Ref No", "Cheque Number", "Branch", "S.No."])
def test_unused_columns(name):
    assert column_role(name) is None


def test_hdfc_style_run_together_headers():
    columns = ["Date", "Narration", "Chq./Ref.No.", "ValueDt", "WithdrawalAmt.", "DepositAmt.", "ClosingBalance"]
    assert _is_header(columns)
    assert map_columns(columns) == {
        "date": "Date", "description": "Narration", "debit": "WithdrawalAmt.", "credit": "DepositAmt.",
        "balance": "ClosingBalance",
    }


def test_strongest_date_wins_whatever_the_order():
    assert map_columns(["ValueDt", "TxnDate", "Narration", "Amount"])["date"] == "TxnDate"
    assert map_columns(["Value Date", "Date", "Narration", "Amount"])["date"] == "Date"


def test_csv_with_run_together_headers_end_to_end(tmp_path):
    path = tmp_path / "hdfc.csv"
    path.write_text(
        "Date,Narration,Chq./Ref.No.,ValueDt,WithdrawalAmt.,DepositAmt.,ClosingBalance\n"
        "01/01/26,SALARY ACME,0000123,01/01/26,,\"1,15,000.00\",\"1,63,250.75\"\n"
        "03/01/26,UPI-SWIGGY-123,0000124,03/01/26,625.64,,\"1,62,625.11\"\n"
    )
    txns = load_statement(path).transactions
    assert [(t.date, t.type, t.amount, t.balance) for t in txns] == [
        (dt.date(2026, 1, 1), "credit", 115000.0, 163250.75),
        (dt.date(2026, 1, 3), "debit", 625.64, 162625.11),
    ]


def test_type_column_with_channels_is_not_a_marker():
    rows = [
        {"Date": "02/01/2026", "Description": "SALARY", "Type": "NEFT", "Amount": "5,000.00"},
        {"Date": "03/01/2026", "Description": "RENT", "Type": "UPI", "Amount": "-2,000.00"},
    ]
    assert [t.type for t in normalize(rows).transactions] == ["credit", "debit"]  # sign used instead


def test_type_column_with_dr_cr_is_a_marker():
    rows = [
        {"Date": "02/01/2026", "Description": "SALARY", "Type": "CR", "Amount": "5,000.00"},
        {"Date": "03/01/2026", "Description": "RENT", "Type": "DR", "Amount": "2,000.00"},
    ]
    assert [t.type for t in normalize(rows).transactions] == ["credit", "debit"]


def test_unmappable_columns_say_how_each_was_read():
    with pytest.raises(NormalizationError) as e:
        map_columns(["Date", "Narration", "Branch", "ClosingBalance"])
    message = str(e.value)
    assert "'Branch' -> unused" in message and "'ClosingBalance' -> balance" in message
