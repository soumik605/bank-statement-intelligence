# backend/app/narration.py
"""Parse bank narrations into channel, counterparty, reference, UPI id and memo.

Patterns cover the layouts in app/narrations.py (the generator) plus real-world
variants. When a narration breaks the parser, add it to tests/test_narration.py.
"""
import re
from dataclasses import dataclass
from typing import Literal

Channel = Literal["UPI", "NEFT", "IMPS", "NACH", "ATM", "CARD", "OTHER"]

# Detected from the start of the narration so names like "ATMARAM" in a UPI line don't misfire.
_CHANNELS: list[tuple[Channel, re.Pattern]] = [
    ("UPI", re.compile(r"^UPI[/-]")),
    ("NEFT", re.compile(r"^NEFT\b")),
    ("IMPS", re.compile(r"^(MMT/)?IMPS\b")),
    ("NACH", re.compile(r"^N?ACH\b")),
    ("ATM", re.compile(r"^(ATM|NWD|ATW|EAW|NFS)\b")),
    ("CARD", re.compile(r"^(POS|ECOM|VPS|IPS)\b")),
]

# SBI wraps transfers as "BY TRANSFER-..." (credit) / "TO TRANSFER-..." (debit).
_TRANSFER_PREFIX = re.compile(r"^(BY|TO) TRANSFER-")
# Filler memos that carry no information.
_FILLER_NOTES = {"", "PAYMENT", "UPI", "PAY", "NA", "N/A", "SENT USING PAYTM UPI", "NO REMARKS"}
_NACH_PURPOSE = re.compile(r"\s+(LOAN EMI|EMI|SIP|PREMIUM|INSURANCE)$")
_IFSC = r"[A-Z]{4}0[A-Z0-9]{6}"
_VPA = r"[A-Z0-9._-]+@[A-Z0-9]+"


@dataclass(frozen=True)
class ParsedNarration:
    channel: Channel
    counterparty: str | None = None
    reference: str | None = None
    upi_id: str | None = None
    note: str | None = None


def parse_narration(text: str) -> ParsedNarration:
    s = " ".join(text.upper().split()).rstrip("-").strip()
    s = _TRANSFER_PREFIX.sub("", s)
    channel = next((name for name, pattern in _CHANNELS if pattern.search(s)), "OTHER")
    parser = _PARSERS.get(channel)
    return parser(s) if parser else ParsedNarration(channel)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split()).strip(" -/*")
    return value or None


def _note(value: str | None) -> str | None:
    value = _clean(value)
    return None if value is None or value in _FILLER_NOTES else value


def _name_from_vpa(vpa: str) -> str:
    """'ROHAN.GUPTA@OKAXIS' -> 'ROHAN GUPTA' for layouts that print only the UPI id."""
    return _clean(re.sub(r"[._-]+", " ", vpa.split("@")[0])) or vpa


def _upi(s: str) -> ParsedNarration:
    # SBI: UPI/DR/412345678901/SWIGGY/YESB/swiggy@ybl[/NOTE]
    if m := re.match(rf"^UPI/(?:DR|CR)/(\d+)/([^/]+)/[A-Z]{{4}}/({_VPA})(?:/(.*))?$", s):
        ref, name, vpa, note = m.groups()
        return ParsedNarration("UPI", _clean(name), ref, vpa.lower(), _note(note))
    # HDFC: UPI-SWIGGY-swiggy@ybl-YESB0283714-412345678901-PAYMENT
    if m := re.match(rf"^UPI-(.+?)-({_VPA})-{_IFSC}-(\d+)(?:-(.*))?$", s):
        name, vpa, ref, note = m.groups()
        return ParsedNarration("UPI", _clean(name), ref, vpa.lower(), _note(note))
    # Axis: UPI/P2M/412345678901/SWIGGY/NOTE/YES BANK
    if m := re.match(r"^UPI/P2[MA]/(\d+)/([^/]+)/([^/]*)/[^/]+$", s):
        ref, name, note = m.groups()
        return ParsedNarration("UPI", _clean(name), ref, None, _note(note))
    # ICICI: UPI/412345678901/NOTE/swiggy@ybl/YES BANK (no payee name, only the UPI id)
    if m := re.match(rf"^UPI/(\d+)/([^/]*)/({_VPA})/[^/]+$", s):
        ref, note, vpa = m.groups()
        return ParsedNarration("UPI", _name_from_vpa(vpa), ref, vpa.lower(), _note(note))
    return ParsedNarration("UPI")


def _neft(s: str) -> ParsedNarration:
    # SBI (after BY/TO TRANSFER-): NEFT*ICIC0123456*ICICN012345678901*ACME TECH
    if m := re.match(rf"^NEFT\*{_IFSC}\*([A-Z0-9]+)\*(.+)$", s):
        utr, name = m.groups()
        return ParsedNarration("NEFT", _clean(name), utr)
    # HDFC: NEFT CR-ICIC0123456-ACME TECH-SALARY JAN 2026-ICICN012345678901
    if m := re.match(rf"^NEFT (?:CR|DR)-{_IFSC}-(.+?)-(.*)-([A-Z0-9]+)$", s):
        name, note, utr = m.groups()
        return ParsedNarration("NEFT", _clean(name), utr, None, _note(note))
    # ICICI: NEFT-ICICN012345678901-ACME TECH-SALARY JAN 2026
    if m := re.match(r"^NEFT-([A-Z0-9]+)-(.+?)(?:-(.*))?$", s):
        utr, name, note = m.groups()
        return ParsedNarration("NEFT", _clean(name), utr, None, _note(note))
    # Axis: NEFT/ICICN012345678901/ACME TECH/SALARY JAN 2026
    if m := re.match(r"^NEFT/([A-Z0-9]+)/([^/]+)(?:/(.*))?$", s):
        utr, name, note = m.groups()
        return ParsedNarration("NEFT", _clean(name), utr, None, _note(note))
    return ParsedNarration("NEFT")


def _imps(s: str) -> ParsedNarration:
    # SBI / Axis: IMPS/P2P/412345678901/KARTHIK R[/NOTE]
    if m := re.match(r"^IMPS/P2[PA]/(\d+)/([^/]+)(?:/(.*))?$", s):
        ref, name, note = m.groups()
        return ParsedNarration("IMPS", _clean(name), ref, None, _note(note))
    # HDFC: IMPS-412345678901-KARTHIK R-SBIN-XXXXXXXX1234-TRIP SHARE
    if m := re.match(r"^IMPS-(\d+)-(.+?)-[A-Z]{4}-X+\d+(?:-(.*))?$", s):
        ref, name, note = m.groups()
        return ParsedNarration("IMPS", _clean(name), ref, None, _note(note))
    # ICICI: MMT/IMPS/412345678901/TRIP SHARE/KARTHIK R/SBIN
    if m := re.match(r"^MMT/IMPS/(\d+)/([^/]*)/([^/]+)/[A-Z]{4}$", s):
        ref, note, name = m.groups()
        return ParsedNarration("IMPS", _clean(name), ref, None, _note(note))
    return ParsedNarration("IMPS")


def _nach(s: str) -> ParsedNarration:
    # Axis: NACH-DR-BAJAJ FINANCE LTD-LOAN EMI-SBIN7123456789012345
    if m := re.match(r"^NACH-DR-(.+?)-([^-]+)-([A-Z0-9]+)$", s):
        lender, purpose, umrn = m.groups()
        return ParsedNarration("NACH", _clean(lender), umrn, None, _note(purpose))
    # HDFC: ACH D- BAJAJ FINANCE LTD-123456789
    if m := re.match(r"^ACH D-\s*(.+)-(\d+)$", s):
        lender, umrn = m.groups()
        return ParsedNarration("NACH", _clean(lender), umrn)
    # ICICI: ACH/BAJAJ FINANCE LTD/ICIC7021102230001234
    if m := re.match(r"^ACH/([^/]+)/([A-Z0-9]+)$", s):
        lender, umrn = m.groups()
        return ParsedNarration("NACH", _clean(lender), umrn)
    # SBI: NACH/DR/BAJAJ FINANCE LTD LOAN EMI (purpose run into the lender name)
    if m := re.match(r"^N?ACH/(?:DR/)?(.+)$", s):
        lender = m.group(1)
        purpose = _NACH_PURPOSE.search(lender)
        if purpose:
            lender = lender[: purpose.start()]
        return ParsedNarration("NACH", _clean(lender), None, None, purpose.group(1) if purpose else None)
    return ParsedNarration("NACH")


def _atm(s: str) -> ParsedNarration:
    # SBI: ATM WDL/BENGALURU/S1AW1234   HDFC: NWD-4XXXXXXXXXXX4821-S1AW1234-BENGALURU
    # ICICI: ATM/CASH WDL/S1AW1234/BENGALURU   Axis: ATM-CASH/S1AW1234/BENGALURU
    terminal = re.search(r"\b[A-Z]\d[A-Z0-9]{2}\d{4}\b", s)
    return ParsedNarration("ATM", None, terminal.group(0) if terminal else None)


def _card(s: str) -> ParsedNarration:
    # SBI: ECOM PUR/4821/AMAZON/MUMBAI or POS PRCH/4821/STARBUCKS/BENGALURU
    if m := re.match(r"^(?:ECOM PUR|POS PRCH)/(\d{4})/([^/]+)(?:/.*)?$", s):
        return ParsedNarration("CARD", _clean(m.group(2)))
    # ICICI: IPS/AMAZON/123456/MUMBAI (online) or VPS/STARBUCKS/123456/BENGALURU (in store)
    if m := re.match(r"^(?:IPS|VPS)/([^/]+)/(\d+)(?:/.*)?$", s):
        merchant, ref = m.groups()
        return ParsedNarration("CARD", _clean(merchant), ref)
    # Axis: ECOM PUR/AMAZON/GURGAON or POS/STARBUCKS/BENGALURU
    if m := re.match(r"^(?:ECOM PUR|POS)/([^/]+)(?:/.*)?$", s):
        return ParsedNarration("CARD", _clean(m.group(1)))
    # HDFC: POS 4XXXXXXXXXXX4821 AMAZON PAY INDIA
    if m := re.match(r"^POS\s+[0-9X*]{4,19}\s+(.+)$", s):
        return ParsedNarration("CARD", _clean(m.group(1)))
    return ParsedNarration("CARD")


_PARSERS = {"UPI": _upi, "NEFT": _neft, "IMPS": _imps, "NACH": _nach, "ATM": _atm, "CARD": _card}
