"""Stage 5: flag unusual transactions with simple rules, each giving a plain-English reason.

Every flag has to be explainable: "₹45,000 is 62× your typical debit of ₹720"
is easy to trust and to test. A transaction can trip several rules; the
reasons are joined.

Salary, Rent and Utilities are expected, so the size-based rules never flag
them for being big. They can still be flagged as a possible duplicate, since
paying rent twice is worth a look.
"""
import datetime as dt
import re
import statistics

from app.formatting import format_inr
from app.schema import Category, Transaction

LARGE_DEBIT_MULTIPLE = 3  # a debit above this many times the median debit is large...
# ...and at least this many rupees. When many small UPI payments pull the median
# down to ~₹600, 3x alone flags every ₹2,000 grocery run and ATM withdrawal.
LARGE_DEBIT_FLOOR = 10_000
LARGE_AMOUNT = 50_000  # rupees; the bar for "large" in the round-number and first-payee rules
ROUND_TO = 1_000  # 50,000 and 85,000 are round; 52,340 is not
# Statements carry dates, not times, so only the same date is surely "within 24 hours".
# Including the next day flagged habits like the same ₹400 lunch order every day.
DUPLICATE_WINDOW = dt.timedelta(days=0)
BALANCE_DROP_SHARE = 0.5  # one debit taking out more than half the balance

# Expected, recurring, and big by nature: never flagged just for size.
EXPECTED_CATEGORIES = frozenset({Category.SALARY, Category.RENT, Category.UTILITIES})
# Transfers are left out of the median-multiple rule too. They aren't spending,
# and big ones are caught by the round-number, first-time-payee and balance rules.
SIZE_RULE_EXEMPT = EXPECTED_CATEGORIES | {Category.TRANSFER}


def payee_key(description: str) -> str:
    """The description minus anything with a digit in it (reference numbers,
    masked phone or account numbers, dates), so repeat payments to one payee match."""
    words = re.split(r"[^A-Z0-9]+", description.upper())
    return " ".join(w for w in words if w and not any(c.isdigit() for c in w))


def detect_unusual(transactions: list[Transaction]) -> list[Transaction]:
    """Return the transactions with is_unusual and unusual_reason set from the rules."""
    debit_amounts = [t.amount for t in transactions if t.type == "debit"]
    median_debit = statistics.median(debit_amounts) if debit_amounts else 0.0

    reasons: list[list[str]] = [[] for _ in transactions]
    seen_payees: set[str] = set()
    for i, txn in enumerate(transactions):
        found = reasons[i]
        key = payee_key(txn.description)
        is_debit = txn.type == "debit"
        size_ok = txn.category not in EXPECTED_CATEGORIES  # may be flagged for size at all

        # 1. A debit far above the usual one.
        if is_debit and txn.category not in SIZE_RULE_EXEMPT and median_debit and \
                txn.amount > max(LARGE_DEBIT_MULTIPLE * median_debit, LARGE_DEBIT_FLOOR):
            found.append(
                f"{format_inr(txn.amount)} is {txn.amount / median_debit:.1f}× your typical debit "
                f"of {format_inr(median_debit)}"
            )

        # 2. A large round number, typical of one-off transfers.
        if size_ok and txn.amount >= LARGE_AMOUNT and round(txn.amount * 100) % (ROUND_TO * 100) == 0:
            direction = "out" if is_debit else "in"
            found.append(f"Large round-number amount of {format_inr(txn.amount)} {direction}")

        # 3. Same amount to the same payee shortly after an earlier debit.
        if is_debit and key:
            for earlier in reversed(transactions[:i]):
                if txn.date - earlier.date > DUPLICATE_WINDOW:
                    break
                if earlier.type == "debit" and earlier.amount == txn.amount and payee_key(earlier.description) == key:
                    found.append(
                        f"Possible duplicate: same amount ({format_inr(txn.amount)}) to the same payee "
                        f"earlier the same day"
                    )
                    break

        # 4. A large amount to a payee not seen earlier in the statement.
        if is_debit and size_ok and key and key not in seen_payees and txn.amount >= LARGE_AMOUNT:
            found.append(f"First payment to this payee, and a large one ({format_inr(txn.amount)})")

        # 5. One debit taking a big share of the balance.
        if is_debit and size_ok and txn.balance is not None:
            before = txn.balance + txn.amount
            if before > 0 and txn.amount > BALANCE_DROP_SHARE * before:
                found.append(
                    f"Balance dropped {txn.amount / before:.0%} in one debit, "
                    f"from {format_inr(before)} to {format_inr(txn.balance)}"
                )

        if key:
            seen_payees.add(key)

    return [
        txn.model_copy(update={"is_unusual": bool(r), "unusual_reason": "; ".join(r) or None})
        for txn, r in zip(transactions, reasons)
    ]
