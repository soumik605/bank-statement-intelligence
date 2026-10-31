"""Layouts that real statements failed on: a summary box above the table, a
header over two lines, column rules around only some columns, month-first
dates, and scanned PDFs."""
import datetime as dt

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.extract import ExtractionError, _is_header, _rows_from_grid, extract_pdf, extract_rows
from app.normalize import load_statement, normalize


def test_summary_box_above_the_table_is_not_the_header():
    grid = [
        ["Brought Forward", "Dr Count", "Cr Count", "Total Debits", "Total Credits", "Closing Balance"],
        ["1,000.00", "1", "1", "10.00", "20.00", "1,010.00"],
        ["Date", "Narration", "Debit", "Credit", "Balance"],
        ["01/01/26", "UPI-A", "10.00", "", "990.00"],
    ]
    assert not _is_header(grid[0])  # no date column
    assert _rows_from_grid(grid, "test") == [
        {"Date": "01/01/26", "Narration": "UPI-A", "Debit": "10.00", "Credit": "", "Balance": "990.00"},
    ]


def test_summary_box_above_the_table_in_a_csv(tmp_path):
    path = tmp_path / "statement.csv"
    path.write_text(
        "Brought Forward,Dr Count,Cr Count,Total Debits,Closing Balance\n"
        "1000.00,1,0,10.00,990.00\n"
        "Date,Narration,Withdrawls,Deposits,Balance\n"
        "02/01/2026,UPI-SWIGGY,10.00,,990.00\n"
    )
    [txn] = load_statement(path).transactions
    assert (txn.date, txn.type, txn.amount) == (dt.date(2026, 1, 2), "debit", 10.0)


# Header over two lines; rules drawn only at the table's edges and around the
# money columns, so the first three columns share one ruled span.
COLUMNS = [  # (x, top line, bottom line)
    (40, "Transaction", "Date"), (120, "Value", "Date"), (190, "Description", ""),
    (380, "Debit", ""), (440, "Credit", ""), (500, "Balance", ""),
]
RULES = [30, 370, 430, 490, 560]
ROWS = [
    ("05-01-2026", "05-01-2026", "UPI/SWIGGY/123", "250.00", "-", "9,750.00"),
    ("06-01-2026", "06-01-2026", "NEFT SALARY", "-", "5,000.00", "14,750.00"),
]


@pytest.fixture
def partly_ruled_pdf(tmp_path):
    path = tmp_path / "partly_ruled.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica", 9)
    c.drawString(40, 800, "Brought Forward   Dr Count   Total Debits   Closing Balance")
    c.drawString(40, 788, "10,000.00   1   250.00   14,750.00")
    for x, top, bottom in COLUMNS:
        c.drawString(x, 760, top)
        if bottom:
            c.drawString(x, 750, bottom)
    for i, row in enumerate(ROWS):
        for (x, *_), text in zip(COLUMNS, row):
            c.drawString(x, 730 - 14 * i, text)
    for x in RULES:
        c.line(x, 770, x, 690)
    c.save()
    return path


def test_two_line_header_and_partial_rules(partly_ruled_pdf):
    rows = extract_pdf(partly_ruled_pdf)
    assert list(rows[0]) == ["Transaction Date", "Value Date", "Description", "Debit", "Credit", "Balance"]
    txns = load_statement(partly_ruled_pdf).transactions
    assert [(t.date, t.description, t.type, t.amount) for t in txns] == [
        (dt.date(2026, 1, 5), "UPI/SWIGGY/123", "debit", 250.0),
        (dt.date(2026, 1, 6), "NEFT SALARY", "credit", 5000.0),
    ]


def _rows(*dates):
    return [{"Date": d, "Description": "X", "Amount": "-1.00"} for d in dates]


def test_month_first_only_when_the_column_proves_it():
    us = normalize(_rows("01/06/2023", "01/18/2023"))  # 18 can only be a day
    assert [t.date for t in us.transactions] == [dt.date(2023, 1, 6), dt.date(2023, 1, 18)]
    ambiguous = normalize(_rows("01/06/2023", "02/06/2023"))  # stays day-first
    assert [t.date for t in ambiguous.transactions] == [dt.date(2023, 6, 1), dt.date(2023, 6, 2)]
    indian = normalize(_rows("18/01/2023", "05/01/2023"))
    assert [t.date for t in indian.transactions] == [dt.date(2023, 1, 18), dt.date(2023, 1, 5)]


def test_scanned_pdf_says_so(tmp_path):
    path = tmp_path / "scan.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    c.rect(50, 50, 400, 600, fill=1)  # stands in for a page image: no text at all
    c.save()
    with pytest.raises(ExtractionError, match="scanned"):
        extract_rows(path)
