# backend/app/utils/dates.py
"""Parse statement dates with an explicit list of day-first formats.

Never use a guessing parser (dateutil, pandas inference): 05/01/2026 is 5 January
in India, but US-style guessers read it as 1 May. Every format here puts the day
before the month, apart from ISO year-first, so that mistake can't happen.
"""
import datetime as dt
import re

FORMATS = [
    "%d/%m/%Y",  # 05/01/2026
    "%d-%m-%Y",  # 05-01-2026
    "%d.%m.%Y",  # 05.01.2026
    "%d/%m/%y",  # 05/01/26
    "%d-%m-%y",  # 05-01-26
    "%d-%b-%Y",  # 05-Jan-2026
    "%d-%b-%y",  # 05-Jan-26
    "%d %b %Y",  # 05 Jan 2026
    "%d %b %y",  # 05 Jan 26
    "%d %B %Y",  # 05 January 2026
    "%d/%b/%Y",  # 05/Jan/2026
    "%Y-%m-%d",  # 2026-01-05 (ISO; year-first, so never ambiguous)
]

_TIME_SUFFIX = re.compile(r"\s+\d{1,2}:\d{2}(:\d{2})?(\s*[AP]M)?$", re.IGNORECASE)


def parse_date(text: str | dt.date) -> dt.date:
    """Parse a statement date. Raises ValueError if no format in FORMATS matches."""
    if isinstance(text, dt.datetime):
        return text.date()
    if isinstance(text, dt.date):
        return text
    s = " ".join(str(text).split())  # Collapse odd whitespace from PDF extraction
    s = _TIME_SUFFIX.sub("", s)  # Some exports append a time: "05/01/2026 14:32:10"
    s = re.sub(r"(?i)\bsept\b", "Sep", s)  # strptime's %b only knows three-letter months
    for fmt in FORMATS:
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date: {text!r}")
