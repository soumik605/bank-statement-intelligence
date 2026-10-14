"""Understanding statement column headers, however they're spaced or cased.

Banks write the same column many ways: "Withdrawal Amt.", "WithdrawalAmt.",
"WITHDRAWAL AMOUNT (INR)", "Debit", "Dr Amount", "Paid Out". PDF text
extraction often drops spaces too. So instead of matching a fixed list of
names, a header is split into known words, even when they're run together
("withdrawalamt" -> withdrawal + amt), and its role comes from those words.
"""
import re
from functools import lru_cache

# Words that appear in statement headers. Splitting run-together text only ever
# produces these, so "transfer" is never read as "trans" + "fer".
VOCABULARY = frozenset({
    # dates
    "date", "dt", "txn", "transaction", "tran", "trans", "value", "posting", "post", "book", "booking",
    # description
    "narration", "narrative", "particulars", "particular", "description", "desc", "details", "detail",
    "remarks", "remark",
    # money out / in
    "debit", "debits", "withdrawal", "withdrawals", "withdraw", "payment", "payments", "paid", "out",
    "credit", "credits", "deposit", "deposits", "receipt", "receipts", "received", "in", "money",
    "dr", "cr",
    # amounts and balance
    "amount", "amounts", "amt", "inr", "rs", "balance", "bal", "closing", "running", "available", "avail",
    # reference columns (recognised so they're ignored, not misread)
    "ref", "reference", "no", "number", "num", "chq", "cheque", "check",
    # Dr/Cr indicator columns
    "type", "mode",
})
_LONGEST = max(map(len, VOCABULARY))

DATE_WORDS = {"date", "dt"}
DESCRIPTION_WORDS = {"narration", "narrative", "particulars", "particular", "description", "desc",
                     "details", "detail", "remarks", "remark"}
DEBIT_WORDS = {"debit", "debits", "withdrawal", "withdrawals", "withdraw", "payment", "payments", "dr"}
CREDIT_WORDS = {"credit", "credits", "deposit", "deposits", "receipt", "receipts", "received", "cr"}
AMOUNT_WORDS = {"amount", "amounts", "amt"}
BALANCE_WORDS = {"balance", "bal"}
REFERENCE_WORDS = {"ref", "reference", "chq", "cheque", "check"}

# Any of these in a cell suggests it's a column header (used to find the header row).
HEADER_HINTS = (DATE_WORDS | DESCRIPTION_WORDS | DEBIT_WORDS | CREDIT_WORDS | AMOUNT_WORDS
                | BALANCE_WORDS | REFERENCE_WORDS)


def header_words(name: str) -> list[str]:
    """'WithdrawalAmt.' -> ['withdrawal', 'amt']; 'Chq./Ref.No.' -> ['chq', 'ref', 'no'].
    A chunk that can't be split into known words is kept whole."""
    words: list[str] = []
    for chunk in re.findall(r"[a-z]+", name.lower()):
        words += _split(chunk) or [chunk]
    return words


@lru_cache(maxsize=1024)
def _split(chunk: str) -> list[str] | None:
    """Split run-together text into vocabulary words, fewest words first; None if impossible."""
    best: list[list[str] | None] = [[]] + [None] * len(chunk)
    for end in range(1, len(chunk) + 1):
        for start in range(max(0, end - _LONGEST), end):
            head = best[start]
            if head is not None and chunk[start:end] in VOCABULARY:
                candidate = [*head, chunk[start:end]]
                if best[end] is None or len(candidate) < len(best[end]):
                    best[end] = candidate
    return best[-1]


def is_header_hint(cell: str) -> bool:
    return any(w in HEADER_HINTS for w in header_words(cell))


def column_role(name: str) -> tuple[str, int] | None:
    """What a column holds, and how strongly (lower rank wins when two columns
    claim the same role). None for columns the pipeline doesn't use."""
    words = set(header_words(name))
    out = bool(words & DEBIT_WORDS) or ("out" in words and bool(words & {"paid", "money"}))
    in_ = bool(words & CREDIT_WORDS) or ("in" in words and bool(words & {"paid", "money"}))

    if words & DATE_WORDS:
        # A transaction date beats a plain date, which beats posting and value dates.
        if words & {"txn", "transaction", "tran", "trans"}:
            return "date", 0
        if words & {"value"}:
            return "date", 3
        if words & {"posting", "post", "book", "booking"}:
            return "date", 2
        return "date", 1
    if words & REFERENCE_WORDS:
        return None  # "Chq./Ref.No." and the like
    if words & BALANCE_WORDS:
        return "balance", 0
    if words & DESCRIPTION_WORDS:
        return "description", 0
    if out and in_:
        # "Dr/Cr" or "Debit/Credit" marks direction; with "Amount" it's a signed amount column.
        return ("amount", 0) if words & AMOUNT_WORDS else ("marker", 0)
    if out:
        return "debit", 0
    if in_:
        return "credit", 0
    if words & AMOUNT_WORDS:
        return "amount", 0
    if words & {"type", "mode"}:
        # "Type" may hold Dr/Cr, or a channel like UPI/NEFT; the values decide (see normalize).
        return "marker", 1
    return None
