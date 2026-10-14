"""Statements drawn like HDFC's: lines between columns but none between rows."""
import json

import pdfplumber
import pytest

from app.extract import _has_stacked_rows, extract_header_text, extract_pdf
from app.normalize import NormalizationError, load_statement, normalize
from scripts.generate_samples import generate

STEM = "harbour_q1-2026"


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    out = tmp_path_factory.mktemp("samples")
    generate(out)
    return out / f"{STEM}.pdf", json.loads((out / f"{STEM}.truth.json").read_text())


def test_merged_rows_are_detected():
    # What a table reader returns for a page with column rules but no row rules:
    # one tall cell per column, every row of the page inside it.
    header = ["Date", "Narration", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"]
    merged = ["03/01/26\n04/01/26\n05/01/26", "UPI-A\nUPI-B\nUPI-C", "1.00\n2.00", "\n\n3.00", "9.00\n7.00\n10.00"]
    assert _has_stacked_rows([header, merged])
    assert not _has_stacked_rows([header, ["03/01/26", "UPI-A\nwrapped", "1.00", "", "9.00"]])  # narration wrap is fine


def test_unusable_table_reading_falls_back_to_text_positions(sample):
    path, _ = sample
    with pdfplumber.open(path) as pdf:
        grid = [row for page in pdf.pages for table in page.extract_tables() for row in table]
    assert len(grid) <= 2  # the table reader sees the header and little else
    assert extract_pdf(path) == extract_pdf(path, mode="text")


def test_whole_statement_comes_out_right(sample):
    path, truth = sample
    result = load_statement(path)
    assert result.statement.model_dump(mode="json") == truth["statement"]  # account details from the page header
    want = [row["transaction"] for row in truth["rows"]]
    got = [t.model_dump(mode="json", include=set(want[0])) for t in result.transactions]
    assert [{**g, "description": None} for g in got] == [{**w, "description": None} for w in want]
    exact = sum(g["description"] == w["description"] for g, w in zip(got, want))
    assert exact / len(want) >= 0.9  # the rest differ only in an undecidable space at a line wrap


def test_columns_split_on_drawn_rules(sample):
    path, _ = sample
    # "Withdrawal Amt." and "Deposit Amt." nearly touch; the column rules keep them apart.
    assert list(extract_pdf(path)[0]) == [
        "Date", "Narration", "Chq./Ref.No.", "Value Dt", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance",
    ]


def test_mid_word_wraps_are_joined(sample):
    path, _ = sample
    narrations = [row["Narration"] for row in extract_pdf(path)]
    assert any(n.endswith("-FOOD ORDER") for n in narrations)
    assert not any("FO OD" in n or "SYSTEMSPVT" in n for n in narrations)


def test_page_headers_and_summary_are_not_transactions(sample):
    path, truth = sample
    result = load_statement(path)
    assert len(result.transactions) == len(truth["rows"])
    skipped = " ".join(" ".join(s.row.values()) for s in result.skipped)
    assert "STATEMENT SUMMARY" in skipped and "Dr Count" in skipped
    assert not any("Account No" in t.description or "Statement From" in t.description for t in result.transactions)


def test_header_text_keeps_separate_items_apart(sample):
    path, _ = sample
    first = extract_header_text(path).splitlines()[0]
    assert first.startswith("Harbour Bank Ltd   ") and first.endswith("Page No .: 1")


def test_merged_rows_get_a_clear_error_if_they_slip_through():
    rows = [{"Date": "03/01/26\n04/01/26 05/01/26", "Narration": "X", "Withdrawal Amt.": "1.00", "Deposit Amt.": ""}]
    with pytest.raises(NormalizationError, match="3 dates in one cell.*no lines between rows"):
        normalize(rows)


def test_two_digit_year_dates_still_parse():
    rows = [{"Date": "03/01/26", "Narration": "X", "Withdrawal Amt.": "1.00", "Deposit Amt.": ""}]
    assert normalize(rows).transactions[0].date.isoformat() == "2026-01-03"


@pytest.mark.parametrize("label", ["From : 01/01/2026", "Generated on 13/10/2026", "Statement Date: 05-Jan-2026"])
def test_page_header_labels_in_the_date_column_are_skipped(label):
    # Seen in a real HDFC statement: a later page's "From : 01/01/2026 To : 13/10/2026"
    # block landed in the Date/Narration columns.
    rows = [
        {"Date": "03/01/26", "Narration": "UPI-SWIGGY-1", "Withdrawal Amt.": "250.00", "Deposit Amt.": ""},
        {"Date": label, "Narration": "To : 13/10/2026", "Withdrawal Amt.": "account", "Deposit Amt.": ""},
    ]
    result = normalize(rows)
    assert len(result.transactions) == 1
    assert result.skipped[0].reason == "date column holds a label, not a transaction date"


@pytest.mark.parametrize("cell", ["05-Jan-2026", "5 January 2026", "05/01/2026 02:30 PM", "2026-01-05"])
def test_dates_with_letters_of_their_own_are_still_dates(cell):
    rows = [{"Date": cell, "Narration": "X", "Withdrawal Amt.": "1.00", "Deposit Amt.": ""}]
    assert normalize(rows).transactions[0].date.isoformat() == "2026-01-05"


def test_unreadable_bare_date_still_raises():
    rows = [{"Date": "13/45/2026", "Narration": "X", "Withdrawal Amt.": "1.00", "Deposit Amt.": ""}]
    with pytest.raises(NormalizationError, match="unrecognised date"):
        normalize(rows)


def test_later_pages_without_a_repeated_header(sample):
    path, truth = sample
    with pdfplumber.open(path) as pdf:
        assert len(pdf.pages) > 1
        # Only page 1 has the column header; page 2 starts straight with its page header.
        assert "Narration" not in pdf.pages[1].extract_text()
    assert len(load_statement(path).transactions) == len(truth["rows"])
