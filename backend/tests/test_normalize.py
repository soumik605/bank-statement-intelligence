import datetime as dt
import json
from decimal import Decimal

import pytest

from app.extract import extract_header_text, extract_pdf
from app.normalize import (
    NormalizationError,
    load_statement,
    map_columns,
    mask_account_number,
    normalize,
    parse_amount,
    parse_date,
    parse_header,
)
from scripts.generate_samples import generate

PARSE_FIELDS = {"date", "description", "amount", "type", "balance"}


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    out = tmp_path_factory.mktemp("samples")
    generate(out)
    truths = [json.loads(p.read_text()) for p in sorted(out.glob("*.truth.json"))]
    return [(out / t["source_file"], t) for t in truths]


def _check_against_truth(result, truth):
    got = [t.model_dump(mode="json", include=PARSE_FIELDS) for t in result.transactions]
    assert got == [row["transaction"] for row in truth["rows"]]
    assert result.statement.model_dump(mode="json") == truth["statement"]


def test_samples_match_truth(samples):
    for path, truth in samples:
        _check_against_truth(load_statement(path, truth["password"]), truth)


def test_pdf_text_fallback_matches_truth(samples):
    for path, truth in samples:
        if path.suffix == ".pdf":
            password = truth["password"]
            rows = extract_pdf(path, mode="text", password=password)
            _check_against_truth(normalize(rows, extract_header_text(path, password)), truth)


@pytest.mark.parametrize("text, expected", [
    ("₹1,23,456.00", (Decimal("123456.00"), None)),
    ("1,23,456.00", (Decimal("123456.00"), None)),
    ("123,456.00", (Decimal("123456.00"), None)),
    ("Rs. 99", (Decimal("99"), None)),
    ("INR 1,234.5 CR", (Decimal("1234.5"), "credit")),
    ("1,500.00 Dr", (Decimal("1500.00"), "debit")),
    ("1500.00(Dr)", (Decimal("1500.00"), "debit")),
    ("(250.00)", (Decimal("-250.00"), None)),
    ("-250.00", (Decimal("-250.00"), None)),
    ("", (None, None)),
])
def test_parse_amount(text, expected):
    assert parse_amount(text) == expected


@pytest.mark.parametrize("text", ["abc", "1.2.3", "Dr", "1 Dr Cr"])
def test_parse_amount_rejects(text):
    with pytest.raises(ValueError):
        parse_amount(text)


@pytest.mark.parametrize("text", [
    "05/01/2026", "05/01/26", "05-01-2026", "05-Jan-2026", "05-JAN-26", "05 Jan 2026",
    "5 January 2026", "2026-01-05", "05/01/2026 14:32:10",
])
def test_parse_date_is_day_first(text):
    assert parse_date(text) == dt.date(2026, 1, 5)


def test_parse_date_never_swaps_to_month_first():
    with pytest.raises(ValueError):
        parse_date("01/13/2026")  # only valid as MM/DD


def test_mask_account_number():
    assert mask_account_number("50100123451234") == "XXXX1234"
    assert mask_account_number("XXXX XXXX 7710") == "XXXX7710"
    assert mask_account_number("XXXXXX") is None


def test_map_columns():
    assert map_columns(["Txn Date", "Value Date", "Particulars", "Amount", "Dr/Cr", "Balance"]) == {
        "date": "Txn Date", "description": "Particulars", "amount": "Amount", "marker": "Dr/Cr", "balance": "Balance",
    }
    assert map_columns(["Value Date", "Narration", "Withdrawal (Dr)", "Deposit (Cr)"])["date"] == "Value Date"
    with pytest.raises(NormalizationError, match="can't map"):
        map_columns(["Date", "Narration", "Balance"])


def test_parse_header():
    text = "First Bank\nName: A Sharma    Branch: Pune\nA/c No: 50100123451234\nPeriod: 01/01/2026 to 31/01/2026"
    assert parse_header(text) == {
        "bank_name": "First Bank", "holder_name": "A Sharma", "account_number": "XXXX1234",
        "period_start": dt.date(2026, 1, 1), "period_end": dt.date(2026, 1, 31),
    }
    assert parse_header("") == {}


def test_signed_amounts_and_skipped_rows():
    rows = [
        {"Date": "01/01/2026", "Description": "OPENING BALANCE", "Amount": "", "Balance": "1,000.00"},
        {"Date": "02/01/2026", "Description": "SALARY", "Amount": "5,000.00", "Balance": "6,000.00"},
        {"Date": "03/01/2026", "Description": "RENT", "Amount": "-2,000.00", "Balance": "4,000.00"},
        {"Date": "", "Description": "Total", "Amount": "3,000.00", "Balance": ""},
    ]
    result = normalize(rows)
    assert [(t.type, t.amount) for t in result.transactions] == [("credit", 5000.0), ("debit", 2000.0)]
    assert [s.reason for s in result.skipped] == ["no amount", "no date"]
    assert result.statement.period_start == dt.date(2026, 1, 2)


def test_unsigned_amounts_without_marker_are_an_error():
    rows = [{"Date": "02/01/2026", "Description": "X", "Amount": "5,000.00"}]
    with pytest.raises(NormalizationError, match="can't tell debits from credits"):
        normalize(rows)


def test_unreadable_date_is_an_error_not_a_skip():
    rows = [{"Date": "2026/13/45", "Description": "X", "Debit": "1.00", "Credit": ""}]
    with pytest.raises(NormalizationError, match="row 1"):
        normalize(rows)
