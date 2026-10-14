"""Stage 2: turn extracted text rows into schema objects you can do math on.

- Column names are read by their words, however they're spaced or cased
  ("Withdrawal Amt.", "WithdrawalAmt." and "Debit" all mean debit; "Txn Date",
  "Value Date" and "Date" all mean date), so each bank's layout maps onto one shape.
- Amounts like "₹1,23,456.00", "1,500.00 Dr" or "(250.00)" become numbers.
  Commas are stripped whatever the grouping, so Indian 1,23,456 and western
  123,456 both work.
- Dates are parsed day-first, from an explicit list of formats only.
  "05/01/2026" is 5 January, and a date that only makes sense month-first is
  an error, never silently swapped.
- Account details come from the text above the table, and the account number
  is masked to XXXX1234.

Rows with no date (totals, footers) or no amount (opening-balance rows) are
skipped and reported in `skipped`. A row whose date or amount is there but
can't be read raises, because guessing would put wrong numbers downstream.
"""
import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.extract import RawRow, extract_header_text, extract_rows
from app.headers import column_role
from app.schema import StatementInfo, Transaction, TransactionType


class NormalizationError(ValueError):
    pass


@dataclass
class SkippedRow:
    row: RawRow
    reason: str


@dataclass
class NormalizedStatement:
    statement: StatementInfo
    transactions: list[Transaction]
    skipped: list[SkippedRow] = field(default_factory=list)


def load_statement(path: str | Path, password: str | None = None) -> NormalizedStatement:
    """Extract and normalize a statement file in one go. `password` opens a protected PDF."""
    return normalize(extract_rows(path, password), extract_header_text(path, password))


# --- Columns ------------------------------------------------------------------

def map_columns(columns: list[str]) -> dict[str, str]:
    """Schema field -> the statement's column name, for every field found.

    Headers are read by their words (see app.headers), so "Withdrawal Amt.",
    "WithdrawalAmt.", "Debit" and "Dr Amount" all become the debit column. When
    two columns claim a field, the stronger one wins (a transaction date over a
    value date), then the leftmost.
    """
    best: dict[str, tuple[int, int, str]] = {}
    for position, column in enumerate(columns):
        role = column_role(column)
        if role is None:
            continue
        fld, rank = role
        if fld not in best or (rank, position) < best[fld][:2]:
            best[fld] = (rank, position, column)
    mapping = {fld: column for fld, (_, _, column) in best.items()}

    has_amounts = {"debit", "credit"} <= mapping.keys() or "amount" in mapping
    if "date" not in mapping or "description" not in mapping or not has_amounts:
        read_as = ", ".join(f"{c!r} -> {(column_role(c) or ('unused',))[0]}" for c in columns)
        raise NormalizationError(
            "can't map columns: need a date, a description, and either separate debit and credit "
            f"columns or one amount column. Read the columns as: {read_as}"
        )
    return mapping


def _usable_marker(rows: list[RawRow], column: str) -> bool:
    """A weak marker column ("Type") is used only if its values really are Dr/Cr."""
    values = [row[column] for row in rows if row.get(column)]
    try:
        return bool(values) and all(parse_marker(v) for v in values)
    except ValueError:
        return False


# --- Values -------------------------------------------------------------------

# Day-first only. There is deliberately no %m/%d format: Indian statements are
# DD/MM, and a month-first fallback would silently turn 05/01 into May 1.
DATE_FORMATS = (
    "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y", "%d.%m.%Y", "%d.%m.%y",
    "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %b %y", "%d/%b/%Y", "%d/%b/%y",
    "%d %B %Y", "%d-%B-%Y", "%d %b, %Y", "%d %B, %Y",
    "%Y-%m-%d",  # ISO; the 4-digit year first makes it unambiguous
)
_TIME = re.compile(r"\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?$", re.IGNORECASE)


def parse_date(text: str) -> dt.date:
    s = _TIME.sub("", " ".join(text.split()))
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date {text!r} (expected day-first, e.g. 05/01/2026)")


_CURRENCY = re.compile(r"₹|\bINR\b|\bRs\b\.?", re.IGNORECASE)
_MARKER = re.compile(r"(?<![a-z])(dr|cr)(?![a-z])\.?", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:,\d+)*(?:\.\d+)?|\.\d+")


def parse_amount(text: str) -> tuple[Decimal | None, TransactionType | None]:
    """'₹1,23,456.00' -> (123456.00, None); '1,500.00 Dr' -> (1500.00, 'debit');
    '(250.00)' or '-250.00' -> (-250.00, None); '' -> (None, None)."""
    s = _CURRENCY.sub("", text)
    markers = _MARKER.findall(s)
    if len(markers) > 1:
        raise ValueError(f"more than one Dr/Cr marker in amount {text!r}")
    marker: TransactionType | None = None
    if markers:
        marker = "debit" if markers[0].lower() == "dr" else "credit"
        s = _MARKER.sub("", s).replace("()", "")
    s = "".join(s.split())
    if not s:
        if marker:
            raise ValueError(f"Dr/Cr marker without a number in {text!r}")
        return None, None
    negative = s.startswith("-") or s.endswith("-") or (s.startswith("(") and s.endswith(")"))
    s = s.strip("()-+")
    if not _NUMBER.fullmatch(s):
        raise ValueError(f"unrecognised amount {text!r}")
    try:
        value = Decimal(s.replace(",", ""))
    except InvalidOperation:
        raise ValueError(f"unrecognised amount {text!r}") from None
    return (-value if negative else value), marker


_DEBIT_MARKERS = {"dr", "d", "debit", "db", "withdrawal"}
_CREDIT_MARKERS = {"cr", "c", "credit", "deposit"}


def parse_marker(text: str) -> TransactionType | None:
    key = re.sub(r"[^a-z]", "", text.lower())
    if not key:
        return None
    if key in _DEBIT_MARKERS:
        return "debit"
    if key in _CREDIT_MARKERS:
        return "credit"
    raise ValueError(f"unrecognised Dr/Cr marker {text!r}")


def mask_account_number(text: str) -> str | None:
    """'50100123451234' -> 'XXXX1234'; 'XXXX XXXX 7710' -> 'XXXX7710'."""
    digits = re.sub(r"\D", "", text)
    return f"XXXX{digits[-4:]}" if digits else None


# --- Header text --------------------------------------------------------------

_BANK_NAME = re.compile(r"^\s*bank\s*name\s*[:\-]\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_BANK_LINE = re.compile(r"^.*\bbank\b.*$", re.IGNORECASE | re.MULTILINE)
_HOLDER = re.compile(
    r"^\s*(?:account\s+holder(?:\s+name)?|customer(?:\s+name)?|holder\s+name|name)\s*[:\-]\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)
_ACCOUNT = re.compile(
    r"\b(?:a/c|acct|account)[ \t]*(?:no\.?|number|num|#)?[ \t]*[:\-][ \t]*([Xx*•\d][Xx*•\d \-]*)",
    re.IGNORECASE,
)
_PERIOD = re.compile(
    r"^\s*(?:statement\s+period|period|from)\b[ \t]*[:\-]?[ \t]*(.+?)\s+(?:to|-|–)\s+(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _first_cell(text: str) -> str:
    # Details often share a line with other fields: "Name: A Sharma    Branch: Pune".
    return re.split(r"\s{2,}", text.strip())[0]


def parse_header(text: str) -> dict:
    """Pull what account details the header text has. Missing ones are left out."""
    info: dict = {}
    if m := _BANK_NAME.search(text) or _BANK_LINE.search(text):
        info["bank_name"] = _first_cell(m.group(m.lastindex or 0))
    if m := _HOLDER.search(text):
        info["holder_name"] = _first_cell(m.group(1))
    if (m := _ACCOUNT.search(text)) and (masked := mask_account_number(m.group(1))):
        info["account_number"] = masked
    if m := _PERIOD.search(text):
        try:
            info["period_start"], info["period_end"] = parse_date(m.group(1)), parse_date(m.group(2))
        except ValueError:
            pass  # not a date range after all; fall back to the transaction dates
    return info


# --- Rows ---------------------------------------------------------------------

def normalize(rows: list[RawRow], header_text: str = "") -> NormalizedStatement:
    if not rows:
        raise NormalizationError("no rows to normalize")
    cols = map_columns(list(rows[0]))
    if "marker" in cols and column_role(cols["marker"]) == ("marker", 1) and not _usable_marker(rows, cols["marker"]):
        del cols["marker"]  # e.g. a "Type" column holding UPI/NEFT, not Dr/Cr
    transactions: list[Transaction] = []
    skipped: list[SkippedRow] = []
    typed_by_sign: list[Decimal] = []

    for n, row in enumerate(rows, start=1):
        date_text = row[cols["date"]]
        if not any(ch.isdigit() for ch in date_text):
            skipped.append(SkippedRow(row, "no date"))
            continue
        try:
            date = parse_date(date_text)
            amount, txn_type = _amount_and_type(row, cols, typed_by_sign)
            balance = _balance(row[cols["balance"]]) if "balance" in cols else None
        except ValueError as e:
            raise NormalizationError(f"row {n}: {e}: {row}") from None
        if amount is None:
            skipped.append(SkippedRow(row, "no amount"))
            continue
        transactions.append(Transaction(
            date=date, description=row[cols["description"]], amount=float(amount), type=txn_type,
            balance=float(balance) if balance is not None else None,
        ))

    if not transactions:
        raise NormalizationError("no transactions found")
    if typed_by_sign and not any(v < 0 for v in typed_by_sign):
        # With no marker and no negatives there's no way to tell debits from credits.
        raise NormalizationError(
            "amount column has no Dr/Cr marker and no negative values; can't tell debits from credits"
        )

    info = parse_header(header_text)
    info.setdefault("period_start", min(t.date for t in transactions))
    info.setdefault("period_end", max(t.date for t in transactions))
    return NormalizedStatement(StatementInfo(**info), transactions, skipped)


def _amount_and_type(
    row: RawRow, cols: dict[str, str], typed_by_sign: list[Decimal]
) -> tuple[Decimal | None, TransactionType | None]:
    """The positive amount and its direction, or (None, None) for a row without one."""
    if "debit" in cols and "credit" in cols:
        debit, _ = parse_amount(row[cols["debit"]])
        credit, _ = parse_amount(row[cols["credit"]])
        filled = [(v, t) for v, t in ((debit, "debit"), (credit, "credit")) if v]
        if len(filled) == 2:
            raise ValueError("both debit and credit are filled")
        return (abs(filled[0][0]), filled[0][1]) if filled else (None, None)

    value, marker = parse_amount(row[cols["amount"]])
    if not value:
        return None, None
    if marker is None and "marker" in cols:
        marker = parse_marker(row[cols["marker"]])
    if marker is None:
        typed_by_sign.append(value)
        marker = "debit" if value < 0 else "credit"
    return abs(value), marker


def _balance(text: str) -> Decimal | None:
    # A balance in credit ("4,48,000.00 Cr") is money in the account; "Dr" means overdrawn.
    value, marker = parse_amount(text)
    if value is None:
        return None
    return -abs(value) if marker == "debit" else value
