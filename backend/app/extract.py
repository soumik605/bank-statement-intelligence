"""Stage 1: get raw rows out of a statement file, without interpreting them.

Each row is a dict mapping the statement's own column names to cell text,
exactly as printed apart from whitespace. Dates stay strings, and amounts
keep their commas and Dr/Cr markers. The next stage makes sense of them.

Only structural cleanup happens here:
- Text above the table (bank name, account details) is skipped by finding the
  header row.
- Header rows repeated on later PDF pages are dropped.
- A line holding only wrapped description text (first cell empty, exactly one
  cell filled) is joined onto the row above.
- Completely empty rows are dropped.

Rows that are in the table but are not transactions (opening-balance rows,
month totals, footers) are kept; deciding what is a transaction is
interpretation.
"""
import bisect
import csv
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pandas as pd
import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfplumber.utils.exceptions import PdfminerException

from app.headers import is_header_hint

RawRow = dict[str, str]

class ExtractionError(ValueError):
    pass


class PasswordRequiredError(ExtractionError):
    """The PDF is encrypted and no password was given."""


class IncorrectPasswordError(ExtractionError):
    """The PDF is encrypted and the password given doesn't open it."""


@contextmanager
def open_pdf(path: str | Path, password: str | None = None) -> Iterator[pdfplumber.PDF]:
    """Open a PDF, decrypting it with `password` if it's protected. The password
    is only handed to the PDF library; it never appears in errors or logs."""
    path = Path(path)
    pdf = None
    try:
        pdf = pdfplumber.open(path, password=password or "")
        pdf.pages  # decryption happens here at the latest
    except PdfminerException as e:
        if pdf is not None:
            pdf.close()
        if e.args and isinstance(e.args[0], PDFPasswordIncorrect):
            # pdfminer reports a missing and a wrong password the same way.
            if password:
                raise IncorrectPasswordError(f"the password for {path.name} is incorrect") from None
            raise PasswordRequiredError(f"{path.name} is password protected") from None
        raise
    with pdf:
        yield pdf


def extract_rows(path: str | Path, password: str | None = None) -> list[RawRow]:
    """`password` is used only for protected PDFs; CSVs ignore it."""
    suffix = Path(path).suffix.lower()
    if suffix == ".csv":
        return extract_csv(path)
    if suffix == ".pdf":
        return extract_pdf(path, password=password)
    raise ExtractionError(f"unsupported file type: {Path(path).name}")


def extract_header_text(path: str | Path, password: str | None = None) -> str:
    """The text above the transaction table (bank name, account details,
    period), one line per line, for the next stage to pull details out of.
    Empty when the table starts on the first line."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        lines = []
        with path.open(newline="", encoding=_csv_encoding(path)) as f:
            for row in csv.reader(f):
                if _is_header(row):
                    break
                # "Account Number,XXXX 7710" reads the same as the PDF's "Account Number: XXXX 7710".
                lines.append(": ".join(c.strip() for c in row if c.strip()))
        return "\n".join(line for line in lines if line)
    if suffix == ".pdf":
        lines = []
        with open_pdf(path, password) as pdf:
            for page in pdf.pages:
                for words in _lines(page.extract_words()):
                    if _is_header([c.text for c in _split_on_gaps(words)]):
                        return "\n".join(lines)
                    lines.append(" ".join(w["text"] for w in words))
        return "\n".join(lines)
    raise ExtractionError(f"unsupported file type: {path.name}")


# --- CSV ----------------------------------------------------------------------

def extract_csv(path: str | Path) -> list[RawRow]:
    path = Path(path)
    encoding = _csv_encoding(path)
    with path.open(newline="", encoding=encoding) as f:
        header_at = next((i for i, row in enumerate(csv.reader(f)) if _is_header(row)), None)
    if header_at is None:
        raise ExtractionError(f"{path.name}: no header row found")
    # dtype=str and keep_default_na=False: no type guessing, so "0000412..." keeps its
    # leading zeros and an empty cell stays "" instead of becoming NaN.
    df = pd.read_csv(path, skiprows=header_at, dtype=str, keep_default_na=False, encoding=encoding)
    return _rows_from_grid([list(df.columns), *df.itertuples(index=False, name=None)], path.name)


def _csv_encoding(path: Path) -> str:
    try:
        path.read_text(encoding="utf-8-sig")
        return "utf-8-sig"
    except UnicodeDecodeError:
        return "cp1252"  # Excel exports on Windows


# --- PDF ----------------------------------------------------------------------

def extract_pdf(
    path: str | Path, mode: Literal["auto", "tables", "text"] = "auto", password: str | None = None
) -> list[RawRow]:
    """Read ruled tables with pdfplumber; fall back to positioned text lines when
    no table with a recognisable header is found."""
    path = Path(path)
    with open_pdf(path, password) as pdf:
        if mode in ("auto", "tables"):
            grid = [row for page in pdf.pages for table in page.extract_tables() for row in table]
            if any(_is_header([_clean(c) for c in row]) for row in grid) or mode == "tables":
                return _rows_from_grid(grid, path.name)
        return _rows_from_grid(_text_grid(pdf), path.name)


@dataclass
class _Cell:
    text: str
    x0: float
    x1: float


def _text_grid(pdf: pdfplumber.PDF) -> list[list[str]]:
    """Rebuild table rows from words and their positions.

    Column spans come from the header line. A word goes to the column whose
    header it sits under, which handles numbers aligned under right-aligned
    headers. Otherwise it goes to the last column starting at or left of it,
    which handles long descriptions running past their header.
    """
    grid: list[list[str]] = []
    columns: list[_Cell] | None = None
    for page in pdf.pages:
        for words in _lines(page.extract_words()):
            cells = _split_on_gaps(words)
            if _is_header([c.text for c in cells]):
                columns = cells  # re-read on every page in case the layout shifts
                grid.append([c.text for c in cells])
            elif columns is not None:
                grid.append(_assign(words, columns))
    return grid


def _lines(words: list[dict], tolerance: float = 3) -> list[list[dict]]:
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(w["top"] - lines[-1][0]["top"]) <= tolerance:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def _split_on_gaps(words: list[dict], gap: float = 6) -> list[_Cell]:
    """Group a line's words into cells wherever the gap is wider than a space."""
    cells: list[_Cell] = []
    for w in words:
        if cells and w["x0"] - cells[-1].x1 <= gap:
            cells[-1] = _Cell(f"{cells[-1].text} {w['text']}", cells[-1].x0, w["x1"])
        else:
            cells.append(_Cell(w["text"], w["x0"], w["x1"]))
    return cells


def _assign(words: list[dict], columns: list[_Cell]) -> list[str]:
    starts = [c.x0 for c in columns]
    out: list[list[str]] = [[] for _ in columns]
    for w in words:
        under = [i for i, c in enumerate(columns) if w["x0"] < c.x1 and w["x1"] > c.x0]
        i = under[0] if under else max(bisect.bisect_right(starts, w["x0"]) - 1, 0)
        out[i].append(w["text"])
    return [" ".join(parts) for parts in out]


# --- Shared cleanup -----------------------------------------------------------

def _clean(cell: object) -> str:
    # Joins wrapped lines inside a cell ("PRIVATE\nLIMITED") and squeezes spaces.
    return " ".join(str(cell).split()) if cell is not None else ""


def _is_header(cells: list[str]) -> bool:
    """At least three cells that read like column names ("Date", "WithdrawalAmt."),
    and no digits, which keeps out preamble lines like "Opening Balance,6,20,400.00"
    and data rows."""
    filled = [c for c in cells if c]
    return sum(is_header_hint(c) for c in filled) >= 3 and not any(ch.isdigit() for c in filled for ch in c)


def _column_names(cells: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    names = []
    for i, cell in enumerate(cells):
        name = cell or f"column_{i + 1}"
        n = seen.get(name, 0)
        seen[name] = n + 1
        names.append(name if n == 0 else f"{name}.{n}")  # same scheme pandas uses
    return names


def _rows_from_grid(grid: list, source: str) -> list[RawRow]:
    header: list[str] | None = None
    names: list[str] = []
    rows: list[RawRow] = []
    for raw in grid:
        cells = [_clean(c) for c in raw]
        if header is None:
            if _is_header(cells):
                header, names = cells, _column_names(cells)
            continue  # text above the table
        if cells == header or not any(cells):
            continue  # header repeated on a later page, or a blank row
        cells = (cells + [""] * len(names))[: len(names)]
        filled = [i for i, c in enumerate(cells) if c]
        if rows and not cells[0] and len(filled) == 1:
            key = names[filled[0]]
            rows[-1][key] = f"{rows[-1][key]} {cells[filled[0]]}".strip()
            continue
        rows.append(dict(zip(names, cells)))
    if header is None:
        raise ExtractionError(f"{source}: no header row found")
    return rows
