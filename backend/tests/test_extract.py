import json

import pdfplumber
import pytest

from app.extract import ExtractionError, _rows_from_grid, extract_pdf, extract_rows
from scripts.generate_samples import generate

DESCRIPTION_COLUMN = {
    "northwind_jan2026": "Narration",
    "konark_jan2026": "Particulars",
    "meridian_q1-2026": "Narration",
    "sahyadri_feb-mar2026": "Particulars",
}
# Table rows that aren't transactions: Konark's end-of-statement footer,
# Meridian's B/F opening-balance row, Sahyadri's two total rows per month.
EXTRA_ROWS = {"northwind_jan2026": 0, "konark_jan2026": 1, "meridian_q1-2026": 1, "sahyadri_feb-mar2026": 4}


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    out = tmp_path_factory.mktemp("samples")
    generate(out)
    return {
        p.name.removesuffix(".truth.json"): (out / json.loads(p.read_text())["source_file"], json.loads(p.read_text()))
        for p in out.glob("*.truth.json")
    }


def _is_subsequence(needles, haystack):
    it = iter(haystack)
    return all(n in it for n in needles)


@pytest.mark.parametrize("stem", DESCRIPTION_COLUMN)
def test_extracts_every_row(samples, stem):
    path, truth = samples[stem]
    rows = extract_rows(path)
    descriptions = [r[DESCRIPTION_COLUMN[stem]] for r in rows]
    # Wrapped narrations come back joined, so they match the truth exactly.
    assert _is_subsequence([t["transaction"]["description"] for t in truth["rows"]], descriptions)
    assert len(rows) == len(truth["rows"]) + EXTRA_ROWS[stem]


@pytest.mark.parametrize("stem", ["meridian_q1-2026", "sahyadri_feb-mar2026"])
def test_text_fallback(samples, stem):
    path, truth = samples[stem]
    rows = extract_pdf(path, mode="text")
    descriptions = [r[DESCRIPTION_COLUMN[stem]] for r in rows]
    assert _is_subsequence([t["transaction"]["description"] for t in truth["rows"]], descriptions)


def test_repeated_page_headers_dropped(samples):
    path, _ = samples["meridian_q1-2026"]
    with pdfplumber.open(path) as pdf:
        assert len(pdf.pages) > 1  # otherwise this test proves nothing
    for mode in ("tables", "text"):
        rows = extract_pdf(path, mode=mode)
        assert not any(r["Date"] == "Date" for r in rows), mode


def test_text_is_not_interpreted(samples):
    northwind = extract_rows(samples["northwind_jan2026"][0])
    assert all(r["Chq./Ref.No."].startswith("0000") for r in northwind)  # leading zeros kept
    assert northwind[0]["Withdrawal Amt."] == ""  # empty stays empty, not NaN

    konark = extract_rows(samples["konark_jan2026"][0])
    assert konark[0] | {"Particulars": ""} == {
        "Txn Date": "01-Jan-2026", "Value Date": "01-Jan-2026", "Particulars": "",
        "Amount": "92,500.00", "Dr/Cr": "CR", "Balance": "7,12,900.00",
    }


def test_grid_cleanup():
    grid = [
        ["Some Bank", None, None],
        ["Opening Balance", "1,000.00", None],
        ["Date", "Description", "Amount"],
        ["01/01/26", "UPI/123/LONG", "10.00"],
        ["", "WRAPPED TAIL", ""],
        ["", "", ""],
        ["Date", "Description", "Amount"],
        ["02/01/26", "Next  page\nrow", "20.00"],
    ]
    assert _rows_from_grid(grid, "test") == [
        {"Date": "01/01/26", "Description": "UPI/123/LONG WRAPPED TAIL", "Amount": "10.00"},
        {"Date": "02/01/26", "Description": "Next page row", "Amount": "20.00"},
    ]


def test_no_header_raises(tmp_path):
    path = tmp_path / "junk.csv"
    path.write_text("a,b\n1,2\n")
    with pytest.raises(ExtractionError, match="no header"):
        extract_rows(path)
