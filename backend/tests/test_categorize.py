import json

import pytest

from app.categorize import categorize_by_rules, match_rule
from app.normalize import load_statement
from app.schema import Category, Transaction
from scripts.generate_samples import generate


@pytest.mark.parametrize("description, txn_type, expected", [
    ("NEFT CR-ACME TECHNOLOGIES PVT LTD-SALARY JAN26", "credit", Category.SALARY),
    ("BY NEFT/BLUEPEAK LOGISTICS/SAL JAN26", "credit", Category.SALARY),
    ("UPI-SWIGGY-604118227731", "debit", Category.FOOD),
    ("upi/123/Uber India", "debit", Category.TRAVEL),
    ("UBER EATS ORDER", "debit", Category.FOOD),
    ("IRCTC E-TICKET", "debit", Category.TRAVEL),
    ("Electricity Bill - KSEB - Cons 515142", "debit", Category.UTILITIES),
    ("AMAZON PAY INDIA, BANGALORE", "debit", Category.SHOPPING),
    # Refund keywords beat the merchant keyword.
    ("AMZN REFUND", "credit", Category.REFUND),
    ("AMAZON REFUND", "credit", Category.REFUND),
    ("REV-UPI-SWIGGY-123", "credit", Category.REFUND),
    # More specific beats the generic words that also appear.
    ("Rent Transfer to Kavita Deshpande", "debit", Category.RENT),
    ("CREDIT CARD BILL PAYMENT", "debit", Category.TRANSFER),
    ("NEFT-SELF-SAVINGS", "debit", Category.TRANSFER),
    ("NEFT CR-LIFECOVER INSURANCE-POLICY MATURITY", "credit", Category.INCOME),
    ("IMPS-TRANSFER TO VIKRAM SHAH-PERSONAL LOAN", "debit", Category.TRANSFER),
    # Added on request
    ("UPI-APOLLO PHARMACY-apollo@icici-123", "debit", Category.HEALTH),
    ("POS 4521 SHREE RAM MEDICAL STORE", "debit", Category.HEALTH),
    ("UPI/GUPTA MEDICAL STORES/OKSBI", "debit", Category.HEALTH),
    ("AMAZON PHARMACY ORDER", "debit", Category.HEALTH),  # health beats the Amazon shopping keyword
    ("UPI-TOTO-RIDE-9876xxxx", "debit", Category.TRAVEL),
    ("GOIBIBO HOTEL BOOKING", "debit", Category.TRAVEL),
    ("NEFT-INDIANRAILWAYS-PNR4521", "debit", Category.TRAVEL),
    ("INDIAN RAILWAYS E-TICKET", "debit", Category.TRAVEL),
    ("UPI/WOW MOMO/PAYTM", "debit", Category.FOOD),
    ("UPI-TIBETAN MOMOS-STALL", "debit", Category.FOOD),
    ("PHARMACY REFUND", "credit", Category.REFUND),  # refunds still come first
    # Entertainment
    ("NETFLIX.COM SUBSCRIPTION", "debit", Category.ENTERTAINMENT),
    ("UPI-BOOKMYSHOW-BMS@ICICI", "debit", Category.ENTERTAINMENT),
    ("AMAZON PRIME MEMBERSHIP", "debit", Category.ENTERTAINMENT),  # beats Amazon shopping
    ("YOUTUBE PREMIUM", "debit", Category.ENTERTAINMENT),  # not insurance
    # Education
    ("NEFT-DPS SCHOOL-TERM FEE", "debit", Category.EDUCATION),
    ("UPI-UNACADEMY-SUBSCRIPTION", "debit", Category.EDUCATION),
    # Insurance
    ("LIC PREMIUM 123456789", "debit", Category.INSURANCE),
    ("ACH D- STAR HEALTH INSURANCE", "debit", Category.INSURANCE),  # not Health
    ("NEFT CR-LIFECOVER INSURANCE-POLICY MATURITY", "credit", Category.INCOME),  # a payout is income
    # Loans & EMI, either direction
    ("EMI 12/36 HOME LOAN A/C 4521", "debit", Category.LOANS),
    ("BAJAJ FINANCE LTD EMI", "debit", Category.LOANS),
    ("LOAN DISBURSEMENT CREDIT", "credit", Category.LOANS),
    # Investments, either direction
    ("ACH D- ZERODHA BROKING", "debit", Category.INVESTMENTS),
    ("SIP-AXIS MUTUAL FUND-FOLIO 123", "debit", Category.INVESTMENTS),
    ("MUTUAL FUND REDEMPTION", "credit", Category.INVESTMENTS),
    ("FIXED DEPOSIT MATURITY PROCEEDS", "credit", Category.INVESTMENTS),  # own money back, not income
    # Fees & Taxes
    ("SMS CHARGES Q1 2026", "debit", Category.FEES),
    ("CURRENT A/C CHARGES", "debit", Category.FEES),
    ("DEBIT CARD ANNUAL FEE + GST", "debit", Category.FEES),
    ("TDS ON INTEREST", "debit", Category.FEES),  # "INTEREST" only counts as money in
    ("INCOME TAX CHALLAN-CBDT", "debit", Category.FEES),
    ("REV-SMS CHARGES", "credit", Category.REFUND),
    # Wider lists in existing categories
    ("BILLPAY-CESC LTD-KOLKATA", "debit", Category.UTILITIES),
    ("AIRTEL POSTPAID 98XXXXXX12", "debit", Category.UTILITIES),
    ("UPI-1MG-TATA1MG@ICICI", "debit", Category.HEALTH),
    ("APOLLO HOSPITALS ENTERPRISE", "debit", Category.HEALTH),
    ("UPI-ZEPTO-ZEPTO@YBL", "debit", Category.FOOD),
    ("KFC INDIA", "debit", Category.FOOD),
    ("CLEARTRIP FLIGHT", "debit", Category.TRAVEL),
    ("DMRC SMART CARD RECHARGE", "debit", Category.TRAVEL),
    ("FASTAG RECHARGE", "debit", Category.TRAVEL),  # travel, not a phone recharge
    ("MOBILE RECHARGE 98XXXXXX12", "debit", Category.UTILITIES),  # a bare recharge is still a bill
    ("TO BBPS/MSEDCL/959526", "debit", Category.UTILITIES),
    ("H&M STORE MUMBAI", "debit", Category.SHOPPING),
    ("NWD-XXXXXX6402-ATM", "debit", Category.CASH),
])
def test_match_rule(description, txn_type, expected):
    assert match_rule(description, txn_type) == expected


@pytest.mark.parametrize("description, txn_type", [
    ("UPI/9876xxxx/RAMESH", "debit"),  # a person; no keyword can know that
    ("TO IMPS/430417548783/MEERA JOSHI", "debit"),  # actually rent, but nothing says so
    ("TORRENT POWER LTD", "credit"),  # whole-word: no "RENT" inside "TORRENT"
    ("CURRENT A/C XX1234 TO SAVINGS", "debit"),  # no "RENT" inside "CURRENT"
    ("UPI-SWIGGY-123", "credit"),  # money in from a food app: wrong direction, leave it for the LLM
    ("RENT RECEIVED FROM TENANT", "credit"),
    ("TOTOPAY WALLET", "debit"),  # whole words only: TOTO inside another word doesn't count
    ("MOMOTARO SUSHI", "debit"),
    ("METRO CASH AND CARRY", "debit"),  # no bare METRO
    ("NACH DR-XYZ-123", "debit"),  # NACH is used for SIPs and EMIs alike: left to the LLM
])
def test_no_rule(description, txn_type):
    assert match_rule(description, txn_type) is None


def _txn(**kw):
    return Transaction(date="2026-01-05", amount=100, **kw)


def test_categorize_by_rules_sets_source():
    hit, miss = categorize_by_rules([
        _txn(description="UPI-SWIGGY-1", type="debit"),
        _txn(description="UPI/9876xxxx/RAMESH", type="debit"),
    ])
    assert (hit.category, hit.category_source) == (Category.FOOD, "rule")
    assert (miss.category, miss.category_source) == (None, None)


def test_categorize_by_rules_keeps_existing_category():
    txn = _txn(description="UPI-SWIGGY-1", type="debit", category="Other", category_source="llm")
    assert categorize_by_rules([txn]) == [txn]


def test_rules_never_contradict_truth(tmp_path):
    generate(tmp_path)
    total = matched = 0
    for truth_path in tmp_path.glob("*.truth.json"):
        truth = json.loads(truth_path.read_text())
        txns = categorize_by_rules(load_statement(tmp_path / truth["source_file"], truth["password"]).transactions)
        for txn, row in zip(txns, truth["rows"], strict=True):
            total += 1
            if txn.category is not None:
                matched += 1
                assert txn.category == row["expected_category"], txn.description
    # Rules should take the bulk; the rest is what the LLM step is for.
    assert matched / total > 0.9
