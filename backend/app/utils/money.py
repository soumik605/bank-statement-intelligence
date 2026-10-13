# backend/app/utils/money.py
"""Parse money cells from bank statements."""
import math
import re
from decimal import ROUND_HALF_UP, Decimal

from app.schema import Direction

_EMPTY = {"", "-", "--", "—", "NAN", "NONE", "NULL"}
_CURRENCY = re.compile(r"₹|INR|RS\.?")
_NUMBER = re.compile(r"^\d+(\.\d+)?$|^\.\d+$")


def parse_amount(text: str | float | int | None) -> float | None:
    """Parse a money cell into a signed float rounded to 2 decimals, or None for an empty cell.

    Commas and spaces are dropped, so Indian (2,45,000) and Western (245,000) grouping both work.
    Negative when the value has a leading or trailing '-', is wrapped in parentheses, or ends in Dr.
    A Cr suffix or leading '+' means positive. Raises ValueError for text that is not an amount.
    """
    if text is None:
        return None
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return None if math.isnan(text) else _round(Decimal(str(text)))

    s = str(text).strip().upper()
    if s in _EMPTY:
        return None
    s = _CURRENCY.sub("", s).replace(",", "").replace(" ", "")

    negative = False
    if s.endswith(("DR", "DR.")):
        negative, s = True, s.removesuffix(".")[:-2]
    elif s.endswith(("CR", "CR.")):
        s = s.removesuffix(".")[:-2]
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    if s.startswith("-"):
        negative, s = True, s[1:]
    elif s.startswith("+"):
        s = s[1:]
    elif s.endswith("-"):
        negative, s = True, s[:-1]

    if not _NUMBER.match(s):
        raise ValueError(f"Not an amount: {text!r}")
    value = _round(Decimal(s))
    return -value if negative and value else value


def split_signed(value: float) -> tuple[float, Direction]:
    """Turn a signed amount into the positive amount + direction the schema expects."""
    return abs(value), "debit" if value < 0 else "credit"


def _round(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
