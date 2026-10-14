"""Stage 1: get raw rows out of a statement file, without interpreting them.

Each row is a dict mapping the statement's own column names to cell text,
exactly as printed apart from whitespace. Dates stay strings, and amounts
keep their commas and Dr/Cr markers. The next stage makes sense of them.

Only structural cleanup happens here:
- Text above the table (bank name, account details) is skipped by finding the
  header row.
- Header rows repeated on later PDF pages are dropped.
- A line holding only wrapped text (first cell empty, exactly one cell filled,
  and that cell a text column such as the narration) is joined onto the row above.
- PDF tables drawn with lines between columns but none between rows (HDFC does
  this) are read by text position instead, since a table reader would put every
  row of a page into one cell.
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

from app.headers import column_role, is_header_hint

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
                    # Wide gaps become 3 spaces, which parse_header treats as a field break.
                    lines.append("   ".join(c.text for c in _split_on_gaps(words)))
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
            if mode == "tables":
                return _rows_from_grid(grid, path.name)
            if any(_is_header([_clean(c) for c in row]) for row in grid) and not _has_stacked_rows(grid):
                rows = _rows_from_grid(grid, path.name)
                if rows:  # a header with nothing under it: the table reader missed the body
                    return rows
        return _rows_from_grid(_text_grid(pdf), path.name)


def _has_stacked_rows(grid: list) -> bool:
    """True when the table reader merged many rows into one: tables with lines
    between columns but not between rows come out as one tall cell per column,
    holding every row of the page. Dates never wrap, so a date cell with several
    lines gives it away."""
    header_at = next((i for i, row in enumerate(grid) if _is_header([_clean(c) for c in row])), None)
    if header_at is None:
        return False
    date_columns = [i for i, c in enumerate(grid[header_at]) if (column_role(_clean(c)) or ("",))[0] == "date"]
    return any(
        i < len(row) and row[i] and sum(1 for line in str(row[i]).splitlines() if line.strip()) > 1
        for row in grid[header_at + 1 :]
        for i in date_columns
    )


@dataclass
class _Cell:
    text: str
    x0: float
    x1: float


def _text_grid(pdf: pdfplumber.PDF) -> list[list[str]]:
    """Rebuild table rows from words and their positions.

    Column spans come from the vertical rules between columns when the page
    draws them, otherwise from the header line. Without rules, a word goes to
    the column whose header it sits under (numbers under right-aligned
    headers), else to the last column starting at or left of it (descriptions
    running past their header).

    Wrapped narration lines are joined here, where positions are known: if the
    line above runs right up to the column's edge, the text was broken
    mid-word ("…-FO" / "OD ORDER") and is joined without a space.
    """
    grid: list[list[str]] = []
    columns: list[_Cell] | None = None
    main_header: list[str] | None = None  # the statement's column header, the first one seen
    bounded = False  # columns come from drawn rules, so their right edges are exact
    for page in pdf.pages:
        last_line: dict[int, dict] = {}  # column -> last word of the previous data line; text never wraps across pages
        lines = _lines(page.extract_words())
        bounds = _column_lines(page)
        cells = [_split_on_lines(words, bounds) if bounds else _split_on_gaps(words) for words in lines]
        texts_per_line = [[c.text for c in line] for line in cells]
        # Only the statement's own header starts the table. Another header-like line
        # (a summary block's "Opening Balance  Dr Count  Cr Count") is just a row.
        is_header = [
            _is_header(t) and (main_header is None or t == main_header) for t in texts_per_line
        ]
        if main_header is None and True in is_header:
            main_header = texts_per_line[is_header.index(True)]
        # On a page that repeats the header, everything above it is page furniture
        # (bank name, account details printed on every page), not table rows. Pages
        # that don't repeat it keep everything; furniture there is skipped later
        # because it has no transaction date.
        start = is_header.index(True) if True in is_header else 0
        for words, line_cells, header in zip(lines[start:], cells[start:], is_header[start:]):
            if header:
                columns, bounded, last_line = line_cells, bool(bounds), {}  # positions re-read on every page
                grid.append([c.text for c in line_cells])
                continue
            if columns is None:
                continue
            parts = _assign(words, columns)
            texts = [" ".join(w["text"] for w in part) for part in parts]
            filled = [i for i, t in enumerate(texts) if t]
            if last_line and not texts[0] and len(filled) == 1 and _is_text_column(columns[filled[0]].text):
                i = filled[0]
                joiner = "" if bounded and _runs_to_edge(last_line.get(i), parts[i], columns[i]) else " "
                grid[-1][i] = f"{grid[-1][i]}{joiner}{texts[i]}" if grid[-1][i] else texts[i]
                last_line[i] = parts[i][-1]
                continue
            grid.append(texts)
            last_line = {i: part[-1] for i, part in enumerate(parts) if part}
    return grid


def _runs_to_edge(last: dict | None, next_words: list[dict], column: _Cell) -> bool:
    """Was the line above broken mid-word? Only a long unbroken token (a UPI
    string, say) gets split across lines, and then the line is filled so far
    that not even the next character would have fitted. A line wrapped at a
    space usually ends with a short word, or with room to spare."""
    if last is None or not next_words:
        return False
    padding = max(next_words[0]["x0"] - column.x0, 0)  # assume the right padding matches the left
    inner_width = column.x1 - column.x0 - 2 * padding
    if last["x1"] - last["x0"] < 0.6 * inner_width:
        return False
    first = next_words[0]
    next_char_width = (first["x1"] - first["x0"]) / max(len(first["text"]), 1)
    leftover = column.x1 - padding - last["x1"]
    return leftover < next_char_width + 1


def _column_lines(page: pdfplumber.page.Page, min_length: float = 72) -> list[float]:
    """x positions of the vertical rules drawn between columns, if the page has
    them. Rules are often drawn as one short segment per row, so segments at the
    same x are added up; only rules at least an inch long in total count."""
    lengths: dict[float, float] = {}
    for edge in page.vertical_edges:
        x = round(edge["x0"])
        near = next((k for k in lengths if abs(k - x) <= 1), x)
        lengths[near] = lengths.get(near, 0) + edge["height"]
    xs = sorted(x for x, length in lengths.items() if length >= min_length)
    return xs if len(xs) >= 3 else []  # at least two columns' worth of rules


def _split_on_lines(words: list[dict], bounds: list[float]) -> list[_Cell]:
    """Group a line's words by the column rules they fall between. Exact, even
    when headers nearly touch ("Withdrawal Amt." "Deposit Amt.")."""
    cells: dict[int, list[dict]] = {}
    for w in words:
        middle = (w["x0"] + w["x1"]) / 2
        i = bisect.bisect_right(bounds, middle) - 1
        if 0 <= i < len(bounds) - 1:
            cells.setdefault(i, []).append(w)
    return [
        _Cell(" ".join(w["text"] for w in group), bounds[i], bounds[i + 1])
        for i, group in sorted(cells.items())
    ]


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


def _assign(words: list[dict], columns: list[_Cell]) -> list[list[dict]]:
    """The words of one line, grouped by column."""
    starts = [c.x0 for c in columns]
    out: list[list[dict]] = [[] for _ in columns]
    for w in words:
        under = [i for i, c in enumerate(columns) if w["x0"] < c.x1 and w["x1"] > c.x0]
        i = under[0] if under else max(bisect.bisect_right(starts, w["x0"]) - 1, 0)
        out[i].append(w)
    return out


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


def _is_text_column(name: str) -> bool:
    """Wrapped text only continues in text columns (narration, reference); a lone
    figure under Balance is a footer or a page number, not the rest of a row."""
    role = column_role(name)
    return role is None or role[0] == "description"


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
        if rows and not cells[0] and len(filled) == 1 and _is_text_column(names[filled[0]]):
            key = names[filled[0]]
            rows[-1][key] = f"{rows[-1][key]} {cells[filled[0]]}".strip()
            continue
        rows.append(dict(zip(names, cells)))
    if header is None:
        raise ExtractionError(f"{source}: no header row found")
    return rows
