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
])
def test_match_rule(description, txn_type, expected):
    assert match_rule(description, txn_type) == expected


@pytest.mark.parametrize("description, txn_type", [
    ("UPI/9876xxxx/RAMESH", "debit"),  # a person; no keyword can know that
    ("TO IMPS/430417548783/MEERA JOSHI", "debit"),  # actually rent, but nothing says so
    ("TORRENT POWER LTD", "credit"),  # whole-word: no "RENT" inside "TORRENT"
    ("CURRENT A/C CHARGES", "debit"),
    ("UPI-SWIGGY-123", "credit"),  # money in from a food app: wrong direction, leave it for the LLM
    ("RENT RECEIVED FROM TENANT", "credit"),
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
