"""Stage 1b: when the rules can't read a statement's layout, ask the AI.

Runs only after the rules fail to find the table's header row, or find it but
can't tell which column is which. The AI sees a handful of lines, never the
whole statement: up to 3 lines just above the first transaction that could be
the header (no dates or amounts in them, and as many cells as a table row, so
preamble lines with names don't qualify), and up to 5 transaction rows, with
long digit runs (account numbers) hidden. It answers which line is the header, which of its headings is
the date, description, debit, credit, amount, Dr/Cr type and balance, and how
the dates are written.

The AI never reads the numbers. Our own extract and normalize code reads every
row using the AI's layout, and the result is accepted only if it checks out:
the dates run in order and each balance equals the one before it plus or minus
the amount. Otherwise the file is rejected, as it would have been without the AI.
"""
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from app import llm
from app.extract import ExtractionError, LayoutLine, extract_header_text, extract_rows, layout_lines
from app.formatting import plural
from app.normalize import _DATE_LIKE, NormalizationError, NormalizedStatement, normalize

log = logging.getLogger(__name__)

# Takes chat messages, returns the reply text. Swappable so tests don't call the API.
Complete = Callable[[list[dict]], str]


class LayoutError(NormalizationError):
    """The AI's reading of the layout was missing, unusable, or didn't check out."""


MAX_SAMPLES = 5
HEADER_SEARCH = 6  # the header is among this many lines above the first transaction row
MAX_CANDIDATES = 3  # the ones nearest the table
MIN_AGREEMENT = 0.9  # share of neighbouring rows whose dates and balances must agree
CENT = Decimal("0.01")

# A cell holding just an amount: "1,250.00", "-250", "(250.00)", "₹ 99 Cr".
_AMOUNT_CELL = re.compile(r"[-(]?\s*(?:₹|rs\.?|inr|\$)?\s*\d[\d,]*(?:\.\d+)?\s*\)?\s*(?:cr|dr)?\.?", re.IGNORECASE)
_LONG_DIGITS = re.compile(r"\d{9,}")  # account and card numbers; amounts this long have commas

ROLES = {
    "date": "the transaction date (when there are several date columns, the transaction or posting date, not the value date)",
    "description": "the narration, particulars or details text",
    "debit": "money going out, when the table has separate money-out and money-in columns",
    "credit": "money coming in, when the table has separate money-out and money-in columns",
    "amount": "a single amount column, when there are no separate money-out and money-in columns",
    "type": "a column saying only Dr/Cr (or Debit/Credit) for a single amount column",
    "balance": "the running balance after each transaction",
}
# date_order -> month_first for normalize. Year-first (ISO) dates read the same either way.
DATE_ORDERS = {"day-first": False, "month-first": True, "year-first": False}

SYSTEM_PROMPT = (
    "You work out the layout of a bank statement table. You are shown some numbered lines from a statement, "
    "split into cells with ' | ' between them. For PDFs each cell also shows where it starts on the page, "
    "as (x=...); cells on different lines with a similar x are in the same column.\n"
    "The candidate lines might be the table's column headings. The sample rows are transactions from the table.\n"
    "Say which candidate line is the header, and which of its headings holds each of these:\n"
    + "\n".join(f"- {role}: {why}" for role, why in ROLES.items())
    + "\n\nCopy each heading exactly as written in the header line. Use null for anything the table doesn't have. "
    "There must be a date, a description, and either both debit and credit, or an amount.\n"
    'date_order is how the sample dates are written: "day-first" (31/01/2026), "month-first" (01/31/2026) '
    'or "year-first" (2026-01-31).\n'
    "Reply with only a JSON object and nothing else: no explanation, no markdown. Example:\n"
    '{"header_line": 7, "columns": {"date": "Txn Date", "description": "Remarks", "debit": "Paid Out", '
    '"credit": "Paid In", "amount": null, "type": null, "balance": "Balance"}, "date_order": "day-first"}'
)


@dataclass
class Layout:
    header: LayoutLine
    columns: dict[str, str]  # role -> heading, exactly as in the header line
    month_first: bool


def load_with_ai_layout(
    path: str | Path,
    password: str | None,
    rules_error: Exception,
    model: str | None = None,
    complete: Complete | None = None,
) -> NormalizedStatement:
    """Read the statement with a layout from the AI, after the rules failed with
    `rules_error`. Raises `rules_error` itself when nothing in the file looks
    like a table (so the AI isn't asked), else LayoutError saying why the AI's
    reading couldn't be used."""
    candidates, samples = pick_lines(layout_lines(path, password))
    if not candidates or not samples:
        log.info("  Nothing in the file looks like a table of transactions, so there's nothing to ask the AI")
        raise rules_error
    log.info(
        "  The AI will see only %s that could be the column headings and %s from the table "
        "(long numbers like account numbers are hidden)",
        plural(len(candidates), "line"), plural(len(samples), "sample row"),
    )
    if complete is None:
        complete = lambda messages: llm.chat(  # noqa: E731
            messages, model=model, temperature=0, max_tokens=300,
            purpose="work out which line holds the column headings and what each column is",
        )
    try:
        try:
            reply = complete(prompt(candidates, samples))
        except Exception as e:  # network, auth, rate limit
            raise LayoutError("the AI couldn't be reached" + ("" if isinstance(e, llm.LLMError) else f" ({e})")) from None
        layout = parse_reply(reply, candidates)
        log.info("  The AI says the column headings are: %s", " | ".join(c for c in layout.header.cells if c))
        log.info(
            "  and reads them as: %s; dates written %s",
            ", ".join(f"{role} = {heading!r}" for role, heading in layout.columns.items()),
            "month first" if layout.month_first else "day first",
        )
        statement = _read(path, password, layout)
    except (LayoutError, NormalizationError, ExtractionError) as e:
        log.warning("  Not using the AI's reading of the layout: %s", e)
        raise LayoutError(f"{rules_error}. The AI was asked to work out the layout instead, but {e}") from None
    log.info("  Double-checked the AI's reading against the statement's own figures: it adds up, so using it")
    return statement


def pick_lines(lines: list[LayoutLine]) -> tuple[list[LayoutLine], list[LayoutLine]]:
    """What the AI gets to see: possible header lines just above the first
    transaction row, and the first few transaction rows. Both empty when no
    line looks like a transaction (a date and an amount in separate cells) with
    a possible header above it. A summary line in the preamble ("Opening balance
    as of 01/06/2023 | 4,082.88") can look like a row; the search moves past it."""
    for first in (i for i, line in enumerate(lines) if _is_transaction_row(line)):
        samples = [line for line in lines[first:] if _is_transaction_row(line)][:MAX_SAMPLES]
        # A header names every column, so it has at least as many cells as the sparsest
        # row. That keeps out preamble lines like "Customer | <name> | Branch".
        fewest = min(len(_filled(s)) for s in samples)
        candidates = [
            line for line in lines[max(0, first - HEADER_SEARCH) : first]
            if _could_be_header(line) and len(_filled(line)) >= fewest
        ][-MAX_CANDIDATES:]
        if candidates:
            return candidates, samples
    return [], []


def _filled(line: LayoutLine) -> list[str]:
    return [c for c in line.cells if c]


def _is_transaction_row(line: LayoutLine) -> bool:
    cells = _filled(line)
    dates = [c for c in cells if _DATE_LIKE.search(c)]
    return len(cells) >= 3 and bool(dates) and any(_AMOUNT_CELL.fullmatch(c) for c in cells if c not in dates)


def _could_be_header(line: LayoutLine) -> bool:
    cells = _filled(line)
    return len(cells) >= 3 and not any(_DATE_LIKE.search(c) or _AMOUNT_CELL.fullmatch(c) for c in cells)


def _show(line: LayoutLine) -> str:
    cells = [_LONG_DIGITS.sub("XXXXXXXX", c) for c in line.cells]
    if line.xs:
        cells = [f"{c} (x={round(x)})" for c, x in zip(cells, line.xs)]
    return f"L{line.number}: " + " | ".join(cells)


def prompt(candidates: list[LayoutLine], samples: list[LayoutLine]) -> list[dict]:
    text = (
        "Candidate header lines:\n" + "\n".join(map(_show, candidates))
        + "\n\nSample rows:\n" + "\n".join(map(_show, samples))
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}]


def _key(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def parse_reply(text: str, candidates: list[LayoutLine]) -> Layout:
    """The AI's answer as a Layout, or LayoutError saying in words what's wrong with it."""
    cleaned = re.sub(r"```(?:json)?", "", text or "", flags=re.IGNORECASE)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    try:
        answer = json.loads(cleaned[start : end + 1]) if 0 <= start < end else None
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        raise LayoutError(f"its answer wasn't in the expected format (it said: {(text or '')[:120]!r})")

    by_number = {line.number: line for line in candidates}
    header = by_number.get(answer.get("header_line")) if isinstance(answer.get("header_line"), int) else None
    if header is None:
        raise LayoutError(f"it picked line {answer.get('header_line')!r}, which isn't one of the possible header lines")

    headings = {_key(c): c for c in header.cells if c}
    named = answer.get("columns") if isinstance(answer.get("columns"), dict) else {}
    columns: dict[str, str] = {}
    for role in ROLES:
        value = named.get(role)
        if value in (None, ""):
            continue
        if _key(value) not in headings:
            raise LayoutError(f"it named a column, {value!r}, that isn't in the header line")
        columns[role] = headings[_key(value)]
    if "date" not in columns or "description" not in columns:
        raise LayoutError("it didn't say which column holds the date and which the description")
    if not ({"debit", "credit"} <= columns.keys() or "amount" in columns):
        raise LayoutError("it didn't say which column holds the amounts")
    if answer.get("date_order") not in DATE_ORDERS:
        raise LayoutError(f"it didn't say how the dates are written (it said {answer.get('date_order')!r})")
    return Layout(header, columns, DATE_ORDERS[answer["date_order"]])


def _read(path: str | Path, password: str | None, layout: Layout) -> NormalizedStatement:
    """Read every row with our own code, using only the AI's layout, then check it."""
    rows = extract_rows(path, password, header=layout.header)
    if not rows:
        raise LayoutError("there are no rows under the header line it picked")
    keys = {_key(k): k for k in rows[0]}
    columns = {}
    for role, heading in layout.columns.items():
        if _key(heading) not in keys:
            raise LayoutError(f"the column {heading!r} wasn't found when reading the table")
        columns["marker" if role == "type" else role] = keys[_key(heading)]
    header_text = extract_header_text(path, password, header=layout.header)
    statement = normalize(rows, header_text, columns=columns, month_first=layout.month_first)
    check(statement, has_balance="balance" in columns)
    return statement


def _signed(t) -> Decimal:
    return Decimal(str(t.amount)) if t.type == "credit" else -Decimal(str(t.amount))


def check(statement: NormalizedStatement, has_balance: bool) -> None:
    """Accept a reading only if it agrees with itself: dates in order (oldest
    or newest first), and, when there's a balance column, each balance equal to
    its neighbour's plus or minus the amount."""
    txns = statement.transactions
    if len(txns) < 2:
        raise LayoutError(f"it found only {plural(len(txns), 'transaction')}, too few to double-check")
    pairs = list(zip(txns, txns[1:]))
    in_order = max(sum(a.date <= b.date for a, b in pairs), sum(a.date >= b.date for a, b in pairs))
    if in_order < MIN_AGREEMENT * len(pairs):
        raise LayoutError("the dates it read don't run in order, so the date column is probably wrong")
    if not has_balance:
        log.info("  The dates run in order. There's no balance column, so the amounts can't be double-checked")
        return
    checkable = [(a, b) for a, b in pairs if a.balance is not None and b.balance is not None]
    if not checkable:
        raise LayoutError("the balance column it chose has no balances in it")
    oldest_first = sum(
        abs(Decimal(str(a.balance)) + _signed(b) - Decimal(str(b.balance))) <= CENT for a, b in checkable
    )
    newest_first = sum(
        abs(Decimal(str(b.balance)) + _signed(a) - Decimal(str(a.balance))) <= CENT for a, b in checkable
    )
    agree = max(oldest_first, newest_first)
    if agree < MIN_AGREEMENT * len(checkable):
        raise LayoutError(
            f"the amounts don't add up to the balances (only {agree} of {len(checkable)} rows do), "
            "so the money columns are probably wrong"
        )
    log.info(
        "  The dates run in order, and %d of %d balances equal the one before plus or minus the amount",
        agree, len(checkable),
    )
