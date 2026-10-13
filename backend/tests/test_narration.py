# backend/tests/test_narration.py
"""Narration parser cases. When a narration breaks the parser, add it here."""
import pytest

from app.narration import parse_narration

# (narration, channel, counterparty)
CASES = [
    # UPI, one per bank layout
    ("UPI/DR/412345678901/SWIGGY/YESB/swiggy@ybl", "UPI", "SWIGGY"),
    ("UPI-ZOMATO-zomato@paytm-PYTM0198702-691937865764-PAYMENT", "UPI", "ZOMATO"),
    ("UPI/577110510426/PAYMENT/swiggy@ybl/YES BANK", "UPI", "SWIGGY"),
    ("UPI/P2M/199817887524/SWIGGY INSTAMART/UPI/YES BANK", "UPI", "SWIGGY INSTAMART"),
    ("UPI/P2A/592759215392/SNEHA IYER/CAB SHARE/STATE BANK OF INDIA", "UPI", "SNEHA IYER"),
    ("UPI/256419011138/DINNER SPLIT/rohan.gupta@okaxis/AXIS BANK", "UPI", "ROHAN GUPTA"),
    ("UPI/DR/405730753142/UDANT DEWAN/UTIB/udant.dewan@okaxis", "UPI", "UDANT DEWAN"),
    ("UPI/CR/281403868608/FLIPKART/UTIB/flipkart@axl", "UPI", "FLIPKART"),
    # NEFT
    ("NEFT CR-ICIC0150877-ACME TECH-SALARY JAN 2026-ICICN652605398608", "NEFT", "ACME TECH"),
    ("BY TRANSFER-NEFT*ICIC0246316*ICICN214832269481*ACME TECH", "NEFT", "ACME TECH"),
    ("NEFT-ICICN168494888361-ACME TECH-SALARY JAN 2026", "NEFT", "ACME TECH"),
    ("NEFT/ICICN568392664978/ACME TECH/SALARY JAN 2026", "NEFT", "ACME TECH"),
    # IMPS
    ("IMPS/P2P/550144771997/RUSHIL SAINI", "IMPS", "RUSHIL SAINI"),
    ("IMPS-343103922853-JAGRATI SIBAL-SBIN-XXXXXXXX5727-SELF", "IMPS", "JAGRATI SIBAL"),
    ("MMT/IMPS/144760853609/TRIP SHARE/KARTHIK R/SBIN", "IMPS", "KARTHIK R"),
    ("IMPS/P2A/538028184436/WRIDDHISH HAYER/SELF", "IMPS", "WRIDDHISH HAYER"),
    # NACH
    ("NACH/DR/BAJAJ FINANCE LTD LOAN EMI", "NACH", "BAJAJ FINANCE LTD"),
    ("ACH D- BAJAJ FINANCE LTD-795190672", "NACH", "BAJAJ FINANCE LTD"),
    ("ACH/BAJAJ FINANCE LTD/ICIC7021102230001234", "NACH", "BAJAJ FINANCE LTD"),
    ("NACH-DR-BAJAJ FINANCE LTD-LOAN EMI-SBIN7507104716530736", "NACH", "BAJAJ FINANCE LTD"),
    # ATM: no counterparty
    ("ATM WDL/BENGALURU/S1AW1234", "ATM", None),
    ("NWD-4XXXXXXXXXXX4821-S1AC9066-BENGALURU", "ATM", None),
    ("ATM/CASH WDL/S1AW5679/BENGALURU", "ATM", None),
    ("ATM-CASH/S1AW6146/BENGALURU", "ATM", None),
    # Card
    ("POS 4XXXXXXXXXXX4821 AMAZON PAY INDIA", "CARD", "AMAZON PAY INDIA"),
    ("ECOM PUR/4821/FLIPKART/BANGALORE", "CARD", "FLIPKART"),
    ("POS PRCH/4821/STARBUCKS/BENGALURU", "CARD", "STARBUCKS"),
    ("IPS/AMAZON/039242/BANGALORE", "CARD", "AMAZON"),
    ("VPS/STARBUCKS/271002/BENGALURU", "CARD", "STARBUCKS"),
    ("ECOM PUR/AMAZON/GURGAON", "CARD", "AMAZON"),
    ("POS/STARBUCKS/BENGALURU", "CARD", "STARBUCKS"),
    # Not a payment channel
    ("CREDIT INTEREST", "OTHER", None),
    ("INT.PD:01-01-2026 TO 31-01-2026", "OTHER", None),
    ("SB:INT CREDIT:01-01-2026 TO 31-01-2026", "OTHER", None),
]


@pytest.mark.parametrize("narration,channel,counterparty", CASES)
def test_channel_and_counterparty(narration, channel, counterparty):
    parsed = parse_narration(narration)
    assert parsed.channel == channel
    assert parsed.counterparty == counterparty


def test_upi_details_sbi():
    p = parse_narration("UPI/DR/907270532291/RUSHIL SAINI/SBIN/rushil.saini@oksbi/DINNER SPLIT")
    assert (p.reference, p.upi_id, p.note) == ("907270532291", "rushil.saini@oksbi", "DINNER SPLIT")


def test_upi_details_hdfc():
    p = parse_narration("UPI-ROHAN GUPTA-rohan.gupta@okaxis-UTIB0815131-995759484248-DINNER SPLIT")
    assert (p.reference, p.upi_id, p.note) == ("995759484248", "rohan.gupta@okaxis", "DINNER SPLIT")


def test_filler_memo_is_dropped():
    assert parse_narration("UPI-SWIGGY-swiggy@ybl-YESB0483452-205380810795-PAYMENT").note is None
    assert parse_narration("UPI/P2M/235572591311/SWIGGY/UPI/YES BANK").note is None


def test_neft_reference_and_note():
    p = parse_narration("NEFT CR-ICIC0150877-ACME TECH-SALARY JAN 2026-ICICN652605398608")
    assert (p.reference, p.note) == ("ICICN652605398608", "SALARY JAN 2026")


def test_nach_purpose_split_from_lender():
    assert parse_narration("NACH/DR/BAJAJ FINANCE LTD LOAN EMI").note == "LOAN EMI"


def test_atm_terminal_is_reference():
    assert parse_narration("ATM WDL/BENGALURU/S1AW1234").reference == "S1AW1234"


# Real-world variants and traps found while building the parser.

def test_sbi_transfer_prefix_and_trailing_dashes():
    p = parse_narration("TO TRANSFER-UPI/DR/412345678901/RAMESH KUMAR/SBIN/ramesh.k@oksbi/Payment--")
    assert (p.channel, p.counterparty, p.note) == ("UPI", "RAMESH KUMAR", None)


def test_name_containing_atm_is_still_upi():
    p = parse_narration("UPI/DR/412345678901/ATMARAM SHARMA/SBIN/atmaram@oksbi")
    assert (p.channel, p.counterparty) == ("UPI", "ATMARAM SHARMA")


def test_icici_imps_prefix_is_not_a_merchant():
    # ICICI's MMT/IMPS prefix once collided with MakeMyTrip's "MMT" alias.
    assert parse_narration("MMT/IMPS/144760853609/TRIP SHARE/KARTHIK R/SBIN").channel == "IMPS"


def test_lowercase_and_extra_spaces():
    p = parse_narration("  upi/dr/412345678901/swiggy/yesb/swiggy@ybl  ")
    assert (p.channel, p.counterparty, p.upi_id) == ("UPI", "SWIGGY", "swiggy@ybl")


def test_unrecognised_layout_keeps_channel():
    p = parse_narration("UPI SOMETHING NEW 12345")
    assert p.channel == "OTHER"
    p = parse_narration("UPI/SOMETHING/NEW")
    assert (p.channel, p.counterparty) == ("UPI", None)


def test_unknown_narration():
    assert parse_narration("CHQ DEP 123456 MG ROAD").channel == "OTHER"
